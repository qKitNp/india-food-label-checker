"""Shared product operations used by the HTTP routes, the voice endpoint and the agent."""

import logging

import httpx

from app.catalog import Embedder, Match, VectorIndex
from app.models import Product
from app.sources import off
from app.store import Store

log = logging.getLogger(__name__)


class ProductService:
    def __init__(self, store: Store, http: httpx.AsyncClient, embedder: Embedder | None) -> None:
        self.store = store
        self.http = http
        self.embedder = embedder
        self.index = VectorIndex()

    async def load_index(self) -> None:
        async for key, text, vector in self.store.embeddings():
            self.index.add(key, text, vector)
        log.info("Loaded %d product vectors", len(self.index))

    async def lookup(self, barcode: str) -> Product | None:
        """Store first, then Open Food Facts (saved for next time)."""
        product = await self.store.get_product(barcode)
        if product is None:
            product = await off.fetch(self.http, barcode)
            if product:
                await self.save(product)
        return product

    async def save(self, product: Product) -> None:
        """Save, and index for similarity search when we can embed it."""
        vector = None
        if self.embedder and product.embed_text:
            try:
                [vector] = await self.embedder.embed([product.embed_text])
            except (httpx.HTTPError, RuntimeError) as e:
                log.warning("Not indexing %s: %s", product.key, e)
        await self.store.save_product(product, vector)
        if vector is not None:
            self.index.add(product.key, product.embed_text, vector)

    async def similar(self, text: str, k: int = 5) -> list[Match]:
        if not self.embedder or not len(self.index):
            return []
        [vector] = await self.embedder.embed([text])
        return self.index.search(vector, k)
