"""Tests for Card and Section widgets — compose() behaviour with/without title/heading."""

from textual.widgets import Static


class TestCard:
    """Card compose behaviour."""

    def test_card_with_title_yields_title_and_children(self):
        """Card compose with title yields title Static followed by children."""
        from llmport.ui.widgets import Card

        child = Static("child content")
        card = Card("My Title", child)
        result = list(card.compose())

        assert len(result) == 2
        title_widget = result[0]
        assert isinstance(title_widget, Static)
        assert title_widget.content == "My Title"
        assert title_widget.id == "card-title"
        assert result[1] is child

    def test_card_without_title_only_yields_children(self):
        """Card compose without title only yields children (no title Static)."""
        from llmport.ui.widgets import Card

        child1 = Static("child 1")
        child2 = Static("child 2")
        card = Card("", child1, child2)
        result = list(card.compose())

        assert len(result) == 2
        assert result[0] is child1
        assert result[1] is child2


class TestSection:
    """Section compose behaviour."""

    def test_section_with_heading_yields_heading_and_children(self):
        """Section compose with heading yields heading Static followed by children."""
        from llmport.ui.widgets import Section

        child = Static("child content")
        section = Section("My Heading", child)
        result = list(section.compose())

        assert len(result) == 2
        heading_widget = result[0]
        assert isinstance(heading_widget, Static)
        assert heading_widget.content == "My Heading"
        assert heading_widget.id == "section-heading"
        assert result[1] is child

    def test_section_without_heading_only_yields_children(self):
        """Section compose without heading only yields children (no heading Static)."""
        from llmport.ui.widgets import Section

        child1 = Static("child 1")
        child2 = Static("child 2")
        section = Section("", child1, child2)
        result = list(section.compose())

        assert len(result) == 2
        assert result[0] is child1
        assert result[1] is child2
