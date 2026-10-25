#!/usr/bin/env python3
"""Verify all bundled font binaries and licenses without network access."""
import hashlib
import json
from pathlib import Path
from fontTools.ttLib import TTFont

root = Path(__file__).resolve().parents[1] / 'fonts'
entries = json.loads((root / 'catalog.json').read_text(encoding='utf-8'))
errors = []
ids = set()
for entry in entries:
    try:
        assert entry['id'] not in ids, 'duplicate font id'
        ids.add(entry['id'])
        path = root / entry['file']
        assert path.is_file(), 'font missing'
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry['sha256'], 'SHA-256 mismatch'
        assert (root / entry['license_file']).is_file(), 'license missing'
        with TTFont(str(path), lazy=True) as font:
            assert font.getBestCmap(), 'no character map'
    except Exception as exc:
        errors.append(f"{entry['id']}: {exc}")
if errors:
    raise SystemExit('\n'.join(errors))
print(f'OK: {len(entries)} offline fonts, licenses and SHA-256 verified')
