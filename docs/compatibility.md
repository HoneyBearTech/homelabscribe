# Compatibility promise (draft)

> **Draft. This promise takes effect with HomelabScribe™ 1.0.** Until then it describes what 1.0 is meant to guarantee, it can still change, and the rule under [Until 1.0](#until-10) is the one that holds.

## Versions

HomelabScribe version numbers follow [Semantic Versioning](https://semver.org/). From 1.0 on, something this page lists as covered will change incompatibly only in a new major version. Before such a change it will be deprecated: named under **Deprecated** in the [changelog](../CHANGELOG.md) at least one minor release earlier, with a warning from the tool where that is practical.

## Covered

From 1.0 on, these will stay compatible within a major version:

- **The commands,** their documented options and the exit statuses that [interfaces](interfaces.md) documents.
- **The config file:** the keys listed in [interfaces](interfaces.md) and their meaning. A config that works with a 1.x release will work with every later 1.y. This includes `${VAR}` values and the environment variables `HOMELABSCRIBE_CONFIG` and `HOMELABSCRIBE_LICENSE`. A `license_key` in the config file will take precedence over `HOMELABSCRIBE_LICENSE`.
- **The `homelabscribe.*` container labels.**
- **Where pages are written:** `index.md`, `hosts/`, `services/`, `archive/`, `routes.md` and the Pro pages, and how page names are formed. A service page is named after the service's title, written in lower case with every run of characters other than a to z and 0 to 9 turned into one hyphen (`Pi-hole` becomes `pi-hole.md`); when two services have the same title, each page name also gets the host name added (`sonarr-nas.md`). A host page is named after the host in the same way. A page's title, and with it its file name, changes when the app is renamed in the catalog, when its `homelabscribe.name` label changes, or when a second service with the same title appears; the promise is the rule, not that a given page keeps its name for ever.
- **Your notes:** the notes markers will stay. The notes of a page are carried forward from run to run for as long as the page keeps its file name. When a page gets a new name or its service is gone, the old page is moved to `archive/` with its notes, if it has any, and the new page starts without them. Nothing is promised about what later happens to a page in `archive/`.
- **The front matter keys** of the generated pages, per kind of page. Each key will keep its name and its kind of value, and keys may be added; none will be renamed or removed.

  | Page | Keys |
  | --- | --- |
  | Service page | `title` (text), `host` (text), `category` (text), `status` (text), `image` (text), `tags` (list) |
  | Host page | `title` (text), `kind` (text), `tags` (list) |
  | `index.md` | `title` (text), `generated` (date and time), `tags` (list) |
  | `routes.md` and the Pro pages | `title` (text), `tags` (list) |

  `image` is present only when the service has an image.
- **Your own catalog file** (`catalog_file`): it will keep working within a major version. An entry may have the fields `match`, `name`, `category` and `description`, and no others are read. Your entries take precedence over the built-in ones.
- **License keys:** a key issued for 1.x will be accepted by every later 1.y for as long as the key itself is valid: a lifetime key for good, a key with an end date until that date.
- **The state folder:** a later 1.y will read what an earlier 1.x wrote.
- **The container image:** its entry point, its three volumes (`/config`, `/docs`, `/data`) and its default config path.

## Not covered

These may change in any release, also after 1.0:

- The wording and layout inside a generated page.
- The built-in catalog: its names, descriptions and categories. A page can therefore get a new name when its app is renamed in the catalog; the changelog will say so.
- The files inside the bug-report bundle (`doctor --bundle`) and what they hold.
- The inner format of the snapshot and changelog files.
- What the commands print for people to read.
- The Python modules. There is no Python API.
- Going back to an older version with state that a newer version wrote.
- Anything this page does not list.

## Python and platforms

The supported Python versions follow upstream ([support](../SUPPORT.md)). Dropping a Python version that has reached its end of life will not count as a breaking change.

## Until 1.0

Until 1.0 the rule in [Upgrading](upgrading.md) holds: a minor release may change configuration or defaults, and the changelog says so under **Changed**.
