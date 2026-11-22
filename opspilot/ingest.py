"""Import raw Apache or Loghub BGL logs into SQLite without leaking upstream labels into runtime indexes."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/index.sqlite"

APACHE_LINE = re.compile(r"^\[(?P<time>[^]]+)\] \[(?P<level>[^]]+)\] (?P<message>.*)$")
# Loghub BGL raw format:
# <label> <timestamp> <date> <node> <time> <node_repeat> <type> <component> <level> <content>
BGL_LINE = re.compile(
    r"^(?P<label>\S+)\s+(?P<ts>\d+)\s+(?P<date>\S+)\s+(?P<node>\S+)\s+"
    r"(?P<time>\S+)\s+(?P<node2>\S+)\s+(?P<type>\S+)\s+(?P<component>\S+)\s+"
    r"(?P<level>\S+)\s+(?P<message>.*)$"
)

_LEVEL_MAP = {
    "FATAL": "error",
    "FAILURE": "error",
    "SEVERE": "error",
    "ERROR": "error",
    "WARN": "warn",
    "WARNING": "warn",
    "INFO": "info",
    "NOTICE": "notice",
    "DEBUG": "debug",
}


def redact(text: str) -> str:
    def ip(m: re.Match[str]) -> str:
        try:
            ipaddress.ip_address(m[0])
            return "<IP>"
        except ValueError:
            return m[0]

    text = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", ip, text)

    def ipv6(m: re.Match[str]) -> str:
        try:
            addr = ipaddress.ip_address(m[0])
            return "<IPV6>" if addr.version == 6 else m[0]
        except ValueError:
            return m[0]

    text = re.sub(r"(?i)(?:[0-9a-f]{1,4}:){2,}[0-9a-f:]*", ipv6, text)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "<EMAIL>", text)
    text = re.sub(r"(?i)\b(api[_-]?key|token|password|authorization)\s*[:=]\s*\S+", r"\1=<SECRET>", text)
    return text


def verify() -> dict:
    manifest = json.loads((ROOT / "data/manifest.json").read_text(encoding="utf-8"))
    for f in manifest["files"]:
        if hashlib.sha256((ROOT / f["path"]).read_bytes()).hexdigest() != f["sha256"]:
            raise ValueError(f"Source hash mismatch: {f['path']}")
    return manifest


def _normalize_level(raw_level: str) -> str:
    return _LEVEL_MAP.get(raw_level.strip().upper(), "info")


def ingest(
    db: Path = DB,
    dataset: str | None = None,
    source_path: Path | None = None,
) -> dict:
    """
    Parse and ingest logs into SQLite.

    Supports:
      - dataset='apache' (default): verified Loghub Apache 2k sample
      - dataset='bgl': Loghub BGL log slice (default data/source/BGL_50k.log).
        Upstream anomaly labels in column 1 are written ONLY to data/bgl_labels.json
        for offline evaluation and are strictly excluded from runtime SQLite tables.
    """
    ds = (dataset or os.getenv("OPSPILOT_DATASET", "apache")).strip().lower()
    records: list[tuple[int, str, str, str, str, str]] = []
    rejected: list[int] = []
    redacted_rows = 0
    bgl_labels: dict[str, str] = {}

    if ds == "apache":
        verify()
        raw = source_path or (ROOT / "data/source/Apache_Apache_2k.log")
        lines = raw.read_text(encoding="utf-8", errors="replace").splitlines()
        for n, line in enumerate(lines, 1):
            m = APACHE_LINE.fullmatch(line)
            if not m:
                rejected.append(n)
                continue
            raw_msg = m["message"]
            clean_msg = redact(raw_msg)
            if clean_msg != raw_msg:
                redacted_rows += 1
            records.append((n, m["time"], m["level"].lower(), clean_msg, "apache", ""))
    elif ds == "bgl":
        raw = source_path or (ROOT / "data/source/BGL_50k.log")
        if not raw.exists():
            raise FileNotFoundError(
                f"BGL log file not found at {raw}. Run `python scripts/prepare_bgl.py` first."
            )
        lines = raw.read_text(encoding="utf-8", errors="replace").splitlines()
        for n, line in enumerate(lines, 1):
            m = BGL_LINE.fullmatch(line)
            if not m:
                rejected.append(n)
                continue
            label = m["label"].strip()
            if label != "-":
                bgl_labels[f"L{n}"] = label
            raw_msg = f"[{m['component']}] {m['message']}"
            clean_msg = redact(raw_msg)
            if clean_msg != raw_msg:
                redacted_rows += 1
            records.append(
                (n, m["time"], _normalize_level(m["level"]), clean_msg, m["component"], m["node"])
            )
    else:
        raise ValueError(f"Unsupported dataset: {ds}")

    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as con:
        con.execute("DROP TABLE IF EXISTS logs")
        con.execute(
            """
            CREATE TABLE logs(
                line INTEGER PRIMARY KEY,
                time TEXT,
                level TEXT,
                message TEXT,
                component TEXT DEFAULT '',
                node TEXT DEFAULT ''
            )
            """
        )
        con.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
        con.executemany("INSERT INTO logs VALUES(?,?,?,?,?,?)", records)
        con.execute("CREATE INDEX IF NOT EXISTS logs_level ON logs(level)")
        con.execute("CREATE INDEX IF NOT EXISTS logs_component ON logs(component)")
        con.execute("INSERT OR REPLACE INTO meta VALUES('dataset', ?)", (ds,))
        con.execute("INSERT OR REPLACE INTO meta VALUES('rows', ?)", (str(len(records)),))

    if ds == "bgl":
        labels_path = ROOT / "data/bgl_labels.json"
        labels_path.write_text(json.dumps(bgl_labels, indent=2), encoding="utf-8")

    report = {
        "dataset": ds,
        "rows": len(records),
        "rejected_lines": rejected[:50],
        "rejected_count": len(rejected),
        "source_lines": len(lines),
        "redacted_rows": redacted_rows,
        "anomaly_rows_labeled": len(bgl_labels) if ds == "bgl" else None,
    }
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports/ingest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
