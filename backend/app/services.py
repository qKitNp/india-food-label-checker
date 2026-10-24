"""Shared product operations used by the HTTP routes, the voice endpoint and the agent."""

import logging

import httpx
import numpy as np

from app.catalog import Embedder, Match, VectorIndex
from app.models import Product
from app.sources import off
from app.store import Store, Vectors

log = logging.getLogger(__name__)


class ProductService:
    def __init__(self, store: Store, http: httpx.AsyncClient, embedder: Embedder | None,
                 image_weight: float = 0.5) -> None:
        self.store = store
        self.http = http
        self.embedder = embedder
        self.image_weight = image_weight
        self.index = VectorIndex()

    async def load_index(self) -> None:
        """Load stored vectors. Products embedded by another model are re-embedded from their name
        (their old pack-image vector is dropped; it will be refilled by the next scan)."""
        if not self.embedder:
            return
        stale = []
        async for stored in self.store.vectors():
            v = stored.vectors
            if v.model == self.embedder.model_id:
                self.index.add(stored.key, stored.text, v.name, v.image)
            else:
                stale.append(stored.key)
        for key in stale:
            if product := await self.store.get_product(key):
                await self.save(product)
        log.info("Loaded %d product vectors (%d re-embedded)", len(self.index), len(stale))

    async def lookup(self, barcode: str) -> Product | None:
        """Store first, then Open Food Facts (saved for next time)."""
        product = await self.store.get_product(barcode)
        if product is None:
            product = await off.fetch(self.http, barcode)
            if product:
                await self.save(product)
        return product

    async def embed_image(self, data: bytes) -> np.ndarray | None:
        if not self.embedder:
            return None
        [vector] = await self.embedder.images([data])
        return vector

    async def save(self, product: Product, image: bytes | None = None,
                   image_vector: np.ndarray | None = None) -> Product:
        """Save and index. A pack photo, if given and the product has none yet, goes to
        storage and becomes the product's image vector. Returns the saved product."""
        if image is not None and not product.image_file_id:
            try:
                product = product.model_copy(update={"image_file_id": await self.store.save_image(product.key, image)})
                if image_vector is None:
                    image_vector = await self.embed_image(image)
            except Exception as e:  # storage or embedding trouble shouldn't lose the product
                log.warning("Pack photo not stored for %s: %s", product.key, e)

        vectors = None
        if self.embedder and product.embed_text:
            try:
                [name] = await self.embedder.documents([(product.brand or "", product.embed_text)])
                if image_vector is None and product.key in self.index.keys:
                    i = self.index.keys.index(product.key)
                    image_vector = self.index.images[i] if self.index.has_image[i] else None
                vectors = Vectors(self.embedder.model_id, name, image_vector)
            except Exception as e:
                log.warning("Not indexing %s: %s", product.key, e)

        await self.store.save_product(product, vectors)
        if vectors is not None:
            self.index.add(product.key, product.embed_text, vectors.name, vectors.image)
        return product

    async def similar(self, text: str | None = None, image_vector: np.ndarray | None = None,
                      k: int = 5) -> list[Match]:
        """Name and/or pack-image search. A photo scan passes both; voice passes text."""
        if not self.embedder or not len(self.index):
            return []
        name = (await self.embedder.queries([text]))[0] if text else None
        return self.index.search(name, image_vector, k=k, image_weight=self.image_weight)
