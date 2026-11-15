# Security requirements

What HomelabScribe™ is designed to guarantee, and what it is not.

## Requirements

1. **Read-only.** Collectors never call an API that changes a container, VM or host.
2. **No secret values on disk.** Environment variable *values*, tokens, passwords and credential-like labels are never written to snapshots or Markdown. Only variable names are kept. Tested by `tests/test_collectors.py::test_secrets_never_stored`.
3. **No phone-home.** No telemetry, update checks or license calls. Licenses verify offline against an embedded public key. The one exception is a notification (Pro) you turn on yourself: it posts a short summary of changes to the ntfy or Discord address in your own config, and nowhere else.
4. **Least privilege.** Docker access through a socket proxy with `POST=0`; Proxmox access with the `PVEAuditor` role only. The container runs as a non-root user.
5. **Safe parsing.** YAML is loaded with `safe_load`; templates are bundled, not user-supplied.
6. **Your notes are never destroyed.** Removed pages that hold notes are archived.
7. **Verifiable releases.** Releases are signed (Sigstore), with checksums, an SBOM and provenance ([verifying releases](verifying-releases.md)).

## What HomelabScribe does not protect

- **The generated docs are a map of your lab** (hosts, ports, service names, paths). Treat the output folder as sensitive, especially if you publish it to Git.
- **An exposed Docker API is not authenticated.** Bind the proxy to a trusted LAN address only.
- **Config files may hold a Proxmox token or Pro key.** Keep `homelabscribe.yaml` out of Git and readable only by you.
- It does not harden Docker, Proxmox, Nginx Proxy Manager or the host.

Nginx Proxy Manager: the API only issues a login token (it has no read-only token), so HomelabScribe logs in with the credentials in your config, reads the proxy hosts and discards the session token. It changes no proxy settings. Use an NPM user whose role is view-only, so a leaked config can't change your proxies.

Credential handling: tokens come from the config or environment (`${VAR}`) and are used only to talk to the configured endpoint; they are not logged or rendered. Report problems via [SECURITY.md](../SECURITY.md).

## The bug-report bundle

`homelabscribe doctor --bundle` writes a small zip archive to attach to an issue, which is public. It masks more rather than less, and it holds text files only:

| File | Holds |
| --- | --- |
| `README.txt` | What the archive is and what was removed |
| `doctor.txt` | For each configured source: its placeholder, its kind and OK, FAILED (with the type name of the error) or SKIPPED (needs Pro). The license as one of five fixed texts. Whether a config file was found |
| `environment.json` | The versions of HomelabScribe, Python and the direct dependencies; the operating system family and machine type; whether it runs in a container |
| `config.redacted.yaml` | The config as it was loaded, with its values masked, and the number of keys HomelabScribe does not know |
| `snapshot-summary.json` | How many snapshots there are, the time of the newest and counts from it |

What is removed:

- **Names** of Docker hosts, Proxmox clusters, proxies and hand-described services become numbered placeholders (`docker-host-1`, `proxmox-1`, `npm-1`, `manual-1`), the same one in every file.
- **Every other text in the config** (addresses, users, tokens, passwords, emails, contacts, paths, descriptions) becomes `<redacted>`, and the license key becomes `<set>` or `<not set>`. What stays is structure: which keys are set, the length of a list, booleans, port numbers, a notification's `type` and `on`, and the scheme of a URL when it is `unix`, `tcp`, `http`, `https` or `ssh`. A config field added in a later version is masked until the code lists it as safe to keep.
- **What a server answered** is kept only as a version number or a whole number. A version is up to three dotted parts of at most four digits each (`27.3.1`; a suffix such as `-ce` is dropped); anything else, such as an address or a longer dotted number, becomes `unknown`. A count is a whole number from 0 to 999999, otherwise `unknown`. An error is reduced to its type name; its text is dropped.
- **From the newest snapshot** only counts are taken, under fixed keys: hosts and services by kind, services by the states Docker and Proxmox document, services by the categories of the built-in catalog, and routes. Anything else counts under `other`. No names, images, labels, addresses or notes are copied. A snapshot that cannot be read or summarised is reported as unreadable, with the type name of the error, and the bundle is still written.
- **Left out entirely:** the email and end date of a license, the config path, the output folder, the host name, the user name and environment variables. Generated pages, your notes and the changelog entries are not read.

The entries of the archive carry the time of the run and nothing else of the machine. The command makes no network call beyond the connection tests `doctor` already makes, creates no folder and never overwrites an existing file. `tests/test_bundle.py` seeds a marker into every free-text field of the config, a snapshot and the servers' answers and asserts that none reaches the archive.

**Read the archive before you share it.** It is plain text, and you are the last check. The bundle is not the same as what `doctor` prints on screen: that output holds no secret values, but it does show the names of your sources, the config path and the email a Pro key is licensed to.
