# Gooo CI failure context experiment

This public experiment captures bounded, reproducible Go test failures from Gooo-emitted bodies. The current phase uses four primary IR-search intents (`clamp`, `absolute`, `piecewise`, and `compound-precedence`) and intentionally selects the first declared `identity` expression with `max_attempts: 1`. The workflow passes only when the emitted source compiles, every declared training case is observed, and the expected mismatches are independently reproduced by Go tests.

The compiler is rebuilt in CI from `kimjooyoon/meta-ontology-go` at `29d44bc778d85aee03b9af500bd83dc98f368189`. Each plan and artifact contains training cases only. The workflow has no Laya endpoint or API key, does not call a model, and does not download models. It records the raw body-codegen output, emitted Go, raw failing test output, and a receipt binding compiler revision, fixture, plan, activity, candidate, source, and training suite hashes. The test harness is written for verification; only `emitted.go` is attributed to Gooo.

The test failures are expected evidence, not a broken default check. They show finite training mismatches for four declared examples and do not claim that the selected expression is wrong over every possible input. These finite training denominators are reported separately; there is no 100% semantic or holdout claim in this cohort.

## Frozen Laya selection design

For each intent, a later model-selection phase may compare three treatments:

1. **No context:** pass the original bounded intent and unchanged training plan.
2. **Exact matching failed-CI context:** append a compact, explicitly labeled failure summary to `intent`, only after matching the captured fixture, activity, compiler revision, plan, and source digests. Keep the appended text at most 900 Unicode code points and the full `intent` at most the compiler's 2,000-code-point limit. Include only training failures and hashes; never append any other case set.
3. **Rejected stale-source context:** present a context record whose source digest differs, reject it before injection, and make the choice request with the unchanged base intent. This is a rejection control, not a different model input.

The current `IRBodySearchPlan` exposes no structured external-context field, and its decoder rejects unknown fields. Therefore the matching-context treatment appends a bounded summary to the natural-language `intent`, not a typed compiler input or authenticated proof. The wrapper validates compiler, fixture, activity, plan, training suite, candidate, and emitted-source bindings before building that plan. It rejects a stale source binding before injection.

The frozen run design contains four intents × three treatments, shuffled once using the recorded seed, one invocation per cell, no warmup, and one allowed attempt per invocation. All Laya requests contain training cases only. The runner saves raw requests and responses before opening independent holdout vectors; holdout scoring happens only on the final emitted bodies after selection. A recorded provider request is not treated as a model-selected choice unless Gooo's receipt attributes the decision to Laya.

`cli_active_wall_ms` ends when the Gooo process returns. `harness_sampling_window_ms` includes later sampler shutdown; sampled CPU and RSS are process-level observations, not continuous peaks or pure-model timing. This design has n=1 per cell and supports no statistical or general latency claim. Finite scores do not prove behavior over the full int64 domain.

The saved study will include copied independent finite-oracle files only in its postselection validation folder, after all selection responses have been persisted. The manifest pins their byte digests without exposing holdout vectors to the model. The initial CI artifact remains training-only.

## Captured result

Run `laya-selection-2026-09-30` completed all 12 planned Laya choices, with one call per intent-treatment cell. Its raw POST bodies passed recursive holdout-privacy scans; each rejected-stale control sent bytes identical to its no-context counterpart. The report records separate finite training and postselection holdout counts for every choice.

Pooling the four intents gives these descriptive totals: no-context 6/13 training and 14/16 holdout; exact-context 3/13 and 13/16; rejected-stale 6/13 and 14/16. A separate [candidate-discrimination analysis](audit/laya-selection-2026-09-30/case-discrimination.md) shows how many holdout cases distinguish the three declared candidates; it does not change the emitted-body finite scores above. Each denominator is a repeated set of finite examples, with one selection per cell, so these are not statistical estimates.

The four exact-context requests all reported `multilingual/it` routing, while the eight unchanged-intent requests reported `english/en`. Routing was not held constant, so choice differences may reflect the route as well as the context. The exact-context arm did not improve the pooled finite training or holdout counts in this run. The 12 observations are descriptive (n=1 per cell); they do not establish a causal treatment effect, broad correctness, or a general speed result. See [`report.md`](audit/laya-selection-2026-09-30/report.md) and its raw request/response and compiled Go artifacts.

The non-model CI cost audit in `audit/compiler-ci-cost-2026-09-30/` is run-metadata evidence, not context supplied to a selection request.

The original runner overwrote `run-metadata.json.completed_utc` during
postselection finalization. That field is the finalization endpoint in this
saved run; subtracting `started_utc` would mix capture and later processing.
Use the recorded per-invocation process and proxy timers for the reported
latencies. The historical bytes remain intact. Future captures record
`capture_completed_utc` and `finalization_completed_utc` separately.

## Evidence mutation checks

An unchanged control passed all 12 saved-source replays. Six temporary evidence
mutations were rejected at their expected checks: changed typed request digest,
changed emitted source, relabeled finite score, relabeled stale-context gate,
missing raw request, and mismatched provider choice. These are offline validator
experiments with zero model calls. The original recorder and results are retained
in [`audit/selection-replay-mutations-2026-09-30/`](audit/selection-replay-mutations-2026-09-30/).
They show detection of these six mutations; they do not prove detection of every
possible evidence forgery.

`scripts/validate_replay_mutations.py` repeats the unchanged control and six
negative treatments on temporary copies in CI. Its outputs join the saved-replay
artifact, with validator/report digests and zero model calls.

## Routing and prompt budget

The [offline routing diagnosis](audit/laya-routing-diagnosis-2026-09-30/gooo-laya-routing-diagnosis-20260930.md)
found that the installed detector counts the `CI` acronym as Italian `ci`.
Changing only that marker in memory switched three of four exact-context states
to English; no inference was run for that ablation. The actual multilingual
requests fit their default token budget. Encoding the same full context for the
English default would truncate all four states, so pinning a model alone would
introduce a separate input-length difference.

The installed HTTP endpoint supports explicit `model: "english"` or
`model: "multilingual"` routing. A future comparison can log both compiler and
upstream request bytes through an audited adapter, pin each model, and keep a
compact failure-summary arm separate from the original full-context arm.
Provenance hashes remain in the local evidence record when omitted from a compact
model prompt. First calls and warm repeats need separate measurements. These
follow-up treatments are planned, not counted as measured model calls.

## Reproduction

The GitHub Actions workflow checks out the exact compiler revision, runs offline privacy/provenance regression checks, builds `cmd/gooo`, and runs the training-only probe. The CI artifact is named `ci-context-probe-<workflow-commit>` and contains raw evidence and receipts. A passing workflow means the expected test failures and bindings were verified; it does not mean that Gooo's internal search evaluator has become an authority for external CI. A separate read-only replay workflow validates any saved Laya study without contacting a provider.
