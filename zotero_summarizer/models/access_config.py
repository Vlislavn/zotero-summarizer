"""Institutional browser-access configuration."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

__all__ = ["UniversityAccessConfig"]


def _choice(value: str, allowed: frozenset[str], field: str) -> str:
    normalized = (value or "").strip().lower()
    if normalized not in allowed:
        raise ValueError(f"{field} must be one of {sorted(allowed)}, got {value!r}")
    return normalized


class UniversityAccessConfig(BaseModel):
    """Persistent-browser access for subscribed scholarly full text."""

    enabled: bool = Field(default=False)
    ezproxy_prefix: str = Field(default="")
    login_url: str = Field(default="")
    browser_profile_dir: str = Field(default="")
    headless: bool = Field(default=True)
    fetch_timeout_secs: float = Field(default=60.0, ge=5.0, le=600.0)
    cookie_browser: str = Field(default="")
    browser_channel: str = Field(default="chrome")

    @field_validator("cookie_browser")
    @classmethod
    def _validate_cookie_browser(cls, value: str) -> str:
        return _choice(
            value,
            frozenset(
                {
                    "",
                    "chrome",
                    "chromium",
                    "firefox",
                    "edge",
                    "brave",
                    "safari",
                    "opera",
                    "vivaldi",
                }
            ),
            "cookie_browser",
        )

    @field_validator("browser_channel")
    @classmethod
    def _validate_browser_channel(cls, value: str) -> str:
        return _choice(
            value,
            frozenset(
                {
                    "",
                    "chromium",
                    "chrome",
                    "chrome-beta",
                    "chrome-dev",
                    "chrome-canary",
                    "msedge",
                    "msedge-beta",
                    "msedge-dev",
                    "msedge-canary",
                }
            ),
            "browser_channel",
        )
