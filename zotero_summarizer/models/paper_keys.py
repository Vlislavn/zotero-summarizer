"""Pure lexical paper-key boundary shared by storage and reading services."""


def validate_paper_item_key(item_key: object) -> str:
    """Require a nonblank single component, preserving its original identity."""
    if (
        not isinstance(item_key, str) or not item_key.strip() or item_key in {".", ".."}
        or any(c in item_key for c in "/\\\0")
    ):
        raise ValueError("Invalid paper item key")
    return item_key
