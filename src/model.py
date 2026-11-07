"""Автомобильный MVP. Запуск: python -m src.model data/processed/model_input.json."""

import csv
import json
import math
import sys
from collections import defaultdict
from heapq import heappop, heappush
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUT_DIR = PROJECT_ROOT / "data" / "input"
OUTPUT_DIR = PROJECT_ROOT / "outputs"


def shortest_paths(start, adjacency, costs):
    distance = {start: 0.0}
    previous = {}
    queue = [(0.0, start)]
    while queue:
        value, node = heappop(queue)
        if value != distance[node]:
            continue
        for end, edge in adjacency[node]:
            candidate = value + costs[edge]
            if candidate < distance.get(end, math.inf):
                distance[end] = candidate
                previous[end] = (node, edge)
                heappush(queue, (candidate, end))
    return distance, previous


def gravity(zones, times, departures, arrivals, gamma=2):
    """Обратная степень времени и попеременная балансировка."""
    rows = [float(departures.get(zone, 0)) for zone in zones]
    cols = [float(arrivals.get(zone, 0)) for zone in zones]
    if any(not math.isfinite(x) or x < 0 for x in rows + cols):
        raise ValueError("Суммы спроса должны быть конечными и неотрицательными")
    if not math.isclose(sum(rows), sum(cols), rel_tol=1e-9):
        raise ValueError("Общие отправления и прибытия должны совпадать")
    size = len(zones)
    matrix = [
        [times[i][j] ** -gamma if rows[i] and cols[j] and
         0 < times[i][j] < math.inf else 0.0 for j in range(size)]
        for i in range(size)
    ]
    for _ in range(2000):
        for i, target in enumerate(rows):
            total = sum(matrix[i])
            if target and not total:
                raise ValueError(f"Нет допустимых назначений из {zones[i]}")
            if total:
                matrix[i] = [x * (target / total) for x in matrix[i]]
        for j, target in enumerate(cols):
            total = sum(row[j] for row in matrix)
            if target and not total:
                raise ValueError(f"Нет допустимых отправлений в {zones[j]}")
            if total:
                for row in matrix:
                    row[j] *= target / total
        if all(abs(sum(row) - target) <= 1e-7 * max(1, target)
               for row, target in zip(matrix, rows)):
            return matrix
    raise ValueError("Балансировка OD не сошлась. Проверьте суммы и допустимые пары.")


class Network:
    def __init__(self, edges, zones):
        self.edges = edges
        self.zones = zones
        self.adjacency = defaultdict(list)
        for index, edge in enumerate(edges):
            if any(not math.isfinite(edge[key]) or edge[key] <= 0
                   for key in ("time_s", "capacity_vph")):
                raise ValueError("Время и пропускная способность должны быть положительными")
            self.adjacency[edge["u"]].append((edge["v"], index))

    def free_times(self):
        costs = [edge["time_s"] for edge in self.edges]
        result = []
        for origin, start in self.zones.items():
            distances, _ = shortest_paths(start["departure"], self.adjacency, costs)
            result.append([
                distances.get(end["arrival"], math.inf)
                + start.get("departure_s", 0) + end.get("arrival_s", 0)
                if origin != destination and not (start.get("external") and end.get("external"))
                else math.inf
                for destination, end in self.zones.items()
            ])
        return result

    def costs(self, flows):
        # Потоки внутри расчёта измеряются в машинах за получас.
        return [edge["time_s"] * (1 + 0.15 * (2 * flow / edge["capacity_vph"]) ** 4)
                for edge, flow in zip(self.edges, flows)]

    def assign(self, matrix, costs):
        flows = [0.0] * len(self.edges)
        shortest_total = 0.0
        for start, row in zip(self.zones.values(), matrix):
            if not any(row):
                continue
            source = start["departure"]
            distance, previous = shortest_paths(source, self.adjacency, costs)
            for end, trips in zip(self.zones.values(), row):
                if not trips:
                    continue
                node = end["arrival"]
                shortest_total += trips * distance[node]
                while node != source:
                    node, edge = previous[node]
                    flows[edge] += trips
        return flows, shortest_total

    def equilibrium(self, matrix, tolerance=1e-4, max_iterations=3000):
        flows, _ = self.assign(matrix, self.costs([0] * len(self.edges)))
        for iteration in range(max_iterations + 1):
            costs = self.costs(flows)
            target, shortest = self.assign(matrix, costs)
            total = sum(flow * cost for flow, cost in zip(flows, costs))
            gap = max(0, 1 - shortest / total) if total else 0
            if gap <= tolerance:
                return flows, gap, iteration
            if iteration == max_iterations:
                break

            direction = [y - x for x, y in zip(flows, target)]
            low, high = 0.0, 1.0
            for _ in range(36):
                step = (low + high) / 2
                trial = [x + step * d for x, d in zip(flows, direction)]
                derivative = sum(d * c for d, c in zip(direction, self.costs(trial)))
                if derivative < 0:
                    low = step
                else:
                    high = step
            flows = [x + ((low + high) / 2) * d for x, d in zip(flows, direction)]
        raise RuntimeError(f"Равновесие не достигнуто, relative gap = {gap:.6g}")


def run(data, output):
    network = Network(data["edges"], data["zones"])
    times = network.free_times()
    edge_index = {edge["id"]: index for index, edge in enumerate(network.edges)}
    if len(edge_index) != len(network.edges):
        raise ValueError("ID рёбер должны быть уникальны")
    with (INPUT_DIR / "flows_template.csv").open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        columns = reader.fieldnames
        rows = list(reader)
    for period in columns[1:]:
        demand = data["periods"][period]
        matrix = gravity(network.zones, times, demand["departures"], demand["arrivals"])
        flows, gap, iterations = network.equilibrium(matrix)
        print(f"{period}: {iterations} iterations, relative gap={gap:.6g}")
        for row in rows:
            edges = data["point_edges"][row["id"]]
            if not edges or len(edges) != len(set(edges)):
                raise ValueError(f"Некорректная привязка {row['id']}")
            row[period] = round(2 * sum(flows[edge_index[edge]] for edge in edges), 3)
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    path = Path(sys.argv[1])
    OUTPUT_DIR.mkdir(exist_ok=True)
    run(json.loads(path.read_text(encoding="utf-8-sig")), OUTPUT_DIR / "flows.csv")
