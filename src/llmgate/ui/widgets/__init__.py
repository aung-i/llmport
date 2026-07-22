"""Reusable TUI widgets for llmgate."""

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Static


class Card(Container):
    """A bordered card with title and content area."""

    DEFAULT_CSS = """
    Card {
        border: solid $primary-darken-2;
        background: $surface;
        padding: 1 2;
        margin: 1 0;
    }
    Card > #card-title {
        text-style: bold;
        color: $primary;
        padding-bottom: 1;
    }
    """

    def __init__(self, title: str = "", *children, **kwargs):
        super().__init__(**kwargs)
        self._title = title
        self._children = children

    def compose(self) -> ComposeResult:
        if self._title:
            yield Static(self._title, id="card-title")
        for child in self._children:
            yield child


class Section(Container):
    """A section with a heading and children."""

    DEFAULT_CSS = """
    Section {
        padding: 1 0;
    }
    Section > #section-heading {
        text-style: bold;
        color: $secondary-lighten-1;
        padding: 1 0;
    }
    """

    def __init__(self, heading: str = "", *children, **kwargs):
        super().__init__(**kwargs)
        self._heading = heading
        self._children = children

    def compose(self) -> ComposeResult:
        if self._heading:
            yield Static(self._heading, id="section-heading")
        for child in self._children:
            yield child


