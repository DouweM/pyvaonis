# pystellina

Async Python client, CLI, and Home Assistant integration for **Vaonis Stellina** smart
telescopes, over the telescope's **local Wi-Fi API** — no Vaonis cloud, no account.

Reverse-engineered from *Singularity by Vaonis* v1.38.10 (`com.vaonis.barnard`). The full
wire protocol is documented in [`PROTOCOL.md`](PROTOCOL.md); this README is the usage +
developer/agent reference.

> ⚠️ **Status: alpha, validated statically.** The crypto/auth, catalog, astronomy, sequencing,
> and parsing are unit-tested, but the *live* socket.io connection and the exact status/image
> JSON shapes have not yet been confirmed against hardware. See
> [Hardware validation](#hardware-validation) for the 10-minute checklist.

---

## Contents
- [What it can do](#what-it-can-do)
- [How it works](#how-it-works)
- [Install](#install)
- [CLI reference](#cli-reference)
- [Library reference](#library-reference)
- [Targets & "tonight"](#targets--tonight)
- [Live view, stacking & sequences](#live-view-stacking--sequences)
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
- **Sequences**: run an unattended plan ("observe these N targets, then park & shut down").
- **Export & archive**: render full-res TIFF / JPEG-XL of a capture; browse & download the saved
  image library over FTP.
- **Home Assistant**: sensors, binary sensors (incl. "dark enough"), buttons, a "Tonight's
  target" select, a live-view camera, a media-browser source, and an export service.

## How it works

The Stellina is its own Wi-Fi access point at `10.0.0.1` with three services:

| Channel | Endpoint | Use |
|---|---|---|
| REST (OkHttp/Retrofit) | `http://10.0.0.1:8082/v1/` | commands (park, observe, autoinit, export, …) |
| socket.io | `http://10.0.0.1:8083` (`/socket.io`) | live status stream + take/release control |
| HTTP images | `http://10.0.0.1:8082<path>` | live stacked frames + full-res exports (no auth) |
| FTP (anonymous) | `10.0.0.1:21` (`/user/...`) | the saved image library |

Each REST call carries an `Authorization` header: an **Ed25519 (TweetNaCl) signature** over the
rotating `challenge` from the status stream, using keys embedded in the app — computed locally in
[`pystellina/auth.py`](pystellina/auth.py), so no server/account is needed. The `challenge`
changes with every status push, so the client signs each request from the latest status (you must
have a socket.io status before REST calls authenticate).

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

`stellina <command>` (all accept `--ip`, default `10.0.0.1`):

| Command | What it does |
|---|---|
| `status` | Connect and print one status snapshot (read-only) |
| `watch [--seconds N]` | Stream raw socket.io events (discovery/debug) |
| `observing` | Current observation: target, step, stacking count, integration |
| `image [--out f.jpg]` | Download the current live-stacked frame |
| `tonight LAT LON [--require-dark] [--min-grade G] [--limit N]` | Ranked visible objects + dark window |
| `info OBJECT` | Full catalog detail (name, description, magnitude, …) for an object |
| `observe-object OBJECT` | Slew to a catalog object (e.g. `M42`, `"Orion Nebula"`, `Jupiter`) |
| `observe [--object-name … --ra … --de …]` | Slew to explicit coordinates |
| `autoinit LAT LON [--skip-autofocus]` | Initialise / align at a location |
| `stop` / `park` / `shutdown` | Stop observation / park / power off |
| `sequence T1:30 T2:20 … LAT LON [--no-park] [--shutdown] [--wait-for-dark]` | Unattended plan |
| `export CAPTURE_ID [--format tiff\|jxl] [--out f]` | Render & download full-res |
| `library [PATH]` | List the saved FTP library (default `/user`) |
| `download FTP_PATH [--out f]` | Download a saved file |
| `doctor` | Probe reachability (TCP 8082/8083/21, socket.io handshake, FTP) — **run first** |
| `selftest [LAT LON]` | End-to-end checklist: connect → status → control → image → library → export |
| `api ENDPOINT [-X METHOD] [-f k=v …] [-d JSON]` | `gh api`-style raw signed call; prints HTTP status + JSON |
| `post ENDPOINT [--json-body '{}']` / `get ENDPOINT` | Lower-level raw signed REST calls |

Global `--debug` logs wire traffic (socket.io / Engine.IO / aiohttp): `stellina --debug status`.

**Debugging a new telescope** (do this in order):
```bash
stellina doctor                  # is the bridge up? which ports/services respond?
stellina --debug watch           # see raw socket.io events + the status payload shape
stellina selftest 52.37 4.90     # functional pass/fail across every capability
stellina api app/status          # poke any endpoint; e.g. -X POST general/park
```

Example unattended night:
```bash
stellina sequence M42:30 "Andromeda Galaxy:45" Jupiter:10 52.37 4.90 --wait-for-dark --shutdown
```

## Library reference

```python
from pystellina import StellinaClient, ObservationBody, visible_now, get_object, run_sequence, SequenceItem

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
  `start_observation/observe_object/stop_observation`, `park`, `request_shutdown`,
  `switch_frequency`, `current_observation/current_image/recent_images/fetch_current_image`,
  `export_url/export_capture`, `library/download_file`, `post/get/request`. Accepts an existing
  `aiohttp.ClientSession` (HA passes its own) and `on_status(callback)` for push updates.
- `pystellina.catalog` — `load_catalog`, `get_object`, `visible_now`, `CatalogObject`.
- `pystellina.astro` — `is_dark`, `sun_altitude`, `observing_window`, `solar_system_radec`.
- `pystellina.observation` — `ObservationProgress`, `LiveImage`, `recent_images`.
- `pystellina.sequence` — `run_sequence`, `SequenceItem`, `SequenceEvent`.
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

## Live view, stacking & sequences

During an observation the telescope **live-stacks**: one capture accumulates frames over time, so
integration grows as `stacking_count × exposure` — the app's "layering over a longer exposure". A
sequence step is just one observation held for its duration while the scope stacks.

```python
from pystellina import run_sequence, SequenceItem
await run_sequence(
    scope,
    [SequenceItem("M42", 30), SequenceItem("M51", 20), SequenceItem("Jupiter", 10)],
    latitude=52.37, longitude=4.90,
    require_dark=True, wait_for_dark=True, park_at_end=True, shutdown_at_end=True,
    on_event=lambda e: print(e.kind, e.item and e.item.target),
)
```

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

**Entities & services:**
- Sensors: battery, current operation, **current target**, **current step**, **stacked frames**,
  **integration time**.
- Binary sensors: connected, initialised, has control, **dark enough to observe** (with
  `sun_altitude`, `dark_start`, `dark_end` attributes).
- Buttons: take control, park, stop, shut down.
- Select: **Tonight's target** — dark-gated, grade-ranked, includes planets/Moon; selecting starts
  the observation. `suggestions` attribute carries name/altitude/magnitude/constellation/description.
- Camera: **Live view** of the current stacked frame.
- Media source: **Stellina** in the HA media browser — *Recent captures* (live) and *Saved library*
  (FTP), streamed through HA via a proxy view ([`http.py`](custom_components/stellina/http.py)).
- Service: **`stellina.export_capture`** (`capture_id` optional → current; `format` tiff/jxl) — saves
  a full-res image under the HA media directory and returns its path.

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
  sequence.py               # run_sequence (unattended plans)
  ftp.py                    # saved-library browse/download
  cli.py                    # Typer CLI (entry point `stellina`)
  data/catalog.json         # bundled object catalog (regenerate via tools/)
custom_components/stellina/ # HACS integration wrapping pystellina
  __init__.py coordinator.py entity.py config_flow.py
  sensor.py binary_sensor.py button.py select.py camera.py
  media_source.py http.py   # media browser + proxy view
  manifest.json hacs.json strings.json services.yaml const.py
tools/extract_catalog.py    # regenerate data/catalog.json from an APK
tests/                      # pytest (auth, catalog, astro, observation, sequence, ftp, export, models)
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

Two things couldn't be confirmed statically (the one `connect()` method didn't decompile):
1. **socket.io / Engine.IO version** — if `connect` fails, drop `transports=["websocket"]` in
   `StellinaClient.connect()` (EIO3 vs EIO4).
2. **Inbound status event name** — the client auto-detects status by the `challenge` field, so it's
   resilient; confirm payload shapes with `stellina watch`.

On the Stellina Wi-Fi, in order: `stellina doctor` (reachability), `stellina --debug watch` (confirm
event names / payload shapes), then `stellina selftest 52.37 4.90` which runs the whole functional
checklist (connect → status → control → `app/status` → live image → FTP library → export) and prints
PASS/FAIL/SKIP per step. Use `stellina api <endpoint>` to poke anything by hand. If the live shapes
differ from what's parsed, adjust the JSON keys in `observation.py`.

## Legal / License

For interoperability with hardware you own. Not affiliated with or endorsed by Vaonis. The bundled
catalog contains only factual data (designations, coordinates, magnitudes, recommended capture
settings, names/descriptions) extracted from the app; no images are redistributed. MIT licensed.
