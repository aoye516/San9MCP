"""领域词典 + OCR 纠错。

为什么需要
----------
RapidOCR 对这套游戏字体的小字会读错一两个字，实测例子：

    上旬 → 上甸    資金 → 青金    兵糧 → 兵程    疆界 → 强界
    平原 → 垂平原  南皮 → 晋南皮  北海 → 無北海  東萊港 → 東莱港

单字错不影响判断前提是**候选集是封闭的** —— 三国志9 的界面词汇就那么几百个。
所以：先 OCR，再拿候选词表做模糊匹配纠正，比硬调 OCR 参数划算得多。

匹配顺序（从可靠到宽松）
------------------------
1. 完全命中
2. 去掉非汉字后的结果命中
3. **包含**某个候选词（取最长）—— 干掉"垂平原""晋南皮"这类前后粘连的噪声
4. 编辑距离 1 以内的候选（"信至"→"信望"、"青金"→"資金"）
5. 都不中 → 返回原文 + `low`，**不要假装认出来了**
"""

from __future__ import annotations

import difflib
import re

# ---- 界面固定词汇（高可信，来自说明书与实测）
LABELS = [
    "上旬", "中旬", "下旬", "信望", "資金", "兵糧", "疆界", "情報", "功能",
    "進行", "靜止", "地域", "季節", "春", "夏", "秋", "冬",
    "命令設施", "執行武將", "武將", "智力", "統率", "武力", "政治", "魅力",
    "身分", "學得", "民心", "收益", "收穫", "耐久", "士氣", "士兵", "傷兵",
    "兵役人口", "人口", "在任", "俘虜", "在野", "商人", "市價", "增加兵役",
    "資金收入", "兵糧收入", "到達預定", "總括選擇", "總括解除", "決定", "中止",
    "執行", "撤除", "大臣", "都督", "軍團", "都市", "施設", "部隊", "一覽",
    "全都市", "全武將", "全勢力", "開始", "結束",
]

# ---- 六大类 + 36 条命令（与 actions.py 的 op 一一对应）
MENUS = ["設施", "軍事", "人材", "計略", "外交", "任免"]
COMMANDS = [
    "巡察", "商業", "開墾", "修築", "徵兵", "訓練", "買進", "賣出", "撤除",
    "出征", "建設", "輸送",
    "召來", "移動", "探索", "登庸",
    "離間", "燒夷", "奪取", "流言", "偽報", "擾亂", "激勵", "救援",
    "贈與", "請求", "交還", "勸降",
    "委任", "軍師", "爵位", "褒獎", "授與", "沒收", "處斬", "流放",
]

# ---- 已观测到的我方城市/港口（会被 OCR 投票结果覆盖/扩充）
OBSERVED_PLACES = [
    "平原", "南皮", "鄴", "北海", "濮陽", "陳留", "小沛", "許昌",
    "樂陵港", "安德港", "高唐港", "白馬港", "東萊港", "臨淄港",
]

# ---- 候補城市名 ----
# ⚠️ 2026-09-19 订正：本表原来有一批**与游戏实际字形不符**的条目（凭记忆写的）。
# 依据是 `config/roster_facilities.json`（185 条全量地域表，外部攻略 + Unihan 推繁體，
# 其中 6 条经实测帧验收）。订正的都是"错字形会把正确读数拉错"的那种：
#
#     合肥 → 合淝   琅邪 → 琅玡   鉅鹿 → 巨鹿   譙  → 譙縣
#     上庸 → 上庸港  巫縣 → 巫縣港  廣陵 → 廣陵港  曲阿 → 曲阿港
#     潯陽 → 尋陽港  （字形不同，不是簡繁）
#     汜水關、東萊 → **删**（同一地里已有正确形式：虎牢關 / OBSERVED_PLACES 的東萊港）
#
# ⛔ 别把 VOCAB 当"越大越好"：`namelock` 的实测结论是**候选表越脏，误锁越多**
# （`scripts/compare_place_candidates.py` 量过：185 条全量表在輸送一覽帧上
# locked=11，而 15 条的实测精选表 locked=13）。本表只留能说清来源的名字。
CANDIDATE_PLACES = [
    "洛陽", "長安", "許昌", "鄴", "平原", "南皮", "北海", "濮陽", "陳留",
    "小沛", "下邳", "壽春", "合淝", "建業", "吳", "會稽", "廬江", "柴桑",
    "江夏", "襄陽", "新野", "宛", "上庸港", "漢中", "梓潼", "成都", "江州",
    "永安", "建寧", "雲南", "天水", "武威", "安定", "金城", "弘農",
    "晉陽", "上黨", "虎牢關", "潼關", "劍閣", "陽平關", "葭萌關",
    "江陵", "武陵", "長沙", "桂陽", "零陵", "南海", "交趾", "北平", "薊",
    "漁陽", "代縣", "巫縣港", "麥城", "汝南", "譙縣", "彭城",
    "廣陵港", "曲阿港", "尋陽港", "宜都",
    "隴西", "酒泉", "張掖", "雁門", "巨鹿", "常山", "清河", "琅玡",
    # ---- ⚠️ 以下 17 条在 185 条全量表里**找不到** ----
    # （185 = 本作全部地域：都市 45 · 港 34 · 关 8 · 城塞 1 · 异族 4 · 县 93）
    # 疑为凭记忆写错、或记混了别的代。⛔ 没删的理由：185 表是外部来源
    # （`membership_unverified`），删了万一它漏了会造成退化。
    # 但它们**没经过任何实测帧或游戏本体核对**，用的时候别当回事。
    "中山", "五原", "南鄭", "城陽", "山陰", "房陵", "敦煌",
    "新淦", "朔方", "東海", "樂安", "濟南", "白帝", "西涼", "豫章",
    "錢塘", "雲中",
]

VOCAB: list[str] = sorted(set(LABELS + MENUS + COMMANDS + OBSERVED_PLACES
                             + CANDIDATE_PLACES), key=len, reverse=True)

# 高频界面词优先 —— 两个候选都只差 1 个字时（"上甸"→上旬 vs 上黨），取优先级高的
PRIORITY = [
    "上旬", "中旬", "下旬", "信望", "資金", "兵糧", "疆界", "情報", "功能",
    "進行", "靜止", "地域", "執行", "中止", "決定", "武將", "智力", "統率",
] + MENUS + COMMANDS

_PRIO = {w: i for i, w in enumerate(PRIORITY)}

CJK = re.compile(r"[\u4e00-\u9fff]")


def _only_cjk(s: str) -> str:
    return "".join(CJK.findall(s))


def _edit1(a: str, b: str) -> bool:
    """编辑距离 ≤ 1（含替换/插入/删除）。"""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        return sum(1 for x, y in zip(a, b) if x != y) <= 1
    if la > lb:
        a, b = b, a
        la, lb = lb, la
    i = j = 0
    skipped = False
    while i < la and j < lb:
        if a[i] == b[j]:
            i += 1
            j += 1
        elif skipped:
            return False
        else:
            skipped = True
            j += 1
    return True


def correct(text: str, vocab: list[str] | None = None,
            allow_fuzzy: bool = True) -> tuple[str, float, str]:
    """把 OCR 文本纠正到候选词。返回 (纠正后文本, 置信度, 方式)。

    置信度：1.0 完全命中 / 0.9 去噪后命中 / 0.85 包含命中 / 0.7 编辑距离1 / 0.0 没纠到
    """
    if not text:
        return text, 0.0, "empty"
    vocab = vocab or VOCAB
    t = text.strip()
    if t in vocab:
        return t, 1.0, "exact"
    c = _only_cjk(t)
    if c and c in vocab:
        return c, 0.9, "strip"
    # 包含（取最长的候选，避免"鄴"吃掉别的东西）
    hits = [w for w in vocab if len(w) >= 2 and w in c]
    if hits:
        best = max(hits, key=len)
        return best, 0.85, "contains"
    if allow_fuzzy and len(c) >= 2:
        # 长度接近的候选里找编辑距离 1
        near = [w for w in vocab if abs(len(w) - len(c)) <= 1 and _edit1(c, w)]
        if near:
            # 高频界面词优先，其次取更长/在优先表里更靠前的
            best = min(near, key=lambda w: (_PRIO.get(w, 999), -len(w)))
            return best, 0.7, "edit1"
        m = difflib.get_close_matches(c, vocab, n=1, cutoff=0.6)
        if m:
            return m[0], 0.5, "fuzzy"
    return text, 0.0, "none"


def correct_many(texts: list[str]) -> list[tuple[str, float, str]]:
    return [correct(t) for t in texts]
