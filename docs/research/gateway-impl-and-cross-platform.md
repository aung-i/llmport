# Gateway 实现方式与多系统兼容

> 聚焦两个问题:(1) gateway 具体怎么实现(进程模型 / HTTP server / 请求处理);(2) 怎么兼容 Windows 与 Unix。前几篇偏路由规则,这篇补实现与跨平台。

## A. Gateway 实现方式

### A.1 CCR(Node.js,supervisor-worker 进程模型)

**两个 HTTP 端口、两个进程:**

| 角色 | 进程 | 端口 | 代码 |
| --- | --- | --- | --- |
| 管理进程(web UI + RPC) | 父进程 | `3458` | `packages/core/src/web/management-server.ts` |
| 模型网关(agent 接入) | 子进程(spawn) | `3456` | `gateway-bootstrap.ts` + `gateway/http/request-handler.ts` + `gateway/request/pipeline.ts` |

**进程模型(`gateway/core-runtime/supervisor.ts` + `gateway-bootstrap.ts`):**

- 父进程 `spawn` 一个子 Node 进程,`stdio: ["ignore","pipe","pipe","ipc"]`,`serialization: "advanced"`。
- **config 走 IPC,不落盘**:父进程 `child.send({type:"gateway:start", config, gatewayEntry})`;子进程收到后 `installVirtualConfigFile()` -- **monkeypatch `fs.existsSync/readFileSync/writeFileSync/renameSync`**,让"读 config 路径"的遗留代码读到内存里的 config,同时禁止写回(config 由 CCR 管理)。然后 `require(gatewayEntry)` 启动真正的 HTTP server,回 `process.send({type:"gateway:config-accepted"})`。
- **supervisor 监控**:config-accept 超时 5s、startup 超时 15s、捕获子进程 stdout/stderr(上限 4KB)、监听 exit/error。失败时 `child.kill()`。
- **优雅关闭**(`entrypoints/server.ts`):`SIGINT`/`SIGTERM` -> `runtime.close()` -> `process.exit(130/143)`。`process.once("disconnect", () => process.exit(0))`(父进程退出时子进程自尽)。

**HTTP server(Node `http`):**

- `GatewayHttpRequestHandler.handleRequest` 是路径分发器:CORS -> OPTIONS -> billing-usage-sync -> raw-trace-sync -> remote-control -> browser-automation MCP -> context-archive MCP -> media MCP -> `proxyRequest`(模型转发)。
- `GatewayRequestPipeline.proxyRequest` 是单请求流水线(header 规范化 -> cursor 兼容 -> claude 模型发现 -> 路由决策 -> 协议桥 -> `fetchUpstreamWithFallback` -> 响应改写),详见 [claude-code-router.md](./claude-code-router.md)。

**要点**:CCR 把"管理面"和"数据面"拆成两个进程,config 经 IPC 传递不落盘,supervisor 监控子进程生命周期。这是桌面 app 场景的需要(UI 崩了不杀网关、网关崩了 supervisor 重启)。

### A.2 Hermes(Python,FastAPI + uvicorn + httpx)

**栈与 llmport 完全同源**(`pyproject.toml`):`fastapi>=0.104,<1` + `uvicorn[standard]>=0.24,<1` + `python-multipart` + `httpx[socks]==0.28.1`。

**三个"gateway"入口,实现各异:**

1. **消息网关 `gateway/run.py`**:asyncio 长驻进程,把 agent 接到 IM 平台。
   - 启动顺序:`hermes_bootstrap` 必须最先导入("UTF-8 stdio on Windows,POSIX no-op")-> `faulthandler`(崩溃时 dump 线程栈)-> `signal` -> `interrupt_compat`。
   - 平台 adapter outbound 连接(Telegram 冷轮询要先证一次 `getUpdates` 往返再宣告就绪)+ `delivery_ledger`(可靠投递)+ `session` 状态机 + `stream_dispatch`(流式回复分发)+ `scale_to_zero`(空闲缩容)。
   - **长驻进程的内存治理**:`_AGENT_CACHE_MAX_SIZE=128` + `_AGENT_CACHE_IDLE_TTL_SECS=3600`(LRU + idle TTL 驱逐)+ `agent_cache_pressure.py`(内存压力阀)。这是 llmport 不需要的(llmport 无状态 per-request)。

2. **ACP adapter `acp_adapter/server.py`**:FastAPI server,把 agent 暴露给 IDE(Cursor/Zed 等)走 Agent Client Protocol。

3. **LLM provider 层**:`agent/*_adapter.py`(anthropic/bedrock/gemini/codex/...)+ httpx 客户端 + 凭据池/限流/重试。不是独立 HTTP server,是 agent 内部的调用层。

**进程级 bootstrap(`agent/process_bootstrap.py`):**

- **lazy OpenAI SDK import**:延迟 240ms 的 `from openai import OpenAI`,用 `_OpenAIProxy` 保持 `isinstance` 和 `patch` 兼容。
- **crash-resistant stdio(`_SafeWriter`)**:包 stdout/stderr,吞 `OSError: Input/output error`(systemd/Docker/线程 teardown 的 broken pipe)+ `ValueError: I/O operation on closed file`。-- 长驻 daemon 的真实痛点。
- **HTTP proxy 解析**:读 `HTTPS_PROXY`/`HTTP_PROXY`/`ALL_PROXY` + `NO_PROXY`。

### A.3 llmport 现状对照

| 维度 | llmport | CCR | Hermes |
| --- | --- | --- | --- |
| HTTP 栈 | Starlette + uvicorn | Node `http` | FastAPI + uvicorn |
| 上游客户端 | httpx | node fetch | httpx |
| 进程模型 | 单进程 daemon | supervisor-worker(两进程) | 单进程 + 可选子 agent |
| 请求处理 | server.py 内联 `_forward/_passthrough/_stream` | Handler 类 + Pipeline 类 | adapter + conversation_loop |
| 状态 | 无状态 per-request | 无状态 per-request(日志落 SQLite) | 有状态(会话/缓存) |

llmport 与 **Hermes 同栈同形**(Starlette ≈ FastAPI 底层),比 CCR 简单。llmport 不需要 CCR 的 supervisor-worker(没有独立管理面),也不需要 Hermes 的会话治理(无状态)。llmport 的 daemon 已有 PID 文件 + uvicorn 进程,进程模型已够用。

---

## B. 多系统兼容

### B.1 CCR 的做法(`packages/core/src/platform/`)

统一模式:**`process.platform !== "win32"` 守卫,Unix 上 no-op**;Windows 下用 PowerShell + 绝对路径解析。

| 文件 | 做什么 |
| --- | --- |
| `windows-system.ts` | `windowsSystemCommand(cmd)`:把裸 `powershell.exe` 解析成 `SystemRoot/System32/...` 或 `Sysnative/...` 绝对路径(服务上下文里裸命令解析不到;Sysnative 绕开 32/64 位重定向)。`broadcastWindowsEnvironmentChanged()`:PowerShell + `SendMessageTimeout(HWND_BROADCAST, WM_SETTINGCHANGE)` 广播环境变量变更,让新进程读到新 env。`spawnSync` 带 `windowsHide: true`。 |
| `windows-app-discovery.ts` | 多策略发现桌面 app:install roots(Program Files / WindowsApps)+ 执行别名 + **`.lnk` 快捷方式目标解析(PowerShell + `WScript.Shell` COM)** + MSIX 包 + `where` 命令。 |
| `socket-compat.ts` | `installSocketTypeOfServiceCompat()`:monkeypatch `Socket.prototype.setTypeOfService`,吞 `EINVAL`(Linux 允许设 IP_TOS,部分平台/Windows 拒绝)。启动时安装。 |
| `config/constants.ts` | `CONFIGDIR`:`~/.claude-code-router/`(Unix)vs `%APPDATA%\claude-code-router\`(Windows)。 |

信号:`SIGINT`/`SIGTERM` 由 Node 跨平台处理(Windows 上 Ctrl+C -> SIGINT);没有依赖 `os.killpg`/`start_new_session` 这类 POSIX-only 原语做进程管理(它用 IPC `disconnect` + `child.kill()`)。

### B.2 Hermes 的做法(关键,直接回答 llmport 的待定决策)

**`psutil==7.2.2` 是核心依赖**,`pyproject.toml` 里带明确注释:

> Cross-platform process / PID management. `psutil` is the canonical answer for "is this PID alive" and process-tree walking across Linux, macOS and Windows. It replaces POSIX-only idioms like `os.kill(pid, 0)` (which is a silent killer on Windows - see CONTRIBUTING.md) and `os.killpg` (which doesn't exist on Windows).

**这句话几乎逐字命中 llmport 评估里指出的 Windows 差距。** Hermes 的选择:不模拟 POSIX,引入 psutil 统一接口。

其他跨平台措施:

| 措施 | 说明 |
| --- | --- |
| `tzdata==2025.3; sys_platform == 'win32'` | **平台条件依赖**:Windows 无 IANA tzdata,`zoneinfo` 会 `ZoneInfoNotFoundError`;装 `tzdata` 包补齐。Linux/macOS no-op(有 `/usr/share/zoneinfo`)。 |
| `hermes_bootstrap.py` 最先导入 | UTF-8 stdio on Windows(Python 在 Windows 上 stdout 处理 Unicode 需 `PYTHONUTF8`/`sys.stdout.reconfigure`)。POSIX no-op。 |
| bundled Git Bash(MinGit) | 解压到 `%LOCALAPPDATA%\hermes\git`,免管理员、与系统 Git 隔离;跑 shell 命令用。系统有 Git 则优先用系统的。 |
| `_SafeWriter` | 包 stdout/stderr 吞 broken pipe(systemd/Docker/线程 teardown)。 |
| `interrupt_compat.request_hard_interrupt` | 跨平台中断(Ctrl+C 在 Windows vs Unix 语义不同)。 |
| 依赖精确 pin(`==X.Y.Z`) | provider 专用依赖(`anthropic`/`firecrawl`/`fal-client`)放 extras + lazy install,缩小供应链爆炸半径(回应过 PyPI 投毒事件)。 |

### B.3 对 llmport Windows 抽象的直接结论

**之前悬而未决的"psutil vs 标准库"现在有先例了**:Hermes(同类 Python agent 项目)选了 psutil 作核心依赖,理由和 llmport 面临的问题一模一样。**推荐方案 A:引入 psutil。**

llmport 评估里的四个 Windows 差距,对应方案:

| 差距 | 现状(Unix-only) | 抽象后(psutil + subprocess 原生) |
| --- | --- | --- |
| `os.kill(pid, SIGTERM/SIGKILL)` | daemon.py `stop()` | `psutil.Process(pid).terminate()`(Windows 上=TerminateProcess,但接口统一)/ `.kill()`;`.is_running()` 替代 `os.kill(pid,0)` 存活检查 |
| `ps -p pid -o command=` 读 cmdline | daemon.py `_process_cmdline` | `psutil.Process(pid).cmdline()`(跨平台,不依赖 ps/PowerShell) |
| `chmod 0600/0700` 被忽略 | store.py `_chmod` | 现有 `_chmod` 已 `try/except (OSError, AttributeError)` 吞错;Windows 上靠目录权限(0700 目录)+ ACL,保持现状 |
| `start_new_session=True` 被忽略 | daemon.py `start()` Popen | `subprocess.Popen(..., start_new_session=True)`(POSIX)/ `creationflags=CREATE_NEW_PROCESS_GROUP`(Windows)--subprocess 原生支持,平台分支一行 |

**额外可借鉴**(按需):Hermes 的 `hermes_bootstrap` UTF-8 stdio -- llmport daemon 当前重定向到 DEVNULL,影响小;若将来日志走 stdout,Windows 上需要类似处理。

**抽象落点建议**:

- 新建 `src/llmport/platform/process.py`:统一 `is_alive(pid)` / `cmdline(pid)` / `terminate(pid, graceful=True)` / `spawn(cmd, ...)` 四个函数,内部按 `sys.platform` 分支或直接用 psutil。
- `daemon.py` 的 `_process_cmdline` / `_pid_is_our_daemon` / `stop` / `_wait_for_exit` / `start` 改调这四个函数。
- `store.py` 的 `_chmod` 保持现状(已兼容)。
- `pyproject.toml` 加 `psutil` 依赖。
- 测试:`tests/test_daemon.py` 现在 patch `os.kill`/`dm._process_cmdline`/`dm._wait_for_exit`/`subprocess.Popen`,抽象后改成 patch `llmport.platform.process` 的对应函数(断言点不变,只是 patch 目标迁移)。

这样 llmport 的进程层就和 Hermes 走在同一条路上,而不是自己用 PowerShell 重新发明一遍 cmdline 读取。
