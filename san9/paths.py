"""所有产出的路径总表。

为什么要单独一个模块
--------------------
项目目录里带中文（`WorkBuddy/三国志9`）。很多图像库（OpenCV 是最典型的）
遇到非 ASCII 路径会**静默失败** —— 不报错，就是不干活。
我们已经踩过一次（录了 100 帧，frames/ 是空的）。

所以规则是：

    代码、文档、配置  → 留在项目目录（人要看，编辑器要能打开）
    所有生成的产出    → 放到纯英文的数据目录

数据目录默认是用户主目录下的 san9ai，可以用环境变量 SAN9_DATA 覆盖。
"""
from __future__ import annotations

import os

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 纯英文、无空格、短 —— 不要改成一个"好看"的名字，这个目录是给程序用的
DEFAULT_DATA = os.path.join(os.path.expanduser("~"), "san9ai")
DATA = os.environ.get("SAN9_DATA") or DEFAULT_DATA

SHOTS = os.path.join(DATA, "shots")        # 截图、核对图
REC = os.path.join(DATA, "rec")            # 录制素材（帧 + 模板）
RUNS = os.path.join(DATA, "runs")          # 每次 benchmark 跑的轨迹
SNAPS = os.path.join(DATA, "snapshots")    # 存档检查点
LOGS = os.path.join(DATA, "logs")          # 杂项日志、配置备份

# 小体积的 JSON 配置留在项目里，方便人直接看和改
CONFIG = os.path.join(PROJECT, "config")

ALL_DIRS = [SHOTS, REC, RUNS, SNAPS, LOGS]


def ensure() -> None:
    for d in ALL_DIRS:
        os.makedirs(d, exist_ok=True)
    os.makedirs(CONFIG, exist_ok=True)


def ascii_safe(path: str) -> bool:
    """这个路径能不能安全交给 OpenCV 之类的库。"""
    try:
        path.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def store(path: str) -> str:
    """把绝对路径转成存进 JSON 的形式：数据目录内用相对路径，其余用绝对路径。

    存相对路径是为了整个数据目录能整体搬走。
    """
    ap = os.path.abspath(path)
    try:
        rel = os.path.relpath(ap, DATA)
    except ValueError:
        return ap
    if rel.startswith(".."):
        return ap
    return rel.replace("\\", "/")


def resolve(path: str) -> str:
    """还原 store() 存下来的路径。

    ⚠️ 兼容两种来源，**必须两处都找**：
      · 数据文件（截图/录像/轨迹）→ `DATA/<path>`（纯英文目录，cv2 才不出问题）
      · **模板和配置资产** → `PROJECT/config/<path>`（人要看、要跟代码一起版本管理）

    这条 fallback 是必需的：`config/paths.json` 里写的 `templates/confirm_yes.jpg`
    指的是 `config/templates/confirm_yes.jpg`，而 `DATA` 下**根本没有 templates 目录**。
    只找 DATA 的话，所有带 `template` / `if_template` 的路径步骤都会
    在 `load_rgb` 里抛"读图失败"—— 而且是**运行到那一步才炸**，非常难查。
    """
    if not path:
        return path
    if os.path.isabs(path):
        return path
    under_data = os.path.normpath(os.path.join(DATA, path))
    if os.path.exists(under_data):
        return under_data
    under_cfg = os.path.normpath(os.path.join(PROJECT, "config", path))
    if os.path.exists(under_cfg):
        return under_cfg
    return under_data          # 都不存在 → 返回 DATA 版，让报错指向预期位置


_SHOWN = False


def report() -> str:
    global _SHOWN
    _SHOWN = True
    lines = [f"数据目录 : {DATA}", f"  纯英文 : {'是' if ascii_safe(DATA) else '否 ⚠️'}",
             f"代码目录 : {PROJECT}"]
    return "\n".join(lines)
