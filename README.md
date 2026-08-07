# llmport

Terminal LLM API Gateway - 本地常驻的多供应商 LLM 路由网关。

## 定位

在你的机器上常驻运行。所有工具（IDE 插件、CLI、脚本）指向同一个本地 URL，网关按客户端请求里的 `model` 名路由到对应供应商，并按优先级故障 fallback。

- 客户端在请求 body 里指定 `model`，网关查表路由（没有"激活模型"开关）
- 配置按职责分两个文件：`providers.yaml`（供应商 + gateway + API key，自包含）、`models.yaml`（模型映射）
- 按优先级 fallback
- 多协议透明转发（OpenAI / Anthropic Messages）
- 单端口，仅监听回环地址

## 安装

```bash
uv tool install llmport
```

## 命令

```bash
llmport setup            # 初始化配置目录 + 模板（指路到 provider/model add）
llmport config init      # 生成带注释的配置模板（providers.yaml + models.yaml）
llmport config show      # 打印配置（api_key 打码）+ key 状态
llmport config edit [-t providers|models]  # 用 $EDITOR 打开配置文件
llmport config path      # 打印配置文件路径
llmport provider add     # 添加/更新供应商（base_url + API key 一起存 providers.yaml）
llmport provider list    # 列出供应商
llmport provider test <id>  # 测试供应商连通性（只读 providers.yaml，不需网关运行）
llmport provider remove <id>
llmport model add        # 添加/更新模型映射
llmport model list
llmport model remove <name>
llmport start [--host H --port P]   # 启动网关（需先配置供应商）
llmport stop             # 停止网关
llmport status           # 查看运行状态 / 路由 / 模型 / 统计
llmport restart [--host H --port P] # 重启
llmport                  # 打印帮助
```

首次使用先 `llmport setup`（初始化配置目录）或直接 `llmport provider add`。添加供应商时 `base_url` 和 API key 一起明文写入 `providers.yaml`（0600）；没配置供应商时 `start` 会拒绝并提示先配置。

```bash
# 示例：加供应商（key 不回显，推荐不传 --api-key 交互输入）
llmport provider add --name anthropic --protocol anthropic
llmport provider test anthropic     # 验证连通，OpenAI 还会列出可用模型
llmport model add --name claude-sonnet --provider anthropic --upstream claude-sonnet-4
llmport start
```

## 配置

配置在 `~/.config/llmport/`：

```
providers.yaml   # 供应商 + gateway + API key（0600，自包含）
models.yaml      # 模型映射：公开名 -> 供应商（0600）
```

`providers.yaml` 示例：

```yaml
version: 1
gateway:
  host: 127.0.0.1
  port: 11434

# 供应商：base_url + API key 放一起，自包含。
# name 是供应商标识（模型映射里用此名引用）。
# base_url 填主机根即可，/v1 由网关自动补。
providers:
  - name: anthropic
    protocol: anthropic
    base_url: https://api.anthropic.com
    api_key: sk-ant-xxxxx
  - name: openai
    protocol: openai
    base_url: https://api.openai.com
    api_key: sk-xxxxx
```

`models.yaml` 示例：

```yaml
# 公开名 -> 供应商。upstream 缺省 = 公开名；多供应商/多 upstream 按顺序 fallback。
models:
  claude-sonnet: anthropic                 # 无别名单供应商
  gpt-4o:                                   # 无别名多供应商（顺序=优先级）
    - openai
    - azure
  sonnet:                                   # 有别名：供应商后接真实模型名
    - anthropic: claude-sonnet-4
  gpt4:                                     # 供应商后接列表（依次 fallback）
    - openai: gpt-4
    - azure: [gpt4o-deploy, gpt4o-turbo]
```

**gateway 监听地址优先级**（高 -> 低）：`llmport start --host/--port` > `providers.yaml` 的 `gateway:` 段 > 默认（`127.0.0.1:11434`）。无环境变量层。

## 用法

```bash
llmport setup     # 初始化配置目录
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
- `/api/status` -> 只读运行态；`/api/daemon/{stop,restart}` -> 生命周期

控制 API 只保留只读 status + 生命周期。供应商/模型/网关配置统一走 CLI（写 `providers.yaml` + `models.yaml`，守护进程在运行则自动重启生效），不再暴露运行时写接口--避免程序化注入任意 `base_url` 的 SSRF 面。

`base_url` 校验：CLI 写盘时拦截云元数据/链路本地地址（`169.254.0.0/16`、`100.100.100.200`、`metadata.google.internal` 等）和指向网关自身的自环地址；放行 localhost/private，本地 Ollama / vLLM 照常可用。手编 `providers.yaml` 里的不合规 base_url 会在 `llmport start` 时告警，并在运行时将该 provider 标记为 down 跳过。网关始终只绑回环地址（`0.0.0.0` 等非回环 host 一律强制为 `127.0.0.1`）。

## 技术栈

Python 3.11+ · Starlette/uvicorn · httpx · Textual · uv
