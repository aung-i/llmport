# 1. llmport 本体无 key 管理

- 状态：open
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
