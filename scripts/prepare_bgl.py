"""
Stream the full official Loghub BGL archive (4.7M lines), slice a realistic 50,000-line
multi-window corpus maximizing distinct anomaly classes, ingest into SQLite, and build
an 80-question stratified benchmark (65+ positive + 15 abstention) grounded in real BGL labels.

Usage:
    python scripts/prepare_bgl.py --rows 50000
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import random
import re
import sys
import tarfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from opspilot.ingest import BGL_LINE, ingest

BGL_ZENODO_URLS = [
    ("BGL.zip", "https://zenodo.org/records/8196385/files/BGL.zip?download=1"),
    ("BGL.tar.gz", "https://zenodo.org/records/3227177/files/BGL.tar.gz?download=1"),
]
BGL_GITHUB_2K_URL = "https://raw.githubusercontent.com/logpai/loghub/master/BGL/BGL_2k.log"

# Semantic query paraphrases per known BGL anomaly class (deliberately avoids verbatim copy-paste)
_SEMANTIC_TEMPLATES: dict[str, tuple[list[str], list[str]]] = {
    "KERNDTLB": (
        [
            "Find kernel data TLB translation fault interrupts on compute nodes",
            "Which log lines report memory management unit data TLB error interrupts?",
            "Locate address translation TLB hardware exceptions in KERNEL",
        ],
        ["D:bgl_kernel_tlb"],
    ),
    "KERNSTOR": (
        [
            "Find kernel data storage interrupt memory access violations",
            "Locate invalid memory reference or data storage exceptions in KERNEL",
            "Investigate compute node data storage fault interrupts",
        ],
        ["D:bgl_kernel_storage"],
    ),
    "APPREAD": (
        [
            "Find ciod application daemon message prefix read failures",
            "Locate I/O node communication read errors in APP logs",
            "Which application jobs failed reading message prefix from ciod?",
        ],
        ["D:bgl_app_io"],
    ),
    "KERNREC": (
        [
            "Find recoverable machine check or DDR memory scrubbing alerts in kernel",
            "Locate corrected ECC or cache parity recovery events",
            "Search for kernel memory or cache check recovery messages",
        ],
        ["D:bgl_memory_ddr", "D:bgl_kernel_tlb"],
    ),
    "KERNMNTF": (
        [
            "Find Lustre or filesystem mount failures reported by kernel",
            "Locate storage mount failure errors during node initialization",
        ],
        ["D:bgl_app_io"],
    ),
    "KERNTERM": (
        [
            "Find abnormal kernel termination or rts panic events",
            "Locate compute node kernel panic or unexpected termination faults",
        ],
        ["D:bgl_kernel_program"],
    ),
    "KERNRTSP": (
        [
            "Find runtime system panic or rts internal error failures in kernel",
            "Locate kernel rts panic messages causing node aborts",
        ],
        ["D:bgl_kernel_program"],
    ),
    "KERNMICRO": (
        [
            "Find microcode or processor instruction check failures in kernel",
            "Locate CPU microcode or machine check errors on compute nodes",
        ],
        ["D:bgl_kernel_tlb"],
    ),
    "KERNPRO": (
        [
            "Find program interrupt illegal or privileged instruction traps",
            "Locate CPU opcode or program interrupt faults in kernel logs",
        ],
        ["D:bgl_kernel_program"],
    ),
    "LINKCARD": (
        [
            "Find interconnect link card or torus network failures",
            "Locate linkcard hardware communication drop errors",
        ],
        ["D:bgl_linkcard_network"],
    ),
    "LINKDISC": (
        [
            "Find link card discovery or interconnect initialization errors",
            "Locate disconnected or unreachable linkcard hardware logs",
        ],
        ["D:bgl_linkcard_network", "D:bgl_mmcs_discovery"],
    ),
    "APPCHILD": (
        [
            "Find application child process abnormal exit or signal termination",
            "Locate APP subsystem child process crash logs",
        ],
        ["D:bgl_app_io"],
    ),
    "APPTO": (
        [
            "Find application timeout or unresponsive job daemon errors",
            "Locate APP subsystem timeout failures during execution",
        ],
        ["D:bgl_app_io"],
    ),
    "APPUNAV": (
        [
            "Find unavailable application resource or unreachable node errors",
            "Locate APP resource unavailable failure records",
        ],
        ["D:bgl_app_io"],
    ),
    "MASNORM": (
        [
            "Find master control node state transition or abnormal status alerts",
            "Locate MMCS or master service card alerts during operation",
        ],
        ["D:bgl_mmcs_discovery"],
    ),
    "MONNULL": (
        [
            "Find monitoring daemon null response or missing sensor alerts",
            "Locate hardware monitoring null telemetry errors",
        ],
        ["D:bgl_hardware_ras"],
    ),
    "MONILL": (
        [
            "Find illegal monitor state or invalid sensor reading errors",
            "Locate RAS hardware monitoring illegal value alerts",
        ],
        ["D:bgl_hardware_ras"],
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
    "MongoDB replica set primary election failed due to split brain",
    "RabbitMQ channel closed due to prefetch queue overflow",
    "gRPC deadline exceeded HTTP/2 stream reset by peer",
]

_HEX_RE = re.compile(r"0x[0-9a-fA-F]+|\b[0-9a-fA-F]{8,}\b")
_NUM_RE = re.compile(r"\b\d+\b")
_NODE_RE = re.compile(r"\bR\d{2}-M\d-N[\w:-]+\b")


def _normalize_template(msg: str) -> str:
    t = _NODE_RE.sub("<NODE>", msg)
    t = _HEX_RE.sub("<HEX>", t)
    t = _NUM_RE.sub("<NUM>", t)
    return " ".join(t.split())[:140]


def _ensure_archive(source_dir: Path) -> Path:
    source_dir.mkdir(parents=True, exist_ok=True)
    for fname, _ in BGL_ZENODO_URLS:
        candidate = source_dir / fname
        if candidate.exists() and candidate.stat().st_size > 1_000_000:
            print(f"Using cached BGL archive: {candidate}")
            return candidate

    for fname, url in BGL_ZENODO_URLS:
        candidate = source_dir / fname
        try:
            print(f"Downloading official Loghub BGL archive ({fname}, ~58MB) from Zenodo...")
            urllib.request.urlretrieve(url, candidate)
            return candidate
        except Exception as exc:
            print(f"[WARN] Download failed for {url}: {exc}")
            if candidate.exists():
                candidate.unlink()
    raise RuntimeError("Failed to download Loghub BGL archive from Zenodo.")


def _iter_archive_lines(archive_path: Path):
    if archive_path.suffix == ".zip":
        with zipfile.ZipFile(archive_path, "r") as zf:
            member = next((n for n in zf.namelist() if n.endswith("BGL.log")), None)
            if member is None:
                raise RuntimeError("BGL.log not found inside zip archive")
            with zf.open(member, "r") as fh:
                for raw in fh:
                    yield raw.decode("utf-8", errors="replace").rstrip("\r\n")
    else:
        with tarfile.open(archive_path, "r:gz") as tar:
            member = next((m for m in tar.getmembers() if m.name.endswith("BGL.log")), None)
            if member is None:
                raise RuntimeError("BGL.log not found inside tar archive")
            fh = tar.extractfile(member)
            assert fh is not None
            for raw in fh:
                yield raw.decode("utf-8", errors="replace").rstrip("\r\n")


def slice_bgl_from_archive(archive_path: Path, out_path: Path, target_rows: int = 50_000) -> dict[str, int]:
    """
    Two-pass streaming slice across the entire 4.7M-line BGL log:
      Pass 1: Index 1,000-line contiguous windows and record which distinct anomaly labels appear in each.
      Pass 2: Greedily pick windows maximizing distinct anomaly labels (keeping chronological order)
              until `target_rows` lines are collected.
    """
    win_size = 1000
    print(f"Scanning full {archive_path.name} to maximize anomaly diversity across windows...")
    win_labels: list[set[str]] = []
    cur_labels: set[str] = set()
    cur_count = 0
    total_scanned = 0

    for line in _iter_archive_lines(archive_path):
        total_scanned += 1
        sp = line.find(" ")
        if sp > 0:
            lbl = line[:sp]
            if lbl != "-":
                cur_labels.add(lbl)
        cur_count += 1
        if cur_count == win_size:
            win_labels.append(cur_labels)
            cur_labels = set()
            cur_count = 0

    print(f"Scanned {total_scanned:,} lines ({len(win_labels):,} windows).")
    needed_wins = max(1, target_rows // win_size)
    selected: list[int] = []
    covered: set[str] = set()
    remaining = set(range(len(win_labels)))

    # First pick windows that add brand-new anomaly labels; then pick diverse anomaly-rich windows
    while len(selected) < needed_wins and remaining:
        best_i = max(
            remaining,
            key=lambda i: (len(win_labels[i] - covered), len(win_labels[i]), -i),
        )
        selected.append(best_i)
        covered.update(win_labels[best_i])
        remaining.remove(best_i)

    selected_set = set(selected)
    print(f"Selected {len(selected_set)} windows covering {len(covered)} distinct anomaly labels.")

    sliced: list[str] = []
    label_counts: Counter[str] = Counter()
    win_idx = 0
    in_win_offset = 0

    for line in _iter_archive_lines(archive_path):
        if win_idx in selected_set:
            m = BGL_LINE.fullmatch(line)
            if m:
                sliced.append(line)
                label_counts[m["label"]] += 1
                if len(sliced) >= target_rows:
                    break
        in_win_offset += 1
        if in_win_offset == win_size:
            win_idx += 1
            in_win_offset = 0
            if win_idx > max(selected_set):
                break

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(sliced) + "\n", encoding="utf-8")
    return dict(label_counts)


def _guess_doc_id(comp: str, msg: str) -> str:
    low = msg.lower()
    if comp == "KERNEL" and ("tlb" in low or "machine check" in low):
        return "D:bgl_kernel_tlb"
    if any(w in low for w in ("ddr", "cache", "ecc", "edram", "parity")):
        return "D:bgl_memory_ddr"
    if comp == "APP" or "ciod" in low or "mount" in low or "lustre" in low:
        return "D:bgl_app_io"
    if comp == "LINKCARD" or "torus" in low or "link" in low:
        return "D:bgl_linkcard_network"
    if comp in ("MMCS", "DISCOVERY") or "discovery" in low:
        return "D:bgl_mmcs_discovery"
    if comp == "HARDWARE" or any(w in low for w in ("temp", "fan", "power", "voltage")):
        return "D:bgl_hardware_ras"
    if any(w in low for w in ("illegal", "program interrupt", "panic", "rts")):
        return "D:bgl_kernel_program"
    return "D:bgl_kernel_storage"


def _extract_topic_phrase(template: str) -> str:
    cleaned = re.sub(r"<[A-Z]+>", " ", template)
    cleaned = re.sub(r"[^A-Za-z0-9_/. -]+", " ", cleaned)
    words = [w for w in cleaned.split() if len(w) >= 3 and w.lower() not in {"the", "and", "for", "with", "from"}]
    return " ".join(words[:7])


def build_bgl_benchmark(
    bgl_slice_path: Path,
    out_jsonl: Path,
    target_positives: int = 68,
) -> tuple[int, int]:
    """
    Build a genuinely diverse 80+ question stratified BGL benchmark:
      - Semantic paraphrases for known BGL anomaly classes
      - Template-clustered fault queries grounded to specific (label, template) occurrences
      - Component-scoped symptom queries
      - 15 out-of-scope abstention queries
    """
    rng = random.Random(42)
    lines = bgl_slice_path.read_text(encoding="utf-8", errors="replace").splitlines()

    by_label: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
    by_template: dict[tuple[str, str, str], list[int]] = defaultdict(list)

    for lineno, line in enumerate(lines, 1):
        m = BGL_LINE.fullmatch(line)
        if not m or m["label"] == "-":
            continue
        lbl = m["label"]
        comp = m["component"]
        msg = m["message"]
        by_label[lbl].append((lineno, comp, msg))
        tmpl = _normalize_template(msg)
        by_template[(lbl, comp, tmpl)].append(lineno)

    tasks: list[dict] = []
    seen_questions: set[str] = set()

    def add_task(task: dict) -> None:
        q_norm = task["question"].strip().lower()
        if q_norm in seen_questions:
            return
        seen_questions.add(q_norm)
        tasks.append(task)

    # 1. Semantic paraphrased questions for known anomaly classes present in the slice
    for label, (templates, gold_docs) in _SEMANTIC_TEMPLATES.items():
        if label not in by_label or len(by_label[label]) < 2:
            continue
        gold_lines = [f"L{ln}" for ln, _, _ in by_label[label]]
        for idx, q in enumerate(templates, 1):
            add_task(
                {
                    "id": f"BGL-{label}-SEM-{idx}",
                    "question": q,
                    "label": label,
                    "task_type": "semantic_paraphrase",
                    "answerable": True,
                    "gold_lines": gold_lines[:500],
                    "gold_docs": gold_docs,
                }
            )

    # 2. Distinct (label, component, template) fault clusters — each targets its own exact template lines!
    sorted_clusters = sorted(by_template.items(), key=lambda kv: -len(kv[1]))
    query_styles = [
        lambda comp, topic: f"Find {comp} fault logs reporting {topic}",
        lambda comp, topic: f"Which compute nodes experienced {topic} in {comp}?",
        lambda comp, topic: f"Investigate {comp} errors related to {topic}",
        lambda comp, topic: f"Locate RAS log evidence for {topic} ({comp} subsystem)",
    ]

    cluster_idx = 0
    for (lbl, comp, tmpl), line_nums in sorted_clusters:
        if len(tasks) >= target_positives:
            break
        topic = _extract_topic_phrase(tmpl)
        if len(topic.split()) < 2:
            continue
        doc_id = _guess_doc_id(comp, tmpl)
        q = query_styles[cluster_idx % len(query_styles)](comp, topic)
        cluster_idx += 1
        add_task(
            {
                "id": f"BGL-{lbl}-CLU-{cluster_idx:02d}",
                "question": q,
                "label": lbl,
                "task_type": "template_cluster",
                "answerable": True,
                "gold_lines": [f"L{ln}" for ln in line_nums[:500]],
                "gold_docs": [doc_id],
            }
        )

    # 3. If still under target_positives, add a second distinct phrasing for the richest templates
    extra_idx = 0
    for (lbl, comp, tmpl), line_nums in sorted_clusters:
        if len(tasks) >= target_positives:
            break
        if len(line_nums) < 3:
            continue
        topic = _extract_topic_phrase(tmpl)
        if len(topic.split()) < 3:
            continue
        # Drop the first word or reorder to create a harder lexical-mismatch paraphrase
        words = topic.split()
        rng.shuffle(words)
        rephrased = " ".join(words[:5])
        extra_idx += 1
        add_task(
            {
                "id": f"BGL-{lbl}-VAR-{extra_idx:02d}",
                "question": f"Troubleshoot {comp} anomaly involving {rephrased}",
                "label": lbl,
                "task_type": "rephrased_cluster",
                "answerable": True,
                "gold_lines": [f"L{ln}" for ln in line_nums[:500]],
                "gold_docs": [_guess_doc_id(comp, tmpl)],
            }
        )

    n_pos = len(tasks)

    # 4. Out-of-scope abstention queries
    for idx, nq in enumerate(_NEGATIVE_QUESTIONS, 1):
        add_task(
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
    return n_pos, len(tasks) - n_pos


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare diverse Loghub BGL 50k dataset and 80+ question benchmark.")
    parser.add_argument("--rows", type=int, default=50_000, help="Number of BGL rows to slice.")
    parser.add_argument("--target-positives", type=int, default=68, help="Target number of positive queries.")
    parser.add_argument("--use-2k-fallback", action="store_true", help="Use tiny 2k GitHub sample for quick smoke tests.")
    args = parser.parse_args()

    slice_path = ROOT / "data/source/BGL_50k.log"

    if args.use_2k_fallback:
        raw_path = ROOT / "data/source/BGL_2k.log"
        if not raw_path.exists():
            urllib.request.urlretrieve(BGL_GITHUB_2K_URL, raw_path)
        slice_path.write_text(raw_path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        print(f"Copied 2k fallback to {slice_path}")
    else:
        archive = _ensure_archive(ROOT / "data/source")
        counts = slice_bgl_from_archive(archive, slice_path, target_rows=args.rows)
        anomaly_total = sum(v for k, v in counts.items() if k != "-")
        distinct_anomalies = sum(1 for k in counts if k != "-")
        print(f"Sliced {args.rows} rows to {slice_path} | anomalies={anomaly_total} across {distinct_anomalies} distinct classes")

    report = ingest(dataset="bgl", source_path=slice_path)
    print("Ingest report:", json.dumps(report, indent=2))

    n_pos, n_neg = build_bgl_benchmark(slice_path, ROOT / "eval/bgl_tasks.jsonl", target_positives=args.target_positives)
    print(f"Built {n_pos + n_neg}-question benchmark ({n_pos} positive + {n_neg} abstention) at eval/bgl_tasks.jsonl")


if __name__ == "__main__":
    main()
