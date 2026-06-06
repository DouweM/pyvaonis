# pyvaonis — agent orientation

Reverse-engineered local API client + CLI + Home Assistant integration for **Vaonis** smart telescopes (validated on **Stellina**). This file is the fast-start for a fresh agent session. Read it first, then
`README.md` (usage), `PROTOCOL.md` (wire protocol), `docs/API.md` (every endpoint), `docs/STATUS.md`
(status schema).

## Status: WORKING against real hardware ✅
Validated end-to-end on **firmware 2.35.7** (scope id `stellina-f8bd80`, owner DouweM, Mexico City):
EIO3 socket + `STATUS_UPDATED`, Ed25519 auth (`app/status` → 200), take-control, live stacked-image
fetch (real M104 JPEG), `observing`, FTP. Repo: `github.com/DouweM/pyvaonis` (**public**, so HACS can
install it as a custom repo without a PyPI release), CI green (lint/typecheck/test/hassfest). Push to
`main` directly (personal repo). The `pyvaonis` library is **bundled inside the integration** at
`custom_components/vaonis/pyvaonis/` (no PyPI dependency); `manifest.json` requirements are just
`pynacl`+`ephem` (aiohttp/pydantic ship with HA core).

## Hardware & network facts (firmware 2.35.7)
- Telescope is **always its own Wi-Fi AP at `10.0.0.1`** (no station mode). Three services:
  - REST `http://10.0.0.1:8082/v1/` — commands. Every call needs `Authorization` = Ed25519/TweetNaCl
    signature over the rotating `challenge` (keys embedded in the app; reproduced in `pyvaonis/auth.py`).
  - **Socket.IO v2 / Engine.IO v3** (`EIO=3`) on `:8083`. python-socketio can't do EIO3 → we ship our own
    `pyvaonis/_eio3.py` (aiohttp websocket). Inbound status event = **`STATUS_UPDATED`** (a JSON object,
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

## Bridge to the LAN (built & working ✅)
Scope is AP-only → needs a Wi-Fi bridge. GL.iNet **GL-MT300N-V2 "Mango"** in **repeater mode**
joins the Stellina AP (Stellina = Mango's WAN/`wwan`, 10.0.0.x). The Mango's **LAN (Ethernet)** gets a
**static IP on the oasys IoT VLAN (10.3.142.50/24), DHCP off**, plugged into a UDM IoT port. **UDM
static route 10.0.0.0/24 → 10.3.142.50**; the Mango NATs LAN→Stellina (default), so all ports route
(no per-port forward). HA is on the services VLAN (10.3.127.x). FTP usually works as-is on this route
path (passive + skip-pasv-ip); only enable the Mango FTP conntrack helper if `vaonis library` stalls
(it's really needed for the port-forward/DNAT variant). Repeater **auto-reconnects** when the scope
powers on (~1-2 min warm-up; no API trigger); the
Mango stays up on Ethernet meanwhile. Full recipe in `README.md` → "Wi-Fi bridge". Brought up
end-to-end (status/library/image over the route; gotcha: the UniFi port must carry the IoT VLAN as
**native/untagged**, and the Mango needs a return route `10.3.0.0/16 → 10.3.142.1`).

## Repo layout
```
custom_components/vaonis/  HACS integration (coordinator/entity/config_flow/sensor/binary_sensor/
                       button/select/switch/image/media_source/http + manifest/hacs/strings/services.yaml)
custom_components/vaonis/pyvaonis/   the bundled library — ALSO the importable `pyvaonis` package
                       (single source of truth). const.py auth.py _eio3.py models.py client.py
                       catalog.py astro.py observation.py plan.py weather.py ftp.py cli.py
                       data/catalog.json. HA files import it relatively (`from .pyvaonis ...`);
                       hatch builds it as top-level `pyvaonis` (so the CLI/tests still `import pyvaonis`).
tools/extract_catalog.py     regenerate custom_components/vaonis/pyvaonis/data/catalog.json from an APK
tests/                 pytest (auth, catalog, astro, observation, plan, ftp, export, safety,
                       weather, models, eio3)  — 59 tests
docs/API.md            complete endpoint reference + pyvaonis coverage
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
  set_camera_params, enable_multi_night (setToBeResumable)**, **mosaic** (deep-sky Advanced obs. —
  `observe_object(mosaic=(w°,h°))` → `startObservation.mosaic={widthDegree,heightDegree}`; firmware
  tiles), **multi-night resume** (`resume_capture`/`stored_captures`/`delete_stored_capture` over
  `captureStore/*`), **native plan (`start_plan`/`stop_plan`/`plan_progress`)**, full-res export
  (tiff/jxl), FTP library, live image + recent images, catalog + "tonight" visibility, weather verdict
  (Open-Meteo), darkness/observing window, ephemeris (planets/Moon), **diagnostics**
  (`consume_logs` = `logs/consume`, `available_reports` = `reporter/getAvailableReports`; CLI `logs`/
  `reports`; HA `diagnostics.py` "Download diagnostics" = status + reports, location/ids redacted).
- **Not yet** (full coverage audit done; bodies mapped — see `docs/API.md`): **manual focus**
  (`setUserParams` `MAP` int = focus *motor position* ~0–206000, the app's "Focus" slider — NOT
  "multi-accumulation"; universal, useful), playlist (`playlist/startPlaylist` — RANDOM shuffle),
  **darkManager** (`generateDark`/`stopGenerateDark`, no body — but **Vespera Pro/Pro2 ONLY**, fw
  ≥2.32; needs lens cap on, ~30min, 3 steps BIAS/DARK/MASTER; "use" is a separate `enableDarkUsage`
  setting; **not available on Stellina**), `planner/getPlanObservation`, `general/openForMaintenance`,
  storage browse/delete, expertMode raw acquisition, sun/eclipse mode (`sun/*`, solar-filter-only —
  behind allow_solar). Priority: manual focus, playlist, getPlanObservation; darkManager only matters
  for Vespera Pro owners. (User skipped manual focus + darkManager.)
- **Safety-gated** (never auto-run): firmware upload (blocked), delete/reset (`allow_unsafe`),
  solar/sun-near (`allow_solar`). See `client._guard_endpoint`. Full map in `docs/API.md`.

## Dev workflow
`uv sync --extra cli --extra astro` then `uv run ruff check . && uv run ruff format --check . &&
uv run pyright && uv run pytest`. CI mirrors that + hassfest. On-hardware test order (laptop on the
Stellina Wi-Fi): `vaonis doctor` → `vaonis --debug watch` → `vaonis selftest <lat> <lon>`.
Commit messages end with the Co-Authored-By trailer; bundle related changes; keep docs current.

## Related repos (separate, in ~/dev) — touched this project
- `ha-microclimate` — user's weather integration. We added `cloud_coverage`/`native_dew_point` to its
  hourly Forecast (committed+pushed). HA gates "good sky watching" on `weather.microclimate`.
- `oasys/home/homeassistant` — HA config; `binary_sensor.good_sky_watching` created via **ha-sync**
  (helpers/template/binary_sensor/), NOT configuration.yaml. Manage HA via the `ha-sync` CLI.

## Roadmap / TODO
- **Web UI** (next big thing): offline-first, mobile+laptop. FastAPI over pyvaonis + Vite/React/TS
  (matches oasys `welkom/spion`). Explore/Favorites(local)/Manual/Plan from the bundled catalog;
  Live view + controls + gallery from the scope. Data model in `PROTOCOL.md` → "Catalog & browsing".
  Step 1: re-extract catalog keeping `distance/realSize/discoveredBy` + bundle `catalog_object/*.png`
  (lowercased id, rotate 90°) + `constellations.json`.
- HA: media-source presents **one folder per observation** (label `<Object> · <date>` via
  `_observation_label`; newest first) with a lazy **cover thumbnail** (proxy `cover` kind → newest
  frame of that obs via `client.observation_frames`); frames inside have NO thumbnail (each would be a
  slow FTP download) — click to view. Live frame on top while observing; storeId/`images` + `*.json`
  hidden. `run_plan`/`stop_plan` now drive the native
  Plan-My-Night (`client.start_plan`/`stop_plan`). Single **image** entity (`VaonisImage`, Platform.
  IMAGE — slow stills, not a camera/video feed): live frame while observing, else newest capture via
  `client.latest_capture()`; `source` (live|archived) + `target` attrs; `image_last_updated` = the
  frame's real time (live=now, archived=MDTM). **Image bytes come over FTP** — VERIFIED on hardware
  that the `/files` HTTP server only renders the *live* capture (a GET of an archived frame hangs);
  `file_http_url` exists but is unused. FTP is slow (per-call connect+login + a PASV data connection
  over the bridge, ~seconds each; connection pooling didn't help — the data connection dominates). So
  the shared **`MediaCache`** (`media_cache.py`: in-session memory LRU 64 + on-disk
  `<config>/vaonis_media_cache/`, persists across restarts, pruned to ~1500 files) serves all frames,
  and the proxy **caps** concurrent fetches (`asyncio.Semaphore(4)`). **Proactive capture download**
  (mirrors the app, which keeps local phone copies): the image entity already downloads each live
  frame during an observation, so it persists those bytes into the MediaCache under
  `frame_key(entry_id, ftp_path)` — the exact key the browser/proxy use — for FREE (no extra fetch),
  so that observation's gallery is instant. Old observations are still cached lazily on first view.
  (Link is ~25 KB/s over the bridge — a 224 KB frame ≈ 9 s; HTTP `/files` and FTP are equal speed, so
  we keep FTP. There is NO telescope thumbnail endpoint.) MLSD has no `modify` → timestamp via **MDTM**.
  **Latest target** sensor reads `coordinator.latest_target`, which the image entity sets from the
  live obs or the archived storeId (`observation_object_name`). The config entry is titled after the
  telescope's own `telescopeName` (e.g. "Stellina"), set in setup + config_flow. Observation/plan/init
  sensors use `available_fn` to report **Unavailable** (not "Unknown") when idle; settables that can't
  take effect mid-observation are disabled while busy (Mosaic switch + width/height numbers = start-time
  geometry), while ones that can stay enabled (Multi-night → setToBeResumable; BalENS = safe setting).
  Enum sensors are humanised (`operation`/`plan_state` via labels.py; `band`/`filter` via code maps),
  floats get `suggested_display_precision`, device carries `serial_number`+`configuration_url`.
  `autoinit`/`run_plan`
  default location to `client.location()` (scope's own position) → HA home fallback. Entity names load
  from `translations/en.json` (NOT just strings.json — custom integrations need the translations dir).
  **Two-step observe**: the Tonight's-target select only *picks* (stores `coordinator.selected_target`,
  no slew); the **Observe** button starts it (browse-then-Observe like the app). **Initialize** button
  = one-tap auto-init. **Advanced observation is UI-native**: `switch.mosaic`/`switch.multi_night`
  (local CONFIG toggles, RestoreEntity → `coordinator.mosaic_enabled`/`multi_night_enabled`) +
  `number.mosaic_width`/`mosaic_height` (°, RestoreNumber → coordinator); the Observe button reads
  these and passes `mosaic`/`multi_night` to `observe_object`. **Resume** button + `vaonis.resume`/
  `delete_capture` services + "Multi-night captures" sensor (lists `storedCaptures`). Buttons/select gate their `available` on live state to mirror the app's exact
  enable conditions (take-control only when `masterDeviceId==null`, release only when we're master,
  park only idle+not-parked, observe only idle+initialized+target-chosen, stop/refocus/multi-night
  only while observing, enable-multi-night also needs ≥1 stacked frame and not-already-resumable).
  EntityCategory: observing **activity/telemetry & actions = primary controls** (status, observe/stop/
  init buttons, target select, image); **settings = CONFIG** (BalENS switch + level select, mosaic/
  multi-night toggles + mosaic-size numbers); **device health/info = DIAGNOSTIC** (env, storage, band,
  filter, control state). Controlling-device sensor shows "Nobody" when `masterDeviceId` is null. The
  `band`/`filter` sensors are ENUM (device_class) with `state` translations so they read "2.4 GHz" /
  "No filter" not raw enums. **BalENS** = the app's HDR-background processing (`enableHdrBackground` +
  `algoHdrBackground` level RECOMMENDED/SOFT/HARD/OLD; OLD = "First Edition"); switch/select/CLI
  (`balens-level`) all route through `coordinator.run_action` (settings need control — HA is read-only). The Connectivity
  binary_sensor is `always_available` (reports **off** when the scope is unreachable instead of going
  Unavailable like everything else). Coordinator has a 30s watchdog `update_interval` that reconnects
  after the scope is powered back on (cheap no-op while connected) — so entities recover without a reload.
  **No blocking I/O on the loop**: `load_catalog()` (cached ~0.5 MB JSON read) is warmed via
  `async_add_executor_job` in `async_setup_entry` before platforms load; FTP (`ftp.list_dir`/`download`)
  runs via `asyncio.to_thread`; the image entity fetches frames in a background task and caches them
  (so `async_image` returns instantly, under HA's 10 s image-proxy timeout).
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
- **CLI is one control-session per process**: each `vaonis <cmd>` connects → `take_control` →
  acts → `disconnect()` which **releases control**. So `vaonis stop` then `vaonis observe` are
  two separate take/release cycles (control drops between them; the phone could grab it back). For a
  held multi-step session use ONE Python `async with VaonisClient()` block. (A native `plan` does
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
  from arg→env(`VAONIS_LAT/LON`)→scope (shows observatory name); host `VAONIS_HOST`; times local;
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
  releasing does NOT stop a started observation/plan — the scope runs on autonomously. **HA**
  connects **read-only** (coordinator does not take control) so it coexists with the phone; actions
  are one-shot via `coordinator.run_action` (take → act → release), so HA never holds control (the
  Take/Release buttons are a manual override). Contention surfaces as a clean HomeAssistantError.
- Catalog (`tools/extract_catalog.py`) now also bundles the app's object-card data: distance/unit,
  realSize/unit, discoveredBy/In, shortTitle, resolved categoryLabel + constellationName, and the
  per-object `trivia` fun-facts (from `objects_<id>_trivia`). Surfaced in `vaonis info` (a card),
  `tonight` (recommended minutes + visibility dot), `CatalogObject.summary()`, and HA select
  suggestions. `duration` = recommended observation minutes (0 ⇒ none, e.g. planets).
- `set_multi_light` now also sends the two non-nullable SettingsBody fields with firmware defaults
  (`buttonBrightness=MEDIUM`, `algoHdrBackground=RECOMMENDED`) when status omits them.
- Native plan body = `PlanBody`/`PlanTargetBody` (Moshi `PlanMyNightBody`): per-target
  `startTime`/`endTime` epoch-ms windows + `params` (a full `ObservationBody`); `build_plan` lays
  them back-to-back from `target:minutes`. Plan status is `currentOperation.type=="PLAN"` with a
  `state` + `targets[].storeState=="OBSERVING"` marking the live target.
