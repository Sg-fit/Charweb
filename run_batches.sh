#!/bin/bash
set -a; . /etc/charweb.env; set +a
export CHARWEB_LLM_PROVIDER=nvidia
export CHARWEB_LLM_KEY="$NVIDIA_KEY"
export CHARWEB_MAX_TOKENS=2500 CHARWEB_MAX_ELEMENTS=120 CHARWEB_MAX_STEPS=30
export CHARWEB_MIN_CALL_INTERVAL_S=6
export CHARWEB_HEALTH_CSV=collection_health_v2.csv
export CHARWEB_PERCEPT_HEALTH_CSV=percept_health_v2.csv
CONDS=free_explore,checklist,targeted_search,impossible_goal,single_action,deep_dungeon,reading_visit
MODEL="meta/llama-3.2-11b-vision-instruct"

if [ ${#CHARWEB_LLM_KEY} -lt 20 ]; then echo "ABORT: no key"; exit 1; fi

echo "=== BATCH A: perception triad, 7 conditions ==="
CHARWEB_MAX_TIMEOUT_RATE=1.0 ./venv/bin/python research/run_interleaved.py \
  --perception dom,som,vision --no-scripted --llm-models "$MODEL" \
  --instruction $CONDS --rounds 4 \
  --session-timeout 900 --sleep 8 --run-id percept_v4

echo "=== BATCH B: harness axis, current instrument, 7 conditions ==="
./venv/bin/python research/run_interleaved.py \
  --include-llm --llm-models "$MODEL" \
  --instruction $CONDS --rounds 3 \
  --session-timeout 900 --sleep 8 --run-id harness_v2

echo "=== BACKUP ==="
./venv/bin/python research/export_all.py
echo "=== ALL DONE ==="
