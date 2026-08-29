"""
Round 2 prototype demonstration script for TriageCopilot.

Run with:  python3 simulate.py

Produces ../output/simulation_output.json, which docs/dashboard.html reads
to render the demo. This script is the "working prototype" - it exercises
every behavior called for in the Round 2 minimum prototype expectations:

  - scores >=15-20 simulated patient records                -> Section 1
  - includes an ambiguous, a pediatric/geriatric, and a
    zero-history patient                                    -> patients.json
  - shows behavior under a simulated 3x surge                -> Section 2
  - every score carries an explicit confidence indicator     -> engine.py
  - captures at least one clinician override and what
    gets logged                                              -> Section 3
  - demonstrates waiting-room re-assessment triggers          -> Section 4
"""

import json
import random
import datetime
import copy
from pathlib import Path

from engine import score_patient, recheck_due, SAFE_WAIT_MINUTES

random.seed(7)  # deterministic demo output

BASE_DIR = Path(__file__).resolve().parent
DATA_PATH = BASE_DIR / "data" / "patients.json"
OUTPUT_PATH = BASE_DIR.parent / "output" / "simulation_output.json"

audit_log = []


def log_event(event_type: str, **fields):
    audit_log.append({
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "event_type": event_type,
        **fields,
    })


def load_patients():
    with open(DATA_PATH) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Section 1: baseline scoring pass over the full patient set
# ---------------------------------------------------------------------------

def run_baseline(patients):
    results = []
    for p in patients:
        r = score_patient(p)
        log_event(
            "intake_score",
            patient_id=p["patient_id"],
            ai_suggested_esi=r.esi,
            confidence=round(r.confidence, 2),
            autonomous_escalation=r.autonomous_escalation,
            low_confidence_escalated=r.low_confidence_escalated,
            reasons=r.reasons,
        )
        results.append({"patient": p, "result": r.to_dict()})
    return results


# ---------------------------------------------------------------------------
# Section 2: simulated 3x surge
#
# Takes the same illustrative patient set and triples arrival volume by
# replaying it three times with staggered synthetic arrival offsets, then
# reports how many patients would already be past their safe-wait threshold
# for their acuity level by the time they'd realistically be re-checked
# under 3x load. This demonstrates the system keeps flagging risk even when
# a human couldn't manually re-triage everyone in time.
# ---------------------------------------------------------------------------

def run_surge(patients, multiplier=3):
    surge_patients = []
    for i in range(multiplier):
        for p in patients:
            clone = copy.deepcopy(p)
            clone["patient_id"] = f"{p['patient_id']}-surge{i+1}"
            surge_patients.append(clone)

    # Under 3x volume, assume average time-to-first-nurse-look degrades
    # proportionally (illustrative, not a queueing-theory model).
    avg_minutes_between_checks = 4 * multiplier

    breaches = 0
    surge_results = []
    for idx, p in enumerate(surge_patients):
        r = score_patient(p)
        simulated_wait = (idx % 12) * avg_minutes_between_checks  # spreads load across the queue
        breached = recheck_due(r.esi, simulated_wait)
        if breached:
            breaches += 1
        surge_results.append({
            "patient_id": p["patient_id"],
            "esi": r.esi,
            "confidence": round(r.confidence, 2),
            "simulated_wait_minutes": simulated_wait,
            "safe_wait_minutes": SAFE_WAIT_MINUTES.get(r.esi, 30),
            "recheck_breached": breached,
        })

    log_event(
        "surge_simulation",
        multiplier=multiplier,
        total_patients=len(surge_patients),
        recheck_breaches=breaches,
        note="Patients whose simulated wait already exceeds their safe-wait threshold under 3x load",
    )
    return {
        "multiplier": multiplier,
        "total_patients": len(surge_patients),
        "recheck_breaches": breaches,
        "sample": surge_results[:12],  # keep the JSON small for the dashboard
    }


# ---------------------------------------------------------------------------
# Section 3: deterioration re-check
#
# Patient P006 (initially low acuity, complete data) is revisited 100
# minutes later with vitals that have quietly worsened while waiting -
# exactly the "looked fine at check-in, worse two hours later" scenario
# from the Round 1 pitch. The engine must re-score and flag the change.
# ---------------------------------------------------------------------------

def run_deterioration_check(patients):
    target = next(p for p in patients if p["patient_id"] == "P006")
    before = score_patient(target)

    worsened = copy.deepcopy(target)
    worsened["vitals"]["hr"] = 128
    worsened["vitals"]["rr"] = 27
    worsened["vitals"]["sbp"] = 92
    worsened["vitals"]["spo2"] = 91
    worsened["chief_complaint"] = "laceration on forearm, now reports feeling faint"

    after = score_patient(worsened)

    log_event(
        "waiting_room_recheck",
        patient_id=target["patient_id"],
        minutes_waited=100,
        esi_before=before.esi,
        esi_after=after.esi,
        confidence_after=round(after.confidence, 2),
        trigger="Vitals re-recorded as worsening during a scheduled waiting-room recheck",
        reasons_after=after.reasons,
    )

    return {
        "patient_id": target["patient_id"],
        "minutes_waited": 100,
        "before": before.to_dict(),
        "after": after.to_dict(),
    }


# ---------------------------------------------------------------------------
# Section 4: clinician override
#
# Demonstrates the "nurse signs off, no exceptions" design: the AI suggests
# one score, the nurse overrides it with a documented reason, and the audit
# log captures both the AI's original suggestion and the human's final call
# side by side - the exact record TriageCopilot promised in the Round 1 deck.
# ---------------------------------------------------------------------------

def run_override_demo(patients):
    target = next(p for p in patients if p["patient_id"] == "P011")  # the ambiguous case
    ai_result = score_patient(target)

    override = {
        "patient_id": target["patient_id"],
        "ai_suggested_esi": ai_result.esi,
        "ai_confidence": round(ai_result.confidence, 2),
        "nurse_final_esi": ai_result.esi - 1,  # nurse escalates further based on in-person assessment
        "nurse_reason": (
            "Patient looks more unwell in person than the vitals alone suggest - "
            "pale, diaphoretic on exam. Escalating one level."
        ),
        "overridden": True,
    }

    log_event(
        "clinician_override",
        patient_id=target["patient_id"],
        ai_suggested_esi=override["ai_suggested_esi"],
        ai_confidence=override["ai_confidence"],
        nurse_final_esi=override["nurse_final_esi"],
        nurse_reason=override["nurse_reason"],
    )

    return override


def main():
    patients = load_patients()
    assert len(patients) >= 15, "Round 2 requires at least 15-20 simulated patient records"

    baseline = run_baseline(patients)
    surge = run_surge(patients, multiplier=3)
    deterioration = run_deterioration_check(patients)
    override = run_override_demo(patients)

    output = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "patient_count": len(patients),
        "baseline_results": baseline,
        "surge_simulation": surge,
        "deterioration_recheck": deterioration,
        "clinician_override": override,
        "audit_log": audit_log,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Scored {len(patients)} patients.")
    esi_counts = {}
    for row in baseline:
        esi = row["result"]["esi"]
        esi_counts[esi] = esi_counts.get(esi, 0) + 1
    print("ESI distribution:", dict(sorted(esi_counts.items())))
    print(f"Red-flag autonomous escalations: {sum(1 for r in baseline if r['result']['autonomous_escalation'])}")
    print(f"Low-confidence fail-safe escalations: {sum(1 for r in baseline if r['result']['low_confidence_escalated'])}")
    print(f"Surge: {surge['total_patients']} patients, {surge['recheck_breaches']} recheck-threshold breaches")
    print(f"Deterioration recheck: P006 ESI {deterioration['before']['esi']} -> {deterioration['after']['esi']}")
    print(f"Override: AI suggested ESI {override['ai_suggested_esi']} -> nurse set ESI {override['nurse_final_esi']}")
    print(f"\nWrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
    
