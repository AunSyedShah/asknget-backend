# Deploying to the EC2 test server (no Docker)

A public test deployment on one small Ubuntu server (about 900 MB RAM, 6.7 GB disk, no domain, HTTP only). Docker stays the way the
project runs on a developer's machine and is the plan for the later AWS move; this server runs the same code **without** it, to fit
the memory. Everything here lives in `deploy/ec2/` and `.github/workflows/`.

## How it fits together

```
browser ── http://<public address> ──▶ nginx :80
                                         ├─ /                          React build (static files, unknown paths → index.html)
                                         ├─ /api/  /django-admin/  /static/ ──▶ gunicorn 127.0.0.1:8000 ──▶ Postgres 127.0.0.1
                                         └─ /media/                    uploads from /var/lib/asknget/media
systemd timers (no resident process): embed products every 2 min · deliver notifications every minute · database dump nightly
```

- The browser calls the API on the **same origin** (`/api/v1/...`), so there is no CORS and no mixed content. The backend listens only
  on `127.0.0.1`.
- **No Redis and no realtime service.** The live bell (WebSocket) is off; the frontend polls `/summary`.
- Only the launch market is on: `ACTIVE_MARKETS=ZA` is applied **once**, at the first deploy (`manage.py set_markets ZA`). Later
  changes are yours (`set_markets ZA PK` or Django admin → Countries) and a deploy never undoes them.
- Django admin is at **`/django-admin/`** (setting `DJANGO_ADMIN_URL`), because `/admin/` belongs to the React app.

## Who may do what

| Who | Can | Cannot |
|---|---|---|
| Backend CI key | run `deploy-backend` with a git sha and a `release.tgz` on stdin | get a shell, run anything else |
| Frontend CI key | run `deploy-frontend` with a git sha and a `frontend.tgz` on stdin | get a shell, run anything else |
| `deploy` user | write release folders, restart `asknget-web` (one sudo rule) | change the deploy scripts (root-owned), change nginx |
| `asknget` user (no login) | run the API and the timers, write uploads | log in |
| The server admin (`ubuntu`, the key the CEO holds) | everything | keep this key out of GitHub |

## One-time setup

1. **AWS console (CEO or team lead):**
   - Security group: inbound **TCP 80** from anywhere (and keep 22). 443 later, with a domain.
   - Ask for an **Elastic IP**: the public address changes whenever the server is stopped and started. Until then, if it changes,
     edit the four host lines in `/opt/asknget/backend/.env` and restart the API.
   - The server is small: `t3.micro`, 2 vCPU, 908 MB RAM, 6.7 GB disk. It will work for a test but with little room. If the disk or
     the memory is ever resized, nothing here changes.
2. **Make two deploy keys on your own computer** (never reuse the server key):
   ```bash
   ssh-keygen -t ed25519 -N "" -C "asknget backend CI"  -f asknget-backend-deploy
   ssh-keygen -t ed25519 -N "" -C "asknget frontend CI" -f asknget-frontend-deploy
   ```
   The `.pub` files go to the server (next step); the private files go into GitHub secrets, then delete them from your computer.
3. **Copy the setup files to the server and run them** (from the repository root; `<key>` is the server key the CEO sent):
   ```bash
   scp -i <key> -r deploy/ec2 ubuntu@<public address>:/tmp/ec2
   ssh -i <key> ubuntu@<public address>
   ```
   Then, on the server, paste the contents of the two `.pub` files into the command:
   ```bash
   sudo bash /tmp/ec2/setup.sh --backend-key "ssh-ed25519 AAAA... asknget backend CI" --frontend-key "ssh-ed25519 AAAA... asknget frontend CI"
   ```
   It installs nginx, Postgres with pgvector, uv and Python 3.13, creates the users and folders, writes `/opt/asknget/backend/.env`
   with fresh secrets (first run only), installs the systemd units and the nginx site, and hardens SSH. It does not deploy code.
   It is safe to run again; it never overwrites `.env`.
4. **Keep a copy of `FIELD_ENCRYPTION_KEY`** from `/opt/asknget/backend/.env` somewhere outside the server. Without it the encrypted
   seller data cannot be read after a rebuild.
5. **GitHub, backend repo:** Settings → Environments → new environment **`ec2-test`**, with secrets
   `EC2_HOST` (the public address), `EC2_SSH_KEY` (the private backend key), `EC2_KNOWN_HOSTS` (the output of
   `ssh-keyscan -t ed25519 <public address>`; compare its fingerprint with the one in the instance's console output before trusting it).
   **Frontend repo:** the same environment name, with the *frontend* private key, and add the workflow from
   `deploy/ec2/frontend-deploy.workflow.example.yml` as `.github/workflows/deploy-ec2.yml`.
6. **First deploy:** merge to `main` in the backend repo (the workflow runs the tests, packages the release, sends it, migrates,
   seeds the lookup tables, switches the market, restarts, and smoke-tests `/api/v1/health` and `/api/v1/regions`). Then the same in
   the frontend repo. Open `http://<public address>/`.

## Every day

- **Deploy:** push or merge to `main`. A failed health check puts the previous release back (migrations are not rolled back).
- **Logs:** `sudo journalctl -u asknget-web -f` · `sudo tail -f /var/log/nginx/error.log` · `systemctl list-timers 'asknget-*'`
- **Roll back by hand:** `ln -sfn /opt/asknget/backend/releases/<folder> /opt/asknget/backend/current && sudo systemctl restart asknget-web`
  (the frontend: `ln -sfn .../frontend/releases/<folder> .../frontend/current`, no restart needed). Three releases are kept.
- **Create the first staff account:** `sudo -u asknget bash -c 'cd /opt/asknget/backend/current/asknget && set -a && . ../.env && set +a && /opt/asknget/backend/venv/bin/python manage.py createsuperuser'`
- **Backups:** a nightly dump goes to `/var/backups/asknget` (7 days) **on the same disk**: it protects against a bad migration, not
  against losing the server. Copy dumps off the server if the data starts to matter.
- **Memory:** `free -m`. The API is capped (`MemoryMax=520M`), Postgres is tuned small, and 2 GB of swap is the safety net.

## Moving to Docker later

Only the setup changes. The CI test job, the Docker image build (`ci.yml`, built on every push so it stays working), the nginx routing
rules and the settings are the same. Replace `deploy-backend` / `deploy-frontend` with an image pull and `compose up -d`; the
`deploy/nginx.conf` and `compose.yaml` already exist for that stack.

## Adding HTTPS (needed by the frontend)

Needs a hostname that points at the server (here `asknget.co.za`, with `www.` redirecting to it), port 80 open (Let's Encrypt checks the
domain over HTTP) and, for visitors, inbound TCP **443** in the security group. Two stages, both by re-running `setup.sh` (safe to repeat):

1. **Certificate and HTTPS, HTTP keeps working** (port 443 does not need to be open yet):
   ```bash
   sudo bash /tmp/ec2/setup.sh --domain asknget.co.za --email you@example.com --accept-letsencrypt-tos
   ```
   Installs certbot, gets one certificate for the bare name and `www.`, adds the HTTPS server blocks, a renewal timer and a reload hook,
   and adds the names to `ALLOWED_HOSTS` and the https origins to `CSRF_TRUSTED_ORIGINS`. The script never accepts Let's Encrypt's terms
   on its own: `--accept-letsencrypt-tos` has to be passed. Done on 2026-10-29: certificate valid to 2027-01-27, `certbot renew --dry-run`
   succeeds, every route answers over HTTPS on the server, `www` redirects to the bare domain.
2. **Once `https://<domain>/` loads from outside** (443 open), turn on the redirect and the https links:
   ```bash
   sudo bash /tmp/ec2/setup.sh --domain asknget.co.za --email you@example.com --accept-letsencrypt-tos --https-redirect
   ```
   Port 80 then answers 301 to `https://<domain>` for every name (including the bare IP), and `STOREFRONT_URL` / `PORTAL_URL` become `https://`.
   Do not do this before 443 is reachable: every visitor would be sent to a port that does not answer.

The frontend needs no change (it uses relative URLs). nginx is split into shared rules (`snippets/asknget-app.conf`), the catch-all
(`site-ip.conf`) and one block per domain (`site-domain.conf.tmpl`), so another market's domain is one more `--domain` run.

## Known limits of this setup

- **The React app does not work over plain HTTP on a public address.** Its login storage encrypts tokens with the browser's Web Crypto API
  (`src/api/authTokens.js`), which browsers provide only on HTTPS pages and on `localhost`. Over `http://<public ip>/` every request that goes
  through the auth layer fails with "Secure authentication storage is unavailable in this browser context" (found 2026-10-28; the API itself
  answers fine). It works through the SSH tunnel because `localhost` counts as secure. The fix is HTTPS, which needs a hostname: see
  "Adding HTTPS" below.
- HTTP only until there is a domain; passwords and tokens cross the internet in clear text. Use it for testing, with test data.
- One server, one disk, no automatic recovery if it dies.
- The first test run on Postgres in CI (`test-postgres`) is advisory because the suite has only been run on SQLite so far.
- `uv` is installed from Astral's current installer (unpinned) and Postgres 18 is whatever Ubuntu ships; pin both if you want identical rebuilds.
- Ubuntu 26.04 ships `postgresql-18-pgvector` (0.8.1, checked on the test server). If a future image does not, `setup.sh` falls back to the
  PostgreSQL project's repository and stops with a clear message if pgvector still cannot be installed (search needs it).

## First run, 2026-10-28 (what was actually verified)

Run by hand on the test server with the same scripts CI uses (`setup.sh`, `deploy-backend`, `deploy-frontend`), before port 80 was open:
the API, Swagger, the Django admin at `/django-admin/`, static files, the React build, the SPA fallback (`/login`, `/admin/dashboard`),
both timers, register / login / `/me` / `/cart`, and the access rules (a customer gets 403 on `/admin/*` and `/seller/*`). Fixed on the way:
setup started from a folder `deploy` could not read; the first-deploy market switch was skipped (`readlink -f`); gunicorn's control
file needed a writable `HOME`. Not yet verified: the GitHub workflows, SSH deploys with the forced-command keys, the Postgres CI job.

**To look at the server before port 80 is open**, tunnel it: `ssh -i <key> -N -L 8080:127.0.0.1:80 ubuntu@<public address>`, then open
`http://localhost:8080/`.

**Disk and memory right after the first deploy:** 4.8 GB of 6.7 GB used (2 GB of it is the swap file), about 230 MB of RAM available with
the API idle. It runs, with little room: do not add services to this box.
