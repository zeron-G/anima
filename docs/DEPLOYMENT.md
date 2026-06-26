# ANIMA Deployment Guide

ANIMA is a Python backend (`anima/`, aiohttp on port **8420**) plus an
independently-buildable Vue SPA (`eva-ui/`). After Phase 3 they can run together
(backend serves the built SPA) or fully split (SPA hosted elsewhere, pointing at
a remote backend). This guide covers both.

See also: `docs/API.md` (the contract), `docs/REFACTOR.md` (the kernel/data
separation model).

---

## 1. Backend

**Install profiles (extras).** The base install is slim — LLM + cognitive +
memory + the API/WebSocket server only. Add subsystems via extras:

| install | adds |
|---|---|
| `pip install .` | slim core (headless API/WS, chat, memory) |
| `pip install ".[network]"` | gossip mesh / mDNS discovery / remote-node SSH + spawn |
| `pip install ".[desktop]"` | native pywebview window (`python -m anima`) |
| `pip install ".[tts,stt]"` | voice (pulls **torch** — large) |
| `pip install ".[discord]"` | Discord channel |
| `pip install ".[all]"` | everything (single-machine full install) |

If a feature is enabled in config but its extra isn't installed, ANIMA logs a
clear message and disables that feature — it does not crash.

### Source-tree (development / single machine)
```bash
git clone https://github.com/zeron-G/anima.git && cd anima
pip install -e ".[dev,all]"    # conda 'anima' env, Python 3.11 (base is slim; [all] = all features)
# data/ + agents/eva live in the repo (gitignored); Eva runs as-is
python -m anima --headless     # API + WS only (no desktop window)
```

### Installed (wheel) — fresh instance
```bash
pip install .                  # or the built wheel
python -m anima init           # creates ~/.anima: data/ + agents/eva (from seed) + .env
#   or: python -m anima init --home /srv/anima   (then set ANIMA_HOME=/srv/anima)
# edit <home>/.env with your keys
python -m anima --headless
```

`home_dir()` resolution: `$ANIMA_HOME` → source tree → `~/.anima`. Private state
(`data/`, `agents/<name>`, `.env`, `config.yaml`) lives under the home; nothing
private is in the published kernel. See `docs/REFACTOR.md` §4–§5.

### Enable auth (required for any non-localhost deploy)
In `<home>/config.yaml` or `local/env.yaml`:
```yaml
dashboard:
  auth:
    password: "your-login-secret"     # blank = auth DISABLED
    token: "a-long-random-signing-secret"   # stable → JWTs survive restarts
```
Without a password the API is open — never expose an unauthenticated backend.

---

## 2. Frontend (`eva-ui`)

The SPA reads its backend address from build-time env (`VITE_API_BASE`,
`VITE_WS_BASE`). Blank = same-origin.

```bash
cd eva-ui && npm install
npm run dev      # dev server :5173, proxies /v1 /ws /api to VITE_DEV_PROXY (default localhost:8420)
npm run build    # → eva-ui/dist (static; host anywhere)
```

---

## 3. Recipe A — Same-origin reverse proxy (recommended)

One origin; nginx serves the static SPA and proxies API/WS to the backend. No
CORS, no cross-origin token concerns. Build the SPA with **blank** env (default).

```nginx
server {
  listen 80;
  server_name eva.example.com;

  root /var/www/eva-ui/dist;        # output of `npm run build`
  location / { try_files $uri /index.html; }   # SPA client routing

  location ~ ^/(v1|api|static|desktop)/ { proxy_pass http://127.0.0.1:8420; }
  location /ws {
    proxy_pass http://127.0.0.1:8420;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
  }
}
```
The backend can also serve the SPA itself: set `dashboard.ui_dist` to the dist
path (or leave blank in a source tree → it serves `eva-ui/dist`). With no dist it
runs API-only and says so on `/`.

---

## 4. Recipe B — Fully split (SPA on a CDN/host, remote backend)

Build the SPA pointed at the backend, host the static files anywhere, and
allow-list the SPA's origin on the backend.

```bash
# build-time (e.g. .env.production.local or CI env)
VITE_API_BASE=https://api.eva.example.com \
VITE_WS_BASE=api.eva.example.com \
npm run build
```
Backend `config.yaml`:
```yaml
dashboard:
  cors:
    allow_origins: ["https://eva.example.com"]   # the SPA's origin
    allow_all: false
```
If the UI loads but every API call fails with a network/CORS error, the SPA's
origin is missing from `allow_origins`. The backend logs the active allow-list at
startup. Auth tokens flow as `Authorization: Bearer` (REST) and `?token=` (WS);
both validate against the same JWT, so a single login works for the whole app.

---

## 5. Edge / robot nodes

For PiDog / edge deployments use the spawn packager and `--edge` profile — see
`docs/EDGE_ANIMA.md` and `docs/ROBOTICS_PIDOG.md`.

---

## 6. Checklist

- [ ] Backend reachable: `curl http://HOST:8420/v1/health` → `{"ok":true,...}`.
- [ ] Auth set (`dashboard.auth.password`) for any networked deploy.
- [ ] Split only: SPA origin in `dashboard.cors.allow_origins`; SPA built with `VITE_API_BASE`/`VITE_WS_BASE`.
- [ ] `python -m anima init` run on fresh installs (creates home + .env).
- [ ] Version check: `curl http://HOST:8420/v1/version`.
