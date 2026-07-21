# llmgate Design Spec

**Date:** 2026-07-21
**Status:** Review

## Overview

llmgate is a terminal-based LLM API Gateway. It runs as a daemon on your machine, providing a unified API endpoint for all your tools while managing multiple LLM providers, API keys, model aliases, fallback routing, and health monitoring through a Textual TUI.

### Core Philosophy

- **Configure by provider, use by model.** You set up providers with their keys and protocols. Daily use is just picking a model name.
- **TUI-first, gateway-second.** `llmgate` opens the TUI. The gateway daemon starts automatically and keeps running when you close the TUI.
- **Zero friction.** No master password, no config files to hand-edit. Random key auto-generated on first run for local encryption. All operations in the TUI.

---

## Tech Stack

| Layer | Choice |
|---|---|
| Language | Python 3.12+ |
| TUI | Textual |
| HTTP | httpx (async) |
| Gateway server | uvicorn + starlette |
| Encryption | cryptography (Fernet) |
| Package management | uv |
| Distribution | `uv tool install llmgate` |

---

## Architecture

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────┐
│  llmgate    │     │  llmgate daemon  │     │  Providers  │
│  (TUI)      │ ←→  │  (gateway)       │ ←→  │  OpenAI     │
│  Textual    │ IPC │  uvicorn         │     │  Anthropic  │
│             │     │                  │     │  Groq       │
│  Manage     │     │  port 11434 (OA) │     │  DeepSeek   │
│  Monitor    │     │  port 11435 (AN) │     │  ...        │
└─────────────┘     └──────────────────┘     └─────────────┘
```

- **TUI and daemon communicate via local HTTP control API** (on a separate internal port).
- **Daemon reads encrypted config** at startup using the auto-generated key.
- **TUI can start/stop/restart** the daemon and read real-time status.

### Gateway Endpoints

| URL | Protocol | Example Paths |
|---|---|---|
| `http://localhost:11434/v1` | OpenAI API | `/chat/completions`, `/models` |
| `http://localhost:11435/v1` | Anthropic Messages | `/messages` |

The port and base path the client uses determines the protocol. If the current model's provider doesn't support that protocol, the gateway returns an error.

### Control API (internal)

The TUI talks to the daemon via a local-only HTTP API on `127.0.0.1`. The daemon writes its control port to `~/.config/llmgate/daemon.pid` on startup. The TUI reads this file to discover the daemon.

```
GET  /api/status        — daemon status, uptime, active model
GET  /api/providers     — list providers with health
GET  /api/models        — list models with bindings
POST /api/models/switch — switch active model
POST /api/providers     — add/update provider
POST /api/providers/test — test connection
POST /api/daemon/stop   — stop daemon
POST /api/daemon/restart — restart daemon
```

### Daemon Lifecycle

- On `llmgate` launch, TUI checks if `~/.config/llmgate/daemon.pid` exists and if the port is alive.
- If alive → TUI connects to existing daemon.
- If not alive → TUI starts daemon as a subprocess, writes new PID file.
- On `llmgate stop` → TUI calls `POST /api/daemon/stop`, daemon cleans up PID file and exits.
- If TUI exits normally (Ctrl+C, quit), daemon keeps running.
- If daemon crashes, PID file is stale; TUI detects this on next launch and restarts.

---

## Data Model

### Provider

```yaml
id: "anthropic"
name: "Anthropic"
protocol: "anthropic"          # "openai" | "anthropic"
base_url: "https://api.anthropic.com"
api_key: "<encrypted>"
models:                        # models this provider supports
  - name: "claude-opus-4-820250710"
    aliases: ["claude-opus", "opus"]
  - name: "claude-sonnet-5"
    aliases: ["claude-sonnet", "sonnet"]
health:
  status: "up"                 # "up" | "degraded" | "down"
  latency_ms: 234
  last_check: "2026-07-21T14:32:00Z"
```

### Model (logical, auto-derived from provider model aliases)

Logical models are automatically created from provider model aliases. When two providers use the same alias, they merge into one logical model with multiple bindings.

**Creation rule:** When you add a model with alias "claude-opus" to Anthropic, and OpenAI also has a model with alias "claude-opus", they appear as ONE entry in the model list with "2 供应商". No manual model creation needed — aliases are the linking key.

Users can also manually create a logical model and bind providers to it (via [+ 添加] on the models page), for cases where aliases don't match across providers.

```yaml
id: "claude-opus"
bindings:
  - provider: "anthropic"
    model_name: "claude-opus-4-820250710"
    priority: 1
  - provider: "openai"
    model_name: "claude-opus-4-8"
    priority: 2
routing_strategy: "priority_fallback"  # "priority_fallback" | "round_robin" | "lowest_latency"
```

### Active Route (runtime state)

```yaml
active_model: "claude-opus"
active_provider: "anthropic"
active_model_name: "claude-opus-4-820250710"
```

---

## Configuration & Security

### Encryption

1. On first run, a random Fernet key is generated and saved to `~/.config/llmgate/key`.
2. API keys are encrypted with this key before writing to `~/.config/llmgate/config.enc`.
3. The daemon reads the key at startup, decrypts config, holds keys in memory.
4. No master password. No external dependencies (keychain, secret service, etc.).

### Config Format

```yaml
# ~/.config/llmgate/config.yaml (before encryption)
version: 1
gateway:
  openai_port: 11434
  anthropic_port: 11435
providers: [...]
models: [...]
active_model: "claude-opus"
```

---

## TUI Design

### Page Structure: 5 Tabs

```
[模型]  [供应商]  [网关]  [统计]  [设置]
```

Navigation: `←` `→` to switch tabs, `↑` `↓` to move within a tab, `Enter` to select/expand, `Esc` to go back. Mouse click works everywhere.

### Page 1: Models (home)

```
┌─ llmgate ──────────────────────┐
│  ◉ 模型  ○ 供应商  ○ 网关  ○ 统计  ○ 设置 │
├───────────────────────────────┤
│                               │
│  当前：claude-opus             │
│  Anthropic · 234ms · 🟢      │
│                               │
│  ────────────────────────────  │
│                               │
│  ▶ claude-opus      2 供应商   │
│    claude-sonnet    1 供应商   │
│    gpt-5            1 供应商   │
│    deepseek-v4      1 供应商   │
│    llama-4          2 供应商   │
│                               │
│  [模型详情]  [+ 添加]           │
└───────────────────────────────┘
```

- Main interaction: `↑↓` to scroll, `Enter` to switch active model (immediate).
- `Enter` on the active model or click [模型详情] opens the detail page.
- [+ 添加] is for manually defining a logical model (binding providers to an alias).

### Page 1b: Model Detail (Enter from model list)

```
┌─ llmgate ───────────────────┐
│  ← 返回                      │
├──────────────────────────────┤
│  claude-opus                 │
│                              │
│  供应商              优先级 ↑↓│
│  ┌────────────────────────┐  │
│  │ ① Anthropic  234ms 🟢  │  │
│  │ ② OpenAI     890ms 🟡  │  │
│  │ [+ 绑定供应商]          │  │
│  └────────────────────────┘  │
│                              │
│  路由策略                     │
│  优先级 fallback  ▼          │
│                              │
│  [设为当前]  [删除模型]        │
└──────────────────────────────┘
```

- Bind/unbind providers to this model.
- Adjust priority ordering.
- Change routing strategy per model.

### Page 2: Providers

```
┌─ llmgate ──────────────────────┐
│  ○ 模型  ◉ 供应商  ○ 网关  ○ 统计  ○ 设置 │
├───────────────────────────────┤
│                               │
│  Anthropic             🟢     │
│  api.anthropic.com            │
│  模型: claude-opus, sonnet    │
│                               │
│  OpenAI                 🟡     │
│  api.openai.com               │
│  模型: gpt-5, gpt-4o          │
│                               │
│  DeepSeek               🔴     │
│  api.deepseek.com · 超时      │
│  模型: deepseek-v4            │
│                               │
│  [+ 添加供应商]                 │
└───────────────────────────────┘
```

- Status dot: 🟢 up / 🟡 degraded / 🔴 down.
- `Enter` on a provider → edit form (popup).
- [+ 添加供应商] → add form (popup).

### Add/Edit Provider (popup)

```
┌─ 添加供应商 ───────────────────┐
│                               │
│  名称     [Anthropic________] │
│  API Key  [sk-ant-***_______] │
│  地址     [api.anthropic.com_] │
│  协议     ◉ Anthropic  ○ OpenAI│
│                               │
│  模型                          │
│  ┌─────────────────────────┐  │
│  │ 模型名           别名     │  │
│  │ claude-opus-4-8  opus   │  │
│  │ [+ 添加模型]             │  │
│  └─────────────────────────┘  │
│                               │
│  [从 API 拉取模型]              │
│  [测试连接]      [保存]  [取消]  │
└───────────────────────────────┘
```

- [从 API 拉取模型]: calls `/v1/models` or Anthropic models endpoint.
- [测试连接]: tries to call the API with the key, reports success/failure + latency.
- Each model row: actual model name + comma-separated aliases.

### Page 3: Gateway

```
┌─ llmgate ──────────────────────┐
│  ○ 模型  ○ 供应商  ◉ 网关  ○ 统计  ○ 设置 │
├───────────────────────────────┤
│                               │
│  状态            ● 运行中       │
│  运行时长        2h 34m        │
│  OA 端口         11434        │
│  AN 端口         11435        │
│                               │
│  ────────────────────────────  │
│                               │
│  供应商健康                     │
│  Anthropic      🟢 234ms      │
│  OpenAI         🟡 890ms      │
│  DeepSeek       🔴 不可达      │
│  Groq           🟢 120ms      │
│                               │
│  ────────────────────────────  │
│                               │
│  [停止网关]  [重启网关]         │
└───────────────────────────────┘
```

### Page 4: Statistics

```
┌─ llmgate ──────────────────────┐
│  ○ 模型  ○ 供应商  ○ 网关  ◉ 统计  ○ 设置 │
├───────────────────────────────┤
│                               │
│  今日                          │
│  请求 847    Token 1.2M        │
│  估算费用 $3.42                │
│                               │
│  按模型                        │
│  claude-opus    512  $1.80    │
│  gpt-5          223  $1.20    │
│  deepseek-v4    112  $0.42    │
│                               │
│  按供应商                       │
│  Anthropic      $1.80         │
│  OpenAI         $1.20         │
│  DeepSeek       $0.42         │
└───────────────────────────────┘
```

### Page 5: Settings

```
┌─ llmgate ──────────────────────┐
│  ○ 模型  ○ 供应商  ○ 网关  ○ 统计  ◉ 设置 │
├───────────────────────────────┤
│                               │
│  数据                          │
│  导出配置               [导出]  │
│  导入配置               [导入]  │
│                               │
│  版本                          │
│  llmgate v0.1.0               │
│  [检查更新]                    │
└───────────────────────────────┘
```

### First-run Flow

On first launch with no config:

```
┌─ 欢迎使用 llmgate ────────────┐
│                               │
│  检测到你是第一次使用            │
│                               │
│  我们将为你：                   │
│  1. 创建加密密钥                │
│  2. 引导你添加第一个供应商       │
│  3. 启动网关                   │
│                               │
│  [开始设置]                    │
└───────────────────────────────┘
```

→ Generate key → Open provider add form → Pull/configure models → Start gateway → Go to home page.

---

## Request Flow

```
Client (e.g., Continue plugin)
  │  POST http://localhost:11434/v1/chat/completions
  │  { model: "gpt-5", messages: [...] }
  ▼
Gateway (uvicorn)
  │  1. Look up active model: "claude-opus"
  │  2. Resolve binding: Anthropic / claude-opus-4-820250710
  │  3. Check: Anthropic protocol = "anthropic", but request came to OA port
  │     → ERROR: protocol mismatch, return 400
  │
  │  OR (if request came to AN port):
  │
  │  4. Forward to https://api.anthropic.com/v1/messages
  │     with model: "claude-opus-4-820250710"
  │  5. On failure: try next priority binding (OpenAI / claude-opus-4-8)
  │  6. Stream back response
  ▼
Client receives response
```

---

## Requirements Summary

### v1 (MVP)

| # | Category | Requirement |
|---|---|---|
| 1 | Security | Auto-generated random Fernet key, local encrypted config, no user intervention |
| 2 | Gateway | Two protocol endpoints on different ports |
| 3 | Gateway | Transparent forwarding: OpenAI API + Anthropic Messages |
| 4 | Gateway | Daemon keeps running after TUI closes |
| 5 | Gateway | Protocol mismatch returns error (no translation) |
| 6 | Provider | Config: name, API key, base URL, protocol type |
| 7 | Provider | Test connection on add/edit |
| 8 | Provider | Manually add model names + aliases |
| 9 | Provider | Pull/refresh model list from API |
| 10 | Provider | Each model supports multiple aliases |
| 11 | Model | Switch by alias, gateway resolves to (provider, actual model name) |
| 12 | Routing | Model binds to multiple providers with priority order |
| 13 | Routing | Fallback on failure by priority |
| 14 | Gateway | TUI view: daemon status, uptime, health checks |
| 15 | Gateway | TUI start/stop/restart daemon |
| 16 | TUI | Models page (home): list, search, switch active model |
| 17 | TUI | Providers page: add, edit, delete, test |
| 18 | TUI | Model detail page: bind providers, adjust priority, routing strategy |
| 19 | TUI | Arrow keys + mouse navigation, no vim keys |
| 20 | CLI | Single binary entry: `llmgate` |
| 21 | CLI | `uv tool install` distribution |
| 22 | Onboarding | First-run wizard: generate key → add provider → start gateway |

### v2+

| # | Category | Requirement |
|---|---|---|
| 23 | Routing | Load balancing: round-robin, lowest-latency |
| 24 | Routing | Cost-aware, context-length-aware, task-type routing |
| 25 | Statistics | Usage stats per model/provider (requests, tokens, cost estimates) |
| 26 | Settings | Change gateway ports |
| 27 | Settings | Config export/import |
| 28 | Gateway | Rate limiting |
| 29 | TUI | Request log viewer with search |

---

## Project Structure (planned)

```
llmgate/
├── pyproject.toml
├── README.md
├── src/
│   └── llmgate/
│       ├── __init__.py
│       ├── cli.py              # entry point, arg parsing
│       ├── app.py               # Textual app
│       ├── daemon.py            # gateway server lifecycle
│       ├── gateway/
│       │   ├── __init__.py
│       │   ├── server.py        # uvicorn/starlette app
│       │   ├── router.py        # request routing logic
│       │   ├── openai.py        # OpenAI protocol handler
│       │   └── anthropic.py     # Anthropic protocol handler
│       ├── config/
│       │   ├── __init__.py
│       │   ├── crypto.py        # Fernet encrypt/decrypt
│       │   └── store.py         # config file read/write
│       ├── models/
│       │   ├── __init__.py
│       │   ├── provider.py      # Provider data model
│       │   └── model.py         # Logical model + bindings
│       └── ui/
│           ├── __init__.py
│           ├── screens/
│           │   ├── models.py    # Models tab
│           │   ├── providers.py # Providers tab
│           │   ├── gateway.py   # Gateway tab
│           │   ├── stats.py     # Statistics tab
│           │   ├── settings.py  # Settings tab
│           │   └── onboarding.py
│           └── widgets/
│               ├── provider_form.py
│               └── model_detail.py
└── tests/
    ├── test_config.py
    ├── test_router.py
    └── test_gateway.py
```
