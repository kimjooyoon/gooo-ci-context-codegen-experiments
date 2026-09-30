# Pinned compact training context study — derived summary correction

This report is a derived correction to the original capture summary. The original `report.json` and `report.md` remain unchanged.
The correction fixes a bookkeeping omission: per-invocation context checks and raw model-state/profile hashes pass, while the original request-audit aggregate did not copy those verified results into its summary count.

Original capture report SHA-256: `1f079748a2ba2d4d75adc4f97b46fc3b090e726f7b587d193b8f1a1c28b29c74`.
Derived report decision: **CAPTURED_AND_COMPILED**. Design SHA-256: `00da5cae3ee097870a66320ae18cf3fcd510652a369eb8eef31b5e6b4c5fb896`.

## Measured results

All 72 measured invocations and both warmups have one captured provider POST, a matching pinned response route, a typed request digest matching its receipt, and a model state matching the frozen profile. Each compiled output passed independent Go validation against the reused finite oracle.

| Context | Model | Go validations | Training all | Training discriminating | Reused holdout all | Reused holdout discriminating | Receipt route |
|---|---|---:|---:|---:|---:|---:|---:|
| legacy_no_feedback | english | 12/12 | 18/39 (observed 39, unknown 0) | 18/39 (observed 39, unknown 0) | 42/48 (observed 48, unknown 0) | 6/12 (observed 12, unknown 0) | 12/12 |
| legacy_no_feedback | multilingual | 12/12 | 21/39 (observed 39, unknown 0) | 21/39 (observed 39, unknown 0) | 42/48 (observed 48, unknown 0) | 6/12 (observed 12, unknown 0) | 12/12 |
| compact_no_feedback | english | 12/12 | 18/39 (observed 39, unknown 0) | 18/39 (observed 39, unknown 0) | 42/48 (observed 48, unknown 0) | 6/12 (observed 12, unknown 0) | 12/12 |
| compact_no_feedback | multilingual | 12/12 | 30/39 (observed 39, unknown 0) | 30/39 (observed 39, unknown 0) | 45/48 (observed 48, unknown 0) | 9/12 (observed 12, unknown 0) | 12/12 |
| compact_external_feedback | english | 12/12 | 18/39 (observed 39, unknown 0) | 18/39 (observed 39, unknown 0) | 42/48 (observed 48, unknown 0) | 6/12 (observed 12, unknown 0) | 12/12 |
| compact_external_feedback | multilingual | 12/12 | 9/39 (observed 39, unknown 0) | 9/39 (observed 39, unknown 0) | 42/48 (observed 48, unknown 0) | 6/12 (observed 12, unknown 0) | 12/12 |

## Timing and resources

Warmup timing is saved separately and excluded from measured-cell latency lists. Per-invocation CLI active wall time, harness sampling window, and process-local CPU/RSS samples remain exactly as recorded in the original report.

CLI active wall time measures the compiler process; harness sampling window additionally includes process sampling cleanup. Resolver/provider POST latency is captured separately per raw exchange; none is model-forward-only latency.

Cumulative CPU and sampled RSS are recorded for the compiler CLI and owned Laya server processes. These observations are process-local and are not host CPU increase measurements.

## Correction audit

Raw request/model-state/profile checks passed for 72/72 measured calls; response routes passed for 74/74 calls; reconstructed typed request digests matched receipts for 74/74 calls.
Original feedback aggregate was 0/72; original per-invocation context rows were true for 72/72 measured calls.
Only `capture.measured_requests_matching_plan_feedback.passed` and top-level `decision` were changed in this derived report. No selected candidate, compiled score, timing, resource sample, raw request, raw response, receipt, or source evidence was changed.

## Scope

- Three repeats per finite intent-prompt-profile-model cell do not support a general population or speed claim.
- The source CI observations are training mismatches for the declared identity candidate; they are not full-domain correctness evidence.
- The four existing known intents and their candidate lists define this study's scope.
- Finite oracle outputs are reused benchmark vectors and are not a fresh unseen generalization set.
- CPU and RSS measurements are sampled at the process level and do not measure model-forward-only cost or host CPU increase.
- A raw model reply, Gooo selected-candidate receipt, explicit provider model pin, and source-bound feedback field are recorded separately.
- A failed invocation is kept in the denominator and never silently retried or replaced.
- The known finite oracle is used as a reused benchmark; no unseen intent-generalization claim is made.
- The Gooo resolver has an 8-second request budget. The local capture proxy waits at most 10 seconds upstream and the runner drains pending exchanges for at most 13 seconds before it aborts later planned calls; upstream forwarding can remain active until that bound, and calls are never retried.
