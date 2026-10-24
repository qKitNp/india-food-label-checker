# India Food Label Checker

India Food Label Checker checks Indian packaged food labels against EU/US benchmarks: it tests marketing claims (e.g. "sugar free") against legal limits from EU Regulation 1924/2006, flags additives not permitted in the EU or US, and compares the Indian ingredients with the EU/US version of the same product.

## How it works

- Barcode lookup: `GET /products/{key}` looks up the barcode in the catalog and Open Food Facts, and returns a verdict built with the stored EU counterpart.
- Back-label photo: `POST /labels/extract` reads ingredients, nutrition per 100 g/ml, and claims from back-of-pack photos. Nothing is saved; the user reviews the result and `POST /products/confirm` saves it as verified (a barcode is required).
- Front-of-pack agent: `POST /identify` takes front-of-pack photos and runs as a background job (`202`, poll `GET /jobs/{job_id}`). The agent identifies brand/variant/barcode, tries barcode lookup, then catalog vector search, then web search; web submissions are only accepted with an ingredients quote copied verbatim from a page that names the exact variant, and nutrition numbers not on that page are dropped.
- Counterpart check: `GET /products/{key}/counterpart?region=EU` (EU or US only) returns the verdict against that region's version, fetching it via web search when missing.
- Voice lookup: `POST /voice/lookup` is the tool endpoint for the Bolna agent. It takes a barcode or product name, checks the catalog and Open Food Facts only, matches names by vector similarity (threshold 0.6), and returns a short spoken summary.

## Appwrite usage

- TablesDB `products` table: product payload plus vector columns (`embed_text`, `embed_model`, `embedding`, `image_embedding`) for name and pack-photo search.
- TablesDB `counterparts` table: cached EU/US counterparts per barcode and region.
- Storage bucket `pack-images`: uploaded pack photos. Scan photos are kept as the product's pack photo when it has none.
- Leave `APPWRITE_PROJECT_ID` empty to use the in-memory store locally (see `backend/.env.example`).

## Repo layout

- `backend/` - FastAPI API (`backend/app/main.py`), front-of-pack agent (`backend/app/agent.py`), verdict and rules, label/counterpart sources, store.
- `deploy/` - server deploy script (`deploy.sh`), systemd unit (`labelcheck.service`), Cloudflare Tunnel config (`cloudflared.yml`).

## Data sources

- [Open Food Facts](https://world.openfoodfacts.org/) product data, under the [Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/1-0/).
- Claim thresholds from EU Regulation (EC) No 1924/2006.
- The additive table (`backend/app/rules/additives.json`) is partly unverified: entries without a `source_url` still need checking against the EU, FSSAI and FDA texts.

## Disclaimer

Informational only, not medical or legal advice. Not affiliated with FSSAI or any brand. A flag describes a regulatory difference between countries; it does not mean a product is unsafe. Web-sourced ingredients are quoted from the cited page, not read from your pack.
