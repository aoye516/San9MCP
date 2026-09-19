# -*- coding: utf-8 -*-
"""跨 Codex 进程的单游戏调度器。

本模块只负责请求排队、进程互斥和状态留证；不截图、不 OCR、不点击，
也不推断当前游戏界面。真正的界面门禁和一步一验仍由工具 handler 自己负责。
多个 Codex CLI 进程可以各自启动一个 MCP server，所有 handler 共用同一把
数据目录下的游戏锁，因此同一时刻最多只有一个 handler 能碰游戏。
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Callable, Iterator

try:
    import msvcrt
except ImportError:  # pragma: no cover - Windows 是生产环境
    msvcrt = None  # type: ignore[assignment]

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows 没有 fcntl
    fcntl = None  # type: ignore[assignment]

from san9 import paths


STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
TERMINAL = {STATUS_SUCCEEDED, STATUS_FAILED, STATUS_CANCELLED}


def _pid_alive(pid: int) -> bool:
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


class SchedulerError(RuntimeError):
    """调度器自己的错误。工具异常仍原样抛回 server 翻译。"""


class Scheduler:
    """把所有工具 handler 串成一条跨进程的真机通道。

    `call()` 是同步 API：调用方会在队列里等待，获得游戏租约后才运行 handler。
    状态文件与游戏锁分开，其他进程可以在某个 handler 运行期间观察 queued/running。
    """

    def __init__(self, state_dir: str | None = None, max_history: int = 200):
        self.state_dir = os.path.abspath(
            state_dir or os.path.join(paths.DATA, "mcp", "scheduler"))
        self.lock_path = os.path.join(self.state_dir, "game.lock")
        self.state_lock_path = os.path.join(self.state_dir, "state.lock")
        self.state_path = os.path.join(self.state_dir, "state.json")
        self.max_history = max(10, int(max_history))
        self._state_local = threading.RLock()
        self._game_local = threading.RLock()
        os.makedirs(self.state_dir, exist_ok=True)
        self._ensure_state()
        self._reap_stale()

    @staticmethod
    def _now() -> float:
        return time.time()

    @staticmethod
    def _new_id(name: str) -> str:
        safe = "".join(ch if ch.isalnum() else "_" for ch in (name or "tool"))
        return "%s-%s" % (safe[:24] or "tool", uuid.uuid4().hex[:12])

    @staticmethod
    def _owner() -> dict[str, Any]:
        return {"pid": os.getpid(), "thread": threading.get_ident()}

    def _ensure_state(self) -> None:
        if os.path.exists(self.state_path):
            return
        with self._state_guard():
            if not os.path.exists(self.state_path):
                self._write_state({"version": 1, "updated_at": self._now(), "requests": []})

    def _reap_stale(self) -> None:
        """启动时仅标记已死亡进程留下的 queued/running，不假装完成。"""
        with self._state_guard():
            state = self._read_state()
            changed = False
            for item in state["requests"]:
                if item.get("status") not in (STATUS_QUEUED, STATUS_RUNNING):
                    continue
                owner = item.get("owner") or {}
                pid = owner.get("pid", item.get("pid"))
                if pid and not _pid_alive(pid):
                    item.update({
                        "status": STATUS_FAILED,
                        "finished_at": self._now(),
                        "owner": None,
                        "error": {"type": "StaleRequest",
                                  "message": "发起进程已退出，未确认工具完成"},
                    })
                    changed = True
            if changed:
                self._write_state(state)

    def _prepare_lock_file(self, path: str) -> None:
        """让 Windows msvcrt.locking 有可锁定的至少一个字节。"""
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            with open(path, "ab") as f:
                if os.path.getsize(path) == 0:
                    f.write(b"0")
                    f.flush()
                    os.fsync(f.fileno())

    def _read_state(self) -> dict[str, Any]:
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                value = json.load(f)
        except FileNotFoundError:
            return {"version": 1, "updated_at": self._now(), "requests": []}
        except (OSError, json.JSONDecodeError) as exc:
            raise SchedulerError("调度器状态文件损坏：%s" % exc) from exc
        if not isinstance(value, dict) or not isinstance(value.get("requests"), list):
            raise SchedulerError("调度器状态文件格式错误")
        return value

    def _write_state(self, state: dict[str, Any]) -> None:
        os.makedirs(self.state_dir, exist_ok=True)
        tmp = "%s.%s.tmp" % (self.state_path, os.getpid())
        state["updated_at"] = self._now()
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.state_path)

    @contextmanager
    def _file_lock(self, path: str, timeout: float | None) -> Iterator[None]:
        """获得跨进程文件锁。

        Windows 的 `LK_LOCK` 内建等待约 10 秒后会失败，不适合长游戏动作。
        因此统一使用非阻塞尝试 + 自己轮询：游戏锁可无限排队，状态锁限时等待。
        """
        os.makedirs(self.state_dir, exist_ok=True)
        self._prepare_lock_file(path)
        started = time.monotonic()
        with open(path, "r+b") as f:
            while True:
                f.seek(0)
                try:
                    if msvcrt is not None:
                        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    elif fcntl is not None:  # pragma: no cover - Linux 回归环境
                        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    else:  # pragma: no cover
                        raise SchedulerError("当前平台没有可用的进程锁实现")
                    break
                except (OSError, BlockingIOError) as exc:
                    if timeout is not None and time.monotonic() - started >= timeout:
                        raise SchedulerError("等待调度锁超时（%.1fs）：%s" % (timeout, path)) from exc
                    time.sleep(0.05)
            try:
                yield
            finally:
                f.seek(0)
                if msvcrt is not None:
                    try:
                        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
                elif fcntl is not None:  # pragma: no cover
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def _state_guard(self) -> Iterator[None]:
        with self._state_local:
            with self._file_lock(self.state_lock_path, timeout=30.0):
                yield

    @contextmanager
    def _game_guard(self) -> Iterator[None]:
        with self._game_local:
            with self._file_lock(self.lock_path, timeout=None):
                yield

    def _find(self, state: dict[str, Any], request_id: str) -> dict[str, Any] | None:
        for item in state["requests"]:
            if item.get("request_id") == request_id:
                return item
        return None

    def _append(self, record: dict[str, Any]) -> None:
        with self._state_guard():
            state = self._read_state()
            records = list(state["requests"])
            records.append(record)
            if len(records) > self.max_history:
                live = [x for x in records if x.get("status") not in TERMINAL]
                done = [x for x in records if x.get("status") in TERMINAL]
                if len(live) >= self.max_history:
                    records = live[-self.max_history:]
                else:
                    records = done[-(self.max_history - len(live)):] + live
                records.sort(key=lambda x: x.get("submitted_at", 0.0))
            state["requests"] = records
            self._write_state(state)

    def _update(self, request_id: str, **changes: Any) -> dict[str, Any]:
        with self._state_guard():
            state = self._read_state()
            item = self._find(state, request_id)
            if item is None:
                raise SchedulerError("找不到调度请求：%s" % request_id)
            item.update(changes)
            self._write_state(state)
            return dict(item)

    @staticmethod
    def _stored(value: Any, limit: int = 120000) -> Any:
        """把结果变成可写入状态文件的有限大小副本。"""
        try:
            encoded = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            return {"unserializable": type(value).__name__}
        if len(encoded) > limit:
            return {"truncated": True, "size": len(encoded),
                    "type": type(value).__name__}
        try:
            return json.loads(encoded)
        except json.JSONDecodeError:
            return {"text": encoded}

    def call(self, name: str, args: dict | None, fn: Callable[..., Any]) -> Any:
        """排队并排他执行一个 handler；工具异常会原样重新抛出。"""
        if not callable(fn):
            raise TypeError("调度器 handler 必须可调用")
        if args is not None and not isinstance(args, dict):
            raise TypeError("调度器 args 必须是对象")

        request_id = self._new_id(name)
        self._append({
            "request_id": request_id,
            "name": name,
            "status": STATUS_QUEUED,
            "pid": os.getpid(),
            "submitted_at": self._now(),
            "started_at": None,
            "finished_at": None,
            "owner": None,
            "error": None,
            "result": None,
        })

        with self._game_guard():
            self._update(request_id, status=STATUS_RUNNING,
                         started_at=self._now(), owner=self._owner())
            try:
                result = fn(**(args or {}))
            except BaseException as exc:
                try:
                    self._update(request_id, status=STATUS_FAILED,
                                 finished_at=self._now(), owner=None,
                                 error={"type": type(exc).__name__,
                                        "message": str(exc)})
                except BaseException:
                    pass
                raise
            else:
                try:
                    self._update(request_id, status=STATUS_SUCCEEDED,
                                 finished_at=self._now(), owner=None,
                                 result=self._stored(result))
                except BaseException:
                    # handler 已完成；状态留证失败不能把成功动作伪装成工具失败。
                    pass
                return result

    def get(self, request_id: str | None) -> dict[str, Any] | None:
        if not request_id:
            return None
        with self._state_guard():
            item = self._find(self._read_state(), request_id)
            return dict(item) if item is not None else None

    def live(self) -> list[dict[str, Any]]:
        with self._state_guard():
            state = self._read_state()
            return [dict(item) for item in state["requests"]
                    if item.get("status") not in TERMINAL]

    def history(self) -> list[dict[str, Any]]:
        with self._state_guard():
            return [dict(item) for item in self._read_state()["requests"]]

    def clear_finished(self) -> int:
        with self._state_guard():
            state = self._read_state()
            old = state["requests"]
            state["requests"] = [x for x in old if x.get("status") not in TERMINAL]
            self._write_state(state)
            return len(old) - len(state["requests"])
