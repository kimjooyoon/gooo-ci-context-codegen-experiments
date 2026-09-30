# Saved selection validator: negative mutation checks

The unchanged control passed all twelve saved-source replays. Each altered copy was rejected by the stated validation check.

| Temporary treatment | Expected rejection stage | Outcome | Validator message |
|---|---|---|---|
| unchanged-control | unchanged control | PASS | control passed |
| typed_receipt_sha_changed | recomputed typed request hash | FAIL | typed Laya request SHA-256 receipts differ from independent Go JSON reconstruction |
| emitted_go_copy_changed | saved postselection source binding | FAIL | 01-piecewise-rejected_stale_source_context: saved postselection Go/oracle vectors differ from raw source and pinned cases |
| finite_score_relabeled | independent finite score comparison | FAIL | 01-piecewise-rejected_stale_source_context: report row does not match raw choice and independent finite scores |
| stale_gate_relabeled_accepted | derived context-gate consistency | FAIL | 01-piecewise-rejected_stale_source_context: report row does not match raw choice and independent finite scores |
| request_artifact_missing | raw proxy artifact presence | FAIL | missing saved artifact: /private/var/folders/t4/mkd0zjsn2hj2mqwsjftxcfmc0000gn/T/gooo-selection-replay-mutations-20260930-8c5bc2_c/request_artifact_missing/repo/audit/laya-selection-2026-09-30/proxy/requests/0001.request.raw |
| response_choice_mismatch | provider response/choice binding | FAIL | 01-piecewise-rejected_stale_source_context: raw choice response/event does not name a declared candidate |

No Laya or model calls, Gooo CLI calls, network access, or model downloads were used. Temporary evidence copies and replay output directories were removed after each case.
