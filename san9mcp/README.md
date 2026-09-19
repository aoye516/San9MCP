# san9mcp v0.2.0

San9MCP 的 MCP 服务目录。服务通过标准输入输出提供 JSON-RPC 2.0，不依赖 MCP SDK。

## 启动

请从发布包根目录启动：

```powershell
python -m san9mcp.server
```

MCP 配置中的 `cwd` 必须指向发布包根目录，即包含 `san9/`、`san9mcp/`、`config/` 的目录。

协议自检：

```powershell
python scripts/mcp_probe.py list
```

## 默认能力

默认 `tools/list` 暴露 **24 个工具**：

| 组 | 工具 |
|---|---|
| 环境与启动 | `san9_status` `san9_launch` `san9_focus` |
| 观测（只读）| `san9_look` `san9_topbar` `san9_menu` `san9_panel` `san9_officers` `san9_shot` |
| 情报 | `san9_intel_officer` `san9_city_officers` `san9_journal` |
| 命令 | `san9_facility_command` `san9_target_own_city` `san9_transport` `san9_move` `san9_search` `san9_dengyong` `san9_deploy` |
| 时间与清理 | `san9_end_turn` `san9_dialog` `san9_recover` |
| 知识 | `san9_docs` |
| 通用只读 UI | `san9_ui_read` |

`san9_ui_read` 真机已支持 `command_menu` 与 `officer_picker` 两类界面；它只读、不点击、不关闭，
其它屏幕会明确返回不支持。

### 命令类的调用形态

命令类工具统一是**"先只读、再提交"**：

- 不带提交参数 ⇒ 只做只读侦察（开界面 → 读读数 → 干净退回），把可选项返回给你；
- 带提交参数 ⇒ 走到提交点；`san9_deploy` / `san9_dengyong` 这类还有一个 `dry_run`（默认 `true`），
  默认**只配好、不提交**。

例：`san9_deploy(row=1)` 只把 15 行目标表读回来；
`san9_deploy(row=1, target_row=14, expect_target="洛陽", dry_run=False)` 才真的出兵。

## 暂不作为玩用能力

以下工具代码随包保留，方便后续开发，但当前属于离线规划器或状态机，
**不会**进入默认 `tools/list`：

`san9_ui_choose`、`san9_ui_number`、`san9_intel_city`、
`san9_target_officer`、`san9_target_force`、`san9_target_unit`。

不要在生产 MCP 配置中设置 `SAN9_MCP_DEV=1`。

## 安全边界

- 工具失败必须返回 `ok:false` 和原因，不把失败伪装成成功。
- 读数读不出时返回不确定，**不猜坐标、不猜名称、不盲点**。
- 需要真实游戏画面才能验证的工具，不因离线测试通过就宣称真机验收通过。
- 失败必须把界面收回干净战略面；清不干净时明确要求人工介入。

## 布局约定

- `san9mcp/` 只做**翻译与校验**（MCP 协议 ↔ 语义动作）。
  ⛔ 不许在 `san9mcp/` 里写截图 / OCR / 点击逻辑 —— 否则会变成两份实现，早晚不一致。
- `san9/` 才是截图、OCR、界面识别、输入控制。
- 每个工具一个模块，落在 `san9mcp/tools/<组>.py`，用 `@registry.tool(...)` 注册，schema 写全
  （agent 只看得到 schema）。

详细运行前提、免责声明和 v0.2.0 范围见发布包根目录 `README.md`。
