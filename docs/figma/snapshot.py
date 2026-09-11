"""Snapshot the Figma design as diff-able text so design changes are visible in git.

    python docs/figma/snapshot.py            # refresh docs/figma/screens/*.txt + manifest.json
    python docs/figma/snapshot.py --check    # exit 1 if the Figma file changed since the last snapshot

Run it before planning or implementing anything that touches a screen; then
`git diff docs/figma/` shows precisely which labels, fields, options or flows moved.

Requires FIGMA_TOKEN in the environment (personal access token) — never commit it.
"""

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

FILE_KEY = "dAGMv3hUJXkyW4QmJPw20Q"
PAGE_NAME = "Web UI Design"
HERE = Path(__file__).parent
SCREENS_DIR = HERE / "screens"
MANIFEST = HERE / "manifest.json"

# Layers that repeat on every screen and add noise to diffs.
SKIP_NAMES = re.compile(r"^(Navbar|Footer|Breadcrums|Header ?\d*|Top ?Bar|Nav|Icons)$", re.I)
GENERIC_NAMES = re.compile(r"^(Frame|Group|Rectangle|Vector|Ellipse|Line|Text) ?\d*$")


def _token() -> str:
    token = os.environ.get("FIGMA_TOKEN")
    if not token and sys.platform == "win32":
        # Fall back to the user-level env var when the shell predates `setx`.
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                token = winreg.QueryValueEx(k, "FIGMA_TOKEN")[0]
        except OSError:
            token = None
    if not token:
        sys.exit("FIGMA_TOKEN is not set")
    return token


def _get(path: str) -> dict:
    req = urllib.request.Request(f"https://api.figma.com/v1{path}", headers={"X-Figma-Token": _token()})
    with urllib.request.urlopen(req, timeout=120) as res:
        return json.loads(res.read().decode("utf-8-sig"))


def _outline(node: dict, components: dict, depth: int, out: list[str]) -> None:
    name, typ = node.get("name", ""), node.get("type")
    cname = components.get(node.get("componentId", ""), {}).get("name", "") if typ == "INSTANCE" else ""
    if SKIP_NAMES.match(name) or SKIP_NAMES.match(cname.split("|")[0].strip()):
        return
    pad = "  " * depth
    if typ == "TEXT":
        text = node.get("characters", "").strip().replace("\n", " / ")
        if text:
            out.append(f"{pad}T: {text}")
    elif typ == "INSTANCE":
        is_icon = ":" in name and not cname.startswith(("Input Fields", "Button"))
        if is_icon:
            return
        out.append(f"{pad}[I:{name}" + (f" | {cname}" if cname and cname != name else "") + "]")
    elif typ in ("FRAME", "GROUP", "COMPONENT", "SECTION") and not GENERIC_NAMES.match(name):
        out.append(f"{pad}[{name}]")
    for child in node.get("children", []):
        _outline(child, components, depth + 1, out)


def current_version() -> dict:
    meta = _get(f"/files/{FILE_KEY}?depth=1")
    return {"version": meta["version"], "lastModified": meta["lastModified"], "name": meta["name"]}


def snapshot() -> None:
    doc = _get(f"/files/{FILE_KEY}")
    page = next(p for p in doc["document"]["children"] if p["name"] == PAGE_NAME)
    components = doc.get("components", {})

    SCREENS_DIR.mkdir(parents=True, exist_ok=True)
    for old in SCREENS_DIR.glob("*.txt"):
        old.unlink()

    screens = []
    for frame in page["children"]:
        if frame["type"] not in ("FRAME", "INSTANCE") or SKIP_NAMES.match(frame["name"]):
            continue
        lines: list[str] = []
        _outline(frame, components, 0, lines)
        deduped = [l for i, l in enumerate(lines) if i == 0 or l != lines[i - 1]]
        slug = re.sub(r"[^\w]+", "_", frame["name"]).strip("_")
        path = SCREENS_DIR / f"{slug}__{frame['id'].replace(':', '-')}.txt"
        path.write_text("\n".join(deduped) + "\n", encoding="utf-8")
        screens.append({"name": frame["name"], "node_id": frame["id"], "file": path.name, "lines": len(deduped)})

    MANIFEST.write_text(
        json.dumps(
            {
                "file_key": FILE_KEY,
                "page": PAGE_NAME,
                "version": doc["version"],
                "lastModified": doc["lastModified"],
                "screens": screens,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Snapshot: {len(screens)} screens @ version {doc['version']} ({doc['lastModified']})")
    print("Now run:  git diff --stat docs/figma/")


def check() -> int:
    if not MANIFEST.exists():
        print("No snapshot yet — run without --check first.")
        return 1
    saved = json.loads(MANIFEST.read_text(encoding="utf-8"))
    live = current_version()
    if live["version"] == saved["version"]:
        print(f"Design unchanged since snapshot ({saved['lastModified']}).")
        return 0
    print(f"DESIGN CHANGED: snapshot {saved['lastModified']} -> live {live['lastModified']}. Re-run snapshot and diff.")
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="only compare versions; exit 1 if changed")
    args = ap.parse_args()
    sys.exit(check() if args.check else snapshot())
