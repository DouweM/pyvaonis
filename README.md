# pystellina

Async Python client, CLI, and Home Assistant integration for **Vaonis Stellina** smart
telescopes, over the telescope's **local Wi-Fi API** — no Vaonis cloud, no account.

Reverse-engineered from *Singularity by Vaonis* v1.38.10 (`com.vaonis.barnard`), and confirmed
against real hardware (firmware 2.35.7). Reference docs:
- [`CLAUDE.md`](CLAUDE.md) — agent orientation / project handoff (read first for a fresh session)
- [`PROTOCOL.md`](PROTOCOL.md) — the wire protocol (auth, socket, endpoints, catalog model)
- [`docs/API.md`](docs/API.md) — every REST endpoint + body + pystellina coverage
- [`docs/STATUS.md`](docs/STATUS.md) — the full status-object schema
This README is the usage guide.

> ✅ **Status: working against real hardware** (firmware 2.35.7, scope `stellina-f8bd80`).
> Confirmed end-to-end: the Engine.IO **v3** socket + `STATUS_UPDATED` stream, Ed25519 auth,
> take-control, live observation/stacking readout, live + full-res image fetch, the FTP library,
> and the native plan body shapes. The crypto/auth, catalog, astronomy, plan scheduling, parsing,
> the EIO3 frame codec, and the safety guards are also unit-tested. A full safety audit of the
> decompiled commands informs the [guards](#safety) below — the firmware-upload (brick) endpoint is
> never callable. See [Hardware validation](#hardware-validation).

---

## Contents
- [What it can do](#what-it-can-do)
- [How it works](#how-it-works)
- [Safety](#safety)
- [Install](#install)
- [CLI reference](#cli-reference)
- [Library reference](#library-reference)
- [Targets & "tonight"](#targets--tonight)
- [Live view, stacking & plans](#live-view-stacking--plans)
- [Full-res export & saved library](#full-res-export--saved-library)
- [Home Assistant integration](#home-assistant-integration)
- [Wi-Fi bridge (GL.iNet)](#wi-fi-bridge-glinet)
- [Project layout](#project-layout)
- [Development](#development)
- [Extending](#extending)
- [Hardware validation](#hardware-validation)
- [Legal / License](#legal--license)

---

## What it can do

- **Control**: take control, auto-init/align, start/stop observations, park, shut down.
- **Targets**: bundled 421-object catalog with names, descriptions, coordinates, magnitudes, and
  the app's per-object recommended capture settings; plus planets/Moon via ephemeris.
- **"Tonight"**: rank what's currently up for your location, with the app's exact darkness rule
  (Sun ≤ −10°) and the dusk→dawn observing window.
- **Live imaging**: read the progressively-stacked frame, current target/step, stacking count and
  integration time; download the current frame.
- **Plans**: upload the telescope's own **Plan My Night** ("observe these N targets on a schedule")
  — the firmware then runs the whole night autonomously, even if you disconnect.
- **Weather**: a cloud-cover forecast + Moon → "is tonight worth it?" verdict (Open-Meteo, no key).
- **Export & archive**: render full-res TIFF / JPEG-XL of a capture; browse & download the saved
  image library over FTP.
- **Home Assistant**: sensors, binary sensors (incl. "dark enough"), buttons, a "Tonight's
  target" select, a live-view camera, a media-browser source, and an export service.

## How it works

The Stellina is its own Wi-Fi access point at `10.0.0.1` with three services:

| Channel | Endpoint | Use |
|---|---|---|
| REST (OkHttp/Retrofit) | `http://10.0.0.1:8082/v1/` | commands (park, observe, autoinit, export, …) |
| Socket.IO v2 / EIO3 | `ws://10.0.0.1:8083/socket.io/?EIO=3` | live status (`STATUS_UPDATED`) + take/release control |
| HTTP images | `http://10.0.0.1:8082<path>` | live stacked frames + full-res exports (no auth) |
| FTP (anonymous) | `10.0.0.1:21` (`/user/...`) | the saved image library |

Each REST call carries an `Authorization` header: an **Ed25519 (TweetNaCl) signature** over the
rotating `challenge` from the status stream, using keys embedded in the app — computed locally in
[`pystellina/auth.py`](pystellina/auth.py), so no server/account is needed. The `challenge`
changes with every status push, so the client signs each request from the latest status (you must
have a socket.io status before REST calls authenticate).

## Safety

The commands were audited against the decompiled app for anything that could brick, damage, or
lose data on the telescope (see [PROTOCOL.md → Command safety](PROTOCOL.md)). Guards baked into the
client:

- **Firmware upload (`updates/uploadUpdateFile`) — the only true brick vector — is never callable**,
  even through the raw `api`/`post` passthrough.
- **Irreversible** endpoints (delete library/captures, owner/control reset) require an explicit
  opt-in: `allow_unsafe=True` / CLI `--unsafe`.
- **Solar**: `sun/*` and any target within 10° of the Sun are refused unless `allow_solar`/`--unsafe`
  (imaging the Sun without the Vaonis filter destroys the sensor — software can't verify the filter).
- **Disconnecting** commands warn first: `shutdown` needs `--yes`; `switch_frequency` validates the
  band and warns it drops the link.
- **State guards** mirror the app: commands need control (`master`), the scope not shutting down, and
  (for observe/plan) `initialized` + no operation already running. `take_control` waits for the
  status stream to confirm control (and warns that it demotes the phone app); `observe --replace`
  stops a running observation and waits for idle before taking over.

None of this is a substitute for care, but the dangerous surfaces are gated rather than one typo away.

## Install

```bash
pip install "pystellina[cli,astro]"     # library + CLI + planet/Moon ephemerides
# development:
uv sync --extra cli --extra astro
```

Extras: `cli` (Typer/Rich), `astro` (`ephem`, for solar-system targets). The core (control,
deep-sky catalog, darkness, images, export, FTP) needs neither.

Run from a machine joined to the telescope's Wi-Fi, or reachable via the [bridge](#wi-fi-bridge-glinet).

## CLI reference

`stellina <command>` (all accept `--ip`, default `10.0.0.1`). Set once in your environment to avoid
retyping (and to dodge the negative-longitude arg-parsing footgun):

```bash
export STELLINA_HOST=10.0.0.1     # or your bridge IP
export STELLINA_LAT=19.43 STELLINA_LON=-99.13
```

Location for `tonight`/`forecast`/`plan` resolves **arg → env → the scope's own position** (it knows
its GPS/observatory location), and times print in your **local timezone**. Expected errors (no
control, busy, unreachable) print one clean line; add `--debug` for the full traceback.

| Command | What it does |
|---|---|
| `status` | Connect and print one status snapshot (read-only) |
| `watch [--seconds N]` | Stream raw socket.io events (discovery/debug) |
| `observing` | Current observation: target, step, stacking count, integration |
| `image [--out f.jpg] [--rendered]` | Download the latest frame (current run, or most recent when idle); auto-names `<object>_<frame>.jpg` |
| `recent [--limit N]` | List recent capture runs stored on the telescope (newest first) |
| `tonight [LAT LON] [--now] [--min-grade G] [--limit N]` | Peak altitudes over tonight's dark window with a green/orange/red visibility dot (`--now` for a snapshot) |
| `forecast [LAT LON]` | Is tonight worth it? Cloud forecast over the dark window + Moon → verdict |
| `info OBJECT` | The app's object card: type, constellation, magnitude, real size, distance, discovery, recommended time, description + trivia |
| `observe TARGET` / `observe --ra … --de …` | Slew to a catalog object (`M42`, `Jupiter`) or manual coordinates |
| `autoinit [LAT LON] [--skip-autofocus]` | Initialise / align at a location |
| `stop` / `park` / `shutdown` | Stop running op (plan or observation) / park / power off |
| `plan T1:30 T2:20 … [--lat L --lon L] [--name N] [--wait-for-dark] [--start-in MIN]` | Start the native autonomous Plan-My-Night |
| `stop-plan` | Cancel the running native plan |
| `export CAPTURE_ID [--format tiff\|jxl] [--out f]` | Render & download full-res |
| `library [PATH]` | List the saved FTP library (default `/user`) |
| `download FTP_PATH [--out f]` | Download a saved file |
| `doctor` | Probe reachability (TCP 8082/8083/21, socket.io handshake, FTP) — **run first** |
| `selftest [LAT LON]` | End-to-end checklist: connect → status → control → image → library → export |
| `api ENDPOINT [-X METHOD] [-f k=v …] [-d JSON]` | `gh api`-style raw signed call; prints HTTP status + JSON |
| `post ENDPOINT [--json-body '{}']` / `get ENDPOINT` | Lower-level raw signed REST calls |

Global `--debug` logs wire traffic (socket.io / Engine.IO / aiohttp): `stellina --debug status`.

> Negative coordinates: a leading-`-` value (e.g. a western longitude) is read as an option, so
> end option parsing with `--` first: `stellina forecast -- 19.43 -99.13`.

**Debugging a new telescope** (do this in order):
```bash
stellina doctor                  # is the bridge up? which ports/services respond?
stellina --debug watch           # see raw socket.io events + the status payload shape
stellina selftest 52.37 4.90     # functional pass/fail across every capability
stellina api app/status          # poke any endpoint; e.g. -X POST general/park
```

Example unattended night (native autonomous plan, starts at dusk):
```bash
stellina plan M42:30 "Andromeda Galaxy:45" Jupiter:10 52.37 4.90 --wait-for-dark
```

## Library reference

```python
from pystellina import StellinaClient, ObservationBody, visible_now, get_object, PlanItem

async with StellinaClient(ip="10.0.0.1") as scope:   # opens socket.io, waits for first status
    await scope.take_control()
    print(scope.status.model, scope.status.master_device_id)

    await scope.observe_object("M42")                # catalog target + recommended settings
    obs = scope.current_observation()                # target, step, stacking_count, integration_seconds
    jpeg = await scope.fetch_current_image()         # latest stacked frame (bytes)

    tiff = await scope.export_capture("abc123", "tiff")   # full-res render + download
    files = await scope.library("/user")             # saved archive (FtpEntry list)
    raw = await scope.download_file(files[0].path)
```

Key surfaces:
- `StellinaClient` — `connect/disconnect`, `take_control/release_control`, `start_autoinit`,
  `start_observation/observe_object/stop_observation`, `start_plan/stop_plan/plan_progress`,
  `park`, `request_shutdown`, `switch_frequency`,
  `current_observation/current_image/recent_images/fetch_current_image`,
  `export_url/export_capture`, `library/download_file`, `post/get/request`. Accepts an existing
  `aiohttp.ClientSession` (HA passes its own) and `on_status(callback)` for push updates.
- `pystellina.catalog` — `load_catalog`, `get_object`, `visible_now`, `visible_tonight`, `CatalogObject`.
- `pystellina.astro` — `is_dark`, `sun_altitude`, `observing_window`, `solar_system_radec`.
- `pystellina.observation` — `ObservationProgress`, `LiveImage`, `recent_images`.
- `pystellina.plan` — `build_plan`, `PlanItem`, `PlanProgress`.
- `pystellina.ftp` — `list_dir`, `download`, `FtpEntry`.

## Targets & "tonight"

The app's full catalog is bundled in `pystellina/data/catalog.json` — **421 objects** with name +
description, RA/Dec, magnitude, constellation, a curated `grade` (0–10), and per-object
recommended capture settings. Visibility is computed locally — no cloud:

```python
for v in visible_now(lat=52.37, lon=4.90, min_grade=5, limit=10, require_dark=True):
    print(v.obj.display_name, round(v.altitude), v.obj.grade)   # "Whirlpool Galaxy 79 9.5"
```

**Planets & Moon** are supported (the app uses the same approach): no stored coordinates, so RA/Dec
is computed via `ephem` and fed into a normal go-to. Stellina is a wide-field deep-sky rig, so
planets are small in it — that's optics, not software.

**Darkness** matches the app exactly: night begins when the Sun drops below **−10°**
(`pystellina.astro.is_dark`); `observing_window()` returns tonight's dusk→dawn.

Regenerate the catalog from an APK you own:
```bash
apktool d -f -s -o out singularity.apk
python tools/extract_catalog.py singularity.apk --strings out/res/values/strings.xml
```

## Live view, stacking & plans

During an observation the telescope **live-stacks**: one capture accumulates frames over time, so
integration grows as `stacking_count × exposure` — the app's "layering over a longer exposure".

The live frame is already written to the scope's disk, so `stellina image` downloads that file
directly (instant). `--rendered` instead asks the firmware to re-encode the JPEG on demand (what
the app does) — correct, but slow while it is also stacking.

A **plan** is the app's *Plan My Night*: you upload a list of targets each with a time window
(`planner/startPlan`) and the **firmware runs the whole night itself** — auto-initialising, slewing
target-to-target on schedule, advancing when each window ends — so it continues even if the
controller disconnects. `build_plan()` assigns back-to-back windows from a simple `target:minutes`
list:

```python
from pystellina import PlanItem
# fire-and-forget: returns once the plan is uploaded; the scope runs it autonomously
await scope.take_control()
await scope.start_plan(
    [PlanItem("M42", 30), PlanItem("M51", 20), PlanItem("Jupiter", 10)],
    name="tonight", latitude=52.37, longitude=4.90,
    start_time=None,           # or a dusk datetime to defer the start
)
print(scope.plan_progress())   # state, current target index/name, target_count
# ... later, from any session:
await scope.stop_plan()
```

The native plan parks at the end on its own but has **no power-off step** — keep a session
connected and watch `plan_progress().finished` if you want to `request_shutdown()` afterwards.

## Weather & full automation

**Is tonight worth imaging?** Two paths, depending on whether you're running under Home Assistant:

- **Off-grid (laptop on the scope's Wi-Fi, no HA):** `stellina forecast` / `pystellina.weather.assess_night()`
  pulls a cloud-cover forecast from Open-Meteo (free, no key — its low/mid/high layers are what matter
  for astro), looks only at tonight's dark window, folds in the Moon (via `ephem`), and gives a
  `good`/`marginal`/`poor` verdict:

  ```bash
  stellina forecast 52.37 4.90
  # dark window 00:57-01:57 UTC
  # verdict: GOOD — clear: ~0% mean cloud (max 0%); bright Moon up (98%) — hurts faint deep-sky
  ```

- **In Home Assistant: gate on your own weather entity** (don't duplicate it here). If you run the
  [microclimate](https://github.com/DouweM/ha-microclimate) integration, `weather.microclimate` is a
  calibrated ensemble forecast for your exact site plus live PWS conditions (humidity → dew, wind,
  precipitation, `condition`) — far better than a generic API call. `stellina.run_plan` deliberately
  does **no** weather gating; you gate the automation that calls it (see below).

**Can the whole night run unattended?** Almost entirely — with one hardware caveat:

> ⚠️ **The API can only *shut down*, never power *on*.** Re-powering the board needs the physical
> button. For true turn-on automation, put the scope on a **smart plug** (if it boots when power is
> applied) and have your automation switch it on at dusk; otherwise leave it powered and use `park`
> between nights instead of `shutdown`.

Everything else is automatable. The **`stellina.run_plan`** service runs a whole night in the
background and self-gates on darkness; you put the **weather/dew go-no-go in the automation's
conditions** using your own entities, so one daily automation suffices:

```yaml
automation:
  - alias: Stellina – image on clear nights
    trigger:
      - platform: sun
        event: sunset
        offset: "01:00:00"            # an hour after sunset
    condition:
      # gate on YOUR calibrated weather entity, not a generic API
      - condition: state
        entity_id: weather.microclimate
        state: ["sunny", "clear-night", "partlycloudy"]
      - condition: numeric_state
        entity_id: weather.microclimate
        attribute: humidity
        below: 90                     # dew risk on the optics
    action:
      # (optional) power the scope via a smart plug, then wait for it to boot
      - service: switch.turn_on
        target: { entity_id: switch.stellina_power }
      - delay: "00:02:00"
      - service: stellina.run_plan
        data:
          targets: ["M42:30", "Andromeda Galaxy:45", "Jupiter:10"]
          wait_for_dark: true          # schedule the start at the next dusk (Sun below -10°)
      # the firmware runs the night itself and parks at the end; (optional) cut power later
```

`stellina.run_plan` uploads the native plan and returns immediately (`{started: true}`); the scope
then runs autonomously. `stellina.stop_plan` cancels it. The same plan is available standalone from
the CLI: `stellina plan M42:30 M51:20 … LAT LON --wait-for-dark`.

## Full-res export & saved library

- **Export** a finished capture at full resolution: `capture/exportImageTiff` (TIFF) or
  `capture/exportImageJpegXl` (JPEG-XL). Both return a download URL on the telescope's HTTP server;
  `export_capture()` renders and downloads in one call. CLI: `stellina export <captureId>`.
- **Saved library**: finished observations are stored on the telescope's anonymous FTP under
  `/user/...`. `library()` / `download_file()` browse and fetch them. CLI: `stellina library`,
  `stellina download`.

## Home Assistant integration

This repo doubles as a HACS custom integration in
[`custom_components/stellina`](custom_components/stellina). It wraps `pystellina` and a push-based
`DataUpdateCoordinator` fed by the socket.io stream.

**Install (HACS):** add this repo as a custom repository (category *Integration*), install, restart,
then add the *Stellina* integration and set the host (default `10.0.0.1`). The integration's
`manifest.json` requires `pystellina[astro]` from PyPI — publish it (CI does this on a `v*` tag) or
`pip install` it into the HA venv for local dev.

**Entities & services** (device shows model + firmware version):
- Sensors: **Status** — a one-line human summary in the app's own wording ("M104: 180 stacked
  (30m)", "Initialization: Star pattern analysis (50%)", "tonight — Observation in progress (M51,
  2/5)"), plus **initialization step**, current operation / **target** / **step**, **stacked
  frames** + **total** + **frames acquired** (so you can see the accept/reject ratio),
  **integration time**, **gain**, **exposure**,
  **plan state** + **plan target** (during a native plan), **temperature**, **humidity**, **dew-point
  depression**, **storage free**, **Wi-Fi band**, **filter**, **autofocus temperature**,
  **controlling device**.
- Binary sensors: connected, initialised, has control, **dark enough to observe** (with
  `sun_altitude`/`dark_start`/`dark_end`), **tracking**, **defog active**, **firmware update available**.
- Buttons: take control, **release control**, park, stop, **restart autofocus**, **enable
  multi-night**, shut down.
- Switch: **Multi-Light (HDR)** (CovalENS).
- Select: **Tonight's target** — dark-gated, grade-ranked, includes planets/Moon; selecting starts
  the observation. `suggestions` attribute carries name/altitude/magnitude/constellation/description.
- Camera: **Live view** of the current stacked frame.
- Media source: **Stellina** in the HA media browser — *Recent captures* (live) and *Saved library*
  (FTP `/system/captures`), streamed through HA via a proxy view.
- Services: **`stellina.observe`** (slew to any catalog object), **`stellina.autoinit`** (initialise/
  align), **`stellina.adjust_framing`** (Change Framing) and **`stellina.set_camera_params`**
  (live gain/exposure/saturation, both safe mid-observation), **`stellina.run_plan`** /
  **`stellina.stop_plan`** (start/cancel the native autonomous plan), **`stellina.export_capture`**
  (save a full-res image to the HA media dir).

Still **not** surfaced (and why): Wi-Fi-band switch, firmware upload, factory reset/delete (all
deliberately omitted — link-drop / brick / data-loss); and sun-eclipse, expert raw capture, playlist,
mosaic, and multi-night *resume* (not yet implemented in the client either).

HA (or whatever runs it) must be able to reach `10.0.0.1` — see the bridge below.

## Wi-Fi bridge (GL.iNet)

The Stellina is **always an access point** (no station/client mode — its only network command
toggles the AP between 2.4 GHz and 5 GHz). To reach it from your LAN / Home Assistant, put a small
router in **Repeater / WISP mode** that joins the telescope's Wi-Fi.

**Cheapest hardware:** GL.iNet **GL-MT300N-V2 "Mango" (~$25, 2.4 GHz)** — fine since Stellina
defaults to 2.4 GHz. Dual-band alternatives: GL-SFT1200 "Opal" (~$40), GL-A1300 "Slate Plus" (~$70).

**Setup:**
1. Power on the Stellina; it broadcasts `STELLINA-xxxx`. (Only one device controls it at a time.)
2. Connect to the GL.iNet admin panel (`http://192.168.8.1`).
3. **Internet → Repeater →** scan, join the Stellina's SSID (enter its password if set). The
   router's WAN side gets a `10.0.0.x` lease; its LAN stays `192.168.8.x`.
4. Put Home Assistant (or your laptop) on the GL.iNet LAN (Ethernet or its Wi-Fi). It can now reach
   the telescope at `10.0.0.1`. Test with `stellina watch`.
5. To reach it from your **existing** LAN, either (a) run HA on a box attached to the GL.iNet, or
   (b) uplink the GL.iNet to your home switch and add a static route to `10.0.0.0/24` via the
   router (or use OpenWrt `relayd` to bridge it onto your main subnet).

**Caveats:** single-radio 2.4 GHz halves throughput (fine for control/status, slower for image/FTP
pulls — choose dual-band to avoid); FTP uses passive mode (works through repeater NAT; enable the
conntrack FTP helper if it stalls across a routed setup); the scope allows one controller at a time.

### Bridging onto a VLAN with 1:1 port-forwards (recommended for a fixed install)

To reach the scope from a different subnet (e.g. Home Assistant on a services VLAN, scope exposed on
an IoT VLAN) without merging subnets — the GL.iNet's single radio is the **repeater client of the
Stellina**, and its **WAN Ethernet** plugs into your target VLAN:

1. GL **Repeater** → join `STELLINA-xxxx` (radio becomes the uplink to `10.0.0.1`).
2. GL **WAN Ethernet** → a switch port on the target VLAN; give the GL a reserved IP there
   (e.g. `10.3.142.50`). This IP is what clients talk to.
3. GL **port forwards** (WAN → the repeater-side scope), 1:1 ports so image URLs keep working:
   `TCP 8082 → 10.0.0.1:8082`, `8083 → 10.0.0.1:8083`, `21 → 10.0.0.1:21`.
4. Point pystellina / the HA integration at the GL's VLAN IP (`StellinaClient(ip="10.3.142.50")`).
   REST, socket.io, live images and full-res export all ride HTTP on 8082/8083 — done.
5. FTP (saved library) is passive-mode: install the router's FTP NAT helper
   (`opkg install kmod-nf-nat-ftp kmod-nf-conntrack-ftp`) so dynamic passive ports are forwarded.
   pystellina already ignores the scope's advertised `10.0.0.1` (like `curl --ftp-skip-pasv-ip`),
   so with the helper the archive browse works through the bridge; without it, only FTP is affected
   (HTTP control/imaging/export are fine).

This mirrors the common "GL.iNet + Ethernet + port-forward" pattern, except the forward target is the
**repeater-side** `10.0.0.1` (the Stellina is an AP, so the router is its *client*, not its host).

## Project layout

```
pystellina/                 # the library (flat layout)
  const.py                  # IP, ports, endpoints, URL helpers
  auth.py                   # Ed25519 challenge → Authorization header (embedded keys)
  models.py                 # StellinaStatus, ObservationBody, AutoInitBody
  client.py                 # StellinaClient: socket.io + REST + images + export + ftp
  catalog.py                # bundled catalog, get_object, visible_now
  astro.py                  # sun position, is_dark, observing_window, ephemerides
  observation.py            # ObservationProgress, LiveImage, recent_images
  plan.py                   # native Plan-My-Night: build_plan + PlanProgress
  weather.py                # cloud forecast (Open-Meteo) + Moon -> night verdict
  ftp.py                    # saved-library browse/download
  _eio3.py                  # minimal Engine.IO v3 / Socket.IO v2 websocket client
  cli.py                    # Typer CLI (entry point `stellina`)
  data/catalog.json         # bundled object catalog (regenerate via tools/)
custom_components/stellina/ # HACS integration wrapping pystellina
  __init__.py coordinator.py entity.py config_flow.py
  sensor.py binary_sensor.py button.py select.py camera.py
  media_source.py http.py   # media browser + proxy view
  manifest.json hacs.json strings.json services.yaml const.py
tools/extract_catalog.py    # regenerate data/catalog.json from an APK
tests/                      # pytest (auth, catalog, astro, observation, plan, ftp, export, models)
PROTOCOL.md                 # reverse-engineered wire protocol
```

The reverse-engineering artifacts (the APK, jadx output under `src/`, `apktool_out/`,
`all_strings.txt`) are gitignored — not part of the package.

## Development

Tooling: **uv + ruff + pyright + pytest** (Python 3.13).

```bash
uv sync --extra cli --extra astro   # create .venv, install deps
uv run ruff check . && uv run ruff format --check .
uv run pyright
uv run pytest
uv build                            # wheel includes data/catalog.json
```

CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs lint + format + pyright + pytest and
validates the integration with hassfest + HACS. `publish.yml` builds and publishes to PyPI on a
`v*` tag. Conventions follow the sibling `govee-temperature` / `ha-sync` projects.

## Extending

- **New REST command**: add the path to `const.Endpoint`, a typed body to `models.py` if needed, and
  a method on `StellinaClient` (sign via `self.post/get`). Mirror `StellinaAPI` in `PROTOCOL.md §4`.
- **New status field**: it's already in `status.raw`; add a typed accessor to `StellinaStatus` or a
  parser in `observation.py`. Status uses `extra="allow"`, so unknown fields are preserved.
- **New HA entity**: add a platform file using `StellinaEntity` (device wiring) + the coordinator;
  register the `Platform` in `__init__.py` and a name in `strings.json`.
- **Solar-system math** lives in `astro.py` (`ephem`); deep-sky geometry is pure-Python there too.

## Hardware validation

The socket protocol is now pinned from the decompiled app: **Engine.IO v3** over websocket, inbound
event **`STATUS_UPDATED`** (a JSON object), client-initiated ping — implemented in `pystellina/_eio3.py`.
The client still keeps a shape-based fallback (detect status by the `challenge` field) in case a
firmware variant differs. What remains is a one-time confirmation that the live frames match.

On the Stellina Wi-Fi, in order: `stellina doctor` (reachability), `stellina --debug watch` (confirm
event names / payload shapes), then `stellina selftest 52.37 4.90` which runs the whole functional
checklist (connect → status → control → `app/status` → live image → FTP library → export) and prints
PASS/FAIL/SKIP per step. Use `stellina api <endpoint>` to poke anything by hand. If the live shapes
differ from what's parsed, adjust the JSON keys in `observation.py`.

## Legal / License

For interoperability with hardware you own. Not affiliated with or endorsed by Vaonis. The bundled
catalog contains only factual data (designations, coordinates, magnitudes, recommended capture
settings, names/descriptions) extracted from the app; no images are redistributed. MIT licensed.
