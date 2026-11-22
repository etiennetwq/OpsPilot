"""
7-configuration RAG retrieval ablation benchmark on BGL 50k (or Apache 2k).

Compares:
  1. BM25
  2. LSA (v0.1 offline baseline)
  3. Dense (BGE)
  4. BM25 + LSA (RRF)
  5. BM25 + BGE (RRF)
  6. BM25 + BGE (RRF) + Lexical Rerank
  7. BM25 + BGE (RRF) + Cross-Encoder Rerank

Reports Hit@5, MRR@10, nDCG@10, Abstention F1, P50/P95 latency (ms), and Cold vs Warm index load time.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from opspilot.agent import Agent


def dcg_at_k(relevances: list[int], k: int) -> float:
    return sum(rel / math.log2(idx + 2) for idx, rel in enumerate(relevances[:k]))


def ndcg_at_k(relevances: list[int], total_relevant: int, k: int) -> float:
    if total_relevant <= 0:
        return 0.0
    ideal = [1] * min(k, total_relevant)
    idcg = dcg_at_k(ideal, k)
    return dcg_at_k(relevances, k) / idcg if idcg > 0 else 0.0


def load_tasks(path: Path) -> list[dict[str, Any]]:
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return json.loads(path.read_text(encoding="utf-8"))


def eval_config(
    agent: Agent,
    tasks: list[dict[str, Any]],
    *,
    mode: str,
    rerank: bool = False,
    rerank_mode: str = "lexical",
) -> dict[str, Any]:
    hit5_vals, mrr10_vals, ndcg10_vals, latencies = [], [], [], []
    abs_tp = abs_fp = abs_fn = 0

    for t in tasks:
        q = t["question"]
        t0 = time.perf_counter()
        results = agent.logs.search(q, mode=mode, k=10, rerank=rerank, rerank_mode=rerank_mode)
        docs = agent.docs.search(q, mode=mode, k=3)
        insufficient = agent._is_insufficient(q, mode, results[:5], docs)
        lat_ms = (time.perf_counter() - t0) * 1000
        latencies.append(lat_ms)

        if t["answerable"]:
            gold = set(t["gold_lines"])
            rels = [1 if r["id"] in gold else 0 for r in results]
            hit5_vals.append(1.0 if any(rels[:5]) else 0.0)
            mrr = next((1.0 / (i + 1) for i, rel in enumerate(rels[:10]) if rel), 0.0)
            mrr10_vals.append(mrr)
            ndcg10_vals.append(ndcg_at_k(rels, len(gold), 10))
            if insufficient:
                abs_fp += 1
        else:
            if insufficient:
                abs_tp += 1
            else:
                abs_fn += 1

    abs_p = abs_tp / (abs_tp + abs_fp) if (abs_tp + abs_fp) else 0.0
    abs_r = abs_tp / (abs_tp + abs_fn) if (abs_tp + abs_fn) else 0.0
    abs_f1 = 2 * abs_p * abs_r / (abs_p + abs_r) if (abs_p + abs_r) else 0.0
    lat_sorted = sorted(latencies)

    return {
        "hit_at_5": round(statistics.mean(hit5_vals), 4) if hit5_vals else 0.0,
        "mrr_at_10": round(statistics.mean(mrr10_vals), 4) if mrr10_vals else 0.0,
        "ndcg_at_10": round(statistics.mean(ndcg10_vals), 4) if ndcg10_vals else 0.0,
        "abstention_f1": round(abs_f1, 4),
        "p50_ms": round(statistics.median(lat_sorted), 2) if lat_sorted else 0.0,
        "p95_ms": round(lat_sorted[int(0.95 * (len(lat_sorted) - 1))], 2) if lat_sorted else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run 7-config RAG retrieval ablation benchmark.")
    parser.add_argument("--tasks", default=str(ROOT / "eval/bgl_tasks.jsonl"))
    parser.add_argument("--embedding-model", default="BAAI/bge-small-en-v1.5")
    parser.add_argument("--reranker-model", default="BAAI/bge-reranker-base")
    parser.add_argument("--skip-neural", action="store_true", help="Run LSA/BM25 baselines only.")
    args = parser.parse_args()

    tasks = load_tasks(Path(args.tasks))
    print(f"Loaded {len(tasks)} evaluation tasks from {args.tasks}")

    rows: list[tuple[str, dict[str, Any]]] = []

    print("Building LSA baseline index...")
    lsa_agent = Agent(embedding="lsa")
    rows.append(("BM25", eval_config(lsa_agent, tasks, mode="keyword")))
    rows.append(("LSA (v0.1 baseline)", eval_config(lsa_agent, tasks, mode="vector")))
    rows.append(("BM25 + LSA (RRF)", eval_config(lsa_agent, tasks, mode="hybrid")))

    cold_ms = warm_ms = None
    if not args.skip_neural:
        print(f"Building/Loading Dense BGE index ({args.embedding_model})...")
        bge_agent = Agent(
            embedding="sentence-transformer",
            model_path=args.embedding_model,
            reranker_model=args.reranker_model,
        )
        cold_ms = bge_agent.logs.index_build_ms

        bge_warm = Agent(
            embedding="sentence-transformer",
            model_path=args.embedding_model,
            reranker_model=args.reranker_model,
        )
        warm_ms = bge_warm.logs.index_build_ms

        rows.append((f"Dense ({args.embedding_model})", eval_config(bge_agent, tasks, mode="vector")))
        rows.append(("BM25 + BGE (RRF)", eval_config(bge_agent, tasks, mode="hybrid")))
        rows.append(("BM25 + BGE (RRF) + Lexical Rerank", eval_config(bge_agent, tasks, mode="hybrid", rerank=True, rerank_mode="lexical")))
        rows.append((f"BM25 + BGE (RRF) + Cross-Encoder ({args.reranker_model})", eval_config(bge_agent, tasks, mode="hybrid", rerank=True, rerank_mode="cross-encoder")))

    report_lines = [
        "# OpsPilot RAG Retrieval Ablation Report",
        "",
        f"- **Run UTC**: `{datetime.now(timezone.utc).isoformat()}`",
        f"- **Dataset**: `{lsa_agent.dataset}` (`{len(lsa_agent.logs.items)}` log rows)",
        f"- **Tasks**: `{len(tasks)}` (`{sum(1 for t in tasks if t['answerable'])}` positive, `{sum(1 for t in tasks if not t['answerable'])}` abstention)",
        f"- **Dense Index Persistence**: cold/first load `{cold_ms} ms`, warm disk load `{warm_ms} ms`",
        "",
        "| Retrieval Configuration | Hit@5 | MRR@10 | nDCG@10 | Abstention F1 | P50 (ms) | P95 (ms) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, m in rows:
        report_lines.append(
            f"| {name} | {m['hit_at_5']:.4f} | {m['mrr_at_10']:.4f} | {m['ndcg_at_10']:.4f} | "
            f"{m['abstention_f1']:.4f} | {m['p50_ms']} | {m['p95_ms']} |"
        )

    out_dir = ROOT / "reports"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "retrieval_ablation.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    (out_dir / "retrieval_ablation.json").write_text(
        json.dumps(
            {
                "dataset": lsa_agent.dataset,
                "rows": len(lsa_agent.logs.items),
                "tasks": len(tasks),
                "cold_index_ms": cold_ms,
                "warm_index_ms": warm_ms,
                "results": [{"config": n, **m} for n, m in rows],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print("\n".join(report_lines))


if __name__ == "__main__":
    main()
