"""Проверки сборки городского входа и полного расчета."""

import contextlib
import copy
import csv
import hashlib
import io
import json
import math
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path

import networkx as nx

from src.build_model_input import (
    ROOT, bind_points, load_roads, road_parameters, split_gateways, validate,
)
from src.model import Network, gravity, run
from src.transport_demand.build_periods import build_periods


class ModelInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads((ROOT / "data/input/model_config.json").read_text(encoding="utf-8"))
        cls.data = json.loads((ROOT / "data/processed/model_input.json").read_text(encoding="utf-8"))

    def test_directional_tags_follow_osm_way_direction(self):
        tags = {"highway": "secondary", "reversed": "True", "oneway": "False",
                "maxspeed": "60", "maxspeed:forward": "50", "maxspeed:backward": "40",
                "lanes": "3", "lanes:forward": "2", "lanes:backward": "1"}
        backward = road_parameters(tags, self.config)
        self.assertEqual((backward["speed_kph"], backward["lanes_direction"]), (40, 1))
        tags["reversed"] = "False"
        forward = road_parameters(tags, self.config)
        self.assertEqual((forward["speed_kph"], forward["lanes_direction"]), (50, 2))

    def test_merged_tags_and_missing_data(self):
        params = road_parameters({"highway": "['residential', 'living_street']",
                                  "maxspeed": "['40', '20', 'RU:urban']",
                                  "lanes": "['4', '2']", "oneway": "False",
                                  "reversed": "[False, True]"}, self.config)
        self.assertEqual((params["speed_kph"], params["lanes_direction"]), (20, 1))
        default = road_parameters({"highway": "residential"}, self.config)
        self.assertEqual((default["speed_kph"], default["capacity_vph"]), (30, 1500))
        one_way = road_parameters({"highway": "secondary", "lanes": "2",
                                   "oneway": "True"}, self.config)
        self.assertEqual(one_way["lanes_direction"], 2)

    def test_periods_are_current_and_balanced(self):
        periods = build_periods()
        self.assertEqual(periods, self.data["periods"])
        self.assertEqual(periods, json.loads((ROOT / "data/processed/periods.json").read_text(encoding="utf-8")))
        for demand in periods.values():
            self.assertTrue(math.isclose(sum(demand["departures"].values()),
                                         sum(demand["arrivals"].values()), rel_tol=1e-12))

    def test_source_hashes_are_current(self):
        for relative, expected in self.data["metadata"]["source_sha256"].items():
            self.assertEqual(hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(), expected,
                             f"Пересоберите model_input.json после изменения {relative}")

    def test_all_bindings_have_correct_geometry_and_direction(self):
        _, _, edges, geometries = load_roads(ROOT / "data/osm/roads.graphml", self.config)
        points, report = bind_points(ROOT / "data/input/points.csv",
                                     ROOT / "data/osm/boundary.geojson",
                                     self.config, edges, geometries)
        self.assertEqual(len(points), 30)
        self.assertEqual(len(report), 30)
        self.assertTrue(all(row["direction_cosine"] > 0.9 for row in report))
        # Обратная привязка P03 должна быть отклонена.
        invalid = copy.deepcopy(self.config)
        invalid["point_edges"]["P03A"] = invalid["point_edges"]["P03B"]
        with self.assertRaises(ValueError):
            bind_points(ROOT / "data/input/points.csv", ROOT / "data/osm/boundary.geojson",
                        invalid, edges, geometries)

    def test_gate_splits_preserve_physical_routes(self):
        _, _, edges, geometries = load_roads(ROOT / "data/osm/roads.graphml", self.config)
        points, _ = bind_points(ROOT / "data/input/points.csv",
                                 ROOT / "data/osm/boundary.geojson", self.config, edges, geometries)
        before = nx.MultiDiGraph()
        before.add_weighted_edges_from((e["u"], e["v"], e["time_s"]) for e in edges)
        originals = {e["id"]: e.copy() for e in edges}
        split_gateways(edges, geometries, points, self.config)
        self.assertEqual(len(edges), len(originals) + 6)
        after = nx.MultiDiGraph()
        after.add_weighted_edges_from((e["u"], e["v"], e["time_s"]) for e in edges)
        for source in before:
            expected = nx.single_source_dijkstra_path_length(before, source)
            actual = nx.single_source_dijkstra_path_length(after, source)
            for target, time in expected.items():
                self.assertAlmostEqual(actual[target], time, places=8)
        by_id = {e["id"]: e for e in edges}
        for edge in edges:
            if "gateway" in edge:
                original = originals[edge["id"]]
                outside = by_id[edge["id"] + ":outside"]
                self.assertAlmostEqual(edge["length_m"] + outside["length_m"], original["length_m"])

    def test_model_rejects_unbalanced_demand(self):
        invalid = copy.deepcopy(self.data)
        invalid["periods"]["07:00"]["departures"]["Z01"] += 0.01
        with self.assertRaises(ValueError):
            validate(invalid)

    def test_equilibrium_preserves_demand_and_forbidden_pairs(self):
        validate(self.data)
        network = Network(self.data["edges"], self.data["zones"])
        zones = self.data["zones"]
        for period in ["07:00", "08:00"]:
            demand = self.data["periods"][period]
            matrix = gravity(zones, network.free_times(), demand["departures"], demand["arrivals"])
            for i, origin in enumerate(zones.values()):
                for j, destination in enumerate(zones.values()):
                    if i == j or (origin["external"] and destination["external"]):
                        self.assertEqual(matrix[i][j], 0)
            flows, gap, _ = network.equilibrium(matrix)
            self.assertLessEqual(gap, 1e-4)
            actual, expected = defaultdict(float), defaultdict(float)
            for edge, flow in zip(self.data["edges"], flows):
                actual[edge["u"]] += flow
                actual[edge["v"]] -= flow
            for zone_id, zone in zones.items():
                expected[zone["departure"]] += demand["departures"][zone_id]
                expected[zone["arrival"]] -= demand["arrivals"][zone_id]
            for node in actual.keys() | expected.keys():
                self.assertAlmostEqual(actual[node], expected[node], delta=0.001)

    def test_external_trips_cross_their_measurement_edges(self):
        network = Network(self.data["edges"], self.data["zones"])
        zones = self.data["zones"]
        zone_ids = list(zones)
        edge_index = {edge["id"]: i for i, edge in enumerate(network.edges)}
        for gate, points in self.config["gateways"].items():
            for internal in [name for name, zone in zones.items() if not zone["external"]]:
                for direction, (origin, destination) in {
                    "in": (gate, internal), "out": (internal, gate),
                }.items():
                    matrix = [[0.0] * len(zones) for _ in zones]
                    matrix[zone_ids.index(origin)][zone_ids.index(destination)] = 1.0
                    flows, _ = network.assign(matrix, network.costs([0] * len(network.edges)))
                    edge = self.data["point_edges"][points[direction]][0]
                    self.assertEqual(flows[edge_index[edge]], 1)

    def test_export_is_reproducible_and_uses_hourly_units(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flows.csv"
            with contextlib.redirect_stdout(io.StringIO()):
                run(self.data, path)
            with path.open(newline="", encoding="utf-8-sig") as stream:
                reader = csv.DictReader(stream)
                columns, rows = reader.fieldnames, list(reader)
            self.assertEqual(columns, ["id", "07:00", "07:30", "08:00", "08:30"])
            self.assertEqual(len(rows), 30)
            values = [float(row[period]) for row in rows for period in columns[1:]]
            self.assertEqual(len(values), 120)
            self.assertTrue(all(math.isfinite(value) and value >= 0 for value in values))
            self.assertEqual(path.read_bytes(), (ROOT / "outputs/flows.csv").read_bytes())
            by_id = {row["id"]: row for row in rows}
            self.assertAlmostEqual(float(by_id["P02A"]["07:00"]),
                                   2 * self.data["periods"]["07:00"]["arrivals"]["EXT_P02"], places=3)


if __name__ == "__main__":
    unittest.main()
