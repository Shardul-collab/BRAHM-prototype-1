"""
ganesh_api.py
==============
GANESH FastAPI server — port 8001.

Endpoints:
  GET  /health
  POST /documents/write       — G1+G2+G3 (create doc, load context, plan, draft)
  POST /documents/synthesize  — G4+G5 (cross-review + integrate)
  GET  /documents/{id}        — retrieve document + sections
  GET  /documents             — list documents with filter
  GET  /documents/{id}/status — lightweight status poll

Architecture:
  - Each write/synthesize call runs the GANESH pipeline synchronously
    in a background thread (via asyncio.to_thread) so FastAPI stays responsive
  - SHANI's Repository is used for all DB access (shared research_workflow.db)
  - GANESH schema migration runs on startup (idempotent)
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
load_dotenv("/mnt/d/brahm/agents/chitragupta/.env")
from dotenv import load_dotenv
load_dotenv("/mnt/d/brahm/agents/chitragupta/.env")
from typing import Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── Path setup ────────────────────────────────────────────────────────────────
BRAHM_ROOT   = Path("/mnt/d/brahm")
SHANI_ROOT   = BRAHM_ROOT / "agents/shani"
GANESH_ROOT  = BRAHM_ROOT / "agents/ganesh"

for p in [str(SHANI_ROOT), str(GANESH_ROOT), str(BRAHM_ROOT / "brahm")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from repositories.repository import Repository
from ganesh.schema           import run_migration

from ganesh.tools.load_context          import load_context
from ganesh.tools.plan_document         import plan_document
from ganesh.tools.execute_section_graph import execute_section_graph
from ganesh.tools.cross_section_review  import cross_section_review
from ganesh.tools.integrate_document    import integrate_document

DB_PATH = str(SHANI_ROOT / "database/research_workflow.db")

log = logging.getLogger("ganesh.api")


def _repo() -> Repository:
    return Repository(DB_PATH)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run schema migration on startup
    repo = _repo()
    try:
        run_migration(repo)
        log.info("GANESH schema migration complete.")
    finally:
        repo.close()
    yield
    log.info("GANESH API shut down.")


app = FastAPI(
    title       = "GANESH Scientific Writing API",
    description = "G1-G5 document generation pipeline",
    version     = "1.0.0",
    lifespan    = lifespan,
    docs_url    = "/docs",
    redoc_url   = "/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


def _http_err(msg: str, code: int = 400):
    raise HTTPException(status_code=code, detail={"ok": False, "message": msg})


# ═════════════════════════════════════════════════════════════════════════════
# HEALTH
# ═════════════════════════════════════════════════════════════════════════════

@app.get("/health", tags=["Health"])
async def health():
    return {"ok": True, "service": "GANESH", "port": 8001}


# ═════════════════════════════════════════════════════════════════════════════
# MODELS
# ═════════════════════════════════════════════════════════════════════════════

class WriteRequest(BaseModel):
    workflow_ids:   list[int]
    document_type:  str = "literature_review"
    title:          Optional[str] = None
    project_id:     Optional[int] = None
    llm_backend:    Optional[str] = None   # 'groq' | 'ollama' | 'auto'
    stop_after:     Optional[str] = None   # 'G1' | 'G2' | 'G3' — for partial runs

class SynthesizeRequest(BaseModel):
    document_id: int
    stop_after:  Optional[str] = None   # 'G4' to stop before G5


# ═════════════════════════════════════════════════════════════════════════════
# DOCUMENTS — WRITE (G1 + G2 + G3)
# ═════════════════════════════════════════════════════════════════════════════

@app.post("/documents/write", tags=["Documents"])
async def write_document(body: WriteRequest):
    """
    Trigger G1 (load context) → G2 (plan) → G3 (draft sections).
    Creates a new GaneshDocument and runs the pipeline synchronously.
    Returns document_id immediately; use GET /documents/{id}/status to poll.
    """

    title = body.title or f"{body.document_type.replace('_', ' ').title()} — {datetime.utcnow().strftime('%Y-%m-%d')}"

    def _run() -> dict:
        repo = _repo()
        try:
            now = datetime.utcnow().isoformat()

            # Create GaneshDocument row
            with repo.transaction() as cursor:
                cursor.execute(
                    """
                    INSERT INTO GaneshDocument
                        (title, document_type, status, source_type, source_ids,
                         total_iterations, created_at, updated_at)
                    VALUES (?, ?, 'planning', 'shani', ?, 0, ?, ?)
                    """,
                    (title, body.document_type, json.dumps(body.workflow_ids), now, now),
                )
                document_id = cursor.lastrowid

            log.info("Created GaneshDocument id=%d title='%s'", document_id, title)

            config = {
                "source_type":   "shani",
                "source_ids":    json.dumps(body.workflow_ids),
                "document_type": body.document_type,
                "llm_backend":   body.llm_backend or "auto",
            }

            # G1
            g1 = load_context(repo, document_id, config)
            log.info("G1 complete: %d papers, %d knowledge rows",
                     g1.get("paper_count", 0), g1.get("knowledge_count", 0))
            if body.stop_after == "G1":
                return {"ok": True, "document_id": document_id, "stopped_after": "G1", **g1}

            # G2
            g2 = plan_document(repo, document_id, config)
            log.info("G2 complete: %d sections planned", g2.get("sections_planned", 0))
            if body.stop_after == "G2":
                return {"ok": True, "document_id": document_id, "stopped_after": "G2", **g2}

            # G3
            g3 = execute_section_graph(repo, document_id, config)
            log.info("G3 complete: %d sections approved", g3.get("sections_approved", 0))

            return {
                "ok":          True,
                "document_id": document_id,
                "title":       title,
                "g1":          g1,
                "g2":          g2,
                "g3":          g3,
            }

        except Exception as exc:
            log.exception("Write pipeline failed: %s", exc)
            # Mark document as failed if it was created
            try:
                now = datetime.utcnow().isoformat()
                with repo.transaction() as cursor:
                    cursor.execute(
                        "UPDATE GaneshDocument SET status='failed', updated_at=? WHERE id=?",
                        (now, document_id),
                    )
            except Exception:
                pass
            raise
        finally:
            repo.close()

    try:
        result = await asyncio.to_thread(_run)
        return result
    except Exception as exc:
        _http_err(str(exc), 500)


# ═════════════════════════════════════════════════════════════════════════════
# DOCUMENTS — SYNTHESIZE (G4 + G5)
# ═════════════════════════════════════════════════════════════════════════════

@app.post("/documents/synthesize", tags=["Documents"])
async def synthesize_document(body: SynthesizeRequest):
    """
    Trigger G4 (cross-section review) → G5 (integrate).
    Run after /documents/write completes.
    """

    def _run() -> dict:
        repo = _repo()
        try:
            doc = repo.fetch_one(
                "SELECT id, title, document_type, status, source_ids FROM GaneshDocument WHERE id = ?",
                (body.document_id,),
            )
            if not doc:
                raise ValueError(f"Document {body.document_id} not found")

            if doc["status"] not in ("reviewing", "drafting", "integrating"):
                raise ValueError(
                    f"Document status is '{doc['status']}' — expected 'reviewing'. "
                    f"Run /documents/write first."
                )

            config = {
                "document_type": doc["document_type"],
                "source_ids":    doc["source_ids"],
            }

            # G4
            g4 = cross_section_review(repo, body.document_id, config)
            log.info("G4 complete: coherence_score=%s", g4.get("coherence_score"))
            if body.stop_after == "G4":
                return {"ok": True, "document_id": body.document_id, "stopped_after": "G4", **g4}

            # G5
            g5 = integrate_document(repo, body.document_id, config)
            log.info("G5 complete: %d words", g5.get("word_count", 0))

            return {
                "ok":          True,
                "document_id": body.document_id,
                "title":       doc["title"],
                "g4":          g4,
                "g5":          g5,
                "word_count":  g5.get("word_count", 0),
            }

        except Exception as exc:
            log.exception("Synthesize pipeline failed: %s", exc)
            raise
        finally:
            repo.close()

    try:
        result = await asyncio.to_thread(_run)
        return result
    except Exception as exc:
        _http_err(str(exc), 500)


# ═════════════════════════════════════════════════════════════════════════════
# DOCUMENTS — READ
# ═════════════════════════════════════════════════════════════════════════════

@app.get("/documents/{document_id}", tags=["Documents"])
async def get_document(document_id: int, include_sections: bool = True):

    def _fetch() -> dict:
        repo = _repo()
        try:
            doc = repo.fetch_one(
                """
                SELECT id, title, document_type, status, source_type, source_ids,
                       outline_json, final_output, quality_flag,
                       total_iterations, created_at, updated_at
                FROM GaneshDocument WHERE id = ?
                """,
                (document_id,),
            )
            if not doc:
                return None

            result = dict(doc)

            if include_sections:
                sections = repo.fetch_all(
                    """
                    SELECT s.id, s.section_name, s.section_type, s.status,
                           s.quality_score, s.iteration_count, s.exec_order,
                           d.content as latest_draft, d.version as draft_version
                    FROM GaneshSection s
                    LEFT JOIN GaneshDraft d ON d.section_id = s.id
                        AND d.version = (SELECT MAX(version) FROM GaneshDraft WHERE section_id = s.id)
                    WHERE s.document_id = ?
                    ORDER BY s.exec_order ASC
                    """,
                    (document_id,),
                )
                result["sections"] = [dict(s) for s in sections]

            return result
        finally:
            repo.close()

    doc = await asyncio.to_thread(_fetch)
    if not doc:
        _http_err(f"Document {document_id} not found", 404)

    return {"ok": True, "document": doc}


@app.get("/documents/{document_id}/status", tags=["Documents"])
async def get_document_status(document_id: int):
    """Lightweight status poll — returns status + section progress."""

    def _fetch() -> dict:
        repo = _repo()
        try:
            doc = repo.fetch_one(
                "SELECT id, title, status, quality_flag, updated_at FROM GaneshDocument WHERE id = ?",
                (document_id,),
            )
            if not doc:
                return None

            counts = repo.fetch_one(
                """
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'approved'    THEN 1 ELSE 0 END) as approved,
                    SUM(CASE WHEN status = 'integrated'  THEN 1 ELSE 0 END) as integrated,
                    SUM(CASE WHEN status = 'drafting'    THEN 1 ELSE 0 END) as drafting,
                    SUM(CASE WHEN status = 'pending'     THEN 1 ELSE 0 END) as pending,
                    SUM(CASE WHEN status = 'ready'       THEN 1 ELSE 0 END) as ready
                FROM GaneshSection WHERE document_id = ?
                """,
                (document_id,),
            )

            return {**dict(doc), "section_counts": dict(counts) if counts else {}}
        finally:
            repo.close()

    result = await asyncio.to_thread(_fetch)
    if not result:
        _http_err(f"Document {document_id} not found", 404)

    return {"ok": True, **result}


@app.get("/documents", tags=["Documents"])
async def list_documents(
    status:        Optional[str] = None,
    document_type: Optional[str] = None,
    limit:         int           = 20,
):

    def _fetch() -> list:
        repo = _repo()
        try:
            where_clauses = []
            params        = []

            if status:
                where_clauses.append("status = ?")
                params.append(status)
            if document_type:
                where_clauses.append("document_type = ?")
                params.append(document_type)

            where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
            params.append(limit)

            rows = repo.fetch_all(
                f"""
                SELECT id, title, document_type, status, quality_flag,
                       total_iterations, created_at, updated_at
                FROM GaneshDocument
                {where}
                ORDER BY id DESC LIMIT ?
                """,
                tuple(params),
            )
            return [dict(r) for r in rows]
        finally:
            repo.close()

    docs = await asyncio.to_thread(_fetch)
    return {"ok": True, "count": len(docs), "documents": docs}


# ═════════════════════════════════════════════════════════════════════════════
# STARTUP
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s")
    uvicorn.run(
        "ganesh_api:app",
        host      = "0.0.0.0",
        port      = 8001,
        reload    = False,
        log_level = "info",
    )
