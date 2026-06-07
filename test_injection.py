"""
test_injection.py — BRAHM Full Pipeline Injection Test
=======================================================
Creates a fresh test DB, injects realistic TiO2:Zn photodetector
research data, then tests every tool group end-to-end.

Run with: /mnt/d/brahm/.venv/bin/python /mnt/d/brahm/test_injection.py
"""

import asyncio
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
BRAHM_ROOT   = Path("/mnt/d/brahm")
TEST_DB_PATH = str(BRAHM_ROOT / "data" / "test_tio2.db")
PSEUDO_DIR   = str(BRAHM_ROOT / "agents/vishwakarma/pseudo")
QE_BIN_DIR   = "/mnt/d/miniforge3/bin"
QE_WORKDIR   = "/tmp/brahm_test_qe_jobs"

# ── sys.path ──────────────────────────────────────────────────────────────────
sys.path.insert(0, str(BRAHM_ROOT))
sys.path.insert(0, str(BRAHM_ROOT / "agents/shani"))
sys.path.insert(0, str(BRAHM_ROOT / "agents/chitragupta/analysis"))
sys.path.insert(0, str(BRAHM_ROOT / "agents/vidur"))
sys.path.insert(0, str(BRAHM_ROOT / "agents/vishwakarma"))
sys.path.insert(0, str(BRAHM_ROOT / "agents/ganesh"))
sys.path.insert(0, str(BRAHM_ROOT / "agents/chitragupta"))

from dotenv import load_dotenv
load_dotenv(str(BRAHM_ROOT / "agents/chitragupta/.env"))

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger("brahm.test")

# ── Colours ───────────────────────────────────────────────────────────────────
G  = "\033[92m"  # green
R  = "\033[91m"  # red
Y  = "\033[93m"  # yellow
B  = "\033[94m"  # blue
W  = "\033[97m"  # white
RS = "\033[0m"   # reset

PASS = f"{G}PASS{RS}"
FAIL = f"{R}FAIL{RS}"
SKIP = f"{Y}SKIP{RS}"
INFO = f"{B}INFO{RS}"

results = []

def check(name, condition, detail=""):
    status = PASS if condition else FAIL
    results.append((name, condition, detail))
    detail_str = f" — {detail}" if detail else ""
    print(f"  {status}  {name}{detail_str}")
    return condition

def section(title):
    print(f"\n{W}{'═'*60}{RS}")
    print(f"{W}  {title}{RS}")
    print(f"{W}{'═'*60}{RS}")

# ═══════════════════════════════════════════════════════════════════════════════
# 1. FRESH DATABASE SETUP
# ═══════════════════════════════════════════════════════════════════════════════

def create_test_db():
    """Create fresh SQLite DB with SHANI schema."""
    section("1. FRESH DATABASE SETUP")

    # Ensure dirs exist
    Path(TEST_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    Path(QE_WORKDIR).mkdir(parents=True, exist_ok=True)

    # Remove old test DB if exists
    if Path(TEST_DB_PATH).exists():
        Path(TEST_DB_PATH).unlink()
        print(f"  {INFO}  Removed old test DB")

    con = sqlite3.connect(TEST_DB_PATH)
    con.executescript("""
    CREATE TABLE Workflow (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        status TEXT DEFAULT 'paused',
        current_stage TEXT DEFAULT 'S1',
        created_at TEXT,
        updated_at TEXT
    );
    CREATE TABLE WorkflowResearchConfig (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        workflow_id INTEGER UNIQUE,
        material TEXT,
        focus TEXT,
        structure TEXT,
        method TEXT,
        properties TEXT,
        characterization TEXT,
        domain TEXT
    );
    CREATE TABLE Paper (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        workflow_id INTEGER,
        title TEXT,
        doi TEXT,
        abstract TEXT,
        pdf_url TEXT,
        pdf_status TEXT DEFAULT 'pending',
        status TEXT DEFAULT 'pending',
        source TEXT DEFAULT 'semantic_scholar',
        raw_text TEXT,
        file_path TEXT,
        failed_candidates TEXT,
        last_error TEXT,
        created_at TEXT,
        updated_at TEXT
    );
    CREATE TABLE PaperContent (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        paper_id INTEGER,
        section_name TEXT,
        content TEXT
    );
    CREATE TABLE ResearchKnowledge (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        paper_id INTEGER,
        category TEXT,
        value TEXT,
        context TEXT
    );
    CREATE TABLE Stage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        workflow_id INTEGER,
        stage_name TEXT,
        status TEXT DEFAULT 'pending',
        started_at TEXT,
        ended_at TEXT
    );
    CREATE TABLE ExecutionAttempt (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stage_id INTEGER,
        attempt_number INTEGER DEFAULT 1,
        status TEXT,
        started_at TEXT,
        ended_at TEXT,
        error_message TEXT
    );
    CREATE TABLE FailureLog (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        workflow_id INTEGER,
        failure_type TEXT,
        error_message TEXT,
        stage_id INTEGER,
        created_at TEXT
    );
    CREATE INDEX idx_paper_workflow ON Paper(workflow_id);
    CREATE INDEX idx_content_paper  ON PaperContent(paper_id);
    CREATE INDEX idx_knowledge_paper ON ResearchKnowledge(paper_id);
    CREATE INDEX idx_knowledge_cat  ON ResearchKnowledge(category);
    """)
    con.commit()
    con.close()

    check("Test DB created", Path(TEST_DB_PATH).exists(), TEST_DB_PATH)
    check("QE workdir created", Path(QE_WORKDIR).exists(), QE_WORKDIR)

# ═══════════════════════════════════════════════════════════════════════════════
# 2. INJECT SYNTHETIC RESEARCH DATA
# ═══════════════════════════════════════════════════════════════════════════════

SYNTHETIC_PAPERS = [
    {
        "title": "Zn-doped TiO2 thin films for UV-Vis photodetection: effect of doping concentration",
        "doi": "10.1016/j.tsf.2023.001",
        "abstract": "We report Zn-doped TiO2 anatase thin films deposited by magnetron sputtering with Zn concentrations of 1–5 at%. Optimal photodetector response was observed at 2.5 at% Zn, showing a 340% enhancement in photocurrent under 365 nm illumination. XRD confirms retention of anatase phase up to 3 at% Zn. Above 3 at%, secondary ZnO phases emerge. The optical bandgap narrows from 3.2 eV (pure TiO2) to 2.95 eV at 2.5% Zn, extending photoresponse into visible range.",
        "status": "extracted",
        "content": {
            "introduction": "TiO2 is a wide-bandgap semiconductor (3.2 eV anatase) widely used in photodetectors. Zn doping modifies carrier concentration and optical absorption edge.",
            "methods": "Magnetron sputtering at 300°C substrate temperature. Post-deposition annealing at 400°C in O2 atmosphere. XRD, UV-Vis, and Hall effect measurements.",
            "results": "At 2.5 at% Zn: photocurrent = 12.4 μA, dark current = 0.03 μA. Responsivity = 8.2 A/W at 365 nm. Carrier concentration increased from 1e16 to 4e17 cm-3.",
            "conclusion": "Zn substitution at Ti sites confirmed by XPS. Optimal concentration 2-3 at% for photodetector applications."
        },
        "knowledge": [
            ("synthesis_method", "magnetron sputtering", "Zn-doped TiO2 deposited by magnetron sputtering"),
            ("synthesis_method", "annealing", "post-deposition annealing at 400°C in O2"),
            ("characterization", "XRD", "XRD confirms anatase phase retention"),
            ("characterization", "UV-Vis", "optical bandgap measured by UV-Vis"),
            ("characterization", "Hall effect", "carrier concentration by Hall effect"),
            ("characterization", "XPS", "Zn substitution at Ti sites confirmed by XPS"),
            ("material", "TiO2", "anatase TiO2 host matrix"),
            ("material", "Zn", "dopant at 1-5 at% concentration"),
            ("application", "photodetector", "UV-Vis photodetector application"),
            ("application", "photocurrent", "340% photocurrent enhancement at 2.5% Zn"),
        ]
    },
    {
        "title": "First-principles study of Zn substitution in anatase TiO2: electronic structure and defect levels",
        "doi": "10.1103/PhysRevB.2023.045201",
        "abstract": "DFT+U calculations reveal that Zn substituting at the Ti site (Zn_Ti) in anatase TiO2 introduces acceptor levels 0.3 eV above the valence band maximum. The formation energy of Zn_Ti is 1.8 eV under O-rich conditions, decreasing to 0.9 eV under O-poor conditions. Charge compensation via oxygen vacancies (V_O) stabilises the doped system. The Zn_Ti-V_O complex has a binding energy of 0.4 eV, suggesting co-doping strategies.",
        "doi": "10.1103/PhysRevB.2023.045201",
        "abstract": "DFT+U calculations reveal Zn_Ti acceptor levels 0.3 eV above VBM. Formation energy 1.8 eV (O-rich) to 0.9 eV (O-poor). Zn_Ti-V_O complex binding energy 0.4 eV.",
        "status": "extracted",
        "content": {
            "introduction": "Point defects in TiO2 strongly affect optical and electronic properties. Zn doping introduces acceptor states that modify carrier dynamics.",
            "methods": "DFT+U calculations with U=4.2 eV on Ti 3d states. PAW pseudopotentials. 2x2x2 supercell of anatase TiO2 (96 atoms). PBE functional.",
            "results": "Zn_Ti formation energy: 1.8 eV (O-rich), 0.9 eV (O-poor). Acceptor level at VBM+0.3 eV. Bandgap reduced by 0.28 eV. Effective mass of holes reduced by 15%.",
            "conclusion": "Zn at Ti substitutional site is the most stable configuration. Interstitial Zn is 1.2 eV higher in energy. O-poor growth conditions recommended."
        },
        "knowledge": [
            ("computational_method", "DFT+U", "DFT+U with U=4.2 eV on Ti 3d states"),
            ("computational_method", "PAW", "PAW pseudopotentials PBE functional"),
            ("material", "TiO2", "anatase TiO2 2x2x2 supercell 96 atoms"),
            ("material", "Zn", "Zn_Ti substitutional defect"),
            ("characterization", "formation energy", "Zn_Ti formation energy 1.8 eV O-rich"),
            ("application", "photodetector", "acceptor levels modify carrier dynamics"),
            ("synthesis_method", "O-poor conditions", "O-poor growth recommended for lower formation energy"),
        ]
    },
    {
        "title": "Anatase vs rutile TiO2 for photodetector applications: a comparative study",
        "doi": "10.1021/acsami.2023.12345",
        "abstract": "Anatase TiO2 outperforms rutile for photodetector applications due to higher electron mobility (20 vs 0.1 cm2/Vs), longer carrier lifetime (microseconds vs nanoseconds), and indirect bandgap suppressing recombination. Zn doping is more effective in anatase: photocurrent enhancement 340% vs 85% in rutile. Phase transformation from anatase to rutile occurs above 700°C.",
        "status": "extracted",
        "content": {
            "introduction": "Phase selection is critical for TiO2 device performance. Anatase and rutile have fundamentally different electronic structures.",
            "methods": "Comparative study of sputtered anatase and rutile films. Same Zn doping protocol applied to both phases.",
            "results": "Anatase: mobility=20 cm2/Vs, lifetime=2.3 μs, photoresponse=340%. Rutile: mobility=0.1 cm2/Vs, lifetime=0.8 ns, photoresponse=85%.",
            "conclusion": "Anatase phase strongly preferred for Zn-doped TiO2 photodetectors. Substrate temperature must be kept below 600°C to prevent phase transition."
        },
        "knowledge": [
            ("material", "TiO2 anatase", "anatase phase preferred for photodetectors"),
            ("material", "TiO2 rutile", "rutile phase inferior for photodetectors"),
            ("characterization", "Hall effect", "electron mobility 20 cm2/Vs anatase"),
            ("characterization", "time-resolved PL", "carrier lifetime microseconds anatase"),
            ("application", "photodetector", "anatase outperforms rutile for photodetection"),
            ("synthesis_method", "magnetron sputtering", "sputtered films both phases"),
        ]
    },
    {
        "title": "Effect of post-deposition annealing on Zn:TiO2 photodetector performance",
        "doi": "10.1016/j.apsusc.2023.156789",
        "abstract": "Annealing temperature critically affects Zn:TiO2 photodetector performance. Optimal annealing at 450°C in O2 gives best crystallinity and photoresponse. Lower temperatures retain amorphous regions reducing mobility. Higher temperatures cause Zn segregation to grain boundaries. XRD peak sharpening confirms crystallite growth from 8 nm (as-deposited) to 35 nm (450°C anneal).",
        "status": "extracted",
        "content": {
            "methods": "Annealing at 300-700°C in O2 atmosphere for 1 hour. XRD, SEM, and photoconductivity measurements.",
            "results": "Optimal anneal: 450°C. Crystallite size: 35 nm. Photocurrent: 15.2 μA. Above 600°C: Zn segregation observed by EDX, photocurrent drops to 4.1 μA.",
            "conclusion": "450°C O2 anneal is optimal. Avoid temperatures above 600°C to prevent Zn segregation."
        },
        "knowledge": [
            ("synthesis_method", "annealing", "optimal annealing 450°C in O2 for Zn:TiO2"),
            ("characterization", "XRD", "crystallite size 35 nm after 450°C anneal"),
            ("characterization", "SEM", "grain boundary Zn segregation above 600°C"),
            ("characterization", "EDX", "Zn distribution mapped by EDX"),
            ("application", "photodetector", "photocurrent 15.2 μA at optimal anneal"),
            ("material", "TiO2", "anatase crystallite growth with annealing"),
        ]
    },
    {
        "title": "Oxygen vacancy engineering in Zn-doped TiO2 for enhanced visible light photodetection",
        "doi": "10.1039/d3nr012345",
        "abstract": "Co-engineering of Zn dopants and oxygen vacancies (V_O) in TiO2 extends photoresponse to 550 nm. V_O creates sub-bandgap states at 0.7 eV below conduction band. Combined with Zn acceptors, a cascade absorption mechanism enables broadband detection from UV to green. Responsivity reaches 24 A/W at 450 nm, compared to 2.1 A/W for pure TiO2.",
        "status": "extracted",
        "content": {
            "introduction": "Extending TiO2 photoresponse beyond UV requires defect engineering. V_O and Zn co-doping creates complementary absorption channels.",
            "results": "V_O density: 3.2e18 cm-3 (optimised by reducing anneal in N2/H2). Combined Zn+V_O: responsivity 24 A/W at 450 nm. Response time: 8 ms rise, 15 ms fall.",
            "conclusion": "Zn+V_O co-engineering is the optimal strategy for broadband TiO2 photodetection. O-poor synthesis conditions naturally generate V_O alongside Zn doping."
        },
        "knowledge": [
            ("material", "TiO2", "oxygen vacancy engineering in TiO2"),
            ("material", "Zn", "Zn+V_O co-doping strategy"),
            ("characterization", "UV-Vis", "photoresponse extended to 550 nm"),
            ("application", "photodetector", "responsivity 24 A/W at 450 nm broadband"),
            ("synthesis_method", "annealing", "reducing anneal in N2/H2 for V_O generation"),
            ("computational_method", "DFT", "V_O sub-bandgap states 0.7 eV below CB"),
        ]
    },
    {
        "title": "Scalable ALD growth of Zn:TiO2 for large-area photodetector arrays",
        "doi": "10.1021/acsnano.2023.789",
        "abstract": "Atomic layer deposition (ALD) of Zn:TiO2 at 200°C enables conformal coating on complex geometries for large-area photodetector arrays. Zn incorporation controlled by Zn(Et)2 pulse ratio. Optimal 2.1 at% Zn achieved with 1:8 Zn:Ti pulse ratio. ALD films show superior uniformity (±3% over 100mm wafer) vs sputtered films (±12%). Detectivity D* = 2.4e11 Jones.",
        "status": "extracted",
        "content": {
            "methods": "ALD using TiCl4/H2O and Zn(Et)2/H2O at 200°C. 1:8 Zn:Ti pulse ratio for 2.1 at% Zn.",
            "results": "Uniformity ±3% over 100mm wafer. D* = 2.4e11 Jones. NEP = 8e-13 W/Hz0.5. Response time 3 ms.",
            "conclusion": "ALD is preferred over sputtering for large-area uniform Zn:TiO2 photodetector arrays."
        },
        "knowledge": [
            ("synthesis_method", "ALD", "ALD Zn:TiO2 at 200°C for large-area arrays"),
            ("synthesis_method", "magnetron sputtering", "sputtering inferior uniformity vs ALD"),
            ("characterization", "XRD", "ALD film crystallinity after anneal"),
            ("application", "photodetector", "D*=2.4e11 Jones large-area array"),
            ("material", "Zn", "2.1 at% Zn optimal by ALD"),
            ("material", "TiO2", "conformal ALD TiO2 on complex geometry"),
        ]
    },
    {
        "title": "Bandgap engineering in ZnxTi1-xO2: tunable absorption from UV to visible",
        "doi": "10.1016/j.jallcom.2023.170123",
        "abstract": "Systematic study of Zn content from 0-10 at% in TiO2. Bandgap decreases linearly from 3.2 eV (x=0) to 2.7 eV (x=0.05) then non-linearly above 5%. Above 5 at% Zn: phase separation into TiO2 + ZnO observed by XRD. Optimal composition for visible photodetection: x=0.03-0.05 (3-5 at%). Bowing parameter b=1.8 eV.",
        "status": "extracted",
        "content": {
            "results": "Bandgap vs Zn: 0%→3.2eV, 1%→3.1eV, 2%→3.0eV, 3%→2.95eV, 5%→2.7eV. Phase separation above 5%. Bowing parameter 1.8 eV.",
            "conclusion": "3-5 at% Zn is the solubility limit in anatase TiO2. Beyond this, ZnO secondary phase forms."
        },
        "knowledge": [
            ("material", "TiO2", "bandgap 3.2 eV pure anatase"),
            ("material", "Zn", "Zn solubility limit 5 at% in TiO2"),
            ("characterization", "UV-Vis", "bandgap 2.7 eV at 5% Zn"),
            ("characterization", "XRD", "phase separation above 5 at% Zn"),
            ("application", "photodetector", "optimal 3-5 at% for visible photodetection"),
            ("application", "bandgap engineering", "bowing parameter 1.8 eV"),
        ]
    },
    {
        "title": "Carrier transport mechanisms in Zn-doped TiO2: role of grain boundaries",
        "doi": "10.1103/PhysRevApplied.2023.034567",
        "abstract": "Impedance spectroscopy reveals two transport mechanisms in Zn:TiO2: bulk carrier transport (activation energy 0.18 eV) and grain boundary hopping (0.43 eV). Zn doping reduces grain boundary barrier height by 35%, improving inter-grain transport. Optimal grain size for photodetector: 30-50 nm. Hall mobility increases from 0.8 to 18 cm2/Vs with Zn doping.",
        "status": "extracted",
        "content": {
            "methods": "Impedance spectroscopy 1 Hz - 1 MHz. Hall effect van der Pauw geometry. TEM for grain size analysis.",
            "results": "Bulk activation energy: 0.18 eV. GB activation energy: 0.43 eV. Zn reduces GB barrier by 35%. Mobility: 18 cm2/Vs (2.5% Zn).",
            "conclusion": "Grain boundary engineering via Zn doping is key to high-mobility TiO2 photodetectors."
        },
        "knowledge": [
            ("characterization", "impedance spectroscopy", "bulk and GB transport mechanisms"),
            ("characterization", "Hall effect", "mobility 18 cm2/Vs at 2.5% Zn"),
            ("characterization", "TEM", "grain size 30-50 nm optimal"),
            ("material", "TiO2", "grain boundary barrier reduced by Zn"),
            ("application", "photodetector", "high mobility via grain boundary engineering"),
        ]
    },
    {
        "title": "Flexible Zn:TiO2 photodetectors on polyimide substrates",
        "doi": "10.1002/adma.202301234",
        "abstract": "Low-temperature ALD (180°C) Zn:TiO2 on flexible polyimide substrates demonstrates photodetector performance after 1000 bending cycles. On/off ratio maintained >10^4 after bending. Key: amorphous Zn:TiO2 at low temperature still shows sufficient photoresponse due to Zn-induced mid-gap states. No crystallization needed for flexible applications.",
        "status": "extracted",
        "content": {
            "results": "On/off ratio: 1.2e4. Bending radius: 5 mm, 1000 cycles. Responsivity: 1.8 A/W (reduced vs crystalline but sufficient).",
            "conclusion": "Amorphous Zn:TiO2 viable for flexible photodetectors. Low-temperature ALD (180°C) is key process."
        },
        "knowledge": [
            ("synthesis_method", "ALD", "low-temperature ALD 180°C for flexible substrates"),
            ("application", "photodetector", "flexible photodetector on/off ratio 1.2e4"),
            ("application", "flexible electronics", "1000 bending cycles 5mm radius"),
            ("material", "TiO2", "amorphous Zn:TiO2 for flexible applications"),
        ]
    },
    {
        "title": "Self-powered Zn:TiO2 UV photodetector with heterojunction architecture",
        "doi": "10.1016/j.nanoen.2023.108456",
        "abstract": "Zn:TiO2/ZnO heterojunction self-powered photodetector with zero-bias photocurrent. Built-in electric field at interface drives carrier separation. Responsivity 45 A/W at 340 nm with zero external bias. Response time 1.2 ms / 2.8 ms. The type-II band alignment between Zn:TiO2 and ZnO enables efficient charge extraction.",
        "status": "extracted",
        "content": {
            "results": "Zero-bias responsivity: 45 A/W at 340 nm. Type-II alignment confirmed by UPS. Response time: 1.2/2.8 ms. Detectivity: 8.4e12 Jones.",
            "conclusion": "Zn:TiO2/ZnO heterojunction is optimal architecture for self-powered UV photodetection."
        },
        "knowledge": [
            ("material", "TiO2", "Zn:TiO2 in heterojunction architecture"),
            ("material", "ZnO", "ZnO heterojunction partner type-II alignment"),
            ("characterization", "UPS", "band alignment confirmed by UPS"),
            ("application", "photodetector", "self-powered 45 A/W zero-bias"),
            ("application", "heterojunction", "type-II Zn:TiO2/ZnO interface"),
            ("synthesis_method", "ALD", "ALD deposition of heterojunction layers"),
        ]
    },
    {
        "title": "Nitrogen co-doping with Zn in TiO2 for extended visible response",
        "doi": "10.1039/d3ta056789",
        "abstract": "N and Zn co-doped TiO2 extends photoresponse to 620 nm. N introduces mid-gap states 0.5 eV above VBM; combined with Zn states at 0.3 eV, broadband absorption achieved. Optimal N:Zn ratio 1:2. Photocurrent at 550 nm increased 800% vs pure TiO2. XPS confirms N in substitutional O sites (N_O) and Zn in substitutional Ti sites.",
        "status": "extracted",
        "content": {
            "results": "N_O + Zn_Ti co-doping. Photoresponse to 620 nm. Photocurrent at 550 nm: 800% enhancement. N:Zn ratio 1:2 optimal.",
            "conclusion": "N+Zn co-doping is most effective strategy for broadband visible TiO2 photodetection beyond Zn-only doping."
        },
        "knowledge": [
            ("material", "TiO2", "N+Zn co-doped TiO2"),
            ("material", "Zn", "Zn_Ti substitutional confirmed by XPS"),
            ("material", "N", "N_O substitutional confirmed by XPS"),
            ("characterization", "XPS", "N and Zn site confirmed by XPS"),
            ("application", "photodetector", "800% photocurrent at 550 nm N+Zn co-doping"),
            ("synthesis_method", "magnetron sputtering", "reactive sputtering with N2 gas"),
        ]
    },
    {
        "title": "Thermal stability of Zn dopants in TiO2: SIMS and in-situ XRD study",
        "doi": "10.1016/j.actamat.2023.119045",
        "abstract": "SIMS depth profiling reveals Zn remains uniformly distributed in TiO2 matrix up to 500°C. Above 550°C, Zn diffuses to surface and grain boundaries. In-situ XRD shows anatase-to-rutile transition at 680°C (pure TiO2) delayed to 720°C with 2.5% Zn doping, suggesting Zn stabilises anatase phase. Activation energy for Zn diffusion: 1.4 eV.",
        "status": "extracted",
        "content": {
            "results": "Zn stable in TiO2 up to 500°C. Surface segregation above 550°C. Anatase stabilised to 720°C by Zn. Zn diffusion Ea=1.4 eV.",
            "conclusion": "Zn doping stabilises anatase phase and is thermally stable for device operating temperatures below 500°C."
        },
        "knowledge": [
            ("characterization", "SIMS", "Zn depth profile uniform below 500°C"),
            ("characterization", "XRD", "anatase stabilised to 720°C by Zn doping"),
            ("material", "TiO2", "Zn stabilises anatase phase"),
            ("material", "Zn", "thermal stability up to 500°C"),
            ("synthesis_method", "annealing", "annealing above 550°C causes Zn segregation"),
            ("application", "photodetector", "stable device operation below 500°C"),
        ]
    },
    {
        "title": "Raman and photoluminescence study of defects in Zn:TiO2 films",
        "doi": "10.1016/j.jlumin.2023.120234",
        "abstract": "Raman spectroscopy shows Eg mode of anatase TiO2 red-shifts from 144 to 138 cm-1 with 2.5% Zn, indicating lattice distortion from Zn substitution. PL emission at 420 nm (violet) attributed to Zn-related deep levels. Emission at 520 nm (green) from oxygen vacancies. Intensity ratio I(420)/I(520) tracks Zn:V_O balance and correlates with photodetector performance.",
        "status": "extracted",
        "content": {
            "results": "Raman Eg: 144→138 cm-1 (lattice distortion). PL at 420 nm: Zn deep levels. PL at 520 nm: V_O. Optimal ratio I(420)/I(520)=2.3 for best photodetector.",
            "conclusion": "Raman and PL are non-destructive probes for optimising Zn:V_O balance in TiO2 photodetectors."
        },
        "knowledge": [
            ("characterization", "Raman spectroscopy", "Eg mode red-shift confirms Zn substitution"),
            ("characterization", "PL spectroscopy", "420 nm Zn levels 520 nm V_O"),
            ("material", "TiO2", "lattice distortion from Zn substitution"),
            ("material", "Zn", "Zn deep levels PL at 420 nm"),
            ("application", "photodetector", "PL ratio tracks photodetector optimisation"),
        ]
    },
    {
        "title": "Comparison of Zn, Nb, and Al doping in TiO2 for photodetector applications",
        "doi": "10.1021/acsaelm.2023.03456",
        "abstract": "Systematic comparison: Zn (2+), Nb (5+), and Al (3+) doping in anatase TiO2. Zn gives highest photocurrent (12.4 μA) but requires charge compensation. Nb gives highest conductivity (n-type, 1e18 cm-3) but faster recombination. Al improves crystallinity but negligible effect on photoresponse. For photodetector: Zn > Nb > Al. Zn+Nb co-doping partially compensates charge mismatch while maintaining high photocurrent.",
        "status": "extracted",
        "content": {
            "results": "Photocurrent ranking: Zn(12.4μA) > Nb(8.1μA) > Al(2.3μA) > undoped(1.1μA). Zn+Nb co-doping: 15.8 μA.",
            "conclusion": "Zn is the optimal single dopant for TiO2 photodetectors. Zn+Nb co-doping further improves performance."
        },
        "knowledge": [
            ("material", "Zn", "best single dopant for TiO2 photodetector"),
            ("material", "TiO2", "anatase host for systematic doping comparison"),
            ("characterization", "Hall effect", "n-type conductivity with Nb doping"),
            ("application", "photodetector", "Zn gives highest photocurrent 12.4 μA"),
            ("synthesis_method", "magnetron sputtering", "all dopants deposited by sputtering"),
        ]
    },
    {
        "title": "Machine learning prediction of optimal Zn doping in TiO2 for optoelectronic applications",
        "doi": "10.1038/s41524-2023-01234",
        "abstract": "ML model trained on 847 DFT calculations predicts optimal Zn concentration of 2.3 at% for photodetector performance in anatase TiO2. Feature importance: formation energy (0.34), bandgap reduction (0.28), carrier lifetime (0.21), synthesis temperature (0.17). Predicted optimal synthesis: magnetron sputtering at 300°C, O2/Ar = 0.15, anneal 450°C. Experimental validation confirms 2.4 at% as optimal.",
        "status": "extracted",
        "content": {
            "results": "ML predicted optimum: 2.3 at% Zn. Experimental: 2.4 at%. Agreement within 4%. Optimal synthesis: 300°C sputtering, 450°C anneal, O2/Ar=0.15.",
            "conclusion": "ML-guided materials discovery confirms 2-3 at% Zn as universal optimum for TiO2 photodetectors across synthesis methods."
        },
        "knowledge": [
            ("computational_method", "machine learning", "ML trained on 847 DFT calculations"),
            ("material", "Zn", "optimal 2.3 at% Zn predicted by ML"),
            ("material", "TiO2", "anatase TiO2 optoelectronic optimisation"),
            ("synthesis_method", "magnetron sputtering", "optimal 300°C O2/Ar=0.15"),
            ("synthesis_method", "annealing", "optimal anneal 450°C"),
            ("application", "photodetector", "2-3 at% Zn universal optimum confirmed"),
        ]
    },
]


def inject_data():
    """Inject synthetic workflow + papers + knowledge into test DB."""
    section("2. INJECTING SYNTHETIC RESEARCH DATA")

    now = datetime.now(timezone.utc).isoformat()
    con = sqlite3.connect(TEST_DB_PATH)
    cur = con.cursor()

    # Workflow
    cur.execute("""
        INSERT INTO Workflow (name, status, current_stage, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
    """, ("TiO2 Zn Doping Photodetector Study", "paused", "S4", now, now))
    wf_id = cur.lastrowid

    # Config
    cur.execute("""
        INSERT INTO WorkflowResearchConfig
        (workflow_id, material, focus, structure, method, properties, characterization, domain)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (wf_id,
          "TiO2",
          "Zn doping photodetector optical bandgap defects",
          "thin film anatase rutile",
          "magnetron sputtering ALD annealing",
          "bandgap photocurrent responsivity detectivity",
          "XRD UV-Vis XPS Raman Hall effect",
          "optoelectronics"))

    # Stages S1-S4 completed
    for stage in ["S1", "S2", "S2_75", "S2_5", "S3", "S4"]:
        cur.execute("""
            INSERT INTO Stage (workflow_id, stage_name, status, started_at, ended_at)
            VALUES (?, ?, 'completed', ?, ?)
        """, (wf_id, stage, now, now))

    # Papers
    paper_ids = []
    for p in SYNTHETIC_PAPERS:
        cur.execute("""
            INSERT INTO Paper
            (workflow_id, title, doi, abstract, status, source, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (wf_id, p["title"], p.get("doi",""), p["abstract"],
              p["status"], "synthetic_injection", now, now))
        pid = cur.lastrowid
        paper_ids.append(pid)

        # Content sections
        for section_name, content in p["content"].items():
            cur.execute("""
                INSERT INTO PaperContent (paper_id, section_name, content)
                VALUES (?, ?, ?)
            """, (pid, section_name, content))

        # Knowledge rows
        for cat, val, ctx in p["knowledge"]:
            cur.execute("""
                INSERT INTO ResearchKnowledge (paper_id, category, value, context)
                VALUES (?, ?, ?, ?)
            """, (pid, cat, val, ctx))

    con.commit()
    con.close()

    check("Workflow injected",      True, f"workflow_id={wf_id}")
    check("Papers injected",        True, f"{len(SYNTHETIC_PAPERS)} papers")
    check("Knowledge rows injected",True, f"{sum(len(p['knowledge']) for p in SYNTHETIC_PAPERS)} rows")
    return wf_id


# ═══════════════════════════════════════════════════════════════════════════════
# 3. TOOL TESTS
# ═══════════════════════════════════════════════════════════════════════════════

async def run_tool_tests(wf_id: int):
    """Test all tool groups against injected data."""

    # Patch DB_PATH in constants before importing registry
    import brahm.shared.constants as C
    C.DB_PATH = TEST_DB_PATH

    # Also patch helpers so _repo() uses test DB
    import brahm.shared.helpers as H
    def _test_repo():
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "shani_repository",
            "/mnt/d/brahm/agents/shani/repositories/repository.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.Repository(TEST_DB_PATH)
    H._repo = _test_repo

    def _test_analyzer():
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "research_analyzer",
            "/mnt/d/brahm/agents/chitragupta/analysis/research_analyzer.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.ResearchAnalyzer(TEST_DB_PATH)
    H._analyzer = _test_analyzer

    from brahm.brahm_registry import registry
    import brahm.agents.shani
    import brahm.agents.research
    import brahm.agents.analysis
    import brahm.agents.db_tools
    import brahm.agents.vidur
    import brahm.agents.vishwakarma
    import brahm.agents.meta

    # ── Group C — Research Queries ────────────────────────────────────────────
    section("3. GROUP C — RESEARCH QUERIES")

    r = await registry.dispatch('research_get_database_stats', {})
    check("research_get_database_stats",
          r['status'] == 'success' and r['stats']['total_papers'] == len(SYNTHETIC_PAPERS),
          f"papers={r.get('stats',{}).get('total_papers')}")

    r = await registry.dispatch('research_knowledge_summary', {'workflow_id': wf_id})
    check("research_knowledge_summary",
          r['status'] == 'success' and r['total_knowledge_rows'] > 0,
          f"knowledge_rows={r.get('total_knowledge_rows')}")

    r = await registry.dispatch('research_find_papers_by_topic',
                                {'keywords': ['Zn', 'photodetector'], 'search_in': ['title','abstract']})
    check("research_find_papers_by_topic",
          r['status'] == 'success' and r['count'] > 0,
          f"found={r.get('count')} papers")

    # ── Group D — Analysis ────────────────────────────────────────────────────
    section("4. GROUP D — ANALYSIS")

    r = await registry.dispatch('analysis_technique_frequency',
                                {'category': 'characterization', 'top_n': 5, 'min_count': 1})
    check("analysis_technique_frequency",
          r['status'] == 'success' and len(r.get('results',[])) > 0,
          f"top={r['results'][0]['value'] if r.get('results') else 'none'}")

    r = await registry.dispatch('analysis_trend_report',
                                {'primary_category': 'synthesis_method',
                                 'secondary_category': 'application',
                                 'min_co_occurrence': 1})
    check("analysis_trend_report",
          r['status'] == 'success',
          f"status={r['status']}")

    r = await registry.dispatch('analysis_find_gaps',
                                {'category_a': 'synthesis_method',
                                 'category_b': 'characterization',
                                 'gap_threshold': 1})
    check("analysis_find_gaps",
          r['status'] == 'success',
          f"status={r['status']}")

    r = await registry.dispatch('analysis_parameter_distribution',
                                {'parameter_keywords': ['bandgap', 'eV'],
                                 'extract_numbers': True})
    check("analysis_parameter_distribution",
          r['status'] == 'success',
          f"status={r['status']}")

    r = await registry.dispatch('analysis_workflow_comparison',
                                {'workflow_ids': [wf_id, wf_id], 'compare_by': 'knowledge'})
    check("analysis_workflow_comparison",
          r['status'] == 'success',
          f"status={r['status']}")

    # ── Group E — DB Corrections ──────────────────────────────────────────────
    section("5. GROUP E — DB CORRECTIONS")

    r = await registry.dispatch('db_list_suspect_papers',
                                {'workflow_id': wf_id, 'limit': 5})
    check("db_list_suspect_papers",
          r['status'] == 'success',
          f"suspect={r.get('suspect_count',0)}")

    r = await registry.dispatch('db_update_paper',
                                {'paper_id': 1,
                                 'fields': {'status': 'knowledge_ready'}})
    check("db_update_paper",
          r['status'] == 'success',
          f"updated={r.get('fields_updated')}")

    r = await registry.dispatch('db_bulk_fix',
                                {'workflow_id': wf_id,
                                 'field': 'status',
                                 'match_pattern': '^extracted$',
                                 'replacement': 'extracted',
                                 'dry_run': True})
    check("db_bulk_fix (dry_run)",
          r['status'] == 'success' and r.get('dry_run') == True,
          f"would_affect={r.get('would_affect',0)}")

    # ── Group G — VIDUR ───────────────────────────────────────────────────────
    section("6. GROUP G — VIDUR")

    r = await registry.dispatch('vidur_health', {})
    check("vidur_health",
          r['status'] == 'success' and r.get('ready') == True,
          "all modules ok")

    r = await registry.dispatch('vidur_list_techniques', {})
    check("vidur_list_techniques",
          r['status'] == 'success' and r.get('count') == 4,
          f"techniques={r.get('count')}")

    # ── Group H — Vishwakarma Structure Builder ───────────────────────────────
    section("7. GROUP H — VISHWAKARMA STRUCTURE BUILDER")

    # Test structure builder directly (not yet a registered tool)
    try:
        from vishwakarma.structure_builder import (
            build_any, build_doped_structure, list_materials
        )

        mats = list_materials()
        check("structure_builder: list_materials",
              len(mats) > 5, f"{len(mats)} materials in library")

        atoms = build_any('tio2_anatase')
        check("structure_builder: build_any (library)",
              atoms is not None and len(atoms) > 0,
              f"formula={atoms.get_chemical_formula()}")

        result = build_doped_structure(
            material='tio2_anatase',
            host_element='Ti',
            dopant='Zn',
            supercell=[2,2,2],
            prefix='TiO2_Zn_test',
        )
        check("structure_builder: build_doped_structure",
              result['doping_info']['formula'] == 'O64Ti31Zn',
              f"formula={result['doping_info']['formula']}, "
              f"conc={result['doping_info']['concentration_pct']}%, "
              f"site={result['doping_info']['wyckoff']}")

        # Test MP query
        mp_key = os.environ.get('MP_API_KEY','')
        if mp_key:
            atoms_mp = build_any('SnO2')
            check("structure_builder: build_any (MP query)",
                  atoms_mp is not None,
                  f"formula={atoms_mp.get_chemical_formula()}, "
                  f"mp_id={atoms_mp.info.get('mp_id')}")
        else:
            print(f"  {SKIP}  structure_builder: build_any (MP) — no MP_API_KEY")

    except Exception as e:
        check("structure_builder", False, str(e))

    # ── Vishwakarma: generate QE input ───────────────────────────────────────
    section("8. VISHWAKARMA — QE INPUT GENERATION")

    try:
        from vishwakarma.structure_builder import build_doped_structure, to_qe_structure, build_any
        from vishwakarma import input_generator as ig

        # Build pure TiO2 for SCF test (smaller = faster)
        atoms = build_any('tio2_anatase')
        struct = to_qe_structure(
            atoms,
            prefix='tio2_test',
            pseudo_dir=PSEUDO_DIR,
            kpoints={'mode':'automatic','mesh':[4,4,4],'shift':[0,0,0]}
        )
        calc_params = {
            'ecutwfc':    40.0,
            'ecutrho':    320.0,
            'occupations':'smearing',
            'smearing':   'gaussian',
            'degauss':    0.01,
            'conv_thr':   1e-6,
            'pseudo_dir': PSEUDO_DIR,
            'outdir':     QE_WORKDIR,
            'disk_io':    'low',
        }
        input_text = ig.scf(struct, calc_params)
        check("QE input generation",
              '&CONTROL' in input_text and 'Ti' in input_text,
              f"lines={input_text.count(chr(10))}")
        print(f"\n  {INFO}  QE input preview (first 8 lines):")
        for line in input_text.split('\n')[:8]:
            print(f"         {line}")
        print()

    except Exception as e:
        check("QE input generation", False, str(e))

    # ── Vishwakarma: run actual SCF ───────────────────────────────────────────
    section("9. VISHWAKARMA — REAL QE SCF CALCULATION")
    print(f"  {INFO}  Running SCF on TiO2 primitive cell (12 atoms)...")
    print(f"  {INFO}  This takes 5-15 minutes. Please wait...\n")

    try:
        from vishwakarma import workflow as wf_mod
        from vishwakarma.structure_builder import build_any, to_qe_structure

        atoms = build_any('tio2_anatase')
        struct = to_qe_structure(
            atoms,
            prefix='tio2_scf',
            pseudo_dir=PSEUDO_DIR,
            kpoints={'mode':'automatic','mesh':[4,4,4],'shift':[0,0,0]}
        )
        calc_params = {
            'ecutwfc':       60.0,
            'ecutrho':       480.0,
            'occupations':   'smearing',
            'smearing':      'gaussian',
            'degauss':       0.01,
            'conv_thr':      1e-6,
            'mixing_beta':   0.4,
            'startingwfc':   'atomic+random',
            'diagonalization': 'david',
            'pseudo_dir':    PSEUDO_DIR,
            'outdir':        QE_WORKDIR,
            'disk_io':       'low',
            'verbosity':     'low',
        }

        t0 = time.time()
        result = wf_mod.scf_only(
            structure=struct,
            calc_params=calc_params,
            label='tio2_scf_test',
            workdir=QE_WORKDIR,
            bin_dir=QE_BIN_DIR,
            timeout=900,
            mpi_np=1,
        )
        elapsed = round(time.time() - t0, 1)
        steps = result.get('steps', [{}])
        parsed = steps[0].get('parsed', {}) if steps else {}
        converged = parsed.get('converged', False)
        energy    = parsed.get('total_energy_ry')
        status    = steps[0].get('status','?') if steps else '?'

        job_status = "completed" if (converged and energy is not None) else (steps[0].get("status","?") if steps else "?")
        check("SCF calculation completed",
              job_status == 'completed',
              f"status={job_status}, time={elapsed}s")
        check("SCF converged",
              converged,
              f"converged={converged}")
        check("SCF energy extracted",
              energy is not None,
              f"E={energy} Ry")

        if energy:
            energy_ev = float(energy) * 13.6057
            print(f"\n  {INFO}  TiO2 total energy: {energy} Ry = {energy_ev:.2f} eV")

    except Exception as e:
        check("SCF calculation", False, str(e))
        import traceback
        print(f"  {Y}  {traceback.format_exc()[-300:]}{RS}")

    # ── Meta ──────────────────────────────────────────────────────────────────
    section("10. META — BRAHM HEALTH")

    r = await registry.dispatch('brahm_health', {})
    check("brahm_health",
          r['status'] == 'success',
          f"overall={r.get('overall')}")


# ═══════════════════════════════════════════════════════════════════════════════
# 4. SYNTHESIS — Claude's reasoning over injected data
# ═══════════════════════════════════════════════════════════════════════════════

def print_synthesis():
    section("11. KNOWLEDGE SYNTHESIS SUMMARY")
    print(f"""
  Based on {len(SYNTHETIC_PAPERS)} injected papers on Zn-doped TiO2 photodetectors:

  {G}OPTIMAL STRUCTURE:{RS}
    Material:      TiO2 anatase (spacegroup I4₁/amd)
    Dopant:        Zn at Ti substitutional site (Wyckoff 4a)
    Concentration: 2-3 at% Zn (solubility limit ~5 at%)
    Supercell:     2×2×2 (96 atoms, ~3.1% Zn = 1 Zn per 32 Ti)

  {G}OPTIMAL SYNTHESIS:{RS}
    Method:        Magnetron sputtering (300°C) OR ALD (200°C for flexible)
    Atmosphere:    O2/Ar = 0.15
    Anneal:        450°C in O2 for 1 hour
    Avoid:         >550°C (Zn segregation), >5 at% Zn (phase separation)

  {G}EXPECTED PERFORMANCE:{RS}
    Responsivity:  8-45 A/W (45 A/W with heterojunction)
    Detectivity:   ~2.4×10¹¹ Jones (ALD), ~8.4×10¹² Jones (heterojunction)
    Bandgap:       2.95-3.0 eV at 2.5% Zn (vs 3.2 eV pure)
    Mobility:      18 cm²/Vs (vs 0.8 cm²/Vs undoped)

  {G}DFT TARGETS (for Vishwakarma):{RS}
    1. Relax TiO2 anatase primitive cell → verify a=3.785Å, c=9.512Å
    2. Relax Zn:TiO2 2×2×2 supercell → formation energy, structural distortion
    3. DOS → confirm Zn acceptor level at VBM+0.3 eV
    4. Compare with literature: formation energy 0.9-1.8 eV (O-rich/poor)
""")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

async def main():
    print(f"\n{W}{'█'*60}{RS}")
    print(f"{W}  BRAHM FULL PIPELINE INJECTION TEST{RS}")
    print(f"{W}  TiO2:Zn Photodetector — v2.0{RS}")
    print(f"{W}{'█'*60}{RS}")
    print(f"  Test DB:  {TEST_DB_PATH}")
    print(f"  QE jobs:  {QE_WORKDIR}")
    print(f"  Started:  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # 1. Create DB
    create_test_db()

    # 2. Inject data
    wf_id = inject_data()

    # 3. Run tool tests
    await run_tool_tests(wf_id)

    # 4. Print synthesis
    print_synthesis()

    # ── Final score ───────────────────────────────────────────────────────────
    section("FINAL RESULTS")
    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    total  = len(results)
    pct    = round(passed/total*100) if total else 0

    print(f"\n  Total:  {total}")
    print(f"  {G}Passed: {passed}{RS}")
    if failed:
        print(f"  {R}Failed: {failed}{RS}")
        print(f"\n  {R}Failed tests:{RS}")
        for name, ok, detail in results:
            if not ok:
                print(f"    ✗ {name}: {detail}")
    print(f"\n  Score:  {G if pct>=80 else R}{pct}%{RS}")
    print(f"\n  Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


if __name__ == "__main__":
    asyncio.run(main())
