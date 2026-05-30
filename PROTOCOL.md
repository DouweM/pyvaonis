# Stellina / Vaonis (`com.vaonis.barnard`) — reverse-engineered control protocol

Decompiled from **Singularity by Vaonis v1.38.10** (the current unified app; package
`com.vaonis.barnard`). All info below comes from `com.vaonis.instruments.sdk.*` in the
APK (jadx decompile). Nothing here is from Vaonis docs.

> Status: **statically derived, not yet validated against hardware.** Two details must be
> confirmed on the real telescope (marked ⚠️ below): the socket.io protocol version and the
> exact event name that carries status.

## 1. Network architecture

- The telescope is **always its own Wi-Fi access point**. It hands out `10.0.0.1` to itself.
- There is **no "join my home Wi-Fi" / station mode** in the API. The only network command is
  `network/switchFrequency`, which just flips the AP between 2.4 GHz and `BAND_5_GHZ`
  (`NetworkBody { band }`, `StellinaNetwork.use5GHz()` checks `band == "BAND_5_GHZ"`).
  → To reach it from your LAN you need a **bridge/repeater**, not a config command (see §6).

Three services run on `10.0.0.1`:

| Service        | Port  | Purpose |
|----------------|-------|---------|
| REST (OkHttp/Retrofit) | **8082** | All commands. Base URL `http://10.0.0.1:8082/v1/` |
| socket.io      | **8083** | Live status stream + control acquisition |
| FTP (anonymous)| 21    | Download captured images (`/user/...`, see community `air01a/vaonis-sync`) |

Constants (`StellinaContext.kt`):
```
DEFAULT_IP          = "10.0.0.1"
DEFAULT_HTTP_PORT   = 8082          baseUrl = "http://%s:%s%s/v1/" % (ip, httpPort, httpRoot="")
DEFAULT_SOCKET_PORT = 8083          socketUrl = "http://%s:%s" % (ip, socketPort)
DEFAULT_SOCKET_IO_PATH = "/socket.io"
```
(There are also dev/simulator contexts, e.g. `ursa.dev.vaonis.com` with `/stellina/http` &
`/stellina/socket` paths — useful to know the path layout, not needed for the real device.)

## 2. Socket.IO channel (status + control) — port 8083

**Socket.IO v2 / Engine.IO protocol v3 (`EIO=3`)** — confirmed from the decompiled
`StellinaSocketV2.connect()` (recovered via smali) and `io/socket/engineio/client/Socket.smali`,
which hardcodes `EIO=3`. This matters: `python-socketio` 5.x / `python-engineio` 4.x speak only
`EIO=4` and **cannot** connect (you get a link but never any events) — hence `pystellina` ships its
own tiny EIO3 client (`pystellina/_eio3.py`).

Connect: websocket to `ws://10.0.0.1:8083/socket.io/?EIO=3&transport=websocket&<query>`, default
namespace, query `id=<deviceId>&name=<deviceName>&countryCode=<cc>`, connect timeout 8 s. Heartbeat
is **client-initiated** (client sends `2` ping every `pingInterval`; server replies `3`). The app
uses polling-first then upgrades; websocket-direct also works.

**Outbound** (client → telescope) — all via `emit("message", <key>[, <value>])`:
| key            | value                              | meaning |
|----------------|------------------------------------|---------|
| `takeControl`  | —                                  | become master (forcibly demotes the current controller) |
| `releaseControl`| —                                 | give up control |
| `setUserName`  | `{ "device": <deviceId>, "user": <name|"null"> }` | label this client |
| `setSystemTime`| `<epochMillis>`                    | set telescope clock |

**Inbound** (telescope → client): event **`STATUS_UPDATED`**, whose single argument is the full
status **JSON object** (not a string), parsed into `StellinaStatus`. Also **`CONTROL_ERROR`** for
control errors. The server pushes `STATUS_UPDATED` on its own cadence after connect — no emit is
required first. (`connect`/`disconnect`/`reconnect`/`error` are lifecycle events.)

`StellinaStatus` carries (selected): `challenge`, `telescopeId`, `bootCount`, `initialized`,
`masterDeviceId` (who has control), `model`, `internalBattery`, `motors`, `network`, `filter`,
`position`, `currentOperation` + per-mode operation objects (observation, autoInit, park,
dark, plan, playlist, sunMode, storageAcquisition).

## 3. Authorization (fully reproducible — keys are embedded in the app)

Every REST call sends header `Authorization: <computed>`. It is **not** a server-issued token;
it's a TweetNaCl (Ed25519) signature computed client-side from the live `challenge`. From
`InstrumentRepository.getAuthHeader()`:

```
challenge      = status.challenge           # string from socket.io status
first          = challenge[0]               # selector char, passed through verbatim
rest           = base64decode(challenge[1:])
suffix         = ("|" + telescopeId + "|" + bootCount).encode("utf-8")
digest         = SHA512(rest + suffix)      # 64 bytes
signed         = TweetNaCl.sign(digest, secretKey)   # 64-byte sig ‖ 64-byte digest = 128 bytes
header         = "Basic android|" + first + "|" + base64(signed)
```
Embedded keys (base64):
```
publicKey (32B) = aCPG7E1gvOBDwWdj82OceoebY0ARMdie0XG++to/Afc=
secretKey (64B) = O8GD9ttc5pbB/QvCK1W7TfVOmd4ZYlgOZ22Qvz6GhMZoI8bsTWC84EPBZ2PzY5x6h5tjQBEx2J7Rcb762j8B9w==
```
Verified: `secretKey[32:] == publicKey` (standard NaCl `seed‖pubkey`), so PyNaCl
`SigningKey(secretKey[:32])` reproduces it exactly. `TweetNacl.sign()` returns the signed
message (sig ‖ message), which matches PyNaCl `bytes(SigningKey.sign(digest))` (128 bytes).
The literal string `"Basic android|..."` is the whole header value (it is *not* HTTP Basic).

> Because the challenge changes with every status push, you compute a fresh header per request
> from the latest status. So you must have the socket.io status (for `challenge`/`telescopeId`/
> `bootCount`) before any REST command will authenticate.

## 4. REST API — `http://10.0.0.1:8082/v1/` (`StellinaAPI.kt`)

All take `@Header("Authorization")`; most return `OrderResponse { success: bool }`.

Lifecycle / pointing / imaging:
```
POST general/startAutoInit          AutoInitBody { latitude, longitude, time, observatoryId, observatoryName, skipAutoFocus }
POST general/stopAutoInit
POST general/startObservation       StartObservationBody (see §5)
POST general/stopObservation
POST general/park
POST general/openForMaintenance
POST general/setUserParams          ModeParamsBody { MAP, exposureMicroSec, gain, saturation }
POST general/adjustObservationFraming   AdjustFramingBody { x, y, rotation }
POST general/adjustObservationFocus     AdjustFocusBody
GET  app/status
POST app/setSettings                SettingsBody
```
Darks / planner / playlist / expert / sun / capture / storage / network / board / reporter:
```
POST darkManager/generateDark | darkManager/stopGenerateDark
POST planner/startPlan | planner/stopPlan           GET planner/getPlanObservation?observationId=
POST playlist/startPlaylist | playlist/stopPlaylist
POST expertMode/startStorageAcquisition | stopStorageAcquisition   StorageAcquisitionBody
POST sun/startSunMode | sun/stopObservation | sun/restartAutofocus | sun/changePov | sun/handleUserAction | sun/setUserParams
POST capture/exportImageTiff | capture/exportImageJpegXl | capture/setToBeResumable
GET  captureStore/getObservation?storeId=           POST captureStore/startObservationFromStoredCapture | captureStore/deleteStoredCapture
GET  storage/userStorageFolderContent?folderPath=   POST storage/deleteUserStorageFolders
POST network/switchFrequency        NetworkBody { band: "BAND_5_GHZ" | (2.4) }
POST board/requestShutdown
GET  reporter/getAvailableReports   POST reporter/markReportsAsSynced
POST userManager/makeResetRequest | userManager/applyResetResponse
GET  (Streaming) <url>              downloadImage(@Url)  — full image URL comes from status
POST updates/uploadUpdateFile (multipart)   POST logs/consume
```

## Command safety (audited against the decompiled code)

Risk classes for the 38 endpoints + socket emits, and how `pystellina` guards them:

- **Brick (firmware):** `updates/uploadUpdateFile` — the only true brick vector. **Never callable**
  in `pystellina` (hard-blocked in `request`/`call`, even with `allow_unsafe`).
- **Irreversible (data/ownership):** `storage/deleteUserStorageFolders`,
  `captureStore/deleteStoredCapture`, `userManager/makeResetRequest` + `applyResetResponse`
  (control/owner reset). Refused unless `allow_unsafe=True` (CLI `--unsafe`).
- **Hardware damage (solar):** `sun/*` and any target within `SOLAR_EXCLUSION_DEG` (10°) of the Sun
  — refused unless `allow_solar`/`allow_unsafe`; imaging the Sun without the Vaonis filter destroys
  the sensor. (Software can't verify the filter.)
- **Disconnect (recoverable):** `board/requestShutdown` (powers off; physical button to restart),
  `network/switchFrequency` (changes Wi-Fi band → rejoin). Warned + guarded.
- **Control:** `takeControl` forcibly demotes the phone app (the firmware ignores the app's advisory
  `canTakeControl`); the phone can take it back, after which our signed commands start failing.

Preconditions enforced by the app via `Instrument.can*` (mirrored in `pystellina`):
`canSendRequest` = not `shuttingDown` + connected + **we are master** (`masterDeviceId == us`).
`startObservation` additionally requires `initialized == true` and **no operation running**
(`current*Operation` all stopped/absent). `park`/`openForMaintenance` require no running operation;
`openForMaintenance` also requires `isParked`. `pystellina` refuses out-of-state commands and, in
`sequence`, waits for the scope to go idle before `park`/`requestShutdown`.

## 5. `StartObservationBody` (the "go observe target X" payload)

Moshi keys = field names except where noted:
```
objectId, objectName, objectType,
ra, de, rot                 (Double; RA/Dec/rotation)
gain, exposureMicroSec      (Integer)
doStacking                  (Boolean)
histogramEnabled, histogramLow, histogramMedium, histogramHigh
backgroundEnabled, backgroundPolyorder
observationType, algorithm, brightZoneOffset, store   (enums)
hdrBackground
mosaic        @Json(name="mosaic")     -> MosaicBody
targetType    @Json(name="targetType")
```
Built via `StartObservationBody.Builder` / `InstrumentRepository.getStartObservationParams()`.

### Object catalog (where "tonight's targets" come from)

Targets are **not** served by the telescope — the app bundles them in
`assets/catalog/objects.json` (421 objects: 411 fixed deep-sky/stars with J2000 `ra`/`de`,
plus 8 planets + Sun + Moon that are ephemeris-driven and have no coordinates). Each entry
carries `idMessier`/`idNgc`/`idIc`, `magnitude`, a curated `grade` (0–10), recommended
`duration`/`gain`/`exposure`/`orientation`, and histogram/background params — i.e. everything
needed to populate a `StartObservationBody`. Human **names + descriptions** live in compiled
resources under string keys `objects_<id>_title` / `_description` (decode with apktool).
"What to watch tonight" is just this list filtered by altitude for the user's location/time.
`pystellina` bundles the factual subset (incl. names/descriptions) and computes visibility
locally (`pystellina/catalog.py`); no Vaonis cloud/account is involved. (Cloud `Orion*` APIs
exist for richer "Plan My Night", but aren't needed.)

**Planets / Moon / Sun.** Supported, not special-cased away: `SolarSystemObjects` = sun,
mercury, venus, moon, mars, jupiter, saturn, uranus, neptune. The app computes their positions
with `com.vaonis.kaa.AAPlus` (a Java port of Meeus' *Astronomical Algorithms*) via
`AstroLibBridge.getObjectElevation` / `equatorialCoordinatesFromSolarObject`, then starts a
normal go-to with the computed RA/Dec. `pystellina` does the same via `ephem` (see
`pystellina/astro.py`). Stellina's optics are deep-sky-optimised, so planets are tiny — that's
an *optical* limitation, not a software one.

**Darkness / observing window.** The app defines night as **Sun altitude ≤ −10°**
(`GetSunDetailLifetime.getSunset/getSunrise` walk minute-by-minute until `rint(sunAlt) == -10`).
`pystellina.astro.is_dark` / `observing_window` reproduce this.

### Live images & stacking (verified on firmware 2.35.7)

The active operation is top-level **`currentOperation`** (a single polymorphic object with a
`type` discriminator — `OBSERVATION`, `AUTO_INIT`, `PARK`, …), **not** a per-type field. When
`type == "OBSERVATION"` and `!stopped`, it carries:
- `target { objectId, objectName, ra, de, rot }`, `framing`, `observationType`
- `steps[]` — phases like `{type: "POINT_DEEP_SKY", name, steps:[…]}`, `{type: "CAPTURE"}`
  (no numeric `progress` at this level)
- `capture` — `{ id, cameraParams { exposureMicroSec, gain, width, height }, stackingCount,
  stackingErrorCount, acquisitionCount, hasStacking, debayerInterpolation,
  images:[ { index, url, cropX/Y/W/H, stackingCount, time } ] }`. The latest stacked frame is
  `capture.images[-1]`; **integration ≈ `stackingCount × cameraParams.exposureMicroSec`**.
- `store { state, storeId, totalStackingCount, exportImages[] }`

Image URL (built by `Instrument.getImageUrl`, confirmed live):
`http://<ip>:8082<image.url>?androidImageIndex=<index>&androidCaptureId=<capture.id>` —
e.g. `…/files/captures/2026-05-30_02-09-15_observation_M104/images/IMG_0042.jpg?…`. Plain GET,
**no auth**; the query is cache-busting per frame. Finished operations are under
`previousOperations` (keyed by type) with `…attempts[].lastImage` / per-target images at
`/files/plans/…`. `capture/exportImageTiff` + `capture/exportImageJpegXl` give full-res exports.

Other confirmed top-level status fields: `sensors { temperature, humidity, dewpointDepression,
defogStatus }` (**no battery** — Stellina is mains/USB powered), `motors { AZ, ALT, DER, MAP →
{position,state,calibrated} }`, `network { band, channel }`, `settings`, `storage`, `filter`,
`masterDeviceId`, `connectedDevices[]`. `GET app/status` returns the same object wrapped as
`{ success, result: {…} }`. `pystellina/observation.py` parses this; FTP `/user` was empty on the
test unit (captures are served over HTTP `/files/...`).

## 6. Getting it onto your LAN (bridge options)

Since the scope is AP-only, put a small router/AP in **client/bridge (WISP) mode** that joins
`STELLINA-xxxx` and re-exposes it on your wired/Wi-Fi LAN (OpenWrt `relayd`/routed client, a
GL.iNet travel router in repeater mode, or a Raspberry Pi with `wlan0` as STA + NAT). Two
gotchas: (a) the telescope only knows `10.0.0.1`/its own subnet, so route to that subnet
through the bridge; (b) socket.io + FTP must pass through (FTP needs passive mode handling).

## 7. Recommended build path → Home Assistant (HACS)

1. **`stellina_cli.py`** (this repo) — validate the protocol on your laptop on the Stellina AP.
2. Extract a small **async library** (`pystellina`): socket.io status stream + `auth_header()` +
   typed REST calls. This is the reusable core.
3. **HACS custom integration** wrapping `pystellina`:
   - config flow (host default `10.0.0.1`, deviceId); a `DataUpdateCoordinator` fed by the
     socket.io status stream (push, not poll);
   - entities: battery sensor, current-operation/state sensor, initialized/connected binary
     sensors, motors/position attributes;
   - services/buttons: `take_control`, `start_autoinit`, `start_observation`, `stop`, `park`,
     `shutdown`.
   - One controller at a time — HA should `takeControl` and watch `masterDeviceId`.
