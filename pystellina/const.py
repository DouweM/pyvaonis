"""Connection constants and REST endpoints, reverse-engineered from Singularity 1.38.10.

See PROTOCOL.md for provenance. Values come from
``com.vaonis.instruments.sdk.models.StellinaContext`` and ``StellinaAPI``.
"""

from __future__ import annotations

from typing import Final

DEFAULT_IP: Final = "10.0.0.1"
# Echoed into request bodies that carry it (e.g. planner/startPlan appVersion); mirrors the app.
APP_VERSION: Final = "1.38.10"
HTTP_PORT: Final = 8082
SOCKET_PORT: Final = 8083
SOCKET_PATH: Final = "/socket.io"
HTTP_ROOT: Final = ""  # baseUrl = http://{ip}:{port}{root}/v1/


def base_url(ip: str = DEFAULT_IP, port: int = HTTP_PORT, root: str = HTTP_ROOT) -> str:
    """REST base URL, mirroring StellinaContext.getBaseUrl()."""
    return f"http://{ip}:{port}{root}/v1/"


def http_url(ip: str, path: str, port: int = HTTP_PORT, root: str = HTTP_ROOT) -> str:
    """Telescope HTTP URL for a server-relative path, mirroring Instrument.makeHttpUrl()."""
    return f"http://{ip}:{port}{root}{path}"


FTP_PORT: Final = 21
# Anonymous FTP. On firmware 2.35.7 the captures live under /system/captures (/system also has
# bias, dark, history, logs, plan, reports, temp); /user was empty. HTTP serves the same images
# at /files/captures/<storeId>/...
FTP_ROOT: Final = "/system/captures"


def socket_url(ip: str = DEFAULT_IP, port: int = SOCKET_PORT) -> str:
    """socket.io base URL, mirroring StellinaSocketV2.getUrlFromContext()."""
    return f"http://{ip}:{port}"


# REST endpoints (relative to base_url), from StellinaAPI.kt. POST unless noted.
class Endpoint:
    """Telescope REST endpoints."""

    START_AUTOINIT: Final = "general/startAutoInit"
    STOP_AUTOINIT: Final = "general/stopAutoInit"
    START_OBSERVATION: Final = "general/startObservation"
    STOP_OBSERVATION: Final = "general/stopObservation"
    PARK: Final = "general/park"
    OPEN_FOR_MAINTENANCE: Final = "general/openForMaintenance"
    SET_USER_PARAMS: Final = "general/setUserParams"
    ADJUST_FRAMING: Final = "general/adjustObservationFraming"
    ADJUST_FOCUS: Final = "general/adjustObservationFocus"
    APP_STATUS: Final = "app/status"  # GET
    SET_SETTINGS: Final = "app/setSettings"
    SWITCH_FREQUENCY: Final = "network/switchFrequency"
    REQUEST_SHUTDOWN: Final = "board/requestShutdown"
    EXPORT_TIFF: Final = "capture/exportImageTiff"
    EXPORT_JPEGXL: Final = "capture/exportImageJpegXl"  # POST ?captureId= (no body)
    SET_TO_BE_RESUMABLE: Final = "capture/setToBeResumable"  # "Save" — persist current stack
    GENERATE_DARK: Final = "darkManager/generateDark"
    STOP_GENERATE_DARK: Final = "darkManager/stopGenerateDark"
    START_PLAN: Final = "planner/startPlan"
    STOP_PLAN: Final = "planner/stopPlan"


# Inbound socket.io event names (from decompiled StellinaSocketV2.connect, recovered via smali).
EVENT_STATUS: Final = "STATUS_UPDATED"  # payload is the StellinaStatus JSON object
EVENT_CONTROL_ERROR: Final = "CONTROL_ERROR"

# socket.io control messages, emitted as emit("message", <key>[, <value>]).
SOCKET_EVENT: Final = "message"

# --- Safety classification (see PROTOCOL.md "Command safety"). -------------------------
# Firmware upload is the only true brick vector — never callable, even via the raw passthrough.
FIRMWARE_ENDPOINT: Final = "updates/uploadUpdateFile"
# Irreversible data-loss / ownership-reset endpoints — require an explicit opt-in to call.
DESTRUCTIVE_ENDPOINTS: Final = frozenset(
    {
        "storage/deleteUserStorageFolders",
        "captureStore/deleteStoredCapture",
        "userManager/applyResetResponse",
        "userManager/makeResetRequest",
    }
)
# Pointing at/near the Sun without the Vaonis solar filter can destroy the sensor.
SOLAR_ENDPOINT_PREFIX: Final = "sun/"
SOLAR_EXCLUSION_DEG: Final = 10.0
# Commands that drop the Wi-Fi link (recoverable, but you lose the session).
DISCONNECT_ENDPOINTS: Final = frozenset({"board/requestShutdown", "network/switchFrequency"})
MSG_TAKE_CONTROL: Final = "takeControl"
MSG_RELEASE_CONTROL: Final = "releaseControl"
MSG_SET_USER_NAME: Final = "setUserName"
MSG_SET_SYSTEM_TIME: Final = "setSystemTime"

# Wi-Fi band values for SWITCH_FREQUENCY (NetworkBody.band).
BAND_2_4_GHZ: Final = "BAND_2_4_GHZ"
BAND_5_GHZ: Final = "BAND_5_GHZ"
