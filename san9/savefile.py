"""三国志9 存档文件（D_SavXXX.S9 / D_AutoXX.S9）解析。

已确认的结构（San9WPK / Steam 中文版，205110 字节固定大小）：

    offset 0    'RIFF'  <u32 长度>  'S9SVhead'      <- 8 字节的 RIFF form type
    offset 0x14 S9SVhead 块：112 字节头部（含日期、剧本、势力等）
    之后 13 个自描述子块：4 字节 id + u32 长度 + 定长数据

      work   1024     工作区/临时
      wrld     82     世界地图
      bldg   9600     工作物（阵/砦/箭楼/港口…）
      city   3100     都市
      forc   4600     势力
      item   1200     宝物
      prsn  95200     武将   <- 最大的表
      unit  35700     部队
      turk  17800     异民族
      trki   2360
      uhes    944
      hesi   7600
      nenp  25664     年表/历史

也就是说：**存档是一个自描述的定长记录容器**，块表可以 100% 无损解出来，
不需要猜偏移。每个块内部是定长记录数组，需要再逆一次记录布局。

注意：目前观测到负载里有大量填充字节（0x2E / 0x12 / 0x9E），
且看不到明文汉字 → 武将姓名很可能是对 G_Name.s9 的索引而不是字符串。
记录布局的逆向是 Phase 2 的活；本模块先提供"块表 + 原始访问"。
"""
from __future__ import annotations

import os
import re
import struct
from dataclasses import dataclass

MAGIC = b"RIFF"
FORM_TYPE = b"S9SVhead"
_SAVE_ID_RE = re.compile(rb"^[\x20-\x7e]{4}$")

# 块 id -> 用途说明
CHUNK_NAMES = {
    "head": "存档头（日期/剧本/势力/君主）",
    "work": "工作区 / 临时状态",
    "wrld": "世界地图状态",
    "bldg": "工作物（阵 砦 箭楼 港口 关 …）",
    "city": "都市",
    "forc": "势力",
    "item": "宝物",
    "prsn": "武将",
    "unit": "部队（出征中）",
    "turk": "异民族 / 贼兵",
    "trki": "异民族相关",
    "uhes": "未知（占位）",
    "hesi": "未知（占位）",
    "nenp": "年表 / 历史记录",
}


@dataclass
class Chunk:
    cid: str
    offset: int
    size: int
    name: str = ""

    @property
    def end(self) -> int:
        return self.offset + self.size

    def __repr__(self) -> str:
        return f"<Chunk {self.cid!r} @0x{self.offset:X} size={self.size}>"


class SaveFile:
    """RIFF 容器解析器。`data` 是整文件字节。"""

    def __init__(self, path: str):
        self.path = path
        with open(path, "rb") as f:
            self.data = f.read()
        self.riff_size: int = 0
        self.form_type: bytes = b""
        self.chunks: list[Chunk] = []
        self._parse()

    # -------------------------------------------------- 解析

    def _parse(self) -> None:
        d = self.data
        if d[:4] != MAGIC:
            raise ValueError(f"不是 RIFF 文件：{self.path}")
        self.riff_size = struct.unpack_from("<I", d, 4)[0]
        self.form_type = d[8:16]

        # RIFF form type 是 8 字节 'S9SVhead'，紧跟其后就是第一个块的长度
        head_size = struct.unpack_from("<I", d, 16)[0]
        self.chunks.append(Chunk("head", 20, head_size, CHUNK_NAMES["head"]))

        off = 20 + head_size
        while off + 8 <= len(d):
            cid = d[off : off + 4]
            size = struct.unpack_from("<I", d, off + 4)[0]
            if not _SAVE_ID_RE.match(cid):
                break
            name = cid.decode("latin1")
            self.chunks.append(Chunk(name, off + 8, size, CHUNK_NAMES.get(name, "?")))
            off += 8 + size

        self.trailing = len(d) - off  # 正常应为 0

    # -------------------------------------------------- 访问

    @property
    def header(self) -> bytes:
        return self.data[self.chunks[0].offset : self.chunks[0].end]

    def chunk(self, cid: str) -> Chunk | None:
        for c in self.chunks:
            if c.cid == cid:
                return c
        return None

    def payload(self, cid: str) -> bytes:
        c = self.chunk(cid)
        if c is None:
            raise KeyError(cid)
        return self.data[c.offset : c.end]

    def dump_map(self) -> str:
        lines = [
            f"{os.path.basename(self.path)}  {len(self.data)} 字节  "
            f"form={self.form_type.decode('latin1')}  riff_size=0x{self.riff_size:X}",
        ]
        if getattr(self, "trailing", 0):
            lines.append(f"!! 尾巴多出 {self.trailing} 字节（解析可能有误）")
        for c in self.chunks:
            lines.append(
                f"  @0x{c.offset:06X}  {c.cid:<5} {c.size:>7}  {c.name}"
            )
        return "\n".join(lines)

    # -------------------------------------------------- 候选记录布局

    def guess_record_size(self, cid: str, lo: int = 4, hi: int = 4096) -> list[tuple[int, int]]:
        """猜定长记录的步长。

        做法：对候选步长 s，看所有 s 位置上的字节是否落在"合理"集合里
        （观测到大量 0x2E / 0x12 / 0x9E 填充）。返回 (步长, 命中率) 降序。
        """
        buf = self.payload(cid)
        n = len(buf)
        filler = {0x2E, 0x12, 0x9E, 0x00, 0x20}
        out = []
        for s in range(lo, hi + 1):
            if n // s < 4:
                break
            hits = sum(1 for i in range(0, n, s) if buf[i] in filler)
            cnt = len(range(0, n, s))
            out.append((s, hits / cnt))
        out.sort(key=lambda x: (-x[1], x[0]))
        return out[:20]


def find_save_dir() -> str:
    """在 我的文档\\Koei\\35th 下找到真正在用的存档目录（目录名可能是乱码）。"""
    base = os.path.join(os.path.expanduser("~"), "Documents", "Koei", "35th")
    if not os.path.isdir(base):
        # 有些机器 OneDrive 重定向了"文档"
        alt = os.path.join(os.path.expanduser("~"), "OneDrive", "Documents", "Koei", "35th")
        base = alt if os.path.isdir(alt) else base
    for name in os.listdir(base):
        p = os.path.join(base, name)
        if os.path.isdir(os.path.join(p, "Savedata")):
            return p
    raise FileNotFoundError(f"没找到 Savedata 目录：{base}")


def list_saves() -> dict[str, str]:
    """返回 {文件名: 完整路径}，D_Sav000.S9 是第 1 格，D_Auto00.S9 是自动存档。"""
    sd = os.path.join(find_save_dir(), "Savedata")
    return {f: os.path.join(sd, f) for f in sorted(os.listdir(sd)) if f.upper().endswith(".S9")}


if __name__ == "__main__":
    import sys

    try:
        saves = list_saves()
        print("存档目录:", find_save_dir())
        for name, path in saves.items():
            s = SaveFile(path)
            print(f"\n=== {name} ===")
            print(s.dump_map())
    except Exception as e:
        print("失败:", e)
        sys.exit(1)
