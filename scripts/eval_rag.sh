#!/usr/bin/env bash
# Run RAG retrieval eval against Supabase and save a timestamped baseline.
# Usage:
#   ./scripts/eval_rag.sh                        # uses rag_queries.json (integration, real UUIDs)
#   ./scripts/eval_rag.sh --unit                 # uses rag_queries_unit.json (synthetic IDs)
#   ./scripts/eval_rag.sh --mock                 # offline mock embeddings
set -euo pipefail

FIXTURE="tests/fixtures/rag_queries.json"
MOCK_FLAG=""
DATE=$(date +%Y-%m-%d)

for arg in "$@"; do
  case $arg in
    --unit) FIXTURE="tests/fixtures/rag_queries_unit.json" ;;
    --mock) MOCK_FLAG="--mock" ;;
  esac
done

LABEL=$(basename "$FIXTURE" .json)
OUT="docs/eval-baselines/${DATE}-${LABEL}.json"

echo "Running eval: $FIXTURE → $OUT"
python -m invproc rag eval "$FIXTURE" --all-modes $MOCK_FLAG | tee "$OUT"
echo ""
echo "Saved: $OUT"
