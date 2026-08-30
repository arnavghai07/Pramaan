"""
verdict.py  —  PRAMAAN
========================
Combines the Rule 6 (declarations) result from vlm_extract.extract() and the
Rule 7 (measurement) result from measure_chart.rule7_result() into ONE overall
inspection status.

WHY THIS FILE EXISTS
---------------------
CLAUDE.md rule 1: the model never decides. That rule doesn't only mean "no
LLM issues a verdict" — it also means the combined decision must not live in
a React component either, where it would be invisible to anything that isn't
the browser and impossible to unit-test the same way twice. This is the one
place - plain Python, no camera, no model, no framework - that turns two
engine results into a status a judge or an inspector can be shown.

THE THREE STATUSES
-------------------
COMPLIANT            - every applicable check ran and passed.
NON_COMPLIANT        - a specific, evidence-backed violation was found.
NEEDS_MANUAL_REVIEW  - the system could not confidently decide. Silence,
                       ambiguity, low confidence, or missing measurement
                       coverage all land here, never on a manufactured PASS
                       or FAIL - CLAUDE.md rule 2, "silence is never a pass",
                       extended to this combined decision.

PRECEDENCE
----------
1. A Rule 6 cross-check problem (illegible field, MRP/date arithmetic that
   doesn't add up) always forces NEEDS_MANUAL_REVIEW, regardless of Rule 7.
   An ambiguous Rule 6 read is not cured by a passing measurement.
2. A confirmed Rule 6 mandatory-field omission (no problems, just missing)
   is NON_COMPLIANT on its own - a real violation Rule 7 cannot offset.
3. Only when Rule 6 is completely clean does Rule 7 decide between
   COMPLIANT / NON_COMPLIANT / NEEDS_MANUAL_REVIEW: PASS / FAIL / REVIEW.
   Rule 7 not attempted, or attempted but the marker/photo failed, or
   attempted but no measurement target has been resolved yet, are all
   "insufficient evidence" and all land on NEEDS_MANUAL_REVIEW - never
   NON_COMPLIANT, per BUILD_PLAN.md's "incomplete image coverage must not
   automatically become NON-COMPLIANT".
"""

import argparse
import sys

COMPLIANT = "COMPLIANT"
NON_COMPLIANT = "NON_COMPLIANT"
NEEDS_MANUAL_REVIEW = "NEEDS_MANUAL_REVIEW"


def combine_status(rule6, rule7):
    """
    rule6: the dict vlm_extract.extract() returns - needs "problems",
           "mandatory_present", "mandatory_total".
    rule7: None (not attempted), or a dict with:
             "problem"            - str if the marker/photo itself failed
                                     (MarkerNotFound / MarkerTilted message),
                                     else None/absent
             "verdict"            - "PASS" / "FAIL" / "REVIEW", or None if
                                     no measurement target has been resolved
                                     yet (candidates only)
             "measured_height_mm", "threshold_mm" - for the finding text

    Returns (overall_status, findings): findings is a list of short strings,
    each one traceable to a specific rule and evidence - never an
    unexplained status.
    """
    findings = []

    if rule6["problems"]:
        for p in rule6["problems"]:
            findings.append(f"Rule 6: {p}")
        findings.append("Rule 6 raised cross-check problems that require "
                        "officer confirmation before any verdict is safe")
        return NEEDS_MANUAL_REVIEW, findings

    if rule6["mandatory_present"] < rule6["mandatory_total"]:
        findings.append(
            f"Rule 6: only {rule6['mandatory_present']}/{rule6['mandatory_total']} "
            "mandatory declarations were detected on the label")
        return NON_COMPLIANT, findings

    findings.append(
        f"Rule 6: {rule6['mandatory_present']}/{rule6['mandatory_total']} mandatory "
        "declarations present, no cross-check problems")

    if rule7 is None:
        findings.append("Rule 7: not attempted - no measurement photo was supplied. "
                        "Additional measurement is recommended before this pack "
                        "can be called compliant.")
        return NEEDS_MANUAL_REVIEW, findings

    if rule7.get("problem"):
        findings.append(f"Rule 7: {rule7['problem']}")
        return NEEDS_MANUAL_REVIEW, findings

    verdict = rule7.get("verdict")
    if verdict is None:
        findings.append("Rule 7: measurement candidates were detected but no "
                        "target has been selected yet")
        return NEEDS_MANUAL_REVIEW, findings

    height = rule7.get("measured_height_mm")
    threshold = rule7.get("threshold_mm")
    detail = (f"measured {height:.2f} mm against a {threshold:.1f} mm threshold"
             if height is not None and threshold is not None
             else "measurement recorded")

    if verdict == "PASS":
        findings.append(f"Rule 7: {detail} - PASS")
        return COMPLIANT, findings
    if verdict == "FAIL":
        findings.append(f"Rule 7: {detail} - FAIL")
        return NON_COMPLIANT, findings

    findings.append(f"Rule 7: {detail} - within the review band, not a "
                    "confident PASS or FAIL")
    return NEEDS_MANUAL_REVIEW, findings


# ---------------------------------------------------------------------------
CLEAN_RULE6 = {"problems": [], "mandatory_present": 6, "mandatory_total": 6}
DIRTY_RULE6 = {"problems": ["mrp_value printed but unreadable - officer must confirm"],
              "mandatory_present": 6, "mandatory_total": 6}
MISSING_RULE6 = {"problems": [], "mandatory_present": 4, "mandatory_total": 6}

SELF_TEST = [
    ("PASS + PASS -> COMPLIANT",
     CLEAN_RULE6, {"verdict": "PASS", "measured_height_mm": 2.35, "threshold_mm": 1.5},
     COMPLIANT),
    ("PASS + FAIL -> NON_COMPLIANT",
     CLEAN_RULE6, {"verdict": "FAIL", "measured_height_mm": 0.6, "threshold_mm": 1.0},
     NON_COMPLIANT),
    ("PASS + REVIEW -> NEEDS_MANUAL_REVIEW",
     CLEAN_RULE6, {"verdict": "REVIEW", "measured_height_mm": 1.05, "threshold_mm": 1.0},
     NEEDS_MANUAL_REVIEW),
    ("Rule 6 problems dominate, even with a Rule 7 PASS",
     DIRTY_RULE6, {"verdict": "PASS", "measured_height_mm": 2.35, "threshold_mm": 1.5},
     NEEDS_MANUAL_REVIEW),
    ("Rule 6 missing mandatory field(s), no Rule 7 photo -> NON_COMPLIANT",
     MISSING_RULE6, None,
     NON_COMPLIANT),
    ("Clean Rule 6, Rule 7 not attempted -> NEEDS_MANUAL_REVIEW, not COMPLIANT",
     CLEAN_RULE6, None,
     NEEDS_MANUAL_REVIEW),
    ("Clean Rule 6, Rule 7 marker tilted -> NEEDS_MANUAL_REVIEW, not NON_COMPLIANT",
     CLEAN_RULE6, {"problem": "MARKER TOO TILTED: 12.0% spread"},
     NEEDS_MANUAL_REVIEW),
    ("Clean Rule 6, Rule 7 candidates pending selection -> NEEDS_MANUAL_REVIEW",
     CLEAN_RULE6, {"verdict": None},
     NEEDS_MANUAL_REVIEW),
]


def self_test():
    print("VERDICT SELF-TEST (no photo, no model needed)\n")
    ok = 0
    for name, rule6, rule7, expected in SELF_TEST:
        status, findings = combine_status(rule6, rule7)
        good = status == expected
        ok += good
        print(f"  {'ok  ' if good else 'FAIL'} {name}")
        print(f"        -> {status} (want {expected})")
        if not good:
            for f in findings:
                print(f"           {f}")
    print(f"\n{ok}/{len(SELF_TEST)} correct")
    return ok == len(SELF_TEST)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if not a.self_test:
        ap.error("this module has no CLI beyond --self-test; it is called by api/main.py")
    sys.exit(0 if self_test() else 1)


if __name__ == "__main__":
    main()
