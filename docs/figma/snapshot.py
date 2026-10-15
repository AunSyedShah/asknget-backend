"""Snapshot the Figma design as diff-able text so design changes are visible in git.

    python docs/figma/snapshot.py            # refresh docs/figma/screens/*.txt + manifest.json
    python docs/figma/snapshot.py --check    # exit 1 if the Figma file changed since the last snapshot
    python docs/figma/snapshot.py --nodes    # same refresh, via the per-node endpoint (see below)
    python docs/figma/snapshot.py --nodes --only "Analytics Overview" Chat
    python docs/figma/snapshot.py --index    # just refresh the frame list (cheapest call there is)
    python docs/figma/snapshot.py --render "Analytics Overview" 779:6848   # PNG of a frame
    python docs/figma/snapshot.py --file https://figma.com/design/<key>/…   # a different file
    uv run --with zstandard python docs/figma/snapshot.py --from-fig asknget.fig   # offline, no API

Run it before planning or implementing anything that touches a screen; then
`git diff docs/figma/` shows precisely which labels, fields, options or flows moved.

Requires FIGMA_TOKEN (personal access token) in the repo-root .env, or in the environment — never commit it.

Rate limits: `--check` only calls /versions (cheap). A full snapshot calls
/files/{key}, which on a Starter plan is limited to a handful of calls per day —
a 429 there can lock us out for days.

`/files/{key}/nodes` sits on a **separate** budget, so `--nodes` often works while
`/files/{key}` is locked — but it is cost-based, not per-call: on Starter, roughly
10 full frames is enough to trigger a multi-day 429. So:

- `--index` refreshes only the frame list (names + node ids, one tiny call). Saved
  to page-index.json, which is how you find out what the designer added.
- `--only <name|id> …` snapshots just those frames. This is the one to use while
  rate-limited: pick the two or three screens that matter today.
- `--nodes` sweeps everything, batch by batch. Expect it to stop mid-way with a 429;
  it keeps what it fetched and never deletes a screen it has not replaced, so
  re-running it later resumes rather than starting over.

`--from-fig` skips the API entirely: Figma → File → *Save local copy* gives a .fig,
and `docs/figma/kiwi.py` decodes its scene graph straight to the same screens/*.txt.
No token, no rate limit, no plan tier. The catch is that an export is a point-in-time
copy — `--check` still needs the API to notice that the live file has moved on.

`--render-all` renders every frame in page-index.json (run `--index` first) into renders/, batched (25 per call: the
/images budget counts calls), resumable and recorded in renders/manifest.json; it is how a frontend session gets pictures
without calling Figma itself. Run it against the owner's FRESH DUPLICATE (`--file <duplicate url>`): the original sits on
someone else's Starter plan and locks after a few calls, while a copy on the owner's own plan has its own budget.

`--render` is the last resort: `/images` is a **third** budget and has kept working
while both of the others were locked. It gives a picture, not text — but a picture
of a screen is enough to plan against, and it is the only way in when the JSON
endpoints are cold. Renders land in docs/figma/renders/ (git-ignored).

Nothing here deletes a snapshot file until its replacement has been written.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# The design file. Override with FIGMA_FILE_KEY, or `--file <key|url>`, when the
# design moves (a *copy* into another team gets a new key; a move keeps the old one).
# Rate limits follow the plan the file lives on, so a file in a Starter space stays
# Starter-metered however good your own plan is.
DEFAULT_FILE_KEY = "dAGMv3hUJXkyW4QmJPw20Q"
FILE_KEY = os.environ.get("FIGMA_FILE_KEY", DEFAULT_FILE_KEY)
PAGE_NAME = "Web UI Design"
PAGE_NODE = "0:1"          # the "Web UI Design" canvas; used by the --nodes path
NODE_BATCH = 2             # frames per /nodes call; each full frame is ~250 KiB of JSON
HERE = Path(__file__).parent
SCREENS_DIR = HERE / "screens"
MANIFEST = HERE / "manifest.json"
PAGE_INDEX = HERE / "page-index.json"
RENDERS_DIR = HERE / "renders"

# Layers that repeat on every screen and add noise to diffs.
SKIP_NAMES = re.compile(r"^(Navbar|Footer|Breadcrums|Header ?\d*|Top ?Bar|Nav|Icons)$", re.I)
GENERIC_NAMES = re.compile(r"^(Frame|Group|Rectangle|Vector|Ellipse|Line|Text) ?\d*$")


def file_key_from(value: str) -> str:
    """Accept a raw key or any Figma URL (/file/<key>/…, /design/<key>/…)."""
    match = re.search(r"/(?:file|design|board)/([A-Za-z0-9]+)", value)
    return match.group(1) if match else value.strip()


def _token_from_dotenv() -> str | None:
    """FIGMA_TOKEN from the repo-root .env (git-ignored), without needing python-dotenv.

    It wins over the process environment on purpose: an app started before the token
    was rotated keeps handing out the old value, and Figma answers 401 forever."""
    env_file = Path(__file__).resolve().parents[2] / ".env"
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        line = line.strip().removeprefix("export ").strip()
        if line.startswith("FIGMA_TOKEN="):
            return line.split("=", 1)[1].strip().strip("\"'") or None
    return None


def _token() -> str:
    token = _token_from_dotenv() or os.environ.get("FIGMA_TOKEN")
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


class RateLimited(Exception):
    def __init__(self, path: str, retry_after: int, tier: str):
        self.path, self.retry_after, self.tier = path, retry_after, tier
        super().__init__(f"429 on {path} (plan tier: {tier}); retry after ~{retry_after // 3600} h {retry_after % 3600 // 60} min")


def _get(path: str) -> dict:
    req = urllib.request.Request(f"https://api.figma.com/v1{path}", headers={"X-Figma-Token": _token()})
    try:
        with urllib.request.urlopen(req, timeout=180) as res:
            return json.loads(res.read().decode("utf-8-sig"))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RateLimited(path, int(e.headers.get("Retry-After", "0") or 0), e.headers.get("x-figma-plan-tier", "?")) from None
        if e.code in (403, 404):
            sys.exit(f"{e.code} on {path} — wrong file key, or this token's account cannot open that file. "
                     "Pass the file's Figma URL with --file, and make sure the account owning FIGMA_TOKEN has access.")
        raise


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
    """Latest version id via /versions — a cheap endpoint. `/files/{key}` sits on
    Figma's low-tier rate limit for Starter plans and can be locked for days."""
    latest = _get(f"/files/{FILE_KEY}/versions?page_size=1")["versions"][0]
    return {"version": latest["id"], "lastModified": latest["created_at"], "by": latest["user"]["handle"]}


def _write_screen(frame: dict, components: dict) -> dict:
    lines: list[str] = []
    _outline(frame, components, 0, lines)
    deduped = [l for i, l in enumerate(lines) if i == 0 or l != lines[i - 1]]
    slug = re.sub(r"[^\w]+", "_", frame["name"]).strip("_")
    path = SCREENS_DIR / f"{slug}__{frame['id'].replace(':', '-')}.txt"
    path.write_text("\n".join(deduped) + "\n", encoding="utf-8")
    return {"name": frame["name"], "node_id": frame["id"], "file": path.name, "lines": len(deduped)}


def _wanted(frame: dict) -> bool:
    """Top-level nodes that count as a screen.

    GROUP belongs here: the designer draws dialogs (Media / Pricing / Shipping /
    Review & Publish / Category Request popups) as groups, and leaving them out
    silently lost five screens. Generic auto-names ("Group 5673") are not screens.
    """
    name = frame.get("name", "")
    if SKIP_NAMES.match(name) or GENERIC_NAMES.match(name):
        return False
    return frame.get("type") in ("FRAME", "INSTANCE", "GROUP", "SECTION", "COMPONENT")


def _screens(nodes: list[dict], children=lambda n: n.get("children", [])) -> list[dict]:
    """The screens among a page's top-level nodes, looking inside SECTIONs.

    A SECTION is how the designer files screens ("Seller Portal" holds every seller
    screen since 2026-09-29), not a screen itself. Treating it as one wrote the
    whole portal into a single file and pruned all 35 real screens as "gone".
    `children` lets the .fig path pass its own child lookup.
    """
    out = []
    for node in nodes:
        if node.get("type") == "SECTION":
            out.extend(_screens(children(node), children))
        elif _wanted(node):
            out.append(node)
    return out


def _prune(keep: set[str]) -> None:
    """Drop screen files whose frame is gone. Only called after a complete sweep —
    a partial run must never delete anything."""
    for old in SCREENS_DIR.glob("*.txt"):
        if old.name not in keep:
            old.unlink()
            print(f"  - {old.name} (frame no longer in the design)")


def _write_manifest(version: str, last_modified: str, screens: list[dict], *, via: str) -> None:
    MANIFEST.write_text(
        json.dumps(
            {
                "file_key": FILE_KEY,
                "page": PAGE_NAME,
                "version": version,
                "lastModified": last_modified,
                "fetched_via": via,
                "screens": screens,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Snapshot: {len(screens)} screens @ version {version} ({last_modified}) via {via}")
    print("Now run:  git diff --stat docs/figma/")


def snapshot_via_file() -> None:
    """One big /files/{key} call — cheapest in requests, first to be rate-limited."""
    doc = _get(f"/files/{FILE_KEY}")
    page = next(p for p in doc["document"]["children"] if p["name"] == PAGE_NAME)
    components = doc.get("components", {})
    SCREENS_DIR.mkdir(parents=True, exist_ok=True)
    screens = [_write_screen(f, components) for f in _screens(page["children"])]
    _prune({s["file"] for s in screens})
    _write_manifest(doc["version"], doc["lastModified"], screens, via="files")


# --------------------------------------------------------------------------- #
# Offline: .fig
# --------------------------------------------------------------------------- #

# .fig calls components SYMBOL; the REST API calls them COMPONENT. Everything else
# lines up, so the two paths produce byte-identical screen files.
FIG_TYPE_ALIASES = {"SYMBOL": "COMPONENT"}


def snapshot_from_fig(path, replace: bool = False) -> int:
    """Screens from a local .fig export.

    Fidelity caveat: a .fig stores component **instances by reference** — the node
    has no children and its content lives in the SYMBOL definition plus
    `symbolOverrides`, while the REST API returns instances already expanded. So
    screens built from components (product cards, nav) come out much thinner here.
    Existing screen files are kept unless --replace is passed; this path is for
    picking up frames the API has not given us yet.
    """
    import kiwi

    _schema, root = kiwi.load(path)
    changes = root.get("nodeChanges") or []

    def key(guid):
        return (guid["sessionID"], guid["localID"]) if guid else None

    nodes, children = {}, {}
    for change in changes:
        guid = key(change.get("guid"))
        if guid is None:
            continue
        nodes[guid] = change
        parent = change.get("parentIndex") or {}
        pkey = key(parent.get("guid"))
        if pkey is not None:
            # `position` is a fractional index; lexicographic order is canvas order.
            children.setdefault(pkey, []).append((parent.get("position", ""), guid))

    canvas = next((n for n in changes if n.get("type") == "CANVAS" and n.get("name") == PAGE_NAME), None)
    if canvas is None:
        found = [n.get("name") for n in changes if n.get("type") == "CANVAS"]
        sys.exit(f"No page named {PAGE_NAME!r} in {path} (pages: {', '.join(map(str, found))})")

    symbols = {key(n["guid"]): n.get("name", "") for n in changes if n.get("type") == "SYMBOL"}

    def kids(guid):
        return [nodes[g] for _pos, g in sorted(children.get(guid, []), key=lambda t: t[0]) if g in nodes]

    def outline(node, depth, out):
        name = node.get("name", "") or ""
        typ = FIG_TYPE_ALIASES.get(node.get("type"), node.get("type"))
        cname = symbols.get(key((node.get("symbolData") or {}).get("symbolID")), "") if typ == "INSTANCE" else ""
        if SKIP_NAMES.match(name) or (cname and SKIP_NAMES.match(cname.split("|")[0].strip())):
            return
        pad = "  " * depth
        if typ == "TEXT":
            text = (node.get("textData") or {}).get("characters", "").strip().replace("\n", " / ")
            if text:
                out.append(f"{pad}T: {text}")
        elif typ == "INSTANCE":
            if ":" in name and not cname.startswith(("Input Fields", "Button")):
                return  # an icon instance
            out.append(f"{pad}[I:{name}" + (f" | {cname}" if cname and cname != name else "") + "]")
        elif typ in ("FRAME", "GROUP", "COMPONENT", "SECTION") and not GENERIC_NAMES.match(name):
            out.append(f"{pad}[{name}]")
        for child in kids(key(node["guid"])):
            outline(child, depth + 1, out)

    frames = _screens(kids(key(canvas["guid"])), lambda n: kids(key(n["guid"])))
    SCREENS_DIR.mkdir(parents=True, exist_ok=True)
    screens, skipped = [], []
    for frame in frames:
        lines: list[str] = []
        outline(frame, 0, lines)
        deduped = [l for i, l in enumerate(lines) if i == 0 or l != lines[i - 1]]
        guid = key(frame["guid"])
        node_id = f"{guid[0]}:{guid[1]}"
        slug = re.sub(r"[^\w]+", "_", frame["name"]).strip("_")
        out_path = SCREENS_DIR / f"{slug}__{node_id.replace(':', '-')}.txt"
        if out_path.exists() and not replace:
            skipped.append(out_path.name)   # the API version expands instances; keep it
            continue
        out_path.write_text(chr(10).join(deduped) + chr(10), encoding="utf-8")
        screens.append({"name": frame["name"], "node_id": node_id, "file": out_path.name, "lines": len(deduped)})
        print(f"  + {out_path.name} ({len(deduped)} lines)")

    exported = ""
    try:
        import json as _json
        import zipfile as _zipfile

        with _zipfile.ZipFile(path) as z:
            exported = _json.loads(z.read("meta.json")).get("exported_at", "")
    except Exception:
        pass

    if skipped:
        print(f"  kept {len(skipped)} existing screen file(s) — pass --replace to overwrite them from the .fig")
    print(f"{len(screens)} screen file(s) written from {Path(path).name} (exported {exported or 'unknown'})")
    print("The manifest is left alone: a .fig cannot expand instances, so it is a partial source.")
    print("Reminder: a .fig is a point-in-time export — `--check` still compares against the live file.")
    return 0


def refresh_index() -> list[dict]:
    """One small call: every frame on the page, name + node id. This is how a newly
    drawn screen is discovered without reading the whole file."""
    listing = _get(f"/files/{FILE_KEY}/nodes?ids={PAGE_NODE}&depth=2")   # 2: screens inside SECTIONs
    canvas = listing["nodes"][PAGE_NODE]["document"]
    if canvas.get("name") != PAGE_NAME:
        sys.exit(f"Node {PAGE_NODE} is {canvas.get('name')!r}, not the {PAGE_NAME!r} page — update PAGE_NODE.")
    frames = [{"name": f["name"], "node_id": f["id"], "type": f["type"]} for f in _screens(canvas.get("children", []))]
    known = {s["node_id"]: s["name"] for s in json.loads(MANIFEST.read_text(encoding="utf-8"))["screens"]} if MANIFEST.exists() else {}
    PAGE_INDEX.write_text(json.dumps({"fetched_at": current_version(), "frames": frames}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    added = [f for f in frames if f["node_id"] not in known]
    renamed = [(known[f["node_id"]], f["name"]) for f in frames if f["node_id"] in known and known[f["node_id"]] != f["name"]]
    print(f"{len(frames)} frames on the page (snapshot has {len(known)}) -> {PAGE_INDEX.name}")
    for f in added:
        print(f"  NEW      {f['name']}  ({f['node_id']})")
    for before, after in renamed:
        print(f"  RENAMED  {before}  ->  {after}")
    return frames


def _select(frames: list[dict], only: list[str]) -> list[dict]:
    if not only:
        return frames
    wanted, picked = [o.lower() for o in only], []
    for f in frames:
        if f["name"].lower() in wanted or f["node_id"] in only:
            picked.append(f)
    missing = [o for o in only if o.lower() not in {f["name"].lower() for f in picked} and o not in {f["node_id"] for f in picked}]
    if missing:
        sys.exit(f"Not on the page: {', '.join(missing)}. Run --index to see the frame list.")
    return picked


def snapshot_via_nodes(only: list[str] | None = None) -> int:
    """Same output as the /files path, fetched frame by frame. Writes as it goes and
    keeps everything it managed to fetch when the budget runs out."""
    frames = _select(refresh_index(), only or [])
    live = current_version()
    SCREENS_DIR.mkdir(parents=True, exist_ok=True)

    screens, done = [], 0
    try:
        for start in range(0, len(frames), NODE_BATCH):
            batch = frames[start:start + NODE_BATCH]
            data = _get(f"/files/{FILE_KEY}/nodes?ids={','.join(f['node_id'] for f in batch)}")
            components = data.get("components", {})
            for frame in batch:
                payload = data["nodes"].get(frame["node_id"])
                if not payload:
                    print(f"  ! {frame['name']} ({frame['node_id']}) returned nothing — skipped")
                    continue
                screens.append(_write_screen(payload["document"], components))
                print(f"  + {screens[-1]['file']} ({screens[-1]['lines']} lines)")
            done = min(start + NODE_BATCH, len(frames))
    except RateLimited as limited:
        print(f"\n{limited}")
        print(f"Kept {done}/{len(frames)} frames; existing screens untouched. Re-run later to continue,")
        print("or use --only to spend the next budget on the screens that matter.")
        return 1

    if only:
        print(f"\nRefreshed {len(screens)} frame(s); manifest left alone (partial snapshot).")
        print("Now run:  git diff --stat docs/figma/")
        return 0
    _prune({s["file"] for s in screens})
    _write_manifest(live["version"], live["lastModified"], screens, via="nodes")
    return 0


def render(targets: list[str], scale: float = 1.0) -> int:
    """PNG of one or more frames (name from page-index.json, or a raw node id).

    Uses /images, which is metered separately from /files and /files/{key}/nodes —
    so this still works when a full snapshot is rate-limited. `0:1` renders the whole
    page: useless for reading labels, handy for seeing what the designer added.
    """
    index = json.loads(PAGE_INDEX.read_text(encoding="utf-8"))["frames"] if PAGE_INDEX.exists() else []
    by_name = {f["name"].lower(): f["node_id"] for f in index}
    ids, labels = [], {}
    for target in targets:
        node_id = target if ":" in target else by_name.get(target.lower())
        if not node_id:
            sys.exit(f"Unknown frame {target!r}. Run --index first, or pass the node id (right-click the frame in "
                     "Figma -> Copy link to selection; the node-id is in the URL).")
        ids.append(node_id)
        labels[node_id] = re.sub(r"[^\w]+", "-", target).strip("-").lower()

    data = _get(f"/images/{FILE_KEY}?ids={','.join(ids)}&format=png&scale={scale}")
    if data.get("err"):
        sys.exit(f"Figma could not render: {data['err']}")
    RENDERS_DIR.mkdir(parents=True, exist_ok=True)
    for node_id, url in (data.get("images") or {}).items():
        if not url:
            print(f"  ! {node_id}: no image returned")
            continue
        dest = RENDERS_DIR / f"{labels.get(node_id, node_id.replace(':', '-'))}.png"
        urllib.request.urlretrieve(url, dest)
        print(f"  {node_id} -> {dest.relative_to(HERE.parent.parent)} ({dest.stat().st_size / 1024:.0f} KiB)")
    return 0


RENDER_BATCH = 25          # frames per /images call. The budget counts CALLS, not frames (Starter: a 429 came on the second call of a
                           # sweep with batches of 8), so batch big; the pictures themselves come from Figma's file store, which is not metered
RENDER_MANIFEST = RENDERS_DIR / "manifest.json"


def _safe(name: str) -> str:
    return re.sub(r"[^\w]+", "-", name.strip()).strip("-") or "frame"


def render_all(scale: float = 1.0, force: bool = False, only: list[str] | None = None) -> int:
    """PNG of EVERY frame in page-index.json (or just `only`), in batches, resumable.

    Files are `renders/<Frame-name>__<node-id>.png` and `renders/manifest.json` records what was rendered, at which
    scale and for which version of the design. A frame already rendered for the live version at this scale is skipped,
    so a re-run after a 429 (or after the designer changes the file) only fetches what is missing or stale.
    Run `--index` first so the frame list is current. This is what lets a frontend session work from pictures
    without calling Figma at all: hand it the `renders/` folder.
    """
    frames = json.loads(PAGE_INDEX.read_text(encoding="utf-8"))["frames"] if PAGE_INDEX.exists() else []
    if not frames:
        sys.exit("No frame list: run `--index` first.")
    if only:
        wanted = {o.lower() for o in only}
        frames = [f for f in frames if f["node_id"] in only or f["name"].strip().lower() in wanted]
    version = current_version()["version"]
    RENDERS_DIR.mkdir(parents=True, exist_ok=True)
    done = json.loads(RENDER_MANIFEST.read_text(encoding="utf-8")) if RENDER_MANIFEST.exists() else {}
    todo = [f for f in frames if force or not (
        (entry := done.get(f["node_id"])) and entry["version"] == version and entry["scale"] == scale and (RENDERS_DIR / entry["file"]).exists())]
    print(f"{len(frames)} frames, {len(frames) - len(todo)} already rendered for this version, {len(todo)} to fetch (scale {scale}).")

    def save() -> None:
        RENDER_MANIFEST.write_text(json.dumps(done, indent=2, ensure_ascii=False), encoding="utf-8")

    failed = []
    try:
        for start in range(0, len(todo), RENDER_BATCH):
            batch = todo[start:start + RENDER_BATCH]
            data = _get(f"/images/{FILE_KEY}?ids={','.join(f['node_id'] for f in batch)}&format=png&scale={scale}")
            if data.get("err"):
                print(f"  ! batch {start // RENDER_BATCH + 1}: {data['err']}")
                failed += [f["name"] for f in batch]
                continue
            for frame in batch:
                url = (data.get("images") or {}).get(frame["node_id"])
                if not url:
                    print(f"  ! {frame['name'].strip()} ({frame['node_id']}): no image returned (too large?)")
                    failed.append(frame["name"])
                    continue
                file = f"{_safe(frame['name'])}__{frame['node_id'].replace(':', '-')}.png"
                urllib.request.urlretrieve(url, RENDERS_DIR / file)
                done[frame["node_id"]] = {"name": frame["name"].strip(), "file": file, "scale": scale, "version": version}
                print(f"  {frame['name'].strip()} -> {file} ({(RENDERS_DIR / file).stat().st_size / 1024:.0f} KiB)")
            save()
            time.sleep(2)
    except RateLimited as limited:
        save()
        print(f"\n{limited}\nStopped; progress is saved. Run the same command again later and it resumes.")
        return 2
    save()
    if failed:
        print(f"\n{len(failed)} frame(s) not rendered: {', '.join(n.strip() for n in failed)}")
        return 1
    print(f"\nDone: {len([f for f in frames if f['node_id'] in done])} frames in {RENDERS_DIR.name}/ (manifest.json lists them).")
    return 0


def snapshot() -> int:
    """Prefer the single-call path; fall back to per-node when it is rate-limited."""
    try:
        snapshot_via_file()
        return 0
    except RateLimited as limited:
        print(f"{limited}\n-> /files is locked; falling back to the per-node endpoint.")
        return snapshot_via_nodes()


def check() -> int:
    if not MANIFEST.exists():
        print("No snapshot yet — run without --check first.")
        return 1
    saved = json.loads(MANIFEST.read_text(encoding="utf-8"))
    live = current_version()
    if live["version"] == saved["version"]:
        print(f"Design unchanged since snapshot ({saved['lastModified']}).")
        return 0
    print(f"DESIGN CHANGED: snapshot {saved['lastModified']} -> live {live['lastModified']} by {live['by']}.")
    source = saved.get("content_from")
    if source:
        print()
        print("The original is rate-limited (it lives on someone else's Starter plan), so snapshots come")
        print("from a duplicate. Ask for a fresh one — in Figma: right-click the original -> Duplicate, open the")
        print("copy, and paste its URL. Then:")
        print("  python docs/figma/snapshot.py --file <url of the new copy>")
        print(f"(the previous copy was {source.get('file_key')}; node ids survive duplication, so diffs stay meaningful)")
    else:
        print("Re-run the snapshot and diff.")
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="only compare versions; exit 1 if changed")
    ap.add_argument("--nodes", action="store_true", help="snapshot via /files/{key}/nodes (works when /files is rate-limited)")
    ap.add_argument("--index", action="store_true", help="only refresh page-index.json: which frames exist, what is new or renamed")
    ap.add_argument("--only", nargs="+", metavar="FRAME", help="with --nodes: snapshot just these frames (name or node id)")
    ap.add_argument("--render", nargs="+", metavar="FRAME", help="save a PNG of these frames (separate rate limit — works when the rest is locked)")
    ap.add_argument("--scale", type=float, default=1.0, help="with --render / --render-all: image scale (0.2 for an overview, 1 to read text)")
    ap.add_argument("--render-all", action="store_true", help="PNG of every frame in page-index.json, batched and resumable (renders/manifest.json); re-run to fetch only what is missing or changed")
    ap.add_argument("--force", action="store_true", help="with --render-all: fetch again even when already rendered for this version")
    ap.add_argument("--file", metavar="KEY_OR_URL", help="snapshot a different file (paste its Figma URL); also FIGMA_FILE_KEY")
    ap.add_argument("--from-fig", metavar="PATH", help="add screens from a local .fig export (no token, no rate limit)")
    ap.add_argument("--replace", action="store_true", help="with --from-fig: overwrite existing screen files too (the API versions are usually richer)")
    args = ap.parse_args()
    if args.from_fig:
        sys.exit(snapshot_from_fig(args.from_fig, args.replace))
    if args.file:
        FILE_KEY = file_key_from(args.file)
        print(f"file: {FILE_KEY}")
    if args.check:
        sys.exit(check())
    if args.render_all:
        sys.exit(render_all(args.scale, args.force, args.only))
    if args.render:
        sys.exit(render(args.render, args.scale))
    if args.index:
        refresh_index()
        sys.exit(0)
    sys.exit(snapshot_via_nodes(args.only) if (args.nodes or args.only) else snapshot())
