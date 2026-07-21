# llmgate

Terminal LLM API Gateway — 统一入口，多供应商路由/切换/fallback。

## 定位

在你的机器上常驻运行的 API 网关。你的所有工具（IDE 插件、CLI、脚本）只需指向一个 URL，网关负责：

- 多供应商 API key 安全管理（本地加密，零依赖）
- 以模型为单位一键切换路由
- 按优先级故障 fallback
- 多协议透明转发（OpenAI / Anthropic Messages）

## 安装

```bash
uv tool install llmgate
```

## 使用

```bash
llmgate          # 打开 TUI，网关自动启动
llmgate stop     # 停止网关
llmgate status   # 查看状态
```

## 技术栈

Python 3.12+ · Textual · httpx · uv
