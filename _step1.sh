#!/bin/bash
sqlite3 /mnt/d/brahm/agents/shani/database/research_workflow.db <<'SQL'
SELECT COUNT(*) as knowledge_rows FROM ResearchKnowledge;
SELECT COUNT(*) as knowledge_ready_papers FROM Paper WHERE status='knowledge_ready';
SELECT COUNT(*) as completed_papers FROM Paper WHERE status='completed';
SQL
