# Stellina status object (firmware 2.35.7)

The telescope pushes its full state as the socket.io **`STATUS_UPDATED`** event (a JSON object), and
the same object is returned (wrapped as `{success, result: {...}}`) by `GET app/status`. Captured live
from `stellina-f8bd80` while observing M104. `pyvaonis` keeps the whole thing in
`VaonisStatus.raw` and types only the fields it needs (`models.py`, `observation.py`).

## Top-level keys
| key | type | notes |
|---|---|---|
| `apiVersion` | str | e.g. `"2.0.0"` |
| `version` | str | firmware, e.g. `"2.35.7"` |
| `telescopeId` | str | e.g. `"stellina-f8bd80"` — used in the auth signature |
| `model` | str | `"stellina"` |
| `bootCount` | int | used in the auth signature |
| `challenge` | str | rotating; first char is a selector, rest base64 (see PROTOCOL.md §3) |
| `timestamp` | int | epoch ms |
| `elapsedTime` | int | ms since boot/op |
| `position` | obj | `{latitude, longitude}` (GPS/observatory) |
| `initialized` | bool | true after auto-init; required before `startObservation` |
| `shuttingDown` | bool | part of `canSendRequest` |
| `autofocusTemperature` / `autofocusPosition` | num | focuser state |
| `masterDeviceId` | str | which `connectedDevices.id` holds control (null if none) |
| `connectedDevices` | list | `{id, name, user?}` — every connected client; control = `masterDeviceId` |
| `settings` | obj | see below |
| `sensors` | obj | see below (**no battery**) |
| `motors` | obj | see below |
| `network` | obj | `{band: "BAND_2_4_GHZ"|"BAND_5_GHZ", channel:int}` |
| `storage` | obj | see below |
| `filter` | str | e.g. `"NONE"` |
| `update` | obj | `{installedVersion, minimumCompatibleVersion, state}` |
| `logs` | obj | `{numFiles, bufferPosition, bufferSize}` |
| `availableReports` | int | count for `reporter/getAvailableReports` |
| `currentOperation` | obj\|null | the active operation (polymorphic by `type`) — see below |
| `otherCurrentOperations` | list | usually `[]` |
| `previousOperations` | obj | keyed by op type (`plan`, `autoInit`, …) — history |
| `captureStore` | obj | `{storedCaptures: [...]}` — the "saved" library (`setToBeResumable`) |
| `planner` | obj | `{}` when idle |

## `currentOperation` (type == "OBSERVATION")
```
{
  id, ctx, type: "OBSERVATION", startTime, endTime, stopped: bool, error,
  deviceId, appVersion, observationType: "STANDARD"|"MANUAL"|...,
  target: { type:"CATALOG", ra, de, rot, objectId, objectName },
  framing: { ra, de, rot },
  steps: [ { type:"POINT_DEEP_SKY", name, steps:[{type:"GO_TARGET"|"START_TRACKING"|"ASTROMETRY"...}] },
           { type:"CAPTURE", captureType:"INITIAL" }, ... ],   # phases; NO numeric progress
  actionInProgress: bool, isDithering: bool,
  capture: { ...see below... },
  store: { state:"NON_RESUMABLE"|"TO_BE_RESUMABLE", storeId, totalStackingCount, exportImages:[] }
}
```
Other `currentOperation.type` values seen: `AUTO_INIT` (with `astrometry`, `focusResult`, `armPosition`,
`observatoryName`), `PARK`, `PLAN`. `pyvaonis.observation.ObservationProgress.from_status` only treats
`type=="OBSERVATION" && !stopped` as "observing".

### `capture`
```
{
  id: "capture-105-295-f8bd80",            # <-- androidCaptureId for image URLs
  ctx, startTime, endTime, stopped, error, captureType,
  cameraParams: { exposureMicroSec: 10000000, gain: 200, width: 3072, height: 2080 },
  target, position,
  hasStacking: true,
  acquisitionCount,                        # frames attempted
  stackingCount,                           # frames successfully stacked (the live count)
  stackingErrorCount, outputImageCount, outputImageErrorCount,
  stackingErrorMap: { StackingRoundnessError: n, ... },
  startHumidity, startTemperature, debayerInterpolation: "VNG",
  images: [ { index, url, cropX, cropY, cropWidth, cropHeight, stackingCount, stackingErrorCount, time } ]
}
```
- Live frame = `images[-1]`. URL = `http://<ip>:8082<image.url>?androidImageIndex=<index>&androidCaptureId=<capture.id>`
  e.g. `/files/captures/2026-05-30_02-09-15_observation_M104/images/IMG_0042.jpg` (no auth).
- Integration seconds ≈ `stackingCount × cameraParams.exposureMicroSec / 1e6`.

## `sensors` (no battery — mains/USB powered)
`{ temperature, temperatureDelta, humidity, humidityDelta, defogStatus:"OFF"|..., dewpointDepression }`
`dewpointDepression` (°C) is the dew-risk margin; small = dew imminent.

## `motors`
`{ AZ, ALT, DER, MAP }`, each `{ position, state:"TRACKING"|"IDLE"|..., calibrated:bool }`.

## `settings` (mutated by `app/setSettings` / `SettingsBody`)
`{ telescopeName, storageFileCategories:["OUTPUT","DEBUG","TIFF"], enableLiveFocus, band,
   enableFullResolution, enableDithering }` (+ `enableHdrBackground` = Multi-Light, `enableDarkUsage`,
   `algoHdrBackground`, `buttonBrightness`, `usbFileTypes` exist in the body model).

## `storage` (KB)
`{ system:{size,available}, data:{size,available}, usb:null, public:null }` — `data` ~10 GB total,
~9 GB free on the test unit; `usb`/`public` null when no drive attached.

## `previousOperations`
Keyed by type. `plan` → `{ planName, targets:[ { target, startTime, endTime,
attempts:[ { observationId, lastImage:{ index, url:"/files/plans/.../IMG_NNNN.jpg", stackingCount, ... } } ] } ] }`.
`autoInit` → plate-solve `astrometry {ra,de,rot}`, `focusResult {map, focusValue}`, `armPosition`,
`observatoryName`. `pyvaonis.observation.recent_images` walks these for `lastImage` URLs.

## Auth fields
`getAuthHeader()` uses `challenge`, `telescopeId`, `bootCount` from this object (PROTOCOL.md §3).
A fresh header is computed per REST call from the latest status (the challenge rotates).

## On-device files (anonymous FTP, `:21`) — mapped live
The internal storage tree (the HTTP image server serves the same files under `/files/...`):
```
/system/
  captures/<storeId>/            # finished + in-progress observations  (storeId = "<date>_observation_<id>", older ones just "<date>")
    images/IMG_NNNN.jpg          # the stacked preview frames
    store.json | capture.json    # per-session metadata (older sessions use capture.json)
  plan/target-N-observation-0/   # Plan-My-Night per-target results
    images/...  capture.json
    ../plan.json                 # the plan definition
  bias/IMG/                      # bias frames
  dark/                          # master darks
  history/focus-history.json     # autofocus history
  logs/{winston,sync}/ ...       # firmware logs
  reports/<observationId>.json   # per-observation reports
  temp/{acquisition,export,hdr,stacking}/   # scratch
/user/                           # empty unless a USB drive / user storage is mounted
```
So **all past sessions' frames are retrievable over FTP** under `/system/captures/*` and
`/system/plan/*` — this is the gallery source (each dir has `store.json`/`capture.json` with the
target + settings; `storeId` encodes date + object). Note `captureStore.storedCaptures` (the
"multi-night resumable" set) is *separate* and can be empty even when many capture dirs exist.

**Path quirk (confirmed on hardware):** plan frames are served over **HTTP as `/files/plans/…`
(plural)** but stored on **FTP as `/system/plan/…` (singular)** — `LiveImage.ftp_path` rewrites the
plural→singular. Captures match (`/files/captures` ↔ `/system/captures`). The status
`previousOperations` can also be stale (e.g. only a reboot-restored plan), so the **live `image`
fallback and the gallery read the FTP `/system/captures` library directly** (date-prefixed storeIds,
newest first) rather than trusting `previousOperations`.

## Quirks observed live (firmware 2.35.7)
- **`masterDeviceId` can be `null` while an observation is still running** — the scope keeps
  imaging autonomously after the controlling app releases/backgrounds. So "observing" ≠ "someone
  holds control"; to send commands you still must `take_control`.
- **`GET storage/userStorageFolderContent` → HTTP 500 `STORAGE.NO_USER_STORAGE`** when no USB drive
  is attached (it queries *user* storage, not `/system`). Use FTP for the internal library.
- **`GET reporter/getAvailableReports` → `{result: []}`** when there are no pending reports.
- `connectedDevices` accumulates stale entries (every distinct `deviceId` lingers); use a stable
  `device_id` (pyvaonis derives one from the host MAC) to avoid piling up.
- Stacking acceptance is partial: e.g. M104 showed `stackingCount 180 / acquisitionCount 379`
  (≈half rejected, `StackingRoundnessError`) — normal in poor seeing/wind. `images[]` is sparse
  (not every index), so the latest frame is `images[-1]`, not `images[stackingCount-1]`.
