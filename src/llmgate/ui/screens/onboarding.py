"""Onboarding/welcome screen for first-run setup."""

from textual.app import Screen
from textual.widgets import Static


class OnboardingScreen(Screen[None]):
    """First-run onboarding screen displayed when no API key is configured."""
