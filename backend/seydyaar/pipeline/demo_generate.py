from __future__ import annotations

import json
from pathlib import Path

from .run_daily import run_daily


def demo_generate(
    *,
    date: str = 'today',
    out_dir: str = 'docs/latest',
    past_days: int = 2,
    future_days: int = 10,
    step_hours: int = 6,
    fast: bool = False,
    presence_mode: str = 'auto',
    presence_csv: str | None = None,
    export_cog: bool = False,
    depths_m: list[int] | None = None,
) -> str:
    aoi = json.loads((Path('backend/config/aoi.geojson')).read_text(encoding='utf-8'))
    species_profiles = json.loads((Path('backend/config/species_profiles.json')).read_text(encoding='utf-8'))
    return run_daily(
        out_root=Path(out_dir),
        aoi_geojson=aoi,
        species_profiles=species_profiles,
        date=date,
        past_days=past_days,
        future_days=future_days,
        step_hours=step_hours,
        grid_wh='160x160' if fast else '220x220',
        variant='demo',
        gear_depths_m=depths_m or [5, 10, 15, 20],
    )
