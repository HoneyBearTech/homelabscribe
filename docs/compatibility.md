# Compatibility promise (draft)

> **Draft. This promise takes effect with HomelabScribe™ 1.0.** Until then it describes what 1.0 is meant to guarantee, it can still change, and the rule under [Until 1.0](#until-10) is the one that holds.

## Versions

HomelabScribe version numbers follow [Semantic Versioning](https://semver.org/). From 1.0 on, something this page lists as covered will change incompatibly only in a new major version. Before such a change it will be deprecated: named under **Deprecated** in the [changelog](../CHANGELOG.md) at least one minor release earlier, with a warning from the tool where that is practical.

## Covered

From 1.0 on, these will stay compatible within a major version:

- **The commands,** their documented options and what their exit status means ([interfaces](interfaces.md)).
- **The config file:** the keys listed in [interfaces](interfaces.md) and their meaning. A config that works with a 1.x release will work with every later 1.y. This includes `${VAR}` values and the environment variables `HOMELABSCRIBE_CONFIG` and `HOMELABSCRIBE_LICENSE`.
- **The `homelabscribe.*` container labels.**
- **Where pages are written:** `index.md`, `hosts/`, `services/`, `archive/`, `routes.md` and the Pro pages, and how page names are formed.
- **Your notes:** the notes markers, and that notes are carried forward from one run to the next.
- **The front matter keys** of the generated pages. Keys may be added; none will be renamed or removed.
- **License keys:** a key issued for 1.x will be valid for every 1.y.
- **The state folder:** a later 1.y will read what an earlier 1.x wrote.
- **The container image:** its entry point, its three volumes (`/config`, `/docs`, `/data`) and its default config path.

## Not covered

These may change in any release, also after 1.0:

- The wording and layout inside a generated page.
- The built-in catalog: its names, descriptions and categories. A page can therefore get a new name when its app is renamed in the catalog; the changelog will say so.
- The inner format of the snapshot and changelog files.
- What the commands print for people to read.
- The Python modules. There is no Python API.
- Going back to an older version with state that a newer version wrote.
- Anything this page does not list.

## Python and platforms

The supported Python versions follow upstream ([support](../SUPPORT.md)). Dropping a Python version that has reached its end of life will not count as a breaking change.

## Until 1.0

Until 1.0 the rule in [Upgrading](upgrading.md) holds: a minor release may change configuration or defaults, and the changelog says so under **Changed**.
