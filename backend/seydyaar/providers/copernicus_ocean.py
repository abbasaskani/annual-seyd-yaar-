from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np

from ..utils_geo import GridSpec, bbox_from_geojson


class OceanDataError(RuntimeError):
    pass


class DatasetCandidateRejected(RuntimeError):
    def __init__(self, dataset_id: str, message: str, *, original_exc: Exception | None = None) -> None:
        super().__init__(message)
        self.dataset_id = dataset_id
        self.original_exc = original_exc


def _require_modules():
    try:
        import copernicusmarine  # type: ignore
        import xarray as xr  # type: ignore
    except Exception as exc:  # pragma: no cover - runtime dependent
        raise OceanDataError(
            "Real Copernicus Marine access requires 'copernicusmarine' and 'xarray'. "
            "Install backend/requirements.txt and configure Copernicus credentials first."
        ) from exc
    return copernicusmarine, xr


def load_datasets_config(config_path: Path | None = None) -> dict:
    path = config_path or (Path("backend") / "config" / "datasets.json")
    return json.loads(path.read_text(encoding="utf-8"))


def _format_iso_z(stamp: dt.datetime) -> str:
    return stamp.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_optional_datetime(value: object) -> dt.datetime | None:
    if value in (None, "", "null"):
        return None
    text = str(value)
    if len(text) == 10 and text.count("-") == 2:
        text = f"{text}T00:00:00+00:00"
    else:
        text = text.replace("Z", "+00:00")
    stamp = dt.datetime.fromisoformat(text)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.timezone.utc)
    return stamp.astimezone(dt.timezone.utc)


def _coord_name(candidates: Sequence[str], available: Iterable[str]) -> str | None:
    available_set = {str(x) for x in available}
    for cand in candidates:
        if cand in available_set:
            return cand
    return None


def _time_coord_name(ds) -> str | None:
    return _coord_name(("time", "valid_time"), list(ds.coords) + list(ds.dims))


def _lat_coord_name(ds) -> str:
    name = _coord_name(("latitude", "lat", "y"), list(ds.coords) + list(ds.dims))
    if not name:
        raise OceanDataError(f"Could not locate latitude coordinate in dataset coords={list(ds.coords)}")
    return name


def _lon_coord_name(ds) -> str:
    name = _coord_name(("longitude", "lon", "x"), list(ds.coords) + list(ds.dims))
    if not name:
        raise OceanDataError(f"Could not locate longitude coordinate in dataset coords={list(ds.coords)}")
    return name


def _depth_coord_name(ds) -> str | None:
    return _coord_name(("depth", "depthu", "depthv", "lev", "z"), list(ds.coords) + list(ds.dims))


def _normalize_longitudes(values: np.ndarray, reference_range: np.ndarray | None = None) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64).copy()
    if arr.size == 0:
        return arr
    if reference_range is not None and reference_range.size:
        ref_min = float(np.nanmin(reference_range))
        ref_max = float(np.nanmax(reference_range))
        if ref_min < 0.0 and np.nanmin(arr) >= 0.0:
            arr = np.where(arr > 180.0, arr - 360.0, arr)
        elif ref_max > 180.0 and np.nanmax(arr) <= 180.0:
            arr = np.where(arr < 0.0, arr + 360.0, arr)
    elif np.nanmax(arr) > 180.0:
        arr = np.where(arr > 180.0, arr - 360.0, arr)
    return arr


def _nearest_index(sorted_values: np.ndarray, targets: np.ndarray) -> np.ndarray:
    idx = np.searchsorted(sorted_values, targets)
    idx = np.clip(idx, 0, len(sorted_values) - 1)
    prev_idx = np.clip(idx - 1, 0, len(sorted_values) - 1)
    next_idx = idx
    prev_dist = np.abs(sorted_values[prev_idx] - targets)
    next_dist = np.abs(sorted_values[next_idx] - targets)
    return np.where(prev_dist <= next_dist, prev_idx, next_idx)


def _resample_2d_nearest(values: np.ndarray, src_lats: np.ndarray, src_lons: np.ndarray, grid: GridSpec) -> np.ndarray:
    data = np.asarray(values, dtype=np.float32)
    lats = np.asarray(src_lats, dtype=np.float64)
    lons = np.asarray(src_lons, dtype=np.float64)

    if data.ndim != 2:
        raise OceanDataError(f"Expected a 2D field for resampling, got ndim={data.ndim}")
    if lats.ndim != 1 or lons.ndim != 1:
        raise OceanDataError(
            f"Expected 1D lat/lon coordinates for resampling, got lat_ndim={lats.ndim}, lon_ndim={lons.ndim}"
        )

    lons = _normalize_longitudes(lons, np.asarray([grid.lon_min, grid.lon_max], dtype=np.float64))

    lon_order = np.argsort(lons)
    lat_order = np.argsort(lats)
    lons_sorted = lons[lon_order]
    lats_sorted = lats[lat_order]
    data_sorted = data[np.ix_(lat_order, lon_order)]

    tgt_lons = np.linspace(grid.lon_min, grid.lon_max, grid.width, dtype=np.float64)
    tgt_lats = np.linspace(grid.lat_max, grid.lat_min, grid.height, dtype=np.float64)

    lon_idx = _nearest_index(lons_sorted, tgt_lons)
    lat_idx = _nearest_index(lats_sorted, tgt_lats)
    return data_sorted[np.ix_(lat_idx, lon_idx)].astype(np.float32)




def _normalize_target_time_for_coord(coord_values, target_time_utc: dt.datetime):
    arr = np.asarray(coord_values)
    if np.issubdtype(arr.dtype, np.datetime64):
        naive_utc = target_time_utc.astimezone(dt.timezone.utc).replace(tzinfo=None)
        return np.datetime64(naive_utc, "ns")
    return target_time_utc

def _select_variable_2d(ds, var_name: str, *, target_time_utc: dt.datetime, depth_target_m: float | None):
    if var_name not in ds:
        raise OceanDataError(f"Variable '{var_name}' not found in dataset variables={list(ds.data_vars)}")

    da = ds[var_name]
    actual_time_utc: str | None = None
    actual_depth_m: float | None = None

    time_name = _time_coord_name(da)
    if time_name and time_name in da.coords:
        try:
            sel_target = _normalize_target_time_for_coord(da.coords[time_name].values, target_time_utc)
            da = da.sel({time_name: sel_target}, method="nearest")
            actual_time_raw = np.asarray(da.coords[time_name].values).squeeze()
            actual_time = np.datetime64(actual_time_raw).astype("datetime64[ns]").tolist()
            if isinstance(actual_time, dt.datetime):
                if actual_time.tzinfo is None:
                    actual_time = actual_time.replace(tzinfo=dt.timezone.utc)
                actual_time_utc = actual_time.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        except Exception as exc:
            raise OceanDataError(
                f"Could not select nearest time for variable '{var_name}' (coord dtype={np.asarray(da.coords[time_name].values).dtype})"
            ) from exc

    depth_name = _depth_coord_name(da)
    if depth_name and depth_name in da.coords and depth_target_m is not None:
        try:
            da = da.sel({depth_name: float(depth_target_m)}, method="nearest")
            actual_depth_m = float(np.asarray(da.coords[depth_name].values).squeeze())
        except Exception as exc:
            raise OceanDataError(f"Could not select nearest depth for variable '{var_name}'") from exc

    da = da.squeeze(drop=True)
    lat_name = _lat_coord_name(da)
    lon_name = _lon_coord_name(da)
    if da.ndim != 2:
        raise OceanDataError(f"Variable '{var_name}' did not resolve to 2D after time/depth selection; ndim={da.ndim}")
    return da, lat_name, lon_name, actual_time_utc, actual_depth_m


def _cache_root() -> Path:
    root = os.getenv("SEYDYAAR_CMEMS_CACHE_DIR", str(Path("backend") / "data" / "cache" / "copernicus"))
    path = Path(root)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _time_half_window_hours() -> int:
    return max(int(os.getenv("SEYDYAAR_CMEMS_TIME_WINDOW_HOURS", "48")), 1)


def _retry_attempts() -> int:
    return max(int(os.getenv("SEYDYAAR_CMEMS_RETRY_ATTEMPTS", "4")), 1)


def _retry_base_seconds() -> float:
    return max(float(os.getenv("SEYDYAAR_CMEMS_RETRY_BASE_SECONDS", "3.0")), 0.1)


def _dataset_cache_path(*, dataset_id: str, variables: list[str], bbox: tuple[float, float, float, float], target_time_utc: dt.datetime, depth_target_m: float | None) -> Path:
    time_half_window = dt.timedelta(hours=_time_half_window_hours())
    payload = {
        "dataset_id": dataset_id,
        "variables": sorted(list(variables)),
        "bbox": [round(float(x), 6) for x in bbox],
        "start": _format_iso_z(target_time_utc - time_half_window),
        "end": _format_iso_z(target_time_utc + time_half_window),
        "depth_target_m": None if depth_target_m is None else round(float(depth_target_m), 3),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:20]
    safe_dataset = dataset_id.replace("/", "_")
    return _cache_root() / safe_dataset / f"{digest}.nc"


NON_RETRYABLE_MARKERS = (
    "coordinatesoutofdatasetbounds",
    "exceed the dataset coordinates",
    "outside dataset coverage",
    "no candidate dataset covers",
    "no overlapping data",
)


def _is_non_retryable_error(exc: Exception) -> bool:
    text = f"{exc.__class__.__name__}: {exc}".lower()
    return any(marker in text for marker in NON_RETRYABLE_MARKERS)


def _iter_layer_candidates(logical_name: str, item: Mapping[str, object]) -> list[dict[str, object]]:
    base = {k: v for k, v in item.items() if k != "candidates"}
    raw_candidates = item.get("candidates")
    if not raw_candidates:
        return [base]
    if not isinstance(raw_candidates, Sequence):
        raise OceanDataError(f"Dataset config for '{logical_name}' has a non-list 'candidates' field")
    out: list[dict[str, object]] = []
    for idx, raw in enumerate(raw_candidates):
        if not isinstance(raw, Mapping):
            raise OceanDataError(f"Dataset candidate #{idx + 1} for '{logical_name}' must be a mapping")
        merged = dict(base)
        merged.pop("dataset_id", None)
        merged.pop("variable", None)
        merged.pop("variables", None)
        merged.update(raw)
        out.append(merged)
    return out


def _candidate_variables(candidate: Mapping[str, object], logical_name: str) -> list[str]:
    variables = list(candidate.get("variables") or ([candidate["variable"]] if candidate.get("variable") else []))
    if not variables:
        raise OceanDataError(f"Dataset config for '{logical_name}' has no variable(s)")
    return [str(v) for v in variables]


def _candidate_covers_time(candidate: Mapping[str, object], target_time_utc: dt.datetime) -> bool:
    start = _parse_optional_datetime(candidate.get("coverage_start"))
    end = _parse_optional_datetime(candidate.get("coverage_end"))
    if start and target_time_utc < start:
        return False
    if end and target_time_utc > end:
        return False
    return True


def _candidate_desc(candidate: Mapping[str, object]) -> str:
    dataset_id = str(candidate.get("dataset_id", "<missing-dataset-id>"))
    label = str(candidate.get("label", "")).strip()
    return f"{label} ({dataset_id})" if label else dataset_id


def _download_subset_to_cache(*, dataset_id: str, variables: list[str], bbox: tuple[float, float, float, float], target_time_utc: dt.datetime, depth_target_m: float | None) -> tuple[Path, bool, int]:
    copernicusmarine, _ = _require_modules()
    path = _dataset_cache_path(
        dataset_id=dataset_id,
        variables=variables,
        bbox=bbox,
        target_time_utc=target_time_utc,
        depth_target_m=depth_target_m,
    )
    if path.exists() and path.stat().st_size > 0:
        return path, True, 0

    path.parent.mkdir(parents=True, exist_ok=True)
    time_half_window = dt.timedelta(hours=_time_half_window_hours())
    lon_min, lat_min, lon_max, lat_max = bbox
    kwargs = dict(
        dataset_id=dataset_id,
        variables=variables,
        minimum_longitude=float(lon_min),
        maximum_longitude=float(lon_max),
        minimum_latitude=float(lat_min),
        maximum_latitude=float(lat_max),
        start_datetime=_format_iso_z(target_time_utc - time_half_window),
        end_datetime=_format_iso_z(target_time_utc + time_half_window),
        output_filename=path.name,
        output_directory=str(path.parent),
        file_format="netcdf",
        overwrite=False,
        skip_existing=True,
        disable_progress_bar=True,
    )
    if depth_target_m is not None:
        kwargs["minimum_depth"] = max(0.0, float(depth_target_m) - 10.0)
        kwargs["maximum_depth"] = float(depth_target_m) + 10.0

    attempts = _retry_attempts()
    base_sleep = _retry_base_seconds()
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            copernicusmarine.subset(**kwargs)
            if not path.exists() or path.stat().st_size <= 0:
                raise OceanDataError(f"Subset reported success but cache file is missing or empty: {path}")
            return path, False, attempt
        except Exception as exc:  # pragma: no cover - runtime dependent
            last_exc = exc
            if _is_non_retryable_error(exc):
                raise DatasetCandidateRejected(
                    dataset_id,
                    f"Dataset '{dataset_id}' rejected for requested time window: {exc}",
                    original_exc=exc,
                ) from exc
            if attempt >= attempts:
                break
            time.sleep(base_sleep * (2 ** (attempt - 1)))

    raise OceanDataError(
        f"Failed to download Copernicus subset for dataset '{dataset_id}' after {attempts} attempts."
    ) from last_exc


def _open_cached_dataset(path: Path):
    _, xr = _require_modules()
    try:
        return xr.open_dataset(path)
    except Exception as exc:  # pragma: no cover - runtime dependent
        raise OceanDataError(f"Could not open cached NetCDF subset: {path}") from exc


def _resolve_candidate_for_grid(*, logical_name: str, item: Mapping[str, object], bbox: tuple[float, float, float, float], target_time_utc: dt.datetime):
    failures: list[str] = []
    for idx, candidate in enumerate(_iter_layer_candidates(logical_name, item), start=1):
        dataset_id = str(candidate.get("dataset_id", "")).strip()
        if not dataset_id:
            failures.append(f"candidate #{idx} for '{logical_name}' has no dataset_id")
            continue
        if not _candidate_covers_time(candidate, target_time_utc):
            failures.append(f"candidate #{idx} {_candidate_desc(candidate)} skipped: requested time outside declared coverage")
            continue
        variables = _candidate_variables(candidate, logical_name)
        depth_target_m = candidate.get("depth_target_m")
        try:
            cache_path, cache_hit, retry_attempts_used = _download_subset_to_cache(
                dataset_id=dataset_id,
                variables=variables,
                bbox=bbox,
                target_time_utc=target_time_utc,
                depth_target_m=float(depth_target_m) if depth_target_m is not None else None,
            )
            return candidate, variables, cache_path, cache_hit, retry_attempts_used
        except DatasetCandidateRejected as exc:
            failures.append(str(exc))
            continue
        except OceanDataError as exc:
            failures.append(str(exc))
            continue
    raise OceanDataError(
        f"No candidate dataset covers the requested time {target_time_utc.isoformat()} for layer '{logical_name}'. "
        f"Tried: {' | '.join(failures) if failures else 'no valid candidates'}"
    )


def resolve_reference_grid(
    *,
    aoi_geojson: dict,
    target_time_utc: dt.datetime,
    datasets_cfg: Mapping[str, object] | None = None,
    preferred_logical_name: str | None = None,
) -> tuple[GridSpec, dict[str, object]]:
    cfg = dict(datasets_cfg or load_datasets_config().get("cmems", {}))
    if not cfg:
        raise OceanDataError("No Copernicus datasets were configured in backend/config/datasets.json")

    logical_name = preferred_logical_name or str(cfg.get("reference_grid_from") or "sst")
    item = cfg.get(logical_name)
    if not item or not isinstance(item, Mapping):
        raise OceanDataError(f"Missing dataset config for reference grid layer '{logical_name}'")

    bbox = bbox_from_geojson(aoi_geojson)
    candidate, variables, cache_path, cache_hit, retry_attempts_used = _resolve_candidate_for_grid(
        logical_name=logical_name,
        item=item,
        bbox=bbox,
        target_time_utc=target_time_utc,
    )
    dataset_id = str(candidate["dataset_id"])
    depth_target_m = candidate.get("depth_target_m")

    ds = _open_cached_dataset(cache_path)
    try:
        var_name = variables[0]
        da, lat_name, lon_name, actual_time_utc, actual_depth_m = _select_variable_2d(
            ds,
            var_name,
            target_time_utc=target_time_utc,
            depth_target_m=float(depth_target_m) if depth_target_m is not None else None,
        )
        src_lats = np.asarray(da[lat_name].values, dtype=np.float64)
        src_lons = np.asarray(da[lon_name].values, dtype=np.float64)
        if src_lats.ndim != 1 or src_lons.ndim != 1:
            raise OceanDataError(
                f"Reference grid layer '{logical_name}' must expose 1D lat/lon coordinates; "
                f"got lat_ndim={src_lats.ndim}, lon_ndim={src_lons.ndim}"
            )
        src_lons = _normalize_longitudes(src_lons, np.asarray([bbox[0], bbox[2]], dtype=np.float64))
        grid = GridSpec(
            lon_min=float(np.nanmin(src_lons)),
            lon_max=float(np.nanmax(src_lons)),
            lat_min=float(np.nanmin(src_lats)),
            lat_max=float(np.nanmax(src_lats)),
            width=int(src_lons.size),
            height=int(src_lats.size),
        )
        provenance = {
            "logical_name": logical_name,
            "dataset_id": dataset_id,
            "candidate_label": candidate.get("label"),
            "variable": var_name,
            "actual_time_utc": actual_time_utc,
            "actual_depth_m": actual_depth_m,
            "cache_path": str(cache_path).replace("\\", "/"),
            "cache_hit": bool(cache_hit),
            "retry_attempts_used": int(retry_attempts_used),
            "width": int(src_lons.size),
            "height": int(src_lats.size),
            "bbox": [float(np.nanmin(src_lons)), float(np.nanmin(src_lats)), float(np.nanmax(src_lons)), float(np.nanmax(src_lats))],
        }
        return grid, provenance
    finally:
        try:
            ds.close()
        except Exception:
            pass


def _fetch_layer_from_candidates(*, logical_name: str, item: Mapping[str, object], bbox: tuple[float, float, float, float], target_time_utc: dt.datetime, grid: GridSpec) -> tuple[dict[str, np.ndarray], dict[str, object]]:
    failures: list[str] = []
    for idx, candidate in enumerate(_iter_layer_candidates(logical_name, item), start=1):
        dataset_id = str(candidate.get("dataset_id", "")).strip()
        if not dataset_id:
            failures.append(f"candidate #{idx} for '{logical_name}' has no dataset_id")
            continue
        if not _candidate_covers_time(candidate, target_time_utc):
            failures.append(f"candidate #{idx} {_candidate_desc(candidate)} skipped: requested time outside declared coverage")
            continue
        variables = _candidate_variables(candidate, logical_name)
        depth_target_m = candidate.get("depth_target_m")
        try:
            cache_path, cache_hit, retry_attempts_used = _download_subset_to_cache(
                dataset_id=dataset_id,
                variables=variables,
                bbox=bbox,
                target_time_utc=target_time_utc,
                depth_target_m=float(depth_target_m) if depth_target_m is not None else None,
            )
            ds = _open_cached_dataset(cache_path)
            try:
                loaded: dict[str, np.ndarray] = {}
                used_time: str | None = None
                used_depth: float | None = None
                for var_name in variables:
                    da, lat_name, lon_name, actual_time_utc, actual_depth_m = _select_variable_2d(
                        ds,
                        var_name,
                        target_time_utc=target_time_utc,
                        depth_target_m=float(depth_target_m) if depth_target_m is not None else None,
                    )
                    loaded[var_name] = _resample_2d_nearest(
                        np.asarray(da.values, dtype=np.float32),
                        np.asarray(da[lat_name].values, dtype=np.float64),
                        np.asarray(da[lon_name].values, dtype=np.float64),
                        grid,
                    )
                    if actual_time_utc:
                        used_time = actual_time_utc
                    if actual_depth_m is not None:
                        used_depth = actual_depth_m
            finally:
                try:
                    ds.close()
                except Exception:
                    pass
            provenance = {
                "dataset_id": dataset_id,
                "candidate_label": candidate.get("label"),
                "variables": variables,
                "actual_time_utc": used_time,
                "actual_depth_m": used_depth,
                "cache_path": str(cache_path).replace("\\", "/"),
                "cache_hit": bool(cache_hit),
                "retry_attempts_used": int(retry_attempts_used),
            }
            return loaded, provenance
        except DatasetCandidateRejected as exc:
            failures.append(str(exc))
            continue
        except OceanDataError as exc:
            failures.append(str(exc))
            continue
    raise OceanDataError(
        f"No candidate dataset covers the requested time {target_time_utc.isoformat()} for layer '{logical_name}'. "
        f"Tried: {' | '.join(failures) if failures else 'no valid candidates'}"
    )


def fetch_environment_fields(
    *,
    aoi_geojson: dict,
    grid: GridSpec,
    target_time_utc: dt.datetime,
    datasets_cfg: Mapping[str, object] | None = None,
) -> tuple[dict[str, np.ndarray], dict[str, dict[str, object]]]:
    cfg = dict(datasets_cfg or load_datasets_config().get("cmems", {}))
    if not cfg:
        raise OceanDataError("No Copernicus datasets were configured in backend/config/datasets.json")

    bbox = bbox_from_geojson(aoi_geojson)
    raw_output: dict[str, np.ndarray] = {}
    provenance: dict[str, dict[str, object]] = {}

    for logical_name in ("sst", "currents", "ssh", "waves", "chl"):
        item = cfg.get(logical_name)
        if not item:
            raise OceanDataError(f"Missing dataset config for required layer '{logical_name}'")
        if not isinstance(item, Mapping):
            raise OceanDataError(f"Dataset config for '{logical_name}' must be a mapping")
        loaded, layer_provenance = _fetch_layer_from_candidates(
            logical_name=logical_name,
            item=item,
            bbox=bbox,
            target_time_utc=target_time_utc,
            grid=grid,
        )
        raw_output.update(loaded)
        provenance[logical_name] = layer_provenance

    if "uo" not in raw_output or "vo" not in raw_output:
        raise OceanDataError("Current components uo/vo were not loaded from the configured currents dataset")

    missing = [k for k in ("thetao", "chl", "zos", "VHM0") if k not in raw_output]
    if missing:
        raise OceanDataError(f"Missing required variables after dataset loading: {missing}")

    derived = {
        "sst": np.asarray(raw_output["thetao"], dtype=np.float32),
        "chl": np.asarray(raw_output["chl"], dtype=np.float32),
        "ssh": np.asarray(raw_output["zos"], dtype=np.float32),
        "waves": np.asarray(raw_output["VHM0"], dtype=np.float32),
        "u": np.asarray(raw_output["uo"], dtype=np.float32),
        "v": np.asarray(raw_output["vo"], dtype=np.float32),
    }
    derived["current_speed"] = np.sqrt(np.square(derived["u"]) + np.square(derived["v"])).astype(np.float32)
    return derived, provenance
