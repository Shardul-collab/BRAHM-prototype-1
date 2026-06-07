a"""
CHITRAGUPTA - FULL SYSTEM TEST SCRIPT (V1)
Covers: STT → NLP → JSON → Confirmation → Storage
"""

import json
from datetime import datetime
from typing import Dict, Any


# -------------------------------
# 1. STT LAYER (Whisper Simulation)
# -------------------------------
def test_stt() -> str:
    print("\n[1] STT TEST STARTED")

    # Simulated voice input
    simulated_audio_input = (
        "Today was a good day. I felt productive and worked on my project. "
        "Energy was decent but got a bit tired in evening."
    )

    transcript = simulated_audio_input  # replace with Whisper later

    print("[STT OUTPUT]:", transcript)
    return transcript


# -------------------------------
# 2. NLP LAYER (DistilBERT Simulation)
# -------------------------------
def test_nlp(transcript: str) -> Dict[str, Any]:
    print("\n[2] NLP TEST STARTED")

    # Simulated structured extraction
    extracted_data = {
        "mood": 8,
        "energy": 7,
        "productivity": 9,
        "positive_keywords": ["productive", "good day"],
        "negative_keywords": ["tired"],
        "activities": ["project work"]
    }

    print("[NLP OUTPUT]:", extracted_data)
    return extracted_data


# -------------------------------
# 3. JSON GENERATION
# -------------------------------
def test_json_generation(extracted: Dict[str, Any]) -> Dict[str, Any]:
    print("\n[3] JSON GENERATION STARTED")

    structured_entry = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "mood": extracted["mood"],
        "energy": extracted["energy"],
        "productivity": extracted["productivity"],
        "positive_keywords": extracted["positive_keywords"],
        "negative_keywords": extracted["negative_keywords"],
        "activities": extracted["activities"],
        "notes": "Auto-generated entry (test mode)"
    }

    print("[JSON OUTPUT]:")
    print(json.dumps(structured_entry, indent=2))

    return structured_entry


# -------------------------------
# 4. CONFIRMATION SYSTEM
# -------------------------------
def test_confirmation(data: Dict[str, Any]) -> bool:
    print("\n[4] CONFIRMATION STEP")

    # Simulated user voice response
    simulated_response = "yes"  # change to "edit" to test branch

    print("[USER RESPONSE]:", simulated_response)

    if simulated_response.lower() == "yes":
        print("[STATUS]: CONFIRMED ✅")
        return True

    elif simulated_response.lower() == "edit":
        print("[STATUS]: EDIT MODE TRIGGERED ⚠️")
        return False

    else:
        print("[STATUS]: INVALID RESPONSE ❌")
        return False


# -------------------------------
# 5. STORAGE LAYER (Notion Simulation)
# -------------------------------
def test_storage(data: Dict[str, Any]) -> bool:
    print("\n[5] STORAGE TEST STARTED")

    # Simulated API call
    print("[ACTION]: Sending data to Notion API...")
    print("[DATA SENT]:", json.dumps(data, indent=2))

    # simulate success
    success = True

    if success:
        print("[STATUS]: STORED SUCCESSFULLY ✅")
        return True
    else:
        print("[STATUS]: STORAGE FAILED ❌")
        return False


# -------------------------------
# MASTER PIPELINE EXECUTION
# -------------------------------
def run_full_system_test():
    print("\n===== CHITRAGUPTA SYSTEM TEST START =====")

    # Step 1: STT
    transcript = test_stt()

    # Step 2: NLP
    extracted = test_nlp(transcript)

    # Step 3: JSON
    structured_data = test_json_generation(extracted)

    # Step 4: Confirmation
    confirmed = test_confirmation(structured_data)

    if not confirmed:
        print("\n[PIPELINE STOPPED]: Awaiting user edits")
        return

    # Step 5: Storage
    stored = test_storage(structured_data)

    if stored:
        print("\n===== SYSTEM TEST COMPLETED SUCCESSFULLY =====")
    else:
        print("\n===== SYSTEM TEST FAILED AT STORAGE =====")


# -------------------------------
# ENTRY POINT
# -------------------------------
if __name__ == "__main__":
    run_full_system_test()
