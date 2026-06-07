"""
ganesh/tools/load_context.py
=============================
G1 — Load research context using FAISS vector search.
Builds a per-section evidence map so G3 prompts stay small.

Context source: Chitragupta /context/load (filtered, ready papers only).
FAISS vector routing runs on top for per-section evidence selection.
"""
from __future__ import annotations
import json
import sys
import os
from datetime import datetime
from pathlib import Path

SHANI_ROOT = Path("/mnt/d/brahm/agents/shani")
if str(SHANI_ROOT) not in sys.path:
    sys.path.insert(0, str(SHANI_ROOT))

CHITRAGUPTA_BASE = "http://localhost:8003"
CHITRAGUPTA_KEY  = os.getenv("API_KEY", "chitragupta_api_2026Uzp7N9dRpYguBAEiljqFpn075xIpGdEI")


def _chit_headers() -> dict:
    return {"X-API-Key": CHITRAGUPTA_KEY}


def _fetch_context_from_chitragupta(source_ids: list, document_type: str) -> dict | None:
    """Call Chitragupta /context/load. Returns context package or None on failure."""
    try:
        import httpx
        resp = httpx.post(
            f"{CHITRAGUPTA_BASE}/v1/context/load",
            headers=_chit_headers(),
            json={
                "workflow_ids":     source_ids,
                "document_type":    document_type,
                "max_per_category": 100,
            },
            timeout=30.0,
        )
        if resp.status_code == 200:
            return resp.json()
        print(f"[G1] Chitragupta /context/load returned {resp.status_code} — falling back to direct DB")
        return None
    except Exception as exc:
        print(f"[G1] Chitragupta unreachable: {exc} — falling back to direct DB")
        return None


def _fetch_summary_from_chitragupta(source_ids: list) -> dict | None:
    """Call Chitragupta /context/knowledge_summary. Returns dict or None."""
    try:
        import httpx
        ids_str = ",".join(str(i) for i in source_ids)
        resp = httpx.get(
            f"{CHITRAGUPTA_BASE}/v1/context/knowledge_summary",
            headers=_chit_headers(),
            params={"workflow_ids": ids_str},
            timeout=15.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            # Convert by_category list → dict[category, list[value]]
            summary: dict = {}
            for row in data.get("by_category", []):
                summary[row["category"]] = []
            return summary
        return None
    except Exception:
        return None


def load_context(repo, document_id: int, config: dict) -> dict:
    source_ids    = json.loads(config.get("source_ids") or "[]")
    source_type   = config.get("source_type", "shani")
    document_type = config.get("document_type", "literature_review")

    print(f"[G1] Loading context for document_id={document_id}, "
          f"source_type={source_type}, source_ids={source_ids}")

    # ── Get workflow material context (still from SHANI config) ───────────────
    material_context = _get_material_context(repo, source_ids)

    # ── Get section names for this document type ──────────────────────────────
    from ganesh.document_types.literature_review import LITERATURE_REVIEW_SECTIONS
    from ganesh.document_types.dft_report        import DFT_REPORT_SECTIONS
    from ganesh.document_types.research_report   import RESEARCH_REPORT_SECTIONS
    from ganesh.document_types.technical_summary import TECHNICAL_SUMMARY_SECTIONS
    from ganesh.document_types.manuscript_draft  import MANUSCRIPT_DRAFT_SECTIONS
    SECTION_MAP = {
        "literature_review": LITERATURE_REVIEW_SECTIONS,
        "dft_report":        DFT_REPORT_SECTIONS,
        "research_report":   RESEARCH_REPORT_SECTIONS,
        "technical_summary": TECHNICAL_SUMMARY_SECTIONS,
        "manuscript_draft":  MANUSCRIPT_DRAFT_SECTIONS,
    }
    sections      = SECTION_MAP.get(document_type, LITERATURE_REVIEW_SECTIONS)
    section_names = [s["section_name"] for s in sections]

    # ── G1: Fetch curated context from Chitragupta ────────────────────────────
    chit_package = _fetch_context_from_chitragupta(source_ids, document_type)

    if chit_package:
        print(f"[G1] Chitragupta context: {chit_package['total_papers']} papers, "
              f"{chit_package['total_knowledge']} knowledge rows")
        # Build valid_paper_ids from top_papers returned by Chitragupta
        valid_paper_ids = set(p["id"] for p in chit_package.get("top_papers", []))
        # Flat knowledge pool from Chitragupta (all categories)
        chit_knowledge: list = []
        for entries in chit_package.get("knowledge", {}).values():
            for e in entries:
                chit_knowledge.append(e)
    else:
        # Fallback: query SHANI DB directly
        print("[G1] Using direct SHANI DB fallback for paper list")
        if source_ids:
            placeholders = ",".join("?" * len(source_ids))
            valid_paper_ids = set(
                r["id"] for r in repo.fetch_all(
                    f"SELECT id FROM Paper WHERE workflow_id IN ({placeholders}) "
                    f"AND status IN ('extracted','knowledge_ready','completed')",
                    tuple(source_ids),
                )
            )
        else:
            valid_paper_ids = set()
        chit_knowledge = []

    print(f"[G1] {len(valid_paper_ids)} valid papers in target workflows")

    # ── FAISS vector search per section ───────────────────────────────────────
    section_evidence_map = {}
    total_knowledge = 0

    try:
        from services.vector_db_service import VectorDBService
        vs = VectorDBService(str(SHANI_ROOT / "vector_index.faiss"))
        vector_available = vs.index.ntotal > 0
    except Exception as exc:
        print(f"[G1] Vector search unavailable: {exc} — using fallback")
        vector_available = False

    for section_name in section_names:
        if vector_available and valid_paper_ids:
            query      = f"{material_context} {section_name}"
            raw_results = vs.search(query, top_k=50, return_scores=True)
            filtered    = [
                (pid, score) for pid, score in raw_results
                if pid in valid_paper_ids
            ][:8]
            relevant_paper_ids = [pid for pid, _ in filtered]
        else:
            relevant_paper_ids = list(valid_paper_ids)[:8]

        # Fetch knowledge rows for these papers from SHANI DB
        if relevant_paper_ids:
            placeholders = ",".join("?" * len(relevant_paper_ids))
            knowledge_rows = repo.fetch_all(
                f"SELECT category, value, sentence as context FROM ResearchKnowledge "
                f"WHERE paper_id IN ({placeholders}) LIMIT 30",
                tuple(relevant_paper_ids),
            )
            evidence = [dict(r) for r in knowledge_rows]
        elif chit_knowledge:
            # Use Chitragupta flat pool as section fallback
            evidence = chit_knowledge[:30]
        else:
            evidence = []

        section_evidence_map[section_name] = evidence
        total_knowledge += len(evidence)
        print(f"[G1]   {section_name}: {len(evidence)} knowledge rows "
              f"from {len(relevant_paper_ids)} papers")

    # ── Knowledge summary — prefer Chitragupta, fall back to direct DB ────────
    knowledge_summary = (
        _fetch_summary_from_chitragupta(source_ids)
        or _build_knowledge_summary(repo, source_ids)
    )

    context_bundle = {
        "document_type":        document_type,
        "source_workflow_ids":  source_ids,
        "material_context":     material_context,
        "section_evidence_map": section_evidence_map,
        "knowledge_summary":    knowledge_summary,
        "dft_results":          [],
        "total_papers":         len(valid_paper_ids),
        "total_knowledge_rows": total_knowledge,
    }

    # ── Persist to GaneshContext ──────────────────────────────────────────────
    now = datetime.utcnow().isoformat()
    with repo.transaction() as cursor:
        cursor.execute(
            """
            INSERT INTO GaneshContext
                (document_id, context_type, context_ref, context_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                document_id,
                source_type,
                json.dumps(source_ids),
                json.dumps(context_bundle),
                now,
            ),
        )
        cursor.execute(
            "UPDATE GaneshDocument SET status='planning', updated_at=? WHERE id=?",
            (now, document_id),
        )

    print(f"[G1] Context loaded: {len(valid_paper_ids)} papers, "
          f"{total_knowledge} knowledge rows (section-indexed)")

    return {
        "status":          "success",
        "context_bundle":  context_bundle,
        "paper_count":     len(valid_paper_ids),
        "knowledge_count": total_knowledge,
    }


def _get_material_context(repo, workflow_ids: list) -> str:
    """Get material/focus keywords from SHANI workflow configs."""
    if not workflow_ids:
        return "materials science"
    placeholders = ",".join("?" * len(workflow_ids))
    rows = repo.fetch_all(
        f"SELECT material, focus FROM WorkflowResearchConfig "
        f"WHERE workflow_id IN ({placeholders})",
        tuple(workflow_ids),
    )
    parts = []
    for r in rows:
        if r["material"]: parts.append(r["material"])
        if r["focus"]:    parts.append(r["focus"][:100])
    return " ".join(parts)[:300] if parts else "materials science"


def _build_knowledge_summary(repo, workflow_ids: list) -> dict:
    """Fallback: top 10 values per category directly from SHANI DB."""
    if not workflow_ids:
        return {}
    placeholders = ",".join("?" * len(workflow_ids))
    rows = repo.fetch_all(
        f"""
        SELECT rk.category, rk.value, COUNT(*) as cnt
        FROM ResearchKnowledge rk
        JOIN Paper p ON p.id = rk.paper_id
        WHERE p.workflow_id IN ({placeholders})
        GROUP BY rk.category, rk.value
        ORDER BY rk.category, cnt DESC
        """,
        tuple(workflow_ids),
    )
    summary: dict = {}
    for r in rows:
        cat = r["category"]
        summary.setdefault(cat, [])
        if len(summary[cat]) < 10:
            summary[cat].append(r["value"])
    return summary
