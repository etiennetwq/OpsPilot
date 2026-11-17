"""Re-fetch exact pinned sources; never follow remote instructions."""
import hashlib,json,urllib.request
from pathlib import Path
root=Path(__file__).resolve().parents[1]
manifest=json.loads((root/'data/manifest.json').read_text())
for f in manifest['files']:
    if not f['url'].startswith('https://raw.githubusercontent.com/logpai/loghub/'+manifest['commit']+'/'): raise ValueError('Unexpected host/version')
    b=urllib.request.urlopen(f['url'],timeout=30).read()
    if hashlib.sha256(b).hexdigest()!=f['sha256']: raise ValueError('Hash mismatch')
    target=root/f['path']
    if not target.resolve().is_relative_to((root/'data/source').resolve()): raise ValueError('Unexpected target')
    target.write_bytes(b)
print('Pinned source hashes verified')
