"""Providers tab — manage LLM provider configurations."""

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal, Container
from textual.widgets import Static, ListView, ListItem, Label, Button, Input, Select
from textual.screen import ModalScreen
import httpx

from llmgate.daemon import DaemonManager
from llmgate.ui.screens.onboarding import async_get_json


class ProviderFormScreen(ModalScreen):
    """Modal for adding or editing a provider."""

    CSS = """
    ProviderFormScreen {
        align: center middle;
    }
    #form-container {
        width: 70;
        height: 30;
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

    def compose(self) -> ComposeResult:
        with Container(id="form-container"):
            yield Label("编辑供应商" if self.is_edit else "添加供应商")
            yield Label("")
            yield Label("名称")
            yield Input(
                value=self.provider.get("name", "") if self.provider else "",
                placeholder="Anthropic",
                id="input-name",
            )
            yield Label("API Key")
            yield Input(
                value=self.provider.get("api_key", "") if self.provider else "",
                placeholder="sk-ant-api03-...",
                password=True,
                id="input-key",
            )
            yield Label("地址")
            yield Input(
                value=self.provider.get("base_url", "") if self.provider else "",
                placeholder="https://api.anthropic.com",
                id="input-url",
            )
            yield Label("协议")
            current_protocol = self.provider.get("protocol", "openai") if self.provider else "openai"
            yield Select(
                [(p, p.title()) for p in ["openai", "anthropic"]],
                value=current_protocol,
                id="select-protocol",
            )
            yield Label("模型 (每行一个: 模型名,别名1,别名2)")
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
                yield Button("测试连接", id="btn-test", variant="default")
                yield Button("拉取模型列表", id="btn-fetch", variant="default")
                yield Button("保存", id="btn-save", variant="primary")
                yield Button("取消", id="btn-cancel")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.dismiss()
            return

        name = self.query_one("#input-name", Input).value
        key = self.query_one("#input-key", Input).value
        url = self.query_one("#input-url", Input).value
        protocol = self.query_one("#select-protocol", Select).value
        models_raw = self.query_one("#input-models", Input).value

        if event.button.id == "btn-test":
            models = _parse_models(models_raw)
            body = {
                "id": name.lower().replace(" ", "-"),
                "name": name,
                "protocol": protocol,
                "base_url": url,
                "api_key": key,
                "models": [{"name": m["name"], "aliases": m["aliases"]} for m in models],
            }
            port = self.daemon.get_control_port()
            if port:
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
            port = self.daemon.get_control_port()
            if port:
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
            models = _parse_models(models_raw)
            body = {
                "id": name.lower().replace(" ", "-"),
                "name": name,
                "protocol": protocol,
                "base_url": url,
                "api_key": key,
                "models": [{"name": m["name"], "aliases": m["aliases"]} for m in models],
            }
            port = self.daemon.get_control_port()
            if port:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    await client.post(
                        f"http://127.0.0.1:{port}/api/providers",
                        json=body,
                    )
                self.notify(f"已保存: {name}", title="供应商")
                self.dismiss()
                # Refresh parent
                await self.app.query_one(ProvidersPane).refresh_providers()  # type: ignore


def _parse_models(raw: str) -> list[dict]:
    """Parse comma-separated model input into model dicts."""
    models = []
    for line in raw.strip().split("\n"):
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(",") if p.strip()]
        if parts:
            models.append({"name": parts[0], "aliases": parts[1:]})
    return models


class ProvidersPane(Vertical):
    """Provider management panel."""

    def compose(self) -> ComposeResult:
        yield Static("供应商列表", id="providers-title")
        yield ListView(id="provider-list")
        with Horizontal():
            yield Button("添加供应商", id="btn-add-provider", variant="primary")

    async def on_mount(self) -> None:
        await self.refresh_providers()

    async def refresh_providers(self) -> None:
        daemon = self.app.daemon  # type: ignore
        port = daemon.get_control_port()
        if port is None:
            return
        providers = await async_get_json(f"http://127.0.0.1:{port}/api/providers") or []
        list_view = self.query_one("#provider-list", ListView)
        await list_view.clear()
        for p in providers:
            status_icon = {"up": "🟢", "degraded": "🟡", "down": "🔴"}.get(
                p.get("health", {}).get("status", "unknown"), "⚪"
            )
            model_count = len(p.get("models", []))
            text = f"{status_icon} {p['name']} · {p.get('base_url', '')} · {model_count} 模型"
            list_view.append(ListItem(Label(text)))

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-add-provider":
            await self.app.push_screen(  # type: ignore
                ProviderFormScreen(self.app.daemon)  # type: ignore
            )

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item is None:
            return
        daemon = self.app.daemon  # type: ignore
        port = daemon.get_control_port()
        if port is None:
            return
        providers = await async_get_json(f"http://127.0.0.1:{port}/api/providers") or []
        list_view = self.query_one("#provider-list", ListView)
        if list_view.index is not None and list_view.index < len(providers):
            provider = providers[list_view.index]
            await self.app.push_screen(  # type: ignore
                ProviderFormScreen(self.app.daemon, provider)  # type: ignore
            )
