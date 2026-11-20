#!/usr/bin/env bash
set -euo pipefail

# Run p95-bench v2 sequentially across a list of "<provider> <model>" entries
# inside a single Docker Sandbox so all models share the v2 $30 budget ledger.
#
# Usage:
#   ./sandbox/run-models.sh [--dry-run] [models-file]
#
# Environment:
#   REPEATS=1            Number of repeats per task (default: 1)
#   OUT_DIR=results      Output directory (default: results)
#   PACK=p95-v2.0.0      Pack ID (default: p95-v2.0.0)

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then
  DRY_RUN=1
  shift
fi

MODELS_FILE="${1:-sandbox/models.txt}"
PACK="${PACK:-p95-v2.0.0}"
BUDGETS="${BUDGETS:-packs/${PACK}/efficiency-budgets.json}"
OUT_DIR="${OUT_DIR:-results}"
REPEATS="${REPEATS:-1}"

if [ ! -f "$MODELS_FILE" ]; then
  echo "ERROR: Models file not found: $MODELS_FILE" >&2
  echo "Copy sandbox/models.example.txt to $MODELS_FILE and uncomment your target models." >&2
  exit 1
fi

key_var_for() {
  case "$1" in
    openai)           echo "OPENAI_API_KEY" ;;
    anthropic)        echo "ANTHROPIC_API_KEY" ;;
    gemini)           echo "GEMINI_API_KEY" ;;
    deepseek)         echo "DEEPSEEK_API_KEY" ;;
    meta)             echo "MODEL_API_KEY" ;;
    qwen)             echo "MODELSCOPE_API_KEY" ;;
    thinkingmachines) echo "TINKER_API_KEY" ;;
    *)                echo "" ;;
  esac
}

COUNT=0
while read -r provider model _; do
  # Skip blank lines and comments
  [[ -z "${provider:-}" || "$provider" == \#* ]] && continue
  if [ -z "${model:-}" ]; then
    echo "ERROR: Invalid line in $MODELS_FILE (expected '<provider> <model>'): $provider" >&2
    exit 1
  fi

  key_var="$(key_var_for "$provider")"
  if [ -z "$key_var" ]; then
    echo "ERROR: Unknown provider '$provider' in $MODELS_FILE" >&2
    exit 1
  fi

  cmd=(
    .venv/bin/p95bench run
    --pack "$PACK"
    --efficiency-budgets "$BUDGETS"
    --provider "$provider"
    --model "$model"
    --out "$OUT_DIR"
    --repeats "$REPEATS"
  )

  if [ "$DRY_RUN" = "1" ]; then
    key_state="MISSING"
    [ -n "${!key_var:-}" ] && key_state="set"
    echo "[dry-run] ($key_var=$key_state) ${cmd[*]}"
  else
    if [ -z "${!key_var:-}" ]; then
      echo "ERROR: $key_var is not set for provider '$provider' ($model). Aborting." >&2
      exit 1
    fi
    echo "==> Running $provider / $model (pack=$PACK, repeats=$REPEATS)..."
    "${cmd[@]}"
  fi
  COUNT=$((COUNT + 1))
done < "$MODELS_FILE"

if [ "$COUNT" -eq 0 ]; then
  echo "No active models found in $MODELS_FILE (all lines commented or empty)."
fi
