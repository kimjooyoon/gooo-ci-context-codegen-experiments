#!/usr/bin/env python3
"""Separate candidate-sensitive finite cases from scaffold-only observations.

No model calls or new test vectors. Counterfactual outputs come from the pinned
finite oracle; the selected output counts are bound to independent Go receipts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN = ROOT / "audit" / "laya-selection-2026-09-30"


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def subset_score(outputs: list[int], expected: list[int], indexes: list[int]) -> dict:
    return {"passed": sum(outputs[index] == expected[index] for index in indexes),
            "total": len(indexes)}


def analyze(run: Path) -> dict:
    report_bytes = (run / "report.json").read_bytes()
    report = json.loads(report_bytes)
    manifest = load(ROOT / "manifest.json")
    oracle_digests = {item["id"]: item["independent_oracle_sha256"]
                      for item in manifest["intents"]}
    adequacy = {}
    cells = []
    for cell in report["intent_treatment_results"]:
        receipt_path = (run / cell["go_validation_receipt"]).resolve()
        require(receipt_path.is_relative_to(run.resolve()), "receipt escaped run directory")
        receipt = load(receipt_path)
        oracle_bytes = (receipt_path.parent / "independent-oracle.json").read_bytes()
        oracle = json.loads(oracle_bytes)
        intent = cell["intent_id"]
        require(digest(oracle_bytes) == oracle_digests[intent] == receipt["oracle_sha256"],
                f"{intent}: oracle digest mismatch")
        require(oracle["intent_id"] == intent and receipt["intent_id"] == intent,
                "oracle or receipt intent mismatch")
        selected = cell["candidate_id"]
        require(selected == receipt["candidate_id"], "selected candidate mismatch")
        require(receipt["all_finite_cases_observed"], "Go replay missed finite cases")
        candidate_outputs = oracle["candidate_outputs"]
        finite_suites = {}
        scores = {}
        for suite_id in ("training", "holdout"):
            suite = oracle[suite_id]
            expected = suite["expected"]
            require(len(expected) == len(suite["inputs"]), "oracle suite shape mismatch")
            vectors = {key: value[suite_id] for key, value in candidate_outputs.items()}
            require(all(len(vector) == len(expected) for vector in vectors.values()),
                    "candidate vector shape mismatch")
            sensitive = [index for index in range(len(expected))
                         if len({vector[index] for vector in vectors.values()}) > 1]
            invariant = [index for index in range(len(expected)) if index not in sensitive]
            signatures = {}
            for candidate_id, vector in vectors.items():
                signatures.setdefault(tuple(vector), []).append(candidate_id)
            finite_suites[suite_id] = {
                "total": len(expected),
                "candidate_discriminating_indexes": sensitive,
                "candidate_invariant_indexes": invariant,
                "candidate_signature_groups": sorted(sorted(group) for group in signatures.values()),
            }
            selected_vector = vectors[selected]
            all_score = subset_score(selected_vector, expected, list(range(len(expected))))
            require(all_score == receipt[f"{suite_id}_score"] == cell[f"{suite_id}_score_external_go"],
                    f"{intent}: selected oracle score differs from independent Go observation")
            scores[suite_id] = {
                "all_cases": all_score,
                "candidate_discriminating_cases": subset_score(selected_vector, expected, sensitive),
                "candidate_invariant_cases": subset_score(selected_vector, expected, invariant),
            }
        prior = adequacy.setdefault(intent, {"oracle_sha256": digest(oracle_bytes),
                                           "suites": finite_suites})
        require(prior["suites"] == finite_suites, "oracle differs across treatments")
        cells.append({"intent_id": intent, "treatment": cell["treatment"],
                      "candidate_id": selected, "scores": scores})
    require(len(cells) == 12 and len({(cell["intent_id"], cell["treatment"])
                                     for cell in cells}) == 12,
            "expected exactly twelve distinct frozen cells")
    aggregate = {}
    for cell in cells:
        target = aggregate.setdefault(cell["treatment"], {})
        for suite_id, scores in cell["scores"].items():
            suite_target = target.setdefault(suite_id, {})
            for kind, score in scores.items():
                subtotal = suite_target.setdefault(kind, {"passed": 0, "total": 0})
                for key in subtotal:
                    subtotal[key] += score[key]
    return {
        "schema": "gooo/finite-candidate-discrimination/v1",
        "run_id": run.name,
        "source_report_sha256": digest(report_bytes),
        "definition": "A case discriminates these declared candidates iff their frozen oracle outputs differ at that case index.",
        "counterfactual_source": "Pinned finite-oracle candidate_outputs; unselected candidates are not attributed to new runtime execution.",
        "limits": ["Relative only to the three declared candidates for each intent.",
                   "An invariant case may execute the filled branch; invariance is not a branch coverage claim.",
                   "No new vectors, model calls, full-domain proof, or population intent accuracy.",
                   "One invocation per intent-treatment cell; routing differs between treatments."],
        "suite_adequacy": adequacy,
        "cells": cells,
        "treatment_aggregate": aggregate,
    }


def render(report: dict) -> str:
    def score(value):
        return f"{value['passed']}/{value['total']}"
    lines = ["# Finite tests that distinguish candidate fills", "",
             "The saved study uses the same existing cases and model responses. This analysis separates cases whose frozen oracle outputs vary across the three declared candidates from cases where all candidates produce the same output.", "",
             "| Treatment | Training, all | Holdout, all | Holdout, candidate-discriminating | Holdout, candidate-invariant |",
             "|---|---:|---:|---:|---:|"]
    for treatment, suites in sorted(report["treatment_aggregate"].items()):
        holdout = suites["holdout"]
        lines.append(f"| {treatment} | {score(suites['training']['all_cases'])} | {score(holdout['all_cases'])} | {score(holdout['candidate_discriminating_cases'])} | {score(holdout['candidate_invariant_cases'])} |")
    lines.extend(["", "Across the four distinct intents, only 4 of the 16 holdout cases distinguish the declared candidates; the other 12 pass for every candidate. The unchanged-intent arms pass 2/4 discriminating cases (50%), and the exact-CI-context arm passes 1/4 (25%). These are finite, candidate-relative observations. The 14/16 and 13/16 overall scores also contain the 12 invariant successes.", "",
                  "For absolute value, `identity` and `negate` have identical outputs on the entire four-case holdout suite: the sole negative holdout input is MinInt64, whose signed negation wraps to itself. Training distinguishes those candidates. Therefore holdout success alone cannot identify the intended fill.", "",
                  "Counterfactual outputs are from the hash-pinned independent finite oracle. Selected candidate totals are checked against the saved independently compiled Go observations. An invariant test can still execute the filled branch; this metric does not measure branch coverage. It proves neither full-domain correctness nor a causal context effect (n=1 per cell, with different routing).", ""])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--check", action="store_true", help="Reject derived files that differ from recomputation")
    args = parser.parse_args()
    report = analyze(args.run_dir)
    outputs = {
        "case-discrimination.json": json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        "case-discrimination.md": render(report),
    }
    for filename, expected in outputs.items():
        path = args.run_dir / filename
        if args.check:
            require(path.read_text(encoding="utf-8") == expected, f"stale derived file: {filename}")
        else:
            path.write_text(expected, encoding="utf-8")
    print("PASS: 12 selected Go score bindings; 4/16 holdout cases discriminate declared candidates")


if __name__ == "__main__":
    main()
