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
from .models import PlanBody
from .models import StellinaStatus
from .observation import LiveImage
from .observation import ObservationProgress
from .plan import PlanItem
from .plan import PlanProgress
from .plan import build_plan
from .weather import NightConditions
from .weather import assess_night
from .weather import cloud_forecast

__all__ = [
    "BAND_2_4_GHZ",
    "BAND_5_GHZ",
    "AutoInitBody",
    "CatalogObject",
    "FtpEntry",
    "LiveImage",
    "NightConditions",
    "ObservationBody",
    "ObservationProgress",
    "PlanBody",
    "PlanItem",
    "PlanProgress",
    "StellinaClient",
    "StellinaCommandError",
    "StellinaConnectionError",
    "StellinaError",
    "StellinaStatus",
    "VisibleObject",
    "assess_night",
    "build_auth_header",
    "build_plan",
    "cloud_forecast",
    "get_object",
    "is_dark",
    "load_catalog",
    "observing_window",
    "sun_altitude",
    "visible_now",
]

__version__ = "0.1.0"
