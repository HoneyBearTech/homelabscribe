# Accessibility and internationalization

## Accessibility

HomelabScribe™ is a command-line tool that writes plain Markdown and, optionally, a static HTML copy of it; it has no graphical UI. Output is plain text with heading structure and tables, which works with screen readers and any Markdown viewer. CLI output does not rely on color alone (warnings and errors are labelled with the words `skipped:` and `error:`). Diagrams (Pro `topology.md`) are Mermaid text, so the underlying relationships stay readable as text.

The static HTML copy (`html_dir`) keeps the heading structure and table headers of the Markdown. Each page declares its language (`lang="en"`), has a viewport line so it reads on a phone, follows the system's light or dark setting, and uses text and link colors with a contrast ratio of at least 4.5:1 in both. It needs no JavaScript and stays readable without its styles. Wide tables scroll sideways instead of being cut off. The topology diagram is shown as its Mermaid text, with one line saying how to see it drawn.

## Internationalization

Generated pages and the CLI are in English. Service names, notes and descriptions are UTF-8 and render correctly in any language; user labels (`homelabscribe.name`, `homelabscribe.description`) can be written in any language. Translating the built-in text is possible but not currently planned; open an issue if you need it.
