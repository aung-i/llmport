# Claude Code Router (CCR) - 网关与路由设计

> 仓库:`musistudio/claude-code-router`。一个本地模型网关 + 控制平面,把 Claude Code / Codex / Grok CLI / Kimi CLI / OpenCode / ZCode 等 coding agent 统一接到一个稳定的本地端点,背后管理 provider、模型、账号、路由规则与工具。TypeScript monorepo(`packages/core` / `packages/cli` / `packages/ui`),Electron 桌面 app。

## 1. 定位

CCR 是**独立的 LLM API 网关**(agent 无关):多个 agent 客户端把 API base URL 指向 CCR,CCR 负责路由、转换、重试、可观测。这与 llmport 同类,只是范围大很多。

支持的协议:OpenAI Chat / Responses、Anthropic Messages、Gemini Generate Content / Interactions、OpenRouter、DeepSeek、SiliconFlow、Moonshot、Kimi Code、Mistral、Z.AI、Bailian 及自定义兼容 provider。

## 2. 三层凭据模型(关键,与 llmport 直接对应)

CCR 明确分离三类凭据,互不混用(`docs/.../server.md`):

| 层 | 用途 | 对应 llmport |
| --- | --- | --- |
| Management token | 管理面(UI / `3458` 端口)鉴权 | llmport 无管理面 |
| **Client API key** | 客户端(agent)访问网关用的 key,在 **API Keys** 页创建 | llmport 的 `config.yaml` 里的 `api_key` |
| **Upstream credential pool** | 上游 provider 的 key 池,在 provider 配置里 | llmport 的 `providers.yaml` 里每个 provider 的 `api_keys` |

> 「Gateway clients use keys created under **API Keys** and should never receive upstream provider credentials.」-- 与 llmport「认证密钥是 llmport 自己的 API key,绝不是 gateway key」的约束一致。

管理地址(`3458`)与模型网关地址(`3456`)分离;配置存 SQLite(`~/.claude-code-router/config.sqlite`),legacy `config.json` 仅作一次性迁移源。

## 3. 路由引擎(`packages/core/src/routing/`)

路由是 CCR 的核心,独立成一个模块,文件划分清晰:

```
routing/
├── contracts.ts          # 数据模型:RouteSource / RouteDecision / RouteExecutionPlan
├── policy-engine.ts      # 规则编译与匹配
├── execution-plan.ts     # 决策 -> 有序 attempts 链
├── model-resolution.ts   # 解析 Provider/model 引用
├── model-registry.ts
├── protocol-adapter.ts   # 请求体协议适配
├── protocol-endpoints.ts # 路径 -> 协议判定
├── rewrite.ts            # 改写操作(CompiledRouteRewrite)
├── failure-classifier.ts # 状态码 -> 是否 fallback
├── route-script-context.ts / route-script-result.ts
├── route-script-runtime.ts / route-script-worker.ts / route-script-worker-protocol.ts
└── config-compiler.ts
```

### 3.1 数据模型(`contracts.ts`)

路由决策的来源有六种(`RouteSource`):

```
"builtin" | "custom" | "default" | "profile" | "rule" | "subagent"
```

- `RouteDecision`:`{ model, rewrites, fallback, reason, diagnostics, source }`
- `RouteExecutionPlan`:`{ attempts: RouteAttemptPlan[], fallback, primaryModel }` -- **有序的尝试链**,primary + fallback 模型按序排列,逐个尝试。
- `RouteModelRef`:要么是 provider 模型(`provider` + `model`),要么是 gateway 模型(Fusion 合成模型)。
- `RouteDiagnosticCode`:一组诊断码(`rule-rewrite-invalid` / `script-timeout` / `script-runtime-error` / `custom-model-not-configured` ...),路由失败时有明确归因。

### 3.2 三类路由规则(`docs/.../routing.md`)

1. **内置路由(builtin)**:针对 Claude Code / Codex 的开箱规则。例如 Claude Code 主请求在客户端未选 recognizable model 时落到 Agent Config model;并自动剥离 Claude Code 注入的 `x-anthropic-billing-header` 系统消息,避免干扰后续路由。
2. **自定义规则(custom rule)**:在 Routing 页配置,`Condition`(header / body 字段 + 操作符 + 值)-> `Rewrites`(set / delete / replace-in-array 等操作)。**按列表顺序匹配,第一个 enabled 命中的规则生效**;可拖动调优先级;`enabled` 开关软启停。
3. **Node.js 脚本规则**:当单个 condition 不够时,选一个本地 `.js/.mjs/.cjs` 文件,作为 async function body 在可复用 Worker 里跑。脚本拿到只读 `input`(body/headers/method/url/model/tokenCount/sessionId/apiKeyId/summary...),通过受控 `api` 对象访问 `api.fetch`(http/https,私有网络允许,响应体上限 1 MiB)、文件系统、环境变量,**返回** `{ model, rewrites, fallback }`。脚本异常 / 超时 / 结果非法一律 **fail-open**:记一条路由诊断,继续下一条规则。文件上限 64 KiB,每次执行前重读(改文件无需重存规则)。

### 3.3 Subagent / Workflow 自动路由(CCR 的招牌特性)

Claude Code 的 Agent / Task / Workflow 会派生子请求。CCR 用**标签注入**让派生请求自选模型:

1. 主请求命中内置路由后,CCR 检查工具列表。
2. 若至少一个模型有 Description,CCR 把可选模型 + 描述注入 `Agent` / `Task` 工具描述与 `prompt` 字段描述。
3. 若工具列表含 `Workflow`,追加指令:派生 agent 的 prompt 必须以模型标签开头。
4. Claude Code 调 `Agent` / `Task` 时,prompt 带 `<CCR-SUBAGENT-MODEL>provider/model</CCR-SUBAGENT-MODEL>`。
5. 派生请求到达 CCR 时,CCR 从 system prompt 或前两条 user message 抽出并删除标签,路由到该模型。

`x-claude-code-agent-id` 等 header 仅用于观测,不驱动选模型。

### 3.4 Codex `apply_patch` 协议桥

Codex 原生 `apply_patch` 是 freeform 工具(输入是原始 patch 文本),非 GPT 模型处理普通 function 工具更稳。CCR 把 `apply_patch` 改写为上游可见的 `virtual_apply_patch` function 工具,把 `apply_patch.lark` 文法塞进工具描述,要求模型把 patch 放进 `patch` 字段;模型返回时再改写回 Codex 期望的 `custom_tool_call` 形状。CCR 不直接改文件,Codex 仍执行 patch。GPT 系模型走原生 freeform 路径。

## 4. 上游执行与 fallback(`packages/core/src/gateway/upstream/`)

- `executor.ts`:`fetchUpstreamWithFallback` 按 `RouteExecutionPlan.attempts[]` 逐个尝试,合并 fallback 响应头,管理响应流(取消 / 销毁 / 去重)。
- `retry-policy.ts`:决定是否重试与退避时长。
  - `shouldFallbackAfterStatus(status, mode)` -> 调 `failure-classifier.classifyRouteFailure(status, mode).shouldFallback`。**是否 fallback 取决于状态码 + fallback 模式**,不是一刀切。
  - `retryDelayAfterStatus`:优先读上游 `retry-after` header(秒数或 HTTP-date,上限 60s),否则指数退避。
  - 指数退避:`base=1000ms`,`2 ** attempt`,`cap=30000ms`(最多 10 次方)。
  - 网络错误用同样的指数退避。

## 5. 凭据池与限流(`packages/core/src/providers/` + `gateway/limits/`)

provider 配置里的 `Credential pool`(`docs/.../providers.md`):

- 每个 key 有 `Priority`(小的先试)、`Weight`(同优先级 + 用量相近时的加权倾向,默认 1)、`Limits JSON`(本地限流规则)。
- Limits 字段:`rpm/rph/rpd`、`tpm/tph/tpd`、`ipm/iph/ipd`、`maxRequests+windowMs`、`maxTokens+quotaWindowMs`。
- CCR 跟踪每个 key 的请求 / token / image 窗口,**一旦即将超限就跳过该 key,试同 provider 的另一个 key**(`recordProviderCredentialOutcome` 记录结果)。
- 凭据池是**上游 provider key 池**,与客户端访问 key(API Keys 页)分离。
- `gateway/auth/api-key-authorizer.ts` 的 `reserveApiKeyLimits` 在客户端侧也做 limit 预留。
- `gateway/limits/window-limiter.ts` 是通用窗口限流器。

## 6. 请求管线(`packages/core/src/gateway/request/pipeline.ts`)

`GatewayRequestPipeline.proxyRequest` 是单请求的完整流水线(从源码可见顺序):

1. 读请求体,生成 `requestId`,起 `RequestRouteTraceRecorder`(若开启日志)。
2. **Header normalization**:保留 / 转发 header(`forwardHeaders`),若有 client apiKey 则剥离本地网关鉴权 header(`stripLocalGatewayAuthHeaders`),写入 `x-auth-api-key-id` / `x-auth-sub` / `x-client-request-id`。每一步都记 route trace 变更(前/后值 + 操作 add/remove/replace)。
3. **Cursor 兼容**(`prepareCursorOpenAICompatChatBody`):为 Cursor 改写 OpenAI chat body。
4. **Claude Code 模型发现**(`prepareClaudeCodeDiscoveredModelRequest`):把 Claude Code 选的模型映射到 CCR 模型。
5. **Claude App 模型发现**(`prepareClaudeAppDiscoveredModelRequest`):Claude App 需 Claude 兼容模型名,CCR 把 `Provider/model` / Fusion 映射成 Claude App 能识别的条目。
6. 路由决策(上述 routing 引擎)-> `RouteExecutionPlan`。
7. **Codex apply_patch 桥** / **Codex multi-agent 桥** / **context archive** 等特性按需介入。
8. `fetchUpstreamWithFallback` 按 attempts 链上游执行,带 fallback / 重试。
9. 响应阶段:`anthropic-response-model` 改写(`rewriteAnthropicMessageStartModelStream`)、hosted web search、codex compact 响应流、context archive handoff 等。
10. 全程 `routeTrace` 记录每个 phase(ingress / compatibility / routing / upstream)的变更与耗时;`recordGatewayRequestLog` + `recordGatewayUsageCapture` 落日志与用量。

## 7. Fusion - 能力增强(`packages/core/src/mcp/` + `gateway/features/`)

给本身不支持某能力的模型「 fuse 」上新能力:

- **Fusion vision**:给无视觉的模型加图像理解。
- **Fusion web search**:hosted web search(`gateway/features/hosted-web-search/`),含 discovery / evidence / request-transform / response-transform / sse。
- **Fusion MCP / ToolHub**:把 MCP 工具、ToolHub 暴露给模型(`mcp/toolhub-mcp.ts`、`mcp/fusion-tool-fallback-mcp.ts` 等)。

Fusion 模型在路由里也是一种 `GatewayModelRef`(`kind: "gateway"`),可与 provider 模型一样被选中。

## 8. 可观测性(`packages/core/src/observability/`)

- `request-log-store.ts` + `request-log-runtime.ts` + `request-log-worker.ts`:请求日志(异步 worker 落库)。
- `route-trace.ts`:`RequestRouteTraceRecorder` 逐请求记录每个 phase 的 mutation(headers / body 的 before/after/operation)与耗时,可回放「这个请求被怎么改写、路由到哪」。
- `raw-trace-sync.ts`:body 采样器。
- `request-log-model.ts`:抽取 requested / response model。
- 用量:`usage/store.ts` 捕获 token / cost;`models/pricing-service.ts` 按模型定价估算成本。
- account usage:provider 配置里的 usage connector(standard endpoint / HTTP JSON / browser request / raw connector),把上游余额 / 订阅配额映射成统一字段。

## 9. Agent Profiles - 多实例启动配置(`docs/.../profiles.md`)

每个 Agent Config 有独立 `id` + name,可对同一 agent 建多个配置(如「Claude Code - Work」「Codex - Fusion Vision」),各自模型 / 作用域 / Bot。机制:

- **独立配置文件**:「Only opened from CCR」模式下,Claude Code / Codex 写 CCR 管理的、按 config id 隔离的配置文件。
- **独立 launcher**:Claude Code / Grok CLI / Kimi CLI 用独立 launch wrapper;Codex / ZCode 用独立 middleware launcher。
- **独立 app data 目录**:App 模式下按 config id 隔离 user-data 目录,可同时跑多个实例。
- **运行时状态**:按 entry mode + config id 跟踪运行实例;重开同 config 激活已有窗口。

效果:切换 provider / 模型不用改 agent 配置文件,从 CCR 打开即可。

## 10. 其他

- **Proxy mode**:桌面特性,CCR 作为 HTTP/HTTPS 代理,用 MITM 拦截解密 HTTPS,把支持的模型请求代理进 CCR 网关路径(需装 CA)。
- **平台适配**:`packages/core/src/platform/windows-app-discovery.ts`、`windows-system.ts`、`socket-compat.ts` -- Windows 原生支持。
- **插件 / marketplace**:`packages/core/src/plugins/`(backend-service、marketplace、service)。
- **AgentClaw**:把 App 模式的 agent 通过 Bot 转发到 IM(Telegram / Discord / Slack / 飞书 / 企微 / 钉钉 / Line / 微信 iLink)。

## 11. 设计要点小结

1. **路由是独立模块**,有明确数据模型(决策 / 执行计划 / 诊断码),不是散落在 handler 里的 if-else。
2. **执行计划 = 有序 attempts 链**,primary + fallback 模型前置确定,failure-classifier 决定是否推进到下一个 attempt。
3. **三层凭据严格分离**,客户端 key 与上游 provider key 池不混。
4. **可脚本化的路由**:Node.js Worker 跑用户脚本,受控 API,异常 fail-open,兼顾灵活与稳定。
5. **全程 route trace**:每个请求的每次改写都可回放,可观测性强。
6. **协议桥作为 feature 插件**:每种兼容性修补(apply_patch / cursor / claude-app / web-search)是独立的 feature 模块,按需挂进管线。
