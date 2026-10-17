# Website source (homelabscribe.com)

A static site: plain HTML and CSS, no build step, no JavaScript, no third-party requests. It is deployed from this folder to Cloudflare Pages.

## Deploy with Workers Builds (what is set up)

`wrangler.jsonc` at the repo root points Wrangler at this folder (`assets.directory`), so the Git integration's default deploy command, `npx wrangler deploy`, publishes the site. The Worker `name` in that file must match the project name in the Cloudflare dashboard. The build also auto-runs `pip install .` because the repo is a Python project; it is harmless, and `SKIP_DEPENDENCY_INSTALL=1` as a build variable skips it. Custom domains are under the Worker's Settings → Domains & Routes.

## Deploy (Cloudflare Pages, Git integration)

1. Cloudflare dashboard → Workers & Pages → Create → Pages → Connect to Git → pick this repository.
2. Production branch `main`, **no build command**, build output directory `site`.
3. After the first deploy, Custom domains → add `homelabscribe.com` and `www.homelabscribe.com`.
4. If a redirect rule or placeholder DNS records for the domain exist from before, delete them first so Pages can attach its own records.

`_headers` sets a strict Content-Security-Policy and other security headers; Pages applies it automatically.

HSTS is sent from `_headers` (`max-age` six months, no `includeSubDomains` or `preload`), because Cloudflare's own HSTS toggle would not save. Raise `max-age` only once the domain will stay HTTPS-only for good.

## Launch switch

The site went live on 2026-10-17, together with the public repository and the PyPI package. A `prelaunch` class on `<body>` in `index.html` is the switch: with it, the page hides the GitHub, docs and install links and shows "Public release coming soon"; without it (the current state), those links show. Add the class back to return to pre-launch mode.

## Keeping it in sync

- The images in `site/assets/` are copies of the ones in `docs/assets/` (banner, logo, social preview and the three demo screenshots). Update both when they change. The README uses the website copies (`https://homelabscribe.com/assets/...`) so its images also show on PyPI.
- The Free vs Pro table mirrors the one in the README.
- Pricing is intentionally not on the page yet.
- `.assetsignore` keeps this README out of the published site.
