"""Build the manual zoning and two alternatives for the interactive map."""

import json

import osmnx as ox
from shapely.geometry import Point, Polygon, mapping, shape
from shapely.ops import unary_union

from ..road_network.download_osm import INPUT_DIR, OSM_DIR, PROCESSED_DIR, read_boundary


def feature(zone_id, name, geometry):
    if geometry.is_empty or not geometry.is_valid:
        raise ValueError(f"Invalid geometry for {zone_id}")
    return {
        "type": "Feature",
        "properties": {"zone_id": zone_id, "name": name, "status": "draft"},
        "geometry": mapping(geometry),
    }


def collection(features):
    return {"type": "FeatureCollection", "features": features}


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    path = PROCESSED_DIR / "zones_manual.geojson"
    original = json.loads(path.read_text(encoding="utf-8-sig"))["features"]
    zones = {f["properties"]["zone_id"]: f for f in original if f["properties"]["zone_id"] != "Z08"}
    geometries = {key: shape(f["geometry"]) for key, f in zones.items()}
    boundary = read_boundary(INPUT_DIR / "area.csv").iloc[0]
    places = ox.features.features_from_xml(
        OSM_DIR / "dolgoprudny_wide.osm",
        tags={"name": "Московский физико-технический институт"},
    )
    campus = places.loc[("relation", 4262145)].geometry.intersection(boundary)
    # User sketch: include the strip between Pervomayskaya and the railway,
    # and meet the southern project boundary along the shosse.
    campus_extension = Polygon([
        (37.5159, 55.92760), (37.5173846, 55.9272923),
        (37.5195341, 55.9270865), (37.5221695, 55.92685),
        (37.5234656, 55.926152), (37.5246379, 55.9238735),
        (37.5253946, 55.9224231), (37.5261336, 55.9209705),
        (37.5269, 55.9195), (37.535, 55.9195), (37.535, 55.934),
        (37.5209271, 55.9312941), (37.5168415, 55.9306448),
        (37.5159, 55.9304),
    ])
    exclusions = unary_union([g for key, g in geometries.items() if key != "Z04"])
    mipt = campus.union(campus_extension).intersection(boundary).difference(exclusions)
    if mipt.geom_type != "Polygon" or campus.difference(mipt).area > 1e-12:
        raise ValueError("Expected a connected Z08 containing the main campus")
    geometries["Z04"] = geometries["Z04"].difference(mipt)

    zones["Z08"] = feature("Z08", "МФТИ и студгородок", mipt)
    geometries["Z08"] = mipt
    # Close the strip west of MIPT, between the mapped campus and the Z04 edge.
    remaining = boundary.difference(unary_union(list(geometries.values())))
    gap_location = Point(37.515606, 55.930482)
    gaps = [remaining] if remaining.geom_type == "Polygon" else list(remaining.geoms)
    industrial_gap = next((gap for gap in gaps if gap.covers(gap_location)), None)
    if industrial_gap is not None:
        geometries["Z04"] = geometries["Z04"].union(industrial_gap)
    zones["Z04"] = feature("Z04", zones["Z04"]["properties"]["name"], geometries["Z04"])

    grouped = dict(zones)
    grouped["Z02"] = feature("Z02", "Парк и Центральный с лицеем",
                             geometries["Z02"].union(geometries["Z03"]))
    del grouped["Z03"]

    detailed = dict(zones)
    # The segment of Prospekt Raketostroiteley bordering the lyceum.
    lyceum_side = Polygon([
        (37.4967842, 55.9310202), (37.4985332, 55.9313653),
        (37.4999864, 55.9316531), (37.5015402, 55.931961),
        (37.502, 55.928), (37.4965, 55.928),
    ])
    lyceum = geometries["Z03"].intersection(lyceum_side)
    detailed["Z03"] = feature("Z03", "Центральный", geometries["Z03"].difference(lyceum))
    detailed["Z09"] = feature("Z09", "Физтех-лицей Капицы", lyceum)

    # A longitudinal cut along Pervomayskaya Street, extended north to the rail corridor.
    east_of_street = Polygon([
        (37.5225593, 55.927975), (37.5209271, 55.9312941),
        (37.5195927, 55.9341196), (37.518226, 55.9368396),
        (37.516, 55.9413), (37.55, 55.9413), (37.55, 55.927975),
    ])
    east = geometries["Z07"].intersection(east_of_street)
    detailed["Z07"] = feature("Z07", "Жилые кварталы западнее Первомайской",
                              geometries["Z07"].difference(east))
    detailed["Z10"] = feature("Z10", "Жилые кварталы между Первомайской и железной дорогой", east)

    covered = unary_union(list(geometries.values()))
    if boundary.difference(covered).area > 1e-12:
        raise ValueError("Unassigned area remains between zones")
    variants = {}
    for key, layout in [("manual", zones), ("grouped", grouped), ("detailed", detailed)]:
        polygons = [shape(f["geometry"]) for f in layout.values()]
        union = unary_union(polygons)
        if (union.symmetric_difference(covered).area > 1e-12
                or abs(sum(g.area for g in polygons) - union.area) > 1e-12
                or union.difference(boundary).area > 1e-12):
            raise ValueError(f"Invalid coverage or overlap in {key}")
        variants[key] = collection(list(layout.values()))
        print(f"{key}: {len(polygons)} zones")
    save(path, variants["manual"])
    save(PROCESSED_DIR / "zoning_variants.json", variants)
    save(PROCESSED_DIR / "zones_primary.geojson", variants["grouped"])


if __name__ == "__main__":
    main()
