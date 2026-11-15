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

## Damaged files are skipped

A page, snapshot or `changelog.json` that cannot be read is now left as it is and printed as `skipped: <path> (<reason>)` instead of stopping the run with an error. The same goes for an old page with notes that cannot be moved to `archive/`: it stays, with its notes, and the run goes on. Run with `--strict` if your scheduler should treat that as a failure (exit status 1, docs still written); `--strict` now also fails when the HTML copy skipped a page. A damaged `changelog.json` is moved to `changelog.json.damaged` next to it and a new one is started (if the move is not possible, the damaged file stays and no new changelog is written until you remove it): delete the `.damaged` file when you no longer need it. Temporary files named `.<random>.hls-tmp` can appear next to a file while it is being written; a run killed at that moment can leave one behind, and it is safe to delete. A `services/` or `hosts/` folder that is a link to another folder keeps working as before.

## Default output folder changed

Newer versions default to `homelabscribe-docs/` instead of `docs/`. If you relied on the old default, set `output_dir: docs` in your config or move the folder.

## Front matter is quoted when it needs it

Generated pages now quote a `title`, `host`, `category`, `status` or `image` in the front matter when plain text would not be read back as the same text (for example `Media: TV`, `123`, `true` or a value with a line break). A page where one of these needed quoting is rewritten once on the next run; every other page is unchanged. The second tag of a service page is the category as a tag. Letters and digits of any language (and their combining marks), `_`, `-` and `/` are kept in lower case, every run of other characters becomes one hyphen, and hyphens and slashes at the start and end are dropped (`item` if nothing is left), so a custom category's tag changes only when it held other characters, or several spaces in a row. The tags of the built-in categories do not change.

## Notes on the changelog page, and shorter long page names

The Pro changelog page (`CHANGELOG.md`) now ends with a notes block like every other page; it is rewritten once on the next run to add it. A page whose name was between 201 and 252 characters long gets a new, shorter name (see [where pages are written](compatibility.md#covered)). Its old file is handled like any page that is no longer generated: moved to `archive/` if it holds notes, removed if it does not.
