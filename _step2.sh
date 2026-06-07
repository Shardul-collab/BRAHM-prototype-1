#!/bin/bash
echo "--- curl POST /workflows/1/reset ---"
RESP=$(curl -s -w "\nHTTP_CODE:%{http_code}" -X POST http://localhost:8000/workflows/1/reset \
  -H "Content-Type: application/json" \
  -d '{"from_stage": "S5"}')
echo "$RESP"
echo ""
echo "--- python3 -m json.tool (if JSON body) ---"
BODY=$(echo "$RESP" | sed '/HTTP_CODE:/d')
CODE=$(echo "$RESP" | grep HTTP_CODE | cut -d: -f2)
if [ -n "$BODY" ] && [ "$CODE" != "404" ]; then
  echo "$BODY" | python3 -m json.tool 2>&1 || echo "$BODY"
else
  echo "(empty or non-JSON — HTTP $CODE)"
fi
