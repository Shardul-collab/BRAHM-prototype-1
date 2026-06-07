# ============================================================
# reconstruct_findings.py — S5.5
#
# Takes ResearchKnowledge records (category/value pairs) for
# each paper and reconstructs them into coherent ResearchFinding
# rows using Groq llama-3.1-8b-instant.
#
# For each paper:
#   1. Fetch all knowledge items from ResearchKnowledge
#   2. Group by material
#   3. Send grouped items to LLM → structured finding JSON
#   4. Insert into ResearchFinding
#   5. Mark S55Checkpoint
# ============================================================

import json
import re
import time
import re
from services.llm_service import GroqClient, LLMService


# ============================================================
# PROMPT
# ============================================================

def _build_prompt(title: str, knowledge_items: list) -> str:
    items_text = "\n".join(
        f"- [{item['category']}] {item['value']}"
        for item in knowledge_items[:20]
    )
    return (
        "You are a materials science research assistant.\n"
        "Given a paper title and extracted knowledge items, "
        "reconstruct 1-5 concise research findings.\n\n"
        "Each finding must combine related items into one sentence "
        "covering: material, synthesis/condition, property, value.\n\n"
        "Return a JSON array. Each item:\n"
        '{"material":"...","synthesis_method":"...","characterization":"...",'
        '"property_name":"...","property_value":"...","condition_text":"...",'
        '"finding_text":"one sentence finding"}\n\n'
        "Use empty string if a field is not applicable.\n"
        "Return ONLY the JSON array, no other text.\n\n"
        f'Paper: "{title}"\n\n'
        f"Knowledge items:\n{items_text}\n\n"
        "JSON array:"
    )


# ============================================================
# JSON PARSING
# ============================================================

def _parse_findings(raw: str) -> list:
    try:
        return json.loads(raw)
    except Exception:
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except Exception:
                return []
        return []


REQUIRED_FIELDS = {
    "material", "synthesis_method", "characterization",
    "property_name", "property_value", "condition_text", "finding_text"
}


def _validate_finding(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    if not item.get("finding_text", "").strip():
        return False
    return True


# ============================================================
# MAIN TOOL — S5.5
# ============================================================

def reconstruct_findings(repo, workflow_id, execution_attempt_id=None, **kwargs):

    llm     = GroqClient()
    service = LLMService(llm)

    # Fetch papers that are knowledge_ready and not yet in S55Checkpoint
    papers = repo.fetch_all(
        """
        SELECT p.id, p.title
        FROM Paper p
        WHERE p.workflow_id = ?
          AND p.status = 'knowledge_ready'
          AND p.id NOT IN (
              SELECT paper_id FROM S55Checkpoint
              WHERE workflow_id = ?
          )
        """,
        (workflow_id, workflow_id)
    )

    print(f"[S5.5] Papers to process: {len(papers)}")

    processed   = 0
    total_findings = 0

    for paper in papers:
        paper    = dict(paper)
        paper_id = paper["id"]
        title    = paper["title"]

        # Fetch knowledge items for this paper
        knowledge = repo.fetch_all(
            """
            SELECT category, value, sentence, section_source
            FROM ResearchKnowledge
            WHERE paper_id = ?
            ORDER BY category, value
            """,
            (paper_id,)
        )

        if not knowledge:
            # Mark checkpoint even if no knowledge — don't reprocess
            with repo.transaction() as cursor:
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO S55Checkpoint
                    (paper_id, workflow_id, status, findings_count)
                    VALUES (?, ?, 'skipped', 0)
                    """,
                    (paper_id, workflow_id)
                )
            continue

        knowledge = [dict(k) for k in knowledge]

        prompt = _build_prompt(title, knowledge)

        findings = []
        for _attempt in range(4):
            try:
                raw      = llm.generate(prompt, max_tokens=800, temperature=0.3)
                findings = _parse_findings(raw)
                break
            except Exception as e:
                err = str(e)
                if "429" in err:
                    print(f"[S5.5] Rate limit hit, waiting 15s...")
                    time.sleep(15)
                else:
                    print(f"[S5.5] LLM error for paper {paper_id}: {e}")
                    break
        time.sleep(2)  # pace between papers

        valid_findings = [f for f in findings if _validate_finding(f)]
        print(f"[S5.5] Paper {paper_id} | knowledge={len(knowledge)} → findings={len(valid_findings)}")

        with repo.transaction() as cursor:

            for finding in valid_findings:
                cursor.execute(
                    """
                    INSERT INTO ResearchFinding (
                        paper_id, workflow_id,
                        material, synthesis_method, characterization,
                        property_name, property_value, condition_text,
                        finding_text, source_sentence, section_source,
                        confidence, source_type, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (
                        paper_id, workflow_id,
                        finding.get("material", ""),
                        finding.get("synthesis_method", ""),
                        finding.get("characterization", ""),
                        finding.get("property_name", ""),
                        finding.get("property_value", ""),
                        finding.get("condition_text", ""),
                        finding.get("finding_text", ""),
                        "",   # source_sentence — synthesized, no single source
                        "llm",
                        "high",
                        "s5.5",
                    )
                )

            cursor.execute(
                """
                INSERT OR REPLACE INTO S55Checkpoint
                (paper_id, workflow_id, status, findings_count)
                VALUES (?, ?, 'completed', ?)
                """,
                (paper_id, workflow_id, len(valid_findings))
            )

        total_findings += len(valid_findings)
        processed += 1

    print(f"\n[S5.5] Complete: {processed} papers | {total_findings} findings generated")

    return {
        "status": "success",
        "data": {
            "processed":      processed,
            "total_findings": total_findings,
        },
        "error": None,
    }
