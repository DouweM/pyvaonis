"""Command-line interface: ``vaonis <command>``.

Run on a machine joined to the telescope's Wi-Fi (e.g. ``STELLINA-xxxx`` / ``Vespera-xxxx``), or
reachable through a bridge. Requires the ``cli`` extra: ``pip install "pyvaonis[cli]"``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
from typing import Any

import typer

from . import const
from .client import VaonisClient
from .client import VaonisError
from .models import ObservationBody

app = typer.Typer(
    help="Control a Vaonis smart telescope over its local Wi-Fi.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

# --help groups (typer renders one panel per distinct rich_help_panel).
PANEL_CONTROL = "Telescope control"
PANEL_LIVE = "Live view & library"
PANEL_PLAN = "Planning (offline, no telescope)"
PANEL_DEBUG = "Status & debug"

# Defaults from the environment so you don't retype them (and to dodge the negative-longitude
# argument-parsing footgun): set VAONIS_HOST / VAONIS_LAT / VAONIS_LON once.
DEFAULT_IP = os.environ.get("VAONIS_HOST", const.DEFAULT_IP)
_DEBUG = False


@app.callback()
def _main(
    debug: bool = typer.Option(
        False, "--debug", help="Log wire traffic and show full tracebacks on error."
    ),
) -> None:
    """Vaonis CLI."""
    global _DEBUG
    _DEBUG = debug
    if debug:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")
        for name in ("pyvaonis", "aiohttp", "asyncio"):
            logging.getLogger(name).setLevel(logging.DEBUG)


def _run(coro: Any) -> Any:
    """Run a coroutine, turning expected telescope errors into a clean one-line message.

    Without this, ordinary conditions (no control, an operation already running, scope not
    reachable) dump a full Python traceback. ``--debug`` re-raises so you still get one.
    """
    try:
        return asyncio.run(coro)
    except VaonisError as err:
        if _DEBUG:
            raise
        typer.secho(f"error: {err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None


async def _with_client(ip: str, control: bool, fn: Any) -> Any:
    async with VaonisClient(ip=ip) as scope:
        if control:
            await scope.take_control()
        return await fn(scope)


def _location(
    lat: float | None,
    lon: float | None,
    *,
    ip: str | None = None,
    use_scope: bool = False,
    fallback: tuple[float, float] | None = None,
) -> tuple[float, float]:
    """Resolve a location: CLI args → VAONIS_LAT/LON env → ``fallback`` → (if ``use_scope``) the scope.

    The scope reports its own GPS/observatory position in ``status.position``, so for commands that
    can reach it you don't need to supply a location at all. ``fallback`` lets an already-connected
    command pass that position without opening a second connection.
    """
    if lat is None and (env := os.environ.get("VAONIS_LAT")):
        lat = float(env)
    if lon is None and (env := os.environ.get("VAONIS_LON")):
        lon = float(env)
    if lat is not None and lon is not None:
        return lat, lon
    if fallback is not None:
        return fallback
    if use_scope and ip:
        try:

            async def _fetch() -> tuple[tuple[float, float] | None, str | None]:
                async with VaonisClient(ip=ip) as scope:
                    return scope.location(), scope.observatory_name()

            pos, observatory = asyncio.run(_fetch())
        except VaonisError:
            pos, observatory = None, None
        if pos is not None:
            where = f"observatory {observatory!r}" if observatory else "telescope"
            typer.secho(
                f"(using {where} location {pos[0]:.3f}, {pos[1]:.3f})",
                fg=typer.colors.BRIGHT_BLACK,
                err=True,
            )
            return pos
    raise typer.BadParameter(
        "no location: pass LAT LON, set VAONIS_LAT / VAONIS_LON, "
        "or connect to the telescope (it knows its own position)"
    )


def _print(data: Any) -> None:
    typer.echo(json.dumps(data, indent=2, default=str))


def _vis_dot(altitude: float) -> str:
    """Colored visibility dot (●) mirroring the app's green/orange/red altitude indicator."""
    from .catalog import visibility_rating

    color = {
        "good": typer.colors.GREEN,
        "poor": typer.colors.YELLOW,
        "not_visible": typer.colors.RED,
    }
    return typer.style("●", fg=color[visibility_rating(altitude)])


def _local(dt: Any) -> str:
    """Format a UTC datetime in the machine's local timezone (e.g. '21:08 CST').

    The telescope reports its location (lat/lon) but not a civil timezone, so we render in the
    local zone of wherever the CLI runs — which is the observing site in practice.
    """
    return f"{dt.astimezone():%H:%M %Z}"


@app.command(rich_help_panel=PANEL_DEBUG)
def status(ip: str = DEFAULT_IP) -> None:
    """Connect and print one status snapshot (read-only)."""
    result = _run(_with_client(ip, False, lambda s: _ret(s.status.raw if s.status else {})))
    _print(result)


@app.command(rich_help_panel=PANEL_DEBUG)
def reports(ip: str = DEFAULT_IP) -> None:
    """List the telescope's per-operation telemetry reports (read-only diagnostics)."""

    def _go(s: VaonisClient) -> Any:
        return s.available_reports()

    _print(_run(_with_client(ip, False, _go)))


@app.command(rich_help_panel=PANEL_DEBUG)
def logs(
    out: str = typer.Option("", "--out", help="write the logs to this file instead of stdout"),
    ip: str = DEFAULT_IP,
) -> None:
    """Fetch the telescope's diagnostic logs. [Drains the device log buffer — 'consume'.]"""

    def _go(s: VaonisClient) -> Any:
        return s.consume_logs()

    resp = _run(_with_client(ip, False, _go))
    data = (resp.get("result") or {}).get("data") if isinstance(resp, dict) else None
    text = data if isinstance(data, str) else json.dumps(resp, indent=2, default=str)
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text)
        typer.echo(f"wrote logs to {out}")
    else:
        typer.echo(text)


@app.command(rich_help_panel=PANEL_DEBUG)
def watch(ip: str = DEFAULT_IP, seconds: int = 60) -> None:
    """Stream raw socket events (use to confirm event names / payloads)."""

    async def _watch() -> None:
        scope = VaonisClient(ip=ip)

        async def _dump(event: str, *args: Any) -> None:
            typer.echo(f"[{event}] {json.dumps(args, default=str)[:400]}")

        scope._sock.on_any(_dump)
        await scope.connect(status_timeout=seconds)
        await asyncio.sleep(seconds)
        await scope.disconnect()

    _run(_watch())


@app.command(rich_help_panel=PANEL_CONTROL)
def park(ip: str = DEFAULT_IP) -> None:
    """Close the arm / park the telescope (the app's "Close the arm"). [IDLE ONLY.]"""
    _print(_run(_with_client(ip, True, lambda s: s.park())))


@app.command(rich_help_panel=PANEL_CONTROL)
def stop(ip: str = DEFAULT_IP) -> None:
    """Stop whatever is running — a native plan if one is active, else the current observation."""

    async def _go(s: VaonisClient) -> Any:
        if s.plan_progress() is not None:
            return await s.stop_plan()
        return await s.stop_observation()

    _print(_run(_with_client(ip, True, _go)))


@app.command(rich_help_panel=PANEL_CONTROL)
def reframe(x: int, y: int, rot: float = 0.0, ip: str = DEFAULT_IP) -> None:
    """Change framing: nudge by x/y integer offsets, --rot degrees (takes control).

    [SAFE DURING OBSERVATION] — this is the app's "Change Framing".
    """
    _print(_run(_with_client(ip, True, lambda s: s.adjust_framing(x, y, rot))))


@app.command(rich_help_panel=PANEL_CONTROL)
def restart_autofocus(no_restart_capture: bool = False, ip: str = DEFAULT_IP) -> None:
    """Re-run deep-sky autofocus (also restarts the stack unless --no-restart-capture).

    [SAFE DURING OBSERVATION] — the app's "Restart autofocus".
    """
    _print(
        _run(
            _with_client(
                ip, True, lambda s: s.restart_autofocus(restart_capture=not no_restart_capture)
            )
        )
    )


@app.command(rich_help_panel=PANEL_CONTROL)
def multi_light(
    on: bool = typer.Option(..., "--on/--off", help="enable/disable Multi-Light (HDR)"),
    ip: str = DEFAULT_IP,
) -> None:
    """BalENS: toggle the app's HDR-background processing (a setting; firmware >= 2.28).

    [SAFE DURING OBSERVATION] Distinct from `multi-night` despite the similar name: this changes
    HOW frames are processed (HDR background extraction), not whether the stack is kept across nights.
    """
    _print(_run(_with_client(ip, True, lambda s: s.set_multi_light(on))))


@app.command(rich_help_panel=PANEL_CONTROL)
def balens_level(
    level: str = typer.Argument(..., help="Recommended | Soft | Hard | First-Edition"),
    ip: str = DEFAULT_IP,
) -> None:
    """Set the BalENS (HDR background) processing level. [SAFE DURING OBSERVATION]"""
    _print(_run(_with_client(ip, True, lambda s: s.set_balens_level(level))))


@app.command(rich_help_panel=PANEL_CONTROL)
def multi_night(ip: str = DEFAULT_IP) -> None:
    """Multi-night: mark the current stack resumable so it can keep integrating on a later night.

    [SAFE DURING OBSERVATION] (capture/setToBeResumable.) Distinct from `multi-light` (HDR mode).
    The app's "Save to phone/Singularity" is an image download/cloud upload — a different thing.
    """
    _print(_run(_with_client(ip, True, lambda s: s.enable_multi_night())))


@app.command(rich_help_panel=PANEL_CONTROL)
def shutdown(
    ip: str = DEFAULT_IP,
    yes: bool = typer.Option(False, "--yes", help="confirm: powers off the scope; drops the link"),
) -> None:
    """Power off the telescope board (requires --yes; you must press the button to restart)."""
    if not yes:
        typer.echo(
            "Refusing: shutdown powers off the telescope and drops the link. Re-run with --yes."
        )
        raise typer.Exit(1)
    _print(_run(_with_client(ip, True, lambda s: s.request_shutdown(force=True))))


@app.command(rich_help_panel=PANEL_CONTROL)
def autoinit(
    lat: float | None = typer.Argument(None, help="latitude (default: $VAONIS_LAT)"),
    lon: float | None = typer.Argument(None, help="longitude (default: $VAONIS_LON)"),
    ip: str = DEFAULT_IP,
    skip_autofocus: bool = False,
) -> None:
    """Initialise/align at a location. [IDLE ONLY.]"""
    la, lo = _location(lat, lon)  # CLI > env (autoinit sets position, so don't read it back)
    _print(
        _run(
            _with_client(
                ip, True, lambda s: s.start_autoinit(la, lo, skip_auto_focus=skip_autofocus)
            )
        )
    )


def _parse_mosaic(spec: str) -> tuple[float, float]:
    """Parse a ``WxH`` degrees spec (e.g. ``3.2x2.2``) into ``(width, height)``."""
    try:
        w, h = spec.lower().replace(" ", "").split("x")
        return float(w), float(h)
    except ValueError as err:
        raise typer.BadParameter("--mosaic must be WxH in degrees, e.g. 3.2x2.2") from err


@app.command(rich_help_panel=PANEL_CONTROL)
def observe(
    target: str = typer.Argument(
        "", help="catalog id/designation/name (e.g. M42, Jupiter); omit to use --ra/--de"
    ),
    ra: float | None = typer.Option(None, "--ra", help="manual right ascension (deg)"),
    de: float | None = typer.Option(None, "--de", help="manual declination (deg)"),
    object_name: str = typer.Option("", "--name", help="label for a manual --ra/--de target"),
    gain: int | None = None,
    exposure_us: int | None = None,
    no_stacking: bool = False,
    replace: bool = typer.Option(
        True, "--replace/--no-replace", help="stop a running observation first (take over)"
    ),
    allow_solar: bool = typer.Option(
        False, "--allow-solar", help="permit Sun/near-Sun targets (solar filter only!)"
    ),
    mosaic: str = typer.Option(
        "", "--mosaic", help="deep-sky mosaic field WxH in degrees, e.g. 3.2x2.2 (Advanced obs.)"
    ),
    multi_night: bool = typer.Option(
        False, "--multi-night", help="save the capture so it can be resumed on later nights"
    ),
    ip: str = DEFAULT_IP,
) -> None:
    """Slew to a target and start imaging — `observe M42`, or `observe --ra 83.8 --de -5.4`.

    [IDLE ONLY unless --replace (default), which stops a running observation and takes over.]
    A catalog target uses its recommended settings; --ra/--de starts a manual observation.
    `--mosaic`/`--multi-night` are deep-sky only (catalog targets), like the app's Advanced obs.
    """
    mos = _parse_mosaic(mosaic) if mosaic else None
    if (mos or multi_night) and not target:
        raise typer.BadParameter("--mosaic/--multi-night need a catalog TARGET (deep-sky only)")
    if target:

        async def _go(s: VaonisClient) -> Any:
            return await s.observe_object(
                target,
                replace=replace,
                allow_solar=allow_solar,
                mosaic=mos,
                multi_night=multi_night,
            )
    elif ra is not None and de is not None:
        body = ObservationBody(
            object_name=object_name,
            target_type="MANUAL",
            ra=ra,
            de=de,
            gain=gain,
            exposure_micro_sec=exposure_us,
            # Manual targets have no catalog type → no histogram params to send, so single frames.
            do_stacking=False,
        )

        async def _go(s: VaonisClient) -> Any:
            return await s.start_observation(body, replace=replace, allow_solar=allow_solar)
    else:
        raise typer.BadParameter("give a catalog TARGET, or both --ra and --de for a manual target")

    _print(_run(_with_client(ip, True, _go)))


@app.command(rich_help_panel=PANEL_CONTROL)
def captures(ip: str = DEFAULT_IP) -> None:
    """List saved multi-night captures that can be resumed (captureStore.storedCaptures)."""

    def _list(s: VaonisClient) -> list[dict[str, Any]]:
        return s.stored_captures()

    caps = _run(_with_client(ip, False, _list))  # read-only: no control needed
    if not caps:
        typer.echo("No saved multi-night captures.")
        return
    for c in caps:
        name = (c.get("target") or {}).get("objectName") or c.get("objectName") or ""
        typer.echo(f"{c.get('storeId', '?')}  {name}")


@app.command(rich_help_panel=PANEL_CONTROL)
def resume(
    store_id: str = typer.Argument(
        "", help="storeId to resume (see `vaonis captures`); omit if only one"
    ),
    replace: bool = typer.Option(
        True, "--replace/--no-replace", help="stop a running observation first (take over)"
    ),
    ip: str = DEFAULT_IP,
) -> None:
    """Resume a saved multi-night capture, continuing to stack onto it. [IDLE ONLY unless --replace.]"""

    async def _go(s: VaonisClient) -> Any:
        sid = store_id
        if not sid:
            caps = s.stored_captures()
            if len(caps) == 1:
                sid = caps[0].get("storeId", "")
            else:
                raise typer.BadParameter(
                    f"specify a storeId ({len(caps)} saved captures); see `vaonis captures`"
                )
        return await s.resume_capture(sid, replace=replace)

    _print(_run(_with_client(ip, True, _go)))


@app.command(rich_help_panel=PANEL_PLAN)
def tonight(
    lat: float | None = typer.Argument(None, help="latitude (default: $VAONIS_LAT or the scope)"),
    lon: float | None = typer.Argument(None, help="longitude (default: $VAONIS_LON or the scope)"),
    min_altitude: float = 15.0,
    min_grade: float = 0.0,
    limit: int = 20,
    now: bool = typer.Option(
        False, "--now/--window", help="what's up at this instant, instead of across tonight"
    ),
    require_dark: bool = typer.Option(
        False, "--require-dark", help="(--now only) gate on the Sun being below -10deg right now"
    ),
    ip: str = DEFAULT_IP,
) -> None:
    """What's worth imaging *tonight* at a location.

    By default reports each object's peak altitude over tonight's dark window and when it peaks
    (local time), including targets that haven't risen yet — because "tonight" means the whole
    night, not this instant. Pass --now for a snapshot of what's above the horizon right now (with
    optional --require-dark to gate on darkness). Location comes from LAT/LON, else $VAONIS_LAT/LON,
    else the connected telescope's own position.
    """
    from .astro import is_dark
    from .astro import observing_window
    from .catalog import visible_now
    from .catalog import visible_tonight

    lat, lon = _location(lat, lon, ip=ip, use_scope=True)
    dark = is_dark(lat, lon)
    win = observing_window(lat, lon)
    typer.echo(
        f"dark now: {dark}"
        + (f"  (dark window {_local(win[0])}-{_local(win[1])})" if win else "  (no dark window)")
    )

    if not now:
        rows = visible_tonight(
            lat, lon, min_altitude=min_altitude, min_grade=min_grade, limit=limit
        )
        for v in rows:
            mag = f"mag {v.obj.magnitude}" if v.obj.magnitude is not None else ""
            flag = "up now " if v.up_now else "rises  "
            rec = f"{v.obj.duration}min" if v.obj.duration else ""
            typer.echo(
                f"{_vis_dot(v.peak_altitude)} {v.obj.display_name:<22} "
                f"peak {v.peak_altitude:5.1f}deg @ {_local(v.peak_time)} "
                f"{flag} {rec:>6}  {v.obj.category or '':<18} {mag:<8} (id={v.obj.id})"
            )
        if not rows:
            typer.echo("no dark window tonight, or nothing clears the altitude filter")
        return

    rows = visible_now(
        lat,
        lon,
        min_altitude=min_altitude,
        min_grade=min_grade,
        limit=limit,
        require_dark=require_dark,
    )
    for v in rows:
        mag = f"mag {v.obj.magnitude}" if v.obj.magnitude is not None else ""
        rec = f"{v.obj.duration}min" if v.obj.duration else ""
        typer.echo(
            f"{_vis_dot(v.altitude)} {v.obj.display_name:<22} alt {v.altitude:5.1f}deg  "
            f"{rec:>6}  {v.obj.category or '':<18} {mag:<8} (id={v.obj.id})"
        )
    if not rows:
        typer.echo(
            "nothing up right now with those filters"
            + (" (not dark yet — drop --now to plan tonight)" if require_dark and not dark else "")
        )


@app.command(rich_help_panel=PANEL_PLAN)
def forecast(
    lat: float | None = typer.Argument(None, help="latitude (default: $VAONIS_LAT or the scope)"),
    lon: float | None = typer.Argument(None, help="longitude (default: $VAONIS_LON or the scope)"),
    ip: str = DEFAULT_IP,
) -> None:
    """Is tonight worth imaging? Cloud forecast over the dark window + Moon (needs internet).

    Location comes from LAT/LON, else $VAONIS_LAT/LON, else the connected telescope's position.
    """
    from .weather import assess_night

    lat, lon = _location(lat, lon, ip=ip, use_scope=True)
    night = _run(assess_night(lat, lon))
    if night.dark_start and night.dark_end:
        typer.echo(f"dark window {_local(night.dark_start)}-{_local(night.dark_end)}")
    typer.echo(f"verdict: {night.verdict.upper()} — {night.reason}")
    for h in night.hours or []:
        layers = (
            f"low {h.cloud_low or 0:.0f} mid {h.cloud_mid or 0:.0f} high {h.cloud_high or 0:.0f}"
        )
        typer.echo(f"  {_local(h.time)}  cloud {h.cloud_cover:3.0f}%  ({layers})")


@app.command(rich_help_panel=PANEL_PLAN)
def info(
    object_id: str, json_out: bool = typer.Option(False, "--json", help="raw JSON summary")
) -> None:
    """Show full catalog detail for an object (offline) — the app's object-card data."""
    from .catalog import get_object

    obj = get_object(object_id)
    if obj is None:
        typer.echo(f"unknown object: {object_id!r}")
        raise typer.Exit(1)
    if json_out:
        _print(obj.summary())
        return

    title = obj.display_name
    if obj.designation and obj.designation != title:
        title += f" ({obj.designation})"
    typer.secho(title, fg=typer.colors.CYAN, bold=True)
    rows = [
        ("Type", obj.category_label or obj.category),
        ("Constellation", obj.constellation_name or obj.constellation),
        ("Magnitude", obj.magnitude),
        ("Apparent size", obj.size),
        ("Real size", obj.real_size_display),
        ("Distance", obj.distance_display),
        ("Discovery", obj.discovery_display),
        ("Recommended", f"{obj.duration} min" if obj.duration else None),
    ]
    for label, value in rows:
        if value is not None:
            typer.echo(f"  {label + ':':<14} {value}")
    if obj.description:
        typer.echo("\n" + obj.description)
    if obj.trivia_facts:
        typer.secho("\nDid you know?", bold=True)
        for fact in obj.trivia_facts:
            typer.echo(f"  • {fact}")


@app.command(rich_help_panel=PANEL_LIVE)
def observing(ip: str = DEFAULT_IP) -> None:
    """Show what the scope is doing now (human status + observation/plan detail), read-only."""

    async def _go(scope: VaonisClient) -> Any:
        return await _ret(
            (scope.status_summary(), scope.current_observation(), scope.plan_progress())
        )

    summary, obs, plan = _run(_with_client(ip, False, _go))
    typer.secho(summary, fg=typer.colors.CYAN)
    out: dict[str, Any] = {}
    if obs is not None:
        out = obs.model_dump()
    if plan is not None:
        out["plan"] = plan.model_dump()
    if out:
        _print(out)


def _slugify(name: str) -> str:
    """Filesystem-safe token from an object name (e.g. 'Sombrero Galaxy' -> 'Sombrero_Galaxy')."""
    keep = "".join(c if c.isalnum() or c in "-_" else "_" for c in name).strip("_")
    return keep or "vaonis"


@app.command(rich_help_panel=PANEL_LIVE)
def image(
    out: str = typer.Option(
        "", "--out", "-o", help="output path; default <object>_<frame>.jpg from the live frame"
    ),
    rendered: bool = typer.Option(
        False,
        "--rendered/--fast",
        help="force the slow on-demand render; default --fast grabs the written frame file",
    ),
    timeout: float = typer.Option(
        120.0, "--timeout", help="seconds (only the slow --rendered path)"
    ),
    ip: str = DEFAULT_IP,
) -> None:
    """Download the latest stacked frame (read-only; SAFE during an observation).

    While observing, grabs the current live frame; when idle, falls back to the most recent finished
    run's last frame (use `vaonis recent` to see runs, `vaonis library`/`download` for older
    ones). The frame is already on the scope's disk, so by default (`--fast`) we download that file
    directly — instant. `--rendered` re-renders via the firmware (slow; current frame only). Default
    filename is the object + frame index (e.g. M104_0042.jpg), so repeated runs don't overwrite.
    """

    async def _go(scope: VaonisClient) -> Any:
        img = scope.current_image()
        obs = scope.current_observation()
        if img is not None:  # observing → the live frame
            if rendered and img.capture_id:
                scope.request_timeout = timeout
                data = await scope.fetch_image(img)  # firmware on-demand render (slow)
            else:
                data = await scope.download_file(img.ftp_path)  # written file over FTP (fast)
            name = obs.object_name if obs and obs.object_name else _run_label(img.url_path)
            return data, f"{_slugify(name)}_{img.index:04d}.jpg"
        # idle → the last frame of the newest finished capture, from the FTP library
        frame = await _latest_capture_frame(scope)
        if frame is None:
            return None, None
        return await scope.download_file(frame), _capture_frame_name(frame)

    data, default = _run(_with_client(ip, False, _go))
    if not data:
        typer.echo(
            "nothing to grab — not observing, and no finished captures on disk (`vaonis recent`)."
        )
        raise typer.Exit(1)
    path = out or default
    with open(path, "wb") as fh:
        fh.write(data)
    typer.echo(f"wrote {len(data)} bytes to {path}")


def _run_label(url_path: str) -> str:
    """Derive a run label from an image path, e.g. .../captures/<storeId>/images/IMG.jpg -> storeId."""
    parts = [p for p in url_path.split("/") if p]
    return parts[-3] if len(parts) >= 3 else "vaonis"


async def _latest_capture_frame(scope: VaonisClient) -> str | None:
    """FTP path of the last frame in the newest /system/captures run (None if none)."""
    return await scope.latest_capture_path()


def _capture_frame_name(frame_path: str) -> str:
    """`.../<date>_observation_<obj>/images/IMG_0057.jpg` -> `<obj>_0057.jpg`."""
    parts = frame_path.split("/")
    store = parts[-3] if len(parts) >= 3 else "vaonis"
    obj = store.split("_observation_")[-1] if "_observation_" in store else store
    match = re.search(r"(\d+)", parts[-1])
    return f"{_slugify(obj)}_{int(match.group(1)) if match else 0:04d}.jpg"


@app.command(rich_help_panel=PANEL_LIVE)
def recent(limit: int = 15, ip: str = DEFAULT_IP) -> None:
    """List recent capture runs stored on the telescope (newest first), read-only.

    Each run is a folder under the scope's library; download a frame with
    `vaonis download <path>` or browse with `vaonis library <path>`.
    """

    async def _go(scope: VaonisClient) -> Any:
        captures = plans = []
        with contextlib.suppress(VaonisError):
            captures = [e for e in await scope.library(const.FTP_ROOT) if e.is_dir]
        with contextlib.suppress(VaonisError):
            plans = [e for e in await scope.library("/system/plan") if e.is_dir]
        # captures are date-prefixed → newest first; legacy plan dirs after
        captures.sort(key=lambda e: e.name, reverse=True)
        return captures + plans

    runs = _run(_with_client(ip, False, _go))
    runs.sort(key=lambda e: e.path, reverse=True)  # storeId encodes date → newest first
    for e in runs[:limit]:
        name = e.path.rstrip("/").split("/")[-1]
        typer.echo(f"{name:<48} {e.path}")
    if not runs:
        typer.echo("no stored runs found")


@app.command(rich_help_panel=PANEL_LIVE)
def export(
    capture_id: str,
    out: str = "",
    format: str = "tiff",
    timeout: float = typer.Option(
        300.0, "--timeout", help="seconds; the full-res render blocks server-side and is slow"
    ),
    ip: str = DEFAULT_IP,
) -> None:
    """Render and download a full-res capture (tiff|jxl) by captureId.

    [SAFE DURING OBSERVATION — but the on-demand render competes with stacking.] The telescope
    renders the full-resolution image fresh (there is no pre-written full-res file to grab, unlike
    the live preview), and the request blocks until it finishes — minutes, sometimes — so the
    default --timeout is generous. The default 20s client timeout is why a plain export "hangs".
    """

    async def _go(scope: VaonisClient) -> Any:
        scope.request_timeout = timeout  # the render (POST) + download both use this
        return await scope.export_capture(capture_id, format)

    typer.echo(
        f"rendering {format} of {capture_id} (full-res render is slow; waiting up to {timeout:.0f}s)…"
    )
    data = _run(_with_client(ip, False, _go))
    path = out or f"{capture_id}.{'tif' if format == 'tiff' else 'jxl'}"
    with open(path, "wb") as fh:
        fh.write(data)
    typer.echo(f"wrote {len(data)} bytes to {path}")


@app.command(rich_help_panel=PANEL_LIVE)
def library(
    path: str = typer.Argument("/", help="FTP directory to list"),
    ip: str = DEFAULT_IP,
) -> None:
    """List the saved-image library over FTP (no control needed)."""

    async def _go(scope: VaonisClient) -> Any:
        return await scope.library(path)

    entries = _run(_with_client(ip, False, _go))
    for entry in entries:
        kind = "dir " if entry.is_dir else "file"
        size = f"{entry.size:>10}" if entry.size else " " * 10
        typer.echo(f"{kind} {size}  {entry.path}")
    if not entries:
        typer.echo(f"(empty: {path})")


@app.command(rich_help_panel=PANEL_LIVE)
def download(path: str, out: str = "", ip: str = DEFAULT_IP) -> None:
    """Download a saved file from the library by its FTP path."""
    import os

    async def _go(scope: VaonisClient) -> Any:
        return await scope.download_file(path)

    data = _run(_with_client(ip, False, _go))
    target = out or os.path.basename(path)
    with open(target, "wb") as fh:
        fh.write(data)
    typer.echo(f"wrote {len(data)} bytes to {target}")


@app.command(rich_help_panel=PANEL_CONTROL)
def plan(
    targets: list[str],
    lat: float | None = typer.Option(None, "--lat", help="latitude (default: env or the scope)"),
    lon: float | None = typer.Option(None, "--lon", help="longitude (default: env or the scope)"),
    name: str = "pyvaonis plan",
    wait_for_dark: bool = typer.Option(
        False, "--wait-for-dark", help="schedule the plan to start at the next dusk (Sun < -10°)"
    ),
    start_in: float = typer.Option(0.0, "--start-in", help="minutes from now to start (overrides)"),
    ip: str = DEFAULT_IP,
) -> None:
    """Start the telescope's NATIVE autonomous plan, e.g. `plan M42:30 M51:20 Jupiter:10`.

    [IDLE ONLY.] Uploads one Plan-My-Night (`planner/startPlan`) and returns: the firmware then
    auto-initialises and runs every target on schedule by itself, so you can disconnect. Watch it
    with `vaonis observing`; cancel with `vaonis stop-plan`. Location comes from --lat/--lon,
    else $VAONIS_LAT/LON, else the scope's own position. NB: the native plan parks at the end on
    its own but has no power-off step — leave a session connected if you want `shutdown`.
    """
    from datetime import UTC
    from datetime import datetime
    from datetime import timedelta

    from .astro import observing_window
    from .plan import PlanItem

    items = [PlanItem.parse(t) for t in targets]

    async def _go(scope: VaonisClient) -> Any:
        la, lo = _location(lat, lon, fallback=scope.location())  # CLI > env > scope's own position
        start_time: datetime | None = None
        if start_in:
            start_time = datetime.now(UTC) + timedelta(minutes=start_in)
        elif wait_for_dark:
            window = observing_window(la, lo)
            if window is None:
                raise VaonisError("no dark window in the next 24h; refusing to schedule")
            start_time = max(window[0], datetime.now(UTC))
        when = f" starting {start_time:%H:%M}Z" if start_time else " now"
        typer.echo(f"uploading plan {name!r} ({len(items)} targets){when}…")
        return await scope.start_plan(
            items, name=name, latitude=la, longitude=lo, start_time=start_time
        )

    _print(_run(_with_client(ip, True, _go)))


@app.command(rich_help_panel=PANEL_CONTROL)
def stop_plan(ip: str = DEFAULT_IP) -> None:
    """Cancel the running native plan (planner/stopPlan)."""
    _print(_run(_with_client(ip, True, lambda s: s.stop_plan())))


@app.command(rich_help_panel=PANEL_DEBUG)
def api(
    endpoint: str,
    method: str = typer.Option(
        "", "--method", "-X", help="HTTP method (default: GET, or POST if -f given)"
    ),
    field: list[str] = typer.Option(
        [],
        "--field",
        "-f",
        help="Body field key=value (value JSON-parsed if possible). Repeatable.",
    ),
    data: str = typer.Option("", "--input", "-d", help="Raw JSON body (overrides -f)."),
    control: bool = typer.Option(
        True, "--control/--no-control", help="Take control before non-GET calls."
    ),
    unsafe: bool = typer.Option(
        False, "--unsafe", help="Allow destructive (delete/reset) or solar (sun/*) endpoints."
    ),
    ip: str = DEFAULT_IP,
) -> None:
    """Make a signed REST call, gh-style. Prints HTTP status + JSON body.

    Firmware upload is always blocked; delete/reset/solar endpoints need --unsafe.

    Examples:
      vaonis api app/status
      vaonis api general/park -X POST
      vaonis api general/setUserParams -f gain=20 -f exposureMicroSec=10000000
    """
    body: dict[str, Any] | None = None
    if data:
        body = json.loads(data)
    elif field:
        body = {}
        for item in field:
            key, _, value = item.partition("=")
            try:
                body[key] = json.loads(value)
            except (ValueError, TypeError):
                body[key] = value
    verb = method.upper() or ("POST" if body is not None else "GET")

    async def _go(scope: VaonisClient) -> Any:
        if control and verb != "GET":
            await scope.take_control()
        return await scope.call(verb, endpoint, body, allow_unsafe=unsafe)

    result = _run(_with_client(ip, False, _go))
    typer.echo(f"HTTP {result['status']}", err=True)
    _print(result["body"])
    raise typer.Exit(0 if result["status"] < 400 else 1)


@app.command(rich_help_panel=PANEL_DEBUG)
def doctor(ip: str = DEFAULT_IP) -> None:
    """Probe connectivity to the telescope (reachability only — run this first)."""

    def _err(err: Exception) -> str:
        return str(err) or type(err).__name__

    async def _tcp(port: int) -> str:
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=5)
            writer.close()
        except Exception as err:
            return _err(err)
        return ""

    async def _sio() -> str:
        client = VaonisClient(ip=ip)
        try:
            await client.connect(status_timeout=15)
            ok = client.status is not None and client.status.can_authenticate
            return "" if ok else "connected but no status/challenge"
        except Exception as err:
            return _err(err)
        finally:
            await client.disconnect()

    async def _ftp() -> str:
        try:
            from . import ftp as ftp_mod

            await ftp_mod.list_dir("/user", ip=ip, timeout=10)
        except Exception as err:
            return _err(err)
        return ""

    async def _probe() -> int:
        checks = [
            (f"TCP {ip}:{const.HTTP_PORT} (REST/images)", await _tcp(const.HTTP_PORT)),
            (f"TCP {ip}:{const.SOCKET_PORT} (socket.io)", await _tcp(const.SOCKET_PORT)),
            (f"TCP {ip}:{const.FTP_PORT} (FTP)", await _tcp(const.FTP_PORT)),
            ("socket.io status + challenge", await _sio()),
            ("FTP anonymous login + list /user", await _ftp()),
        ]
        failures = 0
        for label, err in checks:
            ok = not err
            typer.echo(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" — {err}" if err else ""))
            failures += 0 if ok else 1
        return failures

    failures = _run(_probe())
    raise typer.Exit(1 if failures else 0)


@app.command(rich_help_panel=PANEL_DEBUG)
def selftest(lat: float = 0.0, lon: float = 0.0, ip: str = DEFAULT_IP) -> None:
    """End-to-end checklist: connect, status, control, image, library, export.

    Optional steps (live image / export) are skipped cleanly when the scope is idle.
    """

    async def _go() -> int:
        results: list[tuple[str, str, str]] = []  # (name, status, detail)

        def record(name: str, ok: bool | None, detail: str = "") -> None:
            status = "PASS" if ok else ("SKIP" if ok is None else "FAIL")
            results.append((name, status, detail))
            typer.echo(f"[{status}] {name}" + (f" — {detail}" if detail else ""))

        client = VaonisClient(ip=ip)
        required_failed = 0
        try:
            try:
                status = await client.connect(status_timeout=15)
                record("socket.io connect + status", True, f"telescope={status.telescope_id}")
            except Exception as err:
                record("socket.io connect + status", False, str(err))
                return 1  # nothing else works without this

            record("auth fields present", status.can_authenticate)
            if not status.can_authenticate:
                required_failed += 1

            try:
                await client.take_control()
                record("take control", True)
            except Exception as err:
                record("take control", False, str(err))
                required_failed += 1

            try:
                resp = await client.call("GET", const.Endpoint.APP_STATUS)
                ok = resp["status"] < 400
                record("GET app/status (auth works)", ok, f"HTTP {resp['status']}")
                required_failed += 0 if ok else 1
            except Exception as err:
                record("GET app/status (auth works)", False, str(err))
                required_failed += 1

            obs = client.current_observation()
            record(
                "current observation parsed",
                bool(obs),
                (obs.object_name or "(unnamed)") if obs else "idle",
            )

            img = client.current_image()
            if img is None:
                record("download live frame", None, "no active observation")
            else:
                try:
                    data = await client.fetch_current_image()
                    record("download live frame", bool(data), f"{len(data or b'')} bytes")
                except Exception as err:
                    record("download live frame", False, str(err))

            try:
                entries = await client.library("/user")
                record("FTP library list", True, f"{len(entries)} entries")
            except Exception as err:
                record("FTP library list", False, str(err))

            cap = img.capture_id if img else None
            if not cap:
                record("full-res export", None, "no capture to export")
            else:
                try:
                    url = await client.export_url(cap, "tiff")
                    record("full-res export", True, url)
                except Exception as err:
                    record("full-res export", False, str(err))

            if lat or lon:
                from .astro import is_dark
                from .astro import observing_window

                window = observing_window(lat, lon)
                detail = f"dark={is_dark(lat, lon)}"
                if window:
                    detail += f" window={window[0]:%H:%M}-{window[1]:%H:%M}Z"
                record("darkness check", True, detail)
        finally:
            await client.disconnect()

        typer.echo(
            f"\n{sum(s == 'PASS' for _, s, _ in results)} passed, "
            f"{sum(s == 'FAIL' for _, s, _ in results)} failed, "
            f"{sum(s == 'SKIP' for _, s, _ in results)} skipped"
        )
        return required_failed

    raise typer.Exit(1 if _run(_go()) else 0)


@app.command(rich_help_panel=PANEL_DEBUG)
def post(
    endpoint: str,
    json_body: str = "{}",
    ip: str = DEFAULT_IP,
    unsafe: bool = typer.Option(False, "--unsafe", help="Allow destructive/solar endpoints."),
) -> None:
    """Raw signed POST to a ``/v1`` endpoint (see also: `api`)."""
    body = json.loads(json_body)
    _print(_run(_with_client(ip, True, lambda s: s.post(endpoint, body, allow_unsafe=unsafe))))


@app.command(rich_help_panel=PANEL_DEBUG)
def get(endpoint: str, ip: str = DEFAULT_IP) -> None:
    """Raw signed GET of a ``/v1`` endpoint."""
    _print(_run(_with_client(ip, False, lambda s: s.get(endpoint))))


async def _ret(value: Any) -> Any:
    return value


if __name__ == "__main__":
    app()
