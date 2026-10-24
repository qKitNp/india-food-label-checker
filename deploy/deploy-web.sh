#!/usr/bin/env bash
# Build the web app and publish it to Appwrite Sites (site "label-check").
# Run from the repo root on a machine logged in with the Appwrite CLI: deploy/deploy-web.sh
set -euo pipefail
cd "$(dirname "$0")/../web"
npm ci --silent
npm run build
cd ..
appwrite sites create-deployment --site-id label-check --code web/dist --activate
echo "Deployed. Live at https://label-check.appwrite.network once the build is ready."
