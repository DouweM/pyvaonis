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
                       observation.py plan.py weather.py ftp.py cli.py  data/catalog.json
custom_components/stellina/  HACS integration (coordinator/entity/config_flow/sensor/binary_sensor/
                       button/select/camera/media_source/http + manifest/hacs/strings/services.yaml)
tools/extract_catalog.py     regenerate data/catalog.json from an APK
tests/                 pytest (auth, catalog, astro, observation, plan, ftp, export, safety,
                       weather, models, eio3)  — 59 tests
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
- **Implemented** (client + CLI): connect/status stream, take/release control (waits for the
  status echo), autoinit, observe (catalog/manual, `replace=` stop-and-take-over), stop, park,
  shutdown, switch_frequency, **in-observation: adjust_framing, restart_autofocus, set_multi_light,
  set_camera_params, enable_multi_night (setToBeResumable)**, **native plan (`start_plan`/`stop_plan`/
  `plan_progress`, `planner/startPlan`)**, full-res export (tiff/jxl), FTP library, live image
  (`image` defaults to the fast static-file fetch; `--rendered` for on-demand) + recent images,
  catalog + "tonight" visibility (`visible_now` + `visible_tonight` over the dark window), weather
  verdict (Open-Meteo), darkness/observing window, ephemeris (planets/Moon).
- **Not yet**: playlist (`playlist/startPlaylist`), expertMode raw acquisition, sun/eclipse mode,
  captureStore resume (`startObservationFromStoredCapture`/`getObservation`/`deleteStoredCapture`),
  logs/consume + reporter, mosaic (`StartObservationBody.mosaic`), darkManager. Bodies for all of
  these are mapped in the dig notes; see `docs/API.md`.
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
- HA: media-source over `/files`/FTP `/system/captures`; `run_plan`/`stop_plan` now drive the native
  Plan-My-Night (`client.start_plan`/`stop_plan`).
- Next API surfaces (bodies mapped): captureStore resume, playlist, sun/eclipse, expert raw, mosaic.

## Control / observation ordering (mirrors the app — verified in decompiled source)
The app gates every REST command on `connectedAsMaster`, which only flips true when a
`STATUS_UPDATED` shows `masterDeviceId == deviceId`. So control is confirmed *via the status
stream*, not the socket emit. Mirror this:
- `take_control(wait=True)` (default) emits `takeControl`+`setUserName` then **blocks until the
  status stream confirms `masterDeviceId` is us** (`_wait_for_status`). Without this, the next
  command races the echo and fails `_require_control` ("does not hold control"). Returns early if
  already master.
- The app **refuses** `startObservation` while `currentOperation != null` (it does *not* auto-replace).
  `start_observation`/`observe_object` default to that refusal; pass **`replace=True`** to stop a
  running *observation* and `_wait_idle()` (status shows `currentOperation` cleared) before starting
  the new target. A `stopObservation` POST returns before the op actually clears, hence the wait.
  CLI `observe`/`observe-object` default `--replace` ON; the HA observe/select use it. A **native
  plan** (`start_plan`) is separate: it is refused unless idle (no auto-replace) and the firmware
  runs it autonomously (auto-init + scheduled targets), surviving disconnect.
- App preconditions for startObservation (all enforced): not shuttingDown, connected,
  connectedAsMaster, not blocked, `currentOperation == null`, `initialized == true`.
- **CLI is one control-session per process**: each `stellina <cmd>` connects → `take_control` →
  acts → `disconnect()` which **releases control**. So `stellina stop` then `stellina observe` are
  two separate take/release cycles (control drops between them; the phone could grab it back). For a
  held multi-step session use ONE Python `async with StellinaClient()` block. (A native `plan` does
  not need a held session at all — the firmware runs it.)

## Gotchas
- It's EIO3, not python-socketio. Status event is `STATUS_UPDATED`; observation under `currentOperation`.
- Use a stable `device_id` (default is MAC-derived) or `connectedDevices` piles up.
- `app/setSettings` may replace rather than merge — `set_multi_light` echoes current settings to be safe.
- `take_control` waits for the status echo; if you ever call it `wait=False`, don't issue a command
  in the same tick.
- Live `image` is slow only via the on-demand render (`?androidImageIndex=&androidCaptureId=` makes
  firmware re-encode the JPEG). The frame is already on disk, so `image` defaults to the static file
  (`LiveImage.ftp_path` over FTP, or `static_url` = the bare path with no query). Same bytes, instant.
- On connect the client emits `setSystemTime` (epoch ms) to sync the scope's clock, as the app
  does — required because native plan windows are absolute epoch-ms and the firmware compares to its
  own clock.
- Auto-init verified faithful: `AutoInitBody` = `{longitude, latitude, time(epoch ms), observatoryId
  (""), observatoryName, skipAutoFocus(false)}` — exact match to the app; `observatoryId=""` is
  accepted. Init is required before `startObservation` (`initialized==true`); a native plan runs its
  own auto-init (plan `state==AUTO_INIT`). Exposed as CLI `autoinit` + HA `autoinit` service. Init
  step order (status `currentOperation` type AUTO_INIT): PREPARE_MC_BOARD→SEEK_STOP→OPEN_ARM→
  TRY_POSITION→MOVING→WIDE_AUTOFOCUS→ASTROMETRY→START_TRACKING→AUTO_FOCUS (retry path on astrometry
  fail). The arm is opened by init itself; no separate open step.
- CLI ergo: `observe TARGET` (catalog) or `observe --ra/--de` (manual) — `observe-object` merged in;
  `stop` stops a plan if one runs else the observation; commands grouped into --help panels; location
  from arg→env(`STELLINA_LAT/LON`)→scope (shows observatory name); host `STELLINA_HOST`; times local;
  expected errors print one line (`--debug` for trace). `image` defaults to the fast static fetch.
- **startObservation params are rule-derived (to-the-letter with the app), or the firmware 500s**
  (`CHECKPARAMS` for missing histogram*/background*). `catalog.to_observation`: deep-sky types
  {OP,ODC,ODE,ODOC} → `doStacking=true` + the six histogram/background params (per-object from the
  catalog, else per-type rule defaults: histogramLow OP 0/ODC -0.75/ODE -1/ODOC -1, medium 5, high 0,
  backgroundEnabled true, backgroundPolyorder 2). Non-stacking (stars/untyped) → `doStacking=false`,
  omit the six. **Solar** (planets/Moon/Sun) → `doStacking=false`, **no ra/de/rot** (firmware
  resolves), per-planet gain/exp overrides (`_SOLAR_PARAMS_STELLINA`); near-Sun guard moved to
  `observe_object`. `algorithm=AUTO` (not DEEP_SKY), `brightZoneOffset` omitted for AUTO,
  `targetType=CATALOG|MANUAL`, `observationType=STANDARD`. Source: app `observation_rules.json` +
  `CatalogObservationRule.mergeRules`/`getStackingParams` + `ObservationLauncher` solar-vs-deep-sky.
- Control can't be stolen: the firmware refuses `takeControl` while another device is master+connected
  (silent `CONTROL_ERROR`; the app disables its take-control button then). `take_control` raises a
  clear "release it on the other device" error on timeout. Our per-command flow (connect →
  take_control → act → disconnect) releases control on exit (and the socket close also frees it);
  releasing does NOT stop a started observation/plan — the scope runs on autonomously.
- Catalog (`tools/extract_catalog.py`) now also bundles the app's object-card data: distance/unit,
  realSize/unit, discoveredBy/In, shortTitle, resolved categoryLabel + constellationName, and the
  per-object `trivia` fun-facts (from `objects_<id>_trivia`). Surfaced in `stellina info` (a card),
  `tonight` (recommended minutes + visibility dot), `CatalogObject.summary()`, and HA select
  suggestions. `duration` = recommended observation minutes (0 ⇒ none, e.g. planets).
- `set_multi_light` now also sends the two non-nullable SettingsBody fields with firmware defaults
  (`buttonBrightness=MEDIUM`, `algoHdrBackground=RECOMMENDED`) when status omits them.
- Native plan body = `PlanBody`/`PlanTargetBody` (Moshi `PlanMyNightBody`): per-target
  `startTime`/`endTime` epoch-ms windows + `params` (a full `ObservationBody`); `build_plan` lays
  them back-to-back from `target:minutes`. Plan status is `currentOperation.type=="PLAN"` with a
  `state` + `targets[].storeState=="OBSERVING"` marking the live target.
