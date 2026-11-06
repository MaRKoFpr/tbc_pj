"""Check that every OSM highway has its complete node sequence in the snapshot."""

import argparse
from pathlib import Path
from xml.etree import ElementTree as ET


def read_highways(path):
    root = ET.parse(path).getroot()
    if root.tag != "osm":
        raise ValueError(f"{path}: expected an OSM XML file")
    nodes = {node.attrib["id"]: (float(node.attrib["lon"]), float(node.attrib["lat"]))
             for node in root.iterfind("node")}
    ways = {
        way.attrib["id"]: tuple(ref.attrib["ref"] for ref in way.iterfind("nd"))
        for way in root.iterfind("way")
        if any(tag.get("k") == "highway" for tag in way.iterfind("tag"))
    }
    broken = [way for way, refs in ways.items() if len(refs) < 2 or any(ref not in nodes for ref in refs)]
    print(f"{path}: {len(ways)} highway ways, {len(broken)} with incomplete geometry")
    if broken:
        raise ValueError(f"Examples of incomplete ways: {', '.join(broken[:10])}")
    return ways, nodes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--overlap", nargs=4, type=float, metavar=("WEST", "SOUTH", "EAST", "NORTH"))
    args = parser.parse_args()
    ways, nodes = read_highways(args.snapshot)
    if args.compare:
        other, other_nodes = read_highways(args.compare)
        shared = ways.keys() & other.keys()
        changed = sum(ways[way] != other[way] for way in shared)
        print(f"Shared IDs: {len(shared)}, different node sequences: {changed}")
        moved = sum(nodes[node] != other_nodes[node] for node in nodes.keys() & other_nodes.keys())
        print(f"Shared nodes with different coordinates: {moved}")
        if args.overlap:
            west, south, east, north = args.overlap

            def inside(point):
                lon, lat = point
                return west <= lon <= east and south <= lat <= north

            for source, coordinates, reference in ((ways, nodes, other), (other, other_nodes, ways)):
                unique = source.keys() - reference.keys()
                in_overlap = sum(any(inside(coordinates[node]) for node in source[way])
                                 for way in unique)
                print(f"Unique highway IDs: {len(unique)}, with a node in the overlap: {in_overlap}")


if __name__ == "__main__":
    main()
