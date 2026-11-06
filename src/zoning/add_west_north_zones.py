"""Add the provisional western and northern zones to the manual zoning layer."""

import json

from shapely.geometry import Polygon, mapping, shape
from shapely.ops import unary_union

from ..road_network.download_osm import INPUT_DIR, PROCESSED_DIR, read_boundary


def main():
    path = PROCESSED_DIR / "zones_manual.geojson"
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    features = [f for f in data["features"] if f["properties"]["zone_id"] not in {"Z05", "Z06", "Z07"}]
    park = next(f for f in features if f["properties"]["zone_id"] == "Z02")
    park_ring = park["geometry"]["coordinates"][0]
    northwest = park_ring[0]
    northern_edge = park_ring[:6]
    boundary = read_boundary(INPUT_DIR / "area.csv").iloc[0]
    remaining = boundary.difference(unary_union([shape(f["geometry"]) for f in features]))
    west = Polygon([
        (37.47, northwest[1]), northwest, *park_ring[16:19][::-1],
        park_ring[15], (37.49236, 55.9358), (37.49295, 55.9335),
        (37.4931469, 55.9307897), (37.49297, 55.92671),
        (37.4942, 55.91884), (37.4944, 55.916), (37.47, 55.91),
    ])
    north = Polygon([
        (37.47, northwest[1]), *northern_edge,
        (37.5070372, 55.9484714), (37.5081898, 55.9491994),
        (37.5093692, 55.9501009), (37.5108154, 55.9510748),
        (37.5125827, 55.9520947), (37.5144103, 55.9530422),
        (37.5169488, 55.9549492), (37.55, 55.955), (37.55, 55.97), (37.47, 55.97),
    ])

    # Residential side of the line along the north of MIPT, extended to the railway.
    residential = Polygon([
        (37.506, 55.9305599), (37.5149417, 55.9305599), (37.5168415, 55.9306448),
        (37.5209271, 55.9312941), (37.53, 55.93275),
        (37.55, 55.956), (37.506, 55.956),
    ])

    for zone_id, name, mask in [
        ("Z05", "Западнее Z02, Z03 и Z04", west),
        ("Z06", "Севернее Z02, до Водников", north),
        ("Z07", "Восточные жилые кварталы севернее МФТИ", residential),
    ]:
        zone = remaining.intersection(mask)
        if zone.is_empty or not zone.is_valid:
            raise ValueError(f"Invalid zone {zone_id}: {zone.geom_type}")
        parts = [zone] if zone.geom_type == "Polygon" else [
            part for part in zone.geoms if part.geom_type == "Polygon"
        ]
        zone = (max(parts, key=lambda part: part.area) if zone_id == "Z07"
                else unary_union([part for part in parts if part.area > 1e-10]))
        features.append({
            "type": "Feature",
            "properties": {"zone_id": zone_id, "name": name, "status": "draft"},
            "geometry": mapping(zone),
        })
        remaining = remaining.difference(zone)
        print(f"{zone_id}: {zone.geom_type}")
    data["features"] = features
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
