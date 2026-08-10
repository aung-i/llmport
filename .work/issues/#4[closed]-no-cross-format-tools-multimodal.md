# 4. 跨格式翻译不支持工具调用和多模态（与原生接口不对齐）

- 状态：closed
- 提出时间：2026-08-09
- 关联：#2（跨格式转换本期只覆盖 text chat，工具/多模态列为已知边界 deferred）

## 描述

issue #2 做了 OpenAI ↔ Anthropic 双向格式转换，但只覆盖 text chat。工具调用（tools / tool_calls / tool_use）和多模态（image）在跨格式请求里被**静默丢弃**。

OpenAI 和 Anthropic 原生接口都支持工具调用和视觉/多模态。网关的卖点是「任意客户端配任意供应商」--如果跨格式路径丢功能，那条路就是二等公民，与原生接口不对齐。同格式（直通）这两项正常工作，所以这不是「不支持」，而是「跨格式没翻译」。

## 现状

- 同协议（OpenAI→OpenAI、Anthropic→Anthropic）：直通，tools / image 正常。
- 跨协议（OpenAI→Anthropic、反向）：`translator.py` 只翻 text：
  - 请求 `tools` 字段：不带走。
  - assistant 消息的 `tool_calls`、`tool` role 消息：拍平成文本，结构丢失。
  - `image_url` / image content block：丢弃，只留 text 部分。
  - `finish_reason` ↔ `stop_reason` 里 `tool_calls`↔`tool_use` **已映射**（reason 映射了，内容没映射）。
- 静默丢弃，运行时无告警。

## 为什么要做

原生接口都支持 → 网关必须对齐。否则用户用 OpenAI 客户端打 Anthropic 供应商时，tools / 视觉功能凭空消失且无提示，难排查。

## 待解决

- **工具调用双向翻译**（难点）：
  - 请求：OpenAI `tools` / `function` ↔ Anthropic `tools`（`input_schema`）。
  - 消息：OpenAI assistant `tool_calls` ↔ Anthropic `tool_use` content block；OpenAI `tool` role 消息 ↔ Anthropic `tool_result` content block。
  - 流式：Anthropic `content_block_start(tool_use)` / `input_json_delta` ↔ OpenAI `tool_calls` delta 增量（partial JSON 拼接）。
- **多模态双向翻译**（相对小）：
  - OpenAI `image_url`（http url 或 data URL）↔ Anthropic `image` content block（base64 + `media_type`；url source 的处理待定--Anthropic 也支持 url source，可能不用 fetch）。
- **静默丢弃策略**：翻译前检测到不支持/未覆盖的跨格式字段时，warn 继续 还是 拒绝？（即使翻译做完，边界 case 仍需明确策略。）
- **放哪层**：继续放 `translator.py`（纯函数），沿用 #2 架构；handler 保持薄转发。

## 方案

待定。实现前需定：先做工具调用还是多模态？工具调用的流式增量（partial JSON）是主要难点。

---

## 结论 / 解决方案（2026-08-09 完成）

工具调用与多模态**都做**（用户决定「都要做」），双向，覆盖请求 / 非流式响应 / 流式响应。字段映射照 OpenAI Chat Completions 与 Anthropic Messages 公开规格实现，不是开放问题。改动只在 `translator.py`（纯函数）；handler 保持薄转发，server 路由层已调用这些函数，无需改动。

### 工具调用映射

| 维度 | OpenAI Chat Completions | Anthropic Messages |
|---|---|---|
| 工具定义 | `tools:[{type:"function",function:{name,description,parameters}}]` | `tools:[{name,description,input_schema}]` |
| 工具选择 | `tool_choice:"auto"\|"none"\|"required"\|{type:"function",function:{name}}` | `tool_choice:{type:"auto"\|"none"\|"any"\|"tool",name?}` |
| assistant 调用 | message.`tool_calls:[{id,type:"function",function:{name,arguments:"<JSON 串>"}}]` | content block `{type:"tool_use",id,name,input:<对象>}` |
| 工具结果 | `{role:"tool",tool_call_id,content}` | user 消息 content block `{type:"tool_result",tool_use_id,content}` |
| 完成原因 | `finish_reason:"tool_calls"` | `stop_reason:"tool_use"` |
| 流式调用头 | `delta.tool_calls:[{index,id,type,function:{name,arguments:""}}]` | `content_block_start` + `content_block:{type:"tool_use",id,name,input:{}}` |
| 流式参数增量 | `delta.tool_calls:[{index,function:{arguments:"<片段>"}}]` | `content_block_delta` + `delta:{type:"input_json_delta",partial_json:"<片段>"}` |

关键点：
- `arguments`（JSON 字符串）↔ `input`（对象）：非流式需 `json.loads`/`json.dumps`；**流式 `arguments` 片段与 `partial_json` 片段是同一 JSON 字符串的增量，直接透传，不解析**。
- OpenAI `tool_calls[].index`（仅对 tool_call 计数）与 Anthropic content block `index`（含 text 块）分离：翻译时维护「Anthropic block index → OpenAI tool_call index」映射，互不干扰。
- 连续多条 OpenAI `tool` 消息合并为一条 Anthropic user 消息（多个 `tool_result` 块）--Anthropic 要求并行工具结果放一起且角色交替。

### 多模态映射

| OpenAI | Anthropic |
|---|---|
| `{type:"image_url",image_url:{url:"data:<media_type>;base64,<data>"}}` | `{type:"image",source:{type:"base64",media_type,data}}` |
| `{type:"image_url",image_url:{url:"https://..."}}` | `{type:"image",source:{type:"url",url}}` |

- `media_type` 限 jpeg/png/gif/webp（Anthropic 接受集）；不在此集（如 bmp）的图像**静默丢弃**。
- url source 双方都支持，**不 fetch**（解决待解决项里的「url source 处理待定」）。

### 待解决项的落实

- 先做工具调用还是多模态 → 都做。
- url source → 直接透传 url，不 fetch。
- 放哪层 → `translator.py` 纯函数，沿用 #2 架构，handler/server 不动。
- 静默丢弃策略 → 工具与受支持图像已全量翻译，无丢弃；仅不支持 `media_type` 的图像丢弃（与 Anthropic 自身接受集一致，属合理边界）。

### 验证

- 新增 24 个纯函数测试（请求/响应/流式 × 双向 × 工具+图像）+ 1 个端到端路由往返测试（OpenAI 客户端带 tools/tool 结果 → Anthropic 供应商收到翻译后的 tools/tool_result；Anthropic tool_use 响应 → 客户端收到 tool_calls）。
- 全量 379 passed，覆盖率 87.28%（translator.py 85%）。同协议直通无回归。

