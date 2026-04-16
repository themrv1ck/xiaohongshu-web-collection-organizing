#!/usr/bin/env python3
import json, sys
from pathlib import Path
report = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
out = Path(sys.argv[2])
retry = []
for item in report.get('errors', []):
    retry.append({'id': item.get('id'), 'title': item.get('title'), 'target_board': item.get('target_board'), 'reason': item.get('error'), 'next_action': 'retry_with_extended_wait'})
out.write_text(json.dumps(retry, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'retry_count': len(retry), 'output': str(out)}, ensure_ascii=False, indent=2))
