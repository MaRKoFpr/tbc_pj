"""Build a standalone map from the saved OSM export and the course CSV files."""

import argparse
import csv
from pathlib import Path
from xml.etree import ElementTree as ET
from collections import defaultdict
from html import escape

import geopandas as gpd
from shapely.geometry import LineString

from download_osm import ROOT

DATA = ROOT / "osm_data"


def rows(name):
    with (ROOT / name).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def snapshot_layers(path):
    root = ET.parse(path).getroot()
    nodes = {node.attrib["id"]: (float(node.attrib["lon"]), float(node.attrib["lat"]))
             for node in root.iterfind("node")}
    layers = defaultdict(list)
    pedestrian_types = {"footway", "path", "cycleway", "steps", "pedestrian", "bridleway"}
    for way in root.iterfind("way"):
        tags = {tag.get("k"): tag.get("v") for tag in way.iterfind("tag")}
        highway = tags.get("highway")
        if not highway:
            continue
        refs = [ref.attrib["ref"] for ref in way.iterfind("nd")]
        if len(refs) < 2 or any(ref not in nodes for ref in refs):
            continue
        category = "paths" if highway in pedestrian_types else "service" if highway == "service" else "raw"
        geometry = LineString([nodes[ref] for ref in refs])
        label = f'{tags.get("name", highway)} · highway={highway} · OSM way {way.attrib["id"]}'
        layers[category].append((geometry, label))
    return layers


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--buffer-m", type=float, default=1000)
    parser.add_argument("--zones", type=Path)
    args = parser.parse_args()
    roads = gpd.read_file(DATA / "roads.gpkg", layer="edges")
    boundary = gpd.read_file(DATA / "boundary.geojson")
    objects = gpd.read_file(DATA / "features.geojson")
    buildings = objects[objects["building"].notna()]
    destinations = objects[objects[["amenity", "shop", "office"]].notna().any(axis=1)]
    west, south, east, north = roads.total_bounds
    if args.snapshot:
        context = boundary.to_crs(boundary.estimate_utm_crs()).buffer(args.buffer_m)
        west, south, east, north = context.to_crs(boundary.crs).total_bounds
    width = 850
    # At this latitude, a degree of longitude is shorter than a degree of latitude.
    height = round(width * (north - south) / ((east - west) * 0.56))

    def xy(lon, lat):
        return (20 + width * (lon - west) / (east - west),
                20 + height * (north - lat) / (north - south))

    def path(coords, close=False):
        points = " ".join(f"{x:.1f},{y:.1f}" for x, y in (xy(*c[:2]) for c in coords))
        return f"M{points}" + ("Z" if close else "")

    def shapes(geometry):
        if geometry is None or geometry.is_empty:
            return ""
        kind = geometry.geom_type
        if kind == "Point":
            x, y = xy(geometry.x, geometry.y)
            return f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2"/>'
        if kind == "LineString":
            return f'<path d="{path(geometry.coords)}"/>'
        if kind == "Polygon":
            rings = [geometry.exterior, *geometry.interiors]
            return '<path fill-rule="evenodd" d="' + "".join(
                path(ring.coords, close=True) for ring in rings
            ) + '"/>'
        if hasattr(geometry, "geoms"):
            return "".join(shapes(part) for part in geometry.geoms)
        return ""

    measurements = {row["id"]: row for row in rows("calibration.csv")}
    sites = defaultdict(list)
    for point in rows("points.csv"):
        sites[point["id"][:-1]].append(point)

    markers = []
    details = []
    for site, directions in sites.items():
        first = directions[0]
        x, y = xy(float(first["долгота"]), float(first["широта"]))
        observed = any(row["id"] in measurements for row in directions)
        label = escape(first["место"])
        markers.append(f'<a href="#site-{site}" aria-label="{site} {label}">'
                       f'<title>{site} · {label}</title><circle class="{"camera" if observed else "site"}" '
                       f'cx="{x:.1f}" cy="{y:.1f}" r="7"/>'
                       f'<text x="{x + 10:.1f}" y="{y - 6:.1f}">{site}</text></a>')
        lines = []
        for row in directions:
            point_id = row["id"]
            direction = escape(row["направление"])
            camera = measurements.get(point_id)
            value = "В открытой калибровке замера нет."
            if camera:
                value = (f'07:00–08:00 — {camera["07:00-08:00"]} авто/ч; '
                         f'08:00–09:00 — {camera["08:00-09:00"]} авто/ч.')
            lines.append(f"<li><b>{point_id}</b> · {direction}. {value}</li>")
        details.append(f'<section id="site-{site}"><h3>{site} · {label}</h3>'
                       f'<ul>{"".join(lines)}</ul></section>')

    layers = [
        ("buildings", "Здания", buildings.geometry, not args.snapshot),
        ("destinations", "Объекты назначения", destinations.geometry, False),
        ("roads", "Граф модели (после фильтрации)", roads.geometry, not args.snapshot),
        ("boundary", "Граница проекта", boundary.geometry, True),
    ]
    groups = []
    controls = []
    for key, name, geometries, visible in layers:
        groups.append(f'<g id="{key}"{"" if visible else " hidden"}>'
                      + "".join(map(shapes, geometries)) + "</g>")
        controls.append(f'<label><input type="checkbox" data-layer="{key}"'
                        f'{" checked" if visible else ""}> {name}</label>')
    if args.snapshot:
        raw_layers = snapshot_layers(args.snapshot)
        for key, name in (("raw", "Дороги OSM"), ("service", "Служебные проезды OSM"),
                          ("paths", "Дорожки OSM")):
            paths = "".join(f'<g><title>{escape(label)}</title>{shapes(geometry)}</g>'
                            for geometry, label in raw_layers[key])
            visible = True
            groups.insert(1, f'<g id="{key}" clip-path="url(#map-extent)"{"" if visible else " hidden"}>{paths}</g>')
            controls.append(f'<label><input type="checkbox" data-layer="{key}"'
                            f'{" checked" if visible else ""}> {name}</label>')
    if args.zones:
        zones = gpd.read_file(args.zones).to_crs(boundary.crs)
        palette = ("#5491c4", "#d49346", "#7cad75", "#9277bf",
                   "#6dafad", "#d48182", "#c4a950")
        areas, labels = [], []
        for row, color in zip(zones.itertuples(), palette):
            label = escape(f"{row.zone_id} · {row.name}")
            point = row.geometry.representative_point()
            x, y = xy(point.x, point.y)
            areas.append(f'<g fill="{color}" fill-opacity=".26" stroke="{color}">'
                         f'<title>{label}</title>{shapes(row.geometry)}</g>')
            labels.append(f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle">{row.zone_id}</text>')
        groups.insert(0, '<g id="zones">' + "".join(areas) + "</g>")
        groups.append('<g id="zone-labels">' + "".join(labels) + "</g>")
        controls.append('<label><input type="checkbox" data-layer="zones" checked> Черновые зоны</label>')
    controls.append('<label><input type="checkbox" data-layer="points" checked> Створы замеров</label>')
    groups.append(f'<g id="points">{"".join(markers)}</g>')

    document = f"""<!doctype html>
<html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Долгопрудный · исходные данные модели</title>
<style>
body {{ margin: 24px auto; padding: 0 16px; max-width: 1050px; font: 16px system-ui; color: #172638; }}
fieldset {{ display: flex; flex-wrap: wrap; gap: 12px; border: 1px solid #acbcc7; }}
svg {{ width: 100%; max-height: 76vh; background: #f4f7f9; }}
[hidden] {{ display: none; }}
#buildings {{ fill: #b9c8d0; }}
#destinations {{ fill: #6e51a1; opacity: .75; }}
#raw, #service, #paths {{ fill: none; stroke-width: 1.1; }}
#raw {{ stroke: #75868a; }}
#service {{ stroke: #bd8259; }}
#paths {{ stroke: #738858; stroke-dasharray: 2 2; }}
#roads {{ fill: none; stroke: #516781; stroke-width: 1.5; }}
#boundary {{ fill: none; stroke: #087e72; stroke-width: 2; stroke-dasharray: 5 4; }}
.site {{ fill: #245d9e; stroke: white; stroke-width: 1.5; }}
.camera {{ fill: #b14b13; stroke: white; stroke-width: 1.5; }}
text {{ fill: #172638; font-size: 13px; font-weight: 700; paint-order: stroke; stroke: white; stroke-width: 3px; }}
section {{ padding: 0 12px; }}
section:target {{ background: #fff0d3; }}
</style>
<h1>Долгопрудный · исходные данные модели</h1>
<fieldset><legend>Слои</legend>{"".join(controls)}</fieldset>
<p>Синие точки — места проверки. Оранжевые — места с открытыми замерами хотя бы в одном направлении. Нажмите на точку, чтобы увидеть направления и значения.</p>
<svg viewBox="0 0 {width + 40} {height + 40}" aria-label="Граница, дорожная сеть, здания и 15 створов">
<defs><clipPath id="map-extent"><rect x="20" y="20" width="{width}" height="{height}"/></clipPath></defs>
{"".join(groups)}
</svg>
<p>Дороги и объекты © <a href="https://www.openstreetmap.org/copyright">участники OpenStreetMap</a>.
Граница и замеры — материалы курса. Дороги могут заканчиваться у края выгрузки, который лежит за пунктирным контуром. Служебные проезды в этой версии сети опущены.</p>
{"".join(details)}
<script>
for (const control of document.querySelectorAll("[data-layer]")) {{
    const layer = document.getElementById(control.dataset.layer);
    const update = () => {{
        layer.toggleAttribute("hidden", !control.checked);
        if (layer.id === "zones") {{
            document.getElementById("zone-labels").toggleAttribute("hidden", !control.checked);
        }}
    }};
    control.addEventListener("change", update);
    update();
}}
</script></html>
"""
    name = "zones_draft.html" if args.zones else "osm_map_wide.html" if args.snapshot else "osm_map.html"
    output = ROOT / name
    output.write_text(document, encoding="utf-8")
    print(f"{output.name}: {len(roads)} edges, {len(buildings)} buildings, "
          f"{len(destinations)} destinations, {len(sites)} sites")


if __name__ == "__main__":
    main()
