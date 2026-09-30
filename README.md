# Gooo CI failure context probe

This public experiment captures bounded, reproducible Go test failures from Gooo-emitted bodies. The current phase uses four primary IR-search intents (`clamp`, `absolute`, `piecewise`, and `compound-precedence`) and intentionally selects the first declared `identity` expression with `max_attempts: 1`. The workflow passes only when the emitted source compiles, every declared training case is observed, and the expected mismatches are independently reproduced by Go tests.

The compiler is rebuilt in CI from `kimjooyoon/meta-ontology-go` at `29d44bc778d85aee03b9af500bd83dc98f368189`. Each plan and artifact contains training cases only. The workflow has no Laya endpoint or API key, does not call a model, and does not download models. It records the raw body-codegen output, emitted Go, raw failing test output, and a receipt binding compiler revision, fixture, plan, activity, candidate, source, and training suite hashes. The test harness is written for verification; only `emitted.go` is attributed to Gooo.

The test failures are expected evidence, not a broken default check. They show finite training mismatches for four declared examples and do not claim that the selected expression is wrong over every possible input. These finite training denominators are reported separately; there is no 100% semantic or holdout claim in this cohort.

## Planned later selection treatments

For each intent, a later model-selection phase may compare three treatments:

1. **No context:** pass the original bounded intent and unchanged training plan.
2. **Exact matching failed-CI context:** append a compact, explicitly labeled failure summary to `intent`, only after matching the captured fixture, activity, compiler revision, plan, and source digests. Keep the appended text at most 900 Unicode code points and the full `intent` at most the compiler's 2,000-code-point limit. Include only training failures and hashes; never append any other case set.
3. **Rejected stale-source context:** deliberately present a context record whose source or fixture digest differs, then have the external experiment wrapper reject it before any provider request. This is a rejection control, not a model choice treatment.

The current `IRBodySearchPlan` exposes no structured external-context field, and its decoder rejects unknown fields. Therefore the matching-context treatment would be an append to the natural-language `intent`, not a typed compiler input or authenticated proof. The wrapper must validate provenance before building that plan. No later model requests have been made.

## Reproduction

The GitHub Actions workflow checks out the exact compiler revision, builds `cmd/gooo`, and runs the offline training-only probe. The CI artifact is named `ci-context-probe-<workflow-commit>` and contains raw evidence and receipts. A passing workflow means the expected test failures and bindings were verified; it does not mean that Gooo's internal search evaluator has become an authority for external CI.
