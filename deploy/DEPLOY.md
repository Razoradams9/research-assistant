# Deploying to Oracle Cloud Free Tier (Ubuntu 22.04, Ampere ARM)

This deploys the app as a systemd service behind nginx, with the SSE
live-progress stream working correctly through the proxy.

Target box: 2 OCPU / 12 GB Ampere ARM, Ubuntu 22.04 LTS. Groq does the
LLM inference remotely, so the box just runs FastAPI — very light load.

---

## 0. Before you start

- You can SSH into the instance (`ssh ubuntu@<public-ip>`).
- You have your **rotated** Groq (and optional Tavily) API keys ready.
- You know the instance's **public IP** (Oracle console → Instance details).

---

## 1. Open the port in Oracle's VCN (the #1 gotcha)

Oracle blocks inbound traffic at the cloud network level by default. Even
a perfectly running server is unreachable until you do this.

1. OCI Console → **Networking → Virtual Cloud Networks** → your VCN.
2. Click the **public subnet** → its **Security List**.
3. **Add Ingress Rule:**
   - Source CIDR: `0.0.0.0/0`
   - IP Protocol: `TCP`
   - Destination Port Range: `80` (and `443` later if you add HTTPS)
4. Save.

---

## 2. System packages

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip nginx git
```

## 3. Ubuntu firewall

Ubuntu's own firewall also has to allow the port. Oracle images often
have strict iptables rules, so allow HTTP explicitly:

```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo netfilter-persistent save     # persists the rule across reboots
```

(If you use `ufw` instead: `sudo ufw allow 80/tcp`.)

---

## 4. Get the code onto the box

Option A — git clone (recommended):
```bash
sudo mkdir -p /opt/research-assistant
sudo chown ubuntu:ubuntu /opt/research-assistant
git clone <your-repo-url> /opt/research-assistant
cd /opt/research-assistant
```

Option B — copy from your machine with scp/rsync (run locally):
```bash
# from your project folder
scp -r . ubuntu@<public-ip>:/opt/research-assistant
```
Do NOT copy your local `.venv` or `data/` — they're machine-specific.
(The `.gitignore` already excludes them from git.)

---

## 5. Python environment

```bash
cd /opt/research-assistant
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.lock.txt
# if a pin fails to resolve on this Python, use: -r requirements.txt
```

## 6. Configure secrets

```bash
cp .env.example .env
nano .env      # set GROQ_API_KEY, and TAVILY_API_KEY if you have one
```
Leave everything else as defaults. `.env` stays on the server only —
never commit it.

## 7. Smoke test before wiring up services

```bash
./.venv/bin/python -m scripts.smoke_test     # offline suite, no key needed
```
All checks should pass. Then a quick manual boot to confirm it starts:
```bash
./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
# Ctrl+C after you see "Application startup complete"
```

---

## 8. Install the systemd service

```bash
sudo cp deploy/research-assistant.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now research-assistant
sudo systemctl status research-assistant     # should be "active (running)"
```
Logs, if needed:
```bash
journalctl -u research-assistant -f
```

## 9. Install nginx reverse proxy

```bash
sudo cp deploy/nginx.conf /etc/nginx/sites-available/research-assistant
# edit server_name to your public IP (or a domain if you have one):
sudo nano /etc/nginx/sites-available/research-assistant   # set: server_name _;  (or your IP/domain)

sudo ln -s /etc/nginx/sites-available/research-assistant /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default    # remove the welcome page
sudo nginx -t                                   # config test — must pass
sudo systemctl reload nginx
```

---

## 10. Visit it

Open `http://<public-ip>/` in your browser. Ask a question and watch the
live-progress spine flow — if the progress trail updates smoothly, SSE is
proxying correctly.

---

## Updating later

```bash
cd /opt/research-assistant
git pull
./.venv/bin/pip install -r requirements.lock.txt   # if deps changed
sudo systemctl restart research-assistant
```
(Frontend-only changes don't need a restart — static files serve fresh.)

---

## Optional: HTTPS with a domain

If you point a domain at the box, add TLS for free with Let's Encrypt:
```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d yourdomain.com
```
Then add port `443` to the Oracle ingress rule (step 1) and the Ubuntu
firewall (step 3). Certbot auto-renews.

---

## Troubleshooting

| Symptom | Likely cause / fix |
| --- | --- |
| Page won't load at all | Oracle ingress rule (step 1) or Ubuntu firewall (step 3) missing |
| `502 Bad Gateway` | app not running — `sudo systemctl status research-assistant` |
| Progress trail never updates (report still appears) | nginx buffering the SSE stream — confirm the `/stream` location block in nginx.conf is active (`proxy_buffering off`) |
| `401 Invalid API Key` on every run | wrong/rotated key in `.env`; fix it then `sudo systemctl restart research-assistant` (`.env` is read once at startup) |
| `404 model does not exist` | Groq changed its catalog — list models and update `*_MODEL` in `.env`, then restart |
| App crashes on start, Python errors | dependency pin didn't resolve on this Python — reinstall with `requirements.txt` (floors) |
