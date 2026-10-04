#!/bin/bash
set -a; . /etc/charweb.env; set +a
export CHARWEB_LLM_PROVIDER=nvidia CHARWEB_LLM_KEY="$NVIDIA_KEY"
export CHARWEB_MAX_TOKENS=2500 CHARWEB_MAX_ELEMENTS=120 CHARWEB_MAX_STEPS=30
export CHARWEB_MIN_CALL_INTERVAL_S=6
export CHARWEB_HEALTH_CSV=collection_health_v2.csv
export CHARWEB_PERCEPT_HEALTH_CSV=percept_health_v2.csv
CONDS=free_explore,checklist,targeted_search,impossible_goal,single_action,deep_dungeon,reading_visit
[ ${#CHARWEB_LLM_KEY} -lt 20 ] && { echo "ABORT: no key"; exit 1; }
echo "=== model axis v5: 2 models x 7 conditions x 6 rounds ==="
./venv/bin/python research/run_interleaved.py \
  --include-llm --no-scripted \
  --llm-models "openai/gpt-oss-20b,meta/llama-3.2-11b-vision-instruct" \
  --instruction $CONDS --rounds 6 \
  --session-timeout 900 --sleep 8 --run-id modelaxis_v5
echo "=== STAGE1 DONE ==="
