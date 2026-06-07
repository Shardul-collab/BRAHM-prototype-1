#!/usr/bin/env python3
"""
brahm_audit.py  —  BRAHM Publication-Readiness Audit
======================================================
Run with:
    cd /mnt/d/brahm
    .venv/bin/python brahm_audit.py

Produces:
    brahm_audit_report_<timestamp>.txt   (full structured report)
    brahm_audit_summary_<timestamp>.txt  (one-line-per-check summary for CI)

Exit code:
    0  — all checks pass (or known documented stubs)
    1  — one or more FAIL checks
"""

# ─── std lib only for bootstrap ───────────────────────────────────────────────
import asyncio
import ast
import importlib
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import textwrap
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ─── Paths ────────────────────────────────────────────────────────────────────
BRAHM_ROOT      = Path("/mnt/d/brahm")
AGENTS          = BRAHM_ROOT / "agents"
BRAHM_VENV_PY   = BRAHM_ROOT / ".venv/bin/python"
SHANI_VENV_PY   = AGENTS / "shani/venv/bin/python"
CHIT_VENV_PY    = AGENTS / "chitragupta/.venv/bin/python"
GANESH_VENV_PY  = AGENTS / "ganesh/.venv/bin/python"
DB_PATH         = AGENTS / "shani/database/research_workflow.db"
AUDIT_LOG       = AGENTS / "shani/database/mcp_corrections.jsonl"
QUEUE_FILE      = AGENTS / "shani/workflow_queue.json"
PSEUDO_DIR      = AGENTS / "vishwakarma/pseudo"
QE_BIN_DIR      = Path("/usr/bin")
ENV_FILE        = AGENTS / "chitragupta/.env"
ROOT_ENV        = BRAHM_ROOT / ".env"
ROOT_REQS       = BRAHM_ROOT / "requirements.txt"
SHANI_REQS      = AGENTS / "shani/requirements.txt"

# ─── sys.path bootstrap (mirrors mcp_server.py) ───────────────────────────────
for p in [
    str(BRAHM_ROOT),
    str(AGENTS / "shani"),
    str(AGENTS / "chitragupta/analysis"),
    str(AGENTS / "vidur"),
    str(AGENTS / "vishwakarma"),
    str(AGENTS / "ganesh"),
    str(AGENTS / "chitragupta"),
]:
    if p not in sys.path:
        sys.path.insert(0, p)

# load .env before any imports that need it
try:
    from dotenv import load_dotenv
    load_dotenv(str(ENV_FILE))
except ImportError:
    pass

# ─── Result tracking ──────────────────────────────────────────────────────────
PASS    = "PASS"
FAIL    = "FAIL"
WARN    = "WARN"
SKIP    = "SKIP"
INFO    = "INFO"

results: list[dict] = []   # {section, check, status, detail}


def record(section: str, check: str, status: str, detail: str = "") -> None:
    results.append({"section": section, "check": check,
                    "status": status, "detail": detail})


# ─── Terminal colours ─────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
GREY   = "\033[90m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

COLOR = {PASS: GREEN, FAIL: RED, WARN: YELLOW, SKIP: GREY, INFO: CYAN}


def _tag(status: str) -> str:
    c = COLOR.get(status, "")
    return f"{c}{BOLD}[{status:4s}]{RESET}"


def section_header(title: str) -> None:
    bar = "─" * 68
    print(f"\n{CYAN}{BOLD}{bar}{RESET}")
    print(f"{CYAN}{BOLD}  {title}{RESET}")
    print(f"{CYAN}{BOLD}{bar}{RESET}")


def check_line(check: str, status: str, detail: str = "") -> None:
    tag   = _tag(status)
    trunc = (detail[:90] + "…") if len(detail) > 90 else detail
    print(f"  {tag}  {check}")
    if trunc:
        print(f"         {GREY}{trunc}{RESET}")


def emit(section: str, check: str, status: str, detail: str = "") -> None:
    record(section, check, status, detail)
    check_line(check, status, detail)


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 1 — ENVIRONMENT & INFRASTRUCTURE
# ══════════════════════════════════════════════════════════════════════════════

def s1_environment() -> None:
    S = "1. Environment"
    section_header(S)

    # Python version
    v = sys.version_info
    if v >= (3, 12):
        emit(S, f"Python version ({v.major}.{v.minor}.{v.micro})", PASS)
    else:
        emit(S, f"Python version ({v.major}.{v.minor})", FAIL,
             "BRAHM requires Python 3.12+")

    # BRAHM venv packages
    required_brahm = ["mcp", "httpx", "python-dotenv"]
    try:
        import importlib.metadata as ilm
        for pkg in required_brahm:
            try:
                ver = ilm.version(pkg.replace("-", "_")) or ilm.version(pkg)
                emit(S, f"BRAHM venv: {pkg}", PASS, f"v{ver}")
            except Exception:
                emit(S, f"BRAHM venv: {pkg}", FAIL, "Not installed in active venv")
    except ImportError:
        emit(S, "importlib.metadata", FAIL, "Cannot check package versions")

    # Venv presence checks
    for label, path in [
        ("BRAHM venv",        BRAHM_VENV_PY),
        ("SHANI venv",        SHANI_VENV_PY),
        ("Chitragupta venv",  CHIT_VENV_PY),
        ("GANESH venv",       GANESH_VENV_PY),
    ]:
        if path.exists():
            emit(S, f"{label} exists", PASS, str(path))
        else:
            sev = FAIL if label in ("BRAHM venv", "SHANI venv") else WARN
            emit(S, f"{label} exists", sev, f"Not found: {path}")

    # QE binaries
    qe_bins = ["pw.x","ph.x","bands.x","dos.x","projwfc.x",
               "pp.x","neb.x","hp.x","cp.x"]
    all_qe_ok = True
    for exe in qe_bins:
        found = shutil.which(exe) or (
            str(QE_BIN_DIR / exe) if (QE_BIN_DIR / exe).exists() else None
        )
        if found:
            emit(S, f"QE binary: {exe}", PASS, found)
        else:
            emit(S, f"QE binary: {exe}", FAIL, "Not found on PATH or /usr/bin")
            all_qe_ok = False

    # QE runner path mismatch check
    runner_path = AGENTS / "vishwakarma/vishwakarma/runner.py"
    if runner_path.exists():
        src = runner_path.read_text(errors="replace")
        if '"/usr/local/bin"' in src and shutil.which("pw.x") and "/usr/bin" in shutil.which("pw.x"):
            emit(S, "QE runner default bin dir", WARN,
                 "runner.py defaults to /usr/local/bin but binaries are at /usr/bin. "
                 "Set QE_BIN_DIR env var or update runner._DEFAULT_BIN_DIR")
        else:
            emit(S, "QE runner default bin dir", PASS)

    # Pseudopotentials
    ZN_SE_CRITICAL = {"Zn", "Se", "O", "N"}   # for ZnSe/ZnO research
    present_elements = set()
    if PSEUDO_DIR.exists():
        for f in PSEUDO_DIR.glob("*.UPF"):
            elem = f.name.split(".")[0]
            present_elements.add(elem)
        emit(S, f"Pseudopotentials present ({len(present_elements)} elements)",
             PASS, ", ".join(sorted(present_elements)))
        missing_critical = ZN_SE_CRITICAL - present_elements
        if missing_critical:
            emit(S, "Critical pseudopotentials for ZnSe/ZnO",
                 FAIL,
                 f"MISSING: {', '.join(sorted(missing_critical))}. "
                 "DFT calculations on ZnSe/ZnO/ZnSeO will fail without these.")
        else:
            emit(S, "Critical pseudopotentials for ZnSe/ZnO", PASS)
    else:
        emit(S, "Pseudopotential directory", FAIL, f"Not found: {PSEUDO_DIR}")

    # UNPAYWALL email (used by resolve_pdf)
    uw_email = os.environ.get("UNPAYWALL_EMAIL", "").strip()
    if uw_email:
        emit(S, "UNPAYWALL_EMAIL env var", PASS, uw_email)
    else:
        emit(S, "UNPAYWALL_EMAIL env var", WARN,
             "Not set — resolve_pdf (S2_5) will use anonymous Unpaywall requests (lower rate limit)")


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 2 — FILE INTEGRITY
# ══════════════════════════════════════════════════════════════════════════════

def s2_file_integrity() -> None:
    S = "2. File Integrity"
    section_header(S)

    # Files that must exist and be non-empty
    MUST_EXIST_NONEMPTY = [
        (BRAHM_ROOT / "mcp_server.py",                          "Entry point"),
        (BRAHM_ROOT / "brahm/brahm_registry.py",               "Tool registry"),
        (BRAHM_ROOT / "brahm/shared/constants.py",             "Constants"),
        (BRAHM_ROOT / "brahm/shared/helpers.py",               "Helpers"),
        (BRAHM_ROOT / "brahm/shared/http.py",                  "HTTP clients"),
        (BRAHM_ROOT / "brahm/agents/shani.py",                 "Group A agent"),
        (BRAHM_ROOT / "brahm/agents/chitragupta.py",           "Group B agent"),
        (BRAHM_ROOT / "brahm/agents/research.py",              "Group C agent"),
        (BRAHM_ROOT / "brahm/agents/analysis.py",              "Group D agent"),
        (BRAHM_ROOT / "brahm/agents/db_tools.py",              "Group E agent"),
        (BRAHM_ROOT / "brahm/agents/vidur.py",                 "Group G agent"),
        (BRAHM_ROOT / "brahm/agents/vishwakarma.py",           "Group H agent"),
        (BRAHM_ROOT / "brahm/agents/ganesh.py",                "Group I agent"),
        (BRAHM_ROOT / "brahm/agents/meta.py",                  "Meta agent"),
        (AGENTS / "shani/api.py",                              "SHANI FastAPI"),
        (AGENTS / "shani/core/orchestrator.py",                "SHANI orchestrator"),
        (AGENTS / "shani/tools/generate_queries.py",           "S1 tool"),
        (AGENTS / "shani/tools/search_papers.py",              "S2 tool"),
        (AGENTS / "shani/tools/download_papers.py",            "S3 tool"),
        (AGENTS / "shani/tools/extract_paper_content.py",      "S4 tool"),
        (AGENTS / "shani/tools/extract_research_knowledge.py", "S5 tool"),
        (AGENTS / "shani/tools/resolve_pdf.py",                "S2_5 tool"),
        (AGENTS / "shani/services/llm_service.py",             "LLM service"),
        (AGENTS / "shani/repositories/paper_repo.py",          "Paper repo"),
        (AGENTS / "shani/repositories/workflow_repo.py",       "Workflow repo"),
        (AGENTS / "vidur/router.py",                           "VIDUR router"),
        (AGENTS / "vidur/extractor.py",                        "VIDUR extractor"),
        (AGENTS / "vidur/auto_detector.py",                    "VIDUR detector"),
        (AGENTS / "vidur/parsers/xrd.py",                      "XRD parser"),
        (AGENTS / "vidur/parsers/uvvis.py",                    "UV-Vis parser"),
        (AGENTS / "vidur/parsers/sem_eds.py",                  "SEM-EDS parser"),
        (AGENTS / "vidur/parsers/raman.py",                    "Raman parser"),
        (AGENTS / "vishwakarma/vishwakarma/runner.py",         "QE runner"),
        (AGENTS / "vishwakarma/vishwakarma/workflow.py",       "QE workflow"),
        (AGENTS / "vishwakarma/vishwakarma/input_generator.py","QE input gen"),
        (AGENTS / "vishwakarma/vishwakarma/output_parser.py",  "QE output parser"),
        (AGENTS / "chitragupta/notion/notion_client.py",       "Notion client"),
        (AGENTS / "chitragupta/analysis/research_analyzer.py", "Research analyzer"),
    ]

    for path, label in MUST_EXIST_NONEMPTY:
        if not path.exists():
            emit(S, f"{label} — {path.name}", FAIL, f"FILE NOT FOUND: {path}")
        elif path.stat().st_size == 0:
            emit(S, f"{label} — {path.name}", FAIL, f"FILE IS EMPTY: {path}")
        else:
            emit(S, f"{label} — {path.name}", PASS,
                 f"{path.stat().st_size:,} bytes")

    # Known documented stubs (GANESH) — WARN not FAIL
    KNOWN_STUBS = [
        (AGENTS / "ganesh/ganesh_api.py",                "GANESH API (documented stub)"),
        (AGENTS / "ganesh/ganesh/document_planner.py",   "GANESH document_planner (stub)"),
        (AGENTS / "ganesh/ganesh/section_executor.py",   "GANESH section_executor (stub)"),
    ]
    for path, label in KNOWN_STUBS:
        if not path.exists():
            emit(S, label, FAIL, f"FILE NOT FOUND: {path}")
        elif path.stat().st_size == 0:
            emit(S, label, WARN,
                 "EMPTY — GANESH is a documented stub (v2.0 roadmap item). "
                 "5 Group I tools will return 'API not running' until implemented.")
        else:
            emit(S, label, PASS, f"{path.stat().st_size:,} bytes")

    # base_agent.py — empty, not imported by anything critical
    ba = BRAHM_ROOT / "brahm/agents/base_agent.py"
    if ba.exists() and ba.stat().st_size == 0:
        emit(S, "brahm/agents/base_agent.py", WARN,
             "EMPTY — suggests an unfinished base-class architecture. "
             "Not currently imported, so no runtime impact.")
    elif not ba.exists():
        emit(S, "brahm/agents/base_agent.py", WARN, "FILE NOT FOUND")
    else:
        emit(S, "brahm/agents/base_agent.py", PASS)

    # requirements.txt checks
    if not ROOT_REQS.exists() or ROOT_REQS.stat().st_size == 0:
        emit(S, "requirements.txt (root)", FAIL,
             "EMPTY or MISSING — a collaborator cannot reproduce the BRAHM venv. "
             "Fix: .venv/bin/pip freeze > requirements.txt")
    else:
        emit(S, "requirements.txt (root)", PASS)

    if not SHANI_REQS.exists():
        emit(S, "agents/shani/requirements.txt", FAIL,
             "MISSING — SHANI venv cannot be reproduced. "
             "Fix: agents/shani/venv/bin/pip freeze > agents/shani/requirements.txt")
    elif SHANI_REQS.stat().st_size == 0:
        emit(S, "agents/shani/requirements.txt", FAIL, "EMPTY")
    else:
        emit(S, "agents/shani/requirements.txt", PASS)

    # Old v1 monolith check
    old_mono = AGENTS / "vishwakarma/vishwakarma/mcp_server.py"
    if old_mono.exists() and old_mono.stat().st_size > 100_000:
        emit(S, "v1 monolith mcp_server.py in vishwakarma/", WARN,
             f"3591-line v1 server is still present at {old_mono}. "
             "It's not imported by v2 but it is confusing and should be archived or deleted.")
    elif old_mono.exists():
        emit(S, "v1 monolith in vishwakarma/", INFO, "Present but small — likely harmless")
    else:
        emit(S, "v1 monolith mcp_server.py in vishwakarma/", PASS, "Cleaned up")

    # Root .env sanity
    if ROOT_ENV.exists() and ROOT_ENV.stat().st_size == 0:
        emit(S, "Root .env", WARN, "EXISTS but is completely empty — dead file, can be removed")

    # env key naming: docs say NOTION_API_KEY, code uses NOTION_TOKEN
    emit(S, "Env key naming (NOTION_TOKEN vs NOTION_API_KEY)", WARN,
         "Technical doc (Section 5.3) says 'NOTION_API_KEY' but "
         "agents/chitragupta/.env uses 'NOTION_TOKEN' and notion_client.py "
         "imports NOTION_TOKEN from config.settings. Code is self-consistent; "
         "the documentation is wrong. Update Section 5.3 before publishing.")


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 3 — IMPORT CHAIN
# ══════════════════════════════════════════════════════════════════════════════

def s3_imports() -> None:
    S = "3. Import Chain"
    section_header(S)

    modules = [
        # core
        ("brahm.brahm_registry",        "Tool registry"),
        ("brahm.shared.constants",       "Constants"),
        ("brahm.shared.helpers",         "Helpers"),
        ("brahm.shared.http",            "HTTP clients"),
        # agent wrappers
        ("brahm.agents.shani",           "Group A wrapper"),
        ("brahm.agents.chitragupta",     "Group B wrapper"),
        ("brahm.agents.research",        "Group C wrapper"),
        ("brahm.agents.analysis",        "Group D wrapper"),
        ("brahm.agents.db_tools",        "Group E wrapper"),
        ("brahm.agents.vidur",           "Group G wrapper"),
        ("brahm.agents.vishwakarma",     "Group H wrapper"),
        ("brahm.agents.ganesh",          "Group I wrapper"),
        ("brahm.agents.meta",            "Meta wrapper"),
        # SHANI internals
        ("core.orchestrator",            "SHANI orchestrator"),
        ("repositories.repository",      "SHANI base repository"),
        ("repositories.paper_repo",      "Paper repository"),
        ("repositories.workflow_repo",   "Workflow repository"),
        # VIDUR
        ("router",                       "VIDUR router"),
        ("extractor",                    "VIDUR extractor"),
        ("auto_detector",                "VIDUR auto-detector"),
        # VIDUR parsers
        ("parsers.xrd",                  "XRD parser"),
        ("parsers.uvvis",                "UV-Vis parser"),
        ("parsers.sem_eds",              "SEM-EDS parser"),
        ("parsers.raman",                "Raman parser"),
        # Vishwakarma
        ("vishwakarma.runner",           "QE runner"),
        ("vishwakarma.input_generator",  "QE input generator"),
        ("vishwakarma.output_parser",    "QE output parser"),
        ("vishwakarma.workflow",         "QE workflow"),
        # Chitragupta
        ("notion.notion_client",         "Notion client"),
    ]

    for mod, label in modules:
        try:
            importlib.import_module(mod)
            emit(S, f"import {mod}", PASS, label)
        except Exception as exc:
            emit(S, f"import {mod}", FAIL, f"{label} — {exc}")


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 4 — TOOL REGISTRY
# ══════════════════════════════════════════════════════════════════════════════

EXPECTED_GROUPS = {
    "shani":       12,
    "chitragupta":  4,
    "research":     3,
    "analysis":     6,
    "db_tools":     4,
    "vidur":        3,
    "vishwakarma": 13,
    "ganesh":       5,
    "meta":         2,
}
EXPECTED_TOTAL = sum(EXPECTED_GROUPS.values())   # 52


def s4_tool_registry() -> None:
    S = "4. Tool Registry"
    section_header(S)

    try:
        from brahm.brahm_registry import registry
    except Exception as exc:
        emit(S, "Registry import", FAIL, str(exc))
        return

    tools = registry.all_tools()
    n = len(tools)

    if n == EXPECTED_TOTAL:
        emit(S, f"Total tool count ({n})", PASS, f"Expected {EXPECTED_TOTAL}")
    else:
        emit(S, f"Total tool count ({n})", FAIL,
             f"Expected {EXPECTED_TOTAL}, got {n}")

    # Per-group counts
    from collections import Counter
    group_counts: Counter = Counter()
    for t in tools:
        # group is in t.description or t.name prefix — read from registry directly
        pass

    # Access internal registry dict
    try:
        by_group: dict[str, list] = {}
        for name, handler in registry._handlers.items():
            tool_obj = registry._tools.get(name)
            if tool_obj is None:
                continue
            # group is not on the MCP Tool object — read from source
            grp = getattr(handler, "_brahm_group", None)
            if grp is None:
                # try to infer from name prefix
                prefix = name.split("_")[0]
                grp = prefix if prefix in EXPECTED_GROUPS else "unknown"
            by_group.setdefault(grp, []).append(name)
    except Exception as exc:
        emit(S, "Group introspection", WARN, f"Cannot read internal group map: {exc}")
        by_group = {}

    # Fall back: count via tool name prefixes
    group_prefix_map = {
        "shani":       lambda n: n.startswith("shani_") or n.startswith("queue_"),
        "chitragupta": lambda n: n.startswith("notion_"),
        "research":    lambda n: n.startswith("research_"),
        "analysis":    lambda n: n.startswith("analysis_"),
        "db_tools":    lambda n: n.startswith("db_"),
        "vidur":       lambda n: n.startswith("vidur_"),
        "vishwakarma": lambda n: n.startswith("vishwakarma_"),
        "ganesh":      lambda n: n.startswith("ganesh_"),
        "meta":        lambda n: n.startswith("brahm_"),
    }
    tool_names = [t.name for t in tools]
    for grp, predicate in group_prefix_map.items():
        count = sum(1 for tn in tool_names if predicate(tn))
        expected = EXPECTED_GROUPS[grp]
        if count == expected:
            emit(S, f"Group '{grp}' tool count ({count})", PASS)
        else:
            emit(S, f"Group '{grp}' tool count ({count})", FAIL,
                 f"Expected {expected}, got {count}")

    # JSON Schema validation on every tool
    import jsonschema  # type: ignore
    schema_errors = 0
    for t in tools:
        if not hasattr(t, "inputSchema") or t.inputSchema is None:
            emit(S, f"Schema: {t.name}", FAIL, "inputSchema is None")
            schema_errors += 1
            continue
        schema = t.inputSchema
        if not isinstance(schema, dict):
            emit(S, f"Schema: {t.name}", FAIL, "inputSchema is not a dict")
            schema_errors += 1
            continue
        if schema.get("type") != "object":
            emit(S, f"Schema: {t.name}", WARN,
                 f"inputSchema.type = '{schema.get('type')}' (expected 'object')")
        # Check required fields are declared in properties
        props = schema.get("properties", {})
        required = schema.get("required", [])
        missing_props = [r for r in required if r not in props]
        if missing_props:
            emit(S, f"Schema: {t.name}", FAIL,
                 f"Required fields not in properties: {missing_props}")
            schema_errors += 1

    if schema_errors == 0:
        emit(S, f"JSON Schema validation ({len(tools)} tools)", PASS,
             "All inputSchemas valid")

    # Description completeness
    no_desc = [t.name for t in tools if not t.description or len(t.description) < 10]
    if no_desc:
        emit(S, "Tool descriptions", WARN, f"Short/missing descriptions: {no_desc}")
    else:
        emit(S, "Tool descriptions", PASS, "All tools have descriptions")


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 5 — RESPONSE CONTRACT (static analysis)
# ══════════════════════════════════════════════════════════════════════════════

def _check_response_contract(filepath: Path) -> list[str]:
    """
    Parse a file and find @brahm_tool-decorated async functions that:
      - raise exceptions (bare raise / raise SomeError)
      - do not return _ok(...) or _err(...)
    Returns list of violation strings.
    """
    violations = []
    try:
        tree = ast.parse(filepath.read_text(errors="replace"))
    except SyntaxError as e:
        return [f"SyntaxError: {e}"]

    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue

        # Check if decorated with brahm_tool
        has_brahm_tool = any(
            (isinstance(d, ast.Call) and
             getattr(getattr(d.func, "attr", None), "__class__", None) is str and
             "brahm_tool" in ast.unparse(d))
            or
            (isinstance(d, ast.Name) and d.id == "brahm_tool")
            or
            (isinstance(d, ast.Attribute) and d.attr == "brahm_tool")
            for d in node.decorator_list
        )
        if not has_brahm_tool:
            continue

        fname = node.name
        src   = ast.unparse(node)

        # Check for bare raises
        for child in ast.walk(node):
            if isinstance(child, ast.Raise) and child.exc is not None:
                violations.append(
                    f"{fname}: contains bare raise — "
                    "handlers must never raise, use _err() instead"
                )
                break

        # Check that at least one return calls _ok or _err
        has_ok_err_return = False
        for child in ast.walk(node):
            if isinstance(child, ast.Return) and child.value is not None:
                ret_src = ast.unparse(child.value)
                if "_ok(" in ret_src or "_err(" in ret_src:
                    has_ok_err_return = True
                    break
        if not has_ok_err_return:
            violations.append(
                f"{fname}: no return _ok()/_err() found — "
                "may violate response contract"
            )

    return violations


def s5_response_contract() -> None:
    S = "5. Response Contract"
    section_header(S)

    agent_files = list((BRAHM_ROOT / "brahm/agents").glob("*.py"))
    total_violations = 0
    for f in sorted(agent_files):
        if f.stat().st_size == 0:
            continue
        violations = _check_response_contract(f)
        if violations:
            for v in violations:
                emit(S, f"Contract: {f.name}", WARN, v)
            total_violations += len(violations)
        else:
            emit(S, f"Contract: {f.name}", PASS)

    if total_violations == 0:
        emit(S, "Overall response contract", PASS, "All handlers return _ok/_err")
    else:
        emit(S, "Overall response contract", WARN,
             f"{total_violations} potential violations (review above)")


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 6 — DATABASE
# ══════════════════════════════════════════════════════════════════════════════

EXPECTED_TABLES = {
    "Workflow", "WorkflowResearchConfig", "Paper",
    "PaperContent", "ResearchKnowledge", "Stage",
    "ExecutionAttempt", "Failure",
}


def s6_database() -> None:
    S = "6. Database"
    section_header(S)

    if not DB_PATH.exists():
        emit(S, "SQLite DB exists", FAIL, f"Not found: {DB_PATH}")
        return

    size_mb = DB_PATH.stat().st_size / 1_048_576
    emit(S, f"SQLite DB exists ({size_mb:.0f} MB)", PASS)

    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        cur  = conn.cursor()

        # Tables
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r[0] for r in cur.fetchall()}
        missing = EXPECTED_TABLES - tables
        extra   = tables - EXPECTED_TABLES - {"sqlite_sequence"}

        if not missing:
            emit(S, f"All {len(EXPECTED_TABLES)} expected tables present", PASS,
                 ", ".join(sorted(EXPECTED_TABLES)))
        else:
            emit(S, "Missing tables", FAIL,
                 f"Expected but missing: {', '.join(sorted(missing))}")
        if extra:
            emit(S, "Extra tables", INFO,
                 f"Not in spec but present: {', '.join(sorted(extra))}")

        # Row counts
        for table in sorted(EXPECTED_TABLES & tables):
            try:
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                count = cur.fetchone()[0]
                emit(S, f"Table {table}", PASS, f"{count:,} rows")
            except Exception as exc:
                emit(S, f"Table {table} count", FAIL, str(exc))

        # Integrity check
        cur.execute("PRAGMA integrity_check")
        ic = cur.fetchone()[0]
        if ic == "ok":
            emit(S, "SQLite integrity_check", PASS)
        else:
            emit(S, "SQLite integrity_check", FAIL, ic)

        # Index check — at least some indexes should exist
        cur.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index'")
        idx_count = cur.fetchone()[0]
        if idx_count >= 3:
            emit(S, f"Indexes present ({idx_count})", PASS)
        else:
            emit(S, f"Indexes present ({idx_count})", WARN,
                 "Very few indexes — large DB may be slow without them")

        conn.close()

    except sqlite3.OperationalError as exc:
        emit(S, "SQLite connection", FAIL, str(exc))
        return

    # Audit log
    if AUDIT_LOG.exists():
        lines = AUDIT_LOG.read_text(errors="replace").strip().splitlines()
        emit(S, f"Audit log (mcp_corrections.jsonl)", PASS,
             f"{len(lines)} correction records")
    else:
        emit(S, "Audit log (mcp_corrections.jsonl)", WARN,
             "Not found — either no corrections have been made or path is wrong")


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 7 — TOOL EXECUTION (OFFLINE)
# ══════════════════════════════════════════════════════════════════════════════

async def _dispatch(name: str, args: dict) -> dict:
    from brahm.brahm_registry import registry
    return await registry.dispatch(name, args)


async def _run_offline_tools() -> list[tuple[str, str, str, str]]:
    """Returns list of (section, check, status, detail)"""
    results_local = []

    async def check(tool: str, args: dict, expect_key: str | None = None,
                    allow_offline: bool = False) -> None:
        try:
            result = await asyncio.wait_for(_dispatch(tool, args), timeout=15.0)
            if result.get("status") == "error":
                if allow_offline and "not running" in result.get("error", "").lower():
                    results_local.append(
                        ("7. Tool Execution", f"tool: {tool}", SKIP,
                         "API not running (expected for offline test)"))
                else:
                    results_local.append(
                        ("7. Tool Execution", f"tool: {tool}", FAIL,
                         f"status=error: {result.get('error', '')} "
                         f"| {result.get('detail', '')}"))
            elif expect_key and expect_key not in result:
                results_local.append(
                    ("7. Tool Execution", f"tool: {tool}", FAIL,
                     f"Expected key '{expect_key}' not in response"))
            else:
                summary = {}
                for k in list(result.keys())[:4]:
                    summary[k] = result[k]
                results_local.append(
                    ("7. Tool Execution", f"tool: {tool}", PASS,
                     str(summary)[:120]))
        except asyncio.TimeoutError:
            results_local.append(
                ("7. Tool Execution", f"tool: {tool}", FAIL, "Timed out after 15s"))
        except Exception as exc:
            results_local.append(
                ("7. Tool Execution", f"tool: {tool}", FAIL, str(exc)))

    # Meta tools (no external deps)
    await check("brahm_health",   {})
    await check("brahm_overview", {})

    # VIDUR (local import, no API)
    await check("vidur_health",          {})
    await check("vidur_list_techniques", {})
    await check("vidur_classify", {
        "file_path": "/tmp/brahm_test_dummy.txt",
        "content":   "2Theta Intensity\n20.0 100\n25.0 2000\n30.0 150"
    })

    # Vishwakarma (local, no QE execution)
    await check("vishwakarma_health",              {})
    await check("vishwakarma_list_pseudopotentials", {})
    await check("vishwakarma_list_jobs",           {"status_filter": "all"})
    await check("vishwakarma_generate_input", {
        "structure": {
            "elements":   ["Si", "Si"],
            "positions":  [[0.0, 0.0, 0.0], [0.25, 0.25, 0.25]],
            "cell":       [[0, 2.715, 2.715], [2.715, 0, 2.715], [2.715, 2.715, 0]],
        },
        "calc_type":   "scf",
        "pseudo_dir":  str(PSEUDO_DIR),
        "pseudos":     {"Si": "Si.pbe-n-rrkjus_psl.1.0.0.UPF"},
        "ecutwfc":     40.0,
        "kpoints":     [4, 4, 4],
    })

    # SHANI tools that read SQLite directly (no API needed)
    await check("shani_get_all_status",       {}, expect_key="total_workflows")
    await check("research_get_database_stats", {})
    await check("research_knowledge_summary",  {})

    # GANESH (documented stub — should return graceful offline message)
    await check("ganesh_health",       {}, allow_offline=True)
    await check("ganesh_list_documents", {}, allow_offline=True)

    # Chitragupta (requires Notion API key but should not crash without it)
    await check("notion_query_papers", {
        "workflow_theme": "ZnSe Fundamentals",
        "min_relevance":  0.5,
    })

    return results_local


def s7_tool_execution() -> None:
    S = "7. Tool Execution"
    section_header(S)

    try:
        # Write dummy test file for VIDUR classify
        Path("/tmp/brahm_test_dummy.txt").write_text(
            "2Theta Intensity\n20.0 100\n25.0 2000\n30.0 150\n35.0 800\n"
        )
        offline_results = asyncio.run(_run_offline_tools())
        for (sec, check, status, detail) in offline_results:
            emit(sec, check, status, detail)
    except Exception as exc:
        emit(S, "Offline tool execution", FAIL, traceback.format_exc()[:300])
    finally:
        try:
            Path("/tmp/brahm_test_dummy.txt").unlink(missing_ok=True)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 8 — SHANI API (live, conditional)
# ══════════════════════════════════════════════════════════════════════════════

async def _check_shani_live() -> bool:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.get("http://localhost:8000/docs")
            return r.status_code == 200
    except Exception:
        return False


async def _run_shani_live() -> list[tuple]:
    results_local = []
    shani_up = await _check_shani_live()
    if not shani_up:
        results_local.append(
            ("8. SHANI API", "SHANI API reachable at :8000", SKIP,
             "SHANI not running — start with: "
             "cd agents/shani && source venv/bin/activate && "
             "uvicorn api:app --host 0.0.0.0 --port 8000"))
        return results_local

    results_local.append(("8. SHANI API", "SHANI API reachable at :8000", PASS, ""))

    async def call(tool, args, check_key=None):
        try:
            from brahm.brahm_registry import registry
            result = await asyncio.wait_for(registry.dispatch(tool, args), timeout=30.0)
            if result.get("status") == "error":
                results_local.append(
                    ("8. SHANI API", f"live: {tool}", FAIL,
                     result.get("error", "") + " | " + result.get("detail", "")))
            elif check_key and check_key not in result:
                results_local.append(
                    ("8. SHANI API", f"live: {tool}", FAIL,
                     f"Key '{check_key}' not in response"))
            else:
                results_local.append(("8. SHANI API", f"live: {tool}", PASS,
                                       str(result)[:120]))
        except Exception as exc:
            results_local.append(("8. SHANI API", f"live: {tool}", FAIL, str(exc)))

    # Create a test workflow
    await call("shani_create_workflow", {
        "name":     "__brahm_audit_test__",
        "material": "Si",
        "focus":    "bandgap test audit",
    }, check_key="workflow_id")

    # Check all workflows
    await call("shani_get_all_status", {}, check_key="total_workflows")

    # Check SHANI /docs endpoint has expected routes
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get("http://localhost:8000/openapi.json")
            if r.status_code == 200:
                paths = set(r.json().get("paths", {}).keys())
                expected = {"/workflows", "/workflows/{workflow_id}/run",
                            "/workflows/{workflow_id}/status",
                            "/workflows/batch"}
                missing = expected - paths
                if not missing:
                    results_local.append(
                        ("8. SHANI API", "SHANI API routes complete", PASS,
                         f"{len(paths)} routes registered"))
                else:
                    results_local.append(
                        ("8. SHANI API", "SHANI API routes complete", FAIL,
                         f"Missing routes: {missing}"))
    except Exception as exc:
        results_local.append(("8. SHANI API", "SHANI OpenAPI schema", WARN, str(exc)))

    return results_local


def s8_shani_api() -> None:
    S = "8. SHANI API"
    section_header(S)
    r = asyncio.run(_run_shani_live())
    for (sec, check, status, detail) in r:
        emit(sec, check, status, detail)


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 9 — DOCUMENTATION vs CODE CROSS-CHECK
# ══════════════════════════════════════════════════════════════════════════════

def s9_doc_vs_code() -> None:
    S = "9. Doc vs Code"
    section_header(S)

    # Env key naming mismatch (already flagged in s2, detail here)
    emit(S, "NOTION_TOKEN (code) vs NOTION_API_KEY (docs, Section 5.3)",
         FAIL,
         "The technical reference manual Section 5.3 documents 'NOTION_API_KEY' "
         "but notion_client.py imports 'NOTION_TOKEN' from config.settings, "
         "and agents/chitragupta/.env declares 'NOTION_TOKEN'. "
         "The documentation is wrong. Must fix before publication.")

    # Stage sequence: constants.py has S5_5, but tech doc and shani.py STAGE_ENUM don't
    constants_src = (BRAHM_ROOT / "brahm/shared/constants.py").read_text()
    shani_src     = (BRAHM_ROOT / "brahm/agents/shani.py").read_text(errors="replace")
    if "S5_5" in constants_src and "S5_5" not in shani_src:
        emit(S, "Stage S5_5 in constants.py but not in shani.py STAGE_ENUM", WARN,
             "SHANI_STAGE_SEQUENCE in constants.py includes 'S5_5' but shani.py's "
             "STAGE_ENUM (used for 'stop_after_stage') does not. "
             "Claude cannot stop after S5_5 via shani_run_workflow.")
    else:
        emit(S, "Stage S5_5 consistency (constants vs shani.py)", PASS)

    # Tool count per group — doc says Group A=12, cross-check
    tool_names_src = []
    for f in (BRAHM_ROOT / "brahm/agents").glob("*.py"):
        if f.stat().st_size == 0:
            continue
        tool_names_src += re.findall(r"name=['\"]([a-z_]+)['\"]",
                                     f.read_text(errors="replace"))

    group_counts = {
        "shani":       sum(1 for n in tool_names_src
                          if n.startswith("shani_") or n == "queue_add_workflow"),
        "chitragupta": sum(1 for n in tool_names_src if n.startswith("notion_")),
        "research":    sum(1 for n in tool_names_src if n.startswith("research_")),
        "analysis":    sum(1 for n in tool_names_src if n.startswith("analysis_")),
        "db_tools":    sum(1 for n in tool_names_src if n.startswith("db_")),
        "vidur":       sum(1 for n in tool_names_src if n.startswith("vidur_")),
        "vishwakarma": sum(1 for n in tool_names_src if n.startswith("vishwakarma_")),
        "ganesh":      sum(1 for n in tool_names_src if n.startswith("ganesh_")),
        "meta":        sum(1 for n in tool_names_src if n.startswith("brahm_")),
    }
    for grp, actual in group_counts.items():
        expected = EXPECTED_GROUPS[grp]
        if actual == expected:
            emit(S, f"Documented tool count Group '{grp}' ({expected})", PASS)
        else:
            emit(S, f"Documented tool count Group '{grp}'", FAIL,
                 f"Doc says {expected}, source has {actual}")

    # hardcoded paths in mcp_server.py (should all be /mnt/d/)
    entry_src = (BRAHM_ROOT / "mcp_server.py").read_text()
    if "/mnt/d/brahm" not in entry_src:
        emit(S, "Entry point path references", WARN,
             "mcp_server.py doesn't reference /mnt/d/brahm — check sys.path setup")
    else:
        emit(S, "Entry point path references", PASS)

    # GANESH stub status clearly documented
    ganesh_src = (BRAHM_ROOT / "brahm/agents/ganesh.py").read_text()
    if "stub" in ganesh_src.lower() or "pending" in ganesh_src.lower():
        emit(S, "GANESH stub status documented in code", PASS,
             "ganesh.py docstring mentions stub/pending status")
    else:
        emit(S, "GANESH stub status documented in code", WARN,
             "GANESH is a stub but this isn't clearly stated in the source")


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 10 — PUBLICATION GAPS
# ══════════════════════════════════════════════════════════════════════════════

def s10_publication_gaps() -> None:
    S = "10. Publication Gaps"
    section_header(S)

    # GANESH completeness
    emit(S, "GANESH implementation (Group I, 5 tools)", FAIL,
         "ganesh_api.py, document_planner.py, section_executor.py are all empty. "
         "All 5 Group I tools return 'API not running'. "
         "BRAHM cannot produce any scientific documents without GANESH. "
         "This is the single biggest blocker for a complete system demonstration.")

    # Missing pseudopotentials for the stated research purpose
    zn_present = any(f.name.startswith("Zn.") for f in PSEUDO_DIR.glob("*.UPF"))
    se_present = any(f.name.startswith("Se.") for f in PSEUDO_DIR.glob("*.UPF"))
    if not zn_present:
        emit(S, "Zn pseudopotential for ZnSe/ZnO DFT", FAIL,
             "Zn.UPF missing from agents/vishwakarma/pseudo/. "
             "No Zn-containing DFT calculations (ZnSe, ZnO, ZnSeO) are possible.")
    if not se_present:
        emit(S, "Se pseudopotential for ZnSe DFT", FAIL,
             "Se.UPF missing from agents/vishwakarma/pseudo/. "
             "ZnSe and ZnSeO DFT calculations will fail.")

    # requirements.txt reproducibility
    emit(S, "Environment reproducibility (requirements.txt)", FAIL,
         "Root requirements.txt is empty and agents/shani/requirements.txt is missing. "
         "A reviewer or collaborator cannot reproduce either virtual environment. "
         "This is a hard blocker for any publication or open-source release.")

    # Test coverage
    test_files = list(BRAHM_ROOT.rglob("test_*.py")) + list(BRAHM_ROOT.rglob("*_test.py"))
    test_files = [f for f in test_files if "venv" not in str(f) and "site-packages" not in str(f)]
    if test_files:
        emit(S, f"Test files found ({len(test_files)})", INFO,
             " | ".join(str(f.relative_to(BRAHM_ROOT)) for f in test_files[:5]))
    else:
        emit(S, "Unit/integration test suite", WARN,
             "No test_*.py files found outside venvs. "
             "Publication-grade code should have a test suite (pytest).")

    # README at root
    readme = BRAHM_ROOT / "README.md"
    if readme.exists() and readme.stat().st_size > 500:
        emit(S, "README.md", PASS, f"{readme.stat().st_size:,} bytes")
    elif readme.exists():
        emit(S, "README.md", WARN, "Exists but very short — expand for publication")
    else:
        emit(S, "README.md", FAIL, "Not found — required for any public release")

    # CHANGELLOG / CHANGELOG
    cl = next(BRAHM_ROOT.glob("CHANGE*"), None) or next(BRAHM_ROOT.glob("HISTORY*"), None)
    if cl:
        emit(S, "CHANGELOG", PASS, str(cl.name))
    else:
        emit(S, "CHANGELOG", WARN, "No CHANGELOG found — recommended for releases")

    # LICENSE
    lic = next(BRAHM_ROOT.glob("LICENSE*"), None)
    if lic:
        emit(S, "LICENSE", PASS, str(lic.name))
    else:
        emit(S, "LICENSE", WARN, "No LICENSE file — required for open-source publication")

    # .gitignore — should exclude .env, venv, *.db
    gi = BRAHM_ROOT / ".gitignore"
    if gi.exists():
        gi_content = gi.read_text(errors="replace")
        for pat in [".env", "venv/", ".venv/", "*.db"]:
            if pat not in gi_content:
                emit(S, f".gitignore includes '{pat}'", WARN,
                     f"Pattern '{pat}' not in .gitignore — may accidentally commit secrets or binaries")
            else:
                emit(S, f".gitignore includes '{pat}'", PASS)
    else:
        emit(S, ".gitignore", WARN, "No .gitignore — risk of committing .env secrets or 2.2GB .db to git")


# ══════════════════════════════════════════════════════════════════════════════
#  FINAL REPORT
# ══════════════════════════════════════════════════════════════════════════════

def _build_report(duration: float) -> str:
    lines = []
    lines.append("=" * 72)
    lines.append("  BRAHM PUBLICATION-READINESS AUDIT REPORT")
    lines.append(f"  Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"  Duration:  {duration:.1f}s")
    lines.append("=" * 72)

    # Count per status
    counts = {PASS: 0, FAIL: 0, WARN: 0, SKIP: 0, INFO: 0}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    lines.append(f"\n  PASS: {counts[PASS]}   FAIL: {counts[FAIL]}   "
                 f"WARN: {counts[WARN]}   SKIP: {counts[SKIP]}   INFO: {counts[INFO]}")
    lines.append("")

    # Section breakdown
    sections: dict[str, list[dict]] = {}
    for r in results:
        sections.setdefault(r["section"], []).append(r)

    for sec, items in sections.items():
        lines.append(f"\n{'─'*72}")
        lines.append(f"  {sec}")
        lines.append(f"{'─'*72}")
        for item in items:
            tag = f"[{item['status']:4s}]"
            lines.append(f"  {tag}  {item['check']}")
            if item["detail"]:
                wrapped = textwrap.fill(item["detail"], width=65,
                                        initial_indent=" " * 9,
                                        subsequent_indent=" " * 9)
                lines.append(wrapped)

    # Summary verdict
    lines.append(f"\n{'='*72}")
    lines.append("  VERDICT")
    lines.append(f"{'='*72}")
    fails = [r for r in results if r["status"] == FAIL]
    warns = [r for r in results if r["status"] == WARN]

    if not fails:
        lines.append("  PUBLICATION READY (with caveats noted as WARN)")
    else:
        lines.append(f"  NOT PUBLICATION READY — {len(fails)} blocking issue(s):")
        for f in fails:
            lines.append(f"    ✗  [{f['section']}]  {f['check']}")

    if warns:
        lines.append(f"\n  {len(warns)} warning(s) to address before release:")
        for w in warns:
            lines.append(f"    ⚠  [{w['section']}]  {w['check']}")

    lines.append(f"\n{'='*72}")
    lines.append("  ACTION PRIORITY")
    lines.append(f"{'='*72}")
    lines.append("""
  P0 — BLOCKERS (fix before any release)
    1. GANESH API + document_planner + section_executor — all empty.
       Implement G1-G5 pipeline in ganesh_api.py.
    2. Zn.UPF + Se.UPF missing — ZnSe/ZnO DFT calculations impossible.
       Download from https://pseudopotentials.quantum-espresso.org/
    3. requirements.txt (root) empty — add:
         .venv/bin/pip freeze > requirements.txt
    4. agents/shani/requirements.txt missing — add:
         agents/shani/venv/bin/pip freeze > agents/shani/requirements.txt
    5. Fix Technical Reference Manual Section 5.3:
         NOTION_API_KEY  →  NOTION_TOKEN

  P1 — HIGH (fix before open-source release)
    6. Add Zn, Se, ZnO-related pseudopotentials.
    7. Set QE_BIN_DIR=/usr/bin in shell profile (runner defaults to /usr/local/bin).
    8. Add README.md, LICENSE, CHANGELOG.
    9. Archive or delete agents/vishwakarma/vishwakarma/mcp_server.py (v1 ghost).
   10. Add S5_5 to shani.py STAGE_ENUM or remove from constants.py.

  P2 — MEDIUM (polish for peer review)
   11. Add pytest-based test suite.
   12. Fill in brahm/agents/base_agent.py or remove the file.
   13. Add .gitignore to prevent .env / 2.2GB .db from being committed.
   14. Set UNPAYWALL_EMAIL for better paper resolution rate limits.
""")
    lines.append("=" * 72)
    return "\n".join(lines)


def _build_summary() -> str:
    lines = [
        f"brahm_audit {datetime.now(timezone.utc).date()}",
        f"total={len(results)} pass={sum(1 for r in results if r['status']==PASS)} "
        f"fail={sum(1 for r in results if r['status']==FAIL)} "
        f"warn={sum(1 for r in results if r['status']==WARN)}"
    ]
    for r in results:
        lines.append(f"{r['status']}\t{r['section']}\t{r['check']}\t{r['detail'][:100]}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    print(f"\n{BOLD}{CYAN}{'═'*70}{RESET}")
    print(f"{BOLD}{CYAN}  BRAHM PUBLICATION-READINESS AUDIT  v2.0{RESET}")
    print(f"{BOLD}{CYAN}  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{RESET}")
    print(f"{BOLD}{CYAN}{'═'*70}{RESET}\n")

    t0 = time.monotonic()
    s1_environment()
    s2_file_integrity()
    s3_imports()
    s4_tool_registry()
    s5_response_contract()
    s6_database()
    s7_tool_execution()
    s8_shani_api()
    s9_doc_vs_code()
    s10_publication_gaps()
    duration = time.monotonic() - t0

    # Print final summary to terminal
    fails = sum(1 for r in results if r["status"] == FAIL)
    warns = sum(1 for r in results if r["status"] == WARN)
    passes = sum(1 for r in results if r["status"] == PASS)

    bar = "═" * 70
    print(f"\n{BOLD}{CYAN}{bar}{RESET}")
    print(f"{BOLD}  AUDIT COMPLETE  ({duration:.1f}s){RESET}")
    print(f"  {GREEN}{BOLD}PASS: {passes}{RESET}  "
          f"{RED}{BOLD}FAIL: {fails}{RESET}  "
          f"{YELLOW}{BOLD}WARN: {warns}{RESET}")
    if fails > 0:
        print(f"\n  {RED}{BOLD}NOT PUBLICATION READY — {fails} blocking issue(s){RESET}")
    else:
        print(f"\n  {GREEN}{BOLD}All checks passed (review warnings){RESET}")
    print(f"{BOLD}{CYAN}{bar}{RESET}\n")

    # Write report files
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path  = BRAHM_ROOT / f"brahm_audit_report_{ts}.txt"
    summary_path = BRAHM_ROOT / f"brahm_audit_summary_{ts}.txt"

    report_text = _build_report(duration)
    report_path.write_text(report_text, encoding="utf-8")
    summary_path.write_text(_build_summary(), encoding="utf-8")

    print(f"  Full report  → {report_path}")
    print(f"  Summary CSV  → {summary_path}\n")

    return 1 if fails > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
