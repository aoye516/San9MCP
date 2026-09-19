"""语义动作层：agent 说的"命令"，不是鼠标坐标。

设计要点
--------
agent 永远不应该看到像素。它看到的应该是《三国志9》自己的命令语义：

    {"op": "city.patrol", "city": "洛陽", "officers": ["張角", "張寶"]}

下面这一层负责：
  1. 校验参数（缺参数、写了不存在的城、武将数超上限……都在这里挡掉）
  2. 映射到菜单路径（`menu` 字段，交给 driver 翻译成点击序列）
  3. 声明这条命令是"立即生效"还是"延迟生效"，供 driver 决定要不要等

为什么要挡在前面：一次非法动作在真实游戏里可能表现为"点了没反应"，
agent 会以为成功了然后一路错下去。宁可在这里报错。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MAX_OFFICERS_PER_ORDER = 5  # 手册：内政类命令每城每旬最多 5 人


@dataclass
class Param:
    name: str
    type: str  # str / int / list[str] / list[unit] / bool
    required: bool = True
    default: Any = None
    doc: str = ""


@dataclass
class Op:
    name: str
    category: str  # 設施 / 軍事 / 人材 / 計略 / 外交 / 任免
    menu: tuple[str, ...]  # 菜单路径键，driver 用它在 paths.json 里找坐标序列
    delay: bool  # True = 延迟生效（军事/人材/计略/外交），False = 立即生效
    params: list[Param]
    doc: str = ""
    mutates_state: bool = True
    verify: str = ""  # 期望的状态变化描述，driver 用它做校验提示


def _p(n: str, t: str, required: bool = True, default: Any = None, doc: str = "") -> Param:
    return Param(n, t, required, default, doc)


_CITY = _p("city", "str", required=False,
           doc="目标都市名（繁中，如 '洛陽'）。"
               "**或者改用 `city_row`**（右侧「移動列表」里的行号 0 起）—— "
               "名字→行号的映射要 OCR，行号不需要，长流程里用行号更稳。")
_OFFS = _p("officers", "list[str]", required=False,
           doc="执行武將姓名列表，1..5 人。"
               "**或者用 `all_officers: true` 全选**（菜鸟策略就用这个）。")

OPS: dict[str, Op] = {}


def _reg(op: Op) -> None:
    OPS[op.name] = op


# ------------------------------------------------------------------ 設施（立即生效）

_reg(Op("city.patrol", "設施", ("facility", "patrol"), False, [_CITY, _OFFS],
        "巡察：提高民心，民心→人口→兵役/收入。开局必做。", verify="民心↑"))
_reg(Op("city.commerce", "設施", ("facility", "commerce"), False, [_CITY, _OFFS],
        "商業：提高收益（金收入），政治力越高越好。", verify="收益↑"))
_reg(Op("city.farm", "設施", ("facility", "farm"), False, [_CITY, _OFFS],
        "開墾：提高收穫（兵糧收入），政治力越高越好。", verify="收穫↑"))
_reg(Op("city.fortify", "設施", ("facility", "fortify"), False, [_CITY, _OFFS],
        "修築：提高耐久度，統率力越高越好；同时涨攻城/智略熟练度。", verify="耐久↑"))
_reg(Op("city.draft", "設施", ("facility", "draft"), False, [_CITY, _OFFS],
        "徵兵：+士兵 -兵役人口 -信望。统率力决定一次能征多少。", verify="士兵↑ 兵役人口↓"))
_reg(Op("city.train", "設施", ("facility", "train"), False, [_CITY, _OFFS],
        "訓練：提高士气，武力越高越好；涨步兵/骑兵/弓骑/弩兵熟练度。", verify="士氣↑"))
_reg(Op("city.buy_food", "設施", ("facility", "buy"), False,
        [_CITY, _OFFS, _p("gold", "int", required=False, doc="花多少钱；省略则用界面默认最大量")],
        "買進：用金换兵糧。7 月丰收后粮价最低，是买点。", verify="兵糧↑ 資金↓"))
_reg(Op("city.sell_food", "設施", ("facility", "sell"), False,
        [_CITY, _OFFS, _p("food", "int", required=False, doc="卖多少糧；省略则用界面默认最大量")],
        "賣出：用兵糧换金。次年春粮价最高，是卖点。", verify="資金↑ 兵糧↓"))
_reg(Op("city.demolish", "設施", ("facility", "demolish"), False,
        [_CITY, _OFFS, _p("target", "str", doc="要拆的自建工作物")],
        "撤除：拆掉自己建的设施。", verify="設施消失"))

# ------------------------------------------------------------------ 軍事（延迟生效）

_reg(Op("army.deploy", "軍事", ("military", "deploy"), True,
        [_p("from_city", "str", doc="出发都市"), _p("units", "list[unit]", doc="部队列表"),
         _p("target", "str", doc="目标都市/地域/设施名"),
         _p("relay", "str", required=False, doc="中继点（PK 特有，用于迂回/夹击）")],
        "出征：1 部队最多 5 人。阵形决定攻击/守备/机动/视野，出征后野外不能改。"))
_reg(Op("army.transport", "軍事", ("military", "transport"), True,
        [_p("from_city", "str"), _p("to_city", "str"), _p("officers", "list[str]"),
         _p("troops", "int", required=False, doc="运送士兵数，省略则全带")],
        "輸送：把士兵送到自势力设施。"))
_reg(Op("army.build", "軍事", ("military", "build"), True,
        [_p("from_city", "str"), _p("officers", "list[str]"),
         _p("structure", "str", doc="陣/砦/箭樓/城塞/柵欄/土壘/石兵/土砂"),
         _p("target_region", "str", doc="建设地点")],
        "建設：建工作物。带「營造」的武将可省费用。"))

# ------------------------------------------------------------------ 人材

_reg(Op("talent.summon", "人材", ("talent", "summon"), True, [_CITY, _OFFS],
        "召來：把武将召到发令设施。"))
_reg(Op("talent.move", "人材", ("talent", "move"), True,
        [_p("from_city", "str"), _p("to_city", "str"), _OFFS],
        "移動：把武将调到别的设施。"))
_reg(Op("talent.search", "人材", ("talent", "search"), True,
        [_CITY, _OFFS, _p("region", "str", doc="探索目标地域")],
        "探索：可能发现武将/资金/宝物/事件。政治力越高越容易发现。"))
_reg(Op("talent.recruit", "人材", ("talent", "recruit"), True,
        [_CITY, _OFFS, _p("target_officer", "str", doc="想登庸的武将")],
        "登庸：说服在野/俘虏/他势力武将。信望越高越容易。"))

# ------------------------------------------------------------------ 計略

_reg(Op("strat.rumor", "計略", ("strategy", "rumor"), True,
        [_CITY, _OFFS, _p("target_faction", "str"), _p("against_faction", "str")],
        "流言：降低目标势力与另一势力的友好度。"))
_reg(Op("strat.sow_discord", "計略", ("strategy", "discord"), True,
        [_CITY, _OFFS, _p("target_officer", "str"), _p("target_faction", "str")],
        "離間：降低敌将忠诚度。智力越高越容易。"))
_reg(Op("strat.burn", "計略", ("strategy", "burn"), True,
        [_CITY, _OFFS, _p("target_structure", "str")],
        "燒夷：烧敌设施耐久并减其兵糧。武力越高越容易。"))
_reg(Op("strat.seize", "計略", ("strategy", "seize"), True,
        [_CITY, _OFFS, _p("target_structure", "str")],
        "奪取：偷敌设施金錢。武力越高越容易。"))
_reg(Op("strat.fake_report", "計略", ("strategy", "fake_report"), True,
        [_CITY, _OFFS, _p("target_unit", "str")],
        "偽報：让敌方部队撤退。智力越高越容易。"))
_reg(Op("strat.disorder", "計略", ("strategy", "disorder"), True,
        [_CITY, _OFFS, _p("target_unit", "str")],
        "擾亂：让敌方部队陷入「無陣」。"))
_reg(Op("strat.inspire", "計略", ("strategy", "inspire"), True,
        [_CITY, _OFFS, _p("target_unit", "str")],
        "激勵：提升我方部队士气/解除异常状态。"))
_reg(Op("strat.relieve", "計略", ("strategy", "relieve"), True,
        [_CITY, _OFFS, _p("target_unit", "str")],
        "救援：给我方部队补 3000 兵。"))

# ------------------------------------------------------------------ 外交

_reg(Op("diplo.gift", "外交", ("diplomacy", "gift"), True,
        [_CITY, _OFFS, _p("target_faction", "str"),
         _p("gold", "int", required=False), _p("food", "int", required=False)],
        "贈與：送錢/糧提高友好度。"))
_reg(Op("diplo.request", "外交", ("diplomacy", "request"), True,
        [_CITY, _OFFS, _p("target_faction", "str"), _p("target_structure", "str")],
        "請求：请别人出兵打某个设施。接受了就必须在期限内出兵，否则掉信望。"))
_reg(Op("diplo.return_prisoner", "外交", ("diplomacy", "return"), True,
        [_CITY, _OFFS, _p("prisoner", "str")],
        "交還：要求释放我方俘虏。"))
_reg(Op("diplo.persuade", "外交", ("diplomacy", "persuade"), True,
        [_CITY, _OFFS, _p("target_faction", "str")],
        "勸告：劝对方直接投降。信望越高越容易。"))

# ------------------------------------------------------------------ 任免

_reg(Op("appoint.strategist", "任免", ("appoint", "strategist"), False,
        [_CITY, _p("officer", "str", doc="智力 70 以上")],
        "軍師：任命军师，他会给忠告（AI 的天然情报源）。"))
_reg(Op("appoint.rank", "任免", ("appoint", "rank"), False,
        [_CITY, _p("mode", "str", doc="individual/vacancy/promote_all/clear_all")],
        "爵位：授爵。出征时武官系爵位最高者自动当大将，爵位决定能带多少兵。"))
_reg(Op("appoint.reward", "任免", ("appoint", "reward"), False,
        [_CITY, _p("officers", "list[str]", doc="每名武将 100 金")],
        "褒獎：赏金提高忠诚度。"))
_reg(Op("appoint.grant_item", "任免", ("appoint", "grant"), False,
        [_CITY, _p("officer", "str"), _p("item", "str")],
        "授與：赐宝物，大幅提高忠诚度并加能力。"))
_reg(Op("appoint.confiscate", "任免", ("appoint", "confiscate"), False,
        [_CITY, _p("officer", "str"), _p("item", "str")], "沒收：收回武将宝物。"))
_reg(Op("appoint.execute", "任免", ("appoint", "execute"), False,
        [_CITY, _p("prisoner", "str")], "處斬：处斩俘虏（会降低与该势力友好度）。"))
_reg(Op("appoint.exile", "任免", ("appoint", "exile"), False,
        [_CITY, _p("officer", "str")], "流放：放逐武将或释放俘虏。"))
_reg(Op("appoint.delegate", "任免", ("appoint", "delegate"), False,
        [_CITY, _p("governor", "str"), _p("policy", "str",
         doc="打倒勢力/重視內政/攻略設施"), _p("gold", "int", required=False),
         _p("food", "int", required=False)],
        "委任：把都市交给都督托管，自己只管战略。长局后期必用。"))

# ------------------------------------------------------------------ 回合控制（不是游戏命令，但 agent 需要）

META_OPS = {
    "turn.end": "点「進行」，把时间推进 1 旬（或 n 旬）。工具会吞掉所有插入事件。",
    "turn.skip": "什么命令都不下，直接过旬。",
    "obs.refresh": "重新取一次观测。",
    "save.snapshot": "把当前存档复制成检查点。",
    "save.rollback": "回滚到最近一个（或指定）检查点。",
    "game.save": "在游戏内存档到指定格子。",
    "game.load": "读档。",
    "game.answer": "回答一个弹窗（事件/外交使者/军师进言）。",
    "game.dismiss": "关掉当前弹窗/返回上一级。",
}


class ActionError(ValueError):
    pass


def validate(action: dict) -> dict:
    """校验一条动作。返回规范化后的动作；非法则抛 ActionError。"""
    if not isinstance(action, dict):
        raise ActionError(f"动作必须是对象，收到 {type(action).__name__}")
    op_name = action.get("op")
    if not op_name:
        raise ActionError("缺少 op 字段")

    if op_name in META_OPS:
        return dict(action)

    op = OPS.get(op_name)
    if op is None:
        raise ActionError(
            f"未知操作 {op_name!r}。可用：{', '.join(sorted(OPS))} | {', '.join(META_OPS)}"
        )

    out = {"op": op_name}
    for prm in op.params:
        if prm.name in action:
            v = action[prm.name]
        elif prm.required and prm.default is None:
            raise ActionError(f"{op_name} 缺少必填参数 {prm.name!r}（{prm.doc}）")
        else:
            v = prm.default
        out[prm.name] = v

    # 内政类（設施 下 9 条）实际由 `cmdscreen.run_facility_command` 执行，
    # 它认的是 **`city_row` + `all_officers`**（走界面，不需要 OCR 出名字）。
    # 所以这里必须"二选一"地校验，不能只认名字 ——
    # 原来 Op 的 schema 写死了 city/officers 必填，而 _dispatch 走的是另一套参数，
    # 两边不同步的结果是：**所有 city.* 动作在 validate 阶段就被拒了**，
    # 一次都没真正下发过（烟测里 11 条全挂在这个上）。
    if op.menu and op.menu[0] == "facility":
        if not (out.get("city") or action.get("city_row") is not None):
            raise ActionError(
                f"{op_name}: 需要 `city`（都市名）或 `city_row`（列表行号）之一")
        if not (out.get("officers") or action.get("all_officers")):
            raise ActionError(
                f"{op_name}: 需要 `officers`（姓名列表）或 `all_officers: true` 之一")

    if "officers" in out and isinstance(out["officers"], list):
        n = len(out["officers"])
        if n == 0:
            raise ActionError(f"{op_name}: officers 不能为空")
        if op.category == "設施" and n > MAX_OFFICERS_PER_ORDER:
            raise ActionError(
                f"{op_name}: 内政类命令每城每旬最多 {MAX_OFFICERS_PER_ORDER} 人，收到 {n} 人"
            )

    if op_name == "army.deploy":
        units = out.get("units") or []
        if not units:
            raise ActionError("army.deploy: units 不能为空")
        for i, u in enumerate(units):
            if not isinstance(u, dict):
                raise ActionError(f"army.deploy: units[{i}] 必须是对象")
            if not u.get("officers"):
                raise ActionError(f"army.deploy: units[{i}] 缺少 officers")
            if not u.get("formation"):
                raise ActionError(
                    f"army.deploy: units[{i}] 缺少 formation（陣形），"
                    "合法值见 FORMATIONS"
                )
        out.pop("_", None)

    return out


# 阵形表（手册 P.56-57）。出征决策的核心，agent 必须能查到。
FORMATIONS: dict[str, dict] = {
    "魚鱗": {"對部隊": 15, "對守兵": 10, "對城壁": 10, "守備": 10, "機動": 12,
           "特點": "容易发动步兵系兵法"},
    "鋒矢": {"對部隊": 15, "對守兵": 8, "對城壁": 10, "守備": 9, "機動": 14,
           "特點": "容易发动骑兵系兵法；容易触发单挑"},
    "長蛇": {"對部隊": 9, "對守兵": 9, "對城壁": 10, "守備": 7, "機動": 20,
           "特點": "机动最高"},
    "鶴翼": {"對部隊": 12, "對守兵": 10, "對城壁": 10, "守備": 12, "機動": 10,
           "特點": "容易发动步兵系兵法；容易俘虏敌将"},
    "雁行": {"對部隊": 11, "對守兵": 15, "對城壁": 10, "守備": 11, "機動": 10,
           "特點": "容易发动弩兵系兵法"},
    "方圓": {"對部隊": 9, "對守兵": 10, "對城壁": 10, "守備": 15, "機動": 8,
           "特點": "不易陷入异常状态，降低士兵死亡率"},
    "錐行": {"對部隊": 13, "對守兵": 10, "對城壁": 10, "守備": 12, "機動": 16,
           "特點": "容易发动骑兵/弓骑系兵法；容易引起兵法连锁"},
    "箕形": {"對部隊": 12, "對守兵": 12, "對城壁": 10, "守備": 12, "機動": 10,
           "特點": "容易发动弩兵/弓骑系兵法及狙击"},
    "井闌": {"對部隊": 10, "對守兵": 40, "對城壁": 10, "守備": 7, "機動": 9,
           "特點": "对守兵强力间接攻击；每队 200 金"},
    "衝車": {"對部隊": 10, "對守兵": 10, "對城壁": 40, "守備": 8, "機動": 9,
           "特點": "对城壁最高；每队 300 金"},
    "發石": {"對部隊": 15, "對守兵": 20, "對城壁": 20, "守備": 6, "機動": 9,
           "特點": "对部队及工作物强力间接攻击；每队 300 金"},
    "象兵": {"對部隊": 20, "對守兵": 10, "對城壁": 30, "守備": 12, "機動": 9,
           "特點": "野战攻城皆强，对步兵系兵法较弱；每队 1000 金"},
    "走舸": {"對部隊": 10, "對守兵": 10, "對城壁": 10, "守備": 10, "機動": 12,
           "特點": "无需水军兵法即可编成"},
    "艨艟": {"對部隊": 15, "對守兵": 10, "對城壁": 30, "守備": 9, "機動": 13,
           "特點": "水上攻击最高；每队 400 金；需水军兵法"},
    "樓船": {"對部隊": 13, "對守兵": 30, "對城壁": 10, "守備": 12, "機動": 10,
           "特點": "水上间接攻击；每队 600 金；需水军兵法"},
    "鬥艦": {"對部隊": 15, "對守兵": 20, "對城壁": 20, "守備": 12, "機動": 11,
           "特點": "攻守俱佳；每队 800 金；需水军兵法"},
}


def catalogue() -> dict:
    """给 agent 看的动作目录（放进 prompt / resources 里）。"""
    return {
        "ops": [
            {
                "op": o.name,
                "category": o.category,
                "delay": o.delay,
                "params": [
                    {"name": p.name, "type": p.type, "required": p.required,
                     "doc": p.doc}
                    for p in o.params
                ],
                "doc": o.doc,
            }
            for o in sorted(OPS.values(), key=lambda x: (x.category, x.name))
        ],
        "meta_ops": [{"op": k, "doc": v} for k, v in META_OPS.items()],
        "formations": FORMATIONS,
    }
