"""Providers tab — manage LLM provider configurations."""

from typing import TYPE_CHECKING, cast

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal, Container
from textual.widgets import Static, ListView, ListItem, Label, Button, Input, Select
from textual.screen import ModalScreen
import httpx

from llmport.daemon import DaemonManager

if TYPE_CHECKING:
    from llmport.app import LlmPortApp
from llmport.models.parser import parse_models
from llmport.ui import async_get_json
from llmport.ui.widgets import Section


class ProviderFormScreen(ModalScreen):
    """Modal for adding or editing a provider."""

    CSS = """
    ProviderFormScreen {
        align: center middle;
    }
    #form-container {
        width: 64;
        height: auto;
        min-height: 28;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    Input {
        margin: 1 0;
        width: 100%;
    }
    Select {
        margin: 1 0;
        width: 100%;
    }
    """

    def __init__(self, daemon: DaemonManager, provider: dict | None = None):
        super().__init__()
        self.daemon = daemon
        self.provider = provider
        self.is_edit = provider is not None
        self._key_cleared = False  # set True when user clicks "清空"

    def compose(self) -> ComposeResult:
        with Container(id="form-container"):
            title = f"编辑供应商: {self.provider['name']}" if self.is_edit else "添加供应商"
            yield Static(f"[bold $primary]{title}[/]")
            yield Label("")
            yield Label("[dim]名称[/]")
            yield Input(
                value=self.provider.get("name", "") if self.provider else "",
                placeholder="Anthropic",
                id="input-name",
            )
            yield Label("[dim]API Key[/]")
            with Horizontal():
                yield Input(
                    value="",
                    placeholder="保留原值，留空则不修改" if self.is_edit else "sk-ant-api03-...",
                    password=True,
                    id="input-key",
                )
                yield Button(" 清空", id="btn-clear-key", variant="error")
            yield Label("[dim]地址[/]")
            yield Input(
                value=self.provider.get("base_url", "") if self.provider else "",
                placeholder="https://api.anthropic.com",
                id="input-url",
            )
            yield Label("[dim]协议[/]")
            current = self.provider.get("protocol", "openai") if self.provider else "openai"
            yield Select(
                [(p.title(), p) for p in ["openai", "anthropic"]],
                value=current,
                id="select-protocol",
            )
            yield Label("[dim]模型 (每行: 模型名,别名1,别名2)[/]")
            model_text = ""
            if self.provider:
                lines = []
                for m in self.provider.get("models", []):
                    parts = [m["name"]] + m.get("aliases", [])
                    lines.append(",".join(parts))
                model_text = "\n".join(lines)
            yield Input(
                value=model_text,
                placeholder="claude-opus-4-8,claude-opus,opus",
                id="input-models",
            )
            yield Label("")
            with Horizontal():
                yield Button(" 测试连接", id="btn-test", variant="default")
                yield Button(" 拉取模型", id="btn-fetch", variant="default")
                yield Button(" 保存", id="btn-save", variant="primary")
                yield Button(" 取消", id="btn-cancel")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.dismiss()
            return

        if event.button.id == "btn-clear-key":
            self.query_one("#input-key", Input).value = ""
            self._key_cleared = True
            self.notify("API Key 已清空 — 保存后将清除密钥", title="API Key")
            return

        name = self.query_one("#input-name", Input).value
        raw_key = self.query_one("#input-key", Input).value
        # When editing and key field is empty:
        #   - if user clicked "清空" → send "" to clear the key
        #   - otherwise → send "***" to keep existing key
        if self._key_cleared:
            key = ""
        elif raw_key:
            key = raw_key
        else:
            key = "***" if self.is_edit else ""
        url = self.query_one("#input-url", Input).value
        protocol = self.query_one("#select-protocol", Select).value
        models_raw = self.query_one("#input-models", Input).value

        port = self.daemon.get_control_port()
        if port is None:
            self.notify("网关未运行，请先启动网关", title="错误", severity="error")
            return

        if event.button.id == "btn-test":
            models = parse_models(models_raw)
            body = {
                "id": name.lower().replace(" ", "-"),
                "name": name,
                "protocol": protocol,
                "base_url": url,
                "api_key": key,
                "models": [{"name": m["name"], "aliases": m["aliases"]} for m in models],
            }
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"http://127.0.0.1:{port}/api/providers/test",
                    json=body,
                )
                result = resp.json()
                if result.get("ok"):
                    self.notify(f"连接成功 · {result.get('latency_ms', 0):.0f}ms", title="测试结果")
                else:
                    self.notify(f"失败: {result.get('error', '未知')}", title="测试结果", severity="error")
            return

        if event.button.id == "btn-fetch":
            provider_id = name.lower().replace(" ", "-")
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"http://127.0.0.1:{port}/api/providers/models",
                    json={
                        "id": provider_id,
                        "name": name,
                        "protocol": protocol,
                        "base_url": url,
                        "api_key": key,
                        "models": [],
                    },
                )
                result = resp.json()
                models_data = result.get("models")
                if models_data:
                    names = [m.get("id", "") for m in models_data]
                    self.query_one("#input-models", Input).value = "\n".join(names[:50])
                    self.notify(f"找到 {len(models_data)} 个模型", title="模型列表")
                else:
                    self.notify(f"获取失败: {result.get('error', '')}", title="模型列表", severity="error")
            return

        if event.button.id == "btn-save":
            models = parse_models(models_raw)
            body = {
                "id": name.lower().replace(" ", "-"),
                "name": name,
                "protocol": protocol,
                "base_url": url,
                "api_key": key,
                "models": [{"name": m["name"], "aliases": m["aliases"]} for m in models],
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"http://127.0.0.1:{port}/api/providers",
                    json=body,
                )
                if resp.status_code != 200:
                    self.notify(f"保存失败: {resp.text}", title="供应商", severity="error")
                    return
            self.notify(f"已保存: {name}", title="供应商")
            self.dismiss()
            await cast("LlmPortApp", self.app).query_one(ProvidersPane).refresh_providers()


class ProvidersPane(Vertical):
    """Provider management panel."""

    def __init__(self):
        super().__init__()
        self.providers: list[dict] = []

    def compose(self) -> ComposeResult:
        with Section("供应商列表"):
            yield ListView(id="provider-list")
        with Horizontal(id="provider-actions"):
            yield Button(" 添加供应商", id="btn-add-provider", variant="primary")
            yield Button(" 删除供应商", id="btn-delete-provider", variant="error")

    async def on_mount(self) -> None:
        self.set_interval(10.0, self.refresh_providers)
        await self.refresh_providers()

    async def refresh_providers(self) -> None:
        daemon = cast("LlmPortApp", self.app).daemon
        port = daemon.get_control_port()
        if port is None:
            list_view = self.query_one("#provider-list", ListView)
            await list_view.clear()
            list_view.append(ListItem(Label("[dim]网关未运行，请先在网关页启动[/]")))
            return
        providers = await async_get_json(f"http://127.0.0.1:{port}/api/providers") or []
        self.providers = providers
        list_view = self.query_one("#provider-list", ListView)
        await list_view.clear()
        if not providers:
            list_view.append(ListItem(Label("[dim]暂无供应商 — 点击下方按钮添加[/]")))
        else:
            for p in providers:
                icon = {"up": "🟢", "degraded": "🟡", "down": "🔴"}.get(
                    p.get("health", {}).get("status", "unknown"), "⚪"
                )
                model_count = len(p.get("models", []))
                text = f"{icon} [bold]{p['name']}[/] · [dim]{p.get('base_url', '')}[/] · {model_count} 模型"
                list_view.append(ListItem(Label(text)))

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-add-provider":
            _p_app = cast("LlmPortApp", self.app)
            await _p_app.push_screen(
                ProviderFormScreen(_p_app.daemon)
            )
        elif event.button.id == "btn-delete-provider":
            list_view = self.query_one("#provider-list", ListView)
            if list_view.index is not None and list_view.index < len(self.providers):
                provider = self.providers[list_view.index]
                daemon = cast("LlmPortApp", self.app).daemon
                port = daemon.get_control_port()
                if port is None:
                    self.notify("网关未运行，无法删除", title="供应商", severity="error")
                    return
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.delete(
                        f"http://127.0.0.1:{port}/api/providers",
                        json={"id": provider["id"]},
                    )
                    if resp.status_code == 200:
                        self.notify(f"已删除: {provider['name']}", title="供应商")
                    else:
                        self.notify(f"删除失败", title="供应商", severity="error")
                await self.refresh_providers()

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item is None:
            return
        list_view = self.query_one("#provider-list", ListView)
        if list_view.index is not None and list_view.index < len(self.providers):
            provider = self.providers[list_view.index]
            _p_app2 = cast("LlmPortApp", self.app)
            await _p_app2.push_screen(
                ProviderFormScreen(_p_app2.daemon, provider)
            )
