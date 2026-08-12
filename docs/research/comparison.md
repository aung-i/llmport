# 横向对比与对 llmport 的借鉴

> 基于 [claude-code-router.md](./claude-code-router.md) 与 [hermes-agent.md](./hermes-agent.md) 的调研,对照 llmport 当前实现,给出可落地的演进方向。

## 1. 分类定位

| 维度 | llmport(当前) | CCR | Hermes |
| --- | --- | --- | --- |
| 类别 | 本地 LLM API 网关(最小) | 本地 LLM API 网关 + 控制平面 | 自托管 AI agent |
| 形态 | 独立进程(Starlette/uvicorn) | 独立进程 + Electron 桌面 app | agent 进程 + 消息网关进程 |
| 谁连它 | 客户端把 API base URL 指过来 | 同左(多 agent) | agent 内部调 provider;IM 平台连消息网关 |
| 协议转换 | OpenAI ↔ Anthropic(双向,文本/工具/多模态/流式) | OpenAI Chat/Responses ↔ Anthropic ↔ Gemini + 各类桥 | 各 provider adapter(Anthropic/Bedrock/Gemini/Codex/...) |
| 路由 | 纯转发(按 model 选 provider) | 完整规则引擎 + 脚本 + fallback 链 | adapter + relay + profile routing |
| 凭据 | 每 provider 单 key(inline) | 凭据池(priority/weight/per-key 限流) | 凭据池 + 密钥源抽象(BW/1Password) |
| 重试/fallback | 无 | failure-classifier + 指数退避 + retry-after | retry_utils + error_classifier |
| 可观测性 | 基本无 | route trace + 请求日志 + 用量 + account | OTLP 导出 + gateway_health |
| 配置 | YAML(config.yaml + providers.yaml,0600) | SQLite + 桌面 UI | YAML + cli + 运行时切换 |
| Windows | 部分差距(os.kill/ps/chmod) | 原生(platform/windows-*) | 原生(bundled Git Bash + uv) |

llmport 与 **CCR 同类**,是 CCR 的「最小内核」:协议转换 + 多 provider + 强制鉴权 + 凭据文件隔离。llmport 与 Hermes 不同类(Hermes 是 agent),但 Hermes 的 **provider 路由层**与 llmport 关注点重合。

## 2. 共同的 resilience 模式(CCR 与 Hermes 都有,llmport 缺)

两个项目在「上游 provider 调用」上做了几乎同构的事:

| 模式 | CCR | Hermes | llmport |
| --- | --- | --- | --- |
| 凭据池(多 key 轮转) | `providers/credential-pool.ts`(priority/weight/limits) | `agent/credential_pool.py` | 无(单 key) |
| per-key 限流 | `gateway/limits/window-limiter.ts`(rpm/tpm/...) | `agent/rate_limit_tracker.py` | 无 |
| 失败分类 | `routing/failure-classifier.ts`(status+mode -> shouldFallback) | `agent/error_classifier.py` | 无 |
| 重试退避 | `upstream/retry-policy.ts`(指数 + retry-after) | `agent/retry_utils.py` | 无 |
| fallback 链 | `RouteExecutionPlan.attempts[]` | relay / 多 provider | 无 |
| 错误归因诊断码 | `RouteDiagnosticCode` | error_classifier 分类 | 无 |

**结论**:llmport 当前的「纯转发」在上游失败时直接把错误透传给客户端,没有重试、没有切 key、没有 fallback。这是最值得补的一层。

## 3. 路由模型对比

| 项目 | 路由决策来源 | 表达力 | 实现 |
| --- | --- | --- | --- |
| llmport | 按 `model` 字段选 provider | 单一(模型名直查) | server.py 内联 |
| CCR | 6 种来源(builtin/custom/default/profile/rule/subagent)+ 可脚本 | 高(条件改写 + JS 脚本 + 标签注入) | 独立 `routing/` 模块 + 数据模型 |
| Hermes | adapter 选择 + `hermes model` 运行时切 + profile routing + relay 委派 | 中(运行时切换,非每请求规则) | provider 契约 + agent 适配器 |

CCR 的路由是**每请求决策**(规则引擎跑一遍产出 execution plan);Hermes 是**配置时 / 运行时切换**(用户 `/model` 选,或 relay 派生)。llmport 当前是**直接路由**(model -> provider),最简单。

## 4. llmport 可借鉴点(按优先级)

### 4.1 短期可落地(与现有架构契合,改动可控)

1. **failure-classifier + 重试退避**(最高性价比)
   - 现状:`server.py` 的 `_forward_translated` 等直接 `httpx` 请求,失败即返回。
   - 借鉴:CCR 的 `shouldFallbackAfterStatus(status, mode)` -- 429 / 5xx / 网络错误才重试,4xx(除 429)不重试;退避用指数(base 1s,cap 30s)+ 尊重上游 `retry-after`。
   - 落点:新建 `src/llmport/gateway/retry.py`(纯函数,与 translator.py 风格一致),`server.py` 包一层重试循环。不引入新依赖。
   - 注意:流式响应的重试只能在首字节前重试(一旦开始流式输出就不能重试),CCR 的 `executor` 也是这个语义。

2. **route trace / 请求日志(最小版)**
   - 现状:llmport 基本无可观测性。
   - 借鉴:CCR 的 `RequestRouteTraceRecorder` -- 每请求记 model 解析结果、上游 provider、状态码、耗时、是否重试。
   - 落点:`server.py` 加一个轻量请求日志(可配置开关,写 stderr 或文件),不搞 SQLite。

3. **translator.py 已是纯函数,方向正确**
   - CCR 的 `routing/protocol-adapter.ts` + `gateway/features/*` 也是把协议适配拆成独立模块。llmport 的 `translator.py` 已经是纯函数,架构一致。继续维持「translator 纯函数 + server 编排」的边界,不要把路由逻辑塞进 translator。

### 4.2 中期(范围扩大,需设计)

4. **凭据池 + per-key 限流**
   - 现状:`providers.yaml` 每 provider 一个 inline `api_keys`。
   - 借鉴:CCR 的 credential pool(priority/weight/limits)+ Hermes 的密钥源抽象。
   - 落点:`providers.yaml` 扩展为支持多 key + per-key 限流(rpm/tpm);`config/store.py` 加限流窗口跟踪。这是 `[[gateway-core-architecture]]` 里「多 provider」的自然延伸。
   - 注意:保持凭据文件 0600 不变,只扩结构。

5. **路由规则引擎(条件 -> 改写,有序首匹配)**
   - 现状:llmport 只按 model 选 provider。
   - 借鉴:CCR 的自定义规则(Condition -> Rewrite,有序首匹配),**不**上 Node.js 脚本规则(那是 CCR 桌面特性,对 llmport 过重)。
   - 落点:若 llmport 要支持「按 header/body 路由到不同 provider」(如不同客户端用不同模型),可加一个轻量规则层。但这是**范围扩张**,当前 issue 列表未要求,按 [[crisp-decisions]] 原则不在此时做。

### 4.3 不必照搬(超出 llmport 范围)

- **CCR 的桌面 app / SQLite 配置 / Proxy MITM / Agent Profiles / Fusion / AgentClaw** -- llmport 是 CLI 网关,不需要。
- **Hermes 的消息网关 / 学习闭环 / subagent / 七种后端 / ACP adapter** -- llmport 是网关不是 agent,不需要。
- **CCR 的 Node.js 脚本路由规则** -- 对 llmport 过重,且引入 JS 运行时依赖。
- **Hermes 的 Tool Gateway / Nous Portal** -- 那是商业订阅集成,不适用。

## 5. 对 Windows 抽象的启示(下一个待办任务)

两个项目都原生支持 Windows,做法一致:**绕开 POSIX,不模拟信号语义**。

| 痛点(llmport) | CCR 做法 | Hermes 做法 | llmport 可选方案 |
| --- | --- | --- | --- |
| `os.kill(pid, SIGTERM/SIGKILL)` 硬终止 | 不依赖(独立进程管理) | 不依赖(bundled 工具) | 抽象 `terminate(pid)`:POSIX 用 SIGTERM->SIGKILL,Windows 用 `taskkill` 或 `psutil` |
| `ps -p` 读 cmdline | `platform/windows-system.ts` | bundled 工具规避 | `psutil.Process(pid).cmdline()` 统一(已定) |
| `chmod 0600/0700` 被忽略 | 不依赖文件权限做安全 | 同左 | 现有 `_chmod` 已 try/except 吞错;Windows 上靠 ACL 或目录权限 |
| `start_new_session=True` 被忽略 | 不依赖 | 不依赖 | 抽象 `spawn`:POSIX 用 `start_new_session`,Windows 用 `CREATE_NEW_PROCESS_GROUP` |

**决策(已定,推荐 psutil)**:Windows 上读进程命令行(用于 PID 身份校验,防 stop 误杀回收的 pid)用 `psutil`。

- **先例**:Hermes(`pyproject.toml`)把 `psutil==7.2.2` 作为核心依赖,注释明确写"取代 `os.kill(pid,0)`(Windows silent killer)和 `os.killpg`(Windows 没有)"-- 与 llmport 的问题逐字命中。
- **方案 A:引入 `psutil`**(采用)-- 跨平台、`psutil.Process(pid).cmdline()` / `.terminate()` / `.is_running()` 统一接口,代码最简,代价是加一个依赖。
- ~~方案 B:纯标准库~~ -- POSIX 用 `ps`,Windows 用 PowerShell `Get-CimInstance Win32_Process`,无新依赖但两套实现 + 子进程开销,且 Hermes 已证明 psutil 是这类项目的标准选择。

具体落点(新建 `src/llmport/platform/process.py` + 改 `daemon.py` + 加依赖 + 迁移测试 patch)见 [gateway-impl-and-cross-platform.md](./gateway-impl-and-cross-platform.md) 第 B.3 节。

## 6. 结论

- llmport 的架构方向是对的:**translator 纯函数 + server 编排 + 凭据文件隔离**,与 CCR 的 `routing/protocol-adapter` + `gateway/request/pipeline` + 三层凭据是同构的,只是少了 resilience 层和可观测层。
- **最值得补的是「重试 + 失败分类」**:投入小、收益直接、与现有纯函数风格契合,不引入依赖。
- **Windows 抽象**是下一个明确任务,两个项目都印证了「绕开 POSIX」的思路;**psutil 已有 Hermes 先例,决策已定**(详见 [gateway-impl-and-cross-platform.md](./gateway-impl-and-cross-platform.md))。
- 路由规则引擎、凭据池属于**范围扩张**,不在当前 issue 范围,应等明确需求再做(遵循 [[crisp-decisions]]:不把未来开发当阻塞)。
