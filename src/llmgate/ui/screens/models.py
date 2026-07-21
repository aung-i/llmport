"""Models tab — the main daily-use screen."""

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal, Container
from textual.widgets import Static, ListView, ListItem, Label, Button
from textual.screen import ModalScreen

from llmgate.daemon import DaemonManager
from llmgate.ui.screens.onboarding import async_get_json


class ModelDetailScreen(ModalScreen):
    """Modal for viewing/editing a model's provider bindings."""

    CSS = """
    ModelDetailScreen {
        align: center middle;
    }
    #detail-container {
        width: 60;
        height: 20;
        border: thick $primary;
        background: $surface;
        padding: 1;
    }
    """

    def __init__(self, model: dict, daemon: DaemonManager):
        super().__init__()
        self.model = model
        self.daemon = daemon

    def compose(self) -> ComposeResult:
        with Container(id="detail-container"):
            yield Label(f"模型: {self.model['id']}")
            yield Label(f"供应商数: {len(self.model.get('bindings', []))}")
            yield Label("")
            yield Label("供应商绑定 (优先级排序):")
            for b in self.model.get("bindings", []):
                yield Label(f"  {b['priority']}. {b['provider_id']} → {b['model_name']}")
            yield Label("")
            yield Label("路由策略: priority_fallback")
            yield Label("")
            with Horizontal():
                yield Button("设为当前", id="set-active", variant="primary")
                yield Button("关闭", id="close")


class ModelsPane(Vertical):
    """Models list with current active model highlighted."""

    def __init__(self):
        super().__init__()
        self.models: list[dict] = []
        self.active_model: str | None = None

    def compose(self) -> ComposeResult:
        yield Static("当前: 加载中...", id="active-info")
        yield Static("─" * 40, id="separator")
        yield Static("模型列表", id="model-list-title")
        yield ListView(id="model-list")
        with Horizontal():
            yield Button("模型详情", id="btn-detail", variant="default")
            yield Button("添加模型", id="btn-add", variant="default")

    async def on_mount(self) -> None:
        await self.refresh_models()

    async def refresh_models(self) -> None:
        daemon = self.app.daemon  # type: ignore
        status = await daemon.async_get_status()
        port = daemon.get_control_port()
        if port is None:
            return
        data = await async_get_json(f"http://127.0.0.1:{port}/api/status") or {}
        self.active_model = data.get("active_model")

        providers_data = await async_get_json(f"http://127.0.0.1:{port}/api/providers") or []
        # Build model list from provider data
        alias_map: dict[str, set[str]] = {}
        for p in providers_data:
            for m in p.get("models", []):
                aliases = m.get("aliases", []) or [m["name"]]
                for alias in aliases:
                    if alias not in alias_map:
                        alias_map[alias] = set()
                    alias_map[alias].add(p["id"])

        self.models = [
            {"id": alias, "provider_count": len(providers)}
            for alias, providers in alias_map.items()
        ]

        # Update UI
        list_view = self.query_one("#model-list", ListView)
        await list_view.clear()
        for m in self.models:
            prefix = "▶ " if m["id"] == self.active_model else "  "
            text = f"{prefix}{m['id']}   ({m['provider_count']} 供应商)"
            list_view.append(ListItem(Label(text)))

        active_display = self.active_model or "无"
        self.query_one("#active-info", Static).update(f"当前: {active_display}")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-detail":
            list_view = self.query_one("#model-list", ListView)
            if list_view.index is not None and list_view.index < len(self.models):
                model = self.models[list_view.index]
                await self.app.push_screen(  # type: ignore
                    ModelDetailScreen(model, self.app.daemon)  # type: ignore
                )
        elif event.button.id == "btn-add":
            self.notify("添加模型 — 在供应商页添加模型别名即可自动关联", title="提示")

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Switch active model when user presses Enter."""
        if event.item is None:
            return
        # Find the index
        list_view = self.query_one("#model-list", ListView)
        if list_view.index is not None and list_view.index < len(self.models):
            model_id = self.models[list_view.index]["id"]
            daemon = self.app.daemon  # type: ignore
            port = daemon.get_control_port()
            if port:
                import httpx
                async with httpx.AsyncClient(timeout=5.0) as client:
                    await client.post(
                        f"http://127.0.0.1:{port}/api/models/switch",
                        json={"model_id": model_id},
                    )
                await self.refresh_models()
                self.notify(f"已切换到: {model_id}", title="模型切换")
