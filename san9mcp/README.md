# san9mcp v0.1

这是 San9MCP v0.1 的 MCP 服务目录。服务通过标准输入输出提供 JSON-RPC 2.0，不依赖 MCP SDK。

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

默认 `tools/list` 暴露 20 个工具，包含环境体检、战略面观测、已验收的设施命令、过旬、日志、弹窗、恢复，以及 `san9_ui_read` 的两个真机只读屏幕：

- `command_menu`
- `officer_picker`

`san9_ui_read` 不点击、不关闭当前面板；对未适配屏幕明确返回失败。

## 暂不作为玩用能力

以下新工具代码保留在发布包中，但当前属于离线规划器或状态机，不会进入默认 `tools/list`：

`san9_ui_choose`、`san9_ui_number`、`san9_intel_city`、`san9_target_officer`、`san9_target_force`、`san9_target_unit`。

不要在生产 MCP 配置中设置 `SAN9_MCP_DEV=1`。

详细运行前提、免责声明和 v0.1 范围见发布包根目录 `README.md`。
