#!/bin/bash
set -uo pipefail
ID=${ID:-9SIQT8TOJO}
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
mkdir -p "$R/results"
BK=$R/results/.catalog-backup-$ID.copy
psql() { docker exec -i astronomy-db psql -U postgres -d astronomy_db -v ON_ERROR_STOP=1 "$@"; }
present() { psql -tAc "select count(*) from catalog.products where id='$ID'"; }
ts() { date -u +%H:%M:%SZ; }

case "${1:-}" in
  prep)
    if [[ "$(present)" != "1" ]]; then
      [[ -s "$BK" ]] && { echo "PREP: row absent but backup exists ($BK); run off first"; exit 1; }
      echo "PREP FAILED: row $ID not in catalog and no backup"; exit 1
    fi
    psql -c "\copy (select * from catalog.products where id='$ID') to stdout" > "$BK.tmp" && [[ $(wc -l < "$BK.tmp") -eq 1 ]] \
      && mv "$BK.tmp" "$BK" && echo "PREP backup of $ID saved ($(wc -c < "$BK") bytes) at $(ts)" \
      || { echo "PREP FAILED: backup"; exit 1; } ;;
  on)
    [[ -s "$BK" ]] || { echo "NOT VERIFIED: no backup; run prep"; exit 1; }
    psql -qc "delete from catalog.products where id='$ID'" >/dev/null
    if [[ "$(present)" == "0" ]]; then echo "VERIFIED $ID delisted at $(ts)"; else echo "NOT VERIFIED: row still present"; exit 1; fi ;;
  off)
    if [[ "$(present)" == "0" ]]; then
      [[ -s "$BK" ]] || { echo "NOT VERIFIED: row missing and no backup!"; exit 1; }
      psql -c "\copy catalog.products from stdin" < "$BK" >/dev/null
    fi
    now=$(psql -c "\copy (select * from catalog.products where id='$ID') to stdout")
    if [[ "$now" == "$(cat "$BK")" ]]; then echo "VERIFIED $ID restored (row identical to backup) at $(ts)"; else echo "NOT VERIFIED: restored row differs"; exit 1; fi ;;
  status) echo "$ID present: $(present)" ;;
  *) echo "usage: $0 prep|on|off|status" >&2; exit 2 ;;
esac
