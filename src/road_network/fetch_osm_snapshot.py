"""Download and merge 16 small extracts from the OpenStreetMap map API."""

import argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from xml.etree import ElementTree as ET

import requests

from .download_osm import INPUT_DIR, OSM_DIR, read_boundary


def fetch_tile(bounds):
    response = requests.get(
        "https://api.openstreetmap.org/api/0.6/map",
        params={"bbox": ",".join(f"{value:.8f}" for value in bounds)},
        headers={"User-Agent": "DolgoprudnyTransportCourseProject/1.0"},
        timeout=30,
    )
    response.raise_for_status()
    tile = ET.fromstring(response.content)
    if tile.tag != "osm":
        raise ValueError("Expected an OSM XML response")
    return tile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--buffer-m", type=float, default=250)
    parser.add_argument("--grid", type=int, default=4)
    parser.add_argument("--output", type=Path, default=OSM_DIR / "dolgoprudny.osm")
    args = parser.parse_args()
    if not 0 <= args.buffer_m < float("inf") or args.grid < 1:
        parser.error("Expected a nonnegative buffer and a positive grid size")
    boundary = read_boundary(INPUT_DIR / "area.csv")
    buffered = boundary.to_crs(boundary.estimate_utm_crs()).buffer(args.buffer_m).to_crs(boundary.crs)
    west, south, east, north = buffered.total_bounds
    xs = [west + (east - west) * i / args.grid for i in range(args.grid + 1)]
    ys = [south + (north - south) * i / args.grid for i in range(args.grid + 1)]
    boxes = [(xs[i], ys[j], xs[i + 1], ys[j + 1]) for i in range(args.grid) for j in range(args.grid)]
    items = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        for number, tile in enumerate(pool.map(fetch_tile, boxes), 1):
            for element in tile:
                if element.tag in {"node", "way", "relation"}:
                    items[element.tag, element.attrib["id"]] = element
            print(f"Tile {number}/{len(boxes)}", flush=True)

    root = ET.Element("osm", version="0.6", generator="merged OSM API map extracts")
    for kind in ("node", "way", "relation"):
        root.extend(element for (tag, _), element in items.items() if tag == kind)
    output = args.output
    output.parent.mkdir(exist_ok=True)
    ET.ElementTree(root).write(output, encoding="utf-8", xml_declaration=True)
    print(f"{len(items)} objects saved to {output}")


if __name__ == "__main__":
    main()
