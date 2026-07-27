"""llmport theme definition."""

from textual.theme import Theme

llmport_theme = Theme(
    name="llmport",
    primary="#10b981",
    secondary="#a78bfa",
    accent="#06b6d4",
    warning="#f59e0b",
    error="#ef4444",
    success="#22c55e",
    foreground="#e0e0e0",
    background="#121212",
    surface="#1a1b2e",
    panel="#212340",
    dark=True,
    variables={
        "border-blurred": "#0d7377",
        "text-muted": "#64748b",
        "footer-key-foreground": "#10b981",
        "block-cursor-foreground": "#121212",
        "block-cursor-background": "#10b981",
        "block-cursor-text-style": "none",
        "block-hover-background": "#212340",
    },
)
