#!/usr/bin/env bash
# Bootstrap an Ubuntu 22.04 Azure VM into a running ANIMA cloud node.
# Run ON THE VM as the admin user (sudo available). Idempotent-ish.
#
#   APP_DIR=$HOME/anima REPO=https://github.com/zeron-G/anima.git \
#   LOCAL_PG=1 bash bootstrap.sh
#
# BEFORE the service can start you must scp two private things into $APP_DIR
# (they are gitignored and hold secrets / persona — never in the repo):
#   - .env           (DATABASE_URL, OPENAI_API_KEY, ANIMA_DASHBOARD_PASSWORD, …)
#   - agents/eva/    (Eva's private persona)
set -euo pipefail

APP_DIR="${APP_DIR:-$HOME/anima}"
REPO="${REPO:-https://github.com/zeron-G/anima.git}"
LOCAL_PG="${LOCAL_PG:-0}"
PY=python3.11

echo "== [1/7] OS packages =="
sudo add-apt-repository -y ppa:deadsnakes/ppa
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get update -y
sudo apt-get install -y python3.11 python3.11-venv python3.11-dev \
  git nginx nodejs build-essential libpq-dev curl

echo "== [2/7] clone / update repo at $APP_DIR =="
if [ -d "$APP_DIR/.git" ]; then git -C "$APP_DIR" pull --ff-only || true
else git clone "$REPO" "$APP_DIR"; fi
cd "$APP_DIR"

echo "== [3/7] python venv + install (core + network) =="
[ -d .venv ] || $PY -m venv .venv
./.venv/bin/pip install -U pip wheel
# [network] pulls pyzmq/paramiko/zeroconf — required because builtin tools
# (remote/spawn) import the gossip stack at registration even with mesh disabled.
./.venv/bin/pip install -e ".[network]"

echo "== [4/7] optional local Postgres failover (LOCAL_PG=$LOCAL_PG) =="
if [ "$LOCAL_PG" = "1" ]; then
  LOCAL_PG_PASS="${LOCAL_PG_PASS:-$(openssl rand -hex 16)}"   # random per-deploy, not a shared secret
  command -v docker >/dev/null || { curl -fsSL https://get.docker.com | sudo sh; sudo usermod -aG docker "$USER"; }
  sudo docker inspect anima-pg >/dev/null 2>&1 || sudo docker run -d --name anima-pg \
    --restart unless-stopped -p 127.0.0.1:5432:5432 \
    -e POSTGRES_PASSWORD="$LOCAL_PG_PASS" -e POSTGRES_DB=anima postgres:16
  echo "   waiting for pg…"; sleep 6
  PGPASSWORD="$LOCAL_PG_PASS" psql -h 127.0.0.1 -U postgres -d anima \
    -f anima/memory/pg_schema.sql || echo "   (schema apply: check manually)"
  echo "   -> add this line to .env:  LOCAL_DATABASE_URL=postgresql://postgres:$LOCAL_PG_PASS@127.0.0.1:5432/anima"
fi

echo "== [5/7] build the frontend (eva-ui/dist) =="
( cd eva-ui && npm ci && npm run build )

echo "== [6/7] install nginx + systemd from deploy/azure (paths templated) =="
sed "s#__ANIMA_DIR__#$APP_DIR#g; s#__USER__#$USER#g" deploy/azure/anima.service \
  | sudo tee /etc/systemd/system/anima.service >/dev/null
sed "s#__ANIMA_DIR__#$APP_DIR#g" deploy/azure/nginx-eva.conf \
  | sudo tee /etc/nginx/sites-available/eva >/dev/null
sudo ln -sf /etc/nginx/sites-available/eva /etc/nginx/sites-enabled/eva
sudo rm -f /etc/nginx/sites-enabled/default
# nginx (www-data) must be able to traverse $HOME to reach the SPA dist
sudo chmod o+x "$HOME"; chmod o+x "$APP_DIR" "$APP_DIR/eva-ui"; chmod -R o+rX "$APP_DIR/eva-ui/dist"

echo "== [7/7] preflight =="
MISSING=0
[ -f .env ] || { echo "  !! .env missing — scp it before starting"; MISSING=1; }
[ -d agents/eva ] || { echo "  !! agents/eva missing — scp the persona before starting"; MISSING=1; }
echo
if [ "$MISSING" = "1" ]; then
  echo "Bootstrap done, but secrets/persona missing. After scp-ing .env + agents/eva:"
else
  echo "Bootstrap complete. To go live (after installing the Cloudflare Origin cert, see AZURE_DEPLOY.md):"
fi
echo "  sudo systemctl daemon-reload && sudo systemctl enable --now anima"
echo "  sudo nginx -t && sudo systemctl reload nginx"
echo "  curl -s http://127.0.0.1:8420/v1/health"
