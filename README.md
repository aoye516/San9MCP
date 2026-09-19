# San9MCP v0.1

《三国志9 with 威力加强版》的 MCP 服务最小发布包。

这个版本的目标是：让外部 agent 能直接接入、看到一组已经存在并可调用的 MCP 工具；未完成的离线规划器不会出现在默认 `tools/list` 中。

## 快速启动

在本目录打开终端：

```powershell
python -m pip install -r requirements.txt
python -m san9mcp.server
```

MCP 客户端配置示例：

```json
{
  "mcpServers": {
    "san9": {
      "command": "python",
      "args": ["-m", "san9mcp.server"],
      "cwd": "C:/path/to/san9mcp"
    }
  }
}
```

也可以先不用 agent 做协议自检：

```powershell
python scripts/mcp_probe.py list
python scripts/mcp_probe.py call san9_status
```

## v0.1 默认暴露的工具

默认启动时注册表共有 28 项，`tools/list` 暴露 20 项：

- 环境与启动：`san9_status`、`san9_launch`、`san9_focus`
- 观测：`san9_look`、`san9_topbar`、`san9_menu`、`san9_panel`、`san9_officers`、`san9_shot`
- 情报：`san9_intel_officer`、`san9_city_officers`
- 命令与时间：`san9_facility_command`、`san9_transport`、`san9_target_own_city`、`san9_end_turn`、`san9_dialog`、`san9_journal`、`san9_recover`
- 知识：`san9_docs`
- 通用只读 UI：`san9_ui_read`

`san9_ui_read` 当前只支持用户已经打开的两类界面：`command_menu` 和 `officer_picker`；它只读、不点击、不关闭。其他屏幕会明确返回不支持。

## 明确未纳入默认玩用能力

以下工具代码随包保留，方便后续开发，但当前是离线规划器或状态机，默认不会出现在 `tools/list`：

- `san9_ui_choose`
- `san9_ui_number`
- `san9_intel_city`
- `san9_target_officer`
- `san9_target_force`
- `san9_target_unit`
- 以及其他 `stub` / `dev` 工具

不要把 `SAN9_MCP_DEV=1` 当成生产启动参数；它只用于开发和标定。

## 运行前提

- Windows；游戏窗口化运行，客户区 1280×960。
- 必须使用正版《三国志9 with 威力加强版》及其启动器。
- 游戏窗口必须在前台；截图和输入均作用于当前屏幕。
- 默认运行数据目录为 `~/san9ai`，可用环境变量 `SAN9_DATA` 覆盖；建议使用纯英文路径。
- 本仓库不包含游戏本体、存档、截图、运行日志或说明书原文。

## 目录

- `san9mcp/`：MCP 协议层、注册表、运行时和工具入口。
- `san9/`：截图、OCR、界面识别和输入控制核心库。
- `config/`：运行所需的命令、路径、锚点和模板配置。
- `scenarios/`：benchmark 场景定义。
- `scripts/mcp_probe.py`：MCP 握手、工具列表和工具调用自检。

## 安全边界

- 工具失败必须返回 `ok:false` 和原因，不把失败伪装成成功。
- 读数读不出时返回不确定，不猜坐标、不猜名称、不盲点。
- 需要真实游戏画面才能验证的工具，不因离线测试通过就宣称真机验收通过。

## 免责声明

本项目是互操作性研究与 AI 评测工具，不含游戏本体或游戏数据文件。游戏及其素材版权归光荣特库摩所有。请自备正版游戏。

代码部分采用 MIT License，见 `LICENSE`。
