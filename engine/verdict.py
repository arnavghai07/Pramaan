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
4. engine/analysis.py's additional checks (placement, capture observation,
   readability, declaration validation) are folded in LAST, by
   _apply_analysis(), and can only ever make the status stricter. Checks
   marked advisory are printed and then ignored. When no analysis is supplied
   - which is every inspection recorded before that module existed - the
   result is byte-for-byte what steps 1-3 produced.
"""

import argparse
import os
import sys

# Same import shim as engine/batch.py: these modules are run BOTH as
# `python engine\verdict.py --self-test` from the repo root (where "engine" is
# not an importable package name from inside the directory) and as
# `from engine.verdict import ...` by api/main.py. Putting engine/ on the path
# makes the flat name work in the first case; the package import still works
# in the second because api/main.py runs from the repo root.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analysis import FAIL, NOT_ASSESSED, REVIEW, analysis_findings

COMPLIANT = "COMPLIANT"
NON_COMPLIANT = "NON_COMPLIANT"
NEEDS_MANUAL_REVIEW = "NEEDS_MANUAL_REVIEW"


def combine_status(rule6, rule7, analysis=None):
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
    status, findings = _rule6_rule7_status(rule6, rule7)
    return _apply_analysis(status, findings, analysis)


def _rule6_rule7_status(rule6, rule7):
    """
    The Rule 6 + Rule 7 decision EXACTLY as it was before the additional
    analysis existed. It is a separate function so that the new checks are
    provably incapable of altering it: they only ever see its output.
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


def _apply_analysis(status, findings, analysis):
    """
    Fold engine/analysis.py's checks into the status Rule 6 and Rule 7 have
    already produced.

    FOUR RULES, AND WHY EACH ONE
    -----------------------------
    1. `analysis is None` changes nothing at all - not the status, not one
       findings line. Every inspection recorded before this feature existed
       stores no analysis, and replaying one must give back the verdict it
       was issued with. A record whose meaning changes because the software
       was updated is not evidence.
    2. A deterministic FAIL (an invalid declaration, an unreadable frame)
       can only ever make the status stricter: COMPLIANT becomes
       NON_COMPLIANT. It does NOT overwrite an existing
       NEEDS_MANUAL_REVIEW - Rule 6 ambiguity already means no automatic
       verdict is safe, and turning that into a firm NON_COMPLIANT would be
       resolving an ambiguity by adding evidence about something else.
    3. A REVIEW downgrades COMPLIANT to NEEDS_MANUAL_REVIEW and nothing
       else. NOT_ASSESSED never changes the status - but it is always
       printed, because a check that ran silently reads as a check that
       passed (CLAUDE.md rule 2).
    4. An ADVISORY check is printed in full and then ignored here. Today that
       is the capture observation, whose wide-band-at-the-frame-edge test
       fires on 4 of the 10 photographs in photos/ - all of them usable
       evidence. Letting an uncalibrated heuristic with that false-positive
       rate turn a fully-declared, Rule 7-passing pack into
       NEEDS_MANUAL_REVIEW would make the review queue mostly noise, and a
       review state that means nothing is worse than one check fewer. The
       officer still sees the row, its state and its findings; what the row
       does not get is a vote.
    """
    if analysis is None:
        return status, findings

    findings = list(findings) + analysis_findings(analysis)
    severities = {c.get("state") for c in analysis.get("checks", [])
                  if not c.get("advisory")}

    if FAIL in severities and status == COMPLIANT:
        findings.append("Additional analysis established a deterministic "
                        "non-conformity, so this pack cannot be called compliant")
        return NON_COMPLIANT, findings

    if REVIEW in severities and status == COMPLIANT:
        findings.append("Additional analysis raised a point that cannot be "
                        "decided automatically, so an officer must confirm "
                        "before this pack is called compliant")
        return NEEDS_MANUAL_REVIEW, findings

    if NOT_ASSESSED in severities and status == COMPLIANT:
        findings.append("One or more additional checks could not be assessed. "
                        "That is not a pass - see the states above for which.")

    return status, findings


# ---------------------------------------------------------------------------
CLEAN_RULE6 = {"problems": [], "mandatory_present": 6, "mandatory_total": 6}
DIRTY_RULE6 = {"problems": ["mrp_value printed but unreadable - officer must confirm"],
              "mandatory_present": 6, "mandatory_total": 6}
MISSING_RULE6 = {"problems": [], "mandatory_present": 4, "mandatory_total": 6}

#: A stored analysis result, in engine/analysis.py's shape, reduced to the
#: one field _apply_analysis() reads.
def _analysis(*states):
    return {"version": 1, "overall_state": states[0],
            "checks": [{"check": "declaration_validation",
                        "title": "Declaration validation",
                        "state": s, "explanation": "", "findings": [],
                        "metrics": {}} for s in states]}


#: One verdict-bearing check that passes, plus an advisory check in REVIEW -
#: the exact shape a clear photo of a compliant pack produces once the capture
#: heuristic fires on it.
def _advisory_analysis():
    a = _analysis("PASS")
    a["checks"].append({"check": "capture_observation",
                        "title": "Capture observation", "state": "REVIEW",
                        "explanation": "", "advisory": True,
                        "findings": [], "metrics": {}})
    return a


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


#: (name, rule6, rule7, analysis, expected). Every case exists to prove the
#: additional analysis can only tighten a verdict, never loosen one, and can
#: never rewrite a record that carries no analysis.
ANALYSIS_TEST = [
    ("no analysis (an old record) -> exactly the pre-analysis verdict",
     CLEAN_RULE6, {"verdict": "PASS", "measured_height_mm": 2.35, "threshold_mm": 1.5},
     None, COMPLIANT),
    ("analysis all PASS -> still COMPLIANT",
     CLEAN_RULE6, {"verdict": "PASS", "measured_height_mm": 2.35, "threshold_mm": 1.5},
     _analysis("PASS", "PASS", "PASS"), COMPLIANT),
    ("analysis FAIL -> COMPLIANT becomes NON_COMPLIANT",
     CLEAN_RULE6, {"verdict": "PASS", "measured_height_mm": 2.35, "threshold_mm": 1.5},
     _analysis("FAIL", "PASS", "PASS"), NON_COMPLIANT),
    ("analysis REVIEW -> COMPLIANT becomes NEEDS_MANUAL_REVIEW",
     CLEAN_RULE6, {"verdict": "PASS", "measured_height_mm": 2.35, "threshold_mm": 1.5},
     _analysis("REVIEW", "PASS", "PASS"), NEEDS_MANUAL_REVIEW),
    ("analysis NOT_ASSESSED -> COMPLIANT unchanged, but stated in findings",
     CLEAN_RULE6, {"verdict": "PASS", "measured_height_mm": 2.35, "threshold_mm": 1.5},
     _analysis("NOT_ASSESSED", "PASS", "PASS"), COMPLIANT),
    ("analysis PASS cannot rescue a Rule 7 FAIL",
     CLEAN_RULE6, {"verdict": "FAIL", "measured_height_mm": 0.6, "threshold_mm": 1.0},
     _analysis("PASS", "PASS", "PASS"), NON_COMPLIANT),
    ("analysis PASS cannot rescue a Rule 6 problem",
     DIRTY_RULE6, {"verdict": "PASS", "measured_height_mm": 2.35, "threshold_mm": 1.5},
     _analysis("PASS", "PASS", "PASS"), NEEDS_MANUAL_REVIEW),
    ("analysis FAIL does not overwrite an existing NEEDS_MANUAL_REVIEW",
     DIRTY_RULE6, {"verdict": "PASS", "measured_height_mm": 2.35, "threshold_mm": 1.5},
     _analysis("FAIL", "PASS", "PASS"), NEEDS_MANUAL_REVIEW),
    ("analysis PASS cannot rescue missing mandatory declarations",
     MISSING_RULE6, None, _analysis("PASS", "PASS", "PASS"), NON_COMPLIANT),
    # The regression this split exists to prevent: an uncalibrated capture
    # heuristic firing on a good photograph must not move the verdict.
    ("an ADVISORY REVIEW leaves COMPLIANT alone",
     CLEAN_RULE6, {"verdict": "PASS", "measured_height_mm": 2.35, "threshold_mm": 1.5},
     _advisory_analysis(), COMPLIANT),
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
    print("\n  -- additional analysis integration --")
    for name, rule6, rule7, analysis, expected in ANALYSIS_TEST:
        status, findings = combine_status(rule6, rule7, analysis)
        good = status == expected
        ok += good
        print(f"  {'ok  ' if good else 'FAIL'} {name}")
        print(f"        -> {status} (want {expected})")

    # The pre-analysis path must be untouched, not merely equivalent: an old
    # stored inspection replayed through the new code must produce the same
    # status AND the same findings list, character for character.
    identical = combine_status(CLEAN_RULE6, None) == combine_status(CLEAN_RULE6, None, None)
    ok += identical
    print(f"  {'ok  ' if identical else 'FAIL'} analysis=None is identical to "
          "the two-argument call")

    total = len(SELF_TEST) + len(ANALYSIS_TEST) + 1
    print(f"\n{ok}/{total} correct")
    return ok == total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if not a.self_test:
        ap.error("this module has no CLI beyond --self-test; it is called by api/main.py")
    sys.exit(0 if self_test() else 1)


if __name__ == "__main__":
    main()
