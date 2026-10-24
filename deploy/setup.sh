#!/usr/bin/env bash
# Set up a fresh Ubuntu EC2 instance (24.04 or newer) to run Kalys.
#
# Safe to run more than once: it skips work that is already done, and will not
# reload the database if articles are already in it.
#
# Usage, on the instance:
#   ./kalys_v2/deploy/setup.sh ~/kalys.dump
#
# Expects: the database dump already copied to the instance (see
# docs/deploy.md), and an IAM role attached to the instance granting Bedrock.

set -euo pipefail

REPO_URL="https://github.com/zhanboloteraliev/kalys_v2.git"
BRANCH="claude/kalys-v2-rebuild-d75xqd"
APP_DIR="$HOME/kalys_v2"
DUMP="${1:-$HOME/kalys.dump}"

say() { printf '\n=== %s ===\n' "$1"; }

say "Installing system packages"
sudo apt-get update -qq
sudo apt-get install -y -qq git curl docker.io docker-compose-v2
sudo systemctl enable --now docker
# Lets you run docker without sudo after your next login. This script uses
# sudo for docker anyway, so it does not depend on the group being active yet.
sudo usermod -aG docker "$USER" || true

say "Adding swap"
# t3.medium has 4 GB of RAM and the embedding model wants a large part of it.
# 2 GB of swap stops the first model load from being killed on a tight box.
if [ ! -f /swapfile ]; then
    sudo fallocate -l 2G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
else
    echo "swap already present, skipping"
fi

say "Installing uv and Python 3.12"
if [ ! -x "$HOME/.local/bin/uv" ]; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"
# uv downloads a standalone Python 3.12 rather than using the distro's. Ubuntu
# 26.04 ships Python 3.14, which PyTorch has no stable wheels for yet, and has
# no python3.12 apt package at all. Letting uv own the version keeps this
# script working on any Ubuntu release.
uv python install 3.12

say "Fetching the code"
if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" fetch --quiet origin "$BRANCH"
    git -C "$APP_DIR" checkout --quiet "$BRANCH"
    git -C "$APP_DIR" pull --quiet --ff-only origin "$BRANCH"
else
    git clone --quiet --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"

say "Writing .env"
# No AWS_PROFILE here on purpose: the instance role provides the credentials,
# and naming a profile that does not exist on this machine breaks every call.
printf 'AWS_REGION=us-east-1\n' > .env
cat .env

say "Starting PostgreSQL"
sudo docker compose up -d db
printf 'waiting for postgres'
for _ in $(seq 1 60); do
    if sudo docker compose exec -T db pg_isready -U kalys -d kalys >/dev/null 2>&1; then
        printf ' ready\n'
        break
    fi
    printf '.'
    sleep 2
done

say "Restoring the database"
loaded=$(sudo docker compose exec -T db psql -U kalys -d kalys -tAc \
    "SELECT count(*) FROM articles" 2>/dev/null || echo 0)
if [ "${loaded:-0}" -gt 0 ]; then
    echo "already holds $loaded article rows, skipping restore"
else
    if [ ! -f "$DUMP" ]; then
        echo "ERROR: no dump at $DUMP - copy it up first (see docs/deploy.md)" >&2
        exit 1
    fi
    sudo docker compose cp "$DUMP" db:/tmp/kalys.dump
    sudo docker compose exec -T db pg_restore -U kalys -d kalys \
        --clean --if-exists --no-owner /tmp/kalys.dump
    sudo docker compose exec -T db rm -f /tmp/kalys.dump
    echo "restored"
fi

say "Installing Python dependencies"
# Pinned: requires-python is ">=3.12", so without this uv would pick the
# system's 3.14 and fail to find PyTorch wheels.
uv sync --quiet --python 3.12

# The local embedding model is no longer downloaded here. Since Step 14 the
# embeddings come from Amazon Titan through Bedrock, so the 1.1 GB model and
# PyTorch are never loaded at runtime. Fetch it only if you intend to run with
# KALYS_EMBEDDER=local:
#     uv run python -c "from kalys.embed import LocalEmbedder; LocalEmbedder()"

say "Installing Caddy for HTTPS"
# Caddy serves kalys.eraliev.com and proxies to the app on 127.0.0.1:8000. It
# gets its own Let's Encrypt certificate once DNS points here and ports 80 and
# 443 are open; until then it retries quietly.
if ! command -v caddy >/dev/null 2>&1; then
    sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
        | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
        | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
    sudo apt-get update
    sudo apt-get install -y caddy
fi
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl enable caddy
sudo systemctl reload caddy || sudo systemctl restart caddy

say "Installing the service"
sudo cp deploy/kalys.service /etc/systemd/system/kalys.service
sudo systemctl daemon-reload
sudo systemctl enable --now kalys.service
sleep 5
sudo systemctl --no-pager --lines=0 status kalys.service || true

say "Done"
cat <<'NOTE'
Check it answers, from your laptop (no SSH: port 22 is closed and the instance
is reached through SSM Session Manager):

    ./deploy/forward.sh

then open http://localhost:8000 in your browser.

To serve it publicly you also need, outside this script:
  - an Elastic IP associated with this instance
  - an A record for kalys.eraliev.com pointing at that address
  - ports 80 and 443 open in the security group
  - these in .env, or the robot gate stays off (see src/kalys/gate.py):
        TURNSTILE_SITEKEY=...
        TURNSTILE_SECRET=...
        TURNSTILE_HOSTNAMES=kalys.eraliev.com
        KALYS_GATE_SECRET=<a long random string>

Useful commands on the instance:
    sudo systemctl restart kalys      # after a git pull
    sudo journalctl -u kalys -f       # live logs
    sudo journalctl -u caddy -f       # certificate and proxy logs
NOTE
