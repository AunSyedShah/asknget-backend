"""Embedding providers behind one tiny interface.

    embedder = get_embedder()
    vectors  = embedder.embed_documents(["NARS BB Cream …", ...])
    vector   = embedder.embed_query("tinted moisturiser for dry skin")

Provider is chosen by settings.EMBEDDING_PROVIDER:

  hash    default. Deterministic feature-hashing of words, word bigrams and
          character trigrams — no network, no model download, no key. Gives
          lexical similarity ("creme" ≈ "cream"), not meaning. Good enough to
          develop and test against, and the fallback when a hosted key is missing.
  voyage  Voyage AI (api.voyageai.com) — recommended hosted option.
  openai  OpenAI text-embedding-3-*.

Every provider returns L2-normalised vectors of settings.EMBEDDING_DIMENSIONS
floats so cosine similarity is a plain dot product and pgvector's HNSW index
(fixed width) applies. Hosted providers are asked for that width directly
(Matryoshka truncation); the hash provider is built at that width.

Only product/catalog text ever leaves the box — never customer data.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import urllib.error
import urllib.request
from typing import Protocol

from django.conf import settings

_WORD = re.compile(r"[a-z0-9]+")


def normalise(vec: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in vec))
    return [x / n for x in vec] if n else vec


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class Embedder(Protocol):
    name: str
    #: Cosine similarity below which a neighbour is treated as "unrelated" and
    #: dropped from search results (model-dependent; hosted models never return
    #: values near 0 for unrelated text).
    min_similarity: float

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


# --------------------------------------------------------------------------- #
# Offline default
# --------------------------------------------------------------------------- #


class HashEmbedder:
    """Signed feature hashing (à la HashingVectorizer) — deterministic across
    processes (blake2b, not Python's salted hash())."""

    min_similarity = 0.1  # below this it is character-trigram noise (measured on the demo catalog)

    def __init__(self, dimensions: int):
        self.dimensions = dimensions
        self.name = f"hash-{dimensions}"

    @staticmethod
    def _features(text: str):
        words = _WORD.findall(text.lower())
        for w in words:
            yield "w:" + w, 1.0
            if len(w) >= 4:
                for i in range(len(w) - 2):
                    yield "c:" + w[i : i + 3], 0.35
        for a, b in zip(words, words[1:]):
            yield "b:" + a + " " + b, 0.8

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dimensions
        for feat, weight in self._features(text):
            h = hashlib.blake2b(feat.encode(), digest_size=8).digest()
            idx = int.from_bytes(h[:4], "big") % self.dimensions
            sign = 1.0 if h[4] & 1 else -1.0
            vec[idx] += sign * weight
        return normalise(vec)

    def embed_documents(self, texts):
        return [self._embed(t) for t in texts]

    def embed_query(self, text):
        return self._embed(text)


# --------------------------------------------------------------------------- #
# Hosted providers (plain HTTPS, no SDK)
# --------------------------------------------------------------------------- #


class EmbeddingError(RuntimeError):
    pass


class _HttpEmbedder:
    url: str
    batch_size = 64
    timeout = 60

    def __init__(self, *, api_key: str, model: str, dimensions: int):
        if not api_key:
            raise EmbeddingError(f"{type(self).__name__} needs an API key")
        self.api_key, self.model, self.dimensions = api_key, model, dimensions
        self.name = model

    def _payload(self, texts: list[str], *, query: bool) -> dict:  # pragma: no cover - overridden
        raise NotImplementedError

    def _call(self, texts: list[str], *, query: bool) -> list[list[float]]:
        body = json.dumps(self._payload(texts, query=query)).encode()
        req = urllib.request.Request(
            self.url, data=body, method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                data = json.loads(res.read())
        except urllib.error.HTTPError as e:
            raise EmbeddingError(f"{self.name}: HTTP {e.code} {e.read()[:200]!r}") from e
        except urllib.error.URLError as e:
            raise EmbeddingError(f"{self.name}: {e.reason}") from e
        rows = sorted(data["data"], key=lambda r: r.get("index", 0))
        return [normalise(r["embedding"]) for r in rows]

    def embed_documents(self, texts):
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            out += self._call(texts[i : i + self.batch_size], query=False)
        return out

    def embed_query(self, text):
        return self._call([text], query=True)[0]


class VoyageEmbedder(_HttpEmbedder):
    url = "https://api.voyageai.com/v1/embeddings"
    default_model = "voyage-3.5-lite"
    min_similarity = 0.35

    def _payload(self, texts, *, query):
        return {"input": texts, "model": self.model, "input_type": "query" if query else "document",
                "output_dimension": self.dimensions}


class OpenAIEmbedder(_HttpEmbedder):
    url = "https://api.openai.com/v1/embeddings"
    default_model = "text-embedding-3-small"
    min_similarity = 0.25

    def _payload(self, texts, *, query):
        return {"input": texts, "model": self.model, "dimensions": self.dimensions}


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #

PROVIDERS = {"hash": HashEmbedder, "voyage": VoyageEmbedder, "openai": OpenAIEmbedder}
_cache: dict[tuple, Embedder] = {}


def get_embedder() -> Embedder:
    provider = settings.EMBEDDING_PROVIDER
    dims = settings.EMBEDDING_DIMENSIONS
    key = (provider, settings.EMBEDDING_MODEL, dims)
    if key not in _cache:
        if provider == "hash":
            _cache[key] = HashEmbedder(dims)
        elif provider in PROVIDERS:
            cls = PROVIDERS[provider]
            _cache[key] = cls(
                api_key=getattr(settings, f"{provider.upper()}_API_KEY", ""),
                model=settings.EMBEDDING_MODEL or cls.default_model,
                dimensions=dims,
            )
        else:
            raise EmbeddingError(f"Unknown EMBEDDING_PROVIDER {provider!r}; choose one of {sorted(PROVIDERS)}")
    return _cache[key]


def reset_embedder_cache() -> None:  # tests override settings
    _cache.clear()
