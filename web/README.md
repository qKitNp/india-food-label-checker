# Fineprint web app

React + Vite + Tailwind front end for [Fineprint](https://fineprint.appwrite.network), hosted on Appwrite Sites. See the [main README](../README.md) for what it does.

```
npm install
VITE_API_URL=http://localhost:8000 npm run dev   # defaults to the hosted API when unset
npm run build                                   # type-checks, then builds to dist/
```

- `src/pages/Live.tsx`: live voice mode (camera, Bolna web call, captions)
- `src/pages/Home.tsx`, `Products.tsx`, `Product.tsx`, `Scan.tsx`: catalog and scanning
- `og/`: source for the link-preview image and video (`uv run --with playwright python og/render.py`, needs ffmpeg)
