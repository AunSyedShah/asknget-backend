"""run_db.py prod|staging <manage.py args>  -- direct URL from the root .env; never prints it."""
import os, re, subprocess, sys
from pathlib import Path
ROOT = Path("D:/aunsyedshah/programming/beandband/asknget")
which, args = sys.argv[1], sys.argv[2:]
name = {"prod": "DATABASE_URL_UNPOOLED", "staging": "NEON_STAGING_URL"}[which]
m = re.search(r"^" + name + r"=(.*)$", (ROOT / ".env").read_text(encoding="utf-8"), re.M)
url = m.group(1).strip().strip('"').strip("'") if m else ""
assert url.startswith("postgres") and "-pooler" not in url, "need the direct (unpooled) URL"
url += ("&" if "?" in url else "?") + "keepalives=1&keepalives_idle=10&keepalives_interval=5&keepalives_count=5"
env = dict(os.environ, DATABASE_URL=url, DEBUG=os.environ.get("RUN_DEBUG", "0"), DB_POOL="0", ZEAL_ENABLED="0")
sys.exit(subprocess.call(["uv", "run", "--project", str(ROOT / "backend"), "python", "manage.py", *args],
                         cwd=str(ROOT / "backend" / "asknget"), env=env))
