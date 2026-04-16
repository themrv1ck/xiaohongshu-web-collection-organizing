#!/usr/bin/env python3
import json, sys
from pathlib import Path
report = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
summary = {'visible_count': report.get('visible_count'), 'processed_count': len(report.get('processed', [])), 'error_count': len(report.get('errors', [])), 'board_counts_before': report.get('board_counts_before', {}), 'board_counts_after': report.get('board_counts_after', {})}
print(json.dumps(summary, ensure_ascii=False, indent=2))
