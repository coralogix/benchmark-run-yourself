# jdb (vendored)

Upstream: hollowtree/jdb 0.4.2 (repository deleted in 2019; this is the only copy).

Larder patches (see CHANGES at the bottom of index.js):

* `.3` write-behind flush (`flushDelayMs`) so we don't rewrite the file on every put
* `.2` atomic-ish writes via tmp file + rename
* `.1` `get()` returns the stored object itself instead of a deep copy (perf, 2018)

Do not upgrade, there is nothing to upgrade to.
