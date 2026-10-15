"""Turn a folder of PNGs exported from the Figma app into one clean, named set of frames: no API, no token, no rate limit.

    backend/.venv/Scripts/python docs/figma/catalog_exports.py [EXPORT_FOLDER]      # default: <repo root>/Figma
    uv run --with pillow python docs/figma/catalog_exports.py Figma                 # without the backend venv

How to export (Figma desktop app, one minute): open the file, click the canvas, Ctrl+A, in the right panel under Export add
PNG at 1x, click "Export N layers", save into the folder. Do it again after the designer changes the file.

What this does, writing to `<folder>/frames/` (your originals are never touched):
  * A section exported as ONE huge image (Figma does that for a SECTION, e.g. "Seller Portal") is cut into its screens at exact
    1x pixels. The grid's reading order is listed in `seller-portal-order.json`; if the number of screens found differs, the
    tool says so and does not guess.
  * Every other PNG is matched to a frame of `page-index.json` by its name. When two frames share a name, Figma saves the second
    as "Name-1.png"; the tool pairs them by order in the index, and `export-name-overrides.json` corrects the odd ones.
  * `frames/<Frame-name>__<node-id>.png` for each, and `frames/manifest.json`: node id, name, file, size, where it came from,
    plus the frames of the design that are still MISSING (export them, or ask the backend session to render them).
Run `python docs/figma/snapshot.py --index` first so `page-index.json` is current.
"""

import json
import re
import shutil
import sys
from pathlib import Path

from PIL import Image

Image.MAX_IMAGE_PIXELS = None
HERE = Path(__file__).parent
ROOT = HERE.parent.parent
SECTION_WIDTH = 5000        # an image wider than this is a section, not a screen
CANVAS = 68                 # the flat grey Figma paints behind a section


def safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", name.strip()).strip("-")


def _runs(flags, min_gap):
    out, start, last = [], None, None
    for i, on in enumerate(flags):
        if on:
            if start is None:
                start = i
            elif i - last > min_gap:
                out.append((start, last + 1))
                start = i
            last = i
    if start is not None:
        out.append((start, last + 1))
    return out


def find_screens(path: Path, scale: int = 4, gap: int = 12):
    """Rectangles (x, y, w, h at 1x) of the screens lying on the section's flat canvas, in reading order."""
    gray = Image.open(path).convert("L")
    small = gray.reduce(scale)
    mask = small.point(lambda v: 255 if abs(v - CANVAS) > 6 else 0)
    sw, sh = small.size
    boxes = []
    rows = [mask.crop((0, y, sw, y + 1)).getbbox() is not None for y in range(sh)]
    for y0, y1 in _runs(rows, gap // scale):
        band = mask.crop((0, y0, sw, y1))
        cols = [band.crop((x, 0, x + 1, y1 - y0)).getbbox() is not None for x in range(sw)]
        for x0, x1 in _runs(cols, gap // scale):
            bx0, by0, bx1, by1 = mask.crop((x0, y0, x1, y1)).getbbox()
            boxes.append((((x0 + bx0) * scale), ((y0 + by0) * scale), (bx1 - bx0) * scale, (by1 - by0) * scale))
    boxes = [b for b in boxes if b[2] > 600 and b[3] > 600]          # drops corner marks, the title banner and border strips
    return sorted(boxes, key=lambda b: (round(b[1] / 400), b[0]))


def main() -> int:
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "Figma"
    out = folder / "frames"
    index = json.loads((HERE / "page-index.json").read_text(encoding="utf-8"))["frames"]
    overrides = json.loads((HERE / "export-name-overrides.json").read_text(encoding="utf-8"))
    section = json.loads((HERE / "seller-portal-order.json").read_text(encoding="utf-8"))
    by_id = {f["node_id"]: f["name"].strip() for f in index}
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    entries, notes, claimed = [], [], set()

    def add(node_id, name, image, source):
        file = f"{safe(name)}__{node_id.replace(':', '-')}.png"
        if isinstance(image, Path):
            shutil.copyfile(image, out / file)
            with Image.open(out / file) as im:
                size = im.size
        else:
            image.save(out / file, optimize=True)
            size = image.size
        entries.append({"node_id": node_id, "name": name, "file": file, "w": size[0], "h": size[1], "source": source})
        claimed.add(node_id)

    pngs = sorted(folder.glob("*.png"))
    sections = []
    for png in pngs:
        with Image.open(png) as im:
            if im.width > SECTION_WIDTH:
                sections.append(png)

    # 1. sections exported as one image: cut them into screens
    for png in sections:
        if png.stem != section["section_image"]:
            notes.append(f"{png.name}: a section image with no layout list; add one like seller-portal-order.json")
            continue
        boxes = find_screens(png)
        if len(boxes) != len(section["order"]):
            notes.append(f"{png.name}: found {len(boxes)} screens but seller-portal-order.json lists {len(section['order'])}; not sliced "
                         "(the designer changed the section: re-check the order and update the list)")
            continue
        big = Image.open(png).convert("RGB")
        for (x, y, w, h), node_id in zip(boxes, section["order"]):
            m = 10
            region = big.crop((max(x - m, 0), max(y - m, 0), min(x + w + m, big.width), min(y + h + m, big.height)))
            exact = Image.eval(region.convert("L"), lambda v: 255 if abs(v - CANVAS) > 3 else 0).getbbox()
            add(node_id, by_id[node_id], region.crop(exact), f"{png.name} (sliced)")

    # 2. everything else: by name; "Name-1.png" is the second frame called "Name"
    for png in pngs:
        if png in sections:
            continue
        stem = png.stem
        ov = overrides.get(stem)
        if ov and ov.get("skip"):
            notes.append(f"{png.name}: skipped ({ov['note']})")
            continue
        if ov:
            add(ov["node_id"], ov["name"], png, f"{png.name} ({ov['note'][:60]}...)" if ov.get("note") else png.name)
            continue
        m = re.match(r"^(.*?)(?:-(\d+))?$", stem)
        base, nth = m.group(1).strip(), int(m.group(2) or 0)
        candidates = [f["node_id"] for f in index if f["name"].strip().lower() == base.lower() and f["node_id"] not in claimed]
        if nth >= len(candidates):
            candidates = [f["node_id"] for f in index if f["name"].strip().lower() == stem.lower() and f["node_id"] not in claimed]
            nth = 0
        if not candidates:
            notes.append(f"{png.name}: no frame with that name in page-index.json (run --index, or add an override)")
            continue
        add(candidates[nth], by_id[candidates[nth]], png, png.name)

    missing = [{"node_id": f["node_id"], "name": f["name"].strip()} for f in index if f["node_id"] not in claimed]
    entries.sort(key=lambda e: [f["node_id"] for f in index].index(e["node_id"]))
    (out / "manifest.json").write_text(json.dumps({"frames": entries, "missing": missing, "notes": notes}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(entries)} frames written to {out} ({len(index)} in the design); {len(missing)} missing.")
    for n in notes:
        print("  note:", n)
    if missing:
        print("  missing:", "; ".join(m["name"] for m in missing))
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
