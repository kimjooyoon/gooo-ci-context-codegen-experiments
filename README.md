# Gooo CI failure context experiment

This public experiment captures bounded, reproducible Go test failures from Gooo-emitted bodies. The current phase uses four primary IR-search intents (`clamp`, `absolute`, `piecewise`, and `compound-precedence`) and intentionally selects the first declared `identity` expression with `max_attempts: 1`. The workflow passes only when the emitted source compiles, every declared training case is observed, and the expected mismatches are independently reproduced by Go tests.

The compiler is rebuilt in CI from `kimjooyoon/meta-ontology-go` at `29d44bc778d85aee03b9af500bd83dc98f368189`. Each plan and artifact contains training cases only. The workflow has no Laya endpoint or API key, does not call a model, and does not download models. It records the raw body-codegen output, emitted Go, raw failing test output, and a receipt binding compiler revision, fixture, plan, activity, candidate, source, and training suite hashes. The test harness is written for verification; only `emitted.go` is attributed to Gooo.

The test failures are expected evidence, not a broken default check. They show finite training mismatches for four declared examples and do not claim that the selected expression is wrong over every possible input. These finite training denominators are reported separately; there is no 100% semantic or holdout claim in this cohort.

## Frozen Laya selection design

For each intent, a later model-selection phase may compare three treatments:

1. **No context:** pass the original bounded intent and unchanged training plan.
2. **Exact matching failed-CI context:** append a compact, explicitly labeled failure summary to `intent`, only after matching the captured fixture, activity, compiler revision, plan, and source digests. Keep the appended text at most 900 Unicode code points and the full `intent` at most the compiler's 2,000-code-point limit. Include only training failures and hashes; never append any other case set.
3. **Rejected stale-source context:** present a context record whose source digest differs, reject it before injection, and make the choice request with the unchanged base intent. This is a rejection control, not a different model input.

The compiler revision used by this historical phase (`29d44bc`) exposes no structured external-context field, and its decoder rejects unknown fields. Therefore its matching-context treatment appends a bounded summary to the natural-language `intent`, not a typed compiler input or authenticated proof. The wrapper validates compiler, fixture, activity, plan, training suite, candidate, and emitted-source bindings before building that plan. It rejects a stale source binding before injection.

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
model prompt. First calls and warm repeats need separate measurements. The
historical routing diagnosis made no further inference calls.

## Native model pins and compact feedback study

The follow-up uses Gooo source `bb5c1ec2f81cbfb17ac6fb2f7a9e1d7b67168e7f`
([compiler PR](https://github.com/kimjooyoon/meta-ontology-go/pull/1099)).
`provider_model` binds an explicit route to the typed request and receipt;
`prompt_profile: "compact"` changes packaging independently of feedback;
`external_training_feedback` accepts source- and training-suite-bound advisory
observations. The compact model input carries failed training triples and counts,
while provenance hashes stay in local receipts. Gooo still evaluates and
typechecks the selected expression after the choice.

The planned comparison has four existing intents, three arms (legacy input,
compact input, compact input with actual earlier CI failures), two explicitly
pinned models, and three repeats: **72 measured calls plus two separate warmups**.
These are 24 distinct intent/arm/model cells, not 72 independent functional
intents. Both compact arms share the same question instructions. All plans and
order are frozen before inference. A separate 24-template local mock preflight
checks the exact outgoing protocol and cached tokenizer budgets; those calls
are not model inference. The known finite oracle is reused only for
postselection scoring and does not establish fresh unseen generalization.

The [final design](pinned-context-design/study-design.json) is frozen at SHA-256
`00da5cae3ee097870a66320ae18cf3fcd510652a369eb8eef31b5e6b4c5fb896`.
All 24 exact wire templates passed the cached-tokenizer preflight without state
truncation: 272–415 tokens for English (512-token budget), and 244–416 for
multilingual (1,024-token budget). Source-bound design validation passed with
zero model inference calls. Model outcomes, timings, and memory use are counted
only after raw captures and independent compiled-Go validation are saved.
Failed invocations remain visible, and request digests, model routes, and actual
receipts are checked separately.

A [superseded model-free preparation](pinned-context-design/superseded-preparation-v1/)
records a retention failure: its original frozen design JSON was not preserved.
Its digest, raw mock exchanges, and 74 plan files remain available; the plans are
byte-identical to the final design. That preparation made zero inference calls
and is excluded from measured results.

## Reproduction

The GitHub Actions workflow checks out the exact compiler revision, runs offline privacy/provenance regression checks, builds `cmd/gooo`, and runs the training-only probe. The CI artifact is named `ci-context-probe-<workflow-commit>` and contains raw evidence and receipts. A passing workflow means the expected test failures and bindings were verified; it does not mean that Gooo's internal search evaluator has become an authority for external CI. A separate read-only replay workflow validates any saved Laya study without contacting a provider.
