"""
TriageCopilot scoring engine.

This is the core mechanism referenced in the Round 1 pitch: a decision-support
layer that SUGGESTS an ESI-style acuity score (1 = most urgent, 5 = least
urgent) with a plain-language reason and an explicit confidence value. It
never returns a bare number - every score carries a reason and a confidence,
and low confidence pushes the score toward MORE urgent, never less, per the
"design for the worst case" principle from Round 1.

No third-party dependencies - pure standard library, so it runs anywhere
Python 3.9+ runs (including inside GitHub Actions / GitHub Pages CI if you
want to wire up a badge later).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import math

# ---------------------------------------------------------------------------
# 1. Age-banded vital sign norms
#
# Real pediatric norms vary continuously by age (a 2-year-old and an
# 11-year-old are not comparable) - this prototype uses three illustrative
# bands to demonstrate the MECHANISM (age-aware scoring instead of one
# adult-calibrated model for everyone). A production build would use
# continuous, age-in-months lookup tables (e.g. PALS reference ranges).
# ---------------------------------------------------------------------------

VITAL_NORMS = {
    "pediatric": {  # illustrative, roughly ages 1-11
        "hr": (75, 140),
        "rr": (18, 34),
        "sbp": (80, 110),
        "spo2": (95, 100),
        "temp_fever_c": 38.5,  # fever is expected/common; not urgent alone
    },
    "adult": {  # ages 12-64
        "hr": (60, 100),
        "rr": (12, 20),
        "sbp": (100, 140),
        "spo2": (94, 100),
        "temp_fever_c": 38.0,
    },
    "geriatric": {  # ages 65+
        # Blunted febrile response and lower physiologic reserve are real
        # geriatric-triage nuances: a "mild" fever or a "normal" heart rate
        # can already represent serious pathology in this band.
        "hr": (50, 95),
        "rr": (12, 22),
        "sbp": (100, 150),
        "spo2": (92, 100),
        "temp_fever_c": 37.8,
    },
}


def age_band(age: int) -> str:
    if age < 12:
        return "pediatric"
    if age >= 65:
        return "geriatric"
    return "adult"


# ---------------------------------------------------------------------------
# 2. Red-flag patterns -> autonomous escalation
#
# This is the ONE thing the system is trusted to act on without a human
# first (per the Round 1 "what it decides vs. recommends" design). It does
# not treat, diagnose, or set final acuity - it pushes a notification to a
# clinician immediately. Everything else is a suggestion the nurse signs off.
# ---------------------------------------------------------------------------

RED_FLAG_RULES = [
    {
        "name": "possible_stroke",
        "match": lambda p: any(
            k in p["chief_complaint"].lower()
            for k in ["facial droop", "slurred speech", "one-sided weakness", "sudden vision loss"]
        ),
        "reason": "Stroke-pattern symptoms reported",
    },
    {
        "name": "possible_mi",
        "match": lambda p: (
            "chest pain" in p["chief_complaint"].lower()
            and p["age"] >= 40
            and ("diaphoresis" in p["chief_complaint"].lower() or "sweating" in p["chief_complaint"].lower())
        ),
        "reason": "Chest pain with diaphoresis in a patient 40+",
    },
    {
        "name": "possible_sepsis",
        "match": lambda p: (
            p.get("vitals", {}).get("temp_c") is not None
            and p.get("vitals", {}).get("hr") is not None
            and p.get("vitals", {}).get("sbp") is not None
            and p["vitals"]["temp_c"] >= 38.5
            and p["vitals"]["hr"] >= 120
            and p["vitals"]["sbp"] <= 90
        ),
        "reason": "Fever + tachycardia + hypotension: sepsis pattern",
    },
]


# ---------------------------------------------------------------------------
# 3. Chief complaint severity weighting (illustrative, small lexicon)
# ---------------------------------------------------------------------------

COMPLAINT_WEIGHTS = {
    "chest pain": 0.85,
    "shortness of breath": 0.8,
    "severe abdominal pain": 0.7,
    "high fever": 0.55,
    "fever": 0.4,
    "laceration": 0.35,
    "ankle sprain": 0.15,
    "headache": 0.35,
    "dizziness": 0.45,
    "fall": 0.5,
    "rash": 0.2,
    "allergic reaction": 0.55,
    "cough": 0.25,
    "vomiting": 0.4,
    "confusion": 0.65,
}

AMBIGUOUS_MARKERS = [
    "not feeling well", "generalized weakness", "just feels off",
    "hard to describe", "vague", "tired all the time",
]


@dataclass
class TriageResult:
    patient_id: str
    esi: int
    confidence: float
    reasons: list = field(default_factory=list)
    autonomous_escalation: bool = False
    low_confidence_escalated: bool = False
    missing_vitals: list = field(default_factory=list)

    def to_dict(self):
        return {
            "patient_id": self.patient_id,
            "esi": self.esi,
            "confidence": round(self.confidence, 2),
            "reasons": self.reasons,
            "autonomous_escalation": self.autonomous_escalation,
            "low_confidence_escalated": self.low_confidence_escalated,
            "missing_vitals": self.missing_vitals,
        }


def _deviation(value: Optional[float], low: float, high: float) -> float:
    """0.0 = within normal range, up to ~1.0+ = far outside it."""
    if value is None:
        return None
    if low <= value <= high:
        return 0.0
    span = (high - low) or 1
    if value < low:
        return min(1.5, (low - value) / span)
    return min(1.5, (value - high) / span)


def score_patient(patient: dict) -> TriageResult:
    """
    Suggests an ESI-style acuity score (1=most urgent .. 5=least urgent)
    with a confidence value and plain-language reasons. This function
    NEVER returns a score without a confidence - that is a hard rule of
    the design, enforced structurally by TriageResult requiring both.
    """
    pid = patient["patient_id"]
    reasons = []

    # --- Step 1: red flags always checked first, independent of confidence ---
    for rule in RED_FLAG_RULES:
        if rule["match"](patient):
            return TriageResult(
                patient_id=pid,
                esi=1,
                confidence=0.95,
                reasons=[f"RED FLAG: {rule['reason']}"],
                autonomous_escalation=True,
            )

    band = age_band(patient["age"])
    norms = VITAL_NORMS[band]
    vitals = patient.get("vitals", {})

    core_vitals = ["hr", "rr", "sbp", "spo2", "temp_c"]
    missing = [v for v in core_vitals if vitals.get(v) is None]

    deviations = []
    d = _deviation(vitals.get("hr"), *norms["hr"])
    if d is not None:
        deviations.append(d)
        if d > 0.3:
            reasons.append(f"Heart rate outside {band} normal range")
    d = _deviation(vitals.get("rr"), *norms["rr"])
    if d is not None:
        deviations.append(d)
        if d > 0.3:
            reasons.append(f"Respiratory rate outside {band} normal range")
    d = _deviation(vitals.get("sbp"), *norms["sbp"])
    if d is not None:
        deviations.append(d)
        if d > 0.3:
            reasons.append(f"Blood pressure outside {band} normal range")
    d = _deviation(vitals.get("spo2"), *norms["spo2"])
    if d is not None:
        deviations.append(d * 1.4)  # SpO2 deviations weighted heavier
        if d > 0.15:
            reasons.append("Oxygen saturation below expected range")

    temp = vitals.get("temp_c")
    if temp is not None:
        fever_threshold = norms["temp_fever_c"]
        if temp >= fever_threshold:
            severity = min(1.0, (temp - fever_threshold) / 2.0 + 0.3)
            deviations.append(severity)
            if band == "geriatric":
                reasons.append(
                    f"Fever ({temp}C) - clinically significant for a geriatric patient "
                    f"at a lower threshold than adult norms"
                )
            else:
                reasons.append(f"Fever recorded at {temp}C")

    vitals_score = sum(deviations) / max(len(deviations), 1) if deviations else 0.0

    # --- chief complaint severity ---
    complaint = patient["chief_complaint"].lower()
    complaint_weight = 0.2  # default for anything unmatched
    for key, weight in COMPLAINT_WEIGHTS.items():
        if key in complaint:
            complaint_weight = max(complaint_weight, weight)

    is_ambiguous = any(marker in complaint for marker in AMBIGUOUS_MARKERS)
    if is_ambiguous:
        reasons.append("Presentation is vague / hard to characterize from the complaint alone")

    raw_score = 0.6 * vitals_score + 0.4 * complaint_weight

    # --- map raw_score [0..~1.5] to ESI 2-5 (ESI 1 reserved for red flags) ---
    if raw_score >= 0.75:
        esi = 2
    elif raw_score >= 0.5:
        esi = 3
    elif raw_score >= 0.3:
        esi = 4
    else:
        esi = 5

    # A single badly abnormal vital should not get diluted away by averaging
    # with several normal ones - a patient with one dangerous number is not
    # "mostly fine." This floor makes that explicit and auditable.
    max_deviation = max(deviations) if deviations else 0.0
    if max_deviation >= 0.85:
        esi = min(esi, 2)
        reasons.append("A single vital is severely abnormal - acuity floored regardless of the overall average")
    elif max_deviation >= 0.55:
        esi = min(esi, 3)

    if not reasons:
        reasons.append(f"Vitals and complaint ('{patient['chief_complaint']}') within expected range for {band} patient")

    # --- confidence: penalized by missing data, no history, ambiguity ---
    confidence = 1.0
    confidence -= 0.12 * len(missing)
    if not patient.get("has_ehr_history", False):
        confidence -= 0.15
    if is_ambiguous:
        confidence -= 0.5
    confidence = max(0.05, min(1.0, confidence))

    # --- fail-safe: low confidence escalates toward more urgent, never less ---
    low_confidence_escalated = False
    if confidence < 0.55 and esi > 2:
        esi -= 1
        low_confidence_escalated = True
        reasons.append(
            f"Confidence is low ({round(confidence, 2)}) - defaulting to the more "
            f"cautious, higher-acuity read rather than assuming average risk"
        )

    if missing:
        reasons.append(f"Missing at intake: {', '.join(missing)}")

    return TriageResult(
        patient_id=pid,
        esi=esi,
        confidence=confidence,
        reasons=reasons,
        low_confidence_escalated=low_confidence_escalated,
        missing_vitals=missing,
    )


# ---------------------------------------------------------------------------
# 4. Continuous re-scoring of the waiting queue
#
# Safe-wait thresholds are illustrative, aligned to the Round 2 brief's
# instruction to "trigger re-assessment if wait time exceeds safe thresholds
# for their severity level or if vitals are re-recorded as worsening."
# ---------------------------------------------------------------------------

SAFE_WAIT_MINUTES = {
    1: 0,     # immediate
    2: 10,
    3: 30,
    4: 60,
    5: 120,
}


def recheck_due(esi: int, minutes_waited: int) -> bool:
    return minutes_waited >= SAFE_WAIT_MINUTES.get(esi, 30)
