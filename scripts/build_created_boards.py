#!/usr/bin/env python3
import json, sys
from pathlib import Path

taxonomy = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
existing = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
out = Path(sys.argv[3])
existing_set = set(existing.get('boards', []))
confirmed = [x for x in taxonomy.get('boards', []) if x in existing_set]
missing = [x for x in taxonomy.get('boards', []) if x not in existing_set]
out.write_text(json.dumps({'confirmed': confirmed, 'created': [], 'missing': missing, 'failed': []}, ensure_ascii=False, indent=2), encoding='utf-8')
print(out)
