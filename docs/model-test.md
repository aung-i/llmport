# 模型测试 (`llmport model test`)

`llmport model test <name>` 按模型探测,验证该模型绑定的每条上游路径能不能通。**不需要网关在运行** -- 它直接从 `config.yaml` 读模型绑定、从 `providers.yaml` 读供应商 key,走的是网关运行时同一套 handler,所以"测得过"基本等于"真实请求路径通"。

## 为什么按模型,而不是按供应商

供应商本身没有"用哪个模型测"的明确答案,容易靠 `/v1/models` 猜一个模型名 -- 而很多 OpenAI 兼容服务(Ollama / vLLM / LM Studio / 各种代理)在 `/v1/models` 上根本不校验 key,坏 key 照样返回 200,造成"通"的假象。

模型则已经在 `config.yaml` 里声明了自己绑定的 `(provider, upstream)`,upstream 就是上游真实模型名,直接拿来探测即可 -- 不需要 `--model` 参数,不需要硬编码兜底,也不靠列模型接口猜。

## 什么时候用

- 刚 `model add` 加完模型映射,确认这条路径(key + 模型名 + URL)真能跑通
- 配了多绑定 fallback(`gpt-4o: [openai, azure]`),想看哪几条路径健康
- 网关报 502 / 连不上时,绕开网关直接测上游,定位是哪条绑定的问题

## 前提

- 模型已写入 `config.yaml`(`llmport model add` 过)
- 该模型绑定的供应商已在 `providers.yaml` 里,且带了 `api_key`

## 用法

```bash
llmport model test [name]
```

`<name>` 是模型的**公开名**(客户端请求时填的 `model`),不是上游真实模型名。**省略 `<name>` 则测全部模型**,逐条绑定列成一张表,最后给汇总,任一模型完全不可用(零健康绑定)才退出码 1。

```bash
llmport provider add --name anthropic --protocol anthropic --api-key sk-ant-xxx
llmport model add --name claude-sonnet --provider anthropic --upstream claude-sonnet-4
llmport model test claude-sonnet
```

## 测试逻辑

对模型的**每条绑定** `(provider, upstream)` 发一个最小请求,按供应商 `protocol` 选 handler:

- **OpenAI 协议** -- `POST {base_url}/v1/chat/completions`,带 `Authorization: Bearer {api_key}`
- **Anthropic 协议** -- `POST {base_url}/v1/messages`,带 `x-api-key: {api_key}` + `anthropic-version: 2023-06-01`

请求体:

```json
{"model": "<upstream>", "max_tokens": 128, "messages": [{"role": "user", "content": "只回复：有效"}]}
```

提示词直接要模型回 "有效"。`max_tokens` 给 128 而不是更小,是因为**推理模型**(如 DeepSeek-V4)会先把 token 花在 `reasoning_content` 上、最后才吐 `content`——预算太小(比如 16)会全被推理吃光,`content` 为空,看着像"无回复"。128 够 flash(~29 token)/ pro(~82 token) 推理完再回 "有效";实际计费按真正生成的 token 算,不是这个上限。回复原样回显在表格里(超长才截断)。

状态码语义(两条协议一致):

| 状态码 | 含义 | 结论 |
|--------|------|------|
| `< 400` (2xx) | key 有效,模型存在 | ✓ 通,详情列显示回复(应为"有效") |
| `401 / 403` | key 无效 | ✗ key 问题 |
| `404` | upstream 模型名上游不存在 | ✗ 模型名问题(key 多半没问题) |
| 其他 `>= 400` | 上游其他错误 | ✗ 详情列报上游状态码 |

> 404 单独拎出来:它是"模型名填错"而不是"key 坏"。靠状态码区分 401 / 404 / 2xx,所以 **key 能在 upstream 模型名不完全对的情况下也被验证** -- 只要不是 401/403,key 就是有效的。

如果某条绑定的供应商没在 `providers.yaml` 里配置,或没带 `api_key`,该绑定直接判 ✗ 并在详情列写明原因,不影响其他绑定。

## 输出

一张表,每条绑定一行,列:`模型 / 绑定 / 状态 / 延时 / 详情`(详情列成功时是回复,失败时是错误原因)。

单模型、多绑定:

```
$ llmport model test gpt-4o
模型    绑定                状态  延时   详情
-------------------------------------------------------
gpt-4o  openai/gpt-4o       ✓     123ms  有效
gpt-4o  azure/gpt4o-deploy  ✗     -      key 无效 (401)
```

单绑定(Anthropic):

```
$ llmport model test claude-sonnet
模型           绑定                 状态  延时   详情
------------------------------------------------------------
claude-sonnet  anthropic/claude-4   ✓     456ms  有效
```

全部模型(`model test` 不带名) -- 所有绑定合在一张表,末尾给汇总:

```
$ llmport model test
模型               绑定                        状态  延时   详情
---------------------------------------------------------------------------------------------
gpt-4o             openai/gpt-4o               ✓     123ms  有效
gpt-4o             azure/gpt4o-deploy          ✗     -      key 无效 (401)
deepseek-v4-pro    deepseek/deepseek-v4-pro    ✗     -      模型 deepseek-v4-pro 不存在 (404)
deepseek-v4-flash  deepseek/deepseek-v4-flash  ✓     67ms   有效

汇总: 2/3 模型可用
```

**退出码**:**单模型**有一条绑定健康就 0(模型可用,跟 Router 取第一条健康绑定的语义一致),所有绑定都失败才 1;**测全部模型**时任一模型完全不可用(零健康绑定)就 1,全部可用才 0。方便脚本判断 `if llmport model test x; then ...` 或 `if llmport model test; then ...`。

其他情况:

- 模型名打错或没配置 -> 打印已配置的模型名
- 一个模型都没配 -> 提示 `llmport model add`
- 某绑定供应商未配置 -> 详情列显示 `供应商 <provider> 未配置`

## 注意

- **回显"有效"**:提示词让模型只回 "有效",详情列显示这句回复,确认模型真在响应。
- **推理模型友好**:`max_tokens` 128 让推理模型(DeepSeek-V4 等)有预算推理完再回 "有效";OpenAI 协议下若 `content` 为空会回退到 `reasoning_content`,Anthropic 协议下跳过 `thinking` 块取第一个 `text` 块。
- **回复尽力解析**:上游返回非标准结构、详情列无法取到回复时显示 `（无回复）`,不影响 ✓ 判定。
- **测试不经网关**:直接打供应商,用的是绑定的 `upstream` 和该供应商的 `base_url` / `api_key`。
- **多绑定全测**:每条绑定独立探测,fallback 链上每一段都各占一行。
- `base_url` 填主机根即可,`/v1` 由 handler 自动补。

## 源码

- 入口与表格输出:`src/llmport/cli.py` 的 `_model_test` / `_print_test_table` / `_probe_all_models`
- OpenAI 探测:`src/llmport/gateway/openai_handler.py` 的 `test_connection`
- Anthropic 探测:`src/llmport/gateway/anthropic_handler.py` 的 `test_connection`
- 绑定解析:`src/llmport/models/model.py` 的 `parse_models_config`
