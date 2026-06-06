# pyvaonis

Async Python client, CLI, and Home Assistant integration for **Vaonis** smart telescopes
(Stellina, Vespera, …), over the telescope's **local Wi-Fi API** — no Vaonis cloud, no account.

Reverse-engineered from *Singularity by Vaonis* v1.38.10 (`com.vaonis.barnard`), and confirmed
against real hardware (firmware 2.35.7). Reference docs:
- [`CLAUDE.md`](CLAUDE.md) — agent orientation / project handoff (read first for a fresh session)
- [`PROTOCOL.md`](PROTOCOL.md) — the wire protocol (auth, socket, endpoints, catalog model)
- [`docs/API.md`](docs/API.md) — every REST endpoint + body + pyvaonis coverage
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
[`pyvaonis/auth.py`](pyvaonis/auth.py), so no server/account is needed. The `challenge`
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
# from this repo (not yet on PyPI):
pip install "pyvaonis[cli,astro] @ git+https://github.com/DouweM/pyvaonis"
# development:
uv sync --extra cli --extra astro
```

The library ships *inside* the Home Assistant integration at
[`custom_components/vaonis/pyvaonis`](custom_components/vaonis/pyvaonis) — that single copy is both the
importable `pyvaonis` package (hatch builds it as top-level) and what HACS installs from this repo, so
no separate PyPI release is needed.

Extras: `cli` (Typer/Rich), `astro` (`ephem`, for solar-system targets). The core (control,
deep-sky catalog, darkness, images, export, FTP) needs neither.

Run from a machine joined to the telescope's Wi-Fi, or reachable via the [bridge](#wi-fi-bridge-glinet).

## CLI reference

`vaonis <command>` (all accept `--ip`, default `10.0.0.1`). Set once in your environment to avoid
retyping (and to dodge the negative-longitude arg-parsing footgun):

```bash
export VAONIS_HOST=10.0.0.1     # or your bridge IP
export VAONIS_LAT=19.43 VAONIS_LON=-99.13
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

Global `--debug` logs wire traffic (socket.io / Engine.IO / aiohttp): `vaonis --debug status`.

> Negative coordinates: a leading-`-` value (e.g. a western longitude) is read as an option, so
> end option parsing with `--` first: `vaonis forecast -- 19.43 -99.13`.

**Debugging a new telescope** (do this in order):
```bash
vaonis doctor                  # is the bridge up? which ports/services respond?
vaonis --debug watch           # see raw socket.io events + the status payload shape
vaonis selftest 52.37 4.90     # functional pass/fail across every capability
vaonis api app/status          # poke any endpoint; e.g. -X POST general/park
```

Example unattended night (native autonomous plan, starts at dusk):
```bash
vaonis plan M42:30 "Andromeda Galaxy:45" Jupiter:10 52.37 4.90 --wait-for-dark
```

## Library reference

```python
from pyvaonis import VaonisClient, ObservationBody, visible_now, get_object, PlanItem

async with VaonisClient(ip="10.0.0.1") as scope:   # opens socket.io, waits for first status
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
- `VaonisClient` — `connect/disconnect`, `take_control/release_control`, `start_autoinit`,
  `start_observation/observe_object/stop_observation`, `start_plan/stop_plan/plan_progress`,
  `park`, `request_shutdown`, `switch_frequency`,
  `current_observation/current_image/recent_images/fetch_current_image`,
  `export_url/export_capture`, `library/download_file`, `post/get/request`. Accepts an existing
  `aiohttp.ClientSession` (HA passes its own) and `on_status(callback)` for push updates.
- `pyvaonis.catalog` — `load_catalog`, `get_object`, `visible_now`, `visible_tonight`, `CatalogObject`.
- `pyvaonis.astro` — `is_dark`, `sun_altitude`, `observing_window`, `solar_system_radec`.
- `pyvaonis.observation` — `ObservationProgress`, `LiveImage`, `recent_images`.
- `pyvaonis.plan` — `build_plan`, `PlanItem`, `PlanProgress`.
- `pyvaonis.ftp` — `list_dir`, `download`, `FtpEntry`.

## Targets & "tonight"

The app's full catalog is bundled in `pyvaonis/data/catalog.json` — **421 objects** with name +
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
(`pyvaonis.astro.is_dark`); `observing_window()` returns tonight's dusk→dawn.

Regenerate the catalog from an APK you own:
```bash
apktool d -f -s -o out singularity.apk
python tools/extract_catalog.py singularity.apk --strings out/res/values/strings.xml
```

## Live view, stacking & plans

During an observation the telescope **live-stacks**: one capture accumulates frames over time, so
integration grows as `stacking_count × exposure` — the app's "layering over a longer exposure".

The live frame is already written to the scope's disk, so `vaonis image` downloads that file
directly (instant). `--rendered` instead asks the firmware to re-encode the JPEG on demand (what
the app does) — correct, but slow while it is also stacking.

A **plan** is the app's *Plan My Night*: you upload a list of targets each with a time window
(`planner/startPlan`) and the **firmware runs the whole night itself** — auto-initialising, slewing
target-to-target on schedule, advancing when each window ends — so it continues even if the
controller disconnects. `build_plan()` assigns back-to-back windows from a simple `target:minutes`
list:

```python
from pyvaonis import PlanItem
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

- **Off-grid (laptop on the scope's Wi-Fi, no HA):** `vaonis forecast` / `pyvaonis.weather.assess_night()`
  pulls a cloud-cover forecast from Open-Meteo (free, no key — its low/mid/high layers are what matter
  for astro), looks only at tonight's dark window, folds in the Moon (via `ephem`), and gives a
  `good`/`marginal`/`poor` verdict:

  ```bash
  vaonis forecast 52.37 4.90
  # dark window 00:57-01:57 UTC
  # verdict: GOOD — clear: ~0% mean cloud (max 0%); bright Moon up (98%) — hurts faint deep-sky
  ```

- **In Home Assistant: gate on your own weather entity** (don't duplicate it here). If you run the
  [microclimate](https://github.com/DouweM/ha-microclimate) integration, `weather.microclimate` is a
  calibrated ensemble forecast for your exact site plus live PWS conditions (humidity → dew, wind,
  precipitation, `condition`) — far better than a generic API call. `vaonis.run_plan` deliberately
  does **no** weather gating; you gate the automation that calls it (see below).

**Can the whole night run unattended?** Almost entirely — with one hardware caveat:

> ⚠️ **The API can only *shut down*, never power *on*.** Re-powering the board needs the physical
> button. For true turn-on automation, put the scope on a **smart plug** (if it boots when power is
> applied) and have your automation switch it on at dusk; otherwise leave it powered and use `park`
> between nights instead of `shutdown`.

Everything else is automatable. The **`vaonis.run_plan`** service runs a whole night in the
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
      - service: vaonis.run_plan
        data:
          targets: ["M42:30", "Andromeda Galaxy:45", "Jupiter:10"]
          wait_for_dark: true          # schedule the start at the next dusk (Sun below -10°)
      # the firmware runs the night itself and parks at the end; (optional) cut power later
```

`vaonis.run_plan` uploads the native plan and returns immediately (`{started: true}`); the scope
then runs autonomously. `vaonis.stop_plan` cancels it. The same plan is available standalone from
the CLI: `vaonis plan M42:30 M51:20 … LAT LON --wait-for-dark`.

## Full-res export & saved library

- **Export** a finished capture at full resolution: `capture/exportImageTiff` (TIFF) or
  `capture/exportImageJpegXl` (JPEG-XL). Both return a download URL on the telescope's HTTP server;
  `export_capture()` renders and downloads in one call. CLI: `vaonis export <captureId>`.
- **Saved library**: finished observations are stored on the telescope's anonymous FTP under
  `/user/...`. `library()` / `download_file()` browse and fetch them. CLI: `vaonis library`,
  `vaonis download`.

## Home Assistant integration

This repo doubles as a HACS custom integration in
[`custom_components/vaonis`](custom_components/vaonis). It wraps `pyvaonis` and a push-based
`DataUpdateCoordinator` fed by the socket.io stream. **It connects read-only** — it monitors without
taking control, so it coexists with the phone app. Actions are **one-shot**: each control button /
target select / service takes control, acts, and releases it immediately, so the phone can resume
right after (a started observation or plan keeps running regardless). The *Take/Release control*
buttons are a manual override if you want to hold control yourself. (The firmware allows one
controller at a time, so an action fails with a clear message if the phone currently holds control —
release it there first.)

**Install (HACS):** add this repo as a custom repository (category *Integration*), install, restart,
then add the *Vaonis* integration and set the host (default `10.0.0.1`). No PyPI release is needed —
the `pyvaonis` library is bundled in the integration folder; the only external `requirements` HA
installs are `pynacl` and `ephem` (aiohttp/pydantic already ship with HA core).

**Entities & services** (device shows model + firmware version):
- Sensors: **Status** — a one-line human summary in the app's own wording ("M104: 180 stacked
  (30m)", "Initialization: Star pattern analysis (50%)", "tonight — Observation in progress (M51,
  2/5)"), plus **initialization step**, current operation / **target** / **step**, **stacked
  frames** + **total** + **frames acquired** (so you can see the accept/reject ratio),
  **integration time**, **gain**, **exposure**, **latest target** (the object shown in *Latest image*,
  to caption it), **plan state** + **plan target** (during a native plan), **temperature**, **humidity**, **dew-point
  depression**, **storage free**, **Wi-Fi band**, **filter**, **autofocus temperature**,
  **controlling device**.
- Binary sensors: connected, initialised, has control, **dark enough to observe** (with
  `sun_altitude`/`dark_start`/`dark_end`), **tracking**, **defog active**, **firmware update available**.
- Buttons: **initialize** (one-tap auto-init/align — the start of the flow), **observe** (start the
  target picked in the select), take control, **release control**, park, stop, **restart autofocus**,
  **enable multi-night**, shut down. Each is **disabled when it doesn't apply**, mirroring the app's
  own gates (take control only when nobody holds it, release only when HA does, initialize/park only
  when idle, observe only when idle + initialized + a target is chosen, stop/restart-autofocus/
  enable-multi-night only while observing). HA identifies itself to the telescope as **"Home Assistant"** (shown in the
  Singularity app's connected-devices list and the *Controlling device* sensor).
- Switch: **Multi-Light (HDR)** (CovalENS).
- Select: **Tonight's target** — dark-gated, grade-ranked, includes planets/Moon; selecting only
  *picks* the target (no slew) — press **Observe** to start it (browse-then-Observe, like the app).
  Available only when idle. `suggestions` attribute carries name/altitude/magnitude/constellation/
  description.
- Image: **Latest image** — a single `image` entity (telescope stacks are slow stills, not a video
  feed) that always shows the most recent frame: live while observing, otherwise the newest saved
  capture. Attributes: `source` (`live`/`archived`) and `target` (the object); the timestamp is the
  frame's real capture time. Bytes are fetched over HTTP (the `/files` static server — fast and light
  on the scope), not FTP.
- Media source: **Vaonis** in the HA media browser — **one folder per observation** (labelled
  *Object · date*, newest first), each containing its frames **with thumbnails**; a live frame appears
  on top while observing. Bytes are streamed through HA via a proxy view — fetched over HTTP (light on
  the scope) and capped to a few concurrent fetches so a wall of thumbnails can't overwhelm it (the
  raw FTP storeId/`images` nesting and `*.json` metadata are hidden).
- Services: **`vaonis.observe`** (slew to any catalog object), **`vaonis.autoinit`** (the Initialize
  button's scriptable form — adds explicit `latitude`/`longitude` and `skip_autofocus`),
  **`vaonis.adjust_framing`** (Change Framing) and **`vaonis.set_camera_params`**
  (live gain/exposure/saturation, both safe mid-observation), **`vaonis.run_plan`** /
  **`vaonis.stop_plan`** (start/cancel the native autonomous plan), **`vaonis.export_capture`**
  (save a full-res image to the HA media dir). `autoinit`/`run_plan` default their location to the
  telescope's own last-known position, falling back to Home Assistant's configured home location.

Observation-, plan- and init-specific sensors (target, step, frames, gain/exposure, plan state, …)
report **Unavailable** when the scope is idle rather than a misleading "Unknown" — they come back the
moment the relevant operation starts. The always-meaningful ones (Status, temperature, humidity,
storage, Wi-Fi band, filter) stay populated.

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

**Which side is WAN?** Because the scope is an AP, the Mango must *join* it as a Wi-Fi **client** —
so the **Stellina sits on the Mango's WAN (Wi-Fi/`wwan`) side**, and your **UDM/LAN connects to the
Mango's Ethernet (LAN) side**. You don't bridge at L2; you **route** to `10.0.0.1` through the Mango,
which NATs LAN→Stellina (the default direction, so no custom firewall rules).

**Recommended (fixed install on a UniFi UDM + IoT VLAN):**
1. Power on the Stellina (`STELLINA-xxxx`). **Mango → Internet → Repeater →** join its SSID (enter a
   password only if set). The Mango's `wwan` gets `10.0.0.x` with gateway `10.0.0.1`.
2. **Mango LAN → IoT VLAN.** Give the Mango's **LAN** a **static IP on your IoT VLAN**
   (e.g. `10.3.142.50/24`, gateway = the UDM IoT gateway) and **disable the Mango's DHCP server** (so
   it doesn't fight UniFi). Plug the Mango's **LAN** (not WAN) port into a UDM access port on that VLAN.
3. **UDM → static route:** `10.0.0.0/24` → next hop `10.3.142.50`. That's the whole bridge — your
   network now reaches `10.0.0.1` via the Mango, which NATs to the Stellina. All three services work
   over the route (no per-port forwarding).
4. **Return route on the Mango** (don't skip — replies die without it): the Mango's only default
   route is the Stellina (`10.0.0.1` via the repeater), so replies to HA on another VLAN would go to
   the scope. Add a route so it sends them back to the UDM (LuCI → Static Routes, or SSH):
   ```sh
   uci add network route; uci set network.@route[-1].interface='lan'
   uci set network.@route[-1].target='10.3.0.0'; uci set network.@route[-1].netmask='255.255.0.0'
   uci set network.@route[-1].gateway='10.3.142.1'   # supernet covering your VLANs → UDM
   uci commit network && /etc/init.d/network restart
   ```
5. **FTP (saved library)** needs nothing extra here — **confirmed on hardware**: pyvaonis uses
   passive mode and ignores the scope's advertised `10.0.0.1` (like `curl --ftp-skip-pasv-ip`), so the
   data connection is just another routed+masqueraded outbound; REST (8082) / socket (8083) likewise.
   You only need the Mango's FTP conntrack helper for the *port-forward* variant or active FTP — not
   this route-based path.
6. Point HA / pyvaonis at **`10.0.0.1`** (`VAONIS_HOST=10.0.0.1`). Test with `vaonis doctor`,
   `vaonis watch`, then `vaonis library` (lists your `/system/captures` runs).

**Bridge gotchas (learned bringing one up):**
- **UniFi port must be the IoT VLAN as *native/untagged*, not "tagged."** The Mango's LAN sends
  untagged frames; a tagged-only port drops them. Quick L2 check: from the Mango, `ping 10.3.142.1`
  (the UDM) — works ⇒ VLAN/L2 fine, problem is elsewhere; fails ⇒ fix the native VLAN.
- **OPEN-network repeater flakiness:** the MT300N-V2's legacy MediaTek `apcli0` driver can associate
  but never DHCP on an OPEN AP. If a firmware factory-reset doesn't fix it, give `wwan` a **static IP**
  (`10.0.0.50/24`, gw `10.0.0.1`). (A reset fixed it on the test unit.)
- Disable the Mango's **LAN DHCP** (Interfaces → LAN → DHCP Server → *Ignore interface*) so it
  doesn't fight UniFi.

**Always-on Mango, scope only on when in use:** repeater mode **remembers the SSID and
auto-reconnects** — configure it once (Stellina on), and the Mango rejoins `STELLINA-xxxx` by itself
whenever the scope powers up. No API/manual trigger. Expect a **~1–2 min warm-up** (the scope takes
~30 s to raise its AP, then the Mango needs a scan cycle). The Mango stays online/manageable on its
Ethernet the whole time; while the scope is off, calls to `10.0.0.1` fail and pyvaonis reports a
clean "unreachable" error (and HA's `connected` binary sensor reflects the real link, so you can gate
automations on it).

**Alternative (port-forward):** instead of the UDM static route, you can DNAT on the Mango —
`TCP 8082/8083/21 → 10.0.0.1` — and point pyvaonis at the Mango's IP. Matches the classic "GL.iNet +
Ethernet WAN + port-forward" pattern, but passive FTP across the forward is fiddly, so the route-based
recipe above is cleaner here.

**Caveats:** single-radio 2.4 GHz halves throughput (fine for control/status, slower for image/FTP —
a dual-band GL-SFT1200 "Opal" ~$40 / GL-A1300 "Slate Plus" ~$70 avoids it); keep the scope on 2.4 GHz
(don't `switch_frequency` to 5 GHz or the Mango can't follow); one controller at a time.

## Project layout

```
custom_components/vaonis/   # HACS integration
  __init__.py coordinator.py entity.py config_flow.py
  sensor.py binary_sensor.py button.py select.py switch.py image.py
  media_source.py http.py   # media browser + proxy view
  manifest.json hacs.json strings.json services.yaml const.py
  pyvaonis/                 # the bundled library (also the importable `pyvaonis` package)
    const.py                # IP, ports, endpoints, URL helpers
    auth.py                 # Ed25519 challenge → Authorization header (embedded keys)
    models.py               # VaonisStatus, ObservationBody, AutoInitBody
    client.py               # VaonisClient: socket.io + REST + images + export + ftp
    catalog.py              # bundled catalog, get_object, visible_now
    astro.py                # sun position, is_dark, observing_window, ephemerides
    observation.py          # ObservationProgress, LiveImage, recent_images
    plan.py                 # native Plan-My-Night: build_plan + PlanProgress
    weather.py              # cloud forecast (Open-Meteo) + Moon -> night verdict
    ftp.py                  # saved-library browse/download
    _eio3.py                # minimal Engine.IO v3 / Socket.IO v2 websocket client
    cli.py                  # Typer CLI (entry point `vaonis`)
    data/catalog.json       # bundled object catalog (regenerate via tools/)
tools/extract_catalog.py    # regenerate the catalog.json above from an APK
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
  a method on `VaonisClient` (sign via `self.post/get`). Mirror `StellinaAPI` in `PROTOCOL.md §4`.
- **New status field**: it's already in `status.raw`; add a typed accessor to `VaonisStatus` or a
  parser in `observation.py`. Status uses `extra="allow"`, so unknown fields are preserved.
- **New HA entity**: add a platform file using `VaonisEntity` (device wiring) + the coordinator;
  register the `Platform` in `__init__.py` and a name in `strings.json`.
- **Solar-system math** lives in `astro.py` (`ephem`); deep-sky geometry is pure-Python there too.

### Other Vaonis models (Vespera, …)

The whole Vaonis lineup is driven by one Singularity SDK (`com.vaonis.instruments.sdk`), so the
**protocol is shared** — same REST endpoints, Engine.IO v3 socket + `STATUS_UPDATED`, Ed25519 auth,
and `10.0.0.1:8082/8083` defaults — and `pyvaonis` should **connect, monitor, and control** any of
them (`stellina`, `vespera`, `vespera1ed`, `vespera2`, `vespera3`, `vesperapro`, `vesperapro2`, …).
Device/UI labels already resolve from `status.model` (`model_display_name`).

**Only Stellina is hardware-validated.** The one model-specific piece is **capture tuning**: the
bundled catalog's gain/exposure and `catalog._SOLAR_PARAMS_STELLINA` are Stellina's values, so on
another model observations would run with valid-but-not-optimal exposures. The clean extension point
is the app's `assets/catalog/observation_rules.json`, keyed by `(model, filter, objectType,
objectId)` — bundle it and derive params from `status.model` at runtime. Worth verifying on real
non-Stellina hardware first: still Engine.IO **v3** (not v4), the `customPort` flag in
`StellinaContext`, and that the embedded auth keys are shared.

## Hardware validation

The socket protocol is now pinned from the decompiled app: **Engine.IO v3** over websocket, inbound
event **`STATUS_UPDATED`** (a JSON object), client-initiated ping — implemented in `pyvaonis/_eio3.py`.
The client still keeps a shape-based fallback (detect status by the `challenge` field) in case a
firmware variant differs. What remains is a one-time confirmation that the live frames match.

On the Stellina Wi-Fi, in order: `vaonis doctor` (reachability), `vaonis --debug watch` (confirm
event names / payload shapes), then `vaonis selftest 52.37 4.90` which runs the whole functional
checklist (connect → status → control → `app/status` → live image → FTP library → export) and prints
PASS/FAIL/SKIP per step. Use `vaonis api <endpoint>` to poke anything by hand. If the live shapes
differ from what's parsed, adjust the JSON keys in `observation.py`.

## Legal / License

For interoperability with hardware you own. Not affiliated with or endorsed by Vaonis. The bundled
catalog contains only factual data (designations, coordinates, magnitudes, recommended capture
settings, names/descriptions) extracted from the app; no images are redistributed. MIT licensed.
