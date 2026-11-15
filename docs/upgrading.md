# Upgrading

```bash
pipx upgrade homelabscribe          # pip install
docker compose pull && docker compose run --rm homelabscribe   # Docker
```

Read the [changelog](../CHANGELOG.md) first. Before 1.0, minor releases may change configuration or defaults; the changelog says so under **Changed**. What 1.0 is meant to keep stable is drafted in the [compatibility promise](compatibility.md).

## What state exists

| Location | Contents | Safe to delete? |
| --- | --- | --- |
| `state_dir` (default `.homelabscribe/`, `/data/.homelabscribe` in Docker) | `snapshots/` (last 60 JSON snapshots) and `changelog.json` | Yes, but you lose history that feeds the Pro changelog |
| `output_dir` (default `homelabscribe-docs/`) | Generated Markdown, including **your notes** | **No**: your notes live here |

## Back up and restore

Back up both folders (they are plain files): `tar czf homelabscribe-backup.tgz homelabscribe-docs .homelabscribe`. To restore, extract them back and run `homelabscribe render`. Your notes are inside the generated pages; archived pages keep notes for services that disappeared.

## Default output folder changed

Newer versions default to `homelabscribe-docs/` instead of `docs/`. If you relied on the old default, set `output_dir: docs` in your config or move the folder.

## Front matter is quoted when it needs it

Generated pages now quote a `title`, `host`, `category`, `status` or `image` in the front matter when plain text would not be read back as the same text (for example `Media: TV`, `123`, `true` or a value with a line break). A page where one of these needed quoting is rewritten once on the next run; every other page is unchanged. The second tag of a service page is now the category in the same lower-case, hyphenated form that page names use, so a custom category with unusual characters (an underscore, a dot, an ampersand, non-ASCII letters, or no letters or digits at all) may get a different tag than before. The tags of the built-in categories do not change.
