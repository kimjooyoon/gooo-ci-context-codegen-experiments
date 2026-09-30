# Read-only CI bottleneck audit

**Completed-run cohort observed:** 2026-09-30 05:33:11 UTC  
**Follow-on PR1098 snapshot:** 2026-09-30 05:42:12 UTC  
**Repository:** `kimjooyoon/meta-ontology-go`  
**Files:** `/tmp/gooo-ir-search-ci-cost-20260930.md` and `/tmp/gooo-ir-search-ci-cost-20260930.json`

This uses GitHub Actions run/job/step timestamps and the main branch protection record. No source, CI, or protection settings were changed; no Go commands, tests, or model calls were run. Job spans overlap; run wall time is reported separately.

## Run and critical-stage timings

All times are UTC on 2026-09-30. Each row's job spans are actual start/end timestamps. The race, semantic, and ordinary Go checks run concurrently. CI policy started within three seconds of race completion in every run; evidence, proof bundle, and failure summary followed sequentially.

| Run | Event / try | Run wall | Race job | Semantic job | CI policy | Evidence | Proof bundle | Failure summary |
|---|---|---:|---|---|---|---|---|---|
| [PR1095 source feature](https://github.com/kimjooyoon/meta-ontology-go/actions/runs/36667822960) | PR / 1 | 15m28s, 04:12:58–04:28:26 | 04:14:29–04:22:54 (8m25s) | 04:14:54–04:19:42 (4m48s) | 04:22:57–04:26:21 (3m24s) | 04:26:24–04:27:07 (43s) | 04:27:10–04:27:48 (38s) | 04:27:50–04:28:26 (36s) |
| [dev f53e661 push](https://github.com/kimjooyoon/meta-ontology-go/actions/runs/36669210680) | push / 1 | 13m15s, 04:31:15–04:44:30 | 04:31:26–04:39:25 (7m59s) | 04:32:45–04:37:38 (4m53s) | 04:39:28–04:42:14 (2m46s) | 04:42:17–04:43:02 (45s) | 04:43:05–04:43:44 (39s) | 04:43:48–04:44:29 (41s) |
| [PR1096 promotion dispatch](https://github.com/kimjooyoon/meta-ontology-go/actions/runs/36670266098) | dispatch / 1 | 11m17s, 04:45:19–04:56:36 | 04:45:23–04:51:39 (6m16s) | 04:45:23–04:51:29 (6m06s) | 04:51:42–04:54:08 (2m26s) | 04:54:13–04:55:07 (54s) | 04:55:10–04:55:58 (48s) | 04:56:01–04:56:35 (34s) |
| [PR1096 native PR CI](https://github.com/kimjooyoon/meta-ontology-go/actions/runs/36670267673) | PR / 2 | 13m12s, 04:56:47–05:09:59 | 04:56:51–05:05:01 (8m10s) | 04:56:52–05:02:52 (6m00s) | 05:05:04–05:07:44 (2m40s) | 05:07:47–05:08:36 (49s) | 05:08:39–05:09:18 (39s) | 05:09:21–05:09:59 (38s) |
| [PR1097 telemetry feature](https://github.com/kimjooyoon/meta-ontology-go/actions/runs/36671487306) | PR / 1 | 14m08s, 05:01:16–05:15:24 | 05:02:34–05:10:09 (7m35s) | 05:02:48–05:08:28 (5m40s) | 05:10:12–05:13:10 (2m58s) | 05:13:13–05:13:57 (44s) | 05:13:59–05:14:45 (46s) | 05:14:48–05:15:23 (35s) |
| [dev09 push](https://github.com/kimjooyoon/meta-ontology-go/actions/runs/36672639946) | push / 1 | 15m11s, 05:16:17–05:31:28 | 05:17:20–05:25:20 (8m00s) | 05:17:32–05:24:04 (6m32s) | 05:25:23–05:29:01 (3m38s) | 05:29:05–05:29:55 (50s) | 05:29:58–05:30:48 (50s) | 05:30:51–05:31:27 (36s) |

Race job elapsed time was 6m16s–8m25s (median 7m59.5s); semantic conformance was 4m48s–6m32s (median 5m50s). CI policy was 2m26s–3m38s, and its source metric receipts/feedback step took 51–88s. The evidence-to-failure-summary sequence spanned 2m02s–2m22s. After the race job ended, runs needed another 4m57s–6m08s to complete (median 5m10s). These are observed timings, not a causal explanation. The separate `go test` job took 3m09s–3m47s.

## Promotion wait and overlap

Native PR1096 attempt 1 ended `action_required` at 04:45:20Z with zero jobs. Attempt 2 began at 04:56:47Z and completed 05:09:59Z. The measured interval was **11m27s**; the exact approval timestamp was not available. Dispatch ran 04:45:19–04:56:36Z, overlapping most of this wait. Dispatch-start to native attempt-2 completion was 24m40s wall, so the wait must not be added to the dispatch duration.

PR1097 CI overlapped the native PR retry from 05:01:16Z through 05:09:59Z (8m43s). PR1097 completed at 05:15:24Z and merged at 05:16:14Z; dev09 CI began at 05:16:17Z. PR1095 opened 04:00:59Z, its observed canonical CI began 04:12:58Z, and it merged 04:31:13Z. The cause of the opening-to-run gap is not established by the run records.

## Repeated tree and protection

Each of the six successful runs had the same 12 job names: `go test -race`, `Semantic conformance`, `go test`, `gofmt`, `go vet`, `Language package execution`, `Feedback predecessor`, `COHERENCE boundary probe`, `CI policy`, `CI evidence`, `CI proof bundle`, and `CI failure summary`. The policy/evidence/proof/failure jobs had 33/15/18/12 steps respectively. The proof bundle repeated evidence lineage resolution, download and terminal-job collection, fixture reproduction, deterministic proof compilation/build, and provenance upload.

The COHERENCE probe had eight steps when run and zero when skipped; it was skipped on the f53 push, dispatch, and dev09 push, and ran on PR1095, the native promotion retry, and PR1097.

`main` protection was strict and required exactly six contexts: `CI policy`, `Semantic conformance`, `gofmt`, `go vet`, `go test`, `go test -race`. Required approving reviews remained zero. Guardian was absent from those contexts and these six observed authoritative job trees; this does not establish its status in every workflow.

## dev09 in-progress snapshot

At 05:30:27Z, run 36672639946 was still in progress: proof bundle had begun at 05:29:58Z, setup-go at 05:30:04Z, and later steps remained incomplete in that snapshot. It subsequently completed proof at 05:30:48Z and the full run successfully at 05:31:28Z. Both the incomplete sample and final duration are retained above.

## Efficiency experiments to consider

Recommendations only; no settings were changed.

1. On a disposable branch, compare cold and exact-SHA-keyed Go build-cache timing for the existing full race and semantic checks; keep both required checks unchanged while measuring.
2. Profile CI policy receipt/feedback and evaluate exact-commit workspace/artifact reuse while preserving lineage validation and fresh receipts.
3. Measure repeated checkout/materialization in evidence/proof jobs; test reducing duplicate preparation while retaining fresh evidence, proof reconstruction, and provenance.
4. Capture the promotion approval event time and evaluate a narrowly scoped trusted-workflow approval route; keep native PR CI and all six required contexts.

No unrelated app or workflow is assigned as a cause; the records do not show that it delayed these runs.


## Follow-on PR1098 snapshot (outside the six-run cohort)

A later promotion PR had a separate dispatch/native pair. Dispatch run [36673822112](https://github.com/kimjooyoon/meta-ontology-go/actions/runs/36673822112) began at 05:31:44Z and remained in progress at the 05:42:12Z sample (10m28s elapsed). Its race job ended 05:39:54Z and CI policy had started at 05:39:57Z but was not complete in the sample.

Native PR run [36673948424](https://github.com/kimjooyoon/meta-ontology-go/actions/runs/36673948424) attempt 1 ended `action_required` at 05:33:24Z with zero jobs. Attempt 2 started 05:35:08Z: an observed 1m44s from action-required completion to retry start, with the exact approval timestamp unavailable. At 05:42:12Z, attempt 2 remained in progress; race started 05:35:13Z and was still running, semantic conformance completed 05:41:38Z, and `go test` completed 05:37:41Z. Final run durations were not yet available. These dispatch and native runs overlapped; this snapshot is excluded from the six-run timing metrics above.
