"""
CHITRAGUPTA - STRESS TEST SUITE
Goal: Break the system under extreme conditions
"""

import json
import random
import string
import time
from datetime import datetime


# -------------------------------
# RANDOM TEXT GENERATOR
# -------------------------------
def generate_random_text(length=500):
    words = [
        "productive", "tired", "happy", "sad", "focused",
        "lazy", "gym", "study", "coding", "nothing"
    ]
    return " ".join(random.choices(words, k=length))


# -------------------------------
# EXTREME INPUT TEST
# -------------------------------
def test_extreme_input():
    print("\n[TEST] EXTREME INPUT SIZE")

    large_text = generate_random_text(5000)  # very large input
    print("Input length:", len(large_text))

    return large_text


# -------------------------------
# EDGE CASE TEST
# -------------------------------
def test_edge_cases():
    print("\n[TEST] EDGE CASES")

    cases = [
        "",  # empty
        "....",  # meaningless
        "1234567890",  # numeric only
        "I am dying happy sad confused productive tired all at once",  # conflicting
        "🔥🔥🔥🔥🔥",  # emojis
    ]

    return cases


# -------------------------------
# RAPID FIRE TEST (LOAD TEST)
# -------------------------------
def test_rapid_fire(iterations=100):
    print("\n[TEST] RAPID FIRE")

    start = time.time()

    for i in range(iterations):
        text = generate_random_text(50)

        data = {
            "date": datetime.now().isoformat(),
            "text": text
        }

        _ = json.dumps(data)

    end = time.time()
    print(f"Processed {iterations} entries in {end - start:.2f}s")


# -------------------------------
# DATA CORRUPTION TEST
# -------------------------------
def test_corrupted_data():
    print("\n[TEST] CORRUPTED DATA")

    corrupted = [
        None,
        {"mood": "high"},  # wrong type
        {"energy": -100},  # invalid range
        {"productivity": 9999},  # overflow
    ]

    return corrupted


# -------------------------------
# JSON STABILITY TEST
# -------------------------------
def test_json_stability():
    print("\n[TEST] JSON STABILITY")

    try:
        bad_data = {"date": datetime.now(), "data": set([1, 2, 3])}
        json.dumps(bad_data)
    except Exception as e:
        print("Caught JSON failure:", e)


# -------------------------------
# MEMORY STRESS TEST
# -------------------------------
def test_memory_stress():
    print("\n[TEST] MEMORY STRESS")

    big_list = []

    try:
        for _ in range(100000):
            big_list.append(generate_random_text(100))
        print("Memory test passed")
    except MemoryError:
        print("Memory overflow detected ❌")


# -------------------------------
# MASTER STRESS RUNNER
# -------------------------------
def run_stress_tests():
    print("\n===== STRESS TEST START =====")

    # 1. Extreme input
    extreme = test_extreme_input()

    # 2. Edge cases
    edge_cases = test_edge_cases()
    for case in edge_cases:
        print("Edge case:", case)

    # 3. Rapid fire
    test_rapid_fire(200)

    # 4. Corrupted data
    corrupted = test_corrupted_data()
    for c in corrupted:
        print("Corrupted:", c)

    # 5. JSON failure
    test_json_stability()

    # 6. Memory stress
    test_memory_stress()

    print("\n===== STRESS TEST COMPLETE =====")


# -------------------------------
# ENTRY
# -------------------------------
if __name__ == "__main__":
    run_stress_tests()
