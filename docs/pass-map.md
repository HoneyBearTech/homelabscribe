# Pass map

A short record of each piece of work on homelabscribe: what shipped, or what a
review found, and where to look. Newest first. The reasons behind the choices
are in the [decisions log](decisions-log.md).

## 2026-11-20: `renovate-automerge`

Renovate merges its pull requests itself once every check passes, majors
included; new versions wait 3 days, security fixes and image rebuilds don't.
Nothing kept manual. `platformAutomerge` is false ("Allow auto-merge" is off).
Nothing merges until the review rule on `main` no longer needs an approving or
code-owner review (an owner setting). (#80)
