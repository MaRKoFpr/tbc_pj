"""Serve the project boundary over an interactive map on localhost."""

import argparse
import csv
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INPUT_DIR = PROJECT_ROOT / "data" / "input"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUT_DIR = PROJECT_ROOT / "outputs"

HTML = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Долгопрудный · карта для районирования</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css">
<script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
body { margin: 0; height: 100dvh; display: flex; flex-direction: column; font: 15px system-ui; }
header { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 20px; padding: 10px 16px; }
h1 { margin: 0; font-size: 17px; }
button { font: inherit; }
#map { flex: 1; background: #edf1f3; }
#status { font-variant-numeric: tabular-nums; }
</style>
</head>
<body>
<header>
<h1>Долгопрудный</h1>
<label><input id="boundary" type="checkbox" checked> Граница проекта</label>
<label>Разметка
<select id="zoning">
<option value="manual">Исходный · парк и Центральный отдельно</option>
<option value="grouped" selected>Основной · парк и Центральный вместе</option>
<option value="detailed">Подробный · лицей и Первомайская отдельно</option>
<option value="">Без зон</option>
</select>
</label>
<button id="reset" type="button">Показать весь полигон</button>
<output id="status">Нажми на карту, чтобы увидеть координаты</output>
</header>
<div id="map" aria-label="Интерактивная карта Долгопрудного"></div>
<script>
const status = document.getElementById("status");
if (typeof L === "undefined") {
    status.textContent = "Не загрузилась библиотека карты. Проверь подключение к интернету и обнови страницу.";
} else {
    const map = L.map("map");
    const tiles = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
    });
    tiles.on("tileerror", () => {
        status.textContent = "Часть подложки OSM не загрузилась. Попробуй обновить страницу.";
    });
    tiles.addTo(map);
    const boundary = L.polygon(__BOUNDARY__, {
        color: "#7139ae", weight: 3, dashArray: "8 5", fillOpacity: 0.035, interactive: false
    }).addTo(map);
    const variants = __ZONING_VARIANTS__;
    if (variants.manual) {
        const zones = L.geoJSON(variants.grouped || variants.manual, {
            style: (feature) => ({color: ({Z01: "#245d9e", Z02: "#087d73", Z03: "#9a5318", Z04: "#9a3768", Z05: "#647719", Z06: "#6b508f", Z07: "#ae7622", Z08: "#375eb9", Z09: "#c45242", Z10: "#4e8d55"})[feature.properties.zone_id] || "#6b508f", weight: 3, fillOpacity: 0.13}),
            onEachFeature: (feature, layer) => {
                const title = document.createElement("span");
                title.textContent = feature.properties.zone_id + " · " + feature.properties.name;
                layer.bindTooltip(title, {sticky: true});
            }
        }).addTo(map);
        document.getElementById("zoning").addEventListener("change", (event) => {
            zones.clearLayers();
            const variant = variants[event.target.value];
            if (variant) zones.addData(variant);
        });
    }
    const fit = () => map.fitBounds(boundary.getBounds(), {padding: [30, 30]});
    fit();
    document.getElementById("reset").addEventListener("click", fit);
    document.getElementById("boundary").addEventListener("change", (event) => {
        if (event.target.checked) boundary.addTo(map);
        else boundary.remove();
    });
    map.on("click", ({latlng}) => {
        status.textContent = `${latlng.lat.toFixed(6)}, ${latlng.lng.toFixed(6)}`;
    });
}
</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export-only", action="store_true")
    args = parser.parse_args()
    with (INPUT_DIR / "area.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = sorted(csv.DictReader(stream), key=lambda row: int(row["номер"]))
    boundary = [[float(row["широта"]), float(row["долгота"])] for row in rows]
    zones_path = PROCESSED_DIR / "zones_manual.geojson"
    zones = json.loads(zones_path.read_text(encoding="utf-8-sig")) if zones_path.exists() else None
    variants_path = PROCESSED_DIR / "zoning_variants.json"
    variants = json.loads(variants_path.read_text(encoding="utf-8")) if variants_path.exists() else {}
    variants["manual"] = zones
    page = (HTML.replace("__BOUNDARY__", json.dumps(boundary))
            .replace("__ZONING_VARIANTS__", json.dumps(variants)).encode("utf-8"))

    OUTPUT_DIR.mkdir(exist_ok=True)
    (OUTPUT_DIR / "project_boundary_map.html").write_bytes(page)
    if args.export_only:
        return

    class MapHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

    with HTTPServer(("127.0.0.1", 8765), MapHandler) as server:
        print("http://127.0.0.1:8765/", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
