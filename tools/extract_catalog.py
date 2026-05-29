#!/usr/bin/env python3
"""Regenerate ``pystellina/data/catalog.json`` from a Singularity APK.

The bundled catalog is the factual subset (designations, coordinates, magnitude, recommended
capture settings, and the object's name/description) of the app's catalog.

Coordinates etc. come from ``assets/catalog/objects.json`` inside the APK. Names and
descriptions live in compiled resources, so decode them first with apktool and pass the
resulting ``strings.xml``::

    apktool d -f -s -o out singularity.apk
    python tools/extract_catalog.py singularity.apk --strings out/res/values/strings.xml
"""

from __future__ import annotations

import argparse
import html
import json
import re
import zipfile
from pathlib import Path

KEEP = [
    "id", "idMessier", "idNgc", "idIc", "constellation", "category", "type",
    "ra", "de", "magnitude", "grade", "duration", "gain", "exposure", "orientation", "size",
    "histogramEnabled", "histogramLow", "histogramMedium", "histogramHigh",
    "backgroundEnabled", "backgroundPolyorder",
]  # fmt: skip

OUT = Path(__file__).resolve().parent.parent / "pystellina" / "data" / "catalog.json"


def _unescape(s: str) -> str:
    s = s.replace("\\'", "'").replace('\\"', '"').replace("\\n", "\n").replace("\\@", "@")
    return html.unescape(s).strip()


def _merge_names(objects: list[dict], strings_xml: Path) -> tuple[int, int]:
    xml = strings_xml.read_text(encoding="utf-8")
    pairs = dict(re.findall(r'<string name="([^"]+)">(.*?)</string>', xml, re.DOTALL))
    named = desc = 0
    for o in objects:
        key = "objects_" + o["id"].replace("-", "_")
        if (title := pairs.get(key + "_title")) is not None:
            o["name"] = _unescape(title)
            named += 1
        if (description := pairs.get(key + "_description")) is not None:
            o["description"] = _unescape(description)
            desc += 1
    return named, desc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk", help="path to the Singularity APK")
    parser.add_argument("--strings", help="apktool-decoded res/values/strings.xml for names")
    args = parser.parse_args()

    with zipfile.ZipFile(args.apk) as apk:
        raw = json.loads(apk.read("assets/catalog/objects.json"))
    trimmed = [{k: o[k] for k in KEEP if k in o} for o in raw]
    if args.strings:
        named, desc = _merge_names(trimmed, Path(args.strings))
        print(f"merged {named} names, {desc} descriptions")
    OUT.write_text(json.dumps(trimmed, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    fixed = sum("ra" in o and "de" in o for o in trimmed)
    print(f"wrote {len(trimmed)} objects ({fixed} with fixed coords) -> {OUT}")


if __name__ == "__main__":
    main()
