"""Сборка городского входа: python -m src.build_model_input."""

import ast
import csv
import hashlib
import json
import math
from pathlib import Path

import geopandas as gpd
import networkx as nx
from shapely import wkt
from shapely.geometry import LineString, Point
from shapely.ops import substring

from src.model import Network, gravity
from src.transport_demand.build_periods import GATEWAYS, INTERNAL_TRIPS, build_periods

ROOT = Path(__file__).resolve().parents[1]
METRIC_CRS = "EPSG:32637"


def tag_values(value):
    if isinstance(value, str) and value.startswith("["):
        value = ast.literal_eval(value)
    return value if isinstance(value, (list, tuple)) else [value]


def positive_numbers(value, codes=None):
    numbers = []
    for item in tag_values(value):
        try:
            token = str(item).strip()
            if codes and token in codes:
                number = float(codes[token])
            elif token.endswith(" mph"):
                number = float(token[:-4]) * 1.609344
            else:
                number = float(token)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number) and number > 0:
            numbers.append(number)
    return numbers


def directional_tag(tags, name):
    reversed_values = {str(v).lower() for v in tag_values(tags.get("reversed", "False"))}
    if len(reversed_values) == 1:
        suffix = "backward" if reversed_values == {"true"} else "forward"
        key = f"{name}:{suffix}"
        if tags.get(key) is not None:
            return tags[key], key
    return tags.get(name), name


def road_parameters(tags, config):
    classes = tag_values(tags["highway"])
    default_speed = min(config["default_speed_kph"][kind] for kind in classes)
    raw_speed, speed_source = directional_tag(tags, "maxspeed")
    speeds = positive_numbers(raw_speed, config["speed_codes_kph"])
    if not speeds and speed_source != "maxspeed":
        speeds = positive_numbers(tags.get("maxspeed"), config["speed_codes_kph"])
        speed_source = "maxspeed"
    speed = min(speeds) if speeds else default_speed
    speed_source = speed_source if speeds else "highway_default"

    raw_lanes, lanes_source = directional_tag(tags, "lanes")
    lane_numbers = positive_numbers(raw_lanes)
    if not lane_numbers and lanes_source != "lanes":
        lane_numbers = positive_numbers(tags.get("lanes"))
        lanes_source = "lanes"
    lanes = min(lane_numbers) if lane_numbers else 1.0
    if lane_numbers and lanes_source == "lanes":
        if str(tags.get("oneway")).lower() != "true":
            center = max(positive_numbers(tags.get("lanes:both_ways")), default=0)
            lanes = max(1.0, (lanes - center) / 2)
            lanes_source = "lanes_split"
    if not lane_numbers:
        lanes_source = "default_one_lane"
    return {
        "speed_kph": speed,
        "lanes_direction": lanes,
        "capacity_vph": lanes * config["capacity_per_lane_vph"],
        "speed_source": speed_source,
        "lanes_source": lanes_source,
    }


def load_roads(path, config):
    graph = nx.read_graphml(path, force_multigraph=True)
    geographic_nodes = {
        str(node): Point(float(data["x"]), float(data["y"]))
        for node, data in graph.nodes(data=True)
    }
    nodes = dict(zip(geographic_nodes, gpd.GeoSeries(
        list(geographic_nodes.values()), crs=4326).to_crs(METRIC_CRS)))
    raw = sorted(graph.edges(keys=True, data=True), key=lambda item: tuple(map(str, item[:3])))
    geographic_lines = [
        wkt.loads(tags["geometry"]) if "geometry" in tags else
        LineString([geographic_nodes[str(u)], geographic_nodes[str(v)]])
        for u, v, _, tags in raw
    ]
    lines = gpd.GeoSeries(geographic_lines, crs=4326).to_crs(METRIC_CRS)
    edges, geometries = [], {}
    for (u, v, key, tags), line in zip(raw, lines):
        u, v = str(u), str(v)
        if Point(line.coords[-1]).distance(nodes[u]) < Point(line.coords[0]).distance(nodes[u]):
            line = LineString(list(line.coords)[::-1])
        edge_id = f"{u}:{v}:{key}"
        params = road_parameters(tags, config)
        length = float(tags["length"])
        edges.append({"id": edge_id, "u": u, "v": v, "length_m": length,
                      "name": tags.get("name", ""), "highway": tags["highway"],
                      "time_s": length / (params["speed_kph"] / 3.6), **params})
        geometries[edge_id] = line
    return graph, nodes, edges, geometries


def direction_vector(text):
    vectors = {
        "на север": (0, 1), "на юг": (0, -1),
        "на восток": (1, 0), "на запад": (-1, 0),
        "на северо-восток": (2 ** -0.5, 2 ** -0.5),
        "на юго-запад": (-2 ** -0.5, -2 ** -0.5),
    }
    return vectors[text.split(",")[0].strip()]


def bind_points(points_path, boundary_path, config, edges, geometries):
    with points_path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    ids = [row["id"] for row in rows]
    if len(ids) != len(set(ids)) or set(ids) != set(config["point_edges"]):
        raise ValueError("Привязки должны покрывать все направления из points.csv")
    points = gpd.GeoSeries([Point(float(row["долгота"]), float(row["широта"]))
                            for row in rows], crs=4326).to_crs(METRIC_CRS)
    boundary = gpd.read_file(boundary_path).to_crs(METRIC_CRS).geometry.iloc[0]
    edge_by_id = {edge["id"]: edge for edge in edges}
    point_geometries, report = {}, []
    for row, point in zip(rows, points):
        point_id = row["id"]
        chosen = config["point_edges"][point_id]
        if not chosen or len(chosen) != len(set(chosen)):
            raise ValueError(f"Пустая или повторная привязка {point_id}")
        point_geometries[point_id] = point
        dx, dy = direction_vector(row["направление"])
        for edge_id in chosen:
            line = geometries[edge_id]
            at = line.project(point)
            start, end = line.interpolate(max(0, at - 10)), line.interpolate(min(line.length, at + 10))
            vx, vy = end.x - start.x, end.y - start.y
            cosine = (dx * vx + dy * vy) / math.hypot(vx, vy)
            distance = point.distance(line)
            if distance > config["max_point_distance_m"] or cosine < config["min_direction_cosine"]:
                raise ValueError(f"Проверьте положение или направление {point_id}: {edge_id}")
            report.append({"point_id": point_id, "edge_id": edge_id,
                           "road_name": edge_by_id[edge_id]["name"],
                           "direction": row["направление"],
                           "distance_m": round(distance, 3), "direction_cosine": round(cosine, 6),
                           "boundary_distance_m": round(boundary.boundary.distance(point), 3),
                           "note": config.get("point_notes", {}).get(point_id, "")})
    return point_geometries, report


def split_gateways(edges, geometries, points, config):
    """Вставляем узлы ворот в створы, сохраняя физические маршруты."""
    by_id = {edge["id"]: edge for edge in edges}
    zones, nodes = {}, {}
    for zone_id, bindings in config["gateways"].items():
        zone = {"external": True}
        for direction, point_id in bindings.items():
            edge_ids = config["point_edges"][point_id]
            if len(edge_ids) != 1:
                raise ValueError("Для ворот пока нужен один участок на направление")
            edge_id = edge_ids[0]
            edge = by_id[edge_id]
            line = geometries[edge_id]
            # У перекрестка оставляем хотя бы метр модельного участка.
            margin = min(1.0, line.length / 4)
            at = max(margin, min(line.length - margin, line.project(points[point_id])))
            terminal = f"{zone_id}_{direction.upper()}"
            edge["original_u"], edge["original_v"] = edge["u"], edge["v"]
            other = dict(edge)
            other["id"] = f"{edge_id}:outside"
            if direction == "in":
                edge["u"] = terminal
                clipped = substring(line, at, line.length)
                other["v"] = terminal
                outside = substring(line, 0, at)
                zone["departure"] = terminal
            else:
                edge["v"] = terminal
                clipped = substring(line, 0, at)
                other["u"] = terminal
                outside = substring(line, at, line.length)
                zone["arrival"] = terminal
            ratio = clipped.length / line.length
            edge["length_m"] *= ratio
            edge["time_s"] *= ratio
            edge["gateway"] = zone_id
            other["length_m"] *= outside.length / line.length
            other["time_s"] *= outside.length / line.length
            edges.append(other)
            nodes[terminal] = line.interpolate(at)
            geometries[edge_id] = clipped
            geometries[other["id"]] = outside
        zones[zone_id] = zone
    return zones, nodes


def connect_zones(zones_path, edges, nodes, config):
    network = nx.DiGraph()
    network.add_edges_from((edge["u"], edge["v"]) for edge in edges)
    core = max(nx.strongly_connected_components(network), key=len)
    zone_frame = gpd.read_file(zones_path).to_crs(METRIC_CRS).sort_values("zone_id")
    zones, report = {}, []
    for row in zone_frame.itertuples():
        candidates = [node for node in sorted(core)
                      if not node.startswith("EXT_") and row.geometry.covers(nodes[node])]
        if not candidates:
            raise ValueError(f"В зоне {row.zone_id} нет узла основной компоненты")
        center = row.geometry.representative_point()
        node = min(candidates, key=lambda key: (center.distance(nodes[key]), key))
        distance = center.distance(nodes[node])
        seconds = distance / (config["connector_speed_kph"] / 3.6)
        zones[row.zone_id] = {"departure": node, "arrival": node,
                              "departure_s": seconds, "arrival_s": seconds, "external": False}
        report.append({"zone_id": row.zone_id, "name": row.name, "node": node,
                       "connector_distance_m": round(distance, 3),
                       "connector_time_s": round(seconds, 3)})
    return zones, report


def validate(data, expected_point_ids=None, expected_periods=None):
    edges, zones = data["edges"], data["zones"]
    edge_ids = [edge["id"] for edge in edges]
    if len(edge_ids) != len(set(edge_ids)):
        raise ValueError("ID ребер повторяются")
    if expected_point_ids is not None and set(data["point_edges"]) != set(expected_point_ids):
        raise ValueError("Привязки не совпадают с шаблоном выгрузки")
    if expected_periods is not None and set(data["periods"]) != set(expected_periods):
        raise ValueError("Периоды не совпадают с шаблоном выгрузки")
    for point, chosen in data["point_edges"].items():
        if not chosen or len(chosen) != len(set(chosen)) or not set(chosen) <= set(edge_ids):
            raise ValueError(f"Неверные ребра для {point}")
    network = Network(edges, zones)
    times = network.free_times()
    for i, start in enumerate(zones.values()):
        for j, end in enumerate(zones.values()):
            if i != j and not (start.get("external") and end.get("external")):
                if not math.isfinite(times[i][j]) or times[i][j] <= 0:
                    raise ValueError("Между зонами нет допустимого маршрута")
    for period, demand in data["periods"].items():
        if set(demand["departures"]) != set(zones) or set(demand["arrivals"]) != set(zones):
            raise ValueError(f"Набор зон спроса не совпадает за {period}")
        gravity(zones, times, demand["departures"], demand["arrivals"])


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main():
    input_dir, processed = ROOT / "data" / "input", ROOT / "data" / "processed"
    config_path = input_dir / "model_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    graph_path = ROOT / "data" / "osm" / "roads.graphml"
    _, nodes, edges, geometries = load_roads(graph_path, config)
    point_geometries, point_report = bind_points(
        input_dir / "points.csv", ROOT / "data" / "osm" / "boundary.geojson",
        config, edges, geometries)
    external, terminal_nodes = split_gateways(edges, geometries, point_geometries, config)
    nodes.update(terminal_nodes)
    zones, zone_report = connect_zones(processed / "zones_primary.geojson", edges, nodes, config)
    zones.update(external)
    periods = build_periods()
    sources = [graph_path, config_path, processed / "zones_primary.geojson",
               processed / "zone_stats.csv", input_dir / "points.csv",
               input_dir / "calibration.csv", input_dir / "flows_template.csv",
               ROOT / "data" / "osm" / "boundary.geojson",
               Path(__file__), ROOT / "src" / "transport_demand" / "build_periods.py"]
    data = {"edges": edges, "zones": zones, "periods": periods,
            "point_edges": config["point_edges"],
            "metadata": {
                "status": "uncalibrated_baseline", "demand_units": "vehicles_per_half_hour",
                "capacity_units": "vehicles_per_hour", "time_units": "seconds",
                "output_units": "vehicles_per_hour", "metric_crs": METRIC_CRS,
                "capacity_per_lane_vph": config["capacity_per_lane_vph"],
                "connector_speed_kph": config["connector_speed_kph"],
                "internal_trips_vph": INTERNAL_TRIPS, "gateway_demand": GATEWAYS,
                "gateway_method": "split_at_measurement",
                "intrazonal_trips": False, "external_transit": False,
                "identical_half_hours_within_hour": True,
                "source_sha256": {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                                  for path in sources},
            }}
    with (input_dir / "flows_template.csv").open(encoding="utf-8-sig", newline="") as stream:
        template = csv.DictReader(stream)
        columns = template.fieldnames
        point_ids = [row["id"] for row in template]
    validate(data, point_ids, columns[1:])
    processed.mkdir(exist_ok=True, parents=True)
    for filename, result in [("model_input.json", data), ("periods.json", periods)]:
        (processed / filename).write_text(
            json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    write_csv(processed / "point_bindings.csv", point_report)
    write_csv(processed / "zone_connections.csv", zone_report)
    # Геометрии пригодятся для просмотра привязок в QGIS.
    layer = gpd.GeoDataFrame([
        {"point_id": row["point_id"], "edge_id": row["edge_id"],
         "geometry": geometries[row["edge_id"]]} for row in point_report
    ], crs=METRIC_CRS).to_crs(4326)
    (processed / "point_bindings.geojson").write_text(layer.to_json() + "\n", encoding="utf-8")
    print(f"Готово: {len(edges)} ребер, {len(zones)} зон, {len(point_ids)} направлений, {len(periods)} периода")


if __name__ == "__main__":
    main()
