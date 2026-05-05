from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Tuple

import numpy as np
from shapely.geometry import Point, shape
from shapely.ops import unary_union
from shapely.prepared import prep


@dataclass(frozen=True)
class GridSpec:
    lon_min: float
    lon_max: float
    lat_min: float
    lat_max: float
    width: int
    height: int
    crs: str = "EPSG:4326"

    @property
    def dx(self) -> float:
        return (self.lon_max - self.lon_min) / max(self.width - 1, 1)

    @property
    def dy(self) -> float:
        return (self.lat_max - self.lat_min) / max(self.height - 1, 1)

    def lonlat_mesh(self) -> Tuple[np.ndarray, np.ndarray]:
        lons = np.linspace(self.lon_min, self.lon_max, self.width, dtype=np.float32)
        lats = np.linspace(self.lat_max, self.lat_min, self.height, dtype=np.float32)
        return np.meshgrid(lons, lats)


def _all_geometries(aoi_geojson: dict) -> Iterable[object]:
    for feature in aoi_geojson.get("features", []):
        geom = feature.get("geometry")
        if geom:
            yield shape(geom)


def unified_geometry(aoi_geojson: dict):
    geoms = list(_all_geometries(aoi_geojson))
    if not geoms:
        raise ValueError("AOI GeoJSON has no usable geometries")
    return unary_union(geoms)


def bbox_from_geojson(aoi_geojson: dict) -> Tuple[float, float, float, float]:
    minx, miny, maxx, maxy = unified_geometry(aoi_geojson).bounds
    return float(minx), float(miny), float(maxx), float(maxy)


def mask_from_geojson(aoi_geojson: dict, grid: GridSpec) -> np.ndarray:
    geom = unified_geometry(aoi_geojson)
    pg = prep(geom)
    lon2d, lat2d = grid.lonlat_mesh()
    h, w = lon2d.shape
    mask = np.zeros((h, w), dtype=np.uint8)
    for i in range(h):
        for j in range(w):
            pt = Point(float(lon2d[i, j]), float(lat2d[i, j]))
            if pg.covers(pt):
                mask[i, j] = 1
    return mask
