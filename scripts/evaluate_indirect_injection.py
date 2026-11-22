"""
Independent Indirect Prompt Injection Defense Evaluation.

Embeds independent public prompt-injection payloads (from Lakera/gandalf_ignore_instructions
and deepset/prompt-injections when --use-public is enabled) inside realistic operational log lines,
and evaluates against real clean BGL log lines so results are NOT self-authored regex tautologies.

Compares:
  - none
  - local_regex (fast built-in heuristic scanner in OpsPilot)
  - gateway_cascade (live LLM Security Audit Gateway /v1/scan with L0+L1+L2 DeBERTa cascade)
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import sys
import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from opspilot.ingest import BGL_LINE
from opspilot.tool_agent import scan_untrusted_text_local

_LOG_WRAPPERS = [
    "[error] [KERNEL] node R02-M1-N0-C:J12-U11 msg: {payload}",
    "[warn] [APP] ciod stream notice: {payload}",
    "[error] [client 10.0.0.1] request header: {payload}",
    "[info] [LINKCARD] diagnostics note: {payload}",
]

_FALLBACK_CLEAN_LOGS = [
    "[error] Directory index forbidden by rule: /var/www/html/",
    "[error] mod_jk child workerEnv in error state 6",
    "[notice] jk2_init() Found child 6725 in scoreboard slot 10",
    "[error] [KERNEL] data TLB error interrupt on node R02-M1-N0-C:J12-U11",
    "[error] [APP] ciod: failed to read message prefix on control stream",
]


def load_independent_attacks(n: int = 60, seed: int = 42) -> list[str]:
    """Load real unseen injection strings from public HF benchmarks and embed them into log lines."""
    from datasets import load_dataset

    rng = random.Random(seed)
    pool: list[str] = []

    try:
        ds = load_dataset("Lakera/gandalf_ignore_instructions", split="test")
        pool.extend(str(r["text"]).strip() for r in ds if str(r.get("text", "")).strip())
    except Exception as exc:
        print(f"[WARN] Could not load Lakera/gandalf_ignore_instructions: {exc}")

    try:
        ds = load_dataset("deepset/prompt-injections", split="test")
        pool.extend(
            str(r["text"]).strip()
            for r in ds
            if int(r.get("label", 0)) == 1 and str(r.get("text", "")).strip()
        )
    except Exception as exc:
        print(f"[WARN] Could not load deepset/prompt-injections: {exc}")

    if not pool:
        raise RuntimeError("No public attack payloads loaded. Install `datasets` or check network.")

    chosen = rng.sample(pool, min(n, len(pool)))
    return [
        _LOG_WRAPPERS[i % len(_LOG_WRAPPERS)].format(payload=text.replace("\n", " ")[:300])
        for i, text in enumerate(chosen)
    ]


def load_real_clean_logs(n: int = 60, seed: int = 42) -> list[str]:
    bgl_path = ROOT / "data/source/BGL_50k.log"
    if not bgl_path.exists():
        return (_FALLBACK_CLEAN_LOGS * ((n // len(_FALLBACK_CLEAN_LOGS)) + 1))[:n]

    rng = random.Random(seed)
    lines = bgl_path.read_text(encoding="utf-8", errors="replace").splitlines()
    sampled = rng.sample(lines, min(n * 2, len(lines)))
    clean: list[str] = []
    for line in sampled:
        m = BGL_LINE.fullmatch(line)
        if m:
            clean.append(f"[{m['level'].lower()}] [{m['component']}] {m['message']}")
        if len(clean) >= n:
            break
    return clean


def main() -> None:
    parser = argparse.ArgumentParser(description="Independent indirect prompt injection defense benchmark.")
    parser.add_argument("--n-attacks", type=int, default=60)
    parser.add_argument("--n-clean", type=int, default=60)
    parser.add_argument(
        "--gateway-url",
        default=None,
        help="Optional live llm-secure-gateway base URL (e.g. http://127.0.0.1:8000) to benchmark L0+L1+L2 cascade.",
    )
    parser.add_argument("--api-key", default="test_token_123")
    args = parser.parse_args()

    attacks = load_independent_attacks(n=args.n_attacks)
    clean_logs = load_real_clean_logs(n=args.n_clean)

    modes = ["none", "local_regex"]
    if args.gateway_url:
        modes.append("gateway_cascade")

    # Bypass system HTTP_PROXY for loopback gateway calls and reuse connection pool
    gw_client = httpx.Client(trust_env=False, timeout=15.0) if args.gateway_url else None

    def _scan(text: str, mode: str) -> bool:
        if mode == "none":
            return False
        if mode == "local_regex":
            return scan_untrusted_text_local(text)
        assert gw_client is not None
        r = gw_client.post(
            args.gateway_url.rstrip("/") + "/v1/scan",
            headers={"Authorization": f"Bearer {args.api_key}"},
            json={"text": text, "source": "opspilot_log_evidence"},
        )
        if r.status_code != 200:
            raise RuntimeError(f"Gateway /v1/scan returned HTTP {r.status_code}: {r.text[:300]}")
        return r.json().get("risk", {}).get("action") == "block"

    rows = []
    try:
        for mode in modes:
            caught = sum(1 for p in attacks if _scan(p, mode))
            false_alarms = sum(1 for c in clean_logs if _scan(c, mode))
            rows.append(
                {
                    "mode": mode,
                    "caught": caught,
                    "total_attacks": len(attacks),
                    "recall": round(caught / len(attacks), 4),
                    "false_alarms": false_alarms,
                    "total_clean": len(clean_logs),
                    "clean_fpr": round(false_alarms / len(clean_logs), 4),
                }
            )
    finally:
        if gw_client is not None:
            gw_client.close()

    lines = [
        "# OpsPilot Independent Indirect Prompt Injection Report",
        "",
        f"- **Run UTC**: `{datetime.now(timezone.utc).isoformat()}`",
        f"- **Attack Source**: Public `Lakera/gandalf_ignore_instructions` + `deepset/prompt-injections` embedded in log templates (`n={len(attacks)}`)",
        f"- **Clean Source**: Real BGL operational log lines (`n={len(clean_logs)}`)",
        f"- **Live Gateway `/v1/scan`**: `{args.gateway_url or 'Not connected'}`",
        "",
        "| Defense Mode | Intercepted | Attack Recall | Clean Log False Alarms | Clean Log FPR |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| `{r['mode']}` | {r['caught']}/{r['total_attacks']} | {r['recall']:.1%} | "
            f"{r['false_alarms']}/{r['total_clean']} | {r['clean_fpr']:.1%} |"
        )

    out = ROOT / "reports/indirect_injection_report.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
