"""Command-line interface: `homelabscribe run`, `scan`, `render`, `doctor`, `demo`, `license`."""

from __future__ import annotations

import re
import time
from pathlib import Path

import typer

from homelabscribe import __version__, licensing, notify, pipeline
from homelabscribe.config import Config, load_config

app = typer.Typer(
    add_completion=False, no_args_is_help=True, help="Documentation for your homelab that writes itself."
)

ConfigOpt = typer.Option(
    Path("homelabscribe.yaml"),
    "--config",
    "-c",
    envvar="HOMELABSCRIBE_CONFIG",
    help="Path to homelabscribe.yaml",
)


def _load(config: Path) -> tuple[Config, frozenset[str], str]:
    cfg = load_config(config)
    feats, status = licensing.features(cfg.license_key)
    return cfg, feats, status


def _report_scan(result: pipeline.ScanResult) -> None:
    s = result.snapshot
    typer.echo(f"Scanned {len(s.hosts)} host(s), {len(s.services)} service(s).")
    for msg in result.skipped:
        typer.secho(f"  skipped: {msg}", fg="yellow")
    for msg in result.errors:
        typer.secho(f"  error: {msg}", fg="red")
    _report_file_skips(result.file_skips)
    if result.diff and not result.diff.empty:
        typer.echo(f"{len(result.diff.changes)} change(s) since the last scan.")


def _report_file_skips(lines: list[str]) -> None:
    for line in lines:
        typer.secho(f"  skipped: {line}", fg="yellow")


def _report_rendered(rendered: pipeline.Rendered, cfg: Config) -> None:
    """The lines after a render: files left alone, how many were written, and the HTML copy."""
    _report_file_skips(rendered.skipped)
    typer.echo(f"Updated {len(rendered.written)} file(s) in {cfg.output_dir}.")
    if rendered.html:
        shown = {line.split(" (", 1)[0] for line in rendered.skipped}
        for page in rendered.html.skipped:
            if page.split(" (", 1)[0] not in shown:  # a page already reported above is not reported twice
                typer.secho(f"  skipped: {page}", fg="yellow")
        typer.echo(f"Updated {len(rendered.html.changed)} HTML file(s) in {cfg.html_dir}.")


def _scan(cfg: Config, feats: frozenset[str]) -> pipeline.ScanResult:
    try:
        return pipeline.scan(cfg, feats)
    except pipeline.StateFolderError as e:
        typer.secho(f"error: {e}. Nothing was scanned or saved.", fg="red")
        raise typer.Exit(1) from e


def _render(cfg: Config, feats: frozenset[str], snapshot=None) -> pipeline.Rendered:
    try:
        return pipeline.render_pages(cfg, feats, snapshot)
    except RuntimeError as e:  # no snapshot to render
        typer.secho(f"error: {e}", fg="red")
        raise typer.Exit(1) from e
    except OSError as e:  # an old page that holds notes could not be archived: it stays where it is
        typer.secho(
            f"error: a page with notes could not be archived ({type(e).__name__}); it was left as it is.",
            fg="red",
        )
        raise typer.Exit(1) from e


def _notify(cfg: Config, feats: frozenset[str], result: pipeline.ScanResult) -> None:
    if not cfg.notify:
        return
    if "notify" not in feats:
        typer.secho("Notifications need Pro. Skipped.", fg="yellow")
        return
    for msg in notify.notify_after_scan(cfg.notify, cfg.state_dir, result.diff, result.errors):
        typer.secho(f"  notification failed: {msg}", fg="red")


def _interval(text: str) -> int:
    m = re.fullmatch(r"(\d+)\s*([smhd])", text.strip())
    if not m:
        raise typer.BadParameter("Use a number and unit, e.g. 30m, 6h, 1d.")
    return int(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]


StrictOpt = typer.Option(
    False,
    "--strict",
    help="Exit with status 1 if a host or proxy could not be read or a file was skipped (docs are written).",
)


@app.command()
def scan(config: Path = ConfigOpt, strict: bool = StrictOpt):
    """Collect a new snapshot of your lab (read-only)."""
    cfg, feats, _ = _load(config)
    result = _scan(cfg, feats)
    _report_scan(result)
    if strict and (result.errors or result.file_skips):
        raise typer.Exit(1)


@app.command()
def render(config: Path = ConfigOpt):
    """Write Markdown docs from the latest snapshot (and their HTML copy, when `html_dir` is set)."""
    cfg, feats, _ = _load(config)
    problem = pipeline.html_dir_problem(cfg)
    if problem:
        typer.secho(problem, fg="red")
        raise typer.Exit(1)
    _report_rendered(_render(cfg, feats), cfg)


@app.command()
def run(
    config: Path = ConfigOpt,
    every: str = typer.Option(None, "--every", help="Repeat on a schedule, e.g. 6h or 1d (Pro)."),
    strict: bool = StrictOpt,
):
    """Scan, then render. The usual command."""
    cfg, feats, status = _load(config)
    problem = pipeline.html_dir_problem(cfg)
    if problem:
        typer.secho(problem, fg="red")
        raise typer.Exit(1)
    typer.echo(f"HomelabScribe {__version__} · {status}")
    if every and "schedule" not in feats:
        typer.secho("Scheduled runs need Pro. Running once.", fg="yellow")
        every = None
    while True:
        result = _scan(cfg, feats)
        _report_scan(result)
        _notify(cfg, feats, result)
        rendered = _render(cfg, feats, result.snapshot)
        _report_rendered(rendered, cfg)
        if not every:
            if strict and (result.errors or result.file_skips or rendered.skipped):
                raise typer.Exit(1)
            break
        time.sleep(_interval(every))


@app.command()
def doctor(
    config: Path = ConfigOpt,
    bundle: bool = typer.Option(
        False, "--bundle", help="Also write a redacted archive (a .zip of text files) for a bug report."
    ),
    out: Path = typer.Option(
        None, "--out", "-o", help="With --bundle: the archive to write. An existing file is not overwritten."
    ),
):
    """Check config, connections and license. Safe to paste into a bug report."""
    from homelabscribe import bundle as bundles

    if out is not None and not bundle:
        raise typer.BadParameter("it is only used together with --bundle.", param_hint="--out")
    cfg, feats, status = _load(config)
    checks: list[bundles.Check] = []  # what each connection test found; the bundle is built from these
    typer.echo(f"HomelabScribe {__version__}")
    typer.echo(f"Config:   {config} ({'found' if config.exists() else 'not found, using defaults'})")
    typer.echo(f"License:  {status}")
    typer.echo(f"Output:   {cfg.output_dir}")
    for i, h in enumerate(cfg.docker_hosts):
        try:
            import docker

            info = docker.DockerClient(base_url=h.url, timeout=5).api.info()
            typer.secho(
                f"Docker '{h.name}': OK "
                f"(Docker {info.get('ServerVersion')}, {info.get('Containers')} containers)",
                fg="green",
            )
            checks.append(
                bundles.Check(
                    "docker", i, "ok", version=info.get("ServerVersion"), count=info.get("Containers")
                )
            )
        except Exception as e:
            typer.secho(f"Docker '{h.name}': FAILED ({type(e).__name__})", fg="red")
            checks.append(bundles.Check("docker", i, "failed", error=type(e).__name__))
    for i, n in enumerate(cfg.npm):
        try:
            from homelabscribe.collectors.npm import NpmCollector

            count = len(NpmCollector(n).collect())
            typer.secho(f"Nginx Proxy Manager '{n.name}': OK ({count} proxy hosts)", fg="green")
            checks.append(bundles.Check("npm", i, "ok", count=count))
        except Exception as e:
            typer.secho(f"Nginx Proxy Manager '{n.name}': FAILED ({type(e).__name__})", fg="red")
            checks.append(bundles.Check("npm", i, "failed", error=type(e).__name__))
    for i, p in enumerate(cfg.proxmox):
        if "proxmox" not in feats:
            typer.secho(f"Proxmox '{p.name}': configured, needs Pro", fg="yellow")
            checks.append(bundles.Check("proxmox", i, "skipped"))
            continue
        try:
            from homelabscribe.collectors.proxmox import ProxmoxCollector

            v = ProxmoxCollector(p).api.version.get()
            typer.secho(f"Proxmox '{p.name}': OK (PVE {v.get('version')})", fg="green")
            checks.append(bundles.Check("proxmox", i, "ok", version=v.get("version")))
        except Exception as e:
            typer.secho(f"Proxmox '{p.name}': FAILED ({type(e).__name__})", fg="red")
            checks.append(bundles.Check("proxmox", i, "failed", error=type(e).__name__))
    if not bundle:
        return
    try:
        written = bundles.create(cfg, config, checks, out)
    except FileExistsError as e:
        typer.secho(f"{e.filename} already exists and was not overwritten. No bundle written.", fg="red")
        raise typer.Exit(1) from e
    except OSError as e:
        typer.secho(f"The bundle could not be written ({type(e).__name__}).", fg="red")
        raise typer.Exit(1) from e
    typer.echo(f"Wrote {written}")
    typer.echo("Look inside before you share it: it is plain text, and you are the last check.")


@app.command()
def demo(
    out: Path = typer.Option(
        Path("homelabscribe-demo"), "--out", "-o", help="Folder to write the demo docs to"
    ),
    html: Path = typer.Option(
        None, "--html", help="Also write a static HTML copy of the demo docs to this folder (its own folder)."
    ),
):
    """Render docs for a made-up homelab, with every Pro feature on. No Docker needed."""
    from homelabscribe import htmlsite
    from homelabscribe.demo import demo_snapshots
    from homelabscribe.diff import compare
    from homelabscribe.enrich import enrich, load_catalog
    from homelabscribe.renderer import Renderer

    problem = htmlsite.folder_problem(html, {"the demo folder": out}) if html is not None else None
    if problem:
        typer.secho(problem, fg="red")
        raise typer.Exit(1)
    old, new = demo_snapshots()
    catalog = load_catalog()
    enrich(old.services, catalog)
    enrich(new.services, catalog)
    d = compare(old, new)
    log = [{"at": new.taken_at.isoformat(), "changes": [c.model_dump() for c in d.changes]}]
    cfg = Config()
    cfg.breakglass.owner = "Alex"
    cfg.breakglass.contacts = [
        "Sam (brother-in-law, knows computers) — 555-0142",
        "Internet provider support — 555-0199",
    ]
    cfg.breakglass.critical = ["pihole", "plex", "vaultwarden", "homeassistant"]
    renderer = Renderer(out, licensing.PRO_FEATURES)
    written = renderer.render(new, log, cfg.breakglass)
    typer.echo(f"Wrote {len(written)} file(s) to {out}/. Open {out}/index.md to start.")
    if html is not None:
        exported = htmlsite.export(out, html, renderer.generated)
        for page in exported.skipped:
            typer.secho(f"  skipped: {page}", fg="yellow")
        typer.echo(f"Wrote {len(exported.changed)} HTML file(s) to {html}/. Open {html}/index.html to start.")


@app.command("license")
def license_cmd(config: Path = ConfigOpt):
    """Show license status."""
    _, _, status = _load(config)
    typer.echo(status)


@app.command()
def version():
    """Print the version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
