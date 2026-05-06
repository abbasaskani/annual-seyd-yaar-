from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..utils_time import (
    generate_recurring_window_timestamps,
    load_timezone_options,
    time_id_from_iso,
    trusted_utc_now,
)
from ._bundle import RunContext, build_and_write_bundle


def run_seasonal(
    *,
    out_root: Path,
    aoi_geojson: dict,
    species_profiles: dict,
    start_year: int,
    end_year: int,
    season_start_mmdd: str,
    season_end_mmdd: str,
    step_value: int,
    step_unit: str,
    timezone_name: str,
    snapshot_local_hour: int = 18,
    variant: str = 'seasonal',
    run_id: str = 'seasonal-manual',
) -> str:
    _, time_source = trusted_utc_now()
    time_isos = generate_recurring_window_timestamps(
        start_year=int(start_year),
        end_year=int(end_year),
        season_start_mmdd=season_start_mmdd,
        season_end_mmdd=season_end_mmdd,
        step_value=int(step_value),
        step_unit=step_unit,
        timezone_name=timezone_name,
        snapshot_local_hour=int(snapshot_local_hour),
    )
    time_ids = [time_id_from_iso(ts) for ts in time_isos]
    tz_options = load_timezone_options()
    tz_entry: Optional[dict] = next((x for x in tz_options if x['value'] == timezone_name), None)
    temporal_spec = {
        'mode': 'seasonal_recurring',
        'start_year': int(start_year),
        'end_year': int(end_year),
        'season_start_mmdd': season_start_mmdd,
        'season_end_mmdd': season_end_mmdd,
        'step_unit': step_unit,
        'step_value': int(step_value),
        'timezone': timezone_name,
        'timezone_label': (tz_entry or {}).get('label', timezone_name),
        'timezone_utc_offset': (tz_entry or {}).get('utc_offset'),
        'snapshot_local_hour': int(snapshot_local_hour),
        'partial_last_step_allowed': True,
    }
    ctx = RunContext(
        out_root=Path(out_root),
        run_id=run_id,
        variant=variant,
        aoi_geojson=aoi_geojson,
        species_profiles=species_profiles,
        time_source=time_source,
        temporal_spec=temporal_spec,
    )
    return build_and_write_bundle(ctx, time_ids)
