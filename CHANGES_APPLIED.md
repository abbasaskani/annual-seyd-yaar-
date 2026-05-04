# Changes applied

- Added GitHub Pages root redirect: `docs/index.html -> docs/app.html`
- Added `docs/404.html` redirect fallback
- Updated `docs/manifest.json` with `start_url: ./app.html` and scope
- Updated `docs/sw.js` with navigation fallback and cache bump
- Patched `docs/app.js` so UI-facing latitude values use `lat - 0.27`
- For user-entered latitude fields in UI, values are converted back internally with `+0.27` before calculations
