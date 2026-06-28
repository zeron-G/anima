# ANIMA on Azure (public via Cloudflare)

Deploy ANIMA (Eva) as a always-on cloud node on an Azure VM, served at
`https://eva.<domain>` through Cloudflare. Memory stays on the existing Neon
Postgres (continuity); the VM is the new compute home.

**Architecture**
```
browser ─TLS→ Cloudflare (orange-cloud, WAF/DDoS) ─Origin Cert, Full(Strict)→ VM:443 nginx
                                                                               ├ /            → eva-ui/dist
                                                                               └ /v1 /api /ws → 127.0.0.1:8420 anima
NSG: 443 ← Cloudflare IPs only · 22 ← your IP only
anima → Neon Postgres+pgvector (primary)  [+ optional local Docker PG failover]
systemd Restart=always + in-process Sentinel keep it alive
```

Artifacts: `config/profiles/cloud.yaml`, `deploy/azure/{provision,bootstrap}.sh`,
`deploy/azure/{nginx-eva.conf,anima.service}`.

---

## 0. Prerequisites
- `az` CLI logged in (`az account show`).
- A domain on Cloudflare; decide the hostname (e.g. `eva.example.com`).
- Secrets ready locally in `.env` (DATABASE_URL, OPENAI_API_KEY, LLM keys) and the
  private persona at `agents/eva/`.
- Two new secrets for the public deploy:
  ```bash
  openssl rand -hex 24   # → ANIMA_DASHBOARD_PASSWORD  (your login)
  openssl rand -hex 32   # → ANIMA_DASHBOARD_JWT_SECRET (token signing)
  ```

## 1. Provision the VM
```bash
RG=anima-rg LOCATION=eastus VM_NAME=anima-eva VM_SIZE=Standard_B2s \
ADMIN_USER=azureuser bash deploy/azure/provision.sh
```
Prints the public IP. NSG opens 443 (Cloudflare ranges only) + 22 (your IP only).

## 2. Cloudflare DNS
- DNS → A record `eva` → `<VM public IP>`, **Proxied (orange cloud)**.
- SSL/TLS → Overview → mode **Full (Strict)**.
- SSL/TLS → Origin Server → **Create Certificate** → save the cert + key.

## 3. Bootstrap the VM
```bash
ssh azureuser@<VM_IP>
# on the VM:
APP_DIR=$HOME/anima LOCAL_PG=1 bash <(curl -fsSL \
  https://raw.githubusercontent.com/zeron-G/anima/master/deploy/azure/bootstrap.sh)
# or: git clone … && bash anima/deploy/azure/bootstrap.sh
```
Installs Python 3.11, node, nginx; clones the repo; `pip install -e .`; builds
`eva-ui/dist`; (optional) local Docker PG failover + schema; installs the
systemd unit + nginx site.

## 4. Ship secrets + persona (from your machine — never via git)
```bash
scp .env            azureuser@<VM_IP>:~/anima/.env
scp -r agents/eva   azureuser@<VM_IP>:~/anima/agents/
```
On the VM, append the two public-deploy secrets to `~/anima/.env`:
```
ANIMA_DASHBOARD_PASSWORD=<from step 0>
ANIMA_DASHBOARD_JWT_SECRET=<from step 0>
# if LOCAL_PG=1: paste the LOCAL_DATABASE_URL line that bootstrap.sh printed
```
`chmod 600 ~/anima/.env`.

## 5. Install the Origin cert + go live
```bash
sudo mkdir -p /etc/ssl/cloudflare
sudo tee /etc/ssl/cloudflare/origin.pem   # paste Cloudflare Origin cert
sudo tee /etc/ssl/cloudflare/origin.key   # paste its private key
sudo chmod 600 /etc/ssl/cloudflare/origin.key

sudo systemctl daemon-reload && sudo systemctl enable --now anima
sudo nginx -t && sudo systemctl reload nginx
curl -s http://127.0.0.1:8420/v1/health          # {"ok":true,...}
```
`cloud` profile (`ANIMA_PROFILE=cloud`, set by the unit) enables auth, keeps
evolution local (`git_remote_sync:false`), single-node (no mesh), no voice.

## 6. Cutover (avoid double-brain)
The cloud node and your local Eva share the **same Neon DB**. Run only ONE
heartbeat against it. Before/at go-live, stop the local instance's heartbeat
(quit the local `python -m anima`). The cloud node is now the primary brain.
(Re-introducing local as a mesh peer is a v2 task — set `network.enabled:true`
+ `ANIMA_NETWORK_SECRET` on both.)

## 7. Verify (end-to-end)
1. `curl https://eva.<domain>/v1/version` → version JSON (CF→nginx→backend).
2. Open `https://eva.<domain>` → login with `ANIMA_DASHBOARD_PASSWORD` → chat → SSE reply.
3. `curl https://eva.<domain>/v1/memory/stats` (no token) → **401** (auth on).
4. From a non-Cloudflare host: `curl -k https://<VM_IP>` → blocked by NSG.
5. `sudo systemctl restart anima` → `/v1/healthz` recovers (process resilience).
6. `/v1/status` → `db: on primary` (Neon), no local double-brain.

## Operations
- Logs: `journalctl -u anima -f` · `sudo tail -f /var/log/nginx/error.log`
- Restart / stop: `sudo systemctl restart|stop anima`
- Update code: `git -C ~/anima pull && (cd ~/anima/eva-ui && npm run build) && chmod -R o+rX ~/anima/eva-ui/dist && sudo systemctl restart anima && sudo systemctl reload nginx`
- Refresh Cloudflare IP ranges in the NSG + `nginx-eva.conf` if they change.
- Teardown (removes ALL cloud resources): `az group delete -n anima-rg`

## Security checklist
- [ ] `dashboard.auth.password` set (via `.env`) — never expose unauthenticated.
- [ ] SSL mode **Full (Strict)** + Origin cert installed (no Flexible).
- [ ] NSG 443 = Cloudflare IPs only; 22 = your IP only.
- [ ] `.env` / `agents/eva` shipped over SSH only, `chmod 600 .env`, never committed.
- [ ] `evolution.git_remote_sync: false` (Eva won't push to the public repo).
