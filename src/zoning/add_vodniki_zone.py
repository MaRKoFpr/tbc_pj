"""Add the provisional Vodniki polygon to the manual zoning layer."""

import json
from xml.etree import ElementTree as ET

from shapely.geometry import LineString, Point, mapping, shape
from shapely.ops import linemerge, split

from ..road_network.download_osm import INPUT_DIR, OSM_DIR, PROCESSED_DIR, read_boundary


def main():
    path = PROCESSED_DIR / "zones_manual.geojson"
    saved = json.loads(path.read_text(encoding="utf-8-sig"))
    features = saved["features"] if saved["type"] == "FeatureCollection" else [saved]
    features = [feature for feature in features if feature["properties"]["zone_id"] != "Z01"]
    boundary = read_boundary(INPUT_DIR / "area.csv").iloc[0]
    snapshot = ET.parse(OSM_DIR / "dolgoprudny_wide.osm").getroot()
    nodes = {node.get("id"): (float(node.get("lon")), float(node.get("lat")))
             for node in snapshot.iterfind("node")}
    river = linemerge([
        LineString([nodes[ref.get("ref")] for ref in snapshot.find(f"way[@id='{way_id}']").iterfind("nd")])
        for way_id in ("230316821", "128176139", "106893925")
    ])
    vodniki_label = Point(*nodes["4864443263"])
    pieces = [part for part in split(boundary, river).geoms if part.covers(vodniki_label)]
    if len(pieces) != 1 or pieces[0].equals(boundary):
        raise ValueError("The Klyazma must separate Vodniki from the rest of the project")
    vodniki = pieces[0]
    print(f"Z01 share of the project area: {vodniki.area / boundary.area:.1%}")
    if any(vodniki.intersection(shape(feature["geometry"])).area > 1e-12 for feature in features):
        raise ValueError("Z01 overlaps an existing zone")
    features.insert(0, {
        "type": "Feature",
        "properties": {"zone_id": "Z01", "name": "Водники · граница по Клязьме", "status": "draft"},
        "geometry": mapping(vodniki),
    })
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features},
                               ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
