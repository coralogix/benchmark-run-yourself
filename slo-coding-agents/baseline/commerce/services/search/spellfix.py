"""
"Did you mean" for zero-result queries (AS 2021-05, PM-733).

Computes edit distance from the query to every term in the index. Was
switched off after the first promo day (it ran on every zero-result search
and took ~400ms each). The route that called it was removed; the code wasn't.
TODO(AS 2021-05-20): BK-tree.
"""
from services.search import index, normalise

MAX_DISTANCE = 2
MAX_SUGGESTIONS = 3


def _distance(a, b):
    if a == b:
        return 0
    if abs(len(a) - len(b)) > MAX_DISTANCE:
        return MAX_DISTANCE + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _vocabulary():
    vocab = set()
    for entry in index.index_snapshot():
        vocab.update(t for t in entry.terms if t and " " not in t)
    return vocab


def did_you_mean(q):
    words = normalise.tokens(q)
    if not words:
        return []
    vocab = _vocabulary()
    out = []
    for w in words:
        best = sorted((d, t) for t in vocab for d in [_distance(w, t)] if d <= MAX_DISTANCE)
        if best:
            out.append(best[0][1])
        else:
            out.append(w)
    suggestion = " ".join(out)
    return [suggestion] if suggestion != " ".join(words) else []
