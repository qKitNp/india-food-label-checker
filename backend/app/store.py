"""Product and counterpart cache in Appwrite TablesDB, with an in-memory fallback for
local development when Appwrite isn't configured.

Uses the TablesDB REST API over a persistent httpx connection rather than the Python
SDK, which opens a new TLS connection per call (too slow for the voice path)."""

import json
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

import httpx
import numpy as np

from app import catalog
from app.config import Settings
from app.models import Counterpart, Product, Region


def row_id(key: str) -> str:
    """Appwrite row IDs: max 36 chars of a-z, A-Z, 0-9, '.', '-', '_'."""
    return re.sub(r"[^a-zA-Z0-9._-]", "_", key)[:36]


@dataclass
class Vectors:
    model: str
    name: np.ndarray | None = None
    image: np.ndarray | None = None


@dataclass
class StoredVectors:
    key: str
    text: str
    vectors: Vectors


class Store(Protocol):
    async def get_product(self, key: str) -> Product | None: ...
    async def save_product(self, product: Product, vectors: Vectors | None = None) -> None: ...
    def vectors(self) -> AsyncIterator[StoredVectors]: ...
    async def save_image(self, key: str, data: bytes) -> str: ...
    async def get_counterpart(self, barcode: str, region: Region) -> Counterpart | None: ...
    async def save_counterpart(self, barcode: str, counterpart: Counterpart) -> None: ...
    async def save_scan_log(self, record: dict) -> None: ...
    async def list_substances(self) -> list[dict]: ...
    async def save_substance(self, sub: dict) -> None: ...


class MemoryStore:
    def __init__(self) -> None:
        self.products: dict[str, Product] = {}
        self.stored_vectors: dict[str, Vectors] = {}
        self.images: dict[str, bytes] = {}
        self.counterparts: dict[str, Counterpart] = {}
        self.scan_logs: list[dict] = []
        self.substances: dict[str, dict] = {}

    async def get_product(self, key):
        return self.products.get(key)

    async def save_product(self, product, vectors=None):
        self.products[product.key] = product
        if vectors is not None:
            self.stored_vectors[product.key] = vectors

    async def vectors(self):
        for key, v in self.stored_vectors.items():
            yield StoredVectors(key, self.products[key].embed_text, v)

    async def save_image(self, key, data):
        file_id = uuid.uuid4().hex
        self.images[file_id] = data
        return file_id

    async def get_counterpart(self, barcode, region):
        return self.counterparts.get(f"{barcode}.{region}")

    async def save_counterpart(self, barcode, counterpart):
        self.counterparts[f"{barcode}.{counterpart.region}"] = counterpart


    async def save_scan_log(self, record):
        self.scan_logs.append(record)

    async def list_substances(self):
        return list(self.substances.values())

    async def save_substance(self, sub):
        self.substances[sub["id"]] = sub


class AppwriteStore:
    def __init__(self, settings: Settings) -> None:
        endpoint = settings.appwrite_endpoint.rstrip("/")
        headers = {"X-Appwrite-Project": settings.appwrite_project_id, "X-Appwrite-Key": settings.appwrite_api_key}
        self.http = httpx.AsyncClient(
            base_url=f"{endpoint}/tablesdb/{settings.appwrite_database_id}/tables/", headers=headers, timeout=10,
        )
        self.storage = httpx.AsyncClient(
            base_url=f"{endpoint}/storage/buckets/{settings.appwrite_images_bucket}/", headers=headers, timeout=30,
        )
        self.products = settings.appwrite_products_table
        self.counterparts = settings.appwrite_counterparts_table
        self.scan_logs = settings.appwrite_scan_logs_table
        self.substances = settings.appwrite_substances_table

    async def _get(self, table: str, key: str) -> str | None:
        r = await self.http.get(f"{table}/rows/{row_id(key)}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()["payload"]

    async def _put(self, table: str, key: str, data: dict) -> None:
        r = await self.http.put(f"{table}/rows/{row_id(key)}", json={"data": data})
        r.raise_for_status()

    async def delete(self, table: str, key: str) -> None:
        r = await self.http.delete(f"{table}/rows/{row_id(key)}")
        if r.status_code != 404:
            r.raise_for_status()

    async def aclose(self) -> None:
        await self.http.aclose()
        await self.storage.aclose()

    async def get_product(self, key):
        payload = await self._get(self.products, key)
        return Product.model_validate_json(payload) if payload else None

    async def save_product(self, product, vectors=None):
        data = {
            "barcode": product.barcode or "", "name": product.name, "brand": product.brand,
            "source": product.source, "verified": product.verified,
            "payload": product.model_dump_json(),
        }
        if vectors is not None:
            data |= {
                "embed_model": vectors.model, "embed_text": product.embed_text[:512],
                "embedding": None if vectors.name is None else catalog.encode(vectors.name),
                "image_embedding": None if vectors.image is None else catalog.encode(vectors.image),
            }
        await self._put(self.products, product.key, data)

    async def vectors(self):
        """All stored product vectors, paged with a cursor."""
        cursor = None
        while True:
            queries = [
                {"method": "select", "values": ["$id", "embed_text", "embed_model", "embedding", "image_embedding"]},
                {"method": "isNotNull", "attribute": "embed_text"},
                {"method": "limit", "values": [100]},
            ]
            if cursor:
                queries.append({"method": "cursorAfter", "values": [cursor]})
            r = await self.http.get(f"{self.products}/rows",
                                    params={"queries[]": [json.dumps(q) for q in queries]})
            r.raise_for_status()
            rows = r.json()["rows"]
            for row in rows:
                yield StoredVectors(row["$id"], row.get("embed_text") or "", Vectors(
                    model=row.get("embed_model") or "",
                    name=catalog.decode(row["embedding"]) if row.get("embedding") else None,
                    image=catalog.decode(row["image_embedding"]) if row.get("image_embedding") else None,
                ))
            if len(rows) < 100:
                return
            cursor = rows[-1]["$id"]

    async def save_image(self, key, data):
        """Store a pack photo in Appwrite Storage; returns the file ID."""
        file_id = uuid.uuid4().hex[:36]
        r = await self.storage.post("files", data={"fileId": file_id},
                                    files={"file": (f"{row_id(key)}.jpg", data, "image/jpeg")})
        r.raise_for_status()
        return file_id

    async def get_counterpart(self, barcode, region):
        payload = await self._get(self.counterparts, f"{barcode}.{region}")
        return Counterpart.model_validate_json(payload) if payload else None

    async def save_counterpart(self, barcode, counterpart):
        await self._put(self.counterparts, f"{barcode}.{counterpart.region}", {
            "barcode": barcode, "region": counterpart.region, "source_url": counterpart.source_url,
            "payload": counterpart.model_dump_json(),
        })


    async def save_scan_log(self, record):
        """One row per finished scan, with the agent's full trace, for observability."""
        steps = json.dumps(record["steps"], ensure_ascii=False)
        await self._put(self.scan_logs, record["job_id"], {
            "job_id": record["job_id"], "status": record["status"], "outcome": record["outcome"],
            "product_key": record["product_key"], "duration_ms": record["duration_ms"],
            "error": (record["error"] or "")[:2000] or None, "steps": steps[:1_000_000],
        })

    async def list_substances(self):
        out, cursor = [], None
        while True:
            queries = [{"method": "select", "values": ["$id", "payload"]}, {"method": "limit", "values": [100]}]
            if cursor:
                queries.append({"method": "cursorAfter", "values": [cursor]})
            r = await self.http.get(f"{self.substances}/rows", params={"queries[]": [json.dumps(q) for q in queries]})
            r.raise_for_status()
            rows = r.json()["rows"]
            out += [json.loads(row["payload"]) for row in rows]
            if len(rows) < 100:
                return out
            cursor = rows[-1]["$id"]

    async def save_substance(self, sub):
        await self._put(self.substances, sub["id"], {
            "name": sub["name"], "codes": ",".join(sub["codes"]), "origin": sub.get("origin", "seed"),
            "payload": json.dumps(sub, ensure_ascii=False),
        })

def make_store(settings: Settings) -> Store:
    return AppwriteStore(settings) if settings.appwrite_project_id else MemoryStore()
