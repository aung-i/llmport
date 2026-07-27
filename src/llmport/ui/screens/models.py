"""Models tab — the main daily-use screen."""

from typing import TYPE_CHECKING, cast

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal, Container
from textual.widgets import Static, ListView, ListItem, Label, Button, Input, LoadingIndicator
from textual.screen import ModalScreen
from textual.binding import Binding
from textual.events import Key

from llmport.daemon import DaemonManager
from llmport.ui import async_get_json
from llmport.ui.widgets import Section

if TYPE_CHECKING:
    from llmport.app import LlmPortApp


def _to_models_list(api_response: dict | list | None) -> list[dict]:
    """Convert a raw ``/api/models`` response to a list of model dicts.

    Handles every edge case the wire can throw at us:

    * ``None`` / ``"models": null`` / missing key / empty dict -> ``[]``
    * ``[...]`` (already a list) -> identity
    * ``{"models": [...]}`` -> unwrap the list
    * ``[]`` (the falsy-list pitfall -- see below)

    **Why not ``or []``?**
    ``[] or []`` evaluates to ``[]`` (correct), but ``[] or {}`` evaluates
    to ``{}`` (broken), and then ``{}.get("models")`` returns ``None``,
    causing ``TypeError`` on iteration.
    """
    if api_response is None:
        return []
    if isinstance(api_response, dict):
        return api_response.get("models") or []
    if isinstance(api_response, list):
        return api_response
    return []


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
        border: solid $primary;
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

    DEFAULT_CSS = """
    ModelsPane {
        overflow-y: auto;
    }
    #model-actions {
        dock: bottom;
        height: auto;
    }
    /* LoadingIndicator defaults to height: 1fr, which (even while hidden via
       visible=False) breaks the dock layout and pushes the whole pane's content
       off-screen. Pin it to a single row and toggle `display` instead of
       `visible` so it is fully removed from the flow once the first refresh
       completes. */
    #loading-indicator {
        height: 1;
    }
    """

    BINDINGS = [
        Binding("delete", "delete_model", "删除", show=True),
        Binding("backspace", "delete_model", "", show=False),
        Binding("n", "switch_next", "切换", show=True),
        Binding("/", "focus_search", "搜索", show=True),
    ]

    def __init__(self):
        super().__init__()
        self.models: list[dict] = []
        self._filtered_models: list[dict] = []
        self.active_model: str | None = None

    def compose(self) -> ComposeResult:
        yield LoadingIndicator(id="loading-indicator")
        yield Static(id="active-info")
        yield Input(placeholder="搜索模型...", id="model-search")
        with Section("模型列表"):
            with Container(id="empty-state-container"):
                yield Static(id="empty-state")
                yield Button(" 启动网关", id="btn-start-gateway", variant="primary")
            yield ListView(id="model-list")
        with Horizontal(id="model-actions"):
            yield Button(" 模型详情", id="btn-detail", variant="default")
            yield Button(" 删除模型", id="btn-delete-model", variant="error")
            yield Button(" 添加模型", id="btn-add", variant="primary")

    async def on_mount(self) -> None:
        self.set_interval(5.0, self.refresh_models)
        await self.refresh_models()
        self.query_one("#model-list", ListView).focus()

    async def refresh_models(self) -> None:
        daemon = cast("LlmPortApp", self.app).daemon
        status = await daemon.async_get_status()
        port = daemon.get_control_port()

        # Always hide loading indicator after refresh completes (success or not).
        # Use `display` (not `visible`) so it leaves the layout flow entirely;
        # an h=0-but-in-flow LoadingIndicator corrupts the pane's dock layout.
        self.query_one("#loading-indicator", LoadingIndicator).styles.display = "none"

        if port is None:
            empty = self.query_one("#empty-state", Static)
            empty.update("网关未运行，请在网关页启动")
            self.query_one("#empty-state-container", Container).visible = True
            self.query_one("#btn-start-gateway", Button).visible = True
            self.query_one("#model-list", ListView).visible = False
            return
        data = await async_get_json(f"http://127.0.0.1:{port}/api/status") or {}
        self.active_model = data.get("active_model")

        models_list = _to_models_list(
            await async_get_json(f"http://127.0.0.1:{port}/api/models")
        )
        self.models = [
            {"id": m["id"], "provider_count": m.get("provider_count", 0)}
            for m in models_list
        ]

        # Show active-info
        active_display = self.active_model or "[dim]无[/]"
        self.query_one("#active-info", Static).update(f"当前活跃: [bold $primary]{active_display}[/]")
        self.query_one("#active-info", Static).visible = True

        # Rebuild the list. Shared with on_input_changed so a background refresh
        # reapplies the active search filter and preserves the current selection
        # instead of wiping them.
        await self._render_list()

    async def _render_list(self) -> None:
        """Rebuild the ListView from ``self.models``.

        Applies the current search filter and preserves the selected model (by
        id) across the rebuild. Shared by ``refresh_models`` (after a data
        fetch) and ``on_input_changed`` (while typing a query), so the 5s
        background refresh never clears the active filter or the user's
        selection.
        """
        list_view = self.query_one("#model-list", ListView)
        empty_container = self.query_one("#empty-state-container", Container)
        empty = self.query_one("#empty-state", Static)
        start_btn = self.query_one("#btn-start-gateway", Button)

        # Remember the selected model id so we can restore it after rebuild.
        selected_id: str | None = None
        if list_view.index is not None and list_view.index < len(self._filtered_models):
            selected_id = self._filtered_models[list_view.index].get("id")

        query = self._current_query()
        filtered = (
            [m for m in self.models if query in m["id"].lower()] if query else list(self.models)
        )
        self._filtered_models = filtered

        await list_view.clear()
        # btn-start-gateway only belongs to the gateway-not-running state
        # (handled in refresh_models); keep it hidden otherwise.
        start_btn.visible = False

        if not filtered:
            empty.update("无匹配模型" if query else "暂无模型 - 请先在供应商页添加 Provider")
            empty_container.visible = True
            list_view.visible = False
            return

        empty_container.visible = False
        list_view.visible = True
        new_index: int | None = None
        for i, m in enumerate(filtered):
            if m["id"] == self.active_model:
                text = f"[green]▶[/] [bold $primary]{m['id']}[/]   [dim]({m['provider_count']} 供应商)[/]"
            else:
                text = f"  {m['id']}   [dim]({m['provider_count']} 供应商)[/]"
            list_view.append(ListItem(Label(text)))
            if m["id"] == selected_id:
                new_index = i
        # Restore selection if the model is still present; else leave unselected.
        list_view.index = new_index

    def _current_query(self) -> str:
        """Lowercased current search query (empty string if none/empty)."""
        try:
            return self.query_one("#model-search", Input).value.strip().lower()
        except Exception:
            return ""

    async def on_input_changed(self, event: Input.Changed) -> None:
        """Filter model list by search query (live, as the user types)."""
        if event.input.id == "model-search":
            await self._render_list()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter in the search box: switch to the highlighted model (fzf-style),
        or focus the list if nothing is selected yet."""
        if event.input.id != "model-search":
            return
        list_view = self.query_one("#model-list", ListView)
        if (
            list_view.index is not None
            and list_view.index < len(self._filtered_models)
        ):
            model_id = self._filtered_models[list_view.index]["id"]
            await self._switch_model_via_api(model_id, cast("LlmPortApp", self.app).daemon)
        else:
            list_view.focus()

    def on_key(self, event: Key) -> None:
        """Let ↑/↓ navigate the result list while the search box is focused.

        Without this, up/down do nothing in the search Input (it is single
        line), so there's no way to pick a filtered result without first
        tabbing into the list. Here we move the list selection directly so the
        user can type to filter, arrow to pick, and Enter to switch -- all
        without leaving the search box.
        """
        if event.key not in ("up", "down"):
            return
        search = self.query_one("#model-search", Input)
        if self.app.focused is not search:
            return  # let the list handle its own up/down natively
        list_view = self.query_one("#model-list", ListView)
        if not list_view.visible or not len(list_view.children):
            return
        event.prevent_default()
        event.stop()
        n = len(list_view.children)
        # Start above the first when going down, below the last when going up,
        # so the first down selects item 0 and the first up selects the last.
        cur = list_view.index if list_view.index is not None else (
            -1 if event.key == "down" else n
        )
        if event.key == "down":
            list_view.index = min(cur + 1, n - 1)
        else:
            list_view.index = max(cur - 1, 0)

    def action_focus_search(self) -> None:
        """Binding: / - jump focus to the search input so you can type to filter."""
        self.query_one("#model-search", Input).focus()

    def focus_list(self) -> None:
        """Focus the model list.

        Used to restore keyboard focus when the models tab is re-activated --
        switching tabs leaves focus None, so without this ↑/↓/Enter would do
        nothing until the user clicked or tabbed back into the list.
        """
        list_view = self.query_one("#model-list", ListView)
        if list_view.visible:
            list_view.focus()
        else:
            # List hidden (e.g. gateway not running) -> focus the search box instead.
            self.query_one("#model-search", Input).focus()


    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-start-gateway":
            daemon = cast("LlmPortApp", self.app).daemon
            daemon.start()
            self.notify("网关已启动", title="网关")
            # Hide start button immediately; next auto-refresh will populate data
            self.query_one("#btn-start-gateway", Button).visible = False
            await self.refresh_models()
        elif event.button.id == "btn-detail":
            list_view = self.query_one("#model-list", ListView)
            if list_view.index is not None and list_view.index < len(self._filtered_models):
                model = self._filtered_models[list_view.index]
                daemon = cast("LlmPortApp", self.app).daemon
                port = daemon.get_control_port()
                bindings = []
                if port:
                    models_list = _to_models_list(
                        await async_get_json(
                            f"http://127.0.0.1:{port}/api/models"
                        )
                    )
                    for m in models_list:
                        if m["id"] == model["id"]:
                            bindings = m.get("bindings", [])
                            break
                model_with_bindings = {**model, "bindings": bindings}
                _m_app = cast("LlmPortApp", self.app)
                await _m_app.push_screen(
                    ModelDetailScreen(model_with_bindings, _m_app.daemon)
                )
        elif event.button.id == "btn-delete-model":
            list_view = self.query_one("#model-list", ListView)
            if list_view.index is not None and list_view.index < len(self._filtered_models):
                model = self._filtered_models[list_view.index]
                daemon = cast("LlmPortApp", self.app).daemon
                port = daemon.get_control_port()
                if port is None:
                    self.notify("网关未运行", title="删除模型", severity="error")
                    return
                import httpx
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.delete(
                        f"http://127.0.0.1:{port}/api/models",
                        json={"model_id": model["id"]},
                    )
                    if resp.status_code != 200:
                        self.notify(f"删除失败", title="删除模型", severity="error")
                        return
                self.notify(f"已删除: {model['id']}", title="删除模型")
                await self.refresh_models()
        elif event.button.id == "btn-add":
            self.notify("在供应商页添加模型别名即可自动关联", title="提示")

    def action_delete_model(self) -> None:
        """Binding: delete — delete the selected model."""
        list_view = self.query_one("#model-list", ListView)
        if list_view.index is not None and list_view.index < len(self._filtered_models):
            model = self._filtered_models[list_view.index]
            daemon = cast("LlmPortApp", self.app).daemon
            port = daemon.get_control_port()
            if port is None:
                self.notify("网关未运行", title="删除模型", severity="error")
                return
            import asyncio
            asyncio.ensure_future(self._delete_model_via_api(model["id"], daemon))

    def action_switch_next(self) -> None:
        """Binding: n — switch to the selected model."""
        list_view = self.query_one("#model-list", ListView)
        if list_view.index is not None and list_view.index < len(self._filtered_models):
            model = self._filtered_models[list_view.index]
            daemon = cast("LlmPortApp", self.app).daemon
            port = daemon.get_control_port()
            if port is None:
                self.notify("网关未运行", title="模型切换", severity="error")
                return
            import asyncio
            asyncio.ensure_future(self._switch_model_via_api(model["id"], daemon))

    async def _delete_model_via_api(self, model_id: str, daemon: DaemonManager) -> None:
        """Call DELETE /api/models for the given model_id."""
        port = daemon.get_control_port()
        if port is None:
            self.notify("网关未运行", title="删除模型", severity="error")
            return
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.delete(
                f"http://127.0.0.1:{port}/api/models",
                json={"model_id": model_id},
            )
            if resp.status_code != 200:
                self.notify(f"删除失败", title="删除模型", severity="error")
                return
        self.notify(f"已删除: {model_id}", title="删除模型")
        await self.refresh_models()

    async def _switch_model_via_api(self, model_id: str, daemon: DaemonManager) -> None:
        """Call POST /api/models/switch for the given model_id."""
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
                self.notify(f"切换失败", title="模型切换", severity="error")
                return
        await self.refresh_models()
        self.notify(f"已切换到: {model_id}", title="模型切换")

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item is None:
            return
        list_view = self.query_one("#model-list", ListView)
        if list_view.index is not None and list_view.index < len(self._filtered_models):
            model_id = self._filtered_models[list_view.index]["id"]
            daemon = cast("LlmPortApp", self.app).daemon
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
