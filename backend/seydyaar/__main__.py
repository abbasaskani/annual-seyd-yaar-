"""Seyd‑Yaar CLI entrypoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _try_load_dotenv() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv()
    except Exception:
        return


def main() -> None:
    _try_load_dotenv()

    parser = argparse.ArgumentParser(prog='seydyaar')
    sub = parser.add_subparsers(dest='cmd', required=True)

    p_daily = sub.add_parser('run-daily', help='Run the real operational pipeline into docs/latest')
    p_daily.add_argument('--date', default='today')
    p_daily.add_argument('--past-days', type=int, default=7)
    p_daily.add_argument('--future-days', type=int, default=4)
    p_daily.add_argument('--step-hours', type=int, default=6)
    p_daily.add_argument('--out', default=str(Path('docs') / 'latest'))
    p_daily.add_argument('--grid', default=None, help=argparse.SUPPRESS)

    p_seasonal = sub.add_parser('run-seasonal', help='Run real recurring seasonal snapshots into docs/latest')
    p_seasonal.add_argument('--start-year', type=int, required=True)
    p_seasonal.add_argument('--end-year', type=int, required=True)
    p_seasonal.add_argument('--season-start', required=True, help='MM-DD')
    p_seasonal.add_argument('--season-end', required=True, help='MM-DD')
    p_seasonal.add_argument('--step-value', type=int, default=1)
    p_seasonal.add_argument('--step-unit', choices=['hour', 'hours', 'day', 'days', 'week', 'weeks'], default='weeks')
    p_seasonal.add_argument('--timezone', default='Indian/Mahe')
    p_seasonal.add_argument('--snapshot-local-hour', type=int, default=18)
    p_seasonal.add_argument('--out', default=str(Path('docs') / 'latest'))
    p_seasonal.add_argument('--variant', default='seasonal')
    p_seasonal.add_argument('--run-id', default='seasonal-manual')
    p_seasonal.add_argument('--grid', default=None, help=argparse.SUPPRESS)

    args = parser.parse_args()
    cfg_dir = Path('backend/config')
    aoi = json.loads((cfg_dir / 'aoi.geojson').read_text(encoding='utf-8'))
    species_profiles = json.loads((cfg_dir / 'species_profiles.json').read_text(encoding='utf-8'))

    if args.cmd == 'run-daily':
        from seydyaar.pipeline.run_daily import run_daily
        run_daily(
            out_root=Path(args.out),
            aoi_geojson=aoi,
            species_profiles=species_profiles,
            date=args.date,
            past_days=int(args.past_days),
            future_days=int(args.future_days),
            step_hours=int(args.step_hours),
        )
    elif args.cmd == 'run-seasonal':
        from seydyaar.pipeline.run_seasonal import run_seasonal
        run_seasonal(
            out_root=Path(args.out),
            aoi_geojson=aoi,
            species_profiles=species_profiles,
            start_year=args.start_year,
            end_year=args.end_year,
            season_start_mmdd=args.season_start,
            season_end_mmdd=args.season_end,
            step_value=args.step_value,
            step_unit=args.step_unit,
            timezone_name=args.timezone,
            snapshot_local_hour=args.snapshot_local_hour,
            variant=args.variant,
            run_id=args.run_id,
        )


if __name__ == '__main__':
    main()
