# Native grid fix

This patch removes the manual grid string from the seasonal/daily pipeline.

What changed:
- `backend/seydyaar/providers/copernicus_ocean.py`
  - added `resolve_reference_grid(...)` to detect AOI-native raster width/height from the reference Copernicus layer (`sst` by default)
- `backend/seydyaar/pipeline/_bundle.py`
  - removed `ctx.grid_wh` parsing
  - asks the provider for the reference grid once, then uses that width/height for masking, bundle metadata, and resampling
- `backend/seydyaar/pipeline/run_seasonal.py`
  - removed `grid_wh`
- `backend/seydyaar/pipeline/run_daily.py`
  - removed `grid_wh`
- `backend/seydyaar/__main__.py`
  - removed visible `--grid` usage
  - keeps a hidden deprecated `--grid` argument so old commands do not immediately crash
- `.github/workflows/run_seasonal.yml`
  - removed the `grid` workflow input
  - removed `--grid` from the seasonal command

Validation done locally:
- `python -m py_compile ...` on modified backend files
- dry-run with monkeypatched provider functions to confirm bundle writing now works without any `grid_wh`
