from __future__ import annotations

from pathlib import Path
from typing import List

from ..utils_time import time_id_from_iso, timestamps_for_range, trusted_utc_now
from ._bundle import RunContext, build_and_write_bundle


def run_daily(
    out_root: Path,
    aoi_geojson: dict,
    species_profiles: dict,
    date: str = 'today',
    past_days: int = 7,
    future_days: int = 4,
    step_hours: int = 6,
    variant: str = 'auto',
    gear_depths_m: List[int] | None = None,
) -> str:
    now_utc, time_source = trusted_utc_now()
    ts_list = timestamps_for_range(anchor_date=date, past_days=past_days, future_days=future_days, step_hours=step_hours)
    time_ids = [time_id_from_iso(ts) for ts in ts_list]
    ctx = RunContext(
        out_root=Path(out_root),
        run_id='main',
        variant=variant,
        aoi_geojson=aoi_geojson,
        species_profiles=species_profiles,
        time_source=time_source,
        temporal_spec={
            'mode': 'operational',
            'date': date,
            'past_days': int(past_days),
            'future_days': int(future_days),
            'step_hours': int(step_hours),
            'generated_anchor_utc': now_utc.isoformat().replace('+00:00', 'Z'),
        },
    )
    return build_and_write_bundle(ctx, time_ids)
