"""Regenerate the endpoint index at the end of docs/frontend-handoff.md from the live OpenAPI schema.

    cd backend && uv run poe endpoints

The index is the one part of that file that goes stale by itself: every sprint adds routes. This
reads them from the running code, so it cannot disagree with the API, and rewrites only the
section after the "## 8. Endpoint index" heading.
"""

import collections
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "asknget"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "asknget.settings")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + str(ROOT / "backend" / "asknget" / "db.sqlite3"))

import django  # noqa: E402

django.setup()
from asknget.api import api  # noqa: E402

HEADING = "## 8. Endpoint index (generated from the OpenAPI schema)"
target = ROOT / "docs" / "frontend-handoff.md"

schema = api.get_openapi_schema()
rows = collections.defaultdict(list)
for path, operations in schema["paths"].items():
    for method, op in operations.items():
        if method in ("get", "post", "put", "patch", "delete"):
            rows[(op.get("tags") or ["other"])[0]].append((path if path.startswith("/api/") else "/api/v1" + path, method.upper(), "bearer" if op.get("security") else "-", (op.get("summary") or "").strip()))

total = sum(len(v) for v in rows.values())
order = sorted(rows, key=lambda t: (t.startswith("admin"), t.startswith("seller"), t))
out = [HEADING, "", f"{total} operations in {len(rows)} groups. Regenerate with `uv run poe endpoints` (from `backend/`) after changing routes.", ""]
for tag in order:
    out += [f"#### {tag}", "", "| Method | Path | Auth | What it does |", "|---|---|---|---|"]
    out += [f"| {m} | `{full}` | {a} | {summary.replace('|', '/')} |" for full, m, a, summary in sorted(rows[tag])]
    out.append("")

text = target.read_text(encoding="utf-8")
head = text[: text.index(HEADING)]
target.write_text(head + "\n".join(out).rstrip("\n") + "\n", encoding="utf-8", newline="\n")
print(f"{total} operations in {len(rows)} groups -> {target.relative_to(ROOT)}")
