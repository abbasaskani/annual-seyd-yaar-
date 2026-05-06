from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Sequence

import numpy as np

from ..providers.copernicus_ocean import (
    OceanDataError,
    fetch_environment_fields,
    load_datasets_config,
    resolve_reference_grid,
)
from ..utils_geo import GridSpec, bbox_from_geojson, mask_from_geojson
from ..utils_time import iso_from_time_id
from .io import minify_json_for_web, write_bin_f32, write_bin_u8, write_json


@dataclass
class RunContext:
    out_root: Path
    run_id: str
    variant: str
    aoi_geojson: dict
    species_profiles: dict
    time_source: str
    temporal_spec: dict


def _safe_log10(arr: np.ndarray, eps: float = 1e-6) -> np.ndarray:
    return np.log10(np.maximum(np.asarray(arr, dtype=np.float32), eps))


def _gaussian_score(values: np.ndarray, mean: float, sigma: float) -> np.ndarray:
    sigma = max(float(sigma), 1e-6)
    z = (np.asarray(values, dtype=np.float32) - float(mean)) / sigma
    return np.exp(-0.5 * np.square(z)).astype(np.float32)


def _upper_soft_constraint(values: np.ndarray, soft_max: float) -> np.ndarray:
    values = np.asarray(values, dtype=np.float32)
    if soft_max <= 0:
        return np.zeros_like(values, dtype=np.float32)
    exceed = np.maximum(values - float(soft_max), 0.0)
    score = np.exp(-(exceed / max(float(soft_max), 1e-6)))
    return score.astype(np.float32)


def _gradient_front(arr: np.ndarray) -> np.ndarray:
    arr = np.asarray(arr, dtype=np.float32)
    gy, gx = np.gradient(arr)
    mag = np.sqrt(np.square(gx) + np.square(gy))
    return mag.astype(np.float32)


def _robust_unit(arr: np.ndarray, mask: np.ndarray) -> np.ndarray:
    arr = np.asarray(arr, dtype=np.float32)
    valid = arr[(mask > 0) & np.isfinite(arr)]
    if valid.size == 0:
        return np.zeros_like(arr, dtype=np.float32)
    lo = float(np.nanpercentile(valid, 5))
    hi = float(np.nanpercentile(valid, 95))
    if hi <= lo:
        return np.zeros_like(arr, dtype=np.float32)
    scaled = (arr - lo) / (hi - lo)
    return np.clip(scaled, 0.0, 1.0).astype(np.float32)


def _weighted_mean(named_arrays: Dict[str, np.ndarray], weights: Dict[str, float]) -> np.ndarray:
    total_w = 0.0
    acc = None
    for key, w in weights.items():
        if key not in named_arrays:
            continue
        w = float(w)
        if w <= 0:
            continue
        arr = np.asarray(named_arrays[key], dtype=np.float32)
        acc = arr * w if acc is None else acc + arr * w
        total_w += w
    if acc is None or total_w <= 0:
        raise OceanDataError(f"Cannot build weighted mean; no valid arrays/weights found for keys={list(weights.keys())}")
    return np.clip(acc / total_w, 0.0, 1.0).astype(np.float32)


def _compute_species_layers(env: Dict[str, np.ndarray], species_cfg: dict, mask: np.ndarray) -> tuple[dict[str, np.ndarray], dict[str, object]]:
    priors = species_cfg.get("priors", {})
    layer_weights = species_cfg.get("layer_weights", {})
    ops_cfg = species_cfg.get("ops_constraints", {})
    front_weights = priors.get("front_weights", {})

    temp_score = _gaussian_score(env["sst"], priors["sst_opt_c"], priors["sst_sigma_c"])
    chl_score = _gaussian_score(_safe_log10(env["chl"]), np.log10(max(float(priors["chl_opt_mg_m3"]), 1e-6)), priors["chl_sigma_log10"])
    current_score = _gaussian_score(env["current_speed"], priors["current_opt_m_s"], priors["current_sigma_m_s"])
    waves_score = _upper_soft_constraint(env["waves"], priors["waves_hs_soft_max_m"])

    temp_front = _robust_unit(_gradient_front(env["sst"]), mask)
    chl_front = _robust_unit(_gradient_front(_safe_log10(env["chl"])), mask)
    ssh_front = _robust_unit(_gradient_front(env["ssh"]), mask)
    front = _weighted_mean(
        {"temp": temp_front, "chl": chl_front, "ssh": ssh_front},
        {k: float(v) for k, v in front_weights.items()},
    )

    phab = _weighted_mean(
        {
            "temp": temp_score,
            "chl": chl_score,
            "front": front,
            "current": current_score,
            "waves": waves_score,
        },
        {k: float(v) for k, v in layer_weights.items()},
    )
    phab = np.where(mask > 0, phab, np.nan).astype(np.float32)

    ops_soft = np.minimum(
        _upper_soft_constraint(env["waves"], ops_cfg["waves_hs_soft_max_m"]),
        _upper_soft_constraint(env["current_speed"], ops_cfg["currents_soft_max_m_s"]),
    ).astype(np.float32)
    ops = ((ops_soft >= 0.5) & (mask > 0)).astype(np.uint8)

    pcatch = np.where(mask > 0, phab, np.nan).astype(np.float32)

    diagnostics = {
        "pcatch_mode": "habitat_proxy_equals_phab_until_effort_or_catch_proxy_is_integrated",
    }
    return {"phab": phab, "pcatch": pcatch, "ops": ops}, diagnostics


def _species_time_base(run_root: Path, variant: str, species_name: str, time_id: str) -> Path:
    return run_root / "variants" / variant / "species" / species_name / "times" / time_id


def _all_outputs_exist(run_root: Path, variant: str, species_names: Sequence[str], time_id: str) -> bool:
    required = ("phab.f32", "pcatch.f32", "ops.u8", "mask.u8", "diagnostics.json")
    for species_name in species_names:
        base = _species_time_base(run_root, variant, species_name, time_id)
        if not all((base / name).exists() for name in required):
            return False
    return True


def _read_existing_provenance(run_root: Path, variant: str, species_names: Sequence[str], time_id: str) -> dict | None:
    if not species_names:
        return None
    diag_path = _species_time_base(run_root, variant, species_names[0], time_id) / "diagnostics.json"
    if not diag_path.exists():
        return None
    try:
        payload = json.loads(diag_path.read_text(encoding="utf-8"))
        return payload.get("datasets")
    except Exception:
        return None


def build_and_write_bundle(ctx: RunContext, time_ids: Sequence[str]) -> str:
    bbox = bbox_from_geojson(ctx.aoi_geojson)
    datasets_cfg = load_datasets_config().get("cmems", {})

    reference_time_iso = iso_from_time_id(time_ids[0]) if time_ids else datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    reference_time_dt = datetime.fromisoformat(reference_time_iso.replace("Z", "+00:00"))
    grid, grid_source = resolve_reference_grid(
        aoi_geojson=ctx.aoi_geojson,
        target_time_utc=reference_time_dt,
        datasets_cfg=datasets_cfg,
    )
    width, height = grid.width, grid.height
    mask = mask_from_geojson(ctx.aoi_geojson, grid)

    run_root = ctx.out_root / "runs" / ctx.run_id
    run_root.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    species_names = list(ctx.species_profiles.keys())
    source_log: List[dict] = []
    per_species_time_entries: dict[str, list[dict]] = {species_name: [] for species_name in species_names}
    skipped_existing_time_ids: list[str] = []

    for time_id in time_ids:
        target_iso = iso_from_time_id(time_id)
        provenance = None
        if _all_outputs_exist(run_root, ctx.variant, species_names, time_id):
            skipped_existing_time_ids.append(time_id)
            provenance = _read_existing_provenance(run_root, ctx.variant, species_names, time_id)
        else:
            target_dt = datetime.fromisoformat(target_iso.replace("Z", "+00:00"))
            env, provenance = fetch_environment_fields(
                aoi_geojson=ctx.aoi_geojson,
                grid=grid,
                target_time_utc=target_dt,
                datasets_cfg=datasets_cfg,
            )
            for species_name in species_names:
                base = _species_time_base(run_root, ctx.variant, species_name, time_id)
                required = [base / "phab.f32", base / "pcatch.f32", base / "ops.u8", base / "mask.u8", base / "diagnostics.json"]
                if all(p.exists() for p in required):
                    continue
                layers, diagnostics = _compute_species_layers(env, ctx.species_profiles[species_name], mask)
                write_bin_f32(base / "phab.f32", layers["phab"])
                write_bin_f32(base / "pcatch.f32", layers["pcatch"])
                write_bin_u8(base / "ops.u8", layers["ops"])
                write_bin_u8(base / "mask.u8", mask)
                diag_meta = {
                    "requested_time_id": time_id,
                    "requested_time_utc": target_iso,
                    "datasets": provenance,
                    "pcatch_mode": diagnostics["pcatch_mode"],
                }
                diag_path = base / "diagnostics.json"
                write_json(diag_path, diag_meta)
                minify_json_for_web(diag_path)

        source_log.append({
            "requested_time_id": time_id,
            "requested_time_utc": target_iso,
            "datasets": provenance,
            "reused_existing_outputs": time_id in skipped_existing_time_ids,
        })
        for species_name in species_names:
            per_species_time_entries[species_name].append({
                "time_id": time_id,
                "time_utc": target_iso,
                "selected_dataset_times": {k: v.get("actual_time_utc") for k, v in (provenance or {}).items()},
            })

    for species_name in species_names:
        species_meta = {
            "species": species_name,
            "label": ctx.species_profiles[species_name].get("label", {}),
            "scientific_name": ctx.species_profiles[species_name].get("scientific_name"),
            "time_ids": list(time_ids),
            "times": [iso_from_time_id(t) for t in time_ids],
            "paths": {
                "per_time": {
                    "phab": f"runs/{ctx.run_id}/variants/{ctx.variant}/species/{species_name}/times/{{time_id}}/phab.f32",
                    "pcatch": f"runs/{ctx.run_id}/variants/{ctx.variant}/species/{species_name}/times/{{time_id}}/pcatch.f32",
                    "ops": f"runs/{ctx.run_id}/variants/{ctx.variant}/species/{species_name}/times/{{time_id}}/ops.u8",
                    "mask": f"runs/{ctx.run_id}/variants/{ctx.variant}/species/{species_name}/times/{{time_id}}/mask.u8",
                    "diagnostics": f"runs/{ctx.run_id}/variants/{ctx.variant}/species/{species_name}/times/{{time_id}}/diagnostics.json",
                }
            },
            "time_entries": per_species_time_entries[species_name],
            "grid": {"width": width, "height": height, "crs": grid.crs, "source": grid_source},
        }
        meta_path = run_root / "variants" / ctx.variant / "species" / species_name / "meta.json"
        write_json(meta_path, species_meta)
        minify_json_for_web(meta_path)

    run_meta = {
        "run_id": ctx.run_id,
        "generated_at_utc": generated_at,
        "time_source": ctx.time_source,
        "times": [iso_from_time_id(t) for t in time_ids],
        "time_ids": list(time_ids),
        "variant": ctx.variant,
        "species": species_names,
        "bbox": list(bbox),
        "grid": {"width": width, "height": height, "crs": grid.crs, "source": grid_source},
        "aoi": ctx.aoi_geojson,
        "temporal_spec": ctx.temporal_spec,
        "data_mode": "real_copernicus_environmental_layers",
        "datasets": datasets_cfg,
        "sources_by_time": source_log,
        "reused_existing_time_ids": skipped_existing_time_ids,
    }
    run_meta_path = run_root / "meta.json"
    write_json(run_meta_path, run_meta)
    minify_json_for_web(run_meta_path)

    run_entry = {
        "run_id": ctx.run_id,
        "variant": ctx.variant,
        "species": species_names,
        "models": ["phab", "pcatch", "ops"],
        "time_ids": list(time_ids),
        "run_path": f"runs/{ctx.run_id}",
    }
    index = {"version": 1, "generated_at_utc": generated_at, "runs": [run_entry]}
    idx_out = ctx.out_root / "index.json"
    write_json(idx_out, index)
    minify_json_for_web(idx_out)

    meta = {
        "version": 1,
        "generated_at_utc": generated_at,
        "run_id": ctx.run_id,
        "variant": ctx.variant,
        "time_source": ctx.time_source,
        "latest_available_time_id": list(time_ids)[-1] if time_ids else None,
        "grid": {"width": width, "height": height, "crs": grid.crs, "source": grid_source},
        "bbox": list(bbox),
        "aoi": ctx.aoi_geojson,
        "species": species_names,
        "models": ["phab", "pcatch", "ops"],
        "available_time_ids": list(time_ids),
        "run_path": f"runs/{ctx.run_id}",
        "temporal_spec": ctx.temporal_spec,
        "data_mode": "real_copernicus_environmental_layers",
    }
    meta_out = ctx.out_root / "meta.json"
    write_json(meta_out, meta)
    minify_json_for_web(meta_out)
    return ctx.run_id
