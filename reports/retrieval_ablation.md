# OpsPilot RAG Retrieval Ablation Report

- **Run UTC**: `2026-11-22T12:59:16.156198+00:00`
- **Dataset**: `bgl` (`49874` log rows)
- **Tasks**: `83` (`68` positive, `15` abstention)
- **Dense Index Persistence**: cold/first load `26955.53 ms`, warm disk load `2197.628 ms`

| Retrieval Configuration | Hit@5 | MRR@10 | nDCG@10 | Abstention F1 | P50 (ms) | P95 (ms) |
|---|---:|---:|---:|---:|---:|---:|
| BM25 | 0.6618 | 0.6318 | 0.6383 | 0.2353 | 37.32 | 54.94 |
| LSA (v0.1 baseline) | 0.3676 | 0.3636 | 0.3539 | 0.1250 | 12.15 | 20.39 |
| BM25 + LSA (RRF) | 0.6765 | 0.6236 | 0.5595 | 0.1250 | 28.56 | 44.91 |
| Dense (BAAI/bge-small-en-v1.5) | 0.6324 | 0.6009 | 0.6048 | 0.0000 | 30.97 | 47.84 |
| BM25 + BGE (RRF) | 0.7059 | 0.6908 | 0.6629 | 0.0000 | 36.05 | 52.13 |
| BM25 + BGE (RRF) + Lexical Rerank | 0.7206 | 0.6921 | 0.6859 | 0.0000 | 37.8 | 63.31 |
| BM25 + BGE (RRF) + Cross-Encoder (BAAI/bge-reranker-base) | 0.6912 | 0.6683 | 0.6583 | 0.0000 | 121.51 | 204.2 |
