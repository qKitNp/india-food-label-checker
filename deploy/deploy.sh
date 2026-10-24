#!/usr/bin/env bash
# Pull the latest code on the server and restart the API. Run on the VM: ~/app/deploy/deploy.sh
set -euo pipefail
cd ~/app
git pull --ff-only
cd backend
~/.local/bin/uv sync --frozen --no-dev
sudo cp ~/app/deploy/labelcheck.service /etc/systemd/system/labelcheck.service
sudo cp ~/app/deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl daemon-reload
sudo systemctl restart labelcheck
sudo systemctl reload caddy
echo "Restarted. Waiting for the API (model load takes ~30 s)..."
for _ in $(seq 1 90); do
  if curl -fs http://127.0.0.1:8000/health; then echo; exit 0; fi
  sleep 2
done
echo "API did not come up; see: journalctl -u labelcheck -n 50" >&2
exit 1
