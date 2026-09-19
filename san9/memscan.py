"""内存扫描 —— "精确读状态"的地基。

为什么值得走这条路
==================
`San9WPK.exe` 是 PE32，`ImageBase=0x400000`，`DllCharacteristics=0x0000`
（既无 DYNAMICBASE 也无 NXCOMPAT）。意思是它**每次启动内存布局完全一样**，
同一个变量永远在同一个地址。于是：

    "现在有没有弹窗" / "现在是几月" / "洛阳归谁" / "我有多少金"
        → 一次 ReadProcessMemory，微秒级，不需要任何图像识别

对比图像识别的代价：慢一个数量级、每来一种新弹窗就要重新调参、
而且"看不清"和"没有"分不开（弹窗是深色面板，二值签名对它是瞎的）。

但**前提是得先知道那个变量在哪**。本模块只做最底层的三件事：

    1. 枚举可读内存区域        regions()
    2. 抓快照                 snapshot()
    3. 差分，找"变了的地方"     diff_words()

找变量的通用办法叫 **差分法**：

    在状态 A 抓两次（A1, A2）→ 两次之间的差异 = 纯噪声（动画、时钟）
    切到状态 B 再抓一次（B） → B 与 A 的差异 = 噪声 + 我们想要的那个变量
    两者相减 → 剩下的就是候选地址

命令行：
    python -m san9.memscan regions
    python -m san9.memscan findint --value 17844
    python -m san9.memscan dialog-diff
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import struct
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010

MEM_COMMIT = 0x1000
MEM_IMAGE = 0x1000000
MEM_MAPPED = 0x40000
MEM_PRIVATE = 0x20000

PAGE_GUARD = 0x100
_READABLE = 0x02 | 0x04 | 0x08 | 0x20 | 0x40 | 0x80  # R / RW / WC / ER / ERW / EWC


class MBI(ctypes.Structure):
    """MEMORY_BASIC_INFORMATION，64 位布局（可安全用于 32 位目标进程）。"""

    _fields_ = [
        ("BaseAddress", ctypes.c_ulonglong),
        ("AllocationBase", ctypes.c_ulonglong),
        ("AllocationProtect", wt.DWORD),
        ("__align1", wt.DWORD),
        ("RegionSize", ctypes.c_ulonglong),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
        ("__align2", wt.DWORD),
    ]


kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.VirtualQueryEx.argtypes = [wt.HANDLE, ctypes.c_void_p,
                                    ctypes.POINTER(MBI), ctypes.c_size_t]
kernel32.VirtualQueryEx.restype = ctypes.c_size_t
kernel32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
kernel32.ReadProcessMemory.restype = wt.BOOL


# --------------------------------------------------------------------------- #
# 进程与区域
# --------------------------------------------------------------------------- #

def find_pid(name: str = "San9WPK") -> int | None:
    import psutil

    for p in psutil.process_iter(["name", "pid"]):
        n = (p.info.get("name") or "").lower()
        if name.lower() in n and n.endswith(".exe"):
            return p.info["pid"]
    return None


class Rig:
    """一个打开的游戏进程句柄。用 with 或显式 close()。"""

    def __init__(self, pid: int | None = None, name: str = "San9WPK"):
        self.pid = pid or find_pid(name)
        if not self.pid:
            raise RuntimeError("找不到游戏进程，先启动游戏")
        self.handle = kernel32.OpenProcess(
            PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, self.pid)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        if self.handle:
            kernel32.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---------------- 区域枚举 ----------------

    def regions(self, include_image: bool = True,
                include_mapped: bool = False) -> list[dict]:
        """枚举**已提交且可读**的内存区域。

        默认排除 MEM_MAPPED（显存/DirectDraw 表面、字体文件映射等），
        它们又大又全是噪声，对找 UI 变量没帮助。
        """
        out: list[dict] = []
        addr = 0
        mbi = MBI()
        limit = 0x7FFFFFFF if ctypes.sizeof(ctypes.c_void_p) == 8 else 0xFFFFFFFF
        while addr < limit:
            got = kernel32.VirtualQueryEx(self.handle, ctypes.c_void_p(addr),
                                          ctypes.byref(mbi), ctypes.sizeof(mbi))
            if not got:
                break
            base, size = int(mbi.BaseAddress), int(mbi.RegionSize)
            if size <= 0:
                break
            kind = int(mbi.Type)
            ok = (int(mbi.State) == MEM_COMMIT
                  and (int(mbi.Protect) & _READABLE)
                  and not (int(mbi.Protect) & PAGE_GUARD))
            if kind == MEM_MAPPED and not include_mapped:
                ok = False
            if kind == MEM_IMAGE and not include_image:
                ok = False
            if ok:
                out.append({"base": base, "size": size,
                            "protect": int(mbi.Protect), "type": kind,
                            "alloc_base": int(mbi.AllocationBase)})
            addr = base + size
        return out

    # ---------------- 读 ----------------

    def read(self, addr: int, size: int, chunk: int = 1 << 20) -> bytes | None:
        """读一段内存。中途任何一块读失败就放弃整段（返回 None），
        因为半截数据做差分会产生假候选。"""
        buf = ctypes.create_string_buffer(size)
        got = ctypes.c_size_t(0)
        off = 0
        while off < size:
            n = min(chunk, size - off)
            ok = kernel32.ReadProcessMemory(
                self.handle, ctypes.c_void_p(addr + off),
                ctypes.byref(buf, off), n, ctypes.byref(got))
            if not ok or got.value != n:
                return None
            off += n
        return buf.raw

    def read_u32(self, addr: int) -> int | None:
        b = self.read(addr, 4)
        return struct.unpack("<I", b)[0] if b else None

    # ---------------- 快照 ----------------

    def snapshot(self, regions: list[dict] | None = None,
                 min_size: int = 4096, cap: int = 1 << 30) -> list[tuple[int, bytes]]:
        """把可读区域整块抓下来。返回 [(base, data), ...]。"""
        regs = regions if regions is not None else self.regions()
        out: list[tuple[int, bytes]] = []
        total = 0
        for r in sorted(regs, key=lambda x: x["base"]):
            if r["size"] < min_size:
                continue
            if total + r["size"] > cap:
                continue
            data = self.read(r["base"], r["size"])
            if data is None:
                continue
            out.append((r["base"], data))
            total += len(data)
        return out

    def search_int(self, values: list[int], regions: list[dict] | None = None,
                   width: int = 4) -> dict[int, list[int]]:
        """在所有可读内存里找这些整数，返回 {值: [地址, ...]}。"""
        regs = regions if regions is not None else self.regions()
        hits: dict[int, list[int]] = {v: [] for v in values}
        for r in regs:
            data = self.read(r["base"], r["size"])
            if data is None:
                continue
            arr = np.frombuffer(data, dtype=np.uint8)
            for v in values:
                pat = np.frombuffer(struct.pack("<I", v)[:width], dtype=np.uint8)
                if len(pat) == 1:
                    idx = np.where(arr == pat[0])[0]
                else:
                    # 滑窗找模式
                    win = np.lib.stride_tricks.sliding_window_view(arr, len(pat))
                    m = (win == pat).all(axis=1)
                    idx = np.where(m)[0]
                hits[v].extend(int(r["base"]) + int(i) for i in idx)
        return hits


# --------------------------------------------------------------------------- #
# 差分
# --------------------------------------------------------------------------- #

def diff_words(a: list[tuple[int, bytes]], b: list[tuple[int, bytes]],
               align: int = 4) -> dict[int, np.ndarray]:
    """逐区域比较两份快照，返回 {base: 变化的**字**下标(bool 数组)}。"""
    amap = {base: data for base, data in a}
    out: dict[int, np.ndarray] = {}
    for base, db in b:
        da = amap.get(base)
        if da is None or len(da) != len(db):
            continue
        n = (len(da) // align) * align
        xa = np.frombuffer(da[:n], dtype=np.uint32)
        xb = np.frombuffer(db[:n], dtype=np.uint32)
        out[base] = (xa != xb)
    return out


def candidates(noise: dict[int, np.ndarray], target: dict[int, np.ndarray],
               raw_a: list[tuple[int, bytes]], raw_b: list[tuple[int, bytes]],
               small_max: int = 4096) -> list[dict]:
    """候选 = 目标态变了 **且** 噪声态没变 **且** 两侧值都是小整数。

    最后那个"小整数"过滤是很有用的一道筛子：UI 状态标志几乎都是 0/1/2 这种小值，
    而随机的动画噪声、时间戳、指针两边都是大数。
    """
    amap = {base: data for base, data in raw_a}
    bmap = {base: data for base, data in raw_b}
    out: list[dict] = []
    for base, tmask in target.items():
        nmask = noise.get(base)
        if nmask is None or len(nmask) != len(tmask):
            continue
        da, db = amap.get(base), bmap.get(base)
        if da is None or db is None:
            continue
        idx = np.where(tmask & ~nmask)[0]
        if not len(idx):
            continue
        ua = np.frombuffer(da[:len(tmask) * 4], dtype=np.uint32)
        ub = np.frombuffer(db[:len(tmask) * 4], dtype=np.uint32)
        for i in idx:
            va, vb = int(ua[i]), int(ub[i])
            if va < small_max and vb < small_max:
                out.append({"addr": base + i * 4, "a": va, "b": vb})
    return out


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _cmd_regions(a) -> int:
    with Rig() as rig:
        regs = rig.regions()
        total = sum(r["size"] for r in regs)
        names = {MEM_IMAGE: "image", MEM_PRIVATE: "private", MEM_MAPPED: "mapped"}
        print(f"pid={rig.pid}   可读区域 {len(regs)} 块，共 {total/1048576:.1f} MB")
        agg: dict[str, list[int]] = {}
        for r in regs:
            k = names.get(r["type"], str(r["type"]))
            agg.setdefault(k, []).append(r["size"])
        for k, v in agg.items():
            print(f"  {k:<8} {len(v):>4} 块  {sum(v)/1048576:>8.1f} MB")
        big = sorted(regs, key=lambda x: -x["size"])[:8]
        print("  最大的几块:")
        for r in big:
            print(f"    0x{r['base']:08X}  {r['size']/1048576:>7.1f} MB  "
                  f"prot=0x{r['protect']:02X} type={names.get(r['type'],'?')}")
    return 0


def _cmd_findint(a) -> int:
    with Rig() as rig:
        t0 = time.time()
        regs = rig.regions()
        vals = [int(v, 0) for v in a.value]
        hits = rig.search_int(vals, regs)
        for v, addrs in hits.items():
            print(f"  {v}: 命中 {len(addrs)} 处" + (f"  前几个 {[hex(x) for x in addrs[:8]]}"
                                                    if addrs else ""))
        print(f"  用时 {time.time()-t0:.1f}s（扫了 {sum(r['size'] for r in regs)/1048576:.0f} MB）")
        if a.out:
            json.dump({str(k): v for k, v in hits.items()},
                      open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            print("  已存", a.out)
    return 0


def _cmd_dialog_diff(a) -> int:
    """在战略面（无弹窗）和确认弹窗（有弹窗）之间做差分，找"弹窗标志"。"""
    from san9 import winio
    from san9.session import GameSession

    with Rig() as rig:
        regs = [r for r in rig.regions() if r["size"] >= 4096]
        print(f"参与差分的区域 {len(regs)} 块，"
              f"{sum(r['size'] for r in regs)/1048576:.0f} MB")

        s = GameSession()
        s.attach()
        s.focus()
        # 关闭一切面板，回到干净战略面
        for _ in range(4):
            gw, gh, _ = winio.grab(s.hwnd)
            if s.screen() == "strategy":
                break
            winio.press("esc")
            time.sleep(0.8)
        print("  当前 screen =", s.screen())

        def grab(tag):
            t0 = time.time()
            snap = rig.snapshot(regs)
            print(f"  快照 {tag}: {len(snap)} 块 "
                  f"{sum(len(d) for _, d in snap)/1048576:.0f} MB  {time.time()-t0:.1f}s")
            return snap

        print("① 抓噪声基线（两次，都不动游戏）")
        a1 = grab("A1")
        time.sleep(a.delay)
        a2 = grab("A2")
        noise = diff_words(a1, a2)
        n_noise = int(sum(int(m.sum()) for m in noise.values()))
        print(f"   噪声变化字 = {n_noise:,}")

        print("② 点「進行」弹出确认框")
        winio.click_client(s.hwnd, 1212, 863)
        time.sleep(1.8)
        print("   弹窗检测 =", s.role(s.screen()), " screen =", s.screen())
        b = grab("B")
        target = diff_words(a2, b)
        n_t = int(sum(int(m.sum()) for m in target.values()))
        print(f"   目标态变化字 = {n_t:,}")

        cand = candidates(noise, target, a2, b, small_max=a.small_max)
        print(f"\n候选地址 = {len(cand)} 个（已是小整数且非噪声）")
        for c in cand[:a.show]:
            print(f"    0x{c['addr']:08X}  无弹窗={c['a']}  有弹窗={c['b']}")

        if a.out and cand:
            json.dump(cand, open(a.out, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
            print("  已存", a.out)

        # 关掉弹窗，回到战略面
        winio.press("esc")
        time.sleep(1.2)
        print("\n  已 Esc 关闭弹窗，screen =", s.screen())
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="san9.memscan", description="内存扫描")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("regions", help="列可读内存区域")
    p.set_defaults(fn=_cmd_regions)

    p = sub.add_parser("findint", help="在内存里搜整数")
    p.add_argument("--value", nargs="+", required=True, help="一个或多个整数")
    p.add_argument("--out", help="把命中地址存成 json")
    p.set_defaults(fn=_cmd_findint)

    p = sub.add_parser("dialog-diff", help="差分法找弹窗标志")
    p.add_argument("--delay", type=float, default=1.5, help="两次噪声快照的间隔")
    p.add_argument("--small-max", type=int, default=4096, help="小整数的上限")
    p.add_argument("--show", type=int, default=40, help="打印多少个候选")
    p.add_argument("--out", default=os.path.join("logs", "dialog_candidates.json"))
    p.set_defaults(fn=_cmd_dialog_diff)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
