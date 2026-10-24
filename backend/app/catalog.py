"""Semantic product search: Appwrite Embeddings (nomic-embed-text, 768-d) for vectors,
stored in the TablesDB products table and searched in memory.

Serverless VectorsDB isn't offered in the Singapore region, and a few thousand products
fit comfortably in memory. Embeddings rank candidates only: they score Coke Zero vs
regular Coke at ~0.74, so the agent must confirm the exact variant before reusing one.
"""

import base64
from dataclasses import dataclass

import httpx
import numpy as np

from app.config import Settings

DIMENSION = 768


def encode(vector: np.ndarray) -> str:
    return base64.b64encode(vector.astype(np.float32).tobytes()).decode()


def decode(text: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(text), dtype=np.float32)


class Embedder:
    def __init__(self, settings: Settings) -> None:
        self.model = settings.embedding_model
        self.http = httpx.AsyncClient(
            base_url=settings.appwrite_endpoint.rstrip("/"),
            headers={"X-Appwrite-Project": settings.appwrite_project_id,
                     "X-Appwrite-Key": settings.appwrite_api_key},
            timeout=10,
        )

    async def embed(self, texts: list[str]) -> list[np.ndarray]:
        r = await self.http.post("/embeddings/text", json={"texts": texts, "model": self.model})
        r.raise_for_status()
        out = []
        for item in r.json()["embeddings"]:
            if item.get("error"):
                raise RuntimeError(f"Embedding failed: {item['error']}")
            out.append(np.asarray(item["embedding"], dtype=np.float32))
        return out

    async def aclose(self) -> None:
        await self.http.aclose()


@dataclass
class Match:
    key: str
    score: float  # cosine similarity, 1 = identical
    text: str


class VectorIndex:
    def __init__(self) -> None:
        self.keys: list[str] = []
        self.texts: list[str] = []
        self.matrix = np.zeros((0, DIMENSION), dtype=np.float32)

    def add(self, key: str, text: str, vector: np.ndarray) -> None:
        unit = vector / (np.linalg.norm(vector) or 1)
        if key in self.keys:
            i = self.keys.index(key)
            self.texts[i], self.matrix[i] = text, unit
        else:
            self.keys.append(key)
            self.texts.append(text)
            self.matrix = np.vstack([self.matrix, unit])

    def search(self, vector: np.ndarray, k: int = 5) -> list[Match]:
        if not self.keys:
            return []
        scores = self.matrix @ (vector / (np.linalg.norm(vector) or 1))
        top = np.argsort(-scores)[:k]
        return [Match(self.keys[i], float(scores[i]), self.texts[i]) for i in top]

    def __len__(self) -> int:
        return len(self.keys)
