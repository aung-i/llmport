"""Models tab — the main daily-use screen."""

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal, Container
from textual.widgets import Static, ListView, ListItem, Label, Button, Input
from textual.screen import ModalScreen

from llmport.daemon import DaemonManager
from llmport.ui.screens.onboarding import async_get_json
from llmport.ui.widgets import Section


class ModelDetailScreen(ModalScreen):
    """Modal for viewing/editing a model's provider bindings."""

    CSS = """
    ModelDetailScreen {
        align: center middle;
    }
    #detail-container {
        width: 56;
        height: auto;
        min-height: 18;
        border: thick $primary;
        background: $surface;
        padding: 2 3;
    }
    #detail-container Label {
        padding: 0;
    }
    """

    def __init__(self, model: dict, daemon: DaemonManager):
        super().__init__()
        self.model = model
        self.daemon = daemon

    def compose(self) -> ComposeResult:
        with Container(id="detail-container"):
            yield Label(f"模型: [bold $primary]{self.model['id']}[/]", id="detail-title")
            yield Label("")
            yield Label(f"[dim]供应商数: {len(self.model.get('bindings', []))}[/]")
            yield Label("")
            yield Label("[bold $secondary-lighten-1]供应商绑定[/] (优先级排序):")
            for b in self.model.get("bindings", []):
                yield Label(f"  {b['priority']}. [bold]{b['provider_id']}[/] → {b['model_name']}")
            yield Label("")
            yield Label("[dim]路由策略: priority_fallback[/]")
            yield Label("")
            with Horizontal():
                yield Button(" 设为当前", id="set-active", variant="primary")
                yield Button(" 关闭", id="close")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close":
            self.dismiss()
        elif event.button.id == "set-active":
            port = self.daemon.get_control_port()
            if port is None:
                self.notify("网关未运行", title="模型切换", severity="error")
                return
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    f"http://127.0.0.1:{port}/api/models/switch",
                    json={"model_id": self.model["id"]},
                )
                if resp.status_code != 200:
                    self.notify(f"切换失败", title="模型切换", severity="error")
                    return
            self.notify(f"已切换到: {self.model['id']}", title="模型切换")
            self.dismiss()


class ModelsPane(Vertical):
    """Models list with current active model highlighted."""

    def __init__(self):
        super().__init__()
        self.models: list[dict] = []
        self._filtered_models: list[dict] = []
        self.active_model: str | None = None

    def compose(self) -> ComposeResult:
        yield Static("[dim]加载中...[/]", id="active-info")
        yield Input(placeholder="搜索模型...", id="model-search")
        with Section("模型列表"):
            yield ListView(id="model-list")
        with Horizontal(id="model-actions"):
            yield Button(" 模型详情", id="btn-detail", variant="default")
            yield Button(" 添加模型", id="btn-add", variant="primary")

    async def on_mount(self) -> None:
        self.set_interval(5.0, self.refresh_models)
        await self.refresh_models()

    async def refresh_models(self) -> None:
        daemon = self.app.daemon  # type: ignore
        status = await daemon.async_get_status()
        port = daemon.get_control_port()
        if port is None:
            return
        data = await async_get_json(f"http://127.0.0.1:{port}/api/status") or {}
        self.active_model = data.get("active_model")

        models_data = (
            await async_get_json(f"http://127.0.0.1:{port}/api/models") or {}
        )
        models_list = models_data.get("models") if isinstance(models_data, dict) else models_data or []
        self.models = [
            {"id": m["id"], "provider_count": m.get("provider_count", 0)}
            for m in models_list
        ]

        list_view = self.query_one("#model-list", ListView)
        await list_view.clear()
        if not self.models:
            list_view.append(ListItem(Label("[dim]暂无模型 — 请先在供应商页添加 Provider[/]")))
        else:
            for m in self.models:
                if m["id"] == self.active_model:
                    text = f"[green]▶[/] [bold $primary]{m['id']}[/]   [dim]({m['provider_count']} 供应商)[/]"
                else:
                    text = f"  {m['id']}   [dim]({m['provider_count']} 供应商)[/]"
                list_view.append(ListItem(Label(text)))

        active_display = self.active_model or "[dim]无[/]"
        self.query_one("#active-info", Static).update(f"当前活跃: [bold $primary]{active_display}[/]")
        self._filtered_models = list(self.models)

    async def on_input_changed(self, event: Input.Changed) -> None:
        """Filter model list by search query."""
        query = event.value.strip().lower()
        list_view = self.query_one("#model-list", ListView)
        await list_view.clear()
        filtered = [m for m in self.models if query in m["id"].lower()] if query else self.models
        if not filtered and query:
            list_view.append(ListItem(Label("[dim]无匹配模型[/]")))
        else:
            for m in filtered:
                if m["id"] == self.active_model:
                    text = f"[green]▶[/] [bold $primary]{m['id']}[/]   [dim]({m['provider_count']} 供应商)[/]"
                else:
                    text = f"  {m['id']}   [dim]({m['provider_count']} 供应商)[/]"
                list_view.append(ListItem(Label(text)))
        self._filtered_models = list(filtered) if filtered else []

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-detail":
            list_view = self.query_one("#model-list", ListView)
            if list_view.index is not None and list_view.index < len(self._filtered_models):
                model = self._filtered_models[list_view.index]
                daemon = self.app.daemon  # type: ignore
                # Fetch full model data via /api/models endpoint
                port = daemon.get_control_port()
                bindings = []
                if port:
                    models_data = (
                        await async_get_json(
                            f"http://127.0.0.1:{port}/api/models"
                        ) or {}
                    )
                    models_list = (
                        models_data.get("models")
                        if isinstance(models_data, dict)
                        else models_data or []
                    )
                    for m in models_list:
                        if m["id"] == model["id"]:
                            bindings = m.get("bindings", [])
                            break
                model_with_bindings = {**model, "bindings": bindings}
                await self.app.push_screen(  # type: ignore
                    ModelDetailScreen(model_with_bindings, self.app.daemon)  # type: ignore
                )
        elif event.button.id == "btn-add":
            self.notify("在供应商页添加模型别名即可自动关联", title="提示")

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item is None:
            return
        list_view = self.query_one("#model-list", ListView)
        if list_view.index is not None and list_view.index < len(self._filtered_models):
            model_id = self._filtered_models[list_view.index]["id"]
            daemon = self.app.daemon  # type: ignore
            port = daemon.get_control_port()
            if port is None:
                self.notify("网关未运行", title="模型切换", severity="error")
                return
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    f"http://127.0.0.1:{port}/api/models/switch",
                    json={"model_id": model_id},
                )
                if resp.status_code != 200:
                    self.notify("切换失败", title="模型切换", severity="error")
                    return
            await self.refresh_models()
            self.notify(f"已切换到: {model_id}", title="模型切换")
