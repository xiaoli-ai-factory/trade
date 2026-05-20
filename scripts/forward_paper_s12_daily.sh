#!/bin/bash
set -euo pipefail

RUN_DATE="${1:-$(date +%F)}"
cd /home/hyd/claude_code/trade

mkdir -p paper/logs
LOG_PATH="paper/logs/forward_${RUN_DATE}.log"

{
  echo "[$(date '+%F %T')] forward_paper_s12 date=${RUN_DATE}"
  IS_TRADING_DAY="$(
    python3 - <<PY
from paper.runner import is_s12_trading_day

print("yes" if is_s12_trading_day("${RUN_DATE}") else "no")
PY
  )"
  if [ "${IS_TRADING_DAY}" != "yes" ]; then
    echo "non-trading day skipped: ${RUN_DATE}"
    exit 0
  fi

  python3 -m paper.runner --mode forward --strategy s12_global_rp --date "${RUN_DATE}"
} >>"${LOG_PATH}" 2>&1
