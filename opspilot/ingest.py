"""Import raw text, never upstream EventId labels, into the runtime index."""
import hashlib, ipaddress, json, re, sqlite3
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/index.sqlite"
LINE = re.compile(r"^\[(?P<time>[^]]+)\] \[(?P<level>[^]]+)\] (?P<message>.*)$")

def redact(text):
    def ip(m):
        try:
            ipaddress.ip_address(m[0]); return "<IP>"
        except ValueError:
            return m[0]
    text = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", ip, text)
    def ipv6(m):
        try:
            addr=ipaddress.ip_address(m[0])
            return "<IPV6>" if addr.version==6 else m[0]
        except ValueError:
            return m[0]
    text = re.sub(r"(?i)(?:[0-9a-f]{1,4}:){2,}[0-9a-f:]*", ipv6, text)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "<EMAIL>", text)
    text = re.sub(r"(?i)\b(api[_-]?key|token|password|authorization)\s*[:=]\s*\S+", r"\1=<SECRET>", text)
    return text

def verify():
    manifest = json.loads((ROOT/'data/manifest.json').read_text())
    for f in manifest['files']:
        if hashlib.sha256((ROOT/f['path']).read_bytes()).hexdigest() != f['sha256']:
            raise ValueError(f"Source hash mismatch: {f['path']}")
    return manifest

def ingest(db=DB):
    verify()
    raw = ROOT/'data/source/Apache_Apache_2k.log'
    records=[]; rejected=[]
    for n,line in enumerate(raw.read_text().splitlines(),1):
        m=LINE.fullmatch(line)
        if not m:
            rejected.append(n); continue
        records.append((n,m['time'],m['level'],redact(m['message'])))
    # One transaction: a failed import never leaves a half-populated table.
    db.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(db) as con:
        con.execute('CREATE TABLE IF NOT EXISTS logs(line INTEGER PRIMARY KEY,time TEXT,level TEXT,message TEXT)')
        con.execute('DELETE FROM logs')
        con.executemany('INSERT INTO logs VALUES(?,?,?,?)',records)
        con.execute('CREATE INDEX IF NOT EXISTS logs_level ON logs(level)')
    report={'rows':len(records),'rejected_lines':rejected,'source_lines':len(raw.read_text().splitlines()),'redacted_rows':sum(redact(LINE.fullmatch(x)['message'])!=LINE.fullmatch(x)['message'] for x in raw.read_text().splitlines() if LINE.fullmatch(x))}
    (ROOT/'reports').mkdir(exist_ok=True)
    (ROOT/'reports/ingest.json').write_text(json.dumps(report,indent=2))
    return report
