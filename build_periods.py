"""
Считает, сколько машин отправляется и прибывает в каждой зоне
за каждые полчаса. Результат пишется в periods.json (блок `periods` для model.py).

"""
import csv
import json



# Поездки внутри города (из одной внутренней зоны в другую), авто в час.
# ЗАГЛУШКА: подбирается калибровкой.
INTERNAL_TRIPS = {"07:00-08:00": 4000, "08:00-09:00": 4000}

# Внешние ворота: сколько машин в час выезжает из города (OUT) и въезжает (IN).
# Первое число - час 07-08, второе - час 08-09.
# Для EXT_P02 числа взяты из calibration.csv (точки P02A и P02B).
# Для EXT_P03 и EXT_P06 чисел нет, поэтому ЗАГЛУШКА: 30% от P02.
GATEWAYS = {
    "EXT_P02": {"OUT": "P02A", "IN": "P02B"},        # берём из calibration.csv
    "EXT_P03": {"OUT": [956, 1042], "IN": [400, 563]},  # ЗАГЛУШКА
    "EXT_P06": {"OUT": [956, 1042], "IN": [400, 563]},  # ЗАГЛУШКА
}

# ---------------- РАСЧЁТ ----------------

# 1. Читаем замеры: calibration[точка] = [поток 07-08, поток 08-09]
calibration = {}
with open("calibration.csv", encoding="utf-8-sig") as f:
    for row in csv.DictReader(f):
        calibration[row["id"]] = [float(row["07:00-08:00"]), float(row["08:00-09:00"])]

# 2. Читаем доли зон: (зона, доля жителей, доля рабочих мест)
zones = []
with open("zone_stats.csv", encoding="utf-8-sig") as f:
    for row in csv.DictReader(f):
        zones.append((row["zone"], float(row["res_share"]), float(row["work_share"])))

# чтобы доли в сумме давали ровно 1
sum_res = sum(z[1] for z in zones)
sum_work = sum(z[2] for z in zones)

# 3. Для каждых полчаса считаем отправления и прибытия
# (название периода, какой это час: 0 = 07-08, 1 = 08-09)
half_hours = [("07:00", 0), ("07:30", 0), ("08:00", 1), ("08:30", 1)]
hour_names = ["07:00-08:00", "08:00-09:00"]

periods = {}
for name, hour in half_hours:
    # Поток в час делим пополам: получасы считаем одинаковыми
    def half(value_per_hour):
        return value_per_hour / 2

    # Сколько ездит внутри города за получас
    internal = half(INTERNAL_TRIPS[hour_names[hour]])

    departures = {}
    arrivals = {}
    total_in = 0
    total_out = 0

    # Внешние ворота: въезд = отправления ворот, выезд = прибытия ворот
    for gate, flows in GATEWAYS.items():
        out_flow = flows["OUT"]
        in_flow = flows["IN"]
        if isinstance(out_flow, str):   # название точки -> берём замер
            out_flow = calibration[out_flow]
            in_flow = calibration[in_flow]
        departures[gate] = half(in_flow[hour])
        arrivals[gate] = half(out_flow[hour])
        total_in += departures[gate]
        total_out += arrivals[gate]

    # Внутренние зоны:
    #   отправляются: поездки внутри города + все, кто уезжает наружу
    #   прибывают:    поездки внутри города + все, кто приезжает снаружи
    all_departures = internal + total_out
    all_arrivals = internal + total_in
    for zone, res_share, work_share in zones:
        departures[zone] = all_departures * res_share / sum_res
        arrivals[zone] = all_arrivals * work_share / sum_work

    # Округляем до сотых
    departures = {k: round(v, 2) for k, v in departures.items()}
    arrivals = {k: round(v, 2) for k, v in arrivals.items()}
    periods[name] = {"departures": departures, "arrivals": arrivals}

# 4. Сохраняем и проверяем баланс (суммы должны почти совпадать)
with open("periods.json", "w", encoding="utf-8") as f:
    json.dump(periods, f, ensure_ascii=False, indent=2)

print("Готово: periods.json")
for name, p in periods.items():
    print(name, "отправления:", round(sum(p["departures"].values()), 1),
          "прибытия:", round(sum(p["arrivals"].values()), 1))