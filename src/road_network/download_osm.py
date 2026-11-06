"""Выгрузка OSM для проекта. Запуск: python download_osm.py."""

import argparse
import csv
from pathlib import Path
from xml.etree import ElementTree as ET

import geopandas as gpd
import networkx as nx
import osmnx as ox
from shapely.geometry import Point, Polygon

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INPUT_DIR = PROJECT_ROOT / "data" / "input"
OSM_DIR = PROJECT_ROOT / "data" / "osm"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUT_DIR = PROJECT_ROOT / "outputs"


def read_boundary(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        points = sorted(csv.DictReader(stream), key=lambda row: int(row["номер"]))
    polygon = Polygon([(float(row["долгота"]), float(row["широта"])) for row in points])
    if polygon.is_empty or not polygon.is_valid:
        raise ValueError(f"Некорректная граница в {path}")
    return gpd.GeoSeries([polygon], crs="EPSG:4326")


def roads_from_snapshot(snapshot, road_area, output):
    """Выбрать автомобильные дороги из полного локального снимка."""
    root = ET.parse(snapshot).getroot()
    road_types = {
        "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified",
        "residential", "living_street", "road",
    }
    road_types.update(f"{kind}_link" for kind in (
        "motorway", "trunk", "primary", "secondary", "tertiary"
    ))
    ways = []
    node_ids = set()
    for way in root.findall("way"):
        tags = {tag.get("k"): tag.get("v") for tag in way.findall("tag")}
        if tags.get("highway") not in road_types or tags.get("area") == "yes":
            continue
        access = next((tags[key] for key in ("motorcar", "motor_vehicle", "vehicle", "access")
                       if key in tags), None)
        if access in {"no", "private"}:
            continue
        ways.append(way)
        node_ids.update(ref.get("ref") for ref in way.findall("nd"))
    roads = ET.Element("osm", version="0.6")
    roads.extend(node for node in root.findall("node") if node.get("id") in node_ids)
    roads.extend(ways)
    filtered_path = output / "roads_source.osm"
    ET.ElementTree(roads).write(filtered_path, encoding="utf-8", xml_declaration=True)
    graph = ox.graph.graph_from_xml(filtered_path, simplify=False, retain_all=True)
    graph = ox.truncate.truncate_graph_polygon(graph, road_area, truncate_by_edge=True)
    graph.remove_nodes_from(list(node for node, degree in graph.degree if degree == 0))
    return ox.simplification.simplify_graph(graph, remove_rings=False)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--buffer-m", type=float, default=250, help="Запас вокруг графа в метрах")
    parser.add_argument("--roads-only", action="store_true", help="Скачать только дорожную сеть")
    parser.add_argument("--osm-file", type=Path, help="Обработать локальный снимок OSM XML")
    args = parser.parse_args()
    if not 0 <= args.buffer_m < float("inf"):
        parser.error("--buffer-m должен быть конечным и неотрицательным")

    boundary = read_boundary(INPUT_DIR / "area.csv")
    metric_crs = boundary.estimate_utm_crs()
    road_area = boundary.to_crs(metric_crs).buffer(args.buffer_m).to_crs(boundary.crs).iloc[0]
    output = OSM_DIR
    output.mkdir(exist_ok=True)
    ox.settings.cache_folder = output / "cache"
    ox.settings.use_cache = True
    ox.settings.useful_tags_way = list(dict.fromkeys([
        *ox.settings.useful_tags_way,
        "lanes:forward", "lanes:backward", "lanes:both_ways",
        "maxspeed:forward", "maxspeed:backward",
    ]))

    if args.osm_file:
        print("Собираю дорожную сеть из локального снимка…", flush=True)
        graph = roads_from_snapshot(args.osm_file, road_area, output)
    else:
        print("Запрашиваю дорожную сеть…", flush=True)
        graph = ox.graph.graph_from_polygon(
            road_area, network_type="drive", retain_all=True, truncate_by_edge=True
        )
    # The download buffer can include disconnected roads in neighbouring areas.
    # Retain components serving the study area and their approaches at the edge.
    study_area = boundary.iloc[0]
    outside_components = [
        component for component in nx.weakly_connected_components(graph)
        if not any(study_area.covers(Point(graph.nodes[node]["x"], graph.nodes[node]["y"]))
                   for node in component)
    ]
    graph.remove_nodes_from(node for component in outside_components for node in component)
    print(f"Удалено внешних несвязанных компонентов: {len(outside_components)}")
    ox.io.save_graphml(graph, filepath=output / "roads.graphml")
    ox.io.save_graph_geopackage(graph, filepath=output / "roads.gpkg", directed=True)
    boundary.to_file(output / "boundary.geojson", driver="GeoJSON")
    print(f"Сохранено узлов: {len(graph)}, направленных рёбер: {graph.number_of_edges()}")

    if not args.roads_only:
        tags = {"building": True, "amenity": True, "shop": True, "office": True,
                "landuse": ["industrial", "commercial", "retail"]}
        if args.osm_file:
            print("Извлекаю здания и объекты из локального снимка…", flush=True)
            features = ox.features.features_from_xml(
                args.osm_file, polygon=boundary.iloc[0], tags=tags
            )
        else:
            print("Запрашиваю здания и объекты назначения…", flush=True)
            features = ox.features.features_from_polygon(boundary.iloc[0], tags=tags)
        (output / "features.geojson").write_text(features.to_json(), encoding="utf-8")
        print(f"Сохранено объектов: {len(features)}")


if __name__ == "__main__":
    main()
