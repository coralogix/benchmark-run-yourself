"""
Autocomplete suggestions (2020 hackathon, DP + AS). Never wired to a route:
the frontend team went with a third-party widget. Prefix trie over product
names, built from the index on first use, never invalidated.
"""
from services.search import index

_TRIE = {}
_BUILT = {"done": False}
MAX_SUGGESTIONS = 8


def _build():
    for entry in index.index_snapshot():
        node = _TRIE
        for ch in entry.name.lower():
            node = node.setdefault(ch, {})
            node.setdefault("$", []).append(entry.name)
    _BUILT["done"] = True


def suggest(prefix):
    if not _BUILT["done"]:
        _build()
    node = _TRIE
    for ch in (prefix or "").lower():
        node = node.get(ch)
        if node is None:
            return []
    return sorted(set(node.get("$", [])))[:MAX_SUGGESTIONS]
