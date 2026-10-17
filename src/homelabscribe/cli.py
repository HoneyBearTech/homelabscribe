"""Command-line interface: `homelabscribe run`, `scan`, `render`, `doctor`, `demo`, `license`."""

from __future__ import annotations

import re
import time
from pathlib import Path

import typer

from homelabscribe import __version__, licensing, pipeline
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
    if result.diff and not result.diff.empty:
        typer.echo(f"{len(result.diff.changes)} change(s) since the last scan.")


def _interval(text: str) -> int:
    m = re.fullmatch(r"(\d+)\s*([smhd])", text.strip())
    if not m:
        raise typer.BadParameter("Use a number and unit, e.g. 30m, 6h, 1d.")
    return int(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]


StrictOpt = typer.Option(
    False,
    "--strict",
    help="Exit with status 1 if any host or proxy could not be read (docs are still written).",
)


@app.command()
def scan(config: Path = ConfigOpt, strict: bool = StrictOpt):
    """Collect a new snapshot of your lab (read-only)."""
    cfg, feats, _ = _load(config)
    result = pipeline.scan(cfg, feats)
    _report_scan(result)
    if strict and result.errors:
        raise typer.Exit(1)


@app.command()
def render(config: Path = ConfigOpt):
    """Write Markdown docs from the latest snapshot."""
    cfg, feats, _ = _load(config)
    written = pipeline.render(cfg, feats)
    typer.echo(f"Updated {len(written)} file(s) in {cfg.output_dir}.")


@app.command()
def run(
    config: Path = ConfigOpt,
    every: str = typer.Option(None, "--every", help="Repeat on a schedule, e.g. 6h or 1d (Pro)."),
    strict: bool = StrictOpt,
):
    """Scan, then render. The usual command."""
    cfg, feats, status = _load(config)
    typer.echo(f"HomelabScribe {__version__} · {status}")
    if every and "schedule" not in feats:
        typer.secho("Scheduled runs need Pro. Running once.", fg="yellow")
        every = None
    while True:
        result = pipeline.scan(cfg, feats)
        _report_scan(result)
        written = pipeline.render(cfg, feats, result.snapshot)
        typer.echo(f"Updated {len(written)} file(s) in {cfg.output_dir}.")
        if not every:
            if strict and result.errors:
                raise typer.Exit(1)
            break
        time.sleep(_interval(every))


@app.command()
def doctor(config: Path = ConfigOpt):
    """Check config, connections and license. Safe to paste into a bug report."""
    cfg, feats, status = _load(config)
    typer.echo(f"HomelabScribe {__version__}")
    typer.echo(f"Config:   {config} ({'found' if config.exists() else 'not found, using defaults'})")
    typer.echo(f"License:  {status}")
    typer.echo(f"Output:   {cfg.output_dir}")
    for h in cfg.docker_hosts:
        try:
            import docker

            info = docker.DockerClient(base_url=h.url, timeout=5).api.info()
            typer.secho(
                f"Docker '{h.name}': OK "
                f"(Docker {info.get('ServerVersion')}, {info.get('Containers')} containers)",
                fg="green",
            )
        except Exception as e:
            typer.secho(f"Docker '{h.name}': FAILED ({type(e).__name__})", fg="red")
    for n in cfg.npm:
        try:
            from homelabscribe.collectors.npm import NpmCollector

            count = len(NpmCollector(n).collect())
            typer.secho(f"Nginx Proxy Manager '{n.name}': OK ({count} proxy hosts)", fg="green")
        except Exception as e:
            typer.secho(f"Nginx Proxy Manager '{n.name}': FAILED ({type(e).__name__})", fg="red")
    for p in cfg.proxmox:
        if "proxmox" not in feats:
            typer.secho(f"Proxmox '{p.name}': configured, needs Pro", fg="yellow")
            continue
        try:
            from homelabscribe.collectors.proxmox import ProxmoxCollector

            v = ProxmoxCollector(p).api.version.get()
            typer.secho(f"Proxmox '{p.name}': OK (PVE {v.get('version')})", fg="green")
        except Exception as e:
            typer.secho(f"Proxmox '{p.name}': FAILED ({type(e).__name__})", fg="red")


@app.command()
def demo(
    out: Path = typer.Option(
        Path("homelabscribe-demo"), "--out", "-o", help="Folder to write the demo docs to"
    ),
):
    """Render docs for a made-up homelab, with every Pro feature on. No Docker needed."""
    from homelabscribe.demo import demo_snapshots
    from homelabscribe.diff import compare
    from homelabscribe.enrich import enrich, load_catalog
    from homelabscribe.renderer import Renderer

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
    written = Renderer(out, licensing.PRO_FEATURES).render(new, log, cfg.breakglass)
    typer.echo(f"Wrote {len(written)} file(s) to {out}/. Open {out}/index.md to start.")


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
