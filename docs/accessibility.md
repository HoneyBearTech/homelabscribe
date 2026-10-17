# Accessibility and internationalization

## Accessibility

HomelabScribe is a command-line tool that writes plain Markdown; it has no graphical UI. Output is plain text with heading structure and tables, which works with screen readers and any Markdown viewer. CLI output does not rely on color alone (warnings and errors are labelled with the words `skipped:` and `error:`). Diagrams (Pro `topology.md`) are Mermaid text, so the underlying relationships stay readable as text.

## Internationalization

Generated pages and the CLI are in English. Service names, notes and descriptions are UTF-8 and render correctly in any language; user labels (`homelabscribe.name`, `homelabscribe.description`) can be written in any language. Translating the built-in text is possible but not currently planned; open an issue if you need it.
