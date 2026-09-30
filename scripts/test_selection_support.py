import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from selection_support import (ArtifactExtractionRejected, ContextRejected, PrivacyScanError,
                               scan_selection_body, verify_ci_failure_context, verify_zip_extraction)


class SelectionSupportTests(unittest.TestCase):
    def setUp(self):
        self.pairs = {(json.dumps(-77), json.dumps(888))}

    def assert_leak_rejected(self, payload):
        with self.assertRaises(PrivacyScanError):
            scan_selection_body(json.dumps(payload).encode(), self.pairs)

    def test_embedded_case_in_outer_question_text_is_rejected(self):
        encoded = json.dumps({"cases": [{"input": -77, "expected": 888}]})
        self.assert_leak_rejected({"question": {"text": "context: " + encoded}})

    def test_embedded_case_in_option_is_rejected(self):
        encoded = json.dumps({"input": -77, "expected": 888})
        self.assert_leak_rejected({"options": [{"description": "quoted value " + encoded}]})

    def test_double_encoded_case_in_nested_state_is_rejected(self):
        encoded = json.dumps(json.dumps({"wrapper": {"input": -77, "expected": 888}}))
        self.assert_leak_rejected({"state": {"request": encoded}})

    def test_duplicate_keys_inside_embedded_json_are_not_discarded(self):
        encoded = '{"wrapper":{"input":-77,"expected":888,"input":4,"expected":5}}'
        self.assert_leak_rejected({"question": "quoted JSON: " + encoded})

    def test_holdout_field_name_is_rejected_without_values(self):
        self.assert_leak_rejected({"state": {"holdout_case_count": 1}})

    def test_candidate_constants_and_irrelevant_numbers_are_allowed(self):
        payload = {"options": [{"expression": "888"}], "state": {"count": 888, "training_test_count": 3}}
        self.assertEqual(scan_selection_body(json.dumps(payload).encode(), self.pairs)["heldout_fields_or_pairs_found"], 0)

    def test_case_shaped_pair_requires_both_values_and_exact_match(self):
        payload = {"unrelated": {"input": -77}, "another": {"expected": 888}, "candidate": {"input": -77, "constant": 888}}
        self.assertEqual(scan_selection_body(json.dumps(payload).encode(), self.pairs)["heldout_fields_or_pairs_found"], 0)

    def test_stale_source_binding_is_rejected_before_context_injection(self):
        binding = {
            "compiler_revision": "rev", "fixture_sha256": "fixture", "plan_sha256": "plan", "activity": "A", "activity_id": "id",
            "training_suite_sha256": "training", "candidate_id": "identity", "candidate_expression": "input",
            "compiler_generated_digest": "generated", "generated_source_sha256": "generated",
        }
        evidence = {
            "binding": binding,
            "result": "EXPECTED_TRAINING_MISMATCHES_REPRODUCED",
            "training_case_count": 1,
            "training_mismatch_count": 1,
            "training_observations": [{"input": -1, "expected": 0, "actual": -1, "passed": False}],
        }
        expected = dict(binding)
        stale = dict(binding, generated_source_sha256="stale-source")
        with self.assertRaises(ContextRejected):
            verify_ci_failure_context(evidence, stale)
        self.assertTrue(verify_ci_failure_context(evidence, expected).startswith("\n\n[Bounded verified CI training failure context] "))

    def test_modified_extracted_ci_evidence_is_rejected_with_archive_unchanged(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            archive_path = root / "artifact.zip"
            extracted = root / "files"
            extracted.mkdir()
            with ZipFile(archive_path, "w") as archive:
                archive.writestr("clamp/evidence.json", b"archive-bound-evidence")
            evidence_path = extracted / "clamp" / "evidence.json"
            evidence_path.parent.mkdir(parents=True)
            evidence_path.write_bytes(b"archive-bound-evidence")
            verify_zip_extraction(archive_path, extracted)
            evidence_path.write_bytes(b"modified-extracted-evidence")
            with self.assertRaises(ArtifactExtractionRejected):
                verify_zip_extraction(archive_path, extracted)


if __name__ == "__main__":
    unittest.main()
