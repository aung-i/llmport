# llmport

Terminal LLM API Gateway - 本地常驻的多供应商 LLM 路由网关。

## 定位

在你的机器上常驻运行。所有工具（IDE 插件、CLI、脚本）指向同一个本地 URL，网关按客户端请求里的 `model` 名路由到对应供应商，并按优先级故障 fallback。

- 客户端在请求 body 里指定 `model`，网关查表路由（没有"激活模型"开关）
- 多供应商 API key 本地加密存储（零依赖，不进 keychain）
- 按优先级 fallback
- 多协议透明转发（OpenAI / Anthropic Messages）
- 单端口，仅监听回环地址

## 安装

```bash
uv tool install llmport
```

## 命令

```bash
llmport setup            # 交互式配置供应商和模型
llmport config init      # 生成带注释的配置模板（直接编辑 config.yaml）
llmport config show      # 打印当前配置 + API key 状态
llmport config edit      # 用 $EDITOR 打开 config.yaml
llmport provider add     # 添加/更新供应商（API key 加密存储）
llmport provider list    # 列出供应商
llmport provider test <id>  # 测试供应商连通性（不需网关运行）
llmport provider remove <id>
llmport model add        # 添加/更新模型映射
llmport model list
llmport model remove <name>
llmport start            # 启动网关（需先配置供应商）
llmport stop             # 停止网关
llmport status           # 查看运行状态 / 路由 / 模型 / 统计
llmport restart          # 重启
llmport                  # 打印帮助
```

首次使用先 `llmport setup`、`llmport config init` 或 `llmport provider add`。添加供应商时 API key 直接加密存入 `secrets.enc`，不写进 `config.yaml`；没配置供应商时 `start` 会拒绝并提示先配置。

```bash
# 示例：加供应商（key 不回显，推荐不传 --api-key 交互输入）
llmport provider add --id anthropic --protocol anthropic
llmport provider test anthropic     # 验证连通，OpenAI 还会列出可用模型
llmport model add --name claude-sonnet --provider anthropic --upstream claude-sonnet-4
llmport start
```

## 配置

配置在 `~/.config/llmport/`：

```
config.yaml    # 可读、可手编辑（网关 / 供应商 / 模型映射）
secrets.enc    # 加密的 API key vault
key            # 加密密钥（0600）
```

`config.yaml` 示例：

```yaml
version: 1
gateway:
  host: 127.0.0.1
  port: 11434

# 供应商：连接信息（api_key 存在 secrets.enc，不写在这里）
# base_url 填主机根即可，/v1 由网关自动补
providers:
  - id: anthropic
    name: Anthropic
    protocol: anthropic
    base_url: https://api.anthropic.com
  - id: openai
    name: OpenAI
    protocol: openai
    base_url: https://api.openai.com

# 模型：公开名 -> 供应商的真实模型名
models:
  - name: claude-sonnet          # 客户端请求时填的 model 名
    provider: anthropic
    upstream: claude-sonnet-4
  - name: gpt-4o
    bindings:                    # 多 binding = fallback 链
      - {provider: openai, upstream: gpt-4o, priority: 1}
      - {provider: azure, upstream: gpt4o-deploy, priority: 2}
```

## 用法

```bash
llmport setup     # 添加供应商和模型（交互式）
llmport start
# 让你的工具指向网关
export OPENAI_BASE_URL=http://127.0.0.1:11434/v1
# 请求里带 model 名，网关按名字路由
curl http://127.0.0.1:11434/v1/chat/completions \
  -H "Authorization: Bearer any" \
  -d '{"model":"claude-sonnet","messages":[{"role":"user","content":"hi"}]}'
```

路由：

- `/openai/v1/*` -> OpenAI 协议
- `/anthropic/v1/*` -> Anthropic 协议
- `/v1/chat/completions`、`/v1/messages` -> SDK 别名
- `/api/*` -> 控制 API

## 技术栈

Python 3.11+ · Starlette/uvicorn · httpx · Textual · uv
