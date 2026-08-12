# Hermes Agent - 网关与路由设计

> 仓库:`NousResearch/hermes-agent`。Nous Research 出品的自托管、自学习 AI agent。Python 为主,带 Node.js TUI。本质是 **agent**(类似 Claude Code),不是 LLM API 网关;但它内部有三种被称作「gateway」的机制,其中 LLM provider 路由层与 llmport 关注点高度重合。

## 1. 定位与澄清

Hermes 是一个 agent:TUI 终端界面 + 消息平台接入 + 学习闭环(自建 skill / 记忆 / 跨会话召回)+ cron 调度 + subagent 委派 + 七种运行后端(本地 / Docker / SSH / Singularity / Modal / Daytona / Vercel Sandbox,后两者 serverless 按需唤醒)。

它有**三种「gateway」**,必须先区分,否则会和 llmport / CCR 那种 LLM API 网关混淆:

| gateway | 位置 | 作用 | 与 llmport 相关度 |
| --- | --- | --- | --- |
| **消息网关** | `gateway/` | 一个进程把 agent 接到 Telegram / Discord / Slack / WhatsApp / Signal / Email | 低(是 IM 多路复用,不是模型 API 网关) |
| **LLM provider 路由层** | `providers/base.py` + `agent/*_adapter.py` + `agent/credential_pool.py` 等 | 多 provider 模型路由、凭据池、限流、重试、错误分类 | **高**(与 llmport / CCR 同类关注点) |
| **Tool Gateway** | Nous Portal(`hermes setup --portal`) | 按工具后端路由 web search / image gen / TTS / 浏览器 | 中(工具路由,非模型路由) |

## 2. 消息网关(`gateway/`)

`hermes gateway start` 启动一个长驻进程,把同一个 agent 同时暴露到多个 IM 平台。这是一个**消息取向的网关**,核心是可靠投递与会话/流式管理。文件清单很能说明设计:

```
gateway/
├── platform_registry.py        # 平台注册表
├── platforms/                  # 各平台 adapter(Telegram/Discord/...)
├── delivery.py / delivery_ledger.py   # 投递 + 投递账本(可靠投递)
├── session.py / session_state.py / session_context.py / session_stall.py
├── stream_consumer.py / stream_dispatch.py / stream_events.py  # 流式回复分发
├── streaming_tts_consumer.py   # TTS 流式消费
├── relay/                      # 跨平台 / 委派中继
├── profile_routing.py          # 按配置路由到不同 agent profile
├── pairing.py                  # 平台账号配对
├── channel_directory.py / dead_targets.py   # 通道目录 / 失效目标
├── scale_to_zero.py            # 空闲缩容到零
├── restart.py / restart_loop_guard.py / shutdown_watchdog.py / shutdown_flush.py / shutdown_forensics.py
├── lifecycle_ledger.py / readiness.py / wake.py / drain_control.py
├── memory_monitor.py / agent_cache_pressure.py / cgroup_cleanup.py
├── systemd_notify.py           # systemd 集成
├── authz_mixin.py / slash_access.py / slash_commands.py
├── hooks.py / builtin_hooks/   # 钩子机制
├── kanban_watchers.py / mirror.py
└── run.py / status.py / config.py
```

设计要点:

- **平台注册表 + 平台 adapter**:`platform_registry` 统一注册,`platforms/` 每个平台一个 adapter,新增平台只实现 adapter。
- **投递账本(`delivery_ledger`)**:消息投递落账,失败可追踪 / 重试,不是 fire-and-forget。
- **会话状态机**:`session` / `session_state` / `session_stall` / `turn_context` / `turn_lease` 管理一次对话回合的生命周期与租约。
- **流式分发**:`stream_dispatch` 把 agent 的流式输出分发到当前平台;`stream_events` 是事件抽象。
- **生命周期加固**:`restart_loop_guard`(防重启风暴)、`shutdown_watchdog`(关停看门狗)、`shutdown_forensics`(关停取证)、`lifecycle_ledger`(生命周期账本)、`readiness` / `wake` / `drain_control`(就绪 / 唤醒 / 排空)。这套比 llmport 当前的 daemon pid 生命周期要重得多。
- **scale-to-zero**:`scale_to_zero` 空闲缩容,配合 serverless 后端实现「闲时几乎不花钱」。
- **profile routing**:按 profile 把不同入口路由到不同 agent 配置。
- **authz + slash**:`authz_mixin` 鉴权,`slash_commands` / `slash_access` 处理 IM 里的斜杠命令。

> 这一层与 llmport 关系不大(llmport 不接 IM),但其**长驻进程的生命周期加固模式**(重启看门狗、关停账本、就绪探针)对 llmport 的 daemon 有参考价值。

## 3. LLM provider 路由层(与 llmport 最相关)

Hermes 用任意 provider 模型,运行时 `hermes model` / `/model provider:model` 切换,「no code changes, no lock-in」。支持 Nous Portal / OpenRouter / OpenAI / 自建端点等。

### 3.1 契约与适配器

- `providers/base.py`:provider 契约基类(目录里只有 `base.py` + `__init__.py` + `README.md`,**契约与实现分离**)。
- 具体适配器在 `agent/` 下,按上游协议分:

| 适配器 | 协议 |
| --- | --- |
| `agent/anthropic_adapter.py` | Anthropic Messages |
| `agent/bedrock_adapter.py` | AWS Bedrock |
| `agent/gemini_native_adapter.py` + `agent/gemini_schema.py` | Gemini 原生 |
| `agent/codex_responses_adapter.py` + `agent/codex_runtime.py` | OpenAI Responses(Codex) |
| `agent/lmstudio_reasoning.py` | LM Studio |
| `agent/moonshot_schema.py` | Moonshot |
| `agent/azure_identity_adapter.py` | Azure 身份 |
| `agent/relay_llm.py` + `agent/relay_runtime.py` + `agent/relay_tools.py` | Relay(委派给 subagent / 远端) |
| `agent/plugin_llm.py` | 插件式 LLM |
| `agent/auxiliary_client.py` + `agent/aux_accounting.py` | 辅助模型客户端 |

### 3.2 凭据 / 限流 / 重试 / 错误分类

与 CCR 几乎同构的 resilience 关注点,但内嵌在 agent 进程里:

| 文件 | 职责 |
| --- | --- |
| `agent/credential_pool.py` | 凭据池(多 key 轮转) |
| `agent/credential_sources.py` + `agent/credential_persistence.py` | 凭据来源 + 持久化 |
| `agent/secret_sources/` | 密钥源抽象:Bitwarden / 1Password / command / registry / cache |
| `agent/rate_limit_tracker.py` | 限流跟踪 |
| `agent/nous_rate_guard.py` | Nous 平台速率守卫 |
| `agent/retry_utils.py` | 重试策略 |
| `agent/error_classifier.py` | 错误分类(决定是否重试 / 切 key / 切 provider) |
| `agent/rate_limit_tracker.py` + `agent/credits_tracker.py` | 用量 / 额度跟踪 |
| `agent/account_usage.py` + `agent/billing_*.py` | 账号用量 / 计费 |

**密钥源抽象**值得注意:`secret_sources/` 把「从哪拿 key」抽象成统一接口(Bitwarden / 1Password / 本地命令 / 内存缓存),provider 层不直接读配置文件里的明文 key。llmport 当前直接从 `providers.yaml` 读 inline key,这是可演化的方向。

### 3.3 Relay / subagent 委派

`agent/relay_llm.py` + `relay_runtime.py` + `relay_tools.py` + `gateway/relay/`:Hermes 可以**派生隔离的 subagent 做并行 workstream**,还能写 Python 脚本通过 RPC 调工具,把多步管线压成「零上下文成本」的一轮。`agent/moa_loop.py` + `moa_trace.py` 是 mixture-of-agents 循环。这是 agent 层的路由(把子任务路由到子 agent),不是模型 API 路由。

## 4. Tool Gateway(Nous Portal)

`hermes setup --portal` 走 OAuth 登 Nous,把 provider 设为 Nous,并打开 Tool Gateway:

- 一个订阅覆盖:web search(Firecrawl)、image generation(FAL)、TTS(OpenAI)、cloud browser(Browser Use)。
- **按后端路由**:每种工具调用路由到对应后端,**不是全有全无**;每个工具仍可单独带自己的 key。
- `hermes portal info` 查看接线状态。

这是「工具调用路由」--模型要调工具时,按工具类型路由到不同后端。与 CCR 的 Fusion(给模型 fuse 上能力)是两种思路:CCR 把能力 fuse 到模型上(模型以为自己做),Hermes 是在工具调用层路由到专门后端。

## 5. 其他与「路由 / 网关」相关的部分

- **`acp_adapter/`**:Agent Client Protocol adapter(`server.py` / `session.py` / `tools.py` / `auth.py` / `permissions.py` / `edit_approval.py` / `provenance.py`)。Hermes 可作为 ACP server 对接 IDE(Cursor / Zed 等),这是「agent 与 IDE 之间的网关」。
- **`tui_gateway/`**:TUI 网关(终端界面入口,与消息网关并列)。
- **`native/`**:七种终端后端(local / Docker / SSH / Singularity / Modal / Daytona / Vercel Sandbox),Daytona / Modal 支持 serverless 持久化(闲时休眠、按需唤醒)。
- **`agent/image_routing.py`** + `image_gen_provider.py` + `image_gen_registry.py`:图像生成路由(按 provider 路由图像生成请求)。
- **`agent/context_engine.py` / `context_compressor.py` / `conversation_compression.py` / `native_compaction.py`**:上下文管理(压缩 / 归档),与路由无直接关系但是 agent 的核心子系统。
- **`agent/monitoring/`**:`gateway_health.py` / `gateway_health_export.py` / `otlp_exporter.py` / `redaction.py` / `policy.py` -- 网关健康与 OTLP 导出(可观测性)。

## 6. Windows 原生支持(对 llmport Windows 抽象的参考)

Hermes 原生支持 Windows(不需 WSL),其方案对 llmport 即将做的平台抽象有直接参考:

- PowerShell 一行装(`install.ps1`),装到 `%LOCALAPPDATA%\hermes`。
- 装箱 `uv`(Rust Python 包管理器,管 Python 环境)+ Python 3.11 + Node.js + ripgrep + ffmpeg。
- **自带便携 Git Bash(MinGit,解压到 `%LOCALAPPDATA%\hermes\git`,不需管理员、与系统 Git 隔离)**:Hermes 用这个 bundled Git Bash 跑 shell 命令。若系统已装 Git 则优先用系统的。
- Windows 原生跑 CLI、gateway、TUI、工具;WSL2 是可选 fallback。
- `docker-compose.windows.yml`:Windows 下的 Docker 编排。
- 处理了 Defender / 杀软误报 `uv.exe` 的问题(给出 attestation 校验流程 + 白名单建议)。

> 关键启示:Hermes 在 Windows 上**不依赖 `ps` / POSIX 信号语义**,而是用自带 bundled 工具 + 进程管理规避平台差异。这印证了 llmport 评估里指出的 `os.kill` / `ps` / `chmod` / `start_new_session` 四个 Windows 差距是真实痛点,且业界做法是「绕开」而非「模拟 POSIX」。

## 7. 设计要点小结

1. **三种 gateway 各司其职**:消息网关(IM 多路复用)、provider 路由层(模型 API)、Tool Gateway(工具后端)。命名都叫 gateway,但层次不同。
2. **provider 路由层 = 契约 + 适配器 + resilience 三件套**:与 CCR 同构(凭据池 / 限流 / 重试 / 错误分类),只是内嵌在 agent 里而非独立进程。
3. **密钥源抽象**(`secret_sources/`)把 key 来源与 provider 解耦,可接 Bitwarden / 1Password / 命令。
4. **消息网关的生命周期加固**值得借鉴:投递账本、重启看门狗、关停取证、就绪探针、scale-to-zero。
5. **Windows 原生支持**用 bundled 工具 + 绕开 POSIX 的思路,不模拟信号语义。
