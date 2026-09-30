# Laya routing diagnosis for the CI-context selection audit

**Scope.** Read-only analysis of the saved 12-request audit at [`laya-selection-2026-09-30`](</Users/alice/meta-go/research/metaprogramming/gooo-ci-context-codegen-experiments/audit/laya-selection-2026-09-30/report.md>). I did not run a model, compiler, or download; tokenizer counts came from the already cached snapshot with Hugging Face offline flags. Raw audit files were not changed.

## Routing control is available on the HTTP endpoint

All 12 captured `/v1/systemone` POST bodies had only `state` and `questions`; none sent `model` or `lang`. Four exact CI-context calls auto-routed to `multilingual/it`; the eight no-context or rejected-stale-context calls routed to `english/en`. The stale-rejected request bodies byte-match their no-context counterparts by intent.

The installed Laya 0.3.21 HTTP handler supports a top-level `model` field. It resolves `body.model` and forwards it as `Router.predict(model=...)`; `Router._route` gives an explicit model precedence over language detection. `english` and `multilingual` are supported checkpoint names. The handler does not read or pass a `lang` field. The separate `laya` CLI supports `--model` and `--lang`, and the Python router accepts `lang=`, but those are different request surfaces. The HTTP endpoint also accepts `max_len` and `head_max_len` overrides.

**Practical follow-up:** an audit proxy can add `model: "english"` or `model: "multilingual"` while leaving the compiler-produced `state` and `questions` unchanged. Verify the response's `routing.model` on every request.

## The detector sees the context text

The state contains a serialized compiler request, with the exact CI-failure context appended inside its `intent`. Laya’s language detector analyzes state text using a deterministic stopword/diacritic heuristic. Its Italian stopword set includes `ci`; the detector lowercases words, so the acronym `CI` can count as Italian `ci`.

I reran only that installed deterministic detector in memory on the four saved exact-context states, replacing the marker phrase “verified CI training failure context” with neutral “verified build training failure context.” Three of four changed from Italian to English. The absolute case stayed Italian; its remaining Italian evidence includes `ed`. This supports a routing-trigger diagnosis, with a residual case to investigate. It does not show an effect on model quality.

## Token length and truncation

I loaded the cached English and multilingual tokenizers only and applied the installed `Agent._to_internal` and `laya.common.build_sequence` path to each saved state/question. This includes Laya’s actual question and option formatting and state-truncation behavior.

| Saved arm and actual route | Request body bytes | State tokens | Full sequence | Default budget | State truncation |
|---|---:|---:|---:|---:|---:|
| No context or stale context, English (n=8) | 1,142–1,261 | 254–294 | 321–361 | 512 | 0/8 |
| Exact CI context, multilingual (n=4) | 2,113–2,298 | 733–820 | 803–892 | 1,024 | 0/4 |

The four exact-context states were also encoded with the English tokenizer as a cross-check: they contain 670–732 state tokens, so English’s 512-token default keeps only 443–445 and truncates all four states. Thus actual multilingual calls retained the complete state, while an English-pinned call at its default budget would receive a truncated tail. Those are different input conditions and should be reported explicitly in a fixed-checkpoint comparison.

Each exact `intent` contains four 64-character SHA-256 strings. Under the multilingual tokenizer, the hashes alone cost 224–229 tokens out of 430–466 intent tokens (about 49–53%). The audit’s full exact requests are still below the multilingual 1,024-token default, but the hash text consumes about half the intent’s token budget. A compact provenance summary could test a separate, explicitly labeled arm that fits both default contexts; it should not be silently substituted for the original exact context.

## Timing: first multilingual call versus warm calls

The first multilingual-routed request was the third selection invocation: 2,717.794 ms proxy latency (2,720.351 ms resolver latency). The next three multilingual requests took 247.740–290.661 ms (mean 268.228 ms). Health reported only English loaded before the run and both English and multilingual afterward. This sequence is consistent with a one-time lazy multilingual checkpoint/tokenizer load, but the audit did not time loading separately from tokenization, CPU forward execution, or other first-call work.

The first English request took 387.809 ms; the next seven English calls averaged 242.106 ms. English was already loaded before the run, so this is evidence of call-order/startup effects without an isolated cause. There was no warmup. Runner source clarifies the timestamp phases: `scripts/run_laya_selection.py` records the capture-phase `elapsed_ms_including_health_and_sampler` and initially sets `completed_utc` at line 892, then report finalization overwrites `completed_utc` at line 755 (`finalize_existing`) or line 979 (`main`). The saved `completed_utc` therefore marks finalization, not the raw-capture endpoint. The runner separately recorded 10,861.549 ms for the capture phase, including health checks and sampling. The recorded start/completed UTC pair cannot establish capture wall time; the monotonic capture-phase duration and per-request event durations have distinct scopes.

## What the results can support

All exact-context cases changed both the input and the selected checkpoint. The saved choice differences therefore cannot attribute an effect to the context alone. Each intent-treatment cell has n=1, and finite training/holdout outcomes do not establish general-domain accuracy or robustness.

A compact next comparison is four intents × two context arms (none/exact) × two explicitly pinned checkpoints, with randomized request order and repeated cells if the call budget permits. Keep the original full-context treatment distinct from any shorter provenance summary. For latency, measure first pinned calls on fresh servers separately from same-server warm repeats, record loaded-model health before/after, and report only timers actually captured.

## Installed sources and versions

- Runner timestamp phases: [`run_laya_selection.py`](</Users/alice/meta-go/research/metaprogramming/gooo-ci-context-codegen-experiments/scripts/run_laya_selection.py:892>) records capture duration; reporting/finalization replaces `completed_utc` at lines 755 or 979.
- Runtime: `laya` 0.3.21 at `/tmp/meta-ontology-go-laya-venv-20260930/lib/python3.11/site-packages/laya`.
- Model/tokenizer snapshot: `convaiinnovations/laya`, revision `55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851`.
- HTTP model resolution and prediction dispatch: [`serve.py`](</tmp/meta-ontology-go-laya-venv-20260930/lib/python3.11/site-packages/laya/serve.py:97>) and [`serve.py`](</tmp/meta-ontology-go-laya-venv-20260930/lib/python3.11/site-packages/laya/serve.py:464>).
- Explicit model routing precedence: [`router.py`](</tmp/meta-ontology-go-laya-venv-20260930/lib/python3.11/site-packages/laya/router.py:618>).
- Detector heuristic and Italian stopwords: [`lang.py`](</tmp/meta-ontology-go-laya-venv-20260930/lib/python3.11/site-packages/laya/lang.py:1>) and [`lang.py`](</tmp/meta-ontology-go-laya-venv-20260930/lib/python3.11/site-packages/laya/lang.py:107>).
- Token sequence assembly and truncation: [`common.py`](</tmp/meta-ontology-go-laya-venv-20260930/lib/python3.11/site-packages/laya/common.py:135>) and [`agent.py`](</tmp/meta-ontology-go-laya-venv-20260930/lib/python3.11/site-packages/laya/agent.py:752>).
- Machine-readable per-invocation counts and evidence notes: [`gooo-laya-routing-diagnosis-20260930.json`](/tmp/gooo-laya-routing-diagnosis-20260930.json).
