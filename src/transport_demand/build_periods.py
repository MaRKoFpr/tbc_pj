"""Предварительный спрос в машинах за получас."""

import csv
import json
import math
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INPUT_DIR = PROJECT_ROOT / "data" / "input"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"

# Заглушки для первого запуска, дальше подбираются калибровкой.
INTERNAL_TRIPS = {"07:00-08:00": 4000, "08:00-09:00": 4000}
GATEWAYS = {
    "EXT_P02": {"OUT": "P02A", "IN": "P02B"},
    "EXT_P03": {"OUT": [956, 1042], "IN": [400, 563]},
    "EXT_P06": {"OUT": [956, 1042], "IN": [400, 563]},
}
HALF_HOURS = [("07:00", 0), ("07:30", 0), ("08:00", 1), ("08:30", 1)]
HOURS = ["07:00-08:00", "08:00-09:00"]


def build_periods(stats_path=None, calibration_path=None):
    stats_path = stats_path or PROCESSED_DIR / "zone_stats.csv"
    calibration_path = calibration_path or INPUT_DIR / "calibration.csv"
    with calibration_path.open(encoding="utf-8-sig", newline="") as stream:
        calibration = {
            row["id"]: [float(row[hour]) for hour in HOURS]
            for row in csv.DictReader(stream)
        }
    with stats_path.open(encoding="utf-8-sig", newline="") as stream:
        zones = [(row["zone"], float(row["res_share"]), float(row["work_share"]))
                 for row in csv.DictReader(stream)]
    if not zones or len({zone for zone, _, _ in zones}) != len(zones):
        raise ValueError("Нужны непустые уникальные зоны")
    shares = [value for _, res, work in zones for value in (res, work)]
    if any(not math.isfinite(x) or x < 0 for x in shares):
        raise ValueError("Доли зон должны быть конечными и неотрицательными")
    sum_res = math.fsum(res for _, res, _ in zones)
    sum_work = math.fsum(work for _, _, work in zones)
    if not sum_res or not sum_work:
        raise ValueError("Нужны положительные суммы жилых и рабочих весов")

    periods = {}
    for name, hour in HALF_HOURS:
        internal = INTERNAL_TRIPS[HOURS[hour]] / 2
        departures, arrivals = {}, {}
        for gate, flows in GATEWAYS.items():
            out_flow, in_flow = flows["OUT"], flows["IN"]
            out_flow = calibration[out_flow] if isinstance(out_flow, str) else out_flow
            in_flow = calibration[in_flow] if isinstance(in_flow, str) else in_flow
            departures[gate] = in_flow[hour] / 2
            arrivals[gate] = out_flow[hour] / 2

        total_departures = internal + math.fsum(arrivals.values())
        total_arrivals = internal + math.fsum(departures.values())
        for zone, res, work in zones:
            departures[zone] = total_departures * res / sum_res
            arrivals[zone] = total_arrivals * work / sum_work

        # До выгрузки flows.csv дробные значения не округляем.
        values = list(departures.values()) + list(arrivals.values())
        if any(not math.isfinite(x) or x < 0 for x in values):
            raise ValueError(f"Некорректный спрос за {name}")
        if not math.isclose(math.fsum(departures.values()),
                            math.fsum(arrivals.values()), rel_tol=1e-9):
            raise ValueError(f"Не сходится баланс за {name}")
        periods[name] = {"departures": departures, "arrivals": arrivals}
    return periods


def main():
    periods = build_periods()
    PROCESSED_DIR.mkdir(exist_ok=True, parents=True)
    (PROCESSED_DIR / "periods.json").write_text(
        json.dumps(periods, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    for name, demand in periods.items():
        print(name, "отправления:", round(math.fsum(demand["departures"].values()), 3),
              "прибытия:", round(math.fsum(demand["arrivals"].values()), 3))


if __name__ == "__main__":
    main()
