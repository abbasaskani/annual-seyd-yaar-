UI restore changes for annual seyd yaar

- Restored docs UI files from the benchmark Seyd-Yaar viewer as the visual baseline.
- Kept the older map-first Leaflet layout instead of the simplified replacement viewer.
- Added a small seasonal details box that appears only when run metadata reports `temporal_spec.mode = seasonal_recurring`.
- Seasonal mode is additive:
  - existing controls remain in place
  - `Average (range)` and lookback are disabled in seasonal mode
  - playback step switches to frame-based stepping instead of hard-coded 6h logic
- Viewer now supports the current annual bundle schema:
  - `latest/meta.json` and `latest/index.json`
  - per-time keys `phab`, `pcatch`, `ops`, `mask`, `diagnostics`
- Grid/bounds fallback now uses run bbox when species meta omits lon/lat bounds.
- Service worker cache version bumped.

Notes:
- The public repo exposes metadata and time folders, but the available tools here could not pull the committed binary `.f32/.u8` rasters directly for exact screenshot reconstruction.
- Preview PNGs were therefore rendered locally from placeholder rasters shaped like the current repo output schema, only to show the restored UI direction.
