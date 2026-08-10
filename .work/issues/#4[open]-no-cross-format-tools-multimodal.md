# 4. 跨格式翻译不支持工具调用和多模态（与原生接口不对齐）

- 状态：open
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
