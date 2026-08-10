# 1. llmport 本体无 key 管理

- 状态：closed（2026-08-09 二次重开已修正：鉴权从「可选」改为「强制」）
- 提出时间：2026-08-09

## 描述

llmport 网关本体目前没有对客户端做任何鉴权，仅靠 loopback-only 绑定作为安全边界。这意味着任何能访问本机对应端口的人，都可以通过网关消耗用户配置的供应商 token（即用户的「token 财产」），这是不可接受的。

## 现状

- 网关绑定 loopback（127.0.0.1），不监听外网。
- 客户端 -> 网关之间没有任何 key / token 校验。
- 网关 -> 供应商之间使用 providers.yaml 里的 api_key（这部分已有）。
- 上一轮讨论过两个方向，尚未定：
  - A：不加鉴权，纯靠 loopback
  - B：可选的网关访问 key（客户端请求需带 key 才能使用）

## 待解决

- 选 A 还是 B（或其它方案）。
- 若选 B：key 存哪、怎么下发到客户端、怎么校验、放哪个配置文件、明文还是哈希存储。

## 方案（2026-08-09）

**决定：选 B -- 可选的 llmport API key。** 未配置时行为不变（纯 loopback，向后兼容）；配置后客户端必须携带该 API key 才能使用网关。B-可选严格优于 A（不配即等同 A，配了即收紧），且 issue 自述现状「不可接受」，方向明确。

> 术语澄清：这个 key 是「llmport 自己的 API key」--客户端访问 llmport 时出示的 api key（类比 OpenAI/Anthropic 的 api key 是访问其服务的凭证），不是「网关 key」。下文统一称「API key」。原 issue 现状里写的「网关访问 key」即指此 key。

逐条回答待解决问题：

- **key 存哪 / 放哪个配置文件**：放 `providers.yaml`（0600 密钥文件）顶层 `api_key` 字段。遵循既有密钥边界--config.yaml（0644）只放非敏感的 host/port，所有密钥统一进 0600 文件。顶层 `api_key` 是 llmport 自己的（客户端用）；`providers[].api_key` 是各上游供应商的（转发用），层级不同、用途不同，文档写清不会混淆。
- **怎么校验**：Starlette 中间件 `APIKeyAuthMiddleware`。配置了 API key 时，除 `/health`（liveness 探针必须免鉴权）外所有路由要求客户端携带 key。接受两种头部：`Authorization: Bearer <key>`（OpenAI SDK 自然走这个）或 `x-api-key: <key>`（Anthropic SDK 自然走这个），任一匹配即可。用 `hmac.compare_digest` 常量时间比较防时序侧信道。缺失/错误 -> 401 JSON。llmport 忽略客户端的 bearer/x-api-key 用于上游鉴权（上游用 provider 自己的 key），无冲突。
- **明文还是哈希存储**：明文存于 providers.yaml，与既有 provider api_key 一致（它们也是明文）。单独哈希 llmport api key 而 provider api key 明文是不一致的安全表演；且 providers.yaml 一旦泄露，provider key 已全暴露，哈希这个 key 无意义。运行时常量时间比较。`config show` 默认打码。
- **怎么下发到客户端**：`llmport api-key show --reveal` 打印明文供用户复制到 SDK 配置（`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` 环境变量或 SDK config）。默认 `llmport api-key show` 只显示已设置/未设置状态（打码）。

**CLI**：新增 `api-key` 子命令组：`llmport api-key set`（交互输入不回显）、`llmport api-key show [--reveal]`、`llmport api-key clear`。

**实现点**：ConfigStore 加 `load_api_key/set_api_key/clear_api_key`；GatewayState.reload 载入 `api_key`；server.py 加 `APIKeyAuthMiddleware` 并接入 create_app；cli.py 加 `api-key` 子命令。

## 结论（2026-08-09）

已实现 **B - 可选的 llmport API key**，向后兼容。状态：**关闭**。

实现：
- `providers.yaml` 顶层 `api_key` 字段存 llmport 自己的 api key（0600 密钥文件）；`ConfigStore.load_api_key/set_api_key/clear_api_key`。顶层 `api_key`（客户端->网关）与 `providers[].api_key`（网关->上游）层级不同、用途不同。
- `GatewayState.reload` 载入 `api_key`；空串 = 不强制鉴权（默认）。
- `server.py` 纯 ASGI `APIKeyAuthMiddleware`：配置了 key 时，除 `/health` 外所有路由要求 `Authorization: Bearer <key>` 或 `x-api-key: <key>`；`hmac.compare_digest` 常量时间比较；缺失/错误 -> 401。纯 ASGI（非 BaseHTTPMiddleware）实现，不缓冲流式 SSE。
- CLI `llmport api-key set`（不回显）/`show [--reveal]`/`clear`。

测试：`tests/test_api_key.py` 19 个用例（store 持久化/容忍缺失/非字符串、state 载入、中间件：无 key 开放、有 key 401/200、`/health` 免鉴权、空 bearer 拒绝）。全量 **311 passed**，覆盖率 **90.4%**，零回归。CLI 端到端冒烟通过（set / show masked / reveal / clear，provider 保留，`config show` 自动打码）。

向后兼容：未配置 api_key 时行为与原先完全一致（纯 loopback，无鉴权）。

## 用户反馈（2026-08-09 重开）

> 我觉得这不对，不应该上游 key 混合在一起。要有自己的 key 管理啊。

上一版把 llmport 自己的 API key 存进了 `providers.yaml` 顶层 `api_key` 字段，和上游供应商的 `providers[].api_key` 混在同一个文件里。这是错误的设计：**llmport 自己的 key（客户端->网关）和上游供应商的 key（网关->上游）是两回事，不该共用一个密钥文件。** llmport 应该有自己独立的 key 管理。

## 重做方案（2026-08-09）

把 llmport 自己的 API key 从 `providers.yaml` 抽出来，不再和上游供应商 key 混在一起。

> 方案演进：起初考虑独立文件 `api_key.yaml`（0600），用户反馈「搞复杂了」，最终决定直接放进 `config.yaml`（一个 `api_key` 字段，和 gateway/models 同文件），保持简单。

- **存哪**：`config.yaml` 的 `api_key` 字段（`~/.config/llmport/config.yaml`）。未设置时该字段不存在（= 无鉴权，纯 loopback）。与 `providers.yaml`（上游供应商 key）彻底分开。
- **ConfigStore**：`load_api_key/set_api_key/clear_api_key` 改为读写 `config.yaml` 的 `api_key` 字段（保留 gateway/models），**完全不碰 providers.yaml**。
- **GatewayState**：`reload()` 改用 `self.store.load_api_key()`，不再从 providers 数据里取 `api_key`。
- **CLI**：`api-key set/show/clear` 已走 store 方法，自动生效；提示文案改为指向 `config.yaml`。`config show` 对 `config.yaml` 的 `api_key` 行也打码（原先只打码 providers.yaml）。
- **边界**：
  - `config.yaml`（0644）：网关地址 + 模型映射 + **llmport 自己的 api_key**（客户端->网关）。`config show` 打码显示。
  - `providers.yaml`（0600）：**上游供应商**及其 api_key（网关->上游）。
- 不做迁移：本特性在分支上、未发布，无存量用户；`providers.yaml` 里若残留顶层 `api_key` 会被直接忽略（不再读取），不会报错。

## 结论（2026-08-09 重做）

已将 llmport 自己的 API key 从 `providers.yaml` 迁到 `config.yaml` 的 `api_key` 字段，与上游供应商 key 彻底分离。状态：**关闭**。

实现：
- `store.py`：`load_api_key/set_api_key/clear_api_key` 读写 `config.yaml` 的 `api_key` 字段（`save_config` 保留 gateway/models），不碰 `providers.yaml`；移除了上一版的 `key_path`/独立文件设计。
- `state.py`：`reload()` 用 `self.store.load_api_key()`。
- `cli.py`：`api-key` 子命令自动生效；`_config_show` 对 `config.yaml` 的 `api_key` 行打码（防泄漏）；`config init` 模板加 `api_key` 注释示例；路径/标签文案更新。
- 测试：`test_api_key.py` 改为断言 `config.yaml` 存储（持久化、保留 gateway/models、clear、容忍缺失/非字符串）；新增 `test_show_masks_llmport_api_key`（`config show` 不泄漏 key）。全量 **349 passed**，覆盖率 **87.09%**。

向后兼容：未配置 `api_key` 时行为不变（纯 loopback，无鉴权）。

> ⚠️ 上一版（重做）的「向后兼容：未配置 api_key 时行为不变（无鉴权）」结论已被本次二次重开**推翻** -- 见下文。鉴权现在是强制的，不存在不鉴权模式。

## 用户反馈（2026-08-09 二次重开）

> 不考虑不鉴权的情况。那不安全。setup 模板带上这个字段没有

上一版把鉴权做成**可选**（未配置 key = 纯 loopback 无鉴权，向后兼容）。用户明确否决：不鉴权不安全，**不考虑**。鉴权是网关的核心职责，不是可选开关。同时问 setup 模板是否带 `api_key` 字段 -- 答案是只带注释示例、不带真实值；用户决定 **setup 自动生成** 这个强制的 api_key。

## 重做方案（2026-08-09 强制鉴权）

把鉴权从「可选」改为「强制」：**不存在不鉴权模式。** 一个未配置 key 的网关是错误状态，必须 fail-closed，绝不能开放服务。

责任边界（本次明确的划分）：
- **gateway runtime（真正的安全强制点）**：`APIKeyAuthMiddleware` 始终强制 + `run_daemon` 拒绝无 key 启动。安全靠这两层，不靠 CLI。
- **CLI `_cmd_start`（UX 前置检查）**：在 providers 检查后加 api_key 检查，给清晰错误信息、避免「spawn 后立刻死」的差体验。是 UX，不是安全边界。
- **`DaemonManager.start`（进程生命周期层）**：**不**掺入 api_key 检查 -- 那是 config 关切，不是进程关切。保持进程层职责单一。（之前一度想放这里，是边界混淆。）

逐条：

- **middleware（server.py `APIKeyAuthMiddleware`）**：去掉 `if expected`（空=open）分支。除 `/health`（liveness 必须免鉴权）外所有路由始终要求 key。三种结果：
  - 未配置 key（`state.api_key` 为空，错误状态）-> **503** fail-closed（`api_key not configured -- run llmport setup`），绝不开放。
  - key 配置了但缺失/错误 -> **401**。
  - 正确 -> 放行。`hmac.compare_digest` 常量时间比较。纯 ASGI，不缓冲 SSE。
- **setup（cli.py `_cmd_setup`）**：`_ensure_store_init` 后，若无 key 则 `generate_api_key()` 生成并写入 `config.yaml`，**打印明文**供用户复制到 SDK（OpenAI `Authorization: Bearer` / Anthropic `x-api-key`）。已有 key 不覆盖。
- **generate_api_key（store.py）**：`sk-llmport-` + `secrets.token_urlsafe(32)`（~43 char 熵）。模块级函数。
- **start 拒绝（cli.py `_cmd_start`）**：providers 检查后加 `if not load_api_key(): 提示运行 setup; return`。
- **run_daemon backstop（daemon.py）**：`init_first_run` 后 `if not load_api_key(): stderr + sys.exit(1)`。防直接 `llmport --daemon` 绕过 CLI。
- **文案修正**：`_CONFIG_TEMPLATE` 注释去掉「留空=不鉴权」，改为「鉴权是强制的，setup 自动生成」；`api-key show`（未设置时）从「不强制鉴权」改为「网关无法启动」；`api-key clear` 从「回到无鉴权」改为「清除后网关无法启动」。

测试改动（强制鉴权波及所有走 ASGI 的测试 -- 它们原来依赖无鉴权）：
- 新增 `tests/_helpers.py`：`TEST_API_KEY` + `AuthedClient(TestClient 子类)` 默认注入 `x-api-key`。6 个非鉴权测试文件（test_app_isolation / test_control_models / test_fallback_loop / test_integration / test_server_routes / test_translator）的 `_make_app/_make_store` helper 写入 `set_api_key(TEST_API_KEY)`，`TestClient(` -> `AuthedClient(`（42 处）。
- `test_api_key.py`：`TestNoKeyConfigured` 从断言「无 key = open 200」改为「无 key = 503 fail-closed」；`TestKeyConfigured`（显式 header 的 plain TestClient）不变。
- 新增：`test_setup_generates_api_key` / `test_setup_preserves_existing_api_key` / `test_start_refuses_without_api_key` / `test_run_daemon_refuses_without_api_key`。

## 结论（2026-08-09 强制鉴权重做）

已把鉴权从可选改为**强制**，不存在不鉴权模式。状态：**关闭**（重开已修正）。

实现：
- `store.py`：`generate_api_key()`（`secrets.token_urlsafe`）；`_CONFIG_TEMPLATE` / `load_api_key` 文案修正（去掉「不鉴权」）。
- `server.py`：`APIKeyAuthMiddleware` 始终强制；无 key -> 503 fail-closed；错误/缺失 -> 401；`/health` 免鉴权。
- `daemon.py`：`run_daemon` 无 key -> `sys.exit(1)`（backstop）。
- `cli.py`：`setup` 自动生成并打印 key；`_cmd_start` api_key 前置检查；`api-key show/clear` 文案修正；`_ensure_store_init` docstring 修正（不再声称生成 key）。

测试：全量 **353 passed**，覆盖率 **87.43%**。E2E（真实 daemon）验证：`/health` 200（免鉴权）、无 key 401、错 key 401、对 key 200。CLI 冒烟：setup 生成+打印 key、`api-key show --reveal` 回显、有 provider 无 key 时 start 拒绝且不 spawn。

安全模型：未配置 key 的网关无法服务（middleware 503 + run_daemon 拒启动 + start 拒启动）。生产路径（`llmport setup` -> `start`）必然带 key。

