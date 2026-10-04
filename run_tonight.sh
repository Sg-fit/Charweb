#!/bin/bash
set -a; . /etc/charweb.env; set +a
export CHARWEB_LLM_PROVIDER=nvidia
export CHARWEB_LLM_KEY="$NVIDIA_KEY"
export CHARWEB_MAX_TOKENS=2500
export CHARWEB_MAX_ELEMENTS=120
export CHARWEB_MAX_STEPS=30
export CHARWEB_MIN_CALL_INTERVAL_S=6
CONDS=free_explore,checklist,targeted_search,impossible_goal,single_action

# /etc/charweb.env deliberately holds no API keys, so NVIDIA_KEY must be
# exported in the launching shell. Without this guard both collection arms
# exit on their own key check and the script runs the backup regardless --
# which looks like success in the log. Cost: two wasted batches.
if [ ${#CHARWEB_LLM_KEY} -lt 20 ]; then
  echo "ABORT: CHARWEB_LLM_KEY is empty or too short (${#CHARWEB_LLM_KEY} chars)."
  echo "Run:  read -rs -p 'NVIDIA key: ' NVIDIA_KEY; export NVIDIA_KEY; echo"
  exit 1
fi

echo "=== [1/3] som_v1 ==="
./venv/bin/python research/run_interleaved.py \
  --perception som --no-scripted \
  --llm-models "meta/llama-3.2-11b-vision-instruct" \
  --instruction $CONDS --rounds 4 \
  --session-timeout 900 --sleep 8 --run-id som_v1

echo "=== [2/3] percept bridge on gpt-oss ==="
./venv/bin/python research/run_interleaved.py \
  --perception dom --no-scripted \
  --llm-models "openai/gpt-oss-20b" \
  --instruction $CONDS --rounds 3 \
  --session-timeout 900 --sleep 8 --run-id percept_bridge

echo "=== [3/3] backup ==="
./venv/bin/python research/export_all.py
ls -lh charweb_export_*.tar.gz | tail -2
echo "=== ALL DONE ==="
