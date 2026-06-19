"""Minimal i18n shim — the shop UI uses client-side JS lang switching."""

DEFAULT_LANG = "uk"
SUPPORTED_LANGS = ("uk", "ru")


def get_t(lang: str) -> dict:
    """Return an empty translations dict (shop template uses JS-based i18n)."""
    return {}
