# 2. 供应商不支持两种 API 格式自动转换

- 状态：open
- 提出时间：2026-08-09

## 描述

用户只配置「实际的供应商」即可，使用 llmport 时应该 OpenAI 和 Anthropic 两种接口都能用——即网关自动做接口格式转换。但当前实测：如果配置的是 OpenAI 供应商，只有 OpenAI 接口能用；配 Anthropic 供应商只有 Anthropic 接口能用。这不符合预期。

开源同类工具通常支持：配一个供应商，两种接口都能调。

## 现状

- 路由按协议前缀分发：`/openai/v1/*` 走 openai_handler，`/anthropic/v1/*` 走 anthropic_handler。
- handler 直接把请求按原格式转发给上游，不做 OpenAI ↔ Anthropic 格式转换。
- 所以上游供应商是什么格式，客户端就只能用那种格式。

## 待解决

- 是否要做双向格式转换（OpenAI ↔ Anthropic）。
- 转换放哪一层（handler 内？独立的 translator 模块？）。
- 流式响应（SSE）的转换怎么处理。
- 哪些字段需要映射、哪些无法映射怎么兜底（如 system prompt 位置差异、工具调用结构差异）。

## 方案（2026-08-09）

**决定：做双向转换，放独立的 `llmport/gateway/translator.py` 模块。** 不塞进 handler（handler 保持「薄转发」职责），translator 是纯函数模块（请求/响应/流式各一组），server 路由层在「客户端协议 != 供应商协议」时调用它。这样 handler_base 的透明转发契约不变，翻译逻辑可独立单测。

逐条回答待解决：

- **是否做双向**：是。OpenAI 客户端 -> Anthropic 供应商、Anthropic 客户端 -> OpenAI 供应商，两个方向都做。路由层：`/openai/v1/chat/completions` 解析到 anthropic 供应商时翻译转发；`/anthropic/v1/messages` 解析到 openai 供应商时翻译转发。同协议仍走原直通路径（零开销、零回归）。
- **放哪层**：独立 translator 模块（纯函数），server 路由层编排（决定何时翻译、调用对应 handler、处理流/非流）。handler 不动。
- **SSE 流式**：translator 提供两个 async generator：`anthropic_stream_to_openai` / `openai_stream_to_anthropic`。用一个增量 UTF-8 解码 + `\n\n` 分帧的 SSE 解析器拆上游字节流为 `(event, data)`，再按目标协议重新编码。Anthropic->OpenAI：message_start 出 role chunk，content_block_delta(text_delta) 出 content chunk，message_delta(stop_reason) 出 finish_reason chunk，message_stop 出 `[DONE]`。OpenAI->Anthropic：先出 message_start + content_block_start，content delta 出 text_delta，finish_reason 出 content_block_stop + message_delta + message_stop。
- **字段映射与兜底**：
  - system prompt：OpenAI 的 `system` role 消息 -> Anthropic 顶层 `system` 字段；反向 Anthropic `system` -> OpenAI system 消息。多个 system 消息用 `\n\n` 拼接。
  - 采样参数：`max_tokens`（Anthropic 必填，OpenAI 缺省时给 1024）、`temperature`、`top_p`、`stop` <-> `stop_sequences` 双向映射。
  - finish_reason <-> stop_reason：stop/end_turn、length/max_tokens、tool_calls/tool_use、stop_sequence。
  - usage：prompt_tokens/input_tokens、completion_tokens/output_tokens，total_tokens 计算。
  - **本轮不做**：工具调用（tools/tool_calls/tool_use）、多模态（image）。跨格式请求里这些字段会被丢弃（文档说明）。错误响应（上游 >=400）原样透传上游格式（不翻译错误体，文档说明）。这些是已知边界，后续 issue 再做。

**实现点**：新建 `translator.py`（请求/响应/流式转换 + SSE 解析器）；`server.py` 加 `_forward_translated`/`_passthrough_translated`/`_stream_translated`，路由层把「协议不匹配 400」改为「翻译转发」；同协议路径不变。

## 结论（2026-08-09）

已实现 **双向 OpenAI ↔ Anthropic 格式转换**，独立 `translator.py` 模块。状态：**关闭**（核心 text chat 双向 + 流式已覆盖；工具/多模态为已知边界，后续再做）。

实现：
- `translator.py` 纯函数模块：请求转换（`openai_to_anthropic_request` / `anthropic_to_openai_request`：system 提升顶层、max_tokens 必填默认 1024、temperature/top_p、stop↔stop_sequences）、非流响应转换（content/finish_reason↔stop_reason/usage 双向）、流式转换（`anthropic_stream_to_openai` / `openai_stream_to_anthropic`，增量 UTF-8 解码 + `\n\n` 分帧的 SSE 解析器，逐事件重编码）。
- `server.py`：`_forward_translated` 编排（翻译请求→对应 handler forward/open_stream→翻译响应），`_passthrough_translated`（非流），`_stream_translated`（流式，2xx 逐事件转换，>=400 原样透传）。`openai_chat` / `anthropic_messages` 把「协议不匹配 400」改为翻译转发；同协议仍走原直通路径（零开销零回归）。

**附带修复**：`handler_base.forward` 用了已废弃的 `allow_redirects=False`（httpx ≥0.28 改名 `follow_redirects`），真实非流转发会 TypeError。此前所有测试都 mock forward 未暴露。改为 `follow_redirects=False`。（同问题在 `open_stream` 已用新 API，仅 `forward` 漏改。）

测试：`tests/test_translator.py` 27 个用例（请求/响应/流式单元 + 路由集成：OpenAI客户端→Anthropic供应商、反向、同协议不转换、流式 SSE 双向、分块不破坏解析）。更新 `test_integration.py`（mismatch 现在翻译而非 400）、`test_handler_base.py`（follow_redirects 断言）。全量 **338 passed**，覆盖率 **87.5%**，零回归。

已知边界（文档化，后续 issue）：
- 工具调用（tools/tool_calls/tool_use）、多模态（image）跨格式不翻译，请求中这类字段会被丢弃。
- 上游错误响应（>=400）原样透传上游格式（不翻译错误体）。
- `openai_catchall`（embeddings 等 OpenAI 专有端点）不做跨格式翻译（仍要求 openai 供应商）。
