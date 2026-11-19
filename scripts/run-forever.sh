#!/usr/bin/env bash
# Restart the paper desk whenever the process exits. No wallet, no live order.
set -u
cd "$(dirname "$0")/.."
export PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}"
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi
while true; do
  python3 -m desk run
  status=$?
  echo "desk exited ${status}; restarting in 5s" >&2
  sleep 5
done
