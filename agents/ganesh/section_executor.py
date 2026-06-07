"""
ganesh/section_executor.py
===========================
SectionExecutor: the write → critic → reviser loop for a single section.

This is GANESH's core cognitive unit.
It runs entirely inside G3 (execute_section_graph), invoked per section
by SectionGraph.

Loop:
    1. Writer  — draft the section given the brief + context
    2. Critic  — evaluate against quality dimensions → score + issues
    3. Decision:
       a. score ≥ threshold OR max_iterations reached → APPROVE (best draft)
       b. otherwise → Reviser applies critique → back to Critic

All drafts, critiques, and revisions are persisted to the DB
so the run can be resumed after a crash.

Quality dimensions:
    SCIENTIFIC_ACCURACY   — claims backed by provided evidence
    NARRATIVE_COHERENCE   — logical, readable prose flow
    BRIEF_COMPLIANCE      — section covers what the brief specified
    EVIDENCE_INTEGRATION  — citations and data woven in naturally
    LOGICAL_PROGRESSION   — paragraphs build on each other
    CROSS_SECTION_FIT     — consistent with already-approved sections

Each dimension is scored 0–10.
Default threshold: 7.5.
Default max iterations: 4 (1 initial + 3 revisions).
"""

from __future__ import annotations

import json
import traceback
from datetime import datetime
from typing import Dict, List, Optional

from ganesh.section_graph import SectionNode, SectionStatus


# ─────────────────────────────────────────────────────────────────────────────
# Quality configuration
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_QUALITY_THRESHOLD = 7.5
DEFAULT_MAX_ITERATIONS    = 4

QUALITY_DIMENSIONS = [
    "scientific_accuracy",
    "narrative_coherence",
    "brief_compliance",
    "evidence_integration",
    "logical_progression",
    "cross_section_fit",
]

# Default weights — can be overridden by Critic persona config.
DEFAULT_DIMENSION_WEIGHTS = {
    "scientific_accuracy":   0.25,
    "narrative_coherence":   0.20,
    "brief_compliance":      0.20,
    "evidence_integration":  0.15,
    "logical_progression":   0.10,
    "cross_section_fit":     0.10,
}


# ─────────────────────────────────────────────────────────────────────────────
# Critique record
# ─────────────────────────────────────────────────────────────────────────────

class CritiqueRecord:
    """
    Parsed output from a Critic LLM call.

    Stored in GaneshCritique table.
    Passed to Reviser as the basis for the next draft.
    """

    def __init__(
        self,
        scores:        Dict[str, float],
        issues:        List[Dict],
        overall_score: float,
        weights:       Optional[Dict[str, float]] = None,
    ):
        self.scores        = scores
        self.issues        = issues
        self.overall_score = overall_score
        self.weights       = weights or DEFAULT_DIMENSION_WEIGHTS

    @classmethod
    def from_llm_output(cls, raw: dict, weights: Optional[Dict] = None) -> "CritiqueRecord":
        """
        Parse a structured LLM response into a CritiqueRecord.

        Expected LLM output schema:
        {
            "scores": {
                "scientific_accuracy": 8.2,
                "narrative_coherence": 7.1,
                ...
            },
            "issues": [
                {
                    "dimension": "scientific_accuracy",
                    "issue": "Claim on line 3 has no citation",
                    "suggestion": "Cite [Smith 2020] here"
                },
                ...
            ]
        }
        """
        w = weights or DEFAULT_DIMENSION_WEIGHTS
        scores = raw.get("scores", {})
        overall = sum(
            scores.get(dim, 0.0) * w.get(dim, 0.0)
            for dim in QUALITY_DIMENSIONS
        )
        return cls(
            scores        = scores,
            issues        = raw.get("issues", []),
            overall_score = round(overall, 2),
            weights       = w,
        )

    def actionable_issues(self, min_severity: int = 5) -> List[Dict]:
        """Issues above a severity threshold — for the Reviser's prompt."""
        return [i for i in self.issues if i.get("severity", 10) >= min_severity]


# ─────────────────────────────────────────────────────────────────────────────
# Section executor
# ─────────────────────────────────────────────────────────────────────────────

class SectionExecutor:
    """
    Runs the write/critique/revise loop for a single GaneshSection.

    Parameters
    ----------
    repo : Repository
        Shared DB connection.
    document_id : int
        Parent GaneshDocument.
    context_bundle : dict
        Loaded by Context Loader — evidence, knowledge records, prior approved
        section texts, user constraints. Read-only.
    llm_client : callable
        LLM invocation function: fn(prompt: str) -> str.
        Injected so the executor doesn't import a specific LLM library.
    quality_threshold : float
        Minimum critic score to approve a section. Default: 7.5.
    max_iterations : int
        Maximum draft/revise cycles before accepting best-so-far. Default: 4.
    critic_persona : str, optional
        Plain-language description of the critic role.
        e.g. "peer reviewer for Nature Materials"
    """

    def __init__(
        self,
        repo,
        document_id:       int,
        context_bundle:    dict,
        llm_client=None,
        quality_threshold: float  = DEFAULT_QUALITY_THRESHOLD,
        max_iterations:    int    = DEFAULT_MAX_ITERATIONS,
        critic_persona:    Optional[str] = None,
    ):
        self.repo              = repo
        self.document_id       = document_id
        self.context_bundle    = context_bundle
        if llm_client is not None:
            self.llm_client = llm_client
        else:
            from ganesh.llm_client import call_llm as _default_llm
            self.llm_client = _default_llm
        self.quality_threshold = quality_threshold
        self.max_iterations    = max_iterations
        self.critic_persona    = critic_persona or "scientific peer reviewer"

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self, section: SectionNode) -> None:
        """
        Execute the write/critique/revise loop for one section.

        On completion (threshold met OR max iterations reached):
          - GaneshSection.status → APPROVED
          - GaneshSection.quality_score → best draft's overall score

        On unrecoverable error:
          - Logs failure and re-raises so the Section Graph can handle it.

        Note: the Section Graph manages status transitions PENDING→READY→DRAFTING.
        This method is responsible for DRAFTING→APPROVED only.
        """

        section_id   = section.section_id
        section_name = section.section_name
        brief        = section.brief

        print(f"\n[GANESH] Section Executor: {section_name}")

        best_draft_id    = None
        best_score       = 0.0
        iteration        = 0
        current_draft_id = None

        # ── Get prior approved sections for cross-section coherence ──
        prior_sections = self._get_prior_approved_sections()

        while iteration < self.max_iterations:

            iteration += 1
            print(f"  [{section_name}] Iteration {iteration}/{self.max_iterations}")

            # ── WRITER ────────────────────────────────────────────────
            self._update_section_status(section_id, SectionStatus.DRAFTING)

            draft_content = self._call_writer(
                brief          = brief,
                prior_sections = prior_sections,
                previous_draft = self._get_draft_content(current_draft_id),
                critique       = self._get_critique(current_draft_id) if current_draft_id else None,
            )

            current_draft_id = self._save_draft(section_id, iteration, draft_content)

            # ── CRITIC ────────────────────────────────────────────────
            self._update_section_status(section_id, SectionStatus.UNDER_REVIEW)

            critique_raw = self._call_critic(
                section_brief  = brief,
                draft_content  = draft_content,
                prior_sections = prior_sections,
            )

            critique = CritiqueRecord.from_llm_output(critique_raw)
            self._save_critique(section_id, current_draft_id, critique)

            print(f"  [{section_name}] Critic score: {critique.overall_score:.1f} / 10.0")

            # Track best draft
            if critique.overall_score > best_score:
                best_score    = critique.overall_score
                best_draft_id = current_draft_id

            # ── THRESHOLD CHECK ───────────────────────────────────────
            if critique.overall_score >= self.quality_threshold:
                print(f"  [{section_name}] ✅ Threshold met ({critique.overall_score:.1f} ≥ {self.quality_threshold})")
                self._approve_section(section_id, best_draft_id, best_score)
                return {
                    'approved': True,
                    'final_score': best_score,
                    'iterations': iteration,
                    'below_threshold': False,
                }

            # ── REVISER (if not at max iterations) ────────────────────
            if iteration < self.max_iterations:
                self._update_section_status(section_id, SectionStatus.REVISING)
                prev_draft_id = current_draft_id

                revised_content = self._call_reviser(
                    draft_content = draft_content,
                    critique      = critique,
                    brief         = brief,
                )

                current_draft_id = self._save_draft(section_id, iteration + 1, revised_content)
                self._save_revision(
                    section_id    = section_id,
                    from_draft_id = prev_draft_id,
                    to_draft_id   = current_draft_id,
                    critique_id   = self._get_latest_critique_id(section_id),
                    summary       = f"Applied {len(critique.actionable_issues())} critique issues",
                )

        # ── MAX ITERATIONS REACHED ────────────────────────────────────
        print(
            f"  [{section_name}] ⚠️  Max iterations reached. "
            f"Approving best draft (score={best_score:.1f})."
        )
        self._approve_section(section_id, best_draft_id, best_score, below_threshold=True)
        return {
            'approved': True,
            'final_score': best_score,
            'iterations': iteration,
            'below_threshold': True,
        }

    # ------------------------------------------------------------------
    # LLM calls (prompt assembly + invocation)
    # ------------------------------------------------------------------

    def _call_writer(
        self,
        brief:          dict,
        prior_sections: List[dict],
        previous_draft: Optional[str],
        critique:       Optional[dict],
    ) -> str:
        """
        Calls the LLM as a Writer.

        If previous_draft and critique are provided (revision mode),
        the prompt instructs the LLM to improve the draft using the critique.
        Otherwise (initial draft mode), the LLM writes from the brief.
        """

        prior_context = "\n\n".join(
            f"[{s['section_name']} — already approved]\n{s['content']}"
            for s in prior_sections
        )

        evidence_summary = self._format_section_evidence(brief.get("section_name", ""))

        if previous_draft and critique:
            # Revision mode
            issues_text = "\n".join(
                f"- [{i.get('dimension','')}] {i.get('issue','')} → {i.get('suggestion','')}"
                for i in (critique.get("issues") or [])
            )
            prompt = f"""You are a scientific writer producing one section of a research document.

SECTION: {brief.get('section_name', '')}
TYPE: {brief.get('section_type', '')}
TARGET LENGTH: approximately {brief.get('target_word_count', 500)} words

SECTION BRIEF:
{brief.get('brief', '')}

QUALITY CRITERIA:
{chr(10).join(f'- {c}' for c in brief.get('quality_criteria', []))}

AVAILABLE EVIDENCE:
{evidence_summary}

ALREADY-APPROVED SECTIONS (for coherence):
{prior_context or 'None yet.'}

YOU ARE REVISING THE FOLLOWING DRAFT.
PREVIOUS DRAFT:
{previous_draft}

CRITIQUE TO ADDRESS:
{issues_text}

Produce an improved version of the draft that specifically addresses each critique point.
Write ONLY the section content — no headers, no meta-commentary.
"""
        else:
            # Initial draft mode
            prompt = f"""You are a scientific writer producing one section of a research document.

SECTION: {brief.get('section_name', '')}
TYPE: {brief.get('section_type', '')}
TARGET LENGTH: approximately {brief.get('target_word_count', 500)} words

SECTION BRIEF:
{brief.get('brief', '')}

QUALITY CRITERIA:
{chr(10).join(f'- {c}' for c in brief.get('quality_criteria', []))}

AVAILABLE EVIDENCE:
{evidence_summary}

ALREADY-APPROVED SECTIONS (for coherence):
{prior_context or 'None yet.'}

Write the section content now.
Write ONLY the section content — no headers, no meta-commentary.
"""

        return self.llm_client(prompt, max_tokens=1500)

    def _call_critic(
        self,
        section_brief:  dict,
        draft_content:  str,
        prior_sections: List[dict],
    ) -> dict:
        """
        Calls the LLM as a Critic. Returns raw dict for CritiqueRecord.from_llm_output().
        """

        dimensions_text = "\n".join(f"- {d}" for d in QUALITY_DIMENSIONS)

        prompt = f"""You are a {self.critic_persona} evaluating a section of a scientific document.

SECTION BRIEF:
{json.dumps(section_brief, indent=2)}

DRAFT TO EVALUATE:
{draft_content}

EVALUATE THE DRAFT on these dimensions (score each 0–10):
{dimensions_text}

For each issue found, provide: dimension, issue description, and a specific suggestion.

Respond ONLY with valid JSON in this exact schema:
{{
    "scores": {{
        "scientific_accuracy": <float 0-10>,
        "narrative_coherence": <float 0-10>,
        "brief_compliance": <float 0-10>,
        "evidence_integration": <float 0-10>,
        "logical_progression": <float 0-10>,
        "cross_section_fit": <float 0-10>
    }},
    "issues": [
        {{
            "dimension": "<dimension name>",
            "severity": <int 1-10>,
            "issue": "<description of the problem>",
            "suggestion": "<specific actionable fix>"
        }}
    ]
}}
No preamble. No markdown fences. Only the JSON object.
"""

        raw_response = self.llm_client(prompt)

        # Strip markdown fences if present, then parse
        cleaned = raw_response.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
            cleaned = cleaned.rsplit("```", 1)[0]

        return json.loads(cleaned)

    def _call_reviser(
        self,
        draft_content: str,
        critique:      CritiqueRecord,
        brief:         dict,
    ) -> str:
        """Reviser re-uses the Writer prompt with the previous draft + critique injected."""
        return self._call_writer(
            brief          = brief,
            prior_sections = self._get_prior_approved_sections(),
            previous_draft = draft_content,
            critique       = {"issues": critique.issues},
        )

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _save_draft(self, section_id: int, version: int, content: str) -> int:
        from datetime import datetime
        word_count = len(content.split())
        with self.repo.transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO GaneshDraft (section_id, version, content, word_count, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (section_id, version, content, word_count, datetime.utcnow().isoformat()),
            )
            return cursor.lastrowid

    def _save_critique(
        self,
        section_id:  int,
        draft_id:    int,
        critique:    CritiqueRecord,
    ) -> int:
        with self.repo.transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO GaneshCritique
                    (section_id, draft_id, scope, scores_json, issues_json, overall_score, created_at)
                VALUES (?, ?, 'section', ?, ?, ?, ?)
                """,
                (
                    section_id,
                    draft_id,
                    json.dumps(critique.scores),
                    json.dumps(critique.issues),
                    critique.overall_score,
                    datetime.utcnow().isoformat(),
                ),
            )
            return cursor.lastrowid

    def _save_revision(
        self,
        section_id:    int,
        from_draft_id: int,
        to_draft_id:   int,
        critique_id:   Optional[int],
        summary:       str,
    ) -> None:
        with self.repo.transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO GaneshRevision
                    (section_id, from_draft_id, to_draft_id, critique_id, changes_summary, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    section_id,
                    from_draft_id,
                    to_draft_id,
                    critique_id,
                    summary,
                    datetime.utcnow().isoformat(),
                ),
            )

    def _approve_section(
        self,
        section_id:      int,
        best_draft_id:   int,
        best_score:      float,
        below_threshold: bool = False,
    ) -> None:
        now = datetime.utcnow().isoformat()
        with self.repo.transaction() as cursor:
            cursor.execute(
                "UPDATE GaneshSection SET status = 'approved', quality_score = ?, updated_at = ? WHERE id = ?",
                (best_score, now, section_id),
            )
        if below_threshold:
            with self.repo.transaction() as cursor:
                cursor.execute(
                    "UPDATE GaneshDocument SET quality_flag = 'below_threshold', updated_at = ? WHERE id = ?",
                    (now, self.document_id),
                )

    def _update_section_status(self, section_id: int, status: str) -> None:
        with self.repo.transaction() as cursor:
            cursor.execute(
                "UPDATE GaneshSection SET status = ?, updated_at = ? WHERE id = ?",
                (status, datetime.utcnow().isoformat(), section_id),
            )

    def _get_draft_content(self, draft_id: Optional[int]) -> Optional[str]:
        if draft_id is None:
            return None
        row = self.repo.fetch_one(
            "SELECT content FROM GaneshDraft WHERE id = ?", (draft_id,)
        )
        return row["content"] if row else None

    def _get_critique(self, draft_id: Optional[int]) -> Optional[dict]:
        if draft_id is None:
            return None
        row = self.repo.fetch_one(
            "SELECT scores_json, issues_json FROM GaneshCritique WHERE draft_id = ?",
            (draft_id,),
        )
        if not row:
            return None
        return {
            "scores": json.loads(row["scores_json"] or "{}"),
            "issues": json.loads(row["issues_json"] or "[]"),
        }

    def _get_latest_critique_id(self, section_id: int) -> Optional[int]:
        row = self.repo.fetch_one(
            "SELECT id FROM GaneshCritique WHERE section_id = ? ORDER BY id DESC LIMIT 1",
            (section_id,),
        )
        return row["id"] if row else None

    def _get_prior_approved_sections(self) -> List[dict]:
        """Returns text of all currently APPROVED sections, ordered by exec_order."""
        rows = self.repo.fetch_all(
            """
            SELECT gs.section_name, gs.exec_order, gd.content
            FROM GaneshSection gs
            JOIN GaneshDraft gd ON gd.section_id = gs.id
            WHERE gs.document_id = ?
              AND gs.status = 'approved'
              AND gd.version = (
                  SELECT MAX(version) FROM GaneshDraft WHERE section_id = gs.id
              )
            ORDER BY gs.exec_order ASC
            """,
            (self.document_id,),
        )
        return [dict(r) for r in rows]

    def _format_section_evidence(self, section_name: str) -> str:
        """Pull pre-indexed evidence for this section from context_bundle."""
        if not self.context_bundle:
            return "No evidence available."
        section_map = self.context_bundle.get("section_evidence_map", {})
        rows = section_map.get(section_name, [])
        if not rows:
            # fallback to knowledge_summary
            summary = self.context_bundle.get("knowledge_summary", {})
            lines = []
            for cat, vals in list(summary.items())[:4]:
                lines.append(f"{cat}: {', '.join(str(v) for v in vals[:5])}")
            return "\n".join(lines) or "No evidence available."
        # Format top 10 knowledge rows only
        lines = []
        for r in rows[:10]:
            cat = r.get("category", "")
            val = r.get("value", "")
            ctx = str(r.get("context") or "")[:150]
            lines.append(f"[{cat}] {val} — {ctx}")
        return "\n".join(lines)

    def _format_evidence(self, evidence_refs: List[str]) -> str:
        """Format evidence references from the context bundle into a readable block."""
        if not evidence_refs or not self.context_bundle:
            return "No specific evidence pre-loaded — draw from general context bundle."

        fragments = []
        for ref in evidence_refs:
            content = self.context_bundle.get("evidence", {}).get(ref)
            if content:
                fragments.append(f"[{ref}]\n{content}")

        return "\n\n".join(fragments) if fragments else "See general context bundle."
