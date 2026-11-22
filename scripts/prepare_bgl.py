"""
Download Loghub BGL logs, slice a realistic 50,000-line contiguous window preserving
diverse anomaly classes, ingest into SQLite, and build a stratified 72-question benchmark.

Usage:
    python scripts/prepare_bgl.py --rows 50000
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import io
import json
from pathlib import Path
import random
import sys
import tarfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from opspilot.ingest import BGL_LINE, ingest

# Official Zenodo Loghub BGL archives + GitHub Loghub 2k fallback
BGL_ZENODO_URLS = [
    ("BGL.zip", "https://zenodo.org/records/8196385/files/BGL.zip?download=1"),
    ("BGL.tar.gz", "https://zenodo.org/records/3227177/files/BGL.tar.gz?download=1"),
]
BGL_GITHUB_2K_URL = "https://raw.githubusercontent.com/logpai/loghub/master/BGL/BGL_2k.log"

# Semantic query paraphrases per BGL anomaly category / keyword (tests Dense BGE vs BM25!)
_SEMANTIC_TEMPLATES: dict[str, tuple[list[str], list[str]]] = {
    "KERNDTLB": (
        [
            "Find kernel data TLB translation fault interrupts on compute nodes",
            "Which log lines report memory management unit data TLB error interrupts?",
            "Locate data TLB error interrupt hardware exceptions",
            "Search for address translation TLB faults in kernel logs",
        ],
        ["D:bgl_kernel_tlb"],
    ),
    "KERNSTOR": (
        [
            "Find kernel data storage interrupt memory access violations",
            "Locate invalid memory reference or data storage exceptions in KERNEL",
            "Which compute nodes hit data storage interrupt faults?",
            "Investigate kernel memory access fault interrupts",
        ],
        ["D:bgl_kernel_storage"],
    ),
    "APPREAD": (
        [
            "Find ciod application daemon message prefix read failures",
            "Locate I/O node communication read errors in APP logs",
            "Which application jobs failed reading message prefix from ciod?",
            "Search for ciod I/O daemon read stream errors",
        ],
        ["D:bgl_app_io"],
    ),
    "KERNREC": (
        [
            "Find recoverable machine check or DDR memory scrubbing alerts in kernel",
            "Locate corrected ECC or cache parity recovery events",
            "Search for kernel memory or cache check recovery messages",
            "Which logs show recoverable hardware machine check events?",
        ],
        ["D:bgl_memory_ddr", "D:bgl_kernel_tlb"],
    ),
    "KERNMNTF": (
        [
            "Find Lustre or filesystem mount failures reported by kernel",
            "Locate storage mount failure errors during node initialization",
            "Which nodes experienced filesystem mount errors?",
            "Search for kernel storage mount fault records",
        ],
        ["D:bgl_app_io"],
    ),
    "KERNTERM": (
        [
            "Find abnormal kernel termination or rts panic events",
            "Locate compute node kernel panic or unexpected termination faults",
            "Which nodes reported kernel runtime system termination?",
            "Search for rts panic and kernel exit errors",
        ],
        ["D:bgl_kernel_program"],
    ),
    "LINKCARD": (
        [
            "Find interconnect link card or torus network failures",
            "Locate linkcard hardware communication drop errors",
            "Which racks or midplanes show link card faults?",
            "Search for high-speed interconnect link failures",
        ],
        ["D:bgl_linkcard_network"],
    ),
    "APPCHILD": (
        [
            "Find application child process abnormal exit or signal termination",
            "Locate APP subsystem child process crash logs",
            "Which application worker processes exited unexpectedly?",
            "Search for compute job child process termination errors",
        ],
        ["D:bgl_app_io"],
    ),
}

_NEGATIVE_QUESTIONS = [
    "Kubernetes pod OOMKilled container memory limit exceeded",
    "PostgreSQL deadlock detected during concurrent transaction update",
    "Nginx upstream 502 bad gateway connection refused",
    "Redis cluster slot migration timeout and master failover",
    "Kafka consumer group rebalance lag spike on partition 12",
    "AWS S3 AccessDenied 403 IAM role policy mismatch",
    "Docker daemon overlay2 disk quota exceeded on /var/lib/docker",
    "MySQL InnoDB buffer pool corruption and replication lag",
    "Elasticsearch circuit breaking exception heap memory exhausted",
    "TLS certificate expired handshake failure on ingress load balancer",
    "Grafana Prometheus scrape timeout target unreachable",
    "Apache mod_jk workerEnv Tomcat AJP connector timeout",
]


def _download_bgl_raw(target_raw: Path) -> Path:
    if target_raw.exists() and target_raw.stat().st_size > 1_000_000:
        print(f"Using cached raw BGL file: {target_raw}")
        return target_raw

    target_raw.parent.mkdir(parents=True, exist_ok=True)
    archive_path: Path | None = None

    for fname, url in BGL_ZENODO_URLS:
        candidate = target_raw.parent / fname
        if candidate.exists() and candidate.stat().st_size > 1_000_000:
            archive_path = candidate
            break
        try:
            print(f"Downloading official Loghub BGL archive ({fname}, ~58MB) from Zenodo...")
            urllib.request.urlretrieve(url, candidate)
            archive_path = candidate
            break
        except Exception as exc:
            print(f"[WARN] Download failed for {url}: {exc}")
            if candidate.exists():
                candidate.unlink()

    if archive_path is None:
        raise RuntimeError("Failed to download Loghub BGL archive from Zenodo.")

    print(f"Extracting BGL.log from {archive_path.name}...")
    if archive_path.suffix == ".zip":
        with zipfile.ZipFile(archive_path, "r") as zf:
            member = next((n for n in zf.namelist() if n.endswith("BGL.log")), None)
            if member is None:
                raise RuntimeError("BGL.log not found inside downloaded zip archive")
            with zf.open(member, "r") as f, target_raw.open("wb") as out:
                for idx, line in enumerate(f):
                    out.write(line)
                    if idx >= 400_000:
                        break
    else:
        with tarfile.open(archive_path, "r:gz") as tar:
            member = next((m for m in tar.getmembers() if m.name.endswith("BGL.log")), None)
            if member is None:
                raise RuntimeError("BGL.log not found inside downloaded tar archive")
            f = tar.extractfile(member)
            assert f is not None
            with target_raw.open("wb") as out:
                for idx, line in enumerate(f):
                    out.write(line)
                    if idx >= 400_000:
                        break
    return target_raw


def slice_bgl(raw_path: Path, out_path: Path, target_rows: int = 50_000) -> dict[str, int]:
    """
    Build a contiguous multi-window BGL slice of `target_rows` lines that preserves
    realistic normal-to-anomaly ratio and covers diverse anomaly classes.
    """
    all_lines = raw_path.read_text(encoding="utf-8", errors="replace").splitlines()
    # Divide raw file into windows of 2,500 lines and pick windows with rich label diversity
    win_size = 2500
    windows: list[tuple[int, int, set[str]]] = []
    for start in range(0, len(all_lines) - win_size + 1, win_size):
        labels_in_win: set[str] = set()
        for line in all_lines[start : start + win_size]:
            m = BGL_LINE.fullmatch(line)
            if m and m["label"] != "-":
                labels_in_win.add(m["label"])
        windows.append((start, start + win_size, labels_in_win))

    needed_wins = max(1, target_rows // win_size)
    # Greedy selection maximizing distinct anomaly labels, keeping chronological order
    selected_indices: list[int] = []
    covered_labels: set[str] = set()
    remaining = set(range(len(windows)))

    while len(selected_indices) < needed_wins and remaining:
        best_i = max(
            remaining,
            key=lambda i: (len(windows[i][2] - covered_labels), len(windows[i][2]), -i),
        )
        selected_indices.append(best_i)
        covered_labels.update(windows[best_i][2])
        remaining.remove(best_i)

    selected_indices.sort()
    sliced: list[str] = []
    label_counts: Counter[str] = Counter()
    for idx in selected_indices:
        s, e, _ = windows[idx]
        for line in all_lines[s:e]:
            m = BGL_LINE.fullmatch(line)
            if m:
                sliced.append(line)
                label_counts[m["label"]] += 1
            if len(sliced) >= target_rows:
                break
        if len(sliced) >= target_rows:
            break

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(sliced) + "\n", encoding="utf-8")
    return dict(label_counts)


def build_bgl_benchmark(bgl_slice_path: Path, out_jsonl: Path, target_positives: int = 60) -> int:
    """Create a 72-question stratified BGL benchmark (60 positive + 12 negative abstention)."""
    rng = random.Random(42)
    lines = bgl_slice_path.read_text(encoding="utf-8", errors="replace").splitlines()
    by_label: dict[str, list[tuple[int, str, str]]] = defaultdict(list)

    for lineno, line in enumerate(lines, 1):
        m = BGL_LINE.fullmatch(line)
        if not m or m["label"] == "-":
            continue
        by_label[m["label"]].append((lineno, m["component"], m["message"]))

    tasks: list[dict] = []

    # 1. Semantic paraphrased questions for known anomaly classes
    for label, (templates, gold_docs) in _SEMANTIC_TEMPLATES.items():
        if label not in by_label or len(by_label[label]) < 2:
            continue
        gold_lines = [f"L{ln}" for ln, _, _ in by_label[label]]
        for idx, q in enumerate(templates):
            tasks.append(
                {
                    "id": f"BGL-{label}-SEM-{idx + 1}",
                    "question": q,
                    "label": label,
                    "task_type": "semantic_paraphrase",
                    "answerable": True,
                    "gold_lines": gold_lines[:500],
                    "gold_docs": gold_docs,
                }
            )

    # 2. Additional anomaly-class specific queries from observed labels in the slice
    for label, entries in sorted(by_label.items(), key=lambda kv: -len(kv[1])):
        if len(tasks) >= target_positives:
            break
        if len(entries) < 2:
            continue
        gold_lines = [f"L{ln}" for ln, _, _ in entries]
        sample_ln, comp, msg = rng.choice(entries)
        # Extract key technical phrase without copying the whole line verbatim
        words = [w for w in msg.split() if len(w) > 3 and not any(c.isdigit() for c in w)][:6]
        topic = " ".join(words) if words else label
        doc_id = (
            "D:bgl_kernel_tlb"
            if comp == "KERNEL" and "tlb" in msg.lower()
            else "D:bgl_memory_ddr"
            if "ddr" in msg.lower() or "cache" in msg.lower() or "ecc" in msg.lower()
            else "D:bgl_app_io"
            if comp == "APP"
            else "D:bgl_linkcard_network"
            if comp == "LINKCARD" or "torus" in msg.lower()
            else "D:bgl_mmcs_discovery"
            if comp in ("MMCS", "DISCOVERY")
            else "D:bgl_hardware_ras"
            if comp == "HARDWARE"
            else "D:bgl_kernel_storage"
        )
        for variant, q in enumerate(
            [
                f"Investigate {comp} subsystem anomaly related to {topic}",
                f"Find log evidence for {label} fault ({topic}) in {comp}",
            ],
            1,
        ):
            if len(tasks) >= target_positives:
                break
            tasks.append(
                {
                    "id": f"BGL-{label}-OBS-{variant}",
                    "question": q,
                    "label": label,
                    "task_type": "component_fault",
                    "answerable": True,
                    "gold_lines": gold_lines[:500],
                    "gold_docs": [doc_id],
                    "anchor_line": f"L{sample_ln}",
                }
            )

    # 3. Negative / out-of-scope abstention questions
    for idx, nq in enumerate(_NEGATIVE_QUESTIONS, 1):
        tasks.append(
            {
                "id": f"BGL-NEG-{idx:02d}",
                "question": nq,
                "label": None,
                "task_type": "abstention",
                "answerable": False,
                "gold_lines": [],
                "gold_docs": ["D:bgl_limits"],
            }
        )

    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with out_jsonl.open("w", encoding="utf-8") as fh:
        for t in tasks:
            fh.write(json.dumps(t, ensure_ascii=False) + "\n")
    return len(tasks)


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare Loghub BGL 50k dataset and benchmark.")
    parser.add_argument("--rows", type=int, default=50_000, help="Number of BGL rows to slice.")
    parser.add_argument("--use-2k-fallback", action="store_true", help="Use tiny 2k GitHub sample for quick smoke tests.")
    args = parser.parse_args()

    raw_path = ROOT / "data/source/BGL_raw.log"
    slice_path = ROOT / "data/source/BGL_50k.log"

    if args.use_2k_fallback:
        raw_path = ROOT / "data/source/BGL_2k.log"
        if not raw_path.exists():
            urllib.request.urlretrieve(BGL_GITHUB_2K_URL, raw_path)
        slice_path.write_text(raw_path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        print(f"Copied 2k fallback to {slice_path}")
    else:
        _download_bgl_raw(raw_path)
        counts = slice_bgl(raw_path, slice_path, target_rows=args.rows)
        anomaly_total = sum(v for k, v in counts.items() if k != "-")
        print(f"Sliced {args.rows} rows to {slice_path} | anomalies={anomaly_total} across {len(counts) - 1} classes")

    report = ingest(dataset="bgl", source_path=slice_path)
    print("Ingest report:", json.dumps(report, indent=2))

    n_tasks = build_bgl_benchmark(slice_path, ROOT / "eval/bgl_tasks.jsonl")
    print(f"Built {n_tasks}-question stratified benchmark at eval/bgl_tasks.jsonl")


if __name__ == "__main__":
    main()
