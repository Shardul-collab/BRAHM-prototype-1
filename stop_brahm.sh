#!/bin/bash
echo "Stopping BRAHM services..."
pkill -f "brahm_db.api"  && echo "✓ CHITRAGUPTA stopped" || echo "  CHITRAGUPTA was not running"
pkill -f "uvicorn api:app" && echo "✓ SHANI stopped"        || echo "  SHANI was not running"
echo "Done."
