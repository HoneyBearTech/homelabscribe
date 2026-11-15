"""A static HTML copy of the generated pages, for readers without a Markdown viewer (config `html_dir`).

The pages hold text from container labels, Proxmox notes and the user's own notes, so nothing from a page
becomes markup: raw HTML is shown as text, a link or an image is kept only when its address is harmless,
and each page carries a Content-Security-Policy and no script. The copy is plain files with no outside
request. In `html_dir` only files this module wrote (they carry its generator line) are ever replaced or
removed, and symbolic links are not followed.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import os
import posixpath
import re
import secrets
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from jinja2 import Environment, PackageLoader
from markdown_it import MarkdownIt
from markdown_it.common.utils import escapeHtml

from homelabscribe import notes

GENERATOR = "HomelabScribe static HTML copy"
# A file in `html_dir` is ours only if its head carries this line, where the page template puts it: a file
# is owned when it starts with OWNED_START. Text further down a file can never make it ours.
OWNED_LINE = f'<meta name="generator" content="{GENERATOR}">'
OWNED_START = f'<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n{OWNED_LINE}\n'
# A file being written beside its destination; it carries OWNED_START, so a leftover one is provably ours.
TEMP_SUFFIX = ".homelabscribe-tmp"
ARCHIVE_FOLDERS = ("services", "hosts")
MERMAID_NOTE = (
    '<p class="note">This is a Mermaid diagram, shown here as its text. To see it drawn, open the Markdown '
    "page in a viewer that draws Mermaid, such as Obsidian or GitHub.</p>\n"
)

CSS = """\
:root{color-scheme:light dark;--bg:#fff;--fg:#1c1e21;--muted:#59616c;--line:#d5d9de;--soft:#f3f4f6}
:root{--link:#0b57d0}
@media (prefers-color-scheme:dark){
:root{--bg:#16181c;--fg:#e4e6ea;--muted:#a9b1bc;--line:#3a3f47;--soft:#22252b;--link:#8ab4f8}
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.6 system-ui,-apple-system,Roboto,sans-serif}
header,main{max-width:60rem;margin:0 auto;padding:1rem 1.25rem}
header{border-bottom:1px solid var(--line);padding-top:.75rem;padding-bottom:.75rem}
a{color:var(--link)}
h1,h2,h3{line-height:1.25}
h2{margin-top:2rem;padding-bottom:.25rem;border-bottom:1px solid var(--line)}
table{display:block;max-width:100%;overflow-x:auto;border-collapse:collapse;margin:1rem 0}
th,td{border:1px solid var(--line);padding:.4rem .7rem;text-align:left;vertical-align:top}
th{background:var(--soft)}
.center{text-align:center}
.right{text-align:right}
code,pre{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.9em}
code{background:var(--soft);padding:.1em .3em;border-radius:3px}
pre{background:var(--soft);padding:.8rem 1rem;overflow-x:auto;border-radius:4px}
pre code{background:none;padding:0}
blockquote{margin:1rem 0;padding:.1rem 1rem;border-left:4px solid var(--line);color:var(--muted)}
img{max-width:100%}
.note{color:var(--muted);font-style:italic}
"""
# Only this stylesheet and images from the same folder tree: no script, no frame, no outside request.
CSP = (
    "default-src 'none'; "
    f"style-src 'sha256-{base64.b64encode(hashlib.sha256(CSS.encode()).digest()).decode()}'; "
    "img-src 'self'; base-uri 'none'; form-action 'none'"
)

# ---- links and images -------------------------------------------------------------------------

_SCHEME = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*):")
_LINK_SCHEMES = frozenset({"http", "https", "mailto"})


def address_kind(url: str) -> str:
    """What an address is: `fragment`, `relative`, `web` (http, https or mailto) or `other`.

    Relative means a local path, with no scheme and no host: `//host/x` is not relative.
    """
    if any(ord(c) <= 0x20 or ord(c) == 0x7F for c in url):
        return "other"  # a browser drops or trims these, so what it would open is not what is written
    plain = url.replace("\\", "/")  # a browser reads a backslash as a slash
    if plain.startswith("#"):
        return "fragment"
    m = _SCHEME.match(plain)
    if m:
        return "web" if m.group(1).lower() in _LINK_SCHEMES else "other"
    if not plain or plain.startswith("//") or ":" in re.split(r"[/?#]", plain, maxsplit=1)[0]:
        return "other"
    return "relative"


def _href(url: str, env: dict) -> str | None:
    """Where a link may point, or None when it must be shown as its text instead."""
    kind = address_kind(url)
    if kind == "other":
        return None
    if kind != "relative":
        return url
    path, rest = re.fullmatch(r"([^?#]*)(.*)", url, re.DOTALL).groups()
    if path.endswith(".md") and not path.startswith("/"):
        here = posixpath.dirname(env.get("page", ""))
        if posixpath.normpath(posixpath.join(here, unquote(path))) in env.get("pages", ()):
            return f"{path[:-3]}.html{rest}"  # a page of this export: point at its HTML file
    return url


def _link_open(self, tokens, idx, options, env) -> str:
    token = tokens[idx]
    href = _href(token.attrGet("href") or "", env)
    env.setdefault("links", []).append(href is not None)
    if href is None:
        return ""
    title = token.attrGet("title")
    return f'<a href="{escapeHtml(href)}"' + (f' title="{escapeHtml(title)}"' if title else "") + ">"


def _link_close(self, tokens, idx, options, env) -> str:
    opened = env.get("links") or [False]
    return "</a>" if opened.pop() else ""


def _image(self, tokens, idx, options, env) -> str:
    token = tokens[idx]
    alt = escapeHtml(self.renderInlineAsText(token.children or [], options, env))
    src = token.attrGet("src") or ""
    if address_kind(src) != "relative" or src.replace("\\", "/").startswith("/"):
        return alt  # nothing from another host, nor from the root of the disk or the server
    title = token.attrGet("title")
    return (
        f'<img src="{escapeHtml(src)}" alt="{alt}"' + (f' title="{escapeHtml(title)}"' if title else "") + ">"
    )


def _fence(self, tokens, idx, options, env) -> str:
    token = tokens[idx]
    code = f"<pre><code>{escapeHtml(token.content)}</code></pre>\n"
    language = (token.info.split() or [""])[0].lower()
    return MERMAID_NOTE + code if language == "mermaid" else code


def _cell(self, tokens, idx, options, env) -> str:
    """A table cell. The converter's inline `style` for alignment becomes a class, so no style attribute."""
    token = tokens[idx]
    align = (token.attrGet("style") or "").removeprefix("text-align:")
    return f'<{token.tag} class="{align}">' if align in ("center", "right") else f"<{token.tag}>"


def _converter() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False}).enable("table")
    # Every link and image is parsed, and the rules below decide what is written: an `<a>` or an `<img>`
    # only comes from them, with an address they checked.
    md.validateLink = lambda url: True
    md.add_render_rule("link_open", _link_open)
    md.add_render_rule("link_close", _link_close)
    md.add_render_rule("image", _image)
    md.add_render_rule("fence", _fence)
    md.add_render_rule("th_open", _cell)
    md.add_render_rule("td_open", _cell)
    return md


_MD = _converter()
_TEMPLATE = Environment(
    loader=PackageLoader("homelabscribe", "templates"), autoescape=True, keep_trailing_newline=True
).get_template("page.html.j2")

# ---- one page ---------------------------------------------------------------------------------

_HEADING = re.compile(r"#{1,6}[ \t]")
_TITLE = re.compile(r"title:[ \t]*(.*)")


def split_front_matter(text: str) -> tuple[str | None, str]:
    """(title, body). The front matter is not parsed as YAML: the title is the text after `title:`.

    Front matter that does not close is not front matter, so the whole text is the body.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip("\r\n\ufeff") != "---":
        return None, text
    end = next((i for i, line in enumerate(lines[1:], start=1) if line.rstrip("\r\n") == "---"), None)
    if end is None:
        return None, text
    title = None
    for line in lines[1:end]:
        m = _TITLE.fullmatch(line.rstrip("\r\n"))
        if m:
            title = m.group(1).strip() or None
            break
    return title, "".join(lines[end + 1 :])


def strip_notes_markers(text: str) -> str:
    """The page without its two marker comments. The notes stay where they are.

    A block that holds only the placeholder sentence is left out, together with the heading above it.
    """
    if notes.START in text and notes.END in text.split(notes.START, 1)[1]:
        before, rest = text.split(notes.START, 1)
        inside, after = rest.split(notes.END, 1)
        if inside.strip() == notes.PLACEHOLDER:
            kept = before.rstrip().splitlines()
            if kept and _HEADING.match(kept[-1]):
                kept.pop()
            text = "\n".join(kept) + "\n" + after
        else:
            text = f"{before}{inside}{after}"
    return text.replace(notes.START, "").replace(notes.END, "")


def page_html(text: str, rel: str = "index.md", pages: Collection[str] = ()) -> str:
    """A generated Markdown page as a complete HTML page.

    `rel` is the page's path below the output folder and `pages` the paths of all exported pages, so a
    link from one page to another can point at the HTML file.
    """
    title, body = split_front_matter(text)
    env = {"page": rel, "pages": pages}
    tokens = _MD.parse(strip_notes_markers(body), env)
    if title is None:
        for i, token in enumerate(tokens):
            if token.type == "heading_open" and i + 1 < len(tokens):
                title = _MD.renderer.renderInlineAsText(
                    tokens[i + 1].children or [], _MD.options, env
                ).strip()
                break
    depth = len(PurePosixPath(rel).parts) - 1
    return _TEMPLATE.render(
        generator=GENERATOR,
        csp=CSP,
        css=CSS,
        title=title or PurePosixPath(rel).stem,
        home="../" * depth + "index.html",
        body=_MD.renderer.render(tokens, _MD.options, env),
    )


# ---- the folders ------------------------------------------------------------------------------


def folder_problem(html_dir: Path, others: dict[str, Path]) -> str | None:
    """Why `html_dir` cannot be used, or None. It has to be a folder of its own.

    `others` are the folders it must not be, lie inside or contain, by the name to call them in the message.
    Both sides are resolved first, so a link to one of them counts as that folder.
    """
    html = html_dir.resolve()
    if html.exists() and not html.is_dir():
        return f"The HTML folder {html_dir} is not a folder. Nothing was written."
    for name, other_dir in others.items():
        other = other_dir.resolve()
        if html == other:
            relation = "is the same folder as"
        elif html.is_relative_to(other):
            relation = "lies inside"
        elif other.is_relative_to(html):
            relation = "contains"
        else:
            continue
        return (
            f"The HTML folder {html_dir} {relation} {name} ({other_dir}). "
            "It has to be a folder of its own. Nothing was written."
        )
    return None


# Open the file itself, never what a link in its place points at (and as bytes, where that is a choice).
_NO_FOLLOW = getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)


def _regular_file(root: Path, rel: PurePosixPath) -> Path | None:
    """A file below `root`, if it and every folder on the way are real: not a symbolic link."""
    path = root
    for part in rel.parts[:-1]:
        path = path / part
        if path.is_symlink() or not path.is_dir():
            return None
    path = path / rel.name
    return path if not path.is_symlink() and path.is_file() else None


def _read(path: Path, limit: int = -1) -> bytes | None:
    """The file's bytes, without following a link that took its place in the meantime."""
    try:
        fd = os.open(path, os.O_RDONLY | _NO_FOLLOW)
    except OSError:
        return None
    with os.fdopen(fd, "rb") as f:
        return f.read(limit)


def _owned(start: bytes | None) -> bool:
    return start is not None and start.startswith(OWNED_START.encode())


@dataclass
class Export:
    """What one export did."""

    pages: int = 0  # pages that now have an up-to-date HTML file
    changed: list[Path] = field(default_factory=list)  # HTML files written or removed
    skipped: list[str] = field(default_factory=list)  # pages that were not exported, and why


def _place(root: Path, rel: PurePosixPath, data: bytes) -> str:
    """Write one HTML file. Returns `written`, `unchanged` or the reason it was skipped."""
    folder = root
    for part in rel.parts[:-1]:
        folder = folder / part
        if folder.is_symlink():
            return "a folder on the way is a symbolic link"
        if folder.exists() and not folder.is_dir():
            return "a file that HomelabScribe did not write is in the way"
    dest = folder / rel.name
    if dest.is_symlink():
        return "the file there is a symbolic link"
    replace = dest.exists()
    if replace:
        current = _read(dest) if dest.is_file() else None
        if not _owned(current):
            return "a file that HomelabScribe did not write is in the way"
        if current == data:
            return "unchanged"
    folder.mkdir(parents=True, exist_ok=True)
    # Nothing is ever written into an existing file: a hard link to it would carry the change to a copy
    # outside `html_dir`, and a write cut short would leave a file that no longer starts with our line. A
    # file of ours is replaced by a new file made beside it; a new file is only ever created, so a link or
    # a file that appeared there in the meantime is not written to either.
    target, mode = dest, None
    try:
        if replace:
            mode = os.lstat(dest).st_mode & 0o777  # the new file keeps the permissions of the one it replaces
            # A short name that does not hold the page's name, which may be near the limit already.
            target = folder / f".{secrets.token_hex(8)}{TEMP_SUFFIX}"
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NO_FOLLOW, 0o666)
    except OSError:
        return "the file there could not be written"
    done = False
    try:
        _write(fd, data, mode)
        if replace:
            os.replace(target, dest)
        done = True
    except OSError:
        pass
    finally:
        if not done:  # the temporary file, or the file this call created: never a file that was there before
            with contextlib.suppress(OSError):
                target.unlink()
    return "written" if done else "the file there could not be written"


def _write(fd: int, data: bytes, mode: int | None = None) -> None:
    """Write all of `data` to the open file, with the permission bits `mode` if given, and close it."""
    try:
        f = os.fdopen(fd, "wb")
    except BaseException:
        os.close(fd)
        raise
    with f:
        if mode is not None and hasattr(os, "fchmod"):
            os.fchmod(f.fileno(), mode)
        f.write(data)


def _sources(output_dir: Path, generated: Iterable[Path]) -> dict[str, PurePosixPath]:
    """The pages to export, by their path below the output folder.

    They are the pages the renderer wrote or confirmed in this run, plus the archived pages: Markdown files
    directly in `archive/services/` and `archive/hosts/` that hold both notes markers. Nothing else in the
    output folder is a page, whatever its name.
    """
    found: dict[str, PurePosixPath] = {}
    for path in generated:
        try:
            rel = PurePosixPath(path.relative_to(output_dir).as_posix())
        except ValueError:
            continue
        if rel.suffix == ".md":
            found[rel.as_posix()] = rel
    for folder in ARCHIVE_FOLDERS:
        where = PurePosixPath("archive", folder)
        if any((output_dir / part).is_symlink() for part in (where.parent, where)):
            continue
        if not (output_dir / where).is_dir():
            continue
        for path in sorted((output_dir / where).glob("*.md")):
            rel = where / path.name
            if _regular_file(output_dir, rel) is None:
                continue
            text = (_read(path) or b"").decode(errors="replace")
            if notes.START in text and notes.END in text:
                found.setdefault(rel.as_posix(), rel)
    return found


def export(output_dir: Path, html_dir: Path, generated: Iterable[Path]) -> Export:
    """Write the HTML copy of the pages in `output_dir` to `html_dir`. Only changed files are written.

    `generated` are the pages the renderer wrote or confirmed in the same run (its own list). Reads
    `output_dir`, writes `html_dir`, and touches nothing else.
    """
    problem = folder_problem(html_dir, {"the Markdown folder": output_dir})
    if problem:
        raise ValueError(problem)
    result = Export()
    sources = _sources(output_dir, generated)
    html_dir.mkdir(parents=True, exist_ok=True)
    expected: set[str] = set()
    for name, rel in sorted(sources.items()):
        target = rel.with_suffix(".html")
        expected.add(target.as_posix())
        source = _regular_file(output_dir, rel)
        raw = _read(source) if source is not None else None
        if raw is None:
            result.skipped.append(f"{name} (the page is a symbolic link or could not be read)")
            continue
        page = page_html(raw.decode(errors="replace"), name, sources.keys())
        outcome = _place(html_dir, target, page.encode())
        if outcome == "written":
            result.changed.append(html_dir / target)
        if outcome in ("written", "unchanged"):
            result.pages += 1
        else:
            result.skipped.append(f"{target.as_posix()} ({outcome})")

    # An HTML file of ours whose page is gone is removed. Regular files only; linked folders are not entered.
    for folder, folders, files in os.walk(html_dir, followlinks=False):
        folders[:] = [f for f in folders if not Path(folder, f).is_symlink()]
        for file in files:
            path = Path(folder, file)
            if path.is_symlink() or not path.is_file():
                continue
            if file.endswith(TEMP_SUFFIX):  # left by a killed run: not a page, so not counted
                if _owned(_read(path, 512)):
                    path.unlink()
            elif file.endswith(".html"):
                if path.relative_to(html_dir).as_posix() not in expected and _owned(_read(path, 512)):
                    path.unlink()
                    result.changed.append(path)
    return result
