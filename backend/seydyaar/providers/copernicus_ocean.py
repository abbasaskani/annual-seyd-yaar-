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


def _require_modules():
    try:
        import copernicusmarine  # type: ignore
        import xarray as xr  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on runtime env
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
    return _coord_name(("depth", "deptho", "lev", "z"), list(ds.coords) + list(ds.dims))


def _normalize_longitudes(lons: np.ndarray, target_lons: np.ndarray) -> np.ndarray:
    arr = np.asarray(lons, dtype=np.float64).copy()
    tgt_min = float(np.nanmin(target_lons))
    tgt_max = float(np.nanmax(target_lons))
    if tgt_min >= 0.0 and np.nanmin(arr) < 0.0:
        arr = np.where(arr < 0.0, arr + 360.0, arr)
    elif tgt_max <= 180.0 and np.nanmax(arr) > 180.0:
        arr = np.where(arr > 180.0, arr - 360.0, arr)
    return arr


def _nearest_index_1d(sorted_values: np.ndarray, targets: np.ndarray) -> np.ndarray:
    if sorted_values.ndim != 1:
        raise OceanDataError("Nearest-index helper expects 1D coordinates")
    if sorted_values.size == 1:
        return np.zeros_like(targets, dtype=np.int64)
    mids = (sorted_values[:-1] + sorted_values[1:]) / 2.0
    return np.searchsorted(mids, targets).astype(np.int64)


def _resample_2d_nearest(values: np.ndarray, src_lats: np.ndarray, src_lons: np.ndarray, grid: GridSpec) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float32)
    lats = np.asarray(src_lats, dtype=np.float64)
    lons = np.asarray(src_lons, dtype=np.float64)

    lon2d, lat2d = grid.lonlat_mesh()
    lons = _normalize_longitudes(lons, lon2d)

    lon_order = np.argsort(lons)
    lat_order = np.argsort(lats)
    lons = lons[lon_order]
    lats = lats[lat_order]
    arr = arr[np.ix_(lat_order, lon_order)]

    lat_idx = np.clip(_nearest_index_1d(lats, lat2d), 0, len(lats) - 1)
    lon_idx = np.clip(_nearest_index_1d(lons, lon2d), 0, len(lons) - 1)
    out = arr[lat_idx, lon_idx]
    return np.asarray(out, dtype=np.float32)


def _select_variable_2d(ds, variable_name: str, *, target_time_utc: dt.datetime, depth_target_m: float | None):
    if variable_name not in ds.data_vars:
        raise OceanDataError(f"Variable '{variable_name}' not found in dataset vars={list(ds.data_vars)}")

    da = ds[variable_name]
    actual_time_utc: str | None = None
    actual_depth_m: float | None = None

    time_name = _time_coord_name(da)
    if time_name and time_name in da.coords and da.sizes.get(time_name, 0) > 0:
        picked = da.sel({time_name: np.datetime64(target_time_utc.astimezone(dt.timezone.utc).replace(tzinfo=None))}, method="nearest")
        try:
            actual_np = picked.coords[time_name].values
            actual_time = dt.datetime.fromisoformat(np.datetime_as_string(actual_np, timezone="UTC").replace("Z", "+00:00"))
            actual_time_utc = _format_iso_z(actual_time)
        except Exception:
            actual_time_utc = None
        da = picked

    depth_name = _depth_coord_name(da)
    if depth_name and depth_name in da.coords and da.sizes.get(depth_name, 0) > 0 and depth_target_m is not None:
        picked = da.sel({depth_name: float(depth_target_m)}, method="nearest")
        try:
            actual_depth_m = float(np.asarray(picked.coords[depth_name].values).item())
        except Exception:
            actual_depth_m = None
        da = picked

    da = da.squeeze(drop=True)
    lat_name = _lat_coord_name(da)
    lon_name = _lon_coord_name(da)
    non_spatial_dims = [d for d in da.dims if d not in {lat_name, lon_name}]
    for d in list(non_spatial_dims):
        if da.sizes.get(d, 0) == 1:
            da = da.isel({d: 0}, drop=True)
    non_spatial_dims = [d for d in da.dims if d not in {lat_name, lon_name}]
    if non_spatial_dims:
        raise OceanDataError(
            f"Variable '{variable_name}' still has unexpected non-spatial dims {non_spatial_dims}; "
            "refine dataset-specific selection before running."
        )

    da = da.transpose(lat_name, lon_name)
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
        except Exception as exc:  # pragma: no cover - network/runtime dependent
            last_exc = exc
            if path.exists() and path.stat().st_size == 0:
                try:
                    path.unlink()
                except Exception:
                    pass
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
    output: dict[str, np.ndarray] = {}
    provenance: dict[str, dict[str, object]] = {}

    for logical_name in ("sst", "currents", "ssh", "waves", "chl"):
        item = cfg.get(logical_name)
        if not item:
            raise OceanDataError(f"Missing dataset config for required layer '{logical_name}'")
        if not isinstance(item, Mapping):
            raise OceanDataError(f"Dataset config for '{logical_name}' must be a mapping")

        variables = list(item.get("variables") or ([item["variable"]] if item.get("variable") else []))
        if not variables:
            raise OceanDataError(f"Dataset config for '{logical_name}' has no variable(s)")

        dataset_id = str(item["dataset_id"])
        depth_target_m = item.get("depth_target_m")
        cache_path, cache_hit, retry_attempts_used = _download_subset_to_cache(
            dataset_id=dataset_id,
            variables=variables,
            bbox=bbox,
            target_time_utc=target_time_utc,
            depth_target_m=float(depth_target_m) if depth_target_m is not None else None,
        )
        ds = _open_cached_dataset(cache_path)

        used_time: str | None = None
        used_depth: float | None = None
        for var_name in variables:
            da, lat_name, lon_name, actual_time_utc, actual_depth_m = _select_variable_2d(
                ds,
                var_name,
                target_time_utc=target_time_utc,
                depth_target_m=float(depth_target_m) if depth_target_m is not None else None,
            )
            resampled = _resample_2d_nearest(
                np.asarray(da.values, dtype=np.float32),
                np.asarray(da[lat_name].values, dtype=np.float64),
                np.asarray(da[lon_name].values, dtype=np.float64),
                grid,
            )
            output[var_name] = resampled
            if actual_time_utc:
                used_time = actual_time_utc
            if actual_depth_m is not None:
                used_depth = actual_depth_m

        try:
            ds.close()
        except Exception:
            pass

        provenance[logical_name] = {
            "dataset_id": dataset_id,
            "variables": variables,
            "actual_time_utc": used_time,
            "actual_depth_m": used_depth,
            "cache_path": str(cache_path).replace("\\", "/"),
            "cache_hit": bool(cache_hit),
            "retry_attempts_used": int(retry_attempts_used),
        }

    if "uo" not in output or "vo" not in output:
        raise OceanDataError("Current components uo/vo were not loaded from the configured currents dataset")

    derived = {
        "sst": np.asarray(output["thetao"], dtype=np.float32),
        "chl": np.asarray(output["chl"], dtype=np.float32),
        "ssh": np.asarray(output["zos"], dtype=np.float32),
        "waves": np.asarray(output["VHM0"], dtype=np.float32),
        "u": np.asarray(output["uo"], dtype=np.float32),
        "v": np.asarray(output["vo"], dtype=np.float32),
    }
    derived["current_speed"] = np.sqrt(np.square(derived["u"]) + np.square(derived["v"])).astype(np.float32)
    return derived, provenance
