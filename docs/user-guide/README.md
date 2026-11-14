# User guide

## What you get

`index.md` (every host and service by category), `hosts/<host>.md`, `services/<service>.md`. Pro adds `CHANGELOG.md`, `break-glass.md` and `topology.md`. With `html_dir` set you also get the same pages as plain HTML ([a static HTML copy](#a-static-html-copy)).

## Your notes

Every page ends with:

```markdown
<!-- homelabscribe:notes:start -->
Your notes here.
<!-- homelabscribe:notes:end -->
```

Write inside the block. HomelabScribe™ rewrites everything else but copies the block forward. If a service disappears and its page has notes, the page moves to `archive/`.

## Naming and describing things

HomelabScribe recognizes common self-hosted apps by image. Override per container with labels (`homelabscribe.name`, `homelabscribe.description`, `homelabscribe.category`, `homelabscribe.url`, `homelabscribe.hidden: "true"`), or add your own catalog file via `catalog_file` (same format as the built-in `catalog.yaml`).

## Web addresses from Nginx Proxy Manager

Docker only knows ports, not the domain names you reach a service by. Configure `npm:` (see `homelabscribe.example.yaml`) and HomelabScribe™ reads the proxy's list of proxy hosts, matches each to a service by machine and port (or by container name or Docker network alias, when exactly one service answers to it), and uses the domain as that service's address. It also writes `routes.md`, listing every domain and where it goes (unmatched entries are shown as such). A URL set by a label or a Traefik rule always wins. Create an NPM user that can only *view* proxy hosts, and keep its password in an environment variable (`secret: ${NPM_PASSWORD}`).

## Things the scanners can't see

Pi-hole on a Raspberry Pi, a NAS or a router can't be scanned, so describe them in the config under `manual_services` (see `homelabscribe.example.yaml`). They get a normal page, appear in the index (marked "not scanned") and can be listed under `breakglass.critical`. Naming one `pihole` or `adguardhome`, or setting `dns: true`, makes the break-glass guide say it is the house's DNS server and which machine to restart.

## A static HTML copy

For readers without Obsidian or another Markdown viewer, HomelabScribe™ can also write the pages as plain HTML. Set `html_dir` in the config:

```yaml
html_dir: /data/html   # a folder of its own
```

`homelabscribe run` and `homelabscribe render` then write the HTML copy after the Markdown: the same pages at the same paths, ending in `.html`. Start at `index.html`. To try it without a lab: `homelabscribe demo --html homelabscribe-demo-html`. It is part of the free edition; Pro pages are in the copy when your license writes them as Markdown.

- **Plain files.** Every page is complete by itself: styles inline, a dark mode that follows the system, no JavaScript, and nothing is fetched from anywhere when you open it. Open the folder from disk or copy it to any web server.
- **Your notes are in it,** without the marker comments. A notes section you have not written in is left out.
- **Nothing in a page becomes markup.** HTML in a label, a description or a note is shown as text. Links between pages point at the HTML files. Web (`http`, `https`), `mailto` and relative links stay links; a link with any other scheme is shown as its text. An image is shown only when its address is relative; any other image is replaced by its alternative text.
- **The topology diagram** (Pro) is shown as its Mermaid text, because drawing it would need a script in the page. Open `topology.md` in a viewer that draws Mermaid to see it drawn.
- **`html_dir` has to be a folder of its own:** not the Markdown folder or the state folder, not inside one of them and not around one of them. Otherwise the command stops before it writes anything. In the container, mount one more volume for it, or use a folder beside the state folder under `/data`.
- **Your own files are safe.** In `html_dir` HomelabScribe only replaces or removes HTML files it wrote itself (they carry its generator line), and it removes one when its page is gone. If a file of yours is where a page would go, that page is skipped and the run prints `skipped:` for it. Symbolic links are not followed.
- The HTML copy is the same map of your lab as the Markdown, so treat it as just as sensitive ([security](../security.md)).

## Pro features

Multiple Docker hosts, Proxmox VMs/LXCs, the changelog page, the break-glass guide (configure `breakglass:`), the topology diagram and `--every` scheduling. Pro is unlocked by a license key checked offline ([security](../security.md)).

## Commands

| Command | What it does |
| --- | --- |
| `homelabscribe run` | Scan, then write docs |
| `homelabscribe scan` | Take a snapshot only |
| `homelabscribe render` | Rewrite docs from the latest snapshot |
| `homelabscribe doctor` | Check config, connections and license; safe to paste into an issue |
| `homelabscribe demo` | Render a sample homelab |

## Keeping it up to date

Run from cron (`0 3 * * * homelabscribe run -c /path/homelabscribe.yaml`) or, with Pro, `homelabscribe run --every 1d`.
