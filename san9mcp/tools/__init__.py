# -*- coding: utf-8 -*-
"""san9mcp 的工具模块。导入即向 `san9mcp.registry` 注册。

分组（对应 README）：
    env        A 组  会话与环境
    observe    B 组  眼睛（读状态，只读）
    knowledge  B 组  知识（不碰游戏）
    act        F 组  内政命令
    move       F 组  人材命令（移動）—— 单独一个模块，避免和 act.py 的改动混在一起
    search     F 组  人材命令（探索）—— 同上
    deploy     F 组  軍事命令（出征）—— 同上
    dengyong   F 组  人材命令（登庸）—— 同上
    turn       L 组  时间与弹窗
"""
from san9mcp.tools import act, dengyong, deploy, env, knowledge, move, observe, search, target_force, target_officer, target_unit, turn  # noqa: F401

__all__ = ["act", "dengyong", "deploy", "env", "knowledge", "move", "observe", "search",
           "target_force", "target_officer", "target_unit", "turn"]
