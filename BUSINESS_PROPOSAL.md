# TriageCopilot — Detailed Business Proposal
### Round 2 · Problem Track 2: PatientTriage.ai · Accenture Innovation Challenge 2026

---

## 1. Problem framing

When a patient walks into an emergency department, one nurse has roughly two minutes to
decide how urgently they need to be seen — based on a chief complaint, a couple of vitals,
and instinct built over years on the floor. That holds up on a quiet Tuesday. It doesn't
hold up during a surge: busy EDs report **under-triage rates of 10 to 20 percent**, and
patients who deteriorate while waiting make up a disproportionate share of preventable harm.

Three things break down under pressure, and none of them are about individual skill:

- **Working memory.** Tracking 30+ patients' shifting risk in your head at once means
  something eventually gets missed — not from carelessness, from sheer volume.
- **Frozen scores.** A triage score given at check-in is rarely revisited. A patient who
  looked stable and is quietly worse 90 minutes later is usually still sitting in the same
  spot in the queue.
- **Thin first-contact data.** A complaint, a BP reading, maybe an old EHR note if the
  system cooperates. Everything past that is a judgment call made under time pressure.

The patients most likely to slip through this gap aren't the ones who look sick — they're
the early sepsis, the silent MI, the stroke that hasn't fully declared itself yet, waiting
in the same room as everyone else.

No two EDs look alike — patient mix, staffing, and existing systems vary enormously — so any
solution has to be a layer that adapts to local workflow, not a rip-and-replace system.

## 2. Solution design

**TriageCopilot never gets the final say.** At check-in it takes in the chief complaint,
available vitals, and EHR history if present, and suggests an ESI-style score (1–5) with a
short, specific reason — "ESI-2: chest pain, age 58, sweating" — not a bare number. The nurse
looks at it, agrees or overrides, and moves on. Every override is logged, partly for
accountability and partly because that override data is exactly what would be used to
improve the model over time.

**What it decides vs. what it recommends** is the central design choice, and it's
deliberately narrow:

- **Recommends:** the acuity score and its reasoning, at intake and on every recheck. Signed
  off by a nurse, no exceptions.
- **Decides on its own:** exactly one thing — pushing a hard red-flag pattern (stroke, MI,
  sepsis) straight to a clinician immediately, and continuously re-scoring everyone still in
  the waiting room so no one's risk quietly goes stale.

**Built assuming things go wrong, not that they go right:**
- Data missing or unclear → defaults to the more cautious, higher-acuity read, never the
  optimistic one. Confidence is a first-class, always-present value, not an afterthought.
- Network or EHR drops → falls back to a plain checklist every nurse already knows, instead
  of failing silently or blocking intake.
- Vitals age-adjusted → a heart rate or fever that's unremarkable in a healthy adult can be
  either meaningless or seriously significant in a young child or an older adult with a
  blunted febrile response. Treating every patient against one adult-calibrated model is a
  silent safety risk the prototype specifically avoids.

**Architecture (illustrative, adapts to local systems):**

```
 Intake kiosk / nurse workstation
          │
          ▼
   Data collection layer  ──► EHR (if available) / vitals monitor / self-reported symptoms
          │
          ▼
   TriageCopilot scoring engine
     ├─ Red-flag pattern check ─────► autonomous clinician alert (bypasses nurse queue)
     ├─ Age-banded vitals scoring
     ├─ Complaint severity weighting
     └─ Confidence calculation ─────► fail-safe escalation if confidence is low
          │
          ▼
   Nurse review UI  (accept / override, reason required on override)
          │
          ▼
   Waiting-room monitor  ──► recheck triggers on wait-time or vitals update
          │
          ▼
   Audit log (every suggestion, override, and recheck, immutably timestamped)
```

## 3. Target users

- **Primary: triage nurses.** The tool has to make their two-minute decision faster and
  better-supported without adding steps — if it slows them down or feels like another system
  to fight, it gets worked around, not used.
- **Secondary: ED charge nurses / attending physicians.** Consumers of the continuously
  re-scored waiting-room view and the red-flag alert stream.
- **Tertiary: hospital quality & compliance teams.** Consumers of the audit trail — every
  override with its reason is exactly the record needed for both clinical quality review and
  regulatory audit.
- **Indirect beneficiary: the patient**, specifically the one who looks fine at check-in and
  isn't fine ninety minutes later.

## 4. Business case & impact

- **Reduced preventable harm.** Even a modest reduction in the 10–20% under-triage rate,
  concentrated on the patients whose risk changes after check-in (the group static,
  one-time triage structurally cannot catch), is a direct patient-safety and liability
  benefit.
- **Reduced average wait-to-appropriate-care time**, not by seeing patients faster overall,
  but by re-ranking the queue so the right patient gets seen sooner without adding staff.
- **Lower liability exposure.** A fully logged, always-overridable, always-explained
  recommendation is a materially different liability position than an unaided human call
  under time pressure with no contemporaneous record of reasoning.
- **Deployable without new headcount.** The tool sits alongside existing nurse workflow; it
  doesn't require a new clinical role to operate.
- **Adoption is the real cost driver, not infrastructure.** Because the engine here is pure
  logic (no proprietary model dependency, no GPU inference cost at this stage), the marginal
  cost per hospital is dominated by integration and change management, not compute.

## 5. Phased roadmap

**Phase 1 — Pilot (this prototype's scope, 0–3 months):**
Rules-based / hybrid scoring engine as demonstrated here, deployed in shadow mode (scores
generated but not shown to nurses) at a single ED to validate the model against real nurse
decisions before it ever influences a live queue.

**Phase 2 — Assisted triage (3–9 months):**
Live recommendations shown to nurses at check-in, override logging active, red-flag
autonomous alerts enabled. Round 1's ideas 5 and 6 — a pre-arrival self-triage kiosk and a
standing waiting-room deterioration watch — become natural extensions here, feeding richer
data into the same engine rather than requiring a new system.

**Phase 3 — Continuous re-scoring at scale (9–18 months):**
Full waiting-room monitoring across shifts, integrated with bed management and staffing data
so re-ranking accounts for real-time capacity, not just acuity in isolation.

**Phase 4 — Model refinement from override data (ongoing):**
The logged override reasons become a structured, clinically-reviewed training signal —
closing the loop from "the nurse disagreed and said why" to a better next version of the
scoring logic, always under clinical governance, never as a silent auto-update.

## 6. Key risks & mitigations

| Risk | Mitigation |
|---|---|
| **Automation bias** — nurses start rubber-stamping AI suggestions instead of exercising judgment. | Override is required to be an active choice with a reason field, not a passive default; override rates are monitored per-user as a training signal, not a compliance stick. |
| **Alert fatigue** from red-flag autonomous escalation firing too often. | Red-flag rules are deliberately narrow (three patterns in this prototype) and tuned conservatively during the Phase 1 shadow-mode pilot before any alert reaches a live clinician. |
| **Age-banding is too coarse** (see README's adolescent-band gap). | Explicitly documented as a known limitation; Phase 1 shadow-mode validation is designed to surface exactly this kind of miscalibration before go-live. |
| **PHI exposure / regulatory non-compliance.** | Jurisdiction assumed as US/HIPAA (stated explicitly, see README); no PHI leaves the scoring layer for retraining without separate de-identification and consent; every access is part of the immutable audit log required for §164.312(b). |
| **Vendor / integration risk** — hospitals differ hugely in EHR maturity. | The engine works with partial or missing data by design (confidence scoring exists specifically for this); it degrades gracefully rather than requiring a specific EHR integration to function at all. |
| **Liability if the AI's suggestion is later shown to be wrong.** | The AI's suggestion and the nurse's final decision are both retained, timestamped, and distinct in the record — the system is built to make clear at every point that the nurse's judgment was the operative clinical decision. |
| **Small transferability failure** — a workflow tuned for a large urban trauma center may not transfer to a small rural ED. | Confidence thresholds and red-flag rules are configuration, not hard-coded constants, specifically so each site's clinical leadership can tune them rather than inheriting another hospital's calibration. |

## 7. What this prototype does and doesn't claim

This is a working demonstration of the **decision logic and safety mechanism**, not a
clinically validated scoring model. The vital-sign norms, complaint weights, and confidence
thresholds in `src/engine.py` are illustrative and stated as such — a real deployment would
require validation against a real clinical dataset and sign-off from an ED clinical advisory
board before touching a live patient queue. What this prototype is built to prove is the
*shape* of the solution: age-aware, confidence-explicit, fail-conservative, fully
overridable, and fully audited — the design commitments made in the Round 1 pitch, made
runnable.
