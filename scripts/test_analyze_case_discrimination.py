"""Regressions for the finite candidate-discrimination calculation."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from analyze_case_discrimination import DEFAULT_RUN, analyze


class CaseDiscriminationTests(unittest.TestCase):
    def test_existing_finite_denominators(self):
        result = analyze(DEFAULT_RUN)
        self.assertEqual(len(result["cells"]), 12)
        for intent in result["suite_adequacy"].values():
            self.assertEqual(len(intent["suites"]["holdout"]["candidate_discriminating_indexes"]), 1)
        expected = {
            "no_context": {"passed": 2, "total": 4},
            "rejected_stale_source_context": {"passed": 2, "total": 4},
            "exact_matching_failed_ci_context": {"passed": 1, "total": 4},
        }
        for treatment, score in expected.items():
            self.assertEqual(result["treatment_aggregate"][treatment]["holdout"]
                             ["candidate_discriminating_cases"], score)

    def test_rejects_altered_counterfactual_oracle(self):
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / DEFAULT_RUN.name
            shutil.copytree(DEFAULT_RUN, copied)
            path = next((copied / "postselection-validation").glob("*/independent-oracle.json"))
            oracle = json.loads(path.read_text())
            oracle["candidate_outputs"]["identity"]["holdout"][0] += 1
            path.write_text(json.dumps(oracle))
            with self.assertRaisesRegex(ValueError, "oracle digest mismatch"):
                analyze(copied)

    def test_rejects_report_score_that_disagrees_with_go(self):
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / DEFAULT_RUN.name
            shutil.copytree(DEFAULT_RUN, copied)
            path = copied / "report.json"
            report = json.loads(path.read_text())
            report["intent_treatment_results"][0]["holdout_score_external_go"]["passed"] += 1
            path.write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError, "differs from independent Go observation"):
                analyze(copied)


if __name__ == "__main__":
    unittest.main()
