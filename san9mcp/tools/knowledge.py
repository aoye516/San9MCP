# -*- coding: utf-8 -*-
"""B 组 · 知识（不碰游戏，纯资料）。

⭐ 2026-09-19 改：加 `brief` / `filter`，并把默认 section 从 `all` 改成 `tools`。
原因：实测 `san9_docs("all")` 输出 **28120 字符** —— agent 调一次就吃掉四分之一上下文，
而它本来是个"**规划前顺手看一眼**"的便宜工具。默认值必须便宜，要细节再单独查。
"""
from __future__ import annotations

import io
import json
import os

from san9 import actions as A
from san9 import paths

from san9mcp import registry, runtime

COMMANDS_JSON = os.path.join(paths.CONFIG, "commands.json")

# ⛔ **不写死输出体积** —— 数字是贬值最快的东西（数据一涨就变成误导）。
# 改成返回前**动态估算**当前这份输出有多大，见 `_cost_note()`。
BIG_OUTPUT = 12000          # 超过这个字符数就在返回里给一句提醒


@registry.tool(
    "san9_docs", group="B", tier="both",
    summary="工具目录 + 游戏命令目录（六类 35 条）+ 语义动作表。**规划前调一次**，"
            "不碰游戏、很便宜。里面也列出**尚未实现**的工具，方便知道边界在哪。"
            "⭐ 默认只给 `tools` 那一段（约 3 千字符）；`commands` / `actions` "
            "要**单独点名**才给，而且默认是**精要版** —— 因为全量三段加起来有 2.8 万字符，"
            "会吃掉大量上下文。想查某一条命令直接用 `filter`。",
    schema={"type": "object", "properties": {
        "section": {
            "type": "string", "enum": ["tools", "commands", "actions", "all"],
            "default": "tools",
            "description": "查哪一段。`tools`=工具目录（默认，最便宜）· "
                           "`commands`=35 条游戏命令 · `actions`=语义动作表 · "
                           "`all`=全给（⚠️ 约 2.8 万字符，非必要别用）"},
        "brief": {
            "type": "boolean", "default": True,
            "description": "true=精要版（名字 / 状态 / 一句话，省上下文）· "
                           "false=带全部输入要求与参数表"},
        "filter": {
            "type": "string",
            "description": "关键词过滤。**繁中和英文都认**：命令中文名（`巡察`）· "
                           "英文 op 名（`city.patrol`）· 说明文字 · 分类 · 阵型名"
                           "（`魚鱗`）。**想查一条就用它**，比拉全表便宜得多。"
                           "带 filter 时不相关的大表（如阵型表）不会再跟着返回"},
    }})
def san9_docs(section: str = "tools", brief: bool = True,
              filter: str | None = None):
    section = (section or "tools").strip() or "tools"
    out: dict = {"section": section, "brief": bool(brief)}
    if filter:
        out["filter"] = filter

    if section in ("all", "tools"):
        out["tools"] = registry.catalogue()
    if section in ("all", "commands"):
        out["commands"] = _commands(brief=brief, filt=filter)
    if section in ("all", "actions"):
        try:
            out["actions"] = _actions(brief=brief, filt=filter)
        except Exception as e:
            out["actions_error"] = "%s: %s" % (type(e).__name__, e)

    # 告诉 agent「你这次拉的量级」—— 它自己就有上下文预算要管，把账摊开比藏着好。
    # ⚠️ 数字是**当场算出来的**，不是写死的（写死的一涨就变成误导）。
    out.update(_cost_note(out))
    return runtime.ok(**out)


def _cost_note(out: dict) -> dict:
    """估算这份输出有多大，太大的话给一句怎么省。**不写死数字。**"""
    try:
        n = len(json.dumps(out, ensure_ascii=False))
    except Exception:
        return {}
    if n <= BIG_OUTPUT:
        return {"size_chars": n}
    return {"size_chars": n,
            "cost_warning": "本次输出约 %d 字符（偏大）。想省上下文："
                            "`section=\"tools\"` 是最便宜的一段（约 3 千字符）· "
                            "`filter` 能精准查一条 · `brief=true`（默认）已去掉参数表"
                            % n}


def _match(filt: str, *fields) -> bool:
    """⭐ 2026-09-19 修：filter 要**同时认英文 op 名和中文说明**。

    病因（实测）：旧实现 `filt in o["op"]` 只搜 `city.patrol` 这种英文 op 名，
    而 agent 手里握的是**游戏里的繁中词**（巡察 / 商業 / 徵兵）。
    `filter=巡察` 返回 `ops: []` —— 明明存在（`city.patrol`），却报查不到。
    同一个工具里 `_commands()` 搜的是中文命令名，两段语义**不一致**，更容易踩。

    规矩：任一字段命中即算命中；空字段跳过，不参与判定。
    """
    if not filt:
        return True
    needle = filt.strip().lower()
    if not needle:
        return True
    for v in fields:
        if v and needle in str(v).lower():
            return True
    return False


def _commands(brief: bool = True, filt: str | None = None) -> dict:
    try:
        with io.open(COMMANDS_JSON, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        return {"error": "读 %s 失败：%s" % (COMMANDS_JSON, e)}

    menus: dict = {}
    total = 0
    for menu, m in (raw.get("menus") or {}).items():
        items = []
        for it in m.get("items") or []:
            name = it.get("name") or ""
            # 走统一匹配器：命令中文名 / 所属菜单名 都算命中（原先只搜 name）
            if not _match(filt, name, menu):
                continue
            total += 1
            row = {"name": name, "status": it.get("status")}
            if not brief:
                row["inputs"] = it.get("inputs") or {}
                if it.get("enabled_default") is not None:
                    row["enabled_default"] = it.get("enabled_default")
            items.append(row)
        if items:
            menus[menu] = items

    n = sum(len(v) for v in menus.values())
    out = {"source": "config/commands.json", "count": n, "total": total,
           "menus": menus,
           "note": "`status` 是**我们下达验证**的进度（verified/partial/blocked/"
                   "unverified），**不是游戏里的可用性** —— 要问\"这城现在能不能下\"，"
                   "用 `san9_menu`（它会读底栏和置灰）。"
                   "`brief=false` 才给 inputs（输入要求）。"}
    if filt and not n:
        out["hint"] = "没有命令名含 %r。命令名是繁中（如 巡察 / 商業 / 開墾 / 徵兵）" % filt
    return out


def _actions(brief: bool = True, filt: str | None = None) -> dict:
    cat = A.catalogue()
    if filt:
        # ⭐ 修 ①：同时匹配英文 op 名、中文 doc、分类名（旧实现只搜 op 名）
        cat["ops"] = [o for o in (cat.get("ops") or [])
                      if _match(filt, o.get("op"), o.get("doc"), o.get("category"))]
        cat["meta_ops"] = [o for o in (cat.get("meta_ops") or [])
                           if _match(filt, o.get("op"), o.get("doc"),
                                     o.get("category"))]
        # ⭐ 修 ②：`formations`（阵型表）**不受 filter 约束**，过去会原样全量带回 ——
        # 查一条命令却收到整张阵型表，输出里绝大部分是噪音（实测 2678 字符几乎全是它）。
        # 阵型名本身也可被搜（如 `filter=魚鱗`），命中就留，否则整段丢掉。
        forms = cat.get("formations") or {}
        kept = {k: v for k, v in forms.items() if _match(filt, k, v.get("特點"))}
        if kept:
            cat["formations"] = kept
        else:
            cat.pop("formations", None)
    if brief:
        # 41 个 op 的 `params` 是 17K 字符的**主要来源**；精要版去掉它。
        # 想知道某个 op 怎么传参 → `brief=false` + `filter`。
        cat["ops"] = [{"op": o.get("op"), "category": o.get("category"),
                       "doc": o.get("doc")} for o in (cat.get("ops") or [])]
        cat["note"] = ("精要版：去掉了每个 op 的 `params`（那是输出主体的来源）。"
                       "要某条的参数表就用 `brief=false` + `filter`。")
    if filt and not (cat.get("ops") or cat.get("meta_ops")
                     or cat.get("formations")):
        # 报病因不报症状：告诉它**能搜什么**，而不是干巴巴一句没找到
        cat["hint"] = ("没有条目含 %r。可搜：英文 op 名（`city.patrol`）· "
                       "繁中说明里的词（`巡察` / `徵兵`）· 分类名 · 阵型名（`魚鱗`）"
                       % filt)
    return cat
