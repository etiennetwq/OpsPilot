# OpsPilot RAG Retrieval Ablation Report

- **Run UTC**: `2026-11-22T09:59:17.702890+00:00`
- **Dataset**: `bgl` (`50000` log rows)
- **Tasks**: `34` (`22` positive, `12` abstention)
- **Dense Index Persistence**: cold/first load `54448.611 ms`, warm disk load `2109.647 ms`

| Retrieval Configuration | Hit@5 | MRR@10 | nDCG@10 | Abstention F1 | P50 (ms) | P95 (ms) |
|---|---:|---:|---:|---:|---:|---:|
| BM25 | 0.5909 | 0.5833 | 0.5986 | 0.1538 | 13.13 | 38.67 |
| LSA (v0.1 baseline) | 0.7727 | 0.6818 | 0.5874 | 0.0000 | 5.39 | 13.76 |
| BM25 + LSA (RRF) | 0.7727 | 0.7727 | 0.6954 | 0.0000 | 7.48 | 31.58 |
| Dense (BAAI/bge-small-en-v1.5) | 0.8182 | 0.7929 | 0.7857 | 0.0000 | 23.95 | 32.5 |
| BM25 + BGE (RRF) | 0.8636 | 0.8202 | 0.7752 | 0.0000 | 11.66 | 34.61 |
| BM25 + BGE (RRF) + Lexical Rerank | 0.8182 | 0.8283 | 0.7714 | 0.0000 | 11.18 | 34.31 |
| BM25 + BGE (RRF) + Cross-Encoder (BAAI/bge-reranker-base) | 0.8636 | 0.8148 | 0.8055 | 0.0000 | 89.65 | 179.25 |
