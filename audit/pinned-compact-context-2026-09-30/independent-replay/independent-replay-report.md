# Independent pinned-context replay

Decision: **PASS**. The frozen plan contains 72 measured calls and 2 separate warmups.

| Treatment | Model | Planned | POSTs | Route | Receipt | Typed digest | Feedback | Go builds | Training score | Reused holdout score |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| legacy_no_feedback | english | 12 | 12 | 12/12 | 12/12 | 12/12 | 12/12 | 12/12 | 18/39 (observed 39, unknown 0) | 42/48 (observed 48, unknown 0) |
| legacy_no_feedback | multilingual | 12 | 12 | 12/12 | 12/12 | 12/12 | 12/12 | 12/12 | 21/39 (observed 39, unknown 0) | 42/48 (observed 48, unknown 0) |
| compact_no_feedback | english | 12 | 12 | 12/12 | 12/12 | 12/12 | 12/12 | 12/12 | 18/39 (observed 39, unknown 0) | 42/48 (observed 48, unknown 0) |
| compact_no_feedback | multilingual | 12 | 12 | 12/12 | 12/12 | 12/12 | 12/12 | 12/12 | 30/39 (observed 39, unknown 0) | 45/48 (observed 48, unknown 0) |
| compact_external_feedback | english | 12 | 12 | 12/12 | 12/12 | 12/12 | 12/12 | 12/12 | 18/39 (observed 39, unknown 0) | 42/48 (observed 48, unknown 0) |
| compact_external_feedback | multilingual | 12 | 12 | 12/12 | 12/12 | 12/12 | 12/12 | 12/12 | 9/39 (observed 39, unknown 0) | 42/48 (observed 48, unknown 0) |

MOCK preflight: 24 choice POST templates, 0 actual Laya calls.
Privacy-scanned raw provider requests: 74.

## Invocation failures

- None

## Scope

- The holdout suite is a reused finite benchmark, not a fresh generalization sample.
- Independent Go execution confirms emitted behavior on declared finite vectors only.
- Source-CI actual values remain observed failures supplied as advisory feedback; they are not promoted to verified passing behavior.
