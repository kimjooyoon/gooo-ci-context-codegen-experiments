# Latency and choice breakdown

This analysis reads the preserved capture and independent replay receipts. It made **0 model calls** and writes only these two analysis files. The 72 measured calls are separate from two warmups.

## Scope and validation

- **Measured design:** 72 calls = 4 intents × 3 arms × 2 provider models × 3 repeats, producing **24 intent/arm/model cells** with three calls each.
- **Warmups:** two calls, one per model, excluded from measured summaries.
- **Capture:** all 74 invocations have one raw Laya POST. All request/response hashes match; HTTP status counts: `{'200': 74}`.
- **Independent replay:** PASS; all 74 replay runs followed expected test-exit protocol. Typed request hashes, routes, and receipts passed for all 74.
- **Original summary bookkeeping:** original report remains `PARTIAL_CAPTURE_OR_VALIDATION` with feedback-match {'passed': 0, 'total': 72}. Raw evidence and its receipt-bound derived correction verify 72/72; original report and existing scores are unchanged.

## Arm and model summaries

Each row has 12 calls. POST time is the captured loopback exchange duration. CLI time is compiler process active wall time. Brackets show min–max; summaries use medians.

| Arm | Model | Calls | POST ms median [range] | CLI ms median [range] | Candidate IDs across intents | Training | Reused holdout |
|---|---|---:|---:|---:|---|---:|---:|
| compact_external_feedback | english | 12 | 269.700 [256.162–286.399] (n=12) | 278.640 [264.620–294.566] (n=12) | seven 3, zero 9 | 18/39 | 42/48 |
| compact_external_feedback | multilingual | 12 | 100.507 [93.674–115.160] (n=12) | 108.861 [101.975–123.546] (n=12) | identity 9, zero 3 | 9/39 | 42/48 |
| compact_no_feedback | english | 12 | 206.437 [195.361–225.559] (n=12) | 214.716 [204.585–233.742] (n=12) | seven 3, zero 9 | 18/39 | 42/48 |
| compact_no_feedback | multilingual | 12 | 78.161 [73.339–90.966] (n=12) | 86.520 [81.535–100.248] (n=12) | hundred 3, negate 6, seven 3 | 30/39 | 45/48 |
| legacy_no_feedback | english | 12 | 238.501 [231.027–256.575] (n=12) | 246.896 [239.395–267.175] (n=12) | seven 3, zero 9 | 18/39 | 42/48 |
| legacy_no_feedback | multilingual | 12 | 93.438 [91.459–104.936] (n=12) | 101.888 [99.604–113.931] (n=12) | hundred 3, identity 3, negate 6 | 21/39 | 42/48 |

Candidate IDs vary by intent; cross-intent counts are descriptive. Per-intent repeated choices follow.

## The 24 intent × arm × model cells

| Intent | Arm | Model | Choices (replicates 1 / 2 / 3) | POST ms median [range] | CLI ms median [range] | Training | Reused holdout |
|---|---|---|---|---:|---:|---:|---:|
| absolute | compact_external_feedback | english | zero / zero / zero | 274.643 [273.691–275.704] (n=3) | 283.246 [282.252–284.019] (n=3) | 0/9 | 9/12 |
| absolute | compact_external_feedback | multilingual | identity / identity / identity | 102.573 [100.887–105.846] (n=3) | 110.510 [109.057–114.765] (n=3) | 0/9 | 12/12 |
| absolute | compact_no_feedback | english | zero / zero / zero | 211.433 [210.330–212.583] (n=3) | 219.596 [218.523–220.878] (n=3) | 0/9 | 9/12 |
| absolute | compact_no_feedback | multilingual | negate / negate / negate | 81.349 [81.221–81.423] (n=3) | 89.594 [89.462–89.676] (n=3) | 9/9 | 12/12 |
| absolute | legacy_no_feedback | english | zero / zero / zero | 241.002 [239.872–241.071] (n=3) | 249.392 [248.157–249.422] (n=3) | 0/9 | 9/12 |
| absolute | legacy_no_feedback | multilingual | negate / negate / negate | 94.124 [93.786–95.266] (n=3) | 102.382 [101.837–103.475] (n=3) | 9/9 | 12/12 |
| clamp | compact_external_feedback | english | zero / zero / zero | 263.335 [259.114–265.708] (n=3) | 272.998 [268.897–275.028] (n=3) | 9/9 | 12/12 |
| clamp | compact_external_feedback | multilingual | zero / zero / zero | 94.134 [93.674–94.583] (n=3) | 102.521 [101.975–102.878] (n=3) | 9/9 | 12/12 |
| clamp | compact_no_feedback | english | zero / zero / zero | 200.329 [199.276–200.617] (n=3) | 208.368 [207.906–208.807] (n=3) | 9/9 | 12/12 |
| clamp | compact_no_feedback | multilingual | negate / negate / negate | 74.418 [74.230–75.101] (n=3) | 83.508 [82.460–83.578] (n=3) | 0/9 | 9/12 |
| clamp | legacy_no_feedback | english | zero / zero / zero | 233.624 [233.301–237.130] (n=3) | 242.150 [241.547–245.635] (n=3) | 9/9 | 12/12 |
| clamp | legacy_no_feedback | multilingual | negate / negate / negate | 92.932 [92.564–93.089] (n=3) | 101.119 [100.633–101.252] (n=3) | 0/9 | 9/12 |
| compound-precedence | compact_external_feedback | english | zero / zero / zero | 284.115 [283.954–286.399] (n=3) | 292.547 [292.245–294.566] (n=3) | 0/12 | 9/12 |
| compound-precedence | compact_external_feedback | multilingual | identity / identity / identity | 114.197 [113.796–115.160] (n=3) | 122.805 [121.961–123.546] (n=3) | 0/12 | 9/12 |
| compound-precedence | compact_no_feedback | english | zero / zero / zero | 220.308 [215.491–225.559] (n=3) | 228.711 [223.460–233.742] (n=3) | 0/12 | 9/12 |
| compound-precedence | compact_no_feedback | multilingual | hundred / hundred / hundred | 90.263 [89.601–90.966] (n=3) | 99.537 [99.124–100.248] (n=3) | 12/12 | 12/12 |
| compound-precedence | legacy_no_feedback | english | zero / zero / zero | 255.957 [253.834–256.575] (n=3) | 266.086 [264.806–267.175] (n=3) | 0/12 | 9/12 |
| compound-precedence | legacy_no_feedback | multilingual | hundred / hundred / hundred | 103.653 [102.949–104.936] (n=3) | 111.774 [110.886–113.931] (n=3) | 12/12 | 12/12 |
| piecewise | compact_external_feedback | english | seven / seven / seven | 256.946 [256.162–259.588] (n=3) | 268.322 [264.620–270.365] (n=3) | 9/9 | 12/12 |
| piecewise | compact_external_feedback | multilingual | identity / identity / identity | 99.779 [99.439–100.127] (n=3) | 108.029 [107.842–108.665] (n=3) | 0/9 | 9/12 |
| piecewise | compact_no_feedback | english | seven / seven / seven | 197.876 [195.361–202.543] (n=3) | 206.435 [204.585–210.910] (n=3) | 9/9 | 12/12 |
| piecewise | compact_no_feedback | multilingual | seven / seven / seven | 73.781 [73.339–73.820] (n=3) | 81.950 [81.535–82.034] (n=3) | 9/9 | 12/12 |
| piecewise | legacy_no_feedback | english | seven / seven / seven | 231.051 [231.027–232.846] (n=3) | 239.538 [239.395–241.993] (n=3) | 9/9 | 12/12 |
| piecewise | legacy_no_feedback | multilingual | identity / identity / identity | 92.716 [91.459–93.062] (n=3) | 101.527 [99.604–101.938] (n=3) | 0/9 | 9/12 |

## Process samples from `ps`

RSS is the per-invocation largest sampled resident size. CPU seconds are coarse deltas in cumulative process CPU time; CPU percent is the maximum sampled process value. `n` counts observed values.

**These values are not instantaneous host CPU increases.** CPU percent is sparse and process-level; it can exceed 100% for multithreaded work. RSS is sampled memory, not a per-request memory delta. Sampling was nominally every 120 ms, longer than many CLI runs; exact per-call CPU attribution is not established.

| Arm | Model | CLI RSS peak MiB median [range] | CLI CPU s median [range] (missing) | CLI sampled CPU% median [range] | Laya RSS peak MiB median [range] | Laya CPU s median [range] | Laya sampled CPU% median [range] |
|---|---|---:|---:|---:|---:|---:|---:|
| compact_external_feedback | english | 14.80 [14.67–15.36] | 0.005 [0.000–0.010] (missing 0) | 1.300 [1.000–1.400] (missing 0) | 3383.95 [3359.06–3384.67] | 0.600 [0.570–0.650] (missing 0) | 206.550 [198.900–214.900] (missing 0) |
| compact_external_feedback | multilingual | 1.99 [1.84–3.16] | n/a (missing 12) | 0.000 [0.000–0.000] (missing 0) | 3384.54 [3302.95–3384.67] | 0.215 [0.170–0.270] (missing 0) | 197.550 [162.500–208.600] (missing 0) |
| compact_no_feedback | english | 14.69 [14.52–14.88] | 0.000 [0.000–0.010] (missing 0) | 1.300 [0.700–1.400] (missing 0) | 3383.66 [3288.94–3384.67] | 0.430 [0.410–0.480] (missing 0) | 199.250 [152.500–207.000] (missing 0) |
| compact_no_feedback | multilingual | 2.11 [1.84–2.34] | n/a (missing 12) | 0.000 [0.000–0.000] (missing 0) | 3384.64 [3302.95–3384.67] | 0.130 [0.120–0.160] (missing 0) | 200.450 [169.200–206.500] (missing 0) |
| legacy_no_feedback | english | 14.72 [14.56–15.22] | 0.000 [0.000–0.010] (missing 0) | 1.300 [1.300–1.500] (missing 0) | 3384.62 [3332.66–3384.67] | 0.520 [0.490–0.570] (missing 0) | 200.450 [193.300–212.200] (missing 0) |
| legacy_no_feedback | multilingual | 2.29 [1.84–2.83] | n/a (missing 12) | 0.000 [0.000–0.000] (missing 0) | 3384.02 [3334.08–3384.67] | 0.185 [0.160–0.240] (missing 0) | 199.450 [172.200–211.000] (missing 0) |

Runner configured threads: 4; maximum overlapping measured CLI intervals: 1. Warmups are excluded.

## Warmups, separate

| Model | POST ms | CLI ms | Choice | Training | Reused holdout |
|---|---:|---:|---|---:|---:|
| english | 537.628 | 586.492 | zero | 3/3 | 4/4 |
| multilingual | 101.105 | 109.432 | negate | 0/3 | 3/4 |

## Failure types and outcomes

- **Operational failures:** 0 capture, HTTP, CLI, route, receipt, or replay-protocol failures; one POST per invocation and no retries.
- **Training-choice failures:** 36/72 choices missed the complete training suite; 114/234 cases passed. Each selected expression passed all or failed all training cases in this finite cohort.
- **Reused-holdout misses:** 33/72 choices missed a holdout case; 255/288 cases passed. 3 choices failed training but passed all reused holdout cases, so this holdout does not distinguish every wrong choice.
- **Interpretation limit:** holdout cases are reused benchmark examples, not fresh unseen cases. These results do not establish generalization.
- **Legacy summary bookkeeping:** original aggregate feedback-match is 0/72; raw evidence and a separate receipt-bound correction verify 72/72. Original report and existing scores remain unchanged.

## Follow-up experiments

1. **Increase balanced repetitions and randomize blocks.** Run at least 10 repeats per each of the 24 intent/arm/model cells; balance arm/model order within each intent block and retain per-call timings. Three repeats per cell are descriptive and do not estimate request-level variability precisely.
2. **Use fresh concealed evaluation cases.** Precommit fresh held-back cases per intent before capture and exclude all evaluation values from prompts and feedback; score only after choices finish. Current holdout cases are reused benchmarks, and three choices that missed training passed all holdout cases.
3. **Ablate feedback content and trust boundaries.** Compare no feedback with source-bound exact observations, failure-only observations, and verified corrective feedback while holding model, prompt, and cases fixed. External feedback had 27/78 training and 84/96 reused holdout passes, versus 48/78 and 87/96 for compact without feedback.
4. **Measure service and CLI timing at higher resolution.** Record monotonic request receipt, inference start/end, response write, and CLI start/exit; collect OS process counters after exit or sample every 20–25 ms. 120 ms sampling yields one to three snapshots for short CLI runs; multilingual CLI CPU deltas are missing.
5. **Counterbalance cold and warm model order.** Repeat warmups in both model orders and measure separate cold-start and warmed steady-state cohorts. There was one warmup per model; English warmup POST took 537.628 ms versus measured median 238.501 ms.
6. **Expand discriminating boundary cases.** Add multiple precommitted training and evaluation inputs that distinguish each distractor, especially boundaries and neighboring values. Three or four training inputs and four reused holdout cases make outcomes coarse.

## Evidence files

- Original report SHA-256: `1f079748a2ba2d4d75adc4f97b46fc3b090e726f7b587d193b8f1a1c28b29c74`.
- Derived corrected report SHA-256: `935ab92f0a9d92437a430e6bc85a8ce64f1fa0f7dda7ab5eef013413b698bb63`.
- Correction receipt SHA-256: `44119aeafcc5982f8b00549e614abe1e17615a6718f964fcf83bb4595ccb3f68`.
- Independent replay report SHA-256: `94f1f287239a5d882c0b08f6ff0aae619572da5aca2f8cae729d96413b1f8fe7` (decision `PASS`).
- Raw exchange refs are in `invocation-index.json`; process snapshots are in each invocation’s `resource.json` and `resource-samples.jsonl`.
