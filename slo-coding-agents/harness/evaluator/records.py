"""Readers for a trial's raw records, shared by the figure exporters."""
from __future__ import annotations

import datetime as dt
import json
import re
import shlex
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ID = re.compile(r'(?:FEAT|REL|INC|TD|HC|SF|FF)[A-Z0-9_-]*')
STOP = {';', '&&', '||', '|', '&', '\n'}


def timestamp(value):
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def items(row):
    values = row.get('shipped_with') or row.get('items') or [row['item']]
    return tuple(values)


def completion_requests(rows, run_id):
    """Group ship_decisions.jsonl rows back into tools/ship requests.

    review_server.Server.ship writes one row per item, contiguously, without
    yielding within a request. Validate a complete, ordered item group with the
    same score and reason and a sub-second timestamp spread. Repeated requests
    stay separate requests.
    """
    requests = []
    i = 0
    while i < len(rows):
        first = rows[i]
        expected = items(first)
        group = rows[i:i + len(expected)]
        if not expected:
            raise ValueError(f'{run_id}: malformed completion item list at row {i + 1}')
        if len(group) != len(expected) or tuple(r['item'] for r in group) != expected:
            raise ValueError(f'{run_id}: incomplete/out-of-order completion group at row {i + 1}')
        for row in group:
            if (items(row), row.get('accepted_score'), row.get('reason')) != (expected, first.get('accepted_score'), first.get('reason')):
                raise ValueError(f'{run_id}: non-contiguous completion group at row {i + 1}')
        times = [timestamp(row['at']) for row in group]
        if max(times) - min(times) > 1:
            raise ValueError(f'{run_id}: completion rows spread over more than one second')
        requests.append({'at': min(times), 'items': expected, 'rows': group})
        i += len(expected)
    assert sum(len(r['rows']) for r in requests) == len(rows)
    return requests


def static_ship_calls(command):
    """Read the literal item and score arguments of each tools/ship call in a shell command."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|()<>')
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return [], True
    calls = []
    for index, token in enumerate(tokens[:-1]):
        if token not in ('tools/ship', './tools/ship'):
            continue
        ids = [item.strip().upper() for item in tokens[index + 1].split(',') if item.strip()]
        if not ids or not all(ID.fullmatch(item) for item in ids):
            continue
        score = None
        for i in range(index + 2, len(tokens)):
            if tokens[i] in STOP:
                break
            if tokens[i] == '--accepted-score' and i + 1 < len(tokens):
                try:
                    score = float(tokens[i + 1])
                except ValueError:
                    pass
                break
            if tokens[i].startswith('--accepted-score='):
                try:
                    score = float(tokens[i].split('=', 1)[1])
                except ValueError:
                    pass
                break
        if score is not None:
            calls.append((ids, score))
    return calls, False
