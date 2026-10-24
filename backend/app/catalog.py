"""Semantic product search with EmbeddingGemma 2 (text + vision, run in-process).

Each product can have two vectors in the same 768-d space: one for its name and one for
a photo of its pack. A photo scan scores image-to-image and name-to-name and blends them;
a voice query (text only) scores against both the name and the pack image.

Measured on 13 products and 25 real-life photos, separate vectors kept about twice the
margin between the right product and the nearest wrong one compared with one fused
vector, and images separated look-alike variants (Coke Zero vs regular Coke) better
than names did. Embeddings rank candidates only: the agent still confirms the variant.

Vectors are stored in the TablesDB products table and searched in memory, since
serverless VectorsDB isn't offered in the Singapore region.
"""

import asyncio
import base64
import io
import logging
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageOps

from app.config import Settings

log = logging.getLogger(__name__)

DIMENSION = 768


def encode(vector: np.ndarray) -> str:
    return base64.b64encode(vector.astype(np.float32).tobytes()).decode()


def decode(text: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(text), dtype=np.float32)


def _unit(v: np.ndarray) -> np.ndarray:
    return v / (np.linalg.norm(v) or 1)


class Embedder:
    """EmbeddingGemma 2 without the audio encoder (440M params, ~3.3 GB RAM on CPU).

    The model is loaded on first use, and calls are serialised: one model on a small
    CPU box gains nothing from running encodes in parallel.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.model_id = settings.embedding_model
        self._model = None
        self._lock = asyncio.Lock()

    def _load(self):
        if self._model is None:
            import torch
            from sentence_transformers import SentenceTransformer

            model = SentenceTransformer(
                self.model_id, device=self.settings.embedding_device,
                config_kwargs={"audio_config": None}, model_kwargs={"torch_dtype": torch.float32},
            )
            # Fewer image tokens: 140 kept accuracy and cut CPU time from 7.5 s to 2.9 s on 2 vCPUs.
            processor = model[0].processor
            processor.image_processor.max_soft_tokens = self.settings.embedding_image_tokens
            processor.image_processor.image_seq_length = self.settings.embedding_image_tokens
            processor.image_seq_length = self.settings.embedding_image_tokens
            self._model = model
            log.info("Loaded %s on %s", self.model_id, self.settings.embedding_device)
        return self._model

    async def load(self) -> None:
        await asyncio.to_thread(self._load)

    async def _encode(self, items, **kwargs) -> list[np.ndarray]:
        async with self._lock:
            out = await asyncio.to_thread(
                lambda: self._load().encode(items, normalize_embeddings=True, convert_to_numpy=True, **kwargs)
            )
        return [np.asarray(v, dtype=np.float32) for v in out]

    async def queries(self, texts: list[str]) -> list[np.ndarray]:
        return await self._encode(texts, prompt_name="SearchQuery")

    async def documents(self, products: list[tuple[str, str]]) -> list[np.ndarray]:
        """(title, text) pairs, e.g. ("Kurkure", "Kurkure Masala Munch")."""
        return await self._encode([f"title: {title or 'none'} | text: {text}" for title, text in products])

    async def images(self, images: list[bytes]) -> list[np.ndarray]:
        pil = [ImageOps.exif_transpose(Image.open(io.BytesIO(b))).convert("RGB") for b in images]
        return await self._encode([{"image": im} for im in pil])


@dataclass
class Match:
    key: str
    score: float  # blended cosine similarity, 1 = identical
    text: str
    name_score: float | None = None
    image_score: float | None = None


class VectorIndex:
    def __init__(self) -> None:
        self.keys: list[str] = []
        self.texts: list[str] = []
        self.names = np.zeros((0, DIMENSION), dtype=np.float32)
        self.images = np.zeros((0, DIMENSION), dtype=np.float32)
        self.has_name = np.zeros(0, dtype=bool)
        self.has_image = np.zeros(0, dtype=bool)

    def add(self, key: str, text: str, name: np.ndarray | None = None, image: np.ndarray | None = None) -> None:
        """Add or update a product. A vector left as None keeps the stored one."""
        if key not in self.keys:
            self.keys.append(key)
            self.texts.append(text)
            self.names = np.vstack([self.names, np.zeros(DIMENSION, dtype=np.float32)])
            self.images = np.vstack([self.images, np.zeros(DIMENSION, dtype=np.float32)])
            self.has_name = np.append(self.has_name, False)
            self.has_image = np.append(self.has_image, False)
        i = self.keys.index(key)
        self.texts[i] = text
        if name is not None:
            self.names[i], self.has_name[i] = _unit(name), True
        if image is not None:
            self.images[i], self.has_image[i] = _unit(image), True

    def search(self, name: np.ndarray | None = None, image: np.ndarray | None = None,
               k: int = 5, image_weight: float = 0.5) -> list[Match]:
        """Photo scan: pass both (blended per product, using whichever vectors it has).
        Voice: pass name only (averaged over the product's name and pack image)."""
        if not self.keys or (name is None and image is None):
            return []
        n = len(self.keys)
        name_scores = np.full(n, np.nan)
        image_scores = np.full(n, np.nan)
        if name is not None:
            q = _unit(name)
            name_scores = np.where(self.has_name, self.names @ q, np.nan)
            if image is None:  # text-only query: also compare against pack images (cross-modal)
                image_scores = np.where(self.has_image, self.images @ q, np.nan)
        if image is not None:
            image_scores = np.where(self.has_image, self.images @ _unit(image), np.nan)

        w = image_weight if (name is not None and image is not None) else 0.5
        both = ~np.isnan(name_scores) & ~np.isnan(image_scores)
        blended = np.where(both, w * image_scores + (1 - w) * name_scores,
                           np.where(np.isnan(name_scores), image_scores, name_scores))
        valid = ~np.isnan(blended)
        order = [i for i in np.argsort(-np.where(valid, blended, -np.inf)) if valid[i]][:k]
        return [Match(self.keys[i], float(blended[i]), self.texts[i],
                      None if np.isnan(name_scores[i]) else float(name_scores[i]),
                      None if np.isnan(image_scores[i]) else float(image_scores[i])) for i in order]

    def __len__(self) -> int:
        return len(self.keys)
