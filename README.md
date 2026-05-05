# annual-seyd-yaar

This revision uses **real Copernicus Marine environmental data** and adds an **independent manual seasonal workflow** for recurring seasonal runs across years.

## What changed

- `run-daily` remains the short-horizon operational path.
- `run-seasonal` is a separate manual command for recurring seasonal snapshots across years.
- Seasonal stepping is configurable in `hours / days / weeks`.
- If the requested seasonal window is not exactly divisible by the step, the pipeline **still appends the seasonal end timestamp** instead of failing.
- Time anchoring is **timezone-explicit** and converted to UTC for storage.
- Output layout stays compatible with the existing viewer (`docs/latest`, `runs/<run_id>`, per-time rasters).
- No averaging is applied in the seasonal generator.
- Raw Copernicus subsets are cached under `backend/data/cache/copernicus` so repeated requests for the same dataset/window do not re-download unnecessarily.
- Copernicus downloads use retry/backoff before failing hard.
- Manual GitHub Actions workflow: `.github/workflows/run_seasonal.yml`

## Runtime requirements

Install the backend requirements and configure your Copernicus Marine credentials:

```bash
pip install -r backend/requirements.txt
copernicusmarine login
```

Or provide credentials through the official environment variables:

- `COPERNICUSMARINE_SERVICE_USERNAME`
- `COPERNICUSMARINE_SERVICE_PASSWORD`

## Real seasonal run example

```bash
PYTHONPATH=backend python -m seydyaar run-seasonal \
  --start-year 2022 \
  --end-year 2025 \
  --season-start 06-15 \
  --season-end 09-30 \
  --step-value 4 \
  --step-unit days \
  --timezone Indian/Mahe \
  --snapshot-local-hour 18 \
  --run-id seasonal-manual
```

## Manual GitHub workflow

After committing the workflow file to the default branch, go to **Actions → Run seasonal manual → Run workflow** and fill the inputs.

The workflow:

- validates Copernicus credentials
- restores subset cache
- runs `run-seasonal`
- uploads `docs/latest` as an artifact
- optionally commits `docs/latest` back to the selected branch

## Notes on outputs

The current repository does **not** yet contain an effort / AIS / catch assimilation module, so `pcatch` remains the environmental habitat-based catch proxy (`phab`) until a separate fishing-presence layer is integrated.

Each per-time folder writes a `diagnostics.json` file showing which Copernicus dataset/time slice was actually selected for that timestamp, whether the raw subset came from cache, and how many retry attempts were needed.
