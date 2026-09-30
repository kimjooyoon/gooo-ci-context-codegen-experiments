# Downloaded CI evidence

`ci-run-36673382253/github-artifact.zip` is the archive returned by GitHub for the passing workflow run below; its SHA-256 matches the artifact digest reported by the GitHub Actions API. `ci-run-36673382253/files/` is the extracted copy. The raw per-intent Gooo output, generated bodies, and intentionally failing Go test output are preserved as downloaded.

The artifact contains training cases only. Its four Go probes report 13 expected training mismatches across 13 finite cases. A passing workflow means those mismatches were reproduced and their receipts agreed; it does not assert all-int64 correctness or establish external CI authority for Gooo's internal evaluator.
