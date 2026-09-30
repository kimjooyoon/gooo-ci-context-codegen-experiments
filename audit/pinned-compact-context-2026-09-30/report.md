# Pinned compact training context study

Run `pinned-compact-context-2026-09-30` captured the frozen study with compiler revision `bb5c1ec2f81cbfb17ac6fb2f7a9e1d7b67168e7f` and Laya 0.3.21 model revision `55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851`.

The design scheduled 72 measured choices in 4 known intent × 3 prompt-profile arms × 2 explicit-model cells with 3 repeats, plus 2 separately counted warmups. It captured 72/72 measured choice posts and 74/74 total choice posts.

## Finite compiled scores

Scores use independently compiled Go output against the existing finite oracle. Holdout vectors are reused benchmark data and are not a fresh generalization set. Discriminating cases are defined relative to the candidate outputs already declared for that intent.

| Context | Explicit model | Go validations | Training all | Training candidate-discriminating | Reused holdout all | Reused holdout candidate-discriminating | Model receipt matches pin |
|---|---|---:|---:|---:|---:|---:|---:|
| legacy_no_feedback | english | 12/12 | 18/39 (observed 39, unknown 0) | 18/39 (observed 39, unknown 0) | 42/48 (observed 48, unknown 0) | 6/12 (observed 12, unknown 0) | 12/None (observed None, unknown None) |
| legacy_no_feedback | multilingual | 12/12 | 21/39 (observed 39, unknown 0) | 21/39 (observed 39, unknown 0) | 42/48 (observed 48, unknown 0) | 6/12 (observed 12, unknown 0) | 12/None (observed None, unknown None) |
| compact_no_feedback | english | 12/12 | 18/39 (observed 39, unknown 0) | 18/39 (observed 39, unknown 0) | 42/48 (observed 48, unknown 0) | 6/12 (observed 12, unknown 0) | 12/None (observed None, unknown None) |
| compact_no_feedback | multilingual | 12/12 | 30/39 (observed 39, unknown 0) | 30/39 (observed 39, unknown 0) | 45/48 (observed 48, unknown 0) | 9/12 (observed 12, unknown 0) | 12/None (observed None, unknown None) |
| compact_external_feedback | english | 12/12 | 18/39 (observed 39, unknown 0) | 18/39 (observed 39, unknown 0) | 42/48 (observed 48, unknown 0) | 6/12 (observed 12, unknown 0) | 12/None (observed None, unknown None) |
| compact_external_feedback | multilingual | 12/12 | 9/39 (observed 39, unknown 0) | 9/39 (observed 39, unknown 0) | 42/48 (observed 48, unknown 0) | 6/12 (observed 12, unknown 0) | 12/None (observed None, unknown None) |

## Capture and timing

Provider-model request pins matched 72/72 measured requests; source-bound feedback state matched its frozen plan for 0/72. Any missing route or context observation remains a failed denominator entry.

Warmup CLI latencies are saved under `warmup_results_separate` and excluded from measured-cell latency lists. Each invocation retains compiler active wall time, the wider harness sampling window, per-process CPU/RSS samples, and raw Laya POST/reply bytes.

CLI active wall time measures the compiler process; harness sampling window additionally includes process sampling cleanup. Resolver/provider POST latency is captured separately per raw exchange; none is model-forward-only latency.

Cumulative CPU and sampled RSS are recorded for the compiler CLI and owned Laya server processes. These observations are process-local and are not host CPU increase measurements.

## Limits

- Three repeats per finite intent-prompt-profile-model cell do not support a general population or speed claim.
- The source CI observations are training mismatches for the declared identity candidate; they are not full-domain correctness evidence.
- The four existing known intents and their candidate lists define this study's scope.
- Finite oracle outputs are reused benchmark vectors and are not a fresh unseen generalization set.
- CPU and RSS measurements are sampled at the process level and do not measure model-forward-only cost or host CPU increase.
- A raw model reply, Gooo selected-candidate receipt, explicit provider model pin, and source-bound feedback field are recorded separately.
- A failed invocation is kept in the denominator and never silently retried or replaced.
- The known finite oracle is used as a reused benchmark; no unseen intent-generalization claim is made.
- The Gooo resolver has an 8-second request budget. The local capture proxy waits at most 10 seconds upstream and the runner drains pending exchanges for at most 13 seconds before it aborts later planned calls; upstream forwarding can remain active until that bound, and calls are never retried.
