# Finite tests that distinguish candidate fills

The saved study uses the same existing cases and model responses. This analysis separates cases whose frozen oracle outputs vary across the three declared candidates from cases where all candidates produce the same output.

| Treatment | Training, all | Holdout, all | Holdout, candidate-discriminating | Holdout, candidate-invariant |
|---|---:|---:|---:|---:|
| exact_matching_failed_ci_context | 3/13 | 13/16 | 1/4 | 12/12 |
| no_context | 6/13 | 14/16 | 2/4 | 12/12 |
| rejected_stale_source_context | 6/13 | 14/16 | 2/4 | 12/12 |

Across the four distinct intents, only 4 of the 16 holdout cases distinguish the declared candidates; the other 12 pass for every candidate. The unchanged-intent arms pass 2/4 discriminating cases (50%), and the exact-CI-context arm passes 1/4 (25%). These are finite, candidate-relative observations. The 14/16 and 13/16 overall scores also contain the 12 invariant successes.

For absolute value, `identity` and `negate` have identical outputs on the entire four-case holdout suite: the sole negative holdout input is MinInt64, whose signed negation wraps to itself. Training distinguishes those candidates. Therefore holdout success alone cannot identify the intended fill.

Counterfactual outputs are from the hash-pinned independent finite oracle. Selected candidate totals are checked against the saved independently compiled Go observations. An invariant test can still execute the filled branch; this metric does not measure branch coverage. It proves neither full-domain correctness nor a causal context effect (n=1 per cell, with different routing).
