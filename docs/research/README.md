# 网关 / 路由方案调研

调研两个开源项目如何设计「网关 / 路由」,作为 llmport 演进的参考。

## 调研对象

| 项目 | 仓库 | 性质 |
| --- | --- | --- |
| Claude Code Router (CCR) | `musistudio/claude-code-router` | 独立的本地 LLM API 网关 + 控制平面(多 agent 接入) |
| Hermes Agent | `NousResearch/hermes-agent` | 自托管 AI agent,内嵌 provider 路由层 + 独立的消息网关 |

## 文档

- [gateway-impl-and-cross-platform.md](./gateway-impl-and-cross-platform.md) - **gateway 实现方式 + 多系统兼容**(进程模型 / HTTP server / Windows-Unix 抽象,含 psutil 决策结论)
- [claude-code-router.md](./claude-code-router.md) - CCR 的网关与路由设计
- [hermes-agent.md](./hermes-agent.md) - Hermes 的三种「网关」与路由设计
- [comparison.md](./comparison.md) - 横向对比 + 对 llmport 的可借鉴点

## TL;DR

- **gateway 实现**:CCR 是 Node.js supervisor-worker(管理进程 spawn 网关子进程,config 走 IPC 不落盘,Node `http` + Handler/Pipeline 类);Hermes 是 FastAPI + uvicorn + httpx(**和 llmport 同栈**),消息网关是 asyncio 长驻进程。llmport 当前同 Hermes 栈、单进程 daemon,够用。
- **多系统兼容**:CCR 用 `platform/` 模块 + `process.platform` 守卫 + PowerShell;Hermes 把 **`psutil` 作为核心依赖**,注释明确写"取代 `os.kill(pid,0)`(Windows silent killer)和 `os.killpg`(Windows 没有)"。**这直接回答了 llmport Windows 抽象的 psutil vs 标准库问题 -- 推荐引入 psutil。** 详见 [gateway-impl-and-cross-platform.md](./gateway-impl-and-cross-platform.md)。
- **对 llmport 最直接的借鉴**:重试 + 失败分类、route trace 可观测性、psutil 统一进程层。详见 [comparison.md](./comparison.md)。

## 方法

通过 GitHub 公开 API / raw 内容抓取 README、源码树、关键源码与官方文档(`docs/src/content/docs/en/configuration/`)。未克隆仓库,结论基于公开源码与文档,标注了文件路径供复核。
