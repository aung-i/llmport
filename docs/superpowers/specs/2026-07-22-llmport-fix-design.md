# llmport Fix Design — 2026-07-22

**Status:** Approved
**Scope:** Crash fixes, missing v1 features
**Based on:** `2026-07-21-llmport-design.md` spec + three exploration reports

---

## Priority 1 (P0) — Crash Bugs

### Bug #1: Config format migration is order-dependent

**Root cause:** `GatewayState.reload()` has migration code for old `{openai_port, anthropic_port}` → new `{host, port}`, but this ONLY runs inside `GatewayState`. `run_daemon()` and TUI screens read raw config that may have old format → `KeyError: 'host'`.

**Fix:** Extract shared `_migrate_gateway_config(data)` in `server.py`, use everywhere.

### Bug #2: run_daemon port reading ignores migration

Same root cause — `run_daemon()` reads `store.load()` directly, bypassing migration.

**Fix:** Use the same `_migrate_gateway_config()`.

---

## Priority 2 (P1) — Missing v1 Features

### #3: Model search on Models tab
- Add `Input(placeholder="搜索模型...")` in `models.py` compose
- Add `on_input_changed` → filter `self.models` → re-render `ListView`

### #4: Provider delete
- Add `DELETE` handler in `server.py` control_providers
- Add "删除供应商" button in `providers.py` with confirmation

### #7: /api/daemon/restart endpoint
- Add `control_daemon_restart()` + route in `server.py`

### #8: Onboarding completion flow
- Rewrite `onboarding.py` as 3-step wizard: generate key → add provider → finish
- After onboarding, focus Models tab in `app.py`

### Additional fixes
- `gateway.py`: guard empty/malformed config dict in `refresh_status()`
- `app.py`: replace hardcoded `v0.1.0` with `importlib.metadata.version`

---

## Priority 3 (P2) — Deferred

- Model detail editable (bind/unbind, priority, routing strategy)
- `/api/models` endpoint
- Export/import real implementation
- Per-model/per-provider stats
- Tab order (Models first)

---

## Implementation Checklist

### Phase 1: P0
- [ ] `server.py`: Define `_migrate_gateway_config(data)` shared function
- [ ] `server.py`: Update `GatewayState.reload()` to use it
- [ ] `server.py`: Update `run_daemon()` to use it
- [ ] `gateway.py`: Fix `refresh_status()` config dict None-check

### Phase 2: P1
- [ ] `models.py`: Add search Input + filter handler
- [ ] `providers.py`: Add delete button + DELETE API call
- [ ] `server.py`: Add DELETE to `/api/providers`
- [ ] `server.py`: Add `/api/daemon/restart` endpoint
- [ ] `onboarding.py`: 3-step wizard rewrite
- [ ] `app.py`: Dynamic version + post-onboarding tab focus

### Phase 3: Tests (P0 — must pass before commit)

**T1** `test_gateway_state_reload_migrates_old_format` — 写入旧格式 `{openai_port: 11435}`，调用 reload，验证迁移后 `state.gateway == {"host": "127.0.0.1", "port": 11435}`，并确认已写回磁盘新格式。

**T2** `test_gateway_state_reload_handles_missing_gateway_key` — config 中无 `gateway` 键，验证 reload 后使用默认值 `{"host": "127.0.0.1", "port": 11434}`。

**T3** `test_config_migration_empty_gateway` — gateway 为空 dict，验证默认值。

**T4** `test_control_test_provider_endpoint` — POST 到 `/api/providers/test`，验证 200 + `ok` + `latency_ms`。

**T5** `test_control_fetch_models_endpoint` — POST 到 `/api/providers/models`，验证返回 `models` 列表。

**T6** `test_provider_delete` — 创建 provider → DELETE → 验证列表为空。

**T7** `test_daemon_restart_endpoint` — POST 到 `/api/daemon/restart` → 验证 200 + `{"ok": True}`。

**T8** `test_gateway_state_save_roundtrip` — 修改 state 字段 → save → 新 GatewayState 加载 → 验证一致性。

#### Phase 4: Tests (P1 — nice to have, follow-up)

**T9** `test_parse_models_utility` — 测试 `_parse_models()`: 正常输入、"空字符串、多行、空白处理。

**T10** `test_openai_handler_list_models` — mock httpx → 测试成功/错误返回。

**T11** `test_openai_handler_forward_error_branches` — mock TimeoutException / ConnectError。

**T12** `test_anthropic_handler_test_connection` — mock POST → 验证 `(ok, latency, error)` 返回。

**T13** `test_daemon_manager_is_running_pid_file` — 写/删 PID 文件 → 验证 is_running()。

**T14** `test_openai_catchall_endpoint` — GET 到 `/openai/v1/embeddings` → 验证转发。
