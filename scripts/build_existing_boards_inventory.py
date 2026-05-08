#!/usr/bin/env python3
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def normalize_text(value: Any) -> str:
    return str(value or '').strip()


def extract_boards(data: Any) -> Tuple[List[str], Dict[str, str]]:
    raw_boards = data.get('boards', []) if isinstance(data, dict) else data
    if not isinstance(raw_boards, list):
        raise ValueError('input must contain a boards list')

    boards: List[str] = []
    note_to_board: Dict[str, str] = {}
    seen_boards = set()

    for entry in raw_boards:
        board_name = ''
        notes = []
        if isinstance(entry, str):
            board_name = normalize_text(entry)
        elif isinstance(entry, dict):
            board_name = normalize_text(entry.get('name') or entry.get('title'))
            notes = entry.get('notes') or []
        else:
            continue

        if not board_name:
            continue
        if board_name not in seen_boards:
            seen_boards.add(board_name)
            boards.append(board_name)

        if not isinstance(notes, list):
            continue
        for note in notes:
            if isinstance(note, str):
                note_id = normalize_text(note)
            elif isinstance(note, dict):
                note_id = normalize_text(note.get('id') or note.get('note_id') or note.get('noteId'))
            else:
                note_id = ''
            if note_id:
                note_to_board[note_id] = board_name

    return boards, note_to_board


def build_inventory(data: Any) -> Dict[str, Any]:
    boards, note_to_board = extract_boards(data)
    return {
        'boards': boards,
        'excluded_note_ids': list(note_to_board.keys()),
        'note_to_board': note_to_board,
        'generated_at': utc_now(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description='从已有专辑 JSON 构建 existing_boards_inventory.json。')
    parser.add_argument('src', help='现有专辑/专辑内容 JSON 路径')
    parser.add_argument('out', nargs='?', default='existing_boards_inventory.json', help='输出路径')
    args = parser.parse_args()

    inventory = build_inventory(load_json(Path(args.src)))
    out = Path(args.out)
    write_json(out, inventory)
    print(json.dumps({
        'board_count': len(inventory['boards']),
        'excluded_note_count': len(inventory['excluded_note_ids']),
        'output': str(out),
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
