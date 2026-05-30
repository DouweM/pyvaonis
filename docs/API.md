# Vaonis Stellina — Local API Reference

Complete, source-derived reference for the Stellina local control protocol. Everything here is
reverse-engineered from **Singularity by Vaonis v1.38.10** (`com.vaonis.barnard`), specifically
`com.vaonis.instruments.sdk.*` (jadx decompile), and validated against real hardware on
**firmware 2.35.7**. For wire-protocol provenance and the auth derivation see `PROTOCOL.md`; for
the full status JSON schema see `docs/STATUS.md`.

> Primary source files:
> - REST surface: `src/sources/com/vaonis/instruments/sdk/StellinaAPI.java`
> - Request bodies: `src/sources/com/vaonis/instruments/sdk/models/body/**`
> - Responses: `src/sources/com/vaonis/instruments/sdk/models/response/**`
> - Socket: `src/sources/com/vaonis/instruments/sdk/socket/StellinaSocketV2.java` + `PROTOCOL.md` §2
> - pystellina coverage: `pystellina/client.py`, `pystellina/const.py`

---

## 1. Connection summary

The telescope is **always its own Wi-Fi access point** at `10.0.0.1` (no station/join-home-Wi-Fi
mode exists in the API). Four services run on that host:

| Service | Endpoint | Auth | Purpose |
|---------|----------|------|---------|
| **REST** (OkHttp/Retrofit) | `http://10.0.0.1:8082/v1/` | `Authorization` header per request (see below) | All commands. Base URL = `http://{ip}:8082/v1/`. |
| **Socket.IO v2 / Engine.IO v3** | `ws://10.0.0.1:8083/socket.io/?EIO=3&transport=websocket` | none | Live status stream (inbound `STATUS_UPDATED`) + control acquisition (outbound `emit("message", <key>)`). |
| **HTTP image server** | `http://10.0.0.1:8082/files/...` | **none** | Captured/stacked JPEG frames, e.g. `/files/captures/<storeId>/images/IMG_0042.jpg?androidImageIndex=…&androidCaptureId=…` (query is cache-busting). |
| **FTP (anonymous)** | `10.0.0.1:21` | none | Image library. Captures under `/system/captures` (also `bias`, `dark`, `history`, `logs`, `plan`, `reports`, `temp`); `/user` was empty on the test unit. |

Connection constants (`StellinaContext.kt`, mirrored in `pystellina/const.py`):
`DEFAULT_IP=10.0.0.1`, `DEFAULT_HTTP_PORT=8082`, `DEFAULT_SOCKET_PORT=8083`,
`DEFAULT_SOCKET_IO_PATH=/socket.io`, `HTTP_ROOT=""`.

Engine.IO v3 (`EIO=3`) is mandatory: `python-socketio`/`python-engineio` (EIO=4) **cannot** connect
— pystellina ships its own EIO3 client (`pystellina/_eio3.py`). The socket query is
`id=<deviceId>&name=<deviceName>&countryCode=<cc>`. Heartbeat is **client-initiated** (client sends
`2`, server replies `3`).

### Authorization (REST)

Every REST call carries an `Authorization` header that is **not** a server-issued token: it is an
Ed25519 (TweetNaCl) signature computed client-side over the rotating `challenge` taken from the
latest socket status, using a keypair embedded in the app. Because the `challenge` changes with
every status push, a **fresh header is computed per request** — so you must have an active socket
status (for `challenge` / `telescopeId` / `bootCount`) before any REST command will authenticate.
Full derivation and the embedded keys are in **`PROTOCOL.md` §3**; the working implementation is
`pystellina/auth.py` (`build_auth_header`). The header value is the literal string
`Basic android|<selector>|<base64(signed)>` (despite the prefix it is *not* HTTP Basic auth).

---

## 2. REST endpoint reference — `http://10.0.0.1:8082/v1/`

All POST endpoints (except the unauthenticated GETs `app/status`, `reporter/getAvailableReports`,
`planner/getPlanObservation`, `storage/userStorageFolderContent`, `captureStore/getObservation`)
take a `@Header("Authorization")`. Unless noted, the response is `OrderResponse { success: boolean }`.

**Risk legend:**
- **SAFE** — read-only or trivially reversible.
- **STATE** — changes operating state (start/stop an operation, tune params); reversible.
- **DISCONNECT** — drops the Wi-Fi link / session; recoverable (rejoin or physical button).
- **DESTRUCTIVE** — irreversible data loss or ownership/control reset.
- **PHYSICAL** — moves the arm / mechanics.
- **SOLAR** — points at/near the Sun; without the Vaonis solar filter this **destroys the sensor**.
- **BRICK** — firmware-level; can permanently brick the device.

**Endpoint total: 42 annotated methods** in `StellinaAPI.java` (41 `@POST`/`@GET` + the `@Streaming`
`downloadImage`). Two further methods — `setUserRole` (`UserRoleBody`) and `resetUserRole` — appear
in the Kotlin `@Metadata` of the interface (and `UserRoleBody` is imported) but JADX emitted no
method body/path for them in this decompile; they are noted at the end of the table but not counted.

### general/ — lifecycle, pointing, imaging

| Method | Path | Request body | Response | Risk | pystellina |
|--------|------|--------------|----------|------|------------|
| POST | `general/startAutoInit` | `AutoInitBody { latitude:double, longitude:double, time:long (epoch ms), observatoryId:String, observatoryName:String, skipAutoFocus:boolean }` | OrderResponse | PHYSICAL/STATE | `start_autoinit()` |
| POST | `general/stopAutoInit` | — | OrderResponse | STATE | `stop_autoinit()` |
| POST | `general/startObservation` | `StartObservationBody` (see §3) | OrderResponse | PHYSICAL/STATE (SOLAR if near Sun) | `start_observation()` / `observe_object()` |
| POST | `general/stopObservation` | — | OrderResponse | STATE | `stop_observation()` |
| POST | `general/park` | — | OrderResponse | PHYSICAL | `park()` |
| POST | `general/openForMaintenance` | — | OrderResponse | PHYSICAL | — (const `OPEN_FOR_MAINTENANCE`, no helper) |
| POST | `general/setUserParams` | `ModeParamsBody { MAP:Integer, exposureMicroSec:Integer, gain:Integer, saturation:Float, exposureMode:StellinaObservationOperation.CurrentParams.ExposureMode }` | OrderResponse | STATE | `set_camera_params()` (gain/exposureMicroSec/saturation) |
| POST | `general/adjustObservationFraming` | `AdjustFramingBody { x:int, y:int, rot:double (degrees) }` | OrderResponse | PHYSICAL/STATE | `adjust_framing()` |
| POST | `general/adjustObservationFocus` | `AdjustFocusBody { restartCapture:boolean }` | OrderResponse | STATE | `restart_autofocus()` |

### app/ — status & settings

| Method | Path | Request body | Response | Risk | pystellina |
|--------|------|--------------|----------|------|------------|
| GET | `app/status` | — | `StatusResponse { result:StellinaStatus, success:boolean }` | SAFE | `app_status()` |
| POST | `app/setSettings` | `SettingsBody { usbFileTypes:List<String>, storageFileCategories:List<String>, telescopeName:String, enableLiveFocus:Boolean, enableFullResolution:Boolean, enableHdrBackground:Boolean, enableDarkUsage:Boolean, enableDithering:Boolean, algoHdrBackground:StellinaSettings.BalensMode, buttonBrightness:String }` | OrderResponse | STATE | `set_multi_light()` (toggles `enableHdrBackground`; echoes current settings) |

> Note: `app/setSettings` may **replace** rather than merge — `set_multi_light` re-sends the current
> settings to be safe.

### capture/ — live frame / full-res export / save

| Method | Path | Request body | Response | Risk | pystellina |
|--------|------|--------------|----------|------|------------|
| POST | `capture/exportImageTiff` | `TiffBody { captureId:String }` | `TiffResponse { result:TiffData }` where `TiffData { savedOnUsbStorage:Boolean, stackingCount:Integer }` (+ `url` returned in result) | STATE | `export_url(fmt="tiff")` / `export_capture()` |
| POST | `capture/exportImageJpegXl` | `@Query("captureId") String` (no body) | `CaptureJxlResponse { result:JXLData }` where `JXLData { image:StellinaCaptureImage, inAppJson:String, url:String }` | STATE | `export_url(fmt="jxl")` / `export_capture()` |
| POST | `capture/setToBeResumable` | — | OrderResponse | STATE | `save_observation()` |

> pystellina treats `exportImageJpegXl` as a GET with `?captureId=` (`export_url`); the decompiled
> interface annotates it `@POST` with a `@Query` param. Both forms reach the same handler in practice.

### captureStore/ — stored-capture library / resume

| Method | Path | Request body | Response | Risk | pystellina |
|--------|------|--------------|----------|------|------------|
| GET | `captureStore/getObservation` | `@Query("storeId") String` | `StoredObservationResponse { result:StellinaObservationOperation, success:boolean }` | SAFE | — |
| POST | `captureStore/startObservationFromStoredCapture` | `StartObservationFromStoredCaptureBody { storeId:String }` | OrderResponse | PHYSICAL/STATE | — |
| POST | `captureStore/deleteStoredCapture` | `DeleteStoredCaptureBody { storeId:String }` | OrderResponse | DESTRUCTIVE | — (guarded) |

### darkManager/ — dark-frame generation

| Method | Path | Request body | Response | Risk | pystellina |
|--------|------|--------------|----------|------|------------|
| POST | `darkManager/generateDark` | — | OrderResponse | STATE | — (const `GENERATE_DARK`, no helper) |
| POST | `darkManager/stopGenerateDark` | — | OrderResponse | STATE | — (const `STOP_GENERATE_DARK`, no helper) |

### planner/ — Plan My Night

| Method | Path | Request body | Response | Risk | pystellina |
|--------|------|--------------|----------|------|------------|
| POST | `planner/startPlan` | `PlanMyNightBody { planId:String, planVersion:String, planName:String, targets:List<PlanMyNightTargetBody>, latitude:double, longitude:double, observatoryId:String, observatoryName:String, userId:int, deviceId:String, appVersion:String }` — each `PlanMyNightTargetBody { startTime:long, endTime:long, storeId:String, params:StartObservationBody }` | OrderResponse | PHYSICAL/STATE | — (const `START_PLAN`, no helper) |
| POST | `planner/stopPlan` | — | OrderResponse | STATE | — (const `STOP_PLAN`, no helper) |
| GET | `planner/getPlanObservation` | `@Query("observationId") String` | `PlanObservationResponse { result:StellinaObservationOperation, success:boolean }` | SAFE | — |

### playlist/ — observation playlists

| Method | Path | Request body | Response | Risk | pystellina |
|--------|------|--------------|----------|------|------------|
| POST | `playlist/startPlaylist` | `PlaylistBody { appVersion:String, deviceId:String, playlistType:StellinaPlaylistOperation.PlaylistType, targets:List<TargetsParam>, userId:Integer }` — each `TargetsParam { params:StartObservationBody }` | OrderResponse | PHYSICAL/STATE | — |
| POST | `playlist/stopPlaylist` | — | OrderResponse | STATE | — |

### expertMode/ — raw storage acquisition (frame stacks to disk)

| Method | Path | Request body | Response | Risk | pystellina |
|--------|------|--------------|----------|------|------------|
| POST | `expertMode/startStorageAcquisition` | `StorageAcquisitionBody { path:String, overwrite:boolean, numExposures:int, gain:int, exposureMicroSec:int, flip:StellinaAcquisitionFlip (NO_FLIP \| FLIP \| BOTH) }` | OrderResponse | PHYSICAL/STATE | — |
| POST | `expertMode/stopStorageAcquisition` | — | OrderResponse | STATE | — |

### sun/ — solar observation (filter required)

| Method | Path | Request body | Response | Risk | pystellina |
|--------|------|--------------|----------|------|------------|
| POST | `sun/startSunMode` | `SunModeBody { appVersion:String, deviceId:String, eclipse:boolean, latitude:double, longitude:double, observatoryId:String, observatoryName:String, skipAutoFocus:boolean, time:long (epoch ms), userId:Integer }` | OrderResponse | **SOLAR**/PHYSICAL | — (guarded) |
| POST | `sun/handleUserAction` | `SunModeActionBody { action:StellinaSunModeAction, pov:StellinaSunModeOperation.StellinaSunModePov }` | OrderResponse | **SOLAR**/STATE | — (guarded) |
| POST | `sun/setUserParams` | `SunModeParamsBody { MAP:Integer, exposureMicroSec:Integer, gain:Integer, exposureMode:StellinaObservationOperation.CurrentParams.ExposureMode }` | OrderResponse | **SOLAR**/STATE | — (guarded) |
| POST | `sun/changePov` | `SunModePovBody { pov:StellinaSunModeOperation.StellinaSunModePov }` | OrderResponse | **SOLAR**/STATE | — (guarded) |
| POST | `sun/restartAutofocus` | — | OrderResponse | **SOLAR**/STATE | — (guarded) |
| POST | `sun/stopObservation` | — | OrderResponse | **SOLAR**/STATE | — (guarded) |

### storage/ — on-telescope user storage

| Method | Path | Request body | Response | Risk | pystellina |
|--------|------|--------------|----------|------|------------|
| GET | `storage/userStorageFolderContent` | `@Query("folderPath") String` | `FolderContentResponse { result:StellinaStorageFolderContent }` | SAFE | — |
| POST | `storage/deleteUserStorageFolders` | `DeleteUserStorageFolderBody { folderPaths:List<String> }` | OrderResponse | DESTRUCTIVE | — (guarded) |

### network/ — Wi-Fi band

| Method | Path | Request body | Response | Risk | pystellina |
|--------|------|--------------|----------|------|------------|
| POST | `network/switchFrequency` | `NetworkBody { band:String ("BAND_2_4_GHZ" \| "BAND_5_GHZ") }` | `Unit` (empty) | DISCONNECT | `switch_frequency()` |

### board/ — power

| Method | Path | Request body | Response | Risk | pystellina |
|--------|------|--------------|----------|------|------------|
| POST | `board/requestShutdown` | — | OrderResponse | DISCONNECT | `request_shutdown()` |

### reporter/ — telemetry reports

| Method | Path | Request body | Response | Risk | pystellina |
|--------|------|--------------|----------|------|------------|
| GET | `reporter/getAvailableReports` | — | `ReportsResponse { result:List<StellinaReport> }` | SAFE | — |
| POST | `reporter/markReportsAsSynced` | `ReportsBody { reports:List<Report> }` — each `Report { operationId:String, operationEnded:boolean }` | `Unit` (empty) | STATE | — |

### userManager/ — ownership / control reset

| Method | Path | Request body | Response | Risk | pystellina |
|--------|------|--------------|----------|------|------------|
| POST | `userManager/makeResetRequest` | — | `ResetCodeResponse { result:String }` | DESTRUCTIVE | — (guarded) |
| POST | `userManager/applyResetResponse` | `RequestCodeBody { response:String }` | OrderResponse | DESTRUCTIVE | — (guarded) |

### updates/ — firmware

| Method | Path | Request body | Response | Risk | pystellina |
|--------|------|--------------|----------|------|------------|
| POST | `updates/uploadUpdateFile` | **Multipart**: `@Part MultipartBody.Part file`, `@Query("fileName") String`, `@Query("model") String` | `Call<ResponseBody>` (raw; not a coroutine) | **BRICK** | — (**hard-blocked**, never callable) |

### logs/ — log retrieval

| Method | Path | Request body | Response | Risk | pystellina |
|--------|------|--------------|----------|------|------------|
| POST | `logs/consume` | — | `LogResponse { file:String, result:Result { data:String, message:String }, success:boolean }` | SAFE | — |

### Image download (no base path)

| Method | Path | Request body | Response | Risk | pystellina |
|--------|------|--------------|----------|------|------------|
| GET (`@Streaming`) | `@Url <full url>` | — | `okhttp3.ResponseBody` (raw bytes) | SAFE | `fetch_image()` / `fetch_current_image()` / `download_file()` (via the `:8082/files` server, no auth) |

> `downloadImage` takes a full `@Url` (built from the live status, e.g.
> `http://10.0.0.1:8082/files/captures/.../IMG_0042.jpg?…`) and does **not** use the `/v1/` base or
> the `Authorization` header.

### Metadata-only (not decompiled to endpoints)

| Method (Kotlin metadata) | Likely path | Body | Notes |
|--------------------------|-------------|------|-------|
| `setUserRole` | `userManager/setUserRole` (inferred) | `UserRoleBody` (imported, source not present) | Present in interface `@Metadata` + import list; no `@POST` body emitted by JADX. |
| `resetUserRole` | `userManager/resetUserRole` (inferred) | — | Present in `@Metadata`; no `@POST` body emitted. |

---

## 3. `StartObservationBody` (the "go observe target X" payload)

Moshi `@JsonClass(generateAdapter = true)`; JSON keys equal field names **except** `mosaic` and
`targetType`, which carry explicit `@Json(name=…)` (here matching the field name). Built via
`StartObservationBody.Builder` / `InstrumentRepository.getStartObservationParams()`.

| Field | Type | Units / notes |
|-------|------|---------------|
| `objectId` | String | catalog id |
| `objectName` | String | |
| `objectType` | String | |
| `ra` | Double | right ascension |
| `de` | Double | declination |
| `rot` | Double | rotation (degrees) |
| `gain` | Integer | |
| `exposureMicroSec` | Integer | microseconds |
| `doStacking` | Boolean | |
| `histogramEnabled` | Boolean | |
| `histogramLow` | Double | |
| `histogramMedium` | Double | |
| `histogramHigh` | Double | |
| `backgroundEnabled` | Boolean | |
| `backgroundPolyorder` | Double | |
| `observationType` | enum `StellinaObservationOperation.StellinaObservationType` | e.g. `MANUAL` |
| `algorithm` | enum `StellinaObservationOperation.StellinaPointingAlgorithm` | |
| `brightZoneOffset` | enum `StellinaObservationOperation.StellinaBrightZoneOffset` | |
| `store` | enum `StellinaObservationOperation.StellingCaptureStore` | |
| `mosaic` | `MosaicBody { widthDegree:float, heightDegree:float }` | `@Json(name="mosaic")` |
| `targetType` | enum `StellinaObservationOperation.ObservationTargetType` | `@Json(name="targetType")` |
| `hdrBackground` | `HdrBackgroundBody { curve:int, polynomialOrder:int, ratioParam:double, ratioParamLocal:double, saturation:double, whiteMeanNoiseRatio:double }` | Multi-Light tuning params (the *toggle* is `app/setSettings.enableHdrBackground`) |

pystellina builds this via `pystellina/models.py:ObservationBody` (`to_payload()`); see also
`PROTOCOL.md` §5 for the catalog/ephemeris flow that populates it.

---

## 4. Socket.IO events — port 8083

Connect via websocket to `ws://10.0.0.1:8083/socket.io/?EIO=3&transport=websocket&id=<deviceId>&name=<deviceName>&countryCode=<cc>`,
default namespace. The server pushes status on its own cadence after connect — no emit is required
first. `connect`/`disconnect`/`reconnect`/`error` are transport lifecycle events.

### Inbound (telescope → client)

| Event | Payload | Meaning |
|-------|---------|---------|
| `STATUS_UPDATED` | the full `StellinaStatus` **JSON object** (not a string) | Live status: `challenge`, `telescopeId`, `bootCount`, `initialized`, `masterDeviceId`, `model`, `motors`, `sensors`, `network`, `filter`, `position`, `currentOperation` (+ `previousOperations`), `settings`, `storage`, `connectedDevices[]`. Carries the rotating auth `challenge`. (Schema: `docs/STATUS.md`.) |
| `CONTROL_ERROR` | error payload | Emitted when a control operation fails. |

### Outbound (client → telescope) — all via `emit("message", <key>[, <value>])`

| Key | Value | Meaning |
|-----|-------|---------|
| `takeControl` | — | Become master. **Forcibly demotes** the current controller (e.g. the owner's phone). |
| `releaseControl` | — | Give up control. |
| `setUserName` | `{ "device": <deviceId>, "user": <name \| "null"> }` | Label this client (shown in `connectedDevices`). |
| `setSystemTime` | `<epochMillis>` | Set the telescope clock. |

pystellina: `take_control()` (emits `takeControl` then `setUserName`), `release_control()`. The
constant `setSystemTime` (`MSG_SET_SYSTEM_TIME`) is defined but no helper currently emits it.

---

## 5. Preconditions & safety guards

Enforced by the app via `Instrument.can*` and mirrored in pystellina (`client.py`):

- **`canSendRequest`** = not `shuttingDown` + connected + **we are master** (`masterDeviceId == us`).
  pystellina: `_require_control()`.
- **`startObservation`/`startAutoInit`** additionally require **no operation running**; observation
  also requires `initialized == true`. pystellina: `_require_idle()` + `initialized` check + a
  `< SOLAR_EXCLUSION_DEG` (10°) Sun-proximity refusal unless `allow_solar=True`.
- **`park`/`openForMaintenance`** require no running operation; `openForMaintenance` also requires
  `isParked`.
- **`takeControl`** forcibly demotes the phone app (the firmware ignores the app's advisory
  `canTakeControl`); the phone can take it back, after which signed commands start failing.

pystellina `_guard_endpoint()` classification (`const.py`):

- **BRICK (hard-blocked, never callable even with `allow_unsafe`):** `updates/uploadUpdateFile`.
- **DESTRUCTIVE (require `allow_unsafe=True`):** `storage/deleteUserStorageFolders`,
  `captureStore/deleteStoredCapture`, `userManager/makeResetRequest`, `userManager/applyResetResponse`.
- **SOLAR (require `allow_unsafe=True`; prefix `sun/`):** all `sun/*`; plus any `start_observation`
  target within `SOLAR_EXCLUSION_DEG` (10°) of the Sun (`allow_solar`).
- **DISCONNECT (warned + guarded):** `board/requestShutdown`, `network/switchFrequency` (also refused
  mid-operation unless `force=True`).

---

## 6. pystellina coverage summary

**15 of 42 endpoints** have dedicated high-level client methods. A further **5** have named
constants in `const.py` but no convenience helper (`general/openForMaintenance`,
`darkManager/generateDark`, `darkManager/stopGenerateDark`, `planner/startPlan`,
`planner/stopPlan`). **Every** endpoint except the firmware uploader is additionally reachable via
the raw signed passthrough (`request()` / `post()` / `get()` / `call()`), subject to the safety
guards above.

**Implemented (dedicated method):**

| Endpoint | pystellina method |
|----------|-------------------|
| `general/startAutoInit` | `start_autoinit` |
| `general/stopAutoInit` | `stop_autoinit` |
| `general/startObservation` | `start_observation` / `observe_object` |
| `general/stopObservation` | `stop_observation` |
| `general/park` | `park` |
| `general/setUserParams` | `set_camera_params` |
| `general/adjustObservationFraming` | `adjust_framing` |
| `general/adjustObservationFocus` | `restart_autofocus` |
| `app/status` | `app_status` |
| `app/setSettings` | `set_multi_light` |
| `capture/exportImageTiff` | `export_url` / `export_capture` |
| `capture/exportImageJpegXl` | `export_url` / `export_capture` |
| `capture/setToBeResumable` | `enable_multi_night` |
| `network/switchFrequency` | `switch_frequency` |
| `board/requestShutdown` | `request_shutdown` |

(Plus image download via the no-auth `:8082/files` server and FTP library: `fetch_image`,
`fetch_current_image`, `library`, `download_file`.)

**Notable UNIMPLEMENTED endpoints:**

- **Planner:** `planner/startPlan` (const only), `planner/stopPlan` (const only),
  `planner/getPlanObservation`.
- **Playlist:** `playlist/startPlaylist`, `playlist/stopPlaylist`.
- **Expert mode:** `expertMode/startStorageAcquisition`, `expertMode/stopStorageAcquisition`.
- **Sun mode:** `sun/startSunMode`, `sun/handleUserAction`, `sun/setUserParams`, `sun/changePov`,
  `sun/restartAutofocus`, `sun/stopObservation` (all SOLAR-gated).
- **Stored captures:** `captureStore/getObservation`, `captureStore/startObservationFromStoredCapture`
  (resume), `captureStore/deleteStoredCapture` (DESTRUCTIVE-gated).
- **Storage:** `storage/userStorageFolderContent`, `storage/deleteUserStorageFolders`
  (DESTRUCTIVE-gated).
- **Dark frames:** `darkManager/generateDark` (const only), `darkManager/stopGenerateDark` (const only).
- **Reports/logs:** `reporter/getAvailableReports`, `reporter/markReportsAsSynced`, `logs/consume`.
- **Maintenance:** `general/openForMaintenance` (const only).
- **Ownership reset:** `userManager/makeResetRequest`, `userManager/applyResetResponse`
  (DESTRUCTIVE-gated).
- **Firmware:** `updates/uploadUpdateFile` (BRICK — intentionally hard-blocked).

**Intentionally gated (deliberately not auto-callable):**

- Firmware: `updates/uploadUpdateFile` — hard-blocked in both `request` and `call`.
- Destructive: `storage/deleteUserStorageFolders`, `captureStore/deleteStoredCapture`,
  `userManager/makeResetRequest`, `userManager/applyResetResponse` — require `allow_unsafe=True`.
- Solar: all `sun/*` and near-Sun observations — require `allow_unsafe`/`allow_solar` (the Vaonis
  solar filter must be physically installed; software cannot verify it).
