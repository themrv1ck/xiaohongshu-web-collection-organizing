#!/usr/bin/env python3
import json, sys
from pathlib import Path
classification = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
report_path = Path(sys.argv[2])
report = {'started_at': 'REPLACE_AT_RUNTIME', 'visible_count': len(classification), 'processed': [], 'errors': [], 'board_counts_before': {}, 'board_counts_after': {}}
for item in classification:
    report['processed'].append({'id': item['id'], 'title': item['title'], 'target_board': item['target_board'], 'status': 'planned', 'attempt': 0, 'events': ['hook real uncollect -> collect -> banner -> pick-board here']})
report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'planned': len(classification), 'report': str(report_path)}, ensure_ascii=False, indent=2))
