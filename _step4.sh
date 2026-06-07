#!/bin/bash
pkill -f "uvicorn api:app" 2>/dev/null || true
sleep 2
cd /mnt/d/brahm/agents/shani
/mnt/d/brahm/agents/shani/venv/bin/python -m uvicorn api:app \
  --host 0.0.0.0 --port 8000 > /tmp/shani.log 2>&1 &
sleep 4
curl -s http://localhost:8000/health
echo ""
