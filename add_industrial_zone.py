"""Add the southern industrial zone, excluding the mapped MIPT campus."""

import json

import osmnx as ox
from shapely.geometry import Polygon, mapping, shape
from shapely.ops import unary_union

from download_osm import ROOT, read_boundary


def main():
    path = ROOT / "zones_manual.geojson"
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    features = [f for f in data["features"] if f["properties"]["zone_id"] != "Z04"]
    central = next(f for f in features if f["properties"]["zone_id"] == "Z03")
    ring = central["geometry"]["coordinates"][0]
    # Follow the sketch via Tsiolkovskogo Street and the west and south sides of MIPT.
    north = ring[8:20][::-1]
    outline = Polygon([
        *north, (37.5149211, 55.9343959),
        (37.5154096, 55.9334538), (37.5168415, 55.9306448),
        (37.5149417, 55.9305599), (37.5150456, 55.93019),
        (37.5155748, 55.9290729), (37.5162444, 55.9277272),
        (37.5173846, 55.9272923), (37.5174742, 55.9272723),
        (37.5195341, 55.9270865), (37.5210595, 55.9271775),
        (37.5225987, 55.926929), (37.52309, 55.92691),
        (37.5234656, 55.926152),
        (37.5246379, 55.9238735), (37.5253946, 55.9224231),
        (37.5261336, 55.9209705), (37.5269, 55.9195),
        (37.4944, 55.916), (37.4942, 55.91884), (37.49297, 55.92671),
    ])
    if not outline.is_valid:
        raise ValueError("Invalid Z04 draft outline")
    boundary = read_boundary(ROOT / "area.csv").iloc[0]
    places = ox.features.features_from_xml(
        ROOT / "osm_data" / "dolgoprudny_wide.osm",
        tags={"name": ["Московский физико-технический институт",
                       "Долгопрудненское научно-производственное предприятие"]},
    )
    campus = places.loc[("relation", 4262145)].geometry
    industrial = places.loc[("relation", 4262143)].geometry
    exclusions = unary_union([campus, *(shape(f["geometry"]) for f in features)])
    zone = outline.intersection(boundary).difference(exclusions)
    if not zone.is_valid or zone.geom_type != "Polygon":
        raise ValueError(f"Expected one valid Z04 polygon, got {zone.geom_type}")
    if zone.intersection(exclusions).area > 1e-12 or zone.difference(boundary).area > 1e-12:
        raise ValueError("Z04 overlaps an exclusion or extends outside the study area")
    print(f"NPP site covered: {zone.intersection(industrial).area / industrial.area:.1%}")
    features.append({
        "type": "Feature",
        "properties": {"zone_id": "Z04", "name": "Промзона Долгопрудненского НПП", "status": "draft"},
        "geometry": mapping(zone),
    })
    data["features"] = features
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
