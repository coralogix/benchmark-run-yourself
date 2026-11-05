#!/bin/bash
# Scenario "optimize": no fault. prep, on and off do nothing, so the runner
# gives a clean window (cool-down, baseline, "lead", agents) with nothing injected.
case "${1:-}" in
  prep) echo "PREP nothing to stage" ;;
  on)   echo "VERIFIED no fault (normal state) at $(date -u +%H:%M:%SZ)" ;;
  off)  echo "VERIFIED nothing to turn off" ;;
  status) echo "no fault" ;;
  *) echo "usage: $0 prep|on|off|status" >&2; exit 2 ;;
esac
