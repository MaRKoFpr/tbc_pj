import geopandas as gpd
import pandas as pd
from pathlib import Path

# ---------------- НАСТРОЙКИ ----------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
ZONES_FILE = PROCESSED_DIR / "zones_primary.geojson"
FEATURES_FILE = PROJECT_ROOT / "data" / "osm" / "features.geojson"
ZONE_COLUMN = "zone_id"
DEFAULT_LEVELS = 5        # этажность, если в OSM её нет
POINT_FLOOR_M2 = 0        # условная площадь точки-магазина/офиса; 0 = не добавлять в долю


RES = ["apartments", "residential", "house", "detached", "semidetached_house",
       "terrace", "dormitory", "bungalow"]
WORK = ["office", "commercial", "retail", "industrial", "warehouse", "supermarket",
        "school", "university", "college", "kindergarten", "hospital", "civic",
        "public", "government", "manufacture", "hotel"]


JOB_AMENITY = ["school", "university", "college", "kindergarten", "hospital", "clinic",
               "dentist", "doctors", "pharmacy", "cafe", "restaurant", "fast_food",
               "bank", "post_office", "townhall", "library", "cinema", "fuel"]

IGNORE = ["garages", "garage", "roof", "shed", "service", "kiosk", "hut", "cabin",
          "carport", "construction", "transformer_tower"]


zones = gpd.read_file(ZONES_FILE)
feats = gpd.read_file(FEATURES_FILE).reset_index(drop=True)

zones = zones[[ZONE_COLUMN, "geometry"]].rename(columns={ZONE_COLUMN: "zone"})
zones["zone"] = zones["zone"].astype(str)

for tag in ["building", "amenity", "office", "shop", "building:levels"]:
    if tag not in feats.columns:
        feats[tag] = None
feats = feats[["building", "amenity", "office", "shop", "building:levels", "geometry"]]

metric_crs = zones.estimate_utm_crs()
zones = zones.to_crs(metric_crs)
feats = feats.to_crs(metric_crs)


buildings = feats[feats.geom_type.isin(["Polygon", "MultiPolygon"])].copy()

buildings["kind"] = None
is_work = (buildings["building"].isin(WORK) | buildings["amenity"].isin(JOB_AMENITY)
           | buildings["office"].notna() | buildings["shop"].notna())
buildings.loc[is_work, "kind"] = "work"
buildings.loc[buildings["building"].isin(RES), "kind"] = "res"


levels = pd.to_numeric(buildings["building:levels"], errors="coerce")
print("Зданий с известной этажностью:", int(levels.notna().sum()), "из", len(buildings))
buildings["floor_m2"] = buildings.geometry.area * levels.fillna(DEFAULT_LEVELS)

unclassified = buildings[buildings["kind"].isna() & buildings["building"].notna()
                         & ~buildings["building"].isin(IGNORE)]

def to_zone(gdf):
    gdf = gdf.copy()
    gdf.geometry = gdf.geometry.representative_point()
    return gpd.sjoin(gdf, zones, predicate="within", how="inner")

counted = to_zone(buildings[buildings["kind"].notna()])
unclassified = to_zone(unclassified) if len(unclassified) else unclassified

# ---------------- 3. Точки (магазины, офисы) ----------------
points = feats[feats.geom_type == "Point"]
is_job = points["shop"].notna() | points["office"].notna() | points["amenity"].isin(JOB_AMENITY)
job_points = to_zone(points[is_job])


# ---------------- 4. Собираем таблицу по зонам ----------------
table = pd.DataFrame(index=zones["zone"])
floor = counted.pivot_table(index="zone", columns="kind", values="floor_m2",
                            aggfunc="sum").reindex(table.index).fillna(0)
count = counted.groupby(["zone", "kind"]).size().unstack(fill_value=0).reindex(table.index).fillna(0)

table["res_buildings"] = count.get("res", 0)
table["res_floor_m2"] = floor.get("res", 0)
table["work_buildings"] = count.get("work", 0)
table["work_floor_m2"] = floor.get("work", 0)
table["work_points"] = job_points.groupby("zone").size().reindex(table.index).fillna(0)
table["unclassified_buildings"] = (unclassified.groupby("zone").size().reindex(table.index).fillna(0)
                                   if len(unclassified) else 0)

# Доли зоны от общего (для спроса)
work_total = table["work_floor_m2"] + table["work_points"] * POINT_FLOOR_M2
table["res_share"] = table["res_floor_m2"] / table["res_floor_m2"].sum()
table["work_share"] = work_total / work_total.sum()

table = table[["res_buildings", "res_floor_m2", "res_share", "work_buildings",
               "work_floor_m2", "work_points", "work_share", "unclassified_buildings"]]
table = table.round({"res_floor_m2": 0, "work_floor_m2": 0, "res_share": 4, "work_share": 4})
table.index.name = "zone"
table.to_csv(PROCESSED_DIR / "zone_stats.csv")
print(table)
