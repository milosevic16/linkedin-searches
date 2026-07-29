"""Per-model request quirks, in one place.

Not every model accepts every parameter, and getting it wrong costs a whole
step: a rejected request means no scores, or no drafts, with only a
BadRequestError to show for it.
"""

from __future__ import annotations

# `effort` is not accepted by every model — Haiku 4.5 rejects it outright,
# which silently killed a whole step on the first run that used it. Keep this
# list to models known to take it.
_NO_EFFORT = ("haiku", "sonnet-4-5")


def supports_effort(model: str) -> bool:
    return not any(marker in (model or "").lower() for marker in _NO_EFFORT)


def output_config(model: str, schema: dict | None = None, effort: str = "low") -> dict:
    """Build output_config for a model, omitting anything it would reject."""
    cfg: dict = {}
    if effort and supports_effort(model):
        cfg["effort"] = effort
    if schema is not None:
        cfg["format"] = {"type": "json_schema", "schema": schema}
    return cfg
