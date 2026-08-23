# Charweb VPS deployment + passive TLS capture

Runbook for moving the testbed off a local machine and onto a small VPS, with
passive TLS handshake capture alongside it.

Two things drive the choices here. **Timing is a measured variable**, so
anything that adds jitter to server response time — cold starts, autoscaling,
CPU throttling, an inline TLS-inspecting proxy — corrupts data rather than
merely inconveniencing you. And **the data is irreplaceable**: 30–50 human
subjects cannot be re-recruited cheaply, so persistence and backups are not
optional polish.

---

## 1. Provision

Ubuntu 24.04 LTS, smallest tier. Hetzner CX22 (~€4/mo), DigitalOcean basic
($6), Vultr ($5). 1 GB RAM runs it; 2 GB is more comfortable.

## 2. DNS — read this before pointing the record

Set `charweb.net` A record to the VPS IP, **DNS-only (grey cloud)**.

If the record is proxied through Cloudflare, or you keep the Cloudflare
Tunnel, TLS terminates at Cloudflare's edge and the ClientHello reaching this
box is Cloudflare's, not the visitor's. Passive fingerprinting then captures
nothing useful.

The tradeoff is real: the origin IP becomes public and you lose Cloudflare's
DDoS shielding. Acceptable for a small research site; note it in Limitations.

## 3. Base system

```bash
adduser charweb && usermod -aG sudo charweb

# Copy root's authorized_keys to the new user BEFORE disabling password
# login. Skip this and the next two lines lock you out of the box entirely.
rsync --archive --chown=charweb:charweb ~/.ssh /home/charweb/

# Now verify from a SECOND terminal that `ssh charweb@<ip>` works and
# `sudo -v` succeeds. Only then disable password auth.
sudo sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo systemctl reload ssh

sudo ufw allow 22,80,443/tcp && sudo ufw enable
sudo apt update && sudo apt install -y \
    python3-venv python3-dev build-essential \
    nginx git certbot python3-certbot-nginx sqlite3 tcpdump
sudo apt install -y unattended-upgrades
```

## 4. Application

```bash
sudo mkdir -p /srv/charweb /var/log/charweb /srv/backups /var/lib/charweb/captures
sudo chown -R charweb:charweb /srv/charweb /var/log/charweb /srv/backups

git clone https://github.com/Sg-fit/Charweb.git /srv/charweb
cd /srv/charweb
python3 -m venv venv
./venv/bin/pip install -r app/requirements.txt gunicorn eventlet
./venv/bin/pip install -r research/requirements.txt   # dpkt, for the capture analysis scripts
```

Secrets go in `/etc/charweb.env` (mode 600, **not** in git):

```
SECRET_KEY=<generate with: python3 -c 'import secrets;print(secrets.token_hex(32))'>
FLASK_APP=app.wsgi:app
# any model API keys used by the agent harnesses
```

## 5. Database

```bash
scp instance/app.db charweb@<vps>:/srv/charweb/instance/app.db   # from your machine
sqlite3 /srv/charweb/instance/app.db "PRAGMA journal_mode=WAL;"
cd /srv/charweb && ./venv/bin/flask db upgrade
```

`flask db upgrade` applies both pending migrations:
`b3f7c21a90de` (per-event `url`) and `c8e2f45b71ac` (client addr/port on
`UserSession`, the join key for TLS capture).

WAL mode is not optional. With two or three agents plus human traffic, each
posting a batch to `/api/track` every five seconds, default SQLite locking
starts throwing write contention.

## 6. Services

```bash
sudo cp deploy/charweb.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now charweb
sudo systemctl status charweb

sudo cp deploy/charweb.nginx /etc/nginx/sites-available/charweb
sudo ln -sf /etc/nginx/sites-available/charweb /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx

sudo certbot --nginx -d charweb.net -d www.charweb.net
```

Two details in those files that are easy to get wrong and expensive to debug:

- **gunicorn runs one eventlet worker.** More than one breaks Flask-SocketIO
  without a Redis message queue, and a single worker also keeps response time
  consistent.
- **nginx `proxy_set_header X-Forwarded-For $remote_addr`** — set, not
  `$proxy_add_x_forwarded_for`. Appending lets a client prepend a forged
  address and poison the session↔capture join.

## 7. Verify

```bash
BASE_URL=https://charweb.net ./research/verify_features.sh
```

All 19 checks must pass. That is the deployment gate — don't proceed until
they do.

## 8. Backups

```bash
sudo cp deploy/backup.sh /usr/local/bin/charweb-backup
sudo crontab -e
# 0 3 * * *  /usr/local/bin/charweb-backup >> /var/log/charweb/backup.log 2>&1
```

Fill in the offsite copy at the bottom of the script. A backup living on the
same VPS as the database is not a backup.

## 9. TLS capture

```bash
sudo cp research/capture_tls.sh /usr/local/bin/charweb-tlscap
sudo chmod +x /usr/local/bin/charweb-tlscap
```

Two ways to run it. Either works; the systemd unit is recommended for any
collection window you won't be actively watching, since it restarts tcpdump
automatically if it dies (OOM, disk full) instead of silently losing hours
of an unattended window:

```bash
# Direct (fine for a short, watched session)
sudo charweb-tlscap start
sudo charweb-tlscap status

# Via systemd (recommended -- auto-restarts on failure)
sudo cp deploy/charweb-tlscap.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start charweb-tlscap      # NOT enable -- see below
sudo systemctl status charweb-tlscap
```

Deliberately not `systemctl enable`d: capture should only run during an
active collection window (see the note below about stopping it when the
window ends), not restart itself on every reboot indefinitely.

The capture filter keeps only client→server packets on 443 whose first
payload byte is `0x16` — TLS handshake records. Application data is never
written to disk, which keeps the pcaps small and means no request bodies or
human-subject content is stored in them.

Full packets are captured (`-s 0`) deliberately: ClientHellos carrying
post-quantum key shares run past one MTU and span several TCP segments.
Truncating them would silently lose the fingerprints of exactly the modern
clients this study is about.

Files rotate hourly, 168 kept (7 days). Pull them down and process offline:

```bash
python research/extract_ja3.py '/var/lib/charweb/captures/tls-*.pcap' \
    -o tls_fingerprints.csv
python research/join_tls_sessions.py --fingerprints tls_fingerprints.csv \
    -o session_tls.csv
```

Stop the capture when a collection window ends — there's no reason to keep
recording handshakes you won't analyse. `sudo systemctl stop charweb-tlscap`
if you used the unit, `sudo charweb-tlscap stop` otherwise.

## 10. Record the environment

Add host and git SHA to the run manifest for every collection run:

```bash
git rev-parse --short HEAD
```

If anything about the environment changes mid-collection, it should be
visible in the data rather than reconstructed from memory later.

---

## After deploying

Server response time has just changed, which shifts page-load timings. Re-run
the 3–5 session pilot on the new host and confirm the pipeline still produces
what you expect **before** starting real collection.

Then freeze: no deploys, no schema changes, no instrumentation edits until the
collection window closes. A mid-collection change creates a regime split that
correlates with whenever you made it — the same class of problem as the
pre/post-URL-capture split already in the dataset.
