#!/bin/bash
set -e
DB=/mnt/d/brahm/agents/shani/database/research_workflow.db

echo "=== Step 1 — DB state before reset ==="
sqlite3 "$DB" \
"SELECT COUNT(*) as knowledge_rows FROM ResearchKnowledge;
SELECT COUNT(*) as knowledge_ready_papers FROM Paper WHERE status='knowledge_ready';
SELECT COUNT(*) as completed_papers FROM Paper WHERE status='completed';"

echo ""
echo "=== Step 2 — Reset workflow 1 to S5 ==="
curl -s -X POST http://localhost:8000/workflows/1/reset \
  -H "Content-Type: application/json" \
  -d '{"from_stage": "S5"}' | python3 -m json.tool

echo ""
echo "=== Step 3 — DB state after reset ==="
sqlite3 "$DB" \
"SELECT COUNT(*) as knowledge_rows FROM ResearchKnowledge;
SELECT COUNT(*) as extracted_papers FROM Paper WHERE status='extracted';
SELECT COUNT(*) as knowledge_ready_papers FROM Paper WHERE status='knowledge_ready';"

echo ""
echo "=== Step 4 — Restart SHANI ==="
pkill -f "uvicorn api:app" 2>/dev/null || true
sleep 2
cd /mnt/d/brahm/agents/shani
/mnt/d/brahm/agents/shani/venv/bin/python -m uvicorn api:app \
  --host 0.0.0.0 --port 8000 > /tmp/shani.log 2>&1 &
sleep 4
curl -s http://localhost:8000/health
echo ""
