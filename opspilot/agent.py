"""Bounded evidence workflow agent supporting both Apache 2k and Loghub BGL 50k corpora."""

from __future__ import annotations

import json
import os
from pathlib import Path
import time

from .ingest import DB, ROOT, ingest, redact
from .llm import generate
from .retrieval import Retriever
from .tools import LogTools


class Agent:
    def __init__(
        self,
        db: Path = DB,
        embedding: str | None = None,
        model_path: str | None = None,
        reranker_model: str | None = None,
    ) -> None:
        if not Path(db).exists():
            ingest(db)
        self.tools = LogTools(db)
        self.dataset = self.tools.dataset
        emb = embedding or os.getenv("OPSPILOT_EMBEDDING", "lsa")
        mpath = model_path or os.getenv("OPSPILOT_EMBEDDING_MODEL")
        index_dir = ROOT / "data" / "vector_index"

        log_items = [
            {
                "id": f"L{r['line']}",
                "line": r["line"],
                "time": r["time"],
                "level": r["level"],
                "text": r["message"],
            }
            for r in self.tools.all()
        ]
        self.logs = Retriever(
            log_items,
            embedding=emb,
            model_path=mpath,
            index_dir=index_dir,
            reranker_model=reranker_model,
        )

        docs_file = ROOT / ("kb/bgl_documents.json" if self.dataset == "bgl" else "kb/documents.json")
        if not docs_file.exists():
            docs_file = ROOT / "kb/documents.json"
        self.docs = Retriever(
            json.loads(docs_file.read_text(encoding="utf-8")),
            embedding=emb,
            model_path=mpath,
            index_dir=index_dir,
            reranker_model=reranker_model,
        )

    def _is_insufficient(
        self,
        question: str,
        mode: str,
        logs: list[dict],
        docs: list[dict],
    ) -> bool:
        if not logs or not docs:
            return True
        bm, vec = self.logs.scores(question)
        max_bm = float(bm.max()) if len(bm) else 0.0
        max_vec = float(vec.max()) if len(vec) else 0.0

        if mode == "keyword":
            return max_bm < 0.5
        if mode == "vector":
            # Neural BGE embeddings have reliable semantic cosine similarity; LSA char n-grams need lexical check
            if self.logs.embedding == "sentence-transformer":
                return max_vec < 0.58
            return max_bm < 0.5
        # Hybrid: accept if lexical overlap holds OR (for neural embeddings only) strong semantic match holds
        if self.logs.embedding == "sentence-transformer":
            return max_bm < 0.5 and max_vec < 0.62
        return max_bm < 0.5

    def diagnose(
        self,
        question: str,
        mode: str = "hybrid",
        rerank: bool = False,
        llm: bool = False,
        k: int = 5,
        config=None,
        rerank_mode: str = "lexical",
    ) -> dict:
        start = time.perf_counter()
        question = redact(question)
        logs = self.logs.search(question, mode, k, rerank=rerank, rerank_mode=rerank_mode)
        docs = self.docs.search(question, mode, 3, rerank=rerank, rerank_mode=rerank_mode)
        trace = [
            {"tool": "retrieve_logs", "mode": mode, "ids": [x["id"] for x in logs]},
            {"tool": "retrieve_docs", "ids": [x["id"] for x in docs]},
        ]
        insufficient = self._is_insufficient(question, mode, logs, docs)
        context = []
        stats = []
        if mode == "hybrid" and not insufficient:
            context = self.tools.context(logs[0]["line"])
            stats = self.tools.stats()
            trace += [
                {"tool": "context", "line": logs[0]["line"], "radius": 2},
                {"tool": "stats", "result": stats},
            ]
        if mode == "keyword" and not insufficient:
            stats = self.tools.stats()
            trace.append({"tool": "stats", "result": stats})

        usage = {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
        error = None
        if insufficient:
            answer = {
                "summary": f"证据不足，无法从当前 {self.dataset.upper()} 日志样本确认该现象。",
                "hypotheses": [],
                "next_checks": ["提供相应系统、时间窗口及已脱敏日志。"],
                "citations": [],
            }
        else:
            answer = {
                "summary": f"日志 {logs[0]['id']} 在 {logs[0]['time']} 记录：{logs[0]['text']}。这是可观察现象，根因尚未确认。",
                "hypotheses": ["需结合版本、配置、相邻时间记录及组件状态验证；单一日志行不能证明因果关系。"],
                "next_checks": [
                    "只读核对相关配置和组件版本。",
                    "对照相邻日志、错误级别计数及参考文档。",
                    docs[0]["text"],
                ],
                "citations": [x["id"] for x in logs + docs],
            }
            if llm:
                try:
                    answer, usage = generate(question, logs, docs, config=config)
                except ValueError as e:
                    error = str(e)
                    usage = {"prompt_tokens": None, "completion_tokens": None, "cost_usd": None}

        return {
            "question": question,
            "dataset": self.dataset,
            "mode": mode,
            "generator": "llm" if llm and not error and not insufficient else "offline-extractive",
            "embedding": self.logs.embedding,
            "insufficient_evidence": insufficient,
            "answer": answer,
            "logs": logs,
            "documents": docs,
            "context": context,
            "statistics": stats,
            "trace": trace,
            "usage": usage,
            "provider_error": error,
            "latency_ms": round((time.perf_counter() - start) * 1000, 3),
            "warning": f"{self.dataset.upper()} 日志样本；仅事件证据检索，无确认根因标签。引用 ID 校验不等于语义正确性校验。",
        }
