# Compatibility promise (draft)

> **Draft. This promise takes effect with HomelabScribe™ 1.0.** Until then it describes what 1.0 is meant to guarantee, it can still change, and the rule under [Until 1.0](#until-10) is the one that holds.

## Versions

HomelabScribe version numbers follow [Semantic Versioning](https://semver.org/). From 1.0 on, something this page lists as covered will change incompatibly only in a new major version. Before such a change it will be deprecated: named under **Deprecated** in the [changelog](../CHANGELOG.md) at least one minor release earlier, with a warning from the tool where that is practical.

## Covered

From 1.0 on, these will stay compatible within a major version:

- **The commands,** their documented options and the exit statuses that [interfaces](interfaces.md) documents.
- **The config file:** the keys listed in [interfaces](interfaces.md) and their meaning. A config that works with a 1.x release will work with every later 1.y. This includes `${VAR}` values and the environment variables `HOMELABSCRIBE_CONFIG` and `HOMELABSCRIBE_LICENSE`. A `license_key` in the config file will take precedence over `HOMELABSCRIBE_LICENSE`.
- **The `homelabscribe.*` container labels.**
- **Where pages are written:** `index.md`, `hosts/`, `services/`, `archive/`, `routes.md` and the Pro pages, and how page names are formed. A service page is named after the service's title, written in lower case with every run of characters other than a to z and 0 to 9 turned into one hyphen, and with hyphens at the start and end removed (`Pi-hole` becomes `pi-hole.md`); a title with no letter or digit gives the name `item`; when two services have the same title, each page name also gets the host name added (`sonarr-nas.md`). A host page is named after the host in the same way. When two different services still end up with the same page name (for example `Plex Media` and `Plex-Media`, which both give `plex-media`, or titles in a script without a to z or 0 to 9, which all give `item`), one keeps the name and each other one gets the name with a hyphen and a code added: the first 8 hex digits of the SHA-256 of `<host>/<title>` (`plex-media-1a2b3c4d.md`), or of `<host>/<title>/<id>` when another of them has the same host and title (the id is `<host>/<name>/<kind>`, and for a VM or LXC also its number: `nas/plex/container`, `nas/plex/vm/101`; it does not change when look-alikes come or go), more digits (10, 12 and so on) if that name is taken too or a page written for another service is there, and the name cut so the whole stays within 200 characters. Host pages work the same way, with the code from the host name. The pages of these services carry that id in the front matter (`id:`). The service that keeps the plain name is the one the page already there was written for: the one with the page's `id` (a page from an earlier version may have the shorter `<host>/<name>`, which counts only when exactly one of them has it), or, on a page without a matching `id`, the only one with its `title` and `host`. A service with a coded page keeps that page, and its notes, in the same way while it is there, whichever look-alikes are added or go away. A newly added look-alike never takes over a page or its notes. When there is no such page, or it cannot be read (only its first 64 KiB are read, and its front matter has to end within them), a fixed order decides, not the order of the scan: a service whose own title is unique comes first, then by title, host and container name. One case does move notes: when the service that has the plain name goes away, the plain page is not archived. The first of the remaining look-alikes in the fixed order (or the only one left) takes the plain name, together with the notes in that file, which were written about the service that went away, and its own coded page goes to `archive/` with its notes, as any page no longer generated. Pages without such a clash keep their names as before. A page name longer than 200 characters is cut to its first 191 characters (without a hyphen at the cut) and ends with a hyphen and the first 8 hex digits of the SHA-256 of the whole name, so a name of 200 characters is kept as it is and one of 252 is cut. A page's file name changes when its title changes (the app is renamed in the catalog, or its `homelabscribe.name` label changes) and when a second service with the same title appears or goes away, because the host name is then added to or dropped from the file names; the promise is the rule, not that a given page keeps its name for ever.
- **Your notes:** the notes markers will stay. Every generated page has a notes block. The notes in a page file stay with that file name for as long as a page of that name is generated, whichever service it describes; when two services or hosts have the same page name, the one the page was already written for keeps it. When no page of that name is generated any more, the file is moved to `archive/` with its notes, if it has any (a file without notes is removed). HomelabScribe never overwrites or deletes a file in `archive/`: when the name is already taken there, the page is saved under another name (today `name-2.md`, `name-3.md` and so on). The promise is that nothing there is overwritten, not the exact name.
- **The front matter keys** of the generated pages, per kind of page. Each key will keep its name and its kind of value, and keys may be added; none will be renamed or removed. The `id` key (`<host>/<name>/<kind>`, and for a VM or LXC also its number) is written only on the pages of services whose page names clashed (see above); other service pages have no `id`.

  | Page | Keys |
  | --- | --- |
  | Service page | `title` (text), `host` (text), `category` (text), `status` (text), `image` (text), `tags` (list) |
  | Host page | `title` (text), `kind` (text), `tags` (list) |
  | `index.md` | `title` (text), `generated` (date and time), `tags` (list) |
  | `routes.md` and the Pro pages | `title` (text), `tags` (list) |

  `image` is present only when the service has an image.
- **Your own catalog file** (`catalog_file`): it will keep working within a major version. An entry may have the fields `match`, `name`, `category` and `description`, and no others are read. An entry of yours is tried before the built-in ones, and a match on the image name is tried before a match on the image owner, so an entry of yours that matches only by owner loses to a built-in entry that matches by image name. For a VM, an LXC container or a service added by hand, the service name is matched, not an image.
- **License keys:** a key issued for 1.x will be accepted by every later 1.y for as long as the key itself is valid: a lifetime key for good, a key with an end date until that date.
- **The state folder:** a later 1.y will read what an earlier 1.x wrote.
- **The container image:** its entry point, its three volumes (`/config`, `/docs`, `/data`) and its default config path.

## Not covered

These may change in any release, also after 1.0:

- The wording and layout inside a generated page.
- The built-in catalog: its names, descriptions and categories. A page can therefore get a new name when its app is renamed in the catalog; the changelog will say so.
- The files inside the bug-report bundle (`doctor --bundle`) and what they hold.
- The markup and styling of the static HTML copy (`html_dir`).
- The inner format of the snapshot and changelog files.
- What the commands print for people to read.
- The Python modules. There is no Python API.
- Going back to an older version with state that a newer version wrote.
- Anything this page does not list.

## Python and platforms

The supported Python versions follow upstream ([support](../SUPPORT.md)). Dropping a Python version that has reached its end of life will not count as a breaking change.

## Until 1.0

Until 1.0 the rule in [Upgrading](upgrading.md) holds: a minor release may change configuration or defaults, and the changelog says so under **Changed**.
