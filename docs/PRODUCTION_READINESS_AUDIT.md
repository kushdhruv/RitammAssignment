# Production-readiness audit

## Scope and evidence

This audit covers the take-home workflow defined in `Ritamm_TakeHome_Brief.pdf`: local A2A 0.3 agents, an OpenAI-compatible classification call, deterministic review/NAV calculation, and the supplied single-fund pack. It is not a claim that this repository is a production multi-tenant fund-accounting platform.

The research basis is recorded in [RESEARCH_AUDIT.md](RESEARCH_AUDIT.md): Google SRE's data-integrity and idempotency guidance, the A2A 0.3 specification, OpenAI's structured-output guidance, Anthropic's evaluation guidance, and OpenTelemetry's tracing guidance.

## Verified controls

| Risk | Control now in the repository | Verification |
| --- | --- | --- |
| Provider rejects an anonymous Python client | The classifier sends an explicit `User-Agent`, `Accept`, authorization and content-type headers. | Mocked transport regression test asserts the request headers. |
| Model output truncates a large ledger | Calls are limited to eight transactions; every batch must return the exact unique ID set or it is retried and then fails closed. | 39-row regression test observes five `8,8,8,8,7` requests; incomplete-batch retry test. |
| Model invents LP/company facts | Prompt includes a Q3-derived LP directory and approved liability context; deterministic code validates LP codes and transaction-text evidence. | Unknown LP and ungrounded-company test cases. |
| Q3 liability settlement enters Q4 expenses | Q3 payees and balances are supplied as a narrow context; eligible payments are deterministically canonicalized to `accrual_settlement`, with an audit reason; cumulative settlements cannot exceed each opening liability. | Sample Coral/Harbourside test plus over-settlement and new-invoice boundary tests. |
| Unexpected agent failure is hidden | The HTTP server logs an exception traceback with request ID; the wire response remains a generic JSON-RPC internal error. | Loopback HTTP regression test captures the server error log and safe `-32603` response. |
| `pytest` imports an unrelated installed `app` package | Root `pytest.ini` places this repository on the test path and declares `tests/`. | `pytest --collect-only` and full pytest run. |
| Bad accounting data reaches final output | Decimal-only money, source amount validation, per-row reconciliation, A2A multiset matching, mark validation and a cent-exact NAV bridge stop publication. | Offline unit, integration, randomized reconciliation and end-to-end tests. |

## Release decision

The supplied take-home workflow is suitable to submit after a successful normal-mode smoke test with a **newly generated** LLM credential. It fails closed on untrusted LLM output and accounting mismatches; it does not silently publish an incomplete close.

An authorized normal-mode provider run completed on 1 November 2026 using the local replacement credential. It classified all 39 transactions in bounded batches, completed the four A2A log events, resolved LP10, and produced `nav_q4` of `169764039.25` with a `0.00` NAV-bridge difference. The credential and raw provider response were not recorded in this repository.

It is **not** production-ready for a real multi-fund financial platform. The remaining material gaps are deliberate scope boundaries:

- no TLS, agent authentication/authorization, tenant isolation, or durable idempotent task store;
- no encrypted object store, retention policy, backup/restore exercise, or human exception workflow;
- no provider-backed LLM accuracy evaluation on a labelled ambiguous transaction corpus; and
- PDF extraction is designed for the supplied statement layout. A new bank template fails closed only when its reconciliation/control checks detect the error; it needs a supported-template registry and review queue for broader rollout.

The offline transport tests verify request construction, retries, rate-limit handling, and failure containment. One successful provider run is a smoke test, not evidence of general model accuracy; a labelled ambiguous transaction corpus remains necessary before a model/provider change is approved.
