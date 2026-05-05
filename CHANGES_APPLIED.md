# Changes applied

## Real Copernicus Marine data path

- Seasonal and daily bundle generation use real Copernicus Marine environmental datasets defined in `backend/config/datasets.json`.
- Synthetic seasonal generation is no longer the active path.

## Seasonal stepping behavior

- Seasonal snapshots support `hours / days / weeks`.
- If the season window is not exactly divisible by the step, the final seasonal endpoint is still included.

## Cache of raw Copernicus subsets

- Added raw subset caching in `backend/data/cache/copernicus`.
- Cache keys are built from dataset id, requested variables, AOI bbox, time window and requested depth.
- Repeated requests for the same subset reuse the cached `.nc` file instead of downloading again.

## Retry / backoff

- Added retry with exponential backoff around Copernicus subset downloads.
- The run still fails hard after retries if the real download cannot be completed.

## Existing-output reuse

- If all expected rasters and diagnostics already exist for a requested time, that time is reused instead of recomputed.
- Reused timestamps are logged in run metadata.

## Manual GitHub Actions workflow

- Added `.github/workflows/run_seasonal.yml`
- It supports `workflow_dispatch` inputs for seasonal range, step, timezone, hour, grid and run id.
- It reads the Copernicus secrets from:
  - `COPERNICUSMARINE_SERVICE_USERNAME`
  - `COPERNICUSMARINE_SERVICE_PASSWORD`
- It uploads `docs/latest` as an artifact and can optionally commit `docs/latest` back to the branch.

## Dependency updates

- Added `netCDF4` for robust local opening of cached NetCDF subsets.
