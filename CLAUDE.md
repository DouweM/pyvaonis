# pystellina — agent orientation

Reverse-engineered local API client + CLI + Home Assistant integration for the **Vaonis Stellina**
smart telescope. This file is the fast-start for a fresh agent session. Read it first, then
`README.md` (usage), `PROTOCOL.md` (wire protocol), `docs/API.md` (every endpoint), `docs/STATUS.md`
(status schema).

## Status: WORKING against real hardware ✅
Validated end-to-end on **firmware 2.35.7** (scope id `stellina-f8bd80`, owner DouweM, Mexico City):
EIO3 socket + `STATUS_UPDATED`, Ed25519 auth (`app/status` → 200), take-control, live stacked-image
fetch (real M104 JPEG), `observing`, FTP. Repo is **private**: `github.com/DouweM/pystellina`, CI green
(lint/typecheck/test/hassfest). Push to `main` directly (personal repo).

## Hardware & network facts (firmware 2.35.7)
- Telescope is **always its own Wi-Fi AP at `10.0.0.1`** (no station mode). Three services:
  - REST `http://10.0.0.1:8082/v1/` — commands. Every call needs `Authorization` = Ed25519/TweetNaCl
    signature over the rotating `challenge` (keys embedded in the app; reproduced in `pystellina/auth.py`).
  - **Socket.IO v2 / Engine.IO v3** (`EIO=3`) on `:8083`. python-socketio can't do EIO3 → we ship our own
    `pystellina/_eio3.py` (aiohttp websocket). Inbound status event = **`STATUS_UPDATED`** (a JSON object,
    not a string); `CONTROL_ERROR` for control errors. Outbound: `emit("message", <key>)` for
    `takeControl`/`releaseControl`/`setUserName`/`setSystemTime`. Heartbeat is client-initiated (send `2`).
  - HTTP images at `:8082/files/captures/<storeId>/images/IMG_NNNN.jpg` (no auth); FTP `:21` library under
    `/system/captures` (also bias/dark/history/logs/plan/reports/temp; `/user` empty).
- Active observation = top-level **`currentOperation`** with `type=="OBSERVATION"` (NOT
  `currentObservationOperation`). `capture.images[-1]` is the live frame; integration =
  `stackingCount × capture.cameraParams.exposureMicroSec`; total in `store.totalStackingCount`.
- **No battery** (mains/USB powered): `sensors` has temperature/humidity/`dewpointDepression`/defogStatus.
- Storage ~10 GB data partition (~9 GB free), no USB. Observatory name "Oasis".
- One controller at a time: **take-control demotes the phone app**; read-only status works for any client.
- The app's "Save" = save-to-phone (not a scope op). Telescope-side persist = `capture/setToBeResumable`.

## Bridge to the LAN (planned, not yet built)
Scope is AP-only → needs a Wi-Fi bridge. Plan: GL.iNet **GL-MT300N-V2 "Mango"** in repeater mode joins
the Stellina AP; its WAN Ethernet → oasys **IoT VLAN (10.3.142.0/24)**; **port-forward 8082/8083/21 →
10.0.0.1**; point pystellina/HA at the GL's IoT IP. HA is on the services VLAN (10.3.127.x). FTP needs the
router's conntrack FTP helper. Details in `README.md` → "Wi-Fi bridge". (User is buying the Mango.)

## Repo layout
```
pystellina/            const.py auth.py _eio3.py models.py client.py catalog.py astro.py
                       observation.py sequence.py weather.py ftp.py cli.py  data/catalog.json
custom_components/stellina/  HACS integration (coordinator/entity/config_flow/sensor/binary_sensor/
                       button/select/camera/media_source/http + manifest/hacs/strings/services.yaml)
tools/extract_catalog.py     regenerate data/catalog.json from an APK
tests/                 pytest (auth, catalog, astro, observation, sequence, ftp, export, safety,
                       weather, models, eio3)  — 53 tests
docs/API.md            complete endpoint reference + pystellina coverage
docs/STATUS.md         full status JSON schema (firmware 2.35.7)
README.md PROTOCOL.md  usage / wire protocol
```
**Reverse-engineering artifacts are gitignored, on disk:** `singularity-1.38.10.apk` (the APK,
`com.vaonis.barnard` v1.38.10), `src/` (jadx Java), `apktool_out/` (apktool resources incl.
`res/values/strings.xml`), `all_strings.txt`. Regenerate: `jadx -d src --no-res app.apk`;
`java -jar /tmp/apktool.jar d -f -s -o apktool_out app.apk`; catalog via
`python tools/extract_catalog.py app.apk --strings apktool_out/res/values/strings.xml`.

## What's implemented vs not
- **Implemented** (client + CLI): connect/status stream, take/release control, autoinit, observe
  (catalog/manual), stop, park, shutdown, switch_frequency, **in-observation: adjust_framing,
  restart_autofocus, set_multi_light, set_camera_params, save_observation**, full-res export
  (tiff/jxl), FTP library, live image + recent images, sequences (`run_sequence`), catalog +
  "tonight" visibility, weather verdict (Open-Meteo), darkness/observing window, ephemeris (planets/Moon).
- **Not yet**: planner/playlist/expertMode/sun-mode, captureStore resume, getLogs/reports, mosaic.
- **Safety-gated** (never auto-run): firmware upload (blocked), delete/reset (`allow_unsafe`),
  solar/sun-near (`allow_solar`). See `client._guard_endpoint`. Full map in `docs/API.md`.

## Dev workflow
`uv sync --extra cli --extra astro` then `uv run ruff check . && uv run ruff format --check . &&
uv run pyright && uv run pytest`. CI mirrors that + hassfest. On-hardware test order (laptop on the
Stellina Wi-Fi): `stellina doctor` → `stellina --debug watch` → `stellina selftest <lat> <lon>`.
Commit messages end with the Co-Authored-By trailer; bundle related changes; keep docs current.

## Related repos (separate, in ~/dev) — touched this project
- `ha-microclimate` — user's weather integration. We added `cloud_coverage`/`native_dew_point` to its
  hourly Forecast (committed+pushed). HA gates "good sky watching" on `weather.microclimate`.
- `oasys/home/homeassistant` — HA config; `binary_sensor.good_sky_watching` created via **ha-sync**
  (helpers/template/binary_sensor/), NOT configuration.yaml. Manage HA via the `ha-sync` CLI.

## Roadmap / TODO
- **Web UI** (next big thing): offline-first, mobile+laptop. FastAPI over pystellina + Vite/React/TS
  (matches oasys `welkom/spion`). Explore/Favorites(local)/Manual/Plan from the bundled catalog;
  Live view + controls + gallery from the scope. Data model in `PROTOCOL.md` → "Catalog & browsing".
  Step 1: re-extract catalog keeping `distance/realSize/discoveredBy` + bundle `catalog_object/*.png`
  (lowercased id, rotate 90°) + `constellations.json`.
- HA: media-source over `/files`/FTP `/system/captures`; HA service/UI for sequences exists (`run_plan`).
- Confirm planner/playlist/sun bodies if those features are wanted.

## Gotchas
- It's EIO3, not python-socketio. Status event is `STATUS_UPDATED`; observation under `currentOperation`.
- Use a stable `device_id` (default is MAC-derived) or `connectedDevices` piles up.
- `app/setSettings` may replace rather than merge — `set_multi_light` echoes current settings to be safe.
