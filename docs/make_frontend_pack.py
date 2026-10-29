"""Assemble a SELF-CONTAINED pack for the frontend session: instructions and design pictures, nothing pointing back at this repository.

    backend/.venv/Scripts/python docs/make_frontend_pack.py "<frontend folder>"

The frontend session then needs no access to the backend repository at all. It writes only inside the frontend folder, and only to:

    CLAUDE.md                 standing instructions (written only if it does not exist yet; never overwritten)
    docs/backend/             README.md (the brief), api-contract.md, api-changes.md, PACK_VERSION.txt   (replaced on every run)
    design/                   frames/ (customer, seller, admin, INDEX.md, manifest.json), outlines/, Icons.png, .gitignore (replaced on every run)

`--force-claude` also replaces an existing CLAUDE.md (only when the template changed and the frontend session has not customised it).

Re-run it whenever the backend session says the docs changed, or after a new Figma export has been catalogued
(`docs/figma/catalog_exports.py`). Everything the frontend session writes itself (code, BACKEND_ASKS.md, DESIGN_NOTES.md, api/) is left alone.

Before writing, every generated document is scanned for secrets (Figma and Twilio tokens, connection strings, keys, the demo password);
the run stops without writing if one is found.
"""

import json
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
FRAMES = ROOT / "Figma" / "frames"
OUTLINES = HERE / "figma" / "screens"
ICONS = ROOT / "Figma" / "Icons.png"
PACK = HERE / "frontend-pack"

SECRET_PATTERNS = {
    "a Figma token": r"figd_[A-Za-z0-9_\-]{16,}",
    "a Twilio account SID": r"\bAC[0-9a-f]{32}\b",
    "a database URL with a password": r"postgres(?:ql)?://[^\s/:@]+:[^\s/@]+@",
    "a private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "a Django or encryption key": r"(?:SECRET_KEY|FIELD_ENCRYPTION_KEY)\s*[=:]\s*\S{12,}",
    "the demo password": r"DemoPass",
    "a Slack or API token": r"\b(?:xox[bap]-|sk-[A-Za-z0-9]{20,})",
}


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return "unknown"


def sections(text: str):
    """[(heading line, body)] split at '## ' headings; the first item is the preamble with heading ''."""
    parts = re.split(r"(?m)^(?=## )", text)
    out = []
    for part in parts:
        head = part.split("\n", 1)[0] if part.startswith("## ") else ""
        out.append((head, part))
    return out


def api_contract() -> str:
    """frontend-handoff.md without what is for the backend session or already in the brief (sections 1, 2, 6, 7), re-pointed."""
    keep = re.compile(r"^## (3|4|5|5a|5b|8)\.")
    text = (HERE / "frontend-handoff.md").read_text(encoding="utf-8")
    body = "".join(part for head, part in sections(text) if keep.match(head))
    body = body.replace("frontend-handoff.md", "api-contract.md")
    body = body.replace("(`docs/data-model.md` Q74–Q83)", "(planned by the backend)")
    body = body.replace("Regenerate with `uv run poe endpoints` (from `backend/`) after changing routes.",
                        "The live OpenAPI document is always the current list; this index is from when the pack was generated.")
    header = ("# The API contract\n\n"
              "> Generated from the backend session's handoff file. The live OpenAPI document is the authority; this explains it. "
              "Section numbers are the backend's (3 the contract, 4 recent changes, 5 to 5b what was built since, 8 the endpoint index). "
              "**Mentions of files such as `docs/data-model.md`, `backend/README.md` or `docs/figma/` refer to the backend repository, which you cannot read:** "
              "ask the owner if you need something from them. The responsive-design defaults are in the brief, section 2.\n\n")
    return header + body


def api_changes() -> str:
    return (HERE / "api-changes.md").read_text(encoding="utf-8").replace("frontend-handoff.md", "api-contract.md")


def brief(manifest: dict) -> str:
    frames = manifest["frames"]
    counts = {s: sum(1 for f in frames if f["surface"] == s) for s in ("customer", "seller", "admin")}
    surfaces = json.loads((HERE / "figma" / "surfaces.json").read_text(encoding="utf-8"))
    totals = {s: len(surfaces[s]) for s in counts}
    text = (PACK / "brief.md").read_text(encoding="utf-8")
    missing = manifest.get("missing", [])
    missing_text = ("**Missing screens (the owner has not exported them yet; skip them until `design/frames/manifest.json` lists them): "
                    + ", ".join(m["name"] for m in missing) + ".**") if missing else "No screen is missing."
    return (text.replace("{{GENERATED}}", date.today().isoformat()).replace("{{COMMIT}}", git_commit())
            .replace("{{COUNTS}}", ", ".join(f"{s} {counts[s]} of {totals[s]}" for s in counts) + f" ({sum(counts.values())} of {sum(totals.values())} screens)")
            .replace("{{MISSING}}", missing_text))


def scan(documents: dict) -> list[str]:
    problems = []
    for name, text in documents.items():
        for label, pattern in SECRET_PATTERNS.items():
            if re.search(pattern, text):
                problems.append(f"{name}: looks like {label}")
    return problems


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force_claude = "--force-claude" in sys.argv
    if len(args) != 1:
        print(__doc__)
        return 2
    target = Path(args[0]).resolve()
    if ROOT in target.parents or target == ROOT:
        print("The frontend folder must be OUTSIDE the backend repository (that is the point: no shared access).")
        return 2
    manifest_path = FRAMES / "manifest.json"
    if not manifest_path.exists():
        print("No Figma/frames/manifest.json: run docs/figma/catalog_exports.py first.")
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    documents = {"README.md": brief(manifest), "api-contract.md": api_contract(), "api-changes.md": api_changes(),
                 "CLAUDE.md": (PACK / "CLAUDE.md").read_text(encoding="utf-8"),
                 "INDEX.md": (FRAMES / "INDEX.md").read_text(encoding="utf-8")}
    problems = scan(documents)
    if problems:
        print("Stopped: a document contains something that looks like a secret; nothing was written.\n  " + "\n  ".join(problems))
        return 1

    target.mkdir(parents=True, exist_ok=True)
    backend_docs = target / "docs" / "backend"
    if backend_docs.exists():
        shutil.rmtree(backend_docs)
    backend_docs.mkdir(parents=True)
    (backend_docs / "README.md").write_text(documents["README.md"], encoding="utf-8")
    (backend_docs / "api-contract.md").write_text(documents["api-contract.md"], encoding="utf-8")
    (backend_docs / "api-changes.md").write_text(documents["api-changes.md"], encoding="utf-8")
    (backend_docs / "PACK_VERSION.txt").write_text(f"generated {date.today().isoformat()} from backend commit {git_commit()}\n", encoding="utf-8")

    design = target / "design"
    for sub in ("frames", "outlines"):
        if (design / sub).exists():
            shutil.rmtree(design / sub)
    design.mkdir(parents=True, exist_ok=True)
    shutil.copytree(FRAMES, design / "frames")
    index_md = design / "frames" / "INDEX.md"
    index_md.write_text(re.sub(r"Generated by `[^`]*`; do not edit\.", "Generated by the backend session; do not edit.", index_md.read_text(encoding="utf-8")), encoding="utf-8")
    shutil.copytree(OUTLINES, design / "outlines")
    if ICONS.exists():
        shutil.copyfile(ICONS, design / "Icons.png")

    (design / ".gitignore").write_text("# 38 MB of pictures the owner regenerates; delete this line to commit them\nframes/\n", encoding="utf-8")

    claude_md = target / "CLAUDE.md"
    wrote_claude = force_claude or not claude_md.exists()
    if wrote_claude:
        claude_md.write_text(documents["CLAUDE.md"], encoding="utf-8")

    pictures = sum(1 for _ in (design / "frames").rglob("*.png"))
    outlines = sum(1 for _ in (design / "outlines").glob("*.txt"))
    print(f"Pack written to {target}")
    print(f"  docs/backend/: README.md, api-contract.md, api-changes.md, PACK_VERSION.txt")
    print(f"  design/: {pictures} pictures, {outlines} text outlines" + (", Icons.png" if ICONS.exists() else ""))
    print(f"  CLAUDE.md: {'written' if wrote_claude else 'already there, left alone (use --force-claude to replace it)'}")
    print("  design/.gitignore keeps the 38 MB of pictures out of git; delete it to commit them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
