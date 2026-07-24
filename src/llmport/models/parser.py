"""Model text parsing utilities.

The ``parse_models()`` function converts user-facing comma-separated model
input into structured model dicts consumed by the provider API.
"""


def parse_models(raw: str) -> list[dict]:
    """Parse comma-separated model input into model dicts.

    Each line becomes one model entry.  The first comma-separated value on
    a line is the model name; subsequent values are aliases.

    Returns a list of ``{"name": str, "aliases": list[str]}`` dicts.
    """
    models: list[dict] = []
    for line in raw.strip().split("\n"):
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(",") if p.strip()]
        if parts:
            models.append({"name": parts[0], "aliases": parts[1:]})
    return models
