"""pystellina — async client + CLI for Vaonis Stellina smart telescopes."""

from __future__ import annotations

from .astro import is_dark
from .astro import observing_window
from .astro import sun_altitude
from .auth import build_auth_header
from .catalog import CatalogObject
from .catalog import VisibleObject
from .catalog import get_object
from .catalog import load_catalog
from .catalog import visible_now
from .client import StellinaClient
from .client import StellinaCommandError
from .client import StellinaConnectionError
from .client import StellinaError
from .const import BAND_2_4_GHZ
from .const import BAND_5_GHZ
from .ftp import FtpEntry
from .models import AutoInitBody
from .models import ObservationBody
from .models import StellinaStatus
from .observation import LiveImage
from .observation import ObservationProgress
from .sequence import SequenceEvent
from .sequence import SequenceItem
from .sequence import run_sequence

__all__ = [
    "BAND_2_4_GHZ",
    "BAND_5_GHZ",
    "AutoInitBody",
    "CatalogObject",
    "FtpEntry",
    "LiveImage",
    "ObservationBody",
    "ObservationProgress",
    "SequenceEvent",
    "SequenceItem",
    "StellinaClient",
    "StellinaCommandError",
    "StellinaConnectionError",
    "StellinaError",
    "StellinaStatus",
    "VisibleObject",
    "build_auth_header",
    "get_object",
    "is_dark",
    "load_catalog",
    "observing_window",
    "run_sequence",
    "sun_altitude",
    "visible_now",
]

__version__ = "0.1.0"
