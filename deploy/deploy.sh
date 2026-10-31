#!/usr/bin/env bash
# Pull the latest code on the server and restart the API. Run on the VM: ~/app/deploy/deploy.sh
# Public traffic arrives through the Cloudflare Tunnel. Its config (/etc/cloudflared/config.yml,
# see deploy/cloudflared.example.yml) and credentials live only on the VM.
set -euo pipefail
cd ~/app
git pull --ff-only
cd backend
~/.local/bin/uv sync --frozen --no-dev
sudo cp ~/app/deploy/labelcheck.service /etc/systemd/system/labelcheck.service
sudo systemctl daemon-reload
sudo systemctl restart labelcheck
sudo systemctl restart cloudflared
echo "Restarted. Waiting for the API (model load takes ~30 s)..."
for _ in $(seq 1 90); do
  if curl -fs http://127.0.0.1:8000/health; then echo; exit 0; fi
  sleep 2
done
echo "API did not come up; see: journalctl -u labelcheck -n 50" >&2
exit 1
