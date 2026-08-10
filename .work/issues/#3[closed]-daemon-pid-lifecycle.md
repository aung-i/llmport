# 3. daemon.pid 是什么、用途与异常处理

- 状态：closed（2026-08-09 pid 生命周期加固完成，四个异常分支全覆盖）
- 提出时间：2026-08-09

## 问题

- `~/.config/llmport/daemon.pid` 是什么？有什么用？
- 什么时候生成？
- 如果有人手动删除该文件，或者 kill 掉进程，会不会影响我们的逻辑？
- 各种异常情况都考虑了吗：
  - pid 文件丢失但进程还在跑
  - pid 文件还在但对应进程已死（残留旧 pid）
  - pid 被别的无关进程占用（pid 复用）
  - 多次启动 / 重复 start

## 待解决

- 先调研当前 daemon.py 对 pid 文件的读写逻辑，回答上述问题。
- 识别没覆盖的异常分支，补齐处理（删除残留、校验 pid 存活、start 前检查等）。

## 调研结论（2026-08-09）

`daemon.pid` 是一个 JSON 文件 `{pid, started_at, port}`：

- **写**：`run_daemon()` 在 uvicorn 启动**前**写入（记录自己 pid、启动时刻、监听端口）；进程退出时在 `finally` 里删除。
- **读**：CLI 侧 `DaemonManager` 读取它来判断 daemon 是否在跑（`is_running`）、监听端口（`get_control_port`/`_gateway_port`）、启动时间（`started_at`）；`stop` 用其中的 pid 发信号。
- **生成时机**：daemon 进程进入 `run_daemon()` 时生成；正常退出（走 finally）时删除。

### 手动删除 / kill 的影响（修复前）

- 手动删除 pid 文件：`is_running` 读不到 pid 返回 False → `start` 误以为没在跑，尝试再起一个 → 端口被原 daemon 占用 → 新进程 bind 失败；`stop` 读不到 pid → 无法停止仍在跑的真正 daemon（形成**孤儿**）。
- `kill` 掉进程（未走 finally）：pid 文件**残留** → `is_running` 用 `os.kill(pid,0)` 探测到已死返回 False（判断正确），但**不清理**残留文件；`stop` 会向残留 pid 发 SIGTERM/SIGKILL——若该 pid 已被**无关进程复用**，则**误杀无关进程**。

### 异常分支覆盖情况（修复前）

| 分支 | 修复前行为 | 是否覆盖 |
|---|---|---|
| pid 文件丢失但进程在跑（孤儿） | `is_running` False；`start` double-spawn 失败；`stop` 无法停止 | ❌ 未处理 |
| pid 残留但进程已死（stale） | `is_running` False 但不清理残留文件；`stop` 向死 pid 发信号（无害但脏） | ⚠️ 部分 |
| pid 被无关进程占用（pid 复用） | `is_running` 误判为在跑；`stop` **SIGTERM/SIGKILL 无关进程（危险）** | ❌ 未处理 |
| 多次启动 / 重复 start | `start` 有 `is_running` 守卫，但依赖上述误判 | ⚠️ 依赖误判 |

## 方案

1. **进程身份校验** `_process_cmdline(pid)` + `_pid_is_our_daemon(pid)`：`os.kill(pid,0)` 存活 **且** 用 `ps -p PID -o command=` 校验命令行同时含 `llmport` 与 `--daemon`（daemon 恒以 `python -m llmport --daemon` 启动）。`ps` 不可用时回退到只信 `os.kill`（不低于旧行为）。
2. **`is_running()`**：pid 不是我们的 daemon（已死 / 被复用）→ **清理残留 pid 文件** + 返回 False。覆盖 stale 与复用。
3. **`stop()`**：发信号前先 `_pid_is_our_daemon` 校验；不是我们的 → 只清理 pid 文件、**不发信号**（避免误杀无关进程）。覆盖复用安全。
4. **`start()`**：`is_running` 为 False 后，先 `_port_answers_health(port)` 探测；若有进程在端口上应答 `/health` 但我们无有效 pid（孤儿）→ 提示用户并返回 False，**不 double-spawn**。覆盖孤儿。
5. **重复 start**：`is_running` 身份校验可靠后，守卫自然正确。

## 结论（2026-08-09）

已在 `src/llmport/daemon.py` 实现上述方案，四个异常分支全部覆盖：

- **stale（残留死 pid）**：`is_running` / `stop` 检测到 pid 已死 -> 清理 pid 文件，`stop` 不发信号。
- **pid 复用（无关进程占用）**：`_pid_is_our_daemon` 用 `ps` 校验命令行含 `llmport` + `--daemon`；不匹配 -> `is_running` 视为未运行并清理，`stop` **拒绝发信号**（绝不误杀无关进程）。`ps` 不可用时回退到只信 `os.kill`（不低于旧行为）。
- **孤儿（pid 文件丢失但进程在跑）**：`start` 在 `is_running` 为 False 后，先用 `_port_answers_health` 探测端口；若已有进程应答 `/health` 则提示用户并返回 False，**不 double-spawn**。
- **重复 start**：`is_running` 经身份校验后可靠，`start` 守卫正确拦截重复启动。

新增/调整测试（`tests/test_daemon.py` + `tests/test_integration.py`）：
- `_pid_is_our_daemon`：死 pid / ps 不可用回退 / 本进程 / 无关进程 四分支。
- `_port_answers_health`：200 / 非 200 / 连接异常。
- `is_running`：死 pid 清理残留、复用 pid 清理残留。
- `stop`：本 daemon 走 SIGTERM、复用 pid 不发信号。
- `start`：孤儿端口不 spawn。

全套测试通过：348 passed，覆盖率 87.10%（≥85% 门槛）。issue 关闭。
