from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import List, Tuple
from zoneinfo import ZoneInfo

import requests


def trusted_utc_now(timeout_s: float = 3.5) -> Tuple[dt.datetime, str]:
    urls = [
        "https://worldtimeapi.org/api/timezone/Etc/UTC",
        "https://timeapi.io/api/Time/current/zone?timeZone=UTC",
    ]
    for url in urls:
        try:
            r = requests.get(url, timeout=timeout_s)
            r.raise_for_status()
            payload = r.json()
            if "utc_datetime" in payload:
                stamp = dt.datetime.fromisoformat(payload["utc_datetime"].replace("Z", "+00:00"))
            elif "dateTime" in payload:
                stamp = dt.datetime.fromisoformat(payload["dateTime"]).astimezone(dt.timezone.utc)
            else:
                continue
            return stamp.astimezone(dt.timezone.utc), url
        except Exception:
            continue
    return dt.datetime.now(dt.timezone.utc), "system"


def parse_anchor_date(date_value: str) -> dt.date:
    value = str(date_value).strip()
    if value.lower() == "today":
        return trusted_utc_now()[0].date()
    return dt.date.fromisoformat(value)


def time_id_from_iso(iso_dt: str) -> str:
    stamp = dt.datetime.fromisoformat(iso_dt.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    return stamp.strftime("%Y%m%d_%H%MZ")


def iso_from_time_id(time_id: str) -> str:
    stamp = dt.datetime.strptime(time_id, "%Y%m%d_%H%MZ").replace(tzinfo=dt.timezone.utc)
    return stamp.isoformat().replace("+00:00", "Z")


def timestamps_for_range(anchor_date: str, past_days: int, future_days: int, step_hours: int) -> List[str]:
    anchor = parse_anchor_date(anchor_date)
    start = dt.datetime.combine(anchor - dt.timedelta(days=int(past_days)), dt.time(0, 0), tzinfo=dt.timezone.utc)
    end = dt.datetime.combine(anchor + dt.timedelta(days=int(future_days)), dt.time(0, 0), tzinfo=dt.timezone.utc)
    step = dt.timedelta(hours=max(int(step_hours), 1))
    return generate_stepwise_timestamps(start, end, step)


def generate_stepwise_timestamps(start: dt.datetime, end: dt.datetime, step: dt.timedelta) -> List[str]:
    if step.total_seconds() <= 0:
        raise ValueError("step must be positive")
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start/end must be timezone-aware")
    if end < start:
        raise ValueError("end must be >= start")

    out: List[str] = []
    current = start
    while current <= end:
        out.append(current.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z"))
        current = current + step

    if not out:
        out.append(start.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z"))
    end_iso = end.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    if out[-1] != end_iso:
        out.append(end_iso)
    return out


def load_timezone_options(config_path: Path | None = None) -> List[dict]:
    path = config_path or (Path("backend") / "config" / "seasonal_timezones.json")
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_month_day(value: str) -> Tuple[int, int]:
    raw = str(value).strip().replace("/", "-")
    parts = [p for p in raw.split("-") if p]

    if len(parts) == 2:
        month, day = int(parts[0]), int(parts[1])
    elif len(parts) == 3:
        # Accept YYYY-MM-DD and ignore the year component.
        # Seasonal recurrence is year-driven by start_year/end_year.
        month, day = int(parts[1]), int(parts[2])
    else:
        raise ValueError(
            f"Invalid seasonal date '{value}'. Use MM-DD or YYYY-MM-DD, e.g. '06-15' or '2022-06-15'."
        )

    if not (1 <= month <= 12):
        raise ValueError(f"Invalid month in seasonal date '{value}': {month}. Expected 1..12.")
    if not (1 <= day <= 31):
        raise ValueError(f"Invalid day in seasonal date '{value}': {day}. Expected 1..31.")
    return month, day


def _step_delta(step_unit: str, step_value: int) -> dt.timedelta:
    value = max(int(step_value), 1)
    unit = step_unit.strip().lower()
    if unit in {"day", "days", "d"}:
        return dt.timedelta(days=value)
    if unit in {"week", "weeks", "w"}:
        return dt.timedelta(weeks=value)
    if unit in {"hour", "hours", "h"}:
        return dt.timedelta(hours=value)
    raise ValueError(f"Unsupported step unit: {step_unit}")


def generate_recurring_window_timestamps(
    *,
    start_year: int,
    end_year: int,
    season_start_mmdd: str,
    season_end_mmdd: str,
    step_value: int,
    step_unit: str,
    timezone_name: str,
    snapshot_local_hour: int = 18,
) -> List[str]:
    start_year = int(start_year)
    end_year = int(end_year)
    if end_year < start_year:
        raise ValueError(f"end_year ({end_year}) must be >= start_year ({start_year})")

    hour = int(snapshot_local_hour)
    if not (0 <= hour <= 23):
        raise ValueError(f"snapshot_local_hour must be 0..23, got {snapshot_local_hour}")

    tzinfo = ZoneInfo(timezone_name)
    start_month, start_day = _parse_month_day(season_start_mmdd)
    end_month, end_day = _parse_month_day(season_end_mmdd)
    step = _step_delta(step_unit, step_value)

    out: List[str] = []
    for year in range(start_year, end_year + 1):
        local_start = dt.datetime(year, start_month, start_day, hour, 0, tzinfo=tzinfo)
        end_year_actual = year + 1 if (end_month, end_day) < (start_month, start_day) else year
        local_end = dt.datetime(end_year_actual, end_month, end_day, hour, 0, tzinfo=tzinfo)
        out.extend(generate_stepwise_timestamps(local_start, local_end, step))

    deduped = sorted(set(out), key=lambda x: dt.datetime.fromisoformat(x.replace("Z", "+00:00")))
    return deduped
