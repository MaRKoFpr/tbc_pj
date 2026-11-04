"""Add the Centralny district and the Kapitsa Lyceum to the manual map."""

import json

from shapely.geometry import Polygon, mapping, shape

from download_osm import ROOT, read_boundary


def main():
    path = ROOT / "zones_manual.geojson"
    collection = json.loads(path.read_text(encoding="utf-8-sig"))
    features = [feature for feature in collection["features"]
                if feature["properties"]["zone_id"] != "Z03"]
    # Start on the exact shared boundary with Z02 along Novy Boulevard.
    park = next(feature for feature in features if feature["properties"]["zone_id"] == "Z02")
    north = park["geometry"]["coordinates"][0][10:16][::-1]
    central = Polygon([
        *north,
        (37.5122381, 55.9358845), (37.5130802, 55.9344477),
        (37.5127689, 55.9340459), (37.5115546, 55.9334274),
        (37.5077581, 55.9332091), (37.5041911, 55.9324901),
        (37.5015402, 55.931961),
        # Include the lyceum site south of Prospekt Raketostroiteley.
        (37.50125, 55.93012), (37.5008, 55.92982), (37.49745, 55.92918),
        (37.49706, 55.93107), (37.4967842, 55.9310202),
        (37.4954792, 55.9308088), (37.4931469, 55.9307897),
        (37.49295, 55.9335), (37.49236, 55.9358),
    ])
    boundary = read_boundary(ROOT / "area.csv").iloc[0]
    if not central.is_valid or not boundary.covers(central):
        raise ValueError("Z03 must be a valid polygon inside the project")
    if any(central.intersection(shape(feature["geometry"])).area > 1e-12 for feature in features):
        raise ValueError("Z03 overlaps an existing zone")
    features.append({
        "type": "Feature",
        "properties": {"zone_id": "Z03", "name": "Центральный и Физтех-лицей имени Капицы",
                       "status": "draft"},
        "geometry": mapping(central),
    })
    collection["features"] = features
    path.write_text(json.dumps(collection, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
