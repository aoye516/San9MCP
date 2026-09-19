# -*- coding: utf-8 -*-
"""把 OCR 读到的**选择器文字**锁定到候选表 —— 锁不上就不许拿去点。

## 为什么需要这个模块

文字分三类，判据是**这个字会不会被拿去点击**：

| 类型 | 举例 | 读错代价 | 策略 |
|---|---|---|---|
| 描述性文字 | 商人状态 / 事件正文 / 底栏提示 | ≈0（只给人看） | 原文照给（`atoms` 的 `values_text`）|
| **选择器文字** | **武将名 / 城名 / 命令名** | **高** —— 点错行会改游戏状态 | **本模块** |
| 数字 | 顶栏 / 面板值 | 高（算错账） | `glyph.py` 字形查表 |

## ⛔ 为什么是**三级**而不是「锁上 / 锁不上」

第一版做成二元判断，离线回归当场抓出一个**危险误锁**：

    OCR 读到「高异」（他势力武将，不在我方名册里）
    → 与名册里的「高昇」只共享一个「高」字，相似度 0.5
    → 但其他候选相似度全是 0，所以"间隔"高达 0.5 ⇒ 判定"唯一" ⇒ **锁上了**

**"间隔大"不等于"对"。** 候选表不全的时候（一覽屏显示的是全武将，而我的名册只有
我方 15 人），"最像的那个"完全可能只是碰巧共享一个字。

所以改成三级：

| `status` | 条件 | 调用方可以做什么 |
|---|---|---|
| `locked` | 归一后**精确相等**，或相似度 ≥ `HIGH_RATIO` | ✅ **可以拿去点** |
| `candidate` | 相似度 ≥ `MIN_RATIO` 且唯一（间隔够） | ⚠️ **不许点**。只能报给 agent，让它结合别的信息判断 |
| `unknown` | 都不满足 | ❌ 什么都不能做 |

**`candidate` 这一级是关键**：它承认"我看见一个名字但认不准"这个真实状态，
既不谎报成 `locked`（会点错），也不当成没看见（会丢信息）。

## 简繁与形近字：靠候选表的**别名**解决，不做自动转换

实测 `explore/intel/i5_wujiang.jpg`（武將一覽）OCR 读数 vs 真名：

    龚都→龔都 · 张燕→張燕      （纯简繁，归一后可精确匹配 —— 如果表里有简体别名）
    晟政→嚴政 · 系仲→孫仲      （OCR **形近字误认**，不是简繁）
    趟弘→趙弘 · 周食→周倉      （同上）
    韩遥→韓暹                （**两个字都错**，字面 0 重合）

⛔ **不引简繁转换库**：多一个依赖，而且转换表本身会出错（一简对多繁）。
⛔ **不从样本统计里归纳映射**（踩过这个坑：`商人='駐在'` 就是从统计归纳出的错规则）。
✅ 做法：候选表里**每个名字可以带一组别名**（`{"name": "龔都", "aka": ["龚都"]}`），
   别名来源是**实测记录**（这一帧 OCR 确实这么读的），属于"实测结论补回知识库"。
   表里没别名 ⇒ 顶多到 `candidate` 级，**绝不冒充 `locked`**。
"""
from __future__ import annotations

import difflib
import unicodedata

HIGH_RATIO = 0.99
"""达到这个相似度才算 `locked`（**可以点**）。

0.99 实际就是"归一后精确相等"。为什么定这么严：
`高异` vs `高昇` 相似度 0.5，`晟政` vs `嚴政` 也是 0.5 ——
**一个是误锁、一个是正确匹配，纯字面分不开**。既然分不开，就都不给 `locked`。
要让它们升到 `locked`，办法是**在候选表里补别名**（实测记录），不是放宽阈值。
"""

MIN_RATIO = 0.34
"""达到这个相似度才够进 `candidate`（**不可点**，仅供 agent 参考）。

为什么这么低：两字名里有一个字对上就是 0.5，能进；一个都对不上是 0.0，进不来。
`candidate` 反正不能拿去点，宽松一点只是多给 agent 一条线索。
"""

MIN_GAP = 0.08
"""`candidate` 还要求最像与第二像拉开这么多 —— 差不够 = **分不清是谁**。"""


def norm(s: str) -> str:
    """归一化：兼容分解（全角/半角）+ 去空白。**不做简繁转换**（见模块头）。"""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    return "".join(ch for ch in s if not ch.isspace())


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def _expand(candidates) -> list[tuple[str, list[str]]]:
    """把候选表统一成 `[(正名, [所有可匹配写法])]`。

    接受两种形态：
      - `["龔都", "張燕"]`                        —— 纯名字
      - `[{"name": "龔都", "aka": ["龚都"]}, …]`   —— 带别名（实测记录）
    """
    out = []
    for c in candidates or []:
        if isinstance(c, dict):
            name = c.get("name")
            if not name:
                continue
            forms = [name] + [a for a in (c.get("aka") or []) if a]
        else:
            if not c:
                continue
            name, forms = c, [c]
        out.append((name, forms))
    return out


def lock(raw: str, candidates) -> dict:
    """把 `raw` 锁到 `candidates` 里的一个。

    返回：
      - `status`   —— `locked` / `candidate` / `unknown`（**看这个，别看 locked 是否非空**）
      - `locked`   —— 只有 `status=="locked"` 时非空，**这才是可以拿去点的身份**
      - `nearest`  —— 最像的候选（`candidate` 级也会给，但**不可点**）
      - `raw`      —— OCR 原文，**永远保留**
      - `ratio` / `gap` / `runner_up` —— 判据数值，便于复核
      - `why`      —— 人话原因
    """
    r = norm(raw)
    pairs = _expand(candidates)
    base = {"status": "unknown", "locked": None, "nearest": None, "raw": raw,
            "ratio": None, "gap": None, "runner_up": None, "why": ""}
    if not r:
        base["why"] = "OCR 什么都没读到"
        return base
    if not pairs:
        base["why"] = "没有候选表 ⇒ 无从锁定（选择器文字**必须**有候选表）"
        return base

    # 每个候选取它**所有写法里最像的那个**分数
    scored = sorted(((max(_ratio(r, norm(f)) for f in forms), name)
                     for name, forms in pairs), reverse=True)
    best_ratio, best = scored[0]
    second_ratio, second = (scored[1] if len(scored) > 1 else (0.0, None))
    gap = best_ratio - second_ratio

    base.update({"ratio": round(best_ratio, 3), "gap": round(gap, 3),
                 "runner_up": second, "nearest": best})

    if best_ratio >= HIGH_RATIO:
        base["status"] = "locked"
        base["locked"] = best
        return base
    if best_ratio < MIN_RATIO:
        base["why"] = ("最像的候选是「%s」但相似度只有 %.2f < %.2f ⇒ "
                       "**这个名字大概不在候选表里**（他势力武将？换了屏？）"
                       % (best, best_ratio, MIN_RATIO))
        base["nearest"] = None
        return base
    if second is not None and gap < MIN_GAP:
        base["why"] = ("「%s」(%.2f) 与「%s」(%.2f) 只差 %.2f < %.2f ⇒ "
                       "**分不清是哪一个**"
                       % (best, best_ratio, second, second_ratio, gap, MIN_GAP))
        base["nearest"] = None
        return base

    base["status"] = "candidate"
    base["why"] = ("疑似「%s」（相似度 %.2f，未达 %.2f 的精确门槛）⇒ "
                   "**不可拿去点**。要升级成可点，请在候选表里给它补一条别名 %r"
                   % (best, best_ratio, HIGH_RATIO, raw))
    return base


def lock_many(raws, candidates, unique: bool = True) -> list[dict]:
    """批量锁定（一屏的一列名字）。

    `unique=True`：一个候选只许被 **`locked` 级**占用一次 ——
    同一屏不可能有两行是同一个人。按 `ratio` 从高到低分配，
    "最有把握的那一行"先拿名额，被占走的候选从后续表里剔除。

    ⚠️ `candidate` 级**不占名额** —— 它本来就不是定论，不该排挤别人。
    """
    raws = list(raws or [])
    if not unique:
        return [lock(raw, candidates) for raw in raws]

    pool = _expand(candidates)
    results: list[dict | None] = [None] * len(raws)

    order = []
    for i, raw in enumerate(raws):
        r = lock(raw, [{"name": n, "aka": f[1:]} for n, f in pool])
        order.append((r["ratio"] if r["ratio"] is not None else -1.0, i))
    order.sort(reverse=True)

    for _s, i in order:
        cur = [{"name": n, "aka": f[1:]} for n, f in pool]
        r = lock(raws[i], cur)
        results[i] = r
        if r["status"] == "locked":
            pool = [(n, f) for n, f in pool if n != r["locked"]]
    return [r if r is not None else lock("", []) for r in results]
