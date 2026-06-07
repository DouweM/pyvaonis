"""Constants for the Stellina integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "vaonis"

CONF_HOST: Final = "host"
CONF_MODEL: Final = (
    "model"  # remembered telescope model, so capability gating survives an offline load
)

DEFAULT_HOST: Final = "10.0.0.1"
DEFAULT_NAME: Final = "Vaonis Smart Telescope"

MANUFACTURER: Final = "Vaonis"
MODEL: Final = "Vaonis telescope"  # generic fallback; the real model comes from status.model
