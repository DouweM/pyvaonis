"""Command-line interface: ``stellina <command>``.

Run on a machine joined to the telescope's ``STELLINA-xxxx`` Wi-Fi (or reachable
through a bridge). Requires the ``cli`` extra: ``pip install "pystellina[cli]"``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import typer

from . import const
from .client import StellinaClient
from .models import ObservationBody

app = typer.Typer(help="Control a Vaonis Stellina over its local Wi-Fi.", no_args_is_help=True)


@app.callback()
def _main(
    debug: bool = typer.Option(False, "--debug", help="Log wire traffic (socket.io/HTTP)."),
) -> None:
    """Stellina CLI."""
    if debug:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")
        for name in ("pystellina", "aiohttp", "asyncio"):
            logging.getLogger(name).setLevel(logging.DEBUG)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _with_client(ip: str, control: bool, fn: Any) -> Any:
    async with StellinaClient(ip=ip) as scope:
        if control:
            await scope.take_control()
        return await fn(scope)


def _print(data: Any) -> None:
    typer.echo(json.dumps(data, indent=2, default=str))


@app.command()
def status(ip: str = const.DEFAULT_IP) -> None:
    """Connect and print one status snapshot (read-only)."""
    result = _run(_with_client(ip, False, lambda s: _ret(s.status.raw if s.status else {})))
    _print(result)


@app.command()
def watch(ip: str = const.DEFAULT_IP, seconds: int = 60) -> None:
    """Stream raw socket events (use to confirm event names / payloads)."""

    async def _watch() -> None:
        scope = StellinaClient(ip=ip)

        async def _dump(event: str, *args: Any) -> None:
            typer.echo(f"[{event}] {json.dumps(args, default=str)[:400]}")

        scope._sock.on_any(_dump)
        await scope.connect(status_timeout=seconds)
        await asyncio.sleep(seconds)
        await scope.disconnect()

    _run(_watch())


@app.command()
def park(ip: str = const.DEFAULT_IP) -> None:
    """Park the telescope."""
    _print(_run(_with_client(ip, True, lambda s: s.park())))


@app.command()
def stop(ip: str = const.DEFAULT_IP) -> None:
    """Stop the current observation."""
    _print(_run(_with_client(ip, True, lambda s: s.stop_observation())))


@app.command()
def shutdown(
    ip: str = const.DEFAULT_IP,
    yes: bool = typer.Option(False, "--yes", help="confirm: powers off the scope; drops the link"),
) -> None:
    """Power off the telescope board (requires --yes; you must press the button to restart)."""
    if not yes:
        typer.echo(
            "Refusing: shutdown powers off the telescope and drops the link. Re-run with --yes."
        )
        raise typer.Exit(1)
    _print(_run(_with_client(ip, True, lambda s: s.request_shutdown(force=True))))


@app.command()
def autoinit(
    lat: float, lon: float, ip: str = const.DEFAULT_IP, skip_autofocus: bool = False
) -> None:
    """Initialise/align at a location."""
    _print(
        _run(
            _with_client(
                ip, True, lambda s: s.start_autoinit(lat, lon, skip_auto_focus=skip_autofocus)
            )
        )
    )


@app.command()
def observe(
    object_name: str = "",
    object_id: str = "",
    ra: float | None = None,
    de: float | None = None,
    gain: int | None = None,
    exposure_us: int | None = None,
    no_stacking: bool = False,
    ip: str = const.DEFAULT_IP,
) -> None:
    """Slew to a target and start imaging."""
    body = ObservationBody(
        object_id=object_id,
        object_name=object_name,
        ra=ra,
        de=de,
        gain=gain,
        exposure_micro_sec=exposure_us,
        do_stacking=not no_stacking,
    )
    _print(_run(_with_client(ip, True, lambda s: s.start_observation(body))))


@app.command()
def tonight(
    lat: float,
    lon: float,
    min_altitude: float = 15.0,
    min_grade: float = 0.0,
    limit: int = 20,
    require_dark: bool = False,
) -> None:
    """List catalog objects currently visible at a location (offline, no telescope).

    Pass --require-dark to only list targets when the Sun is below -10deg (as the app does).
    """
    from .astro import is_dark
    from .astro import observing_window
    from .catalog import visible_now

    dark = is_dark(lat, lon)
    window = observing_window(lat, lon)
    typer.echo(
        f"dark now: {dark}"
        + (f"  (dark window {window[0]:%H:%M}-{window[1]:%H:%M} UTC)" if window else "")
    )
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
        typer.echo(
            f"{v.obj.display_name:<22} alt {v.altitude:5.1f}deg  grade {v.obj.grade}  "
            f"{v.obj.category or '':<18} {mag:<8} (id={v.obj.id})"
        )
    if not rows:
        typer.echo(
            "nothing to observe with those filters"
            + (" (not dark yet)" if require_dark and not dark else "")
        )


@app.command()
def info(object_id: str) -> None:
    """Show full catalog detail for an object (offline)."""
    from .catalog import get_object

    obj = get_object(object_id)
    if obj is None:
        typer.echo(f"unknown object: {object_id!r}")
        raise typer.Exit(1)
    _print(obj.summary())
    if obj.description:
        typer.echo("\n" + obj.description)


@app.command()
def observe_object(object_id: str, ip: str = const.DEFAULT_IP) -> None:
    """Slew to a catalog object by id/name (e.g. M42, Jupiter) using its recommended settings."""
    _print(_run(_with_client(ip, True, lambda s: s.observe_object(object_id))))


@app.command()
def observing(ip: str = const.DEFAULT_IP) -> None:
    """Show the current observation (target, step, stacking), read-only."""

    async def _go(scope: StellinaClient) -> Any:
        return await _ret(scope.current_observation())

    obs = _run(_with_client(ip, False, _go))
    _print(obs.model_dump() if obs is not None else {"observing": False})


@app.command()
def image(out: str = "stellina.jpg", ip: str = const.DEFAULT_IP) -> None:
    """Download the current live-stacked frame to a file."""

    async def _go(scope: StellinaClient) -> Any:
        return await scope.fetch_current_image()

    data = _run(_with_client(ip, False, _go))
    if not data:
        typer.echo("no current image (telescope is not observing)")
        raise typer.Exit(1)
    with open(out, "wb") as fh:
        fh.write(data)
    typer.echo(f"wrote {len(data)} bytes to {out}")


@app.command()
def export(
    capture_id: str,
    out: str = "",
    format: str = "tiff",
    ip: str = const.DEFAULT_IP,
) -> None:
    """Render and download a full-res capture (tiff|jxl) by captureId."""

    async def _go(scope: StellinaClient) -> Any:
        return await scope.export_capture(capture_id, format)

    data = _run(_with_client(ip, False, _go))
    path = out or f"{capture_id}.{'tif' if format == 'tiff' else 'jxl'}"
    with open(path, "wb") as fh:
        fh.write(data)
    typer.echo(f"wrote {len(data)} bytes to {path}")


@app.command()
def library(path: str = "/user", ip: str = const.DEFAULT_IP) -> None:
    """List the saved-image library over FTP (no control needed)."""

    async def _go(scope: StellinaClient) -> Any:
        return await scope.library(path)

    for entry in _run(_with_client(ip, False, _go)):
        kind = "dir " if entry.is_dir else "file"
        size = f"{entry.size:>10}" if entry.size else " " * 10
        typer.echo(f"{kind} {size}  {entry.path}")


@app.command()
def download(path: str, out: str = "", ip: str = const.DEFAULT_IP) -> None:
    """Download a saved file from the library by its FTP path."""
    import os

    async def _go(scope: StellinaClient) -> Any:
        return await scope.download_file(path)

    data = _run(_with_client(ip, False, _go))
    target = out or os.path.basename(path)
    with open(target, "wb") as fh:
        fh.write(data)
    typer.echo(f"wrote {len(data)} bytes to {target}")


@app.command()
def sequence(
    targets: list[str],
    lat: float,
    lon: float,
    ip: str = const.DEFAULT_IP,
    require_dark: bool = True,
    wait_for_dark: bool = False,
    park: bool = True,
    shutdown: bool = False,
) -> None:
    """Run an observing plan, e.g. `sequence M42:30 M51:20 Jupiter:10 --lat 52.4 --lon 4.9`."""
    from .sequence import SequenceEvent
    from .sequence import SequenceItem
    from .sequence import run_sequence

    items = [SequenceItem.parse(t) for t in targets]

    def on_event(ev: SequenceEvent) -> None:
        label = ev.item.target if ev.item else ""
        typer.echo(
            f"[{ev.kind}] {label} {json.dumps(ev.detail, default=str) if ev.detail else ''}".rstrip()
        )

    async def _go() -> None:
        async with StellinaClient(ip=ip) as scope:
            await run_sequence(
                scope,
                items,
                latitude=lat,
                longitude=lon,
                require_dark=require_dark,
                wait_for_dark=wait_for_dark,
                park_at_end=park,
                shutdown_at_end=shutdown,
                on_event=on_event,
            )

    _run(_go())


@app.command()
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
    ip: str = const.DEFAULT_IP,
) -> None:
    """Make a signed REST call, gh-style. Prints HTTP status + JSON body.

    Firmware upload is always blocked; delete/reset/solar endpoints need --unsafe.

    Examples:
      stellina api app/status
      stellina api general/park -X POST
      stellina api general/setUserParams -f gain=20 -f exposureMicroSec=10000000
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

    async def _go(scope: StellinaClient) -> Any:
        if control and verb != "GET":
            await scope.take_control()
        return await scope.call(verb, endpoint, body, allow_unsafe=unsafe)

    result = _run(_with_client(ip, False, _go))
    typer.echo(f"HTTP {result['status']}", err=True)
    _print(result["body"])
    raise typer.Exit(0 if result["status"] < 400 else 1)


@app.command()
def doctor(ip: str = const.DEFAULT_IP) -> None:
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
        client = StellinaClient(ip=ip)
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


@app.command()
def selftest(lat: float = 0.0, lon: float = 0.0, ip: str = const.DEFAULT_IP) -> None:
    """End-to-end checklist: connect, status, control, image, library, export.

    Optional steps (live image / export) are skipped cleanly when the scope is idle.
    """

    async def _go() -> int:
        results: list[tuple[str, str, str]] = []  # (name, status, detail)

        def record(name: str, ok: bool | None, detail: str = "") -> None:
            status = "PASS" if ok else ("SKIP" if ok is None else "FAIL")
            results.append((name, status, detail))
            typer.echo(f"[{status}] {name}" + (f" — {detail}" if detail else ""))

        client = StellinaClient(ip=ip)
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


@app.command()
def post(
    endpoint: str,
    json_body: str = "{}",
    ip: str = const.DEFAULT_IP,
    unsafe: bool = typer.Option(False, "--unsafe", help="Allow destructive/solar endpoints."),
) -> None:
    """Raw signed POST to a ``/v1`` endpoint (see also: `api`)."""
    body = json.loads(json_body)
    _print(_run(_with_client(ip, True, lambda s: s.post(endpoint, body, allow_unsafe=unsafe))))


@app.command()
def get(endpoint: str, ip: str = const.DEFAULT_IP) -> None:
    """Raw signed GET of a ``/v1`` endpoint."""
    _print(_run(_with_client(ip, False, lambda s: s.get(endpoint))))


async def _ret(value: Any) -> Any:
    return value


if __name__ == "__main__":
    app()
