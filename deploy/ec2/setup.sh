#!/usr/bin/env bash
# One-time (and safe to re-run) setup of the asknget test server: Ubuntu, no Docker.
#
#   sudo bash setup.sh --backend-key "ssh-ed25519 AAAA... asknget backend CI" \
#                      --frontend-key "ssh-ed25519 AAAA... asknget frontend CI" [--host 1.2.3.4]
#
# What it does: swap, nginx, Postgres 18 + pgvector (tuned small), the `asknget` (runs the app) and `deploy` (CI may log in, but
# only to run one script) users, uv + Python 3.13, the systemd units and timers, the nginx site, the backend .env (only if absent),
# and SSH hardening. It does NOT deploy any code: the first run of the CI workflows does that.
# Read docs/ec2-deploy.md first. Run it from a copy of the deploy/ec2 folder (setup.sh uses the files next to it).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd /        # commands run as other users (deploy, postgres) must not start in a folder they cannot read, e.g. /home/ubuntu
APP=/opt/asknget
DATA=/var/lib/asknget
BACKEND_KEY="" FRONTEND_KEY="" PUBLIC_HOST="" SWAP_MB=2048
DOMAIN="" LE_EMAIL="" ACCEPT_LE_TOS=0 HTTPS_REDIRECT=0

while [ $# -gt 0 ]; do
  case "$1" in
    --backend-key)  BACKEND_KEY="$2"; shift 2 ;;
    --frontend-key) FRONTEND_KEY="$2"; shift 2 ;;
    --host)         PUBLIC_HOST="$2"; shift 2 ;;
    --swap-mb)      SWAP_MB="$2"; shift 2 ;;
    --domain)       DOMAIN="$2"; shift 2 ;;                 # e.g. asknget.co.za: the bare name; www.<name> is added
    --email)        LE_EMAIL="$2"; shift 2 ;;               # Let's Encrypt account / expiry notices
    --accept-letsencrypt-tos) ACCEPT_LE_TOS=1; shift ;;     # explicit: the script never accepts the terms on its own
    --https-redirect) HTTPS_REDIRECT=1; shift ;;            # only once port 443 is reachable from outside: it sends every visitor to https
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
if [ -n "$DOMAIN" ] && { [ -z "$LE_EMAIL" ] || [ "$ACCEPT_LE_TOS" -ne 1 ]; }; then
  echo "--domain needs --email and --accept-letsencrypt-tos (Let's Encrypt's subscriber agreement: https://letsencrypt.org/repository/)" >&2; exit 2
fi

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33mwarning: %s\033[0m\n' "$*" >&2; }
[ "$(id -u)" -eq 0 ] || { echo "run as root: sudo bash setup.sh ..." >&2; exit 1; }
for f in nginx/asknget-app.conf nginx/asknget-upstream.conf nginx/asknget-tls.conf nginx/site-ip.conf nginx/site-domain.conf.tmpl env.template bin/deploy-backend bin/deploy-frontend systemd/asknget-web.service; do
  [ -f "$HERE/$f" ] || { echo "missing $HERE/$f: copy the whole deploy/ec2 folder" >&2; exit 1; }
done
. /etc/os-release
echo "OS: $PRETTY_NAME, RAM: $(free -m | awk '/Mem:/{print $2}') MB, free disk: $(df -h / | awk 'NR==2{print $4}')"
[ "${ID:-}" = "ubuntu" ] || warn "written for Ubuntu; this is ${ID:-unknown}"

# ----------------------------------------------------------------------------------------------------------------- swap
say "swap"
if [ "$(swapon --show --noheadings | wc -l)" -eq 0 ]; then
  fallocate -l "${SWAP_MB}M" /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "created ${SWAP_MB} MB swap"
else
  echo "swap already present"
fi
echo 'vm.swappiness=20' > /etc/sysctl.d/99-asknget.conf; sysctl -q -p /etc/sysctl.d/99-asknget.conf || true

# ------------------------------------------------------------------------------------------------------------- packages
say "packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends nginx postgresql curl ca-certificates tar openssl
[ -z "$DOMAIN" ] || apt-get install -y --no-install-recommends certbot
apt-get install -y --no-install-recommends fail2ban || warn "fail2ban is not available from the configured repositories; skipped"

# ---------------------------------------------------------------------------------------------------------------- users
say "users and folders"
id asknget >/dev/null 2>&1 || useradd --system --home-dir "$APP" --shell /usr/sbin/nologin --user-group asknget
id deploy  >/dev/null 2>&1 || useradd --create-home --shell /bin/bash deploy
usermod -aG asknget deploy
getent group systemd-journal >/dev/null && usermod -aG systemd-journal deploy || true
mkdir -p "$APP"/{bin,python,.uv-cache} "$APP"/backend/releases "$APP"/frontend/releases "$DATA/media" /var/backups/asknget
chown -R deploy:asknget "$APP"
chmod 755 "$APP" "$APP"/backend "$APP"/frontend "$APP"/python
chown asknget:asknget "$DATA" "$DATA/media"; chmod 755 "$DATA" "$DATA/media"

# --------------------------------------------------------------------------------------------------------------- postgres
say "postgres"
PGVER="$(pg_lsclusters -h | awk 'NR==1{print $1}')"
[ -n "$PGVER" ] || { echo "no Postgres cluster found" >&2; exit 1; }
echo "Postgres $PGVER"
if ! apt-get install -y --no-install-recommends "postgresql-$PGVER-pgvector"; then
  warn "postgresql-$PGVER-pgvector is not in the Ubuntu repositories; trying the PostgreSQL project's repository (apt.postgresql.org)"
  apt-get install -y --no-install-recommends postgresql-common
  /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y || true
  apt-get update -y
  apt-get install -y --no-install-recommends "postgresql-$PGVER-pgvector" \
    || { echo "pgvector could not be installed (needed for search and recommendations). Install it by hand, then re-run this script." >&2; exit 1; }
fi
PGCONF="/etc/postgresql/$PGVER/main/conf.d/asknget.conf"
cat > "$PGCONF.new" <<'EOF'
# A 1 GB box shared with the API: small and steady.
shared_buffers = 64MB
effective_cache_size = 256MB
work_mem = 4MB
maintenance_work_mem = 32MB
max_connections = 30
EOF
if ! cmp -s "$PGCONF.new" "$PGCONF" 2>/dev/null; then mv "$PGCONF.new" "$PGCONF"; systemctl restart postgresql; else rm -f "$PGCONF.new"; fi

ENV_FILE="$APP/backend/.env"
if [ -f "$ENV_FILE" ]; then
  DB_PASSWORD="$(sed -n 's#^DATABASE_URL=postgres://asknget:\([^@]*\)@.*#\1#p' "$ENV_FILE" | head -1)"
  [ -n "$DB_PASSWORD" ] || { echo "cannot read the database password from the existing $ENV_FILE; not touching the database role" >&2; exit 1; }
fi
DB_PASSWORD="${DB_PASSWORD:-$(openssl rand -hex 16)}"
runuser -u postgres -- psql -v ON_ERROR_STOP=1 -q <<EOF
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'asknget') THEN CREATE ROLE asknget LOGIN PASSWORD '$DB_PASSWORD'; END IF;
END \$\$;
ALTER ROLE asknget PASSWORD '$DB_PASSWORD';
EOF
runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_database WHERE datname='asknget'" | grep -q 1 \
  || runuser -u postgres -- createdb -O asknget asknget
runuser -u postgres -- psql -d asknget -q -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm;"
echo "database ready, extensions: vector, pg_trgm"

# ------------------------------------------------------------------------------------------------------------ uv + python
say "uv and Python 3.13"
if ! command -v uv >/dev/null 2>&1; then
  # Unpinned installer from Astral. For a pinned install, replace this line with a release download and checksum.
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh
fi
runuser -u deploy -- env UV_PYTHON_INSTALL_DIR="$APP/python" uv python install 3.13
chmod -R a+rX "$APP/python"

# ------------------------------------------------------------------------------------------------------------ backend .env
say "backend .env"
if [ -f "$ENV_FILE" ]; then
  echo "$ENV_FILE exists: left as it is"
else
  if [ -z "$PUBLIC_HOST" ]; then
    TOKEN="$(curl -fsS -m 3 -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' || true)"
    PUBLIC_HOST="$(curl -fsS -m 3 -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/public-ipv4 || true)"
  fi
  [ -n "$PUBLIC_HOST" ] || { echo "could not detect the public address: pass --host <ip-or-name>" >&2; exit 1; }
  SECRET_KEY="$(openssl rand -base64 48 | tr -d '\n/+=')"
  FERNET="$(head -c 32 /dev/urandom | base64 | tr '+/' '-_')"
  sed -e "s#__SECRET_KEY__#$SECRET_KEY#" -e "s#__PUBLIC_HOST__#$PUBLIC_HOST#g" \
      -e "s#__FIELD_ENCRYPTION_KEY__#$FERNET#" -e "s#__DB_PASSWORD__#$DB_PASSWORD#" "$HERE/env.template" > "$ENV_FILE"
  chown deploy:asknget "$ENV_FILE"; chmod 640 "$ENV_FILE"
  echo "wrote $ENV_FILE for http://$PUBLIC_HOST (new secrets generated; keep a copy of FIELD_ENCRYPTION_KEY outside the server)"
fi

# ----------------------------------------------------------------------------------------------------- scripts and units
say "deploy scripts, systemd units, sudo rule"
chown root:root "$APP/bin"; chmod 755 "$APP/bin"      # root-owned, so the deploy user cannot replace the scripts its own key is forced to run
install -m 755 -o root -g root "$HERE/bin/deploy-backend" "$HERE/bin/deploy-frontend" "$APP/bin/"
install -m 644 "$HERE"/systemd/*.service "$HERE"/systemd/*.timer /etc/systemd/system/
cat > /etc/sudoers.d/asknget-deploy <<'EOF'
# The deploy user may restart the API and nothing else.
deploy ALL=(root) NOPASSWD: /usr/bin/systemctl restart asknget-web
EOF
chmod 440 /etc/sudoers.d/asknget-deploy; visudo -cf /etc/sudoers.d/asknget-deploy >/dev/null
mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=100M\n' > /etc/systemd/journald.conf.d/asknget.conf
systemctl daemon-reload
systemctl restart systemd-journald
systemctl enable asknget-web.service asknget-embed.timer asknget-notify.timer asknget-backup.timer >/dev/null
systemctl start asknget-embed.timer asknget-notify.timer asknget-backup.timer      # they wait for the first release (ConditionPathExists)

# -------------------------------------------------------------------------------------------------------------------- nginx
say "nginx"
if [ ! -e "$APP/frontend/current" ]; then
  mkdir -p "$APP/frontend/releases/placeholder"
  printf '<!doctype html><title>asknget</title><p>Deployment in progress.</p>\n' > "$APP/frontend/releases/placeholder/index.html"
  chmod -R a+rX "$APP/frontend/releases/placeholder"
  ln -sfn "$APP/frontend/releases/placeholder" "$APP/frontend/current"; chown -h deploy:asknget "$APP/frontend/current"
fi
install -m 644 "$HERE/nginx/asknget-upstream.conf" /etc/nginx/conf.d/asknget-upstream.conf
install -m 644 "$HERE/nginx/asknget-app.conf" "$HERE/nginx/asknget-tls.conf" /etc/nginx/snippets/
install -d -m 755 /var/www/certbot
# What port 80 does: serve the site (until HTTPS is confirmed reachable), or send everyone to the HTTPS domain.
if [ "$HTTPS_REDIRECT" -eq 1 ] && [ -n "$DOMAIN" ]; then
  printf 'location / {
    return 301 https://%s$request_uri;
}
' "$DOMAIN" > /etc/nginx/snippets/asknget-http80.conf
else
  printf 'include /etc/nginx/snippets/asknget-app.conf;
' > /etc/nginx/snippets/asknget-http80.conf
fi
install -m 644 "$HERE/nginx/site-ip.conf" /etc/nginx/sites-available/asknget
ln -sfn /etc/nginx/sites-available/asknget /etc/nginx/sites-enabled/asknget
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable nginx >/dev/null; systemctl reload nginx || systemctl restart nginx

# ------------------------------------------------------------------------------------------------------------------- https
if [ -n "$DOMAIN" ]; then
  say "https for $DOMAIN"
  CERT_DIR="/etc/letsencrypt/live/$DOMAIN"
  if [ ! -s "$CERT_DIR/fullchain.pem" ]; then
    # The check runs over port 80 (the webroot above): the domain must already point at this server.
    certbot certonly --webroot -w /var/www/certbot -d "$DOMAIN" -d "www.$DOMAIN"       --email "$LE_EMAIL" --agree-tos --no-eff-email --non-interactive
  else
    echo "certificate for $DOMAIN already present"
  fi
  sed "s#__DOMAIN__#$DOMAIN#g" "$HERE/nginx/site-domain.conf.tmpl" > "/etc/nginx/sites-available/asknget-domain"
  ln -sfn /etc/nginx/sites-available/asknget-domain /etc/nginx/sites-enabled/asknget-domain
  install -d /etc/letsencrypt/renewal-hooks/deploy
  printf '#!/bin/sh
systemctl reload nginx
' > /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
  chmod 755 /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
  nginx -t
  systemctl reload nginx
  systemctl enable --now certbot.timer >/dev/null 2>&1 || true

  # The backend must know these names (and, once https is the way in, build its links with https).
  set_env() { if grep -q "^$1=" "$ENV_FILE"; then sed -i "s#^$1=.*#$1=$2#" "$ENV_FILE"; else echo "$1=$2" >> "$ENV_FILE"; fi; }
  grep -q "^ALLOWED_HOSTS=.*$DOMAIN" "$ENV_FILE" || sed -i "s#^ALLOWED_HOSTS=.*#&,$DOMAIN,www.$DOMAIN#" "$ENV_FILE"
  CSRF="$(sed -n 's/^CSRF_TRUSTED_ORIGINS=//p' "$ENV_FILE" | head -1)"
  case ",$CSRF," in *",https://$DOMAIN,"*) ;; *) set_env CSRF_TRUSTED_ORIGINS "${CSRF:+$CSRF,}https://$DOMAIN,https://www.$DOMAIN" ;; esac
  if [ "$HTTPS_REDIRECT" -eq 1 ]; then
    set_env STOREFRONT_URL "https://$DOMAIN"
    set_env PORTAL_URL "https://$DOMAIN"
  fi
  chown deploy:asknget "$ENV_FILE"; chmod 640 "$ENV_FILE"
  systemctl restart asknget-web 2>/dev/null || true
fi

# ------------------------------------------------------------------------------------------------------------------- ssh
say "ssh"
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
AK=/home/deploy/.ssh/authorized_keys
# Only rewrite the keys when keys are given: a re-run without them must not lock the CI pipelines out.
if [ -n "$BACKEND_KEY" ] || [ -n "$FRONTEND_KEY" ] || [ ! -f "$AK" ]; then : > "$AK"; REWRITE_KEYS=1; else REWRITE_KEYS=0; echo "authorized_keys left as it is"; fi
# `command=` forces one script per key and `restrict` removes everything else (shell, pty, forwarding): the CI keys cannot do more.
if [ "$REWRITE_KEYS" -eq 0 ]; then :
elif [ -n "$BACKEND_KEY" ];  then echo "command=\"$APP/bin/deploy-backend\",restrict $BACKEND_KEY"   >> "$AK"; else warn "no --backend-key: the backend pipeline cannot log in yet"; fi
if [ "$REWRITE_KEYS" -eq 0 ]; then :
elif [ -n "$FRONTEND_KEY" ]; then echo "command=\"$APP/bin/deploy-frontend\",restrict $FRONTEND_KEY" >> "$AK"; else warn "no --frontend-key: the frontend pipeline cannot log in yet"; fi
chown deploy:deploy "$AK"; chmod 600 "$AK"
cat > /etc/ssh/sshd_config.d/99-asknget.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
if sshd -t; then systemctl reload ssh || systemctl reload sshd || true; else rm -f /etc/ssh/sshd_config.d/99-asknget.conf; warn "sshd config check failed; hardening not applied"; fi
systemctl enable --now fail2ban >/dev/null 2>&1 || true

say "done"
cat <<EOF
Next:
  1. Open inbound TCP 80 in the instance's security group (and keep 22).
  2. Add the GitHub secrets (docs/ec2-deploy.md) and run the backend workflow, then the frontend one.
  3. Check:  curl http://<public address>/api/v1/health     and open  http://<public address>/
Memory now:  $(free -m | awk '/Mem:/{printf "%s MB total, %s MB available", $2, $7}'), swap $(free -m | awk '/Swap:/{print $2}') MB
EOF
