"""
BM25 + dense vector retrieval (LSA or SentenceTransformer/BGE) with:
- Optional FAISS / NumPy on-disk vector index persistence (essential for 50k+ BGL logs)
- Reciprocal Rank Fusion (RRF)
- Lexical OR neural Cross-Encoder reranking (e.g., BAAI/bge-reranker-base)
- Query embedding & search LRU caching
"""

from __future__ import annotations

from collections import Counter, OrderedDict
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
from typing import Any

import numpy as np
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", text.lower())


def _corpus_fingerprint(texts: list[str], embedding: str, model_path: str | None) -> str:
    h = hashlib.sha256()
    h.update(f"{embedding}|{model_path or ''}|{len(texts)}|".encode("utf-8"))
    step = max(1, len(texts) // 200)
    for t in texts[::step]:
        h.update(t[:120].encode("utf-8", errors="ignore"))
        h.update(b"\n")
    return h.hexdigest()[:16]


class Retriever:
    def __init__(
        self,
        items: list[dict[str, Any]],
        embedding: str = "lsa",
        model_path: str | None = None,
        index_dir: str | Path | None = None,
        reranker_model: str | None = None,
        cache_size: int = 256,
    ) -> None:
        self.items = items
        self.text = [x["text"] for x in items]
        self.embedding = embedding
        self.model_path = model_path
        self.index_dir = Path(index_dir) if index_dir else None
        self.reranker_model = reranker_model or os.getenv("OPSPILOT_RERANKER_MODEL")
        self._cross_encoder = None
        self._faiss_index = None
        self._query_vec_cache: OrderedDict[str, np.ndarray] = OrderedDict()
        self._cache_size = max(16, cache_size)
        self.index_loaded_from_disk = False
        self.index_build_ms = 0.0

        # Inverted index for fast BM25 on 50k+ rows (avoids scanning every doc per query!)
        self.tf: list[Counter[str]] = [Counter(tokens(s)) for s in self.text]
        self.length = np.array([sum(c.values()) for c in self.tf], dtype=np.float32)
        self.avg = max(float(self.length.mean()) if len(self.length) else 1.0, 1.0)
        self.df: Counter[str] = Counter()
        self._postings: dict[str, list[tuple[int, int]]] = {}
        for idx, counter in enumerate(self.tf):
            for term, freq in counter.items():
                self.df[term] += 1
                self._postings.setdefault(term, []).append((idx, freq))

        build_start = time.perf_counter()
        if embedding == "sentence-transformer":
            from sentence_transformers import SentenceTransformer

            local_only = bool(model_path and Path(model_path).exists())
            model_id = model_path or "BAAI/bge-small-en-v1.5"
            self.encoder = SentenceTransformer(model_id, local_files_only=local_only)
            self.vectors = self._load_or_build_dense_vectors(model_id)
        elif embedding == "lsa":
            self.encoder = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True)
            mat = self.encoder.fit_transform(self.text)
            n_comp = max(1, min(64, mat.shape[0] - 1, mat.shape[1] - 1))
            self.svd = TruncatedSVD(n_components=n_comp, random_state=42)
            self.vectors = normalize(self.svd.fit_transform(mat)).astype(np.float32)
        else:
            raise ValueError(f"Unknown embedding: {embedding}")

        self._build_faiss_index_if_available()
        self.index_build_ms = round((time.perf_counter() - build_start) * 1000, 3)

    def _load_or_build_dense_vectors(self, model_id: str) -> np.ndarray:
        fp = _corpus_fingerprint(self.text, self.embedding, model_id)
        npy_path = self.index_dir / f"dense_{fp}.npy" if self.index_dir else None

        if npy_path and npy_path.exists():
            arr = np.load(npy_path).astype(np.float32)
            if arr.shape[0] == len(self.text):
                self.index_loaded_from_disk = True
                return arr

        vecs = self.encoder.encode(
            self.text,
            batch_size=128,
            normalize_embeddings=True,
            show_progress_bar=len(self.text) >= 5000,
        )
        arr = np.asarray(vecs, dtype=np.float32)
        if npy_path:
            npy_path.parent.mkdir(parents=True, exist_ok=True)
            np.save(npy_path, arr)
            meta = {"embedding": self.embedding, "model": model_id, "rows": len(self.text), "dim": int(arr.shape[1])}
            (npy_path.with_suffix(".meta.json")).write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return arr

    def _build_faiss_index_if_available(self) -> None:
        if len(self.vectors) == 0:
            return
        try:
            import faiss

            dim = int(self.vectors.shape[1])
            index = faiss.IndexFlatIP(dim)
            index.add(np.ascontiguousarray(self.vectors, dtype=np.float32))
            self._faiss_index = index
        except ImportError:
            self._faiss_index = None

    def _encode_query(self, q: str) -> np.ndarray:
        cached = self._query_vec_cache.get(q)
        if cached is not None:
            self._query_vec_cache.move_to_end(q)
            return cached

        if self.embedding == "lsa":
            v = normalize(self.svd.transform(self.encoder.transform([q])))[0].astype(np.float32)
        else:
            v = np.asarray(self.encoder.encode([q], normalize_embeddings=True)[0], dtype=np.float32)

        self._query_vec_cache[q] = v
        if len(self._query_vec_cache) > self._cache_size:
            self._query_vec_cache.popitem(last=False)
        return v

    def _bm25_scores(self, q: str) -> np.ndarray:
        n_docs = len(self.items)
        bm = np.zeros(n_docs, dtype=np.float32)
        if n_docs == 0:
            return bm
        for term in tokens(q):
            postings = self._postings.get(term)
            if not postings:
                continue
            df = self.df[term]
            idf = math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))
            for doc_idx, freq in postings:
                denom = freq + 1.5 * (0.25 + 0.75 * float(self.length[doc_idx]) / self.avg)
                bm[doc_idx] += idf * (freq * 2.5) / denom
        return bm

    def scores(self, q: str) -> tuple[np.ndarray, np.ndarray]:
        bm = self._bm25_scores(q)
        qv = self._encode_query(q)
        vec = np.maximum(self.vectors @ qv, 0.0) if len(self.vectors) else np.zeros(0, dtype=np.float32)
        return bm, vec

    def _top_vector_candidates(self, qv: np.ndarray, top_n: int) -> list[tuple[int, float]]:
        if len(self.items) == 0:
            return []
        cap = min(top_n, len(self.items))
        if self._faiss_index is not None:
            sims, idxs = self._faiss_index.search(np.ascontiguousarray(qv.reshape(1, -1), dtype=np.float32), cap)
            out = []
            for idx, sim in zip(idxs[0].tolist(), sims[0].tolist()):
                if idx >= 0 and sim > 1e-9:
                    out.append((idx, float(sim)))
            return out
        vec = np.maximum(self.vectors @ qv, 0.0)
        top_idx = np.argpartition(-vec, min(cap, len(vec) - 1))[:cap] if len(vec) > cap else np.arange(len(vec))
        ranked = sorted(top_idx.tolist(), key=lambda i: (-float(vec[i]), i))
        return [(i, float(vec[i])) for i in ranked if vec[i] > 1e-9]

    def _top_bm25_candidates(self, q: str, top_n: int) -> list[tuple[int, float]]:
        bm = self._bm25_scores(q)
        if len(bm) == 0:
            return []
        nonzero = np.flatnonzero(bm > 1e-9)
        if len(nonzero) == 0:
            return []
        ranked = sorted(nonzero.tolist(), key=lambda i: (-float(bm[i]), i))[:top_n]
        return [(i, float(bm[i])) for i in ranked]

    def _get_cross_encoder(self):
        if self._cross_encoder is None:
            from sentence_transformers import CrossEncoder

            model_id = self.reranker_model or "BAAI/bge-reranker-base"
            self._cross_encoder = CrossEncoder(model_id)
        return self._cross_encoder

    def search(
        self,
        q: str,
        mode: str = "hybrid",
        k: int = 5,
        rerank: bool = False,
        rerank_mode: str = "lexical",
    ) -> list[dict[str, Any]]:
        if mode not in ("keyword", "vector", "hybrid"):
            raise ValueError(f"Unknown mode: {mode}")

        candidate_depth = max(k, 50 if (rerank and rerank_mode == "cross-encoder") else (20 if rerank else k))
        scores_map: dict[int, float] = {}

        if mode == "keyword":
            for idx, sc in self._top_bm25_candidates(q, candidate_depth):
                scores_map[idx] = sc
        elif mode == "vector":
            qv = self._encode_query(q)
            for idx, sc in self._top_vector_candidates(qv, candidate_depth):
                scores_map[idx] = sc
        else:
            qv = self._encode_query(q)
            bm_list = self._top_bm25_candidates(q, max(candidate_depth, 100))
            vec_list = self._top_vector_candidates(qv, max(candidate_depth, 100))
            for ranked_list in (bm_list, vec_list):
                for rank, (idx, _) in enumerate(ranked_list, 1):
                    scores_map[idx] = scores_map.get(idx, 0.0) + 1.0 / (60.0 + rank)

        ids = sorted(scores_map.keys(), key=lambda i: (-scores_map[i], i))[:candidate_depth]

        if rerank and ids:
            if rerank_mode == "cross-encoder":
                ce = self._get_cross_encoder()
                pairs = [(q, self.text[i]) for i in ids]
                ce_scores = ce.predict(pairs).tolist()
                scored = sorted(zip(ids, ce_scores), key=lambda pair: (-float(pair[1]), pair[0]))
                return [
                    dict(self.items[i], score=float(scores_map[i]), rerank_score=float(rs))
                    for i, rs in scored[:k]
                ]
            # Fast local lexical reranker baseline
            qt = set(tokens(q))
            ids.sort(key=lambda i: (-len(qt.intersection(self.tf[i])) / max(len(qt), 1), -scores_map[i], i))

        return [dict(self.items[i], score=float(scores_map[i])) for i in ids[:k]]
