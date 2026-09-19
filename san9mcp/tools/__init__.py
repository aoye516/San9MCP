# -*- coding: utf-8 -*-
"""san9mcp 的工具模块。导入即向 `san9mcp.registry` 注册。

分组（对应 README）：
    env        A 组  会话与环境
    observe    B 组  眼睛（读状态，只读）
    knowledge  B 组  知识（不碰游戏）
    act        F 组  内政命令
    turn       L 组  时间与弹窗
"""
from san9mcp.tools import act, env, knowledge, observe, target_force, target_officer, target_unit, turn  # noqa: F401

__all__ = ["act", "env", "knowledge", "observe", "target_force", "target_officer", "target_unit", "turn"]
