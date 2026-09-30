# Laya selection with CI failure context

Run `laya-selection-2026-09-30` used compiler `29d44bc778d85aee03b9af500bd83dc98f368189` and Laya revision `55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851` on CPU with four threads, offline.

The randomized study captured 12 selection POSTs for 12 single-attempt invocations. Each intent-treatment cell has n=1.

The exact-context arm appended a bounded, provenance-checked training failure summary to `intent`. The stale-source context was rejected before injection, and that arm used the original intent. Holdout cases were opened only after all selection request and response bytes were saved.

| Intent | Treatment | Choice | Training (finite) | Holdout (post-selection finite) | Route/language | Resolver ms | Proxy ms | CLI active wall ms |
|---|---|---|---:|---:|---|---:|---:|---:|
| clamp | exact_matching_failed_ci_context | negate | 0/3 | 3/4 | multilingual/it | 2720.351 | 2717.794 | 2726.453 |
| clamp | rejected_stale_source_context | zero | 3/3 | 4/4 | english/en | 239.946 | 237.712 | 246.068 |
| clamp | no_context | zero | 3/3 | 4/4 | english/en | 235.752 | 233.401 | 241.661 |
| absolute | rejected_stale_source_context | zero | 0/3 | 3/4 | english/en | 243.548 | 241.166 | 249.658 |
| absolute | exact_matching_failed_ci_context | negate | 3/3 | 4/4 | multilingual/it | 268.561 | 266.282 | 274.849 |
| absolute | no_context | zero | 0/3 | 3/4 | english/en | 242.843 | 240.416 | 248.794 |
| piecewise | rejected_stale_source_context | seven | 3/3 | 4/4 | english/en | 390.515 | 387.809 | 402.197 |
| piecewise | no_context | seven | 3/3 | 4/4 | english/en | 233.274 | 230.745 | 239.586 |
| piecewise | exact_matching_failed_ci_context | identity | 0/3 | 3/4 | multilingual/it | 250.134 | 247.740 | 256.630 |
| compound-precedence | no_context | zero | 0/4 | 3/4 | english/en | 260.617 | 257.045 | 266.936 |
| compound-precedence | rejected_stale_source_context | zero | 0/4 | 3/4 | english/en | 256.643 | 254.260 | 262.747 |
| compound-precedence | exact_matching_failed_ci_context | identity | 0/4 | 3/4 | multilingual/it | 292.954 | 290.661 | 298.837 |

Reported routing was english/en: 8, multilingual/it: 4. Routing was not held constant across treatments, so choice differences may also reflect routing changes.

Training and holdout scores use separate finite denominators. These n=1 observations do not establish full-domain correctness or a general speed effect. Per-invocation resource samples, raw exchanges, receipts, and independent compiled Go runs are stored beside this report.

The [candidate-discrimination analysis](case-discrimination.md) separates candidate-invariant successes from holdout cases that distinguish the three declared expressions. It is a finite, candidate-relative view and does not change the emitted-body scores above.
