# Website source (homelabscribe.com)

A static site: plain HTML and CSS, no build step, no JavaScript, no third-party requests. It is deployed from this folder to Cloudflare Pages.

## Deploy (Cloudflare Pages, Git integration)

1. Cloudflare dashboard → Workers & Pages → Create → Pages → Connect to Git → pick this repository.
2. Production branch `main`, **no build command**, build output directory `site`.
3. After the first deploy, Custom domains → add `homelabscribe.com` and `www.homelabscribe.com`.
4. If a redirect rule or placeholder DNS records for the domain exist from before, delete them first so Pages can attach its own records.

`_headers` sets a strict Content-Security-Policy and other security headers; Pages applies it automatically.

## Launch switch

The page starts in pre-launch mode: `<body class="prelaunch">` hides the GitHub, docs and install links and shows "Public release coming soon". When the repository and the PyPI package are public, remove `class="prelaunch"` from `<body>` in `index.html`.

## Keeping it in sync

- The images in `site/assets/` are copies of the ones in `docs/assets/` (logo, social preview and the three demo screenshots). Update both when they change.
- The Free vs Pro table mirrors the one in the README.
- Pricing is intentionally not on the page yet.
