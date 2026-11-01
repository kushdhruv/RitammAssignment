# GPF Q4 2022 Close

This repository closes Q4 2022 for The Greatness PE Fund, L.P. It runs two local agents over A2A 0.3 JSON-RPC: a Bank & Ingestion Agent extracts, classifies and reconciles bank activity, and a Valuation & NAV Agent validates investment activity before releasing Q4 marks and calculating the fund close.

## Run

Open this folder in your editor or terminal: `C:\Ritamm\gpf-nav-close`. Use Python 3.11 or newer. Install the dependencies, copy `.env.example` to `.env`, fill the three LLM variables, then run:

```bash
python main.py
```

The assignment pack is intentionally kept outside this project at `C:\Ritamm\pack`. To provide another pack, run `python main.py <pack-directory>`. The submission ZIP contains a sibling `pack/` folder, so the same command also works after extraction.

The evaluator's `LLM_API_KEY`, `LLM_BASE_URL`, and `LLM_MODEL` take precedence over `.env`. `AGENT_A_PORT` and `AGENT_B_PORT` default to 8001 and 8002. The command validates the pack and LLM configuration before starting either local service, creates `output/` only after a complete close, and stops both services when the run ends.

`DEMO_CLASSIFIER=1` enables a deterministic local adapter solely to make offline tests and the included sample output reproducible. It is not the normal or evaluator mode; without that setting, every extracted transaction is sent to the configured OpenAI-compatible LLM for classification.

The committed `output/` was generated with `DEMO_CLASSIFIER=1` because this workspace has no LLM credential. Before submitting, run the normal command once with valid evaluator-compatible LLM settings and confirm that the regenerated output still reconciles.

## Architecture

`main.py` loads configuration and invokes `app.orchestrator.run_close`. The Bank Agent parses the statement with position-aware PDF extraction, verifies every running balance, calls the LLM in bounded batches of at most eight transactions, and rejects incomplete/duplicate/unknown responses before any classification is used. It validates category, cash direction, LP codes against Agent B's approved Q3 LP directory, and company names against source-text evidence. It then proves purchases and sales through Agent B's A2A endpoint.

Agent B owns `agent_b_data.json`, Q3 inputs and all valuation/NAV work. It shares a narrow classification context only: LP names/codes, opening portfolio names, and Q3 liability payees and balances. This lets the classifier distinguish prior-period settlements from new expenses without exposing Q4 marks or Agent B's expected activity. Where a known prior-period liability payment or an impossible purchase/sale cash direction is detected, deterministic policy canonicalizes the final category and records the override reason; cumulative settlement amounts cannot exceed the approved Q3 liability balance. Agent B returns `input-required` until a multiset comparison confirms purchase/sale company, date and amount. It calculates holdings from Q3 positions plus transactions, reviews Q4 marks, and produces direct NAV and a NAV bridge. All financial arithmetic uses `Decimal` and the close stops if the bridge differs by any amount.

The implementation follows the A2A 0.3 compatibility requirement in the brief. It deliberately uses structured JSONL trace records instead of requiring a remote observability service: `output/a2a_log.jsonl` captures protocol messages and `output/run_trace.jsonl` captures correlated workflow milestones without exposing credentials. Unexpected HTTP handler exceptions are logged server-side with a request ID and traceback, while the client receives only the safe JSON-RPC internal-error response.

The two required marks-exchange messages travel over HTTP. After that exchange, this single-process exercise invokes Agent B's valuation service directly with Agent A's classified ledger so the brief remains a two-turn conversation. This is a documented local coordinator contract, not a remotely enforced cross-process boundary. A production deployment would persist authenticated task state and expose a dedicated, authorized Agent B ledger-ingestion contract.

## Tests

Tests run offline and use deterministic fixtures:

```bash
python -m unittest discover -s tests -v
python -m pytest -q
```

They cover PDF extraction, one-cent reconciliation failures, bounded and retrying LLM calls, prompt grounding, unknown-identity containment, accrued-liability safeguards, A2A activity gating, malformed/non-finite input, concurrent task IDs, output publication, and a complete local HTTP workflow. The normal command should also be smoke-tested with a newly generated valid LLM configuration before submission.

`tests/test_hardening.py` adds malformed/non-finite input, duplicate-mark, provider-error, concurrent-task, request-size, structured-error, and deterministic randomized reconciliation coverage. The test suite does not claim live-provider model accuracy: no LLM credential is available in this workspace.

## Output

The run writes `transactions.json`, `a2a_log.jsonl`, `marks.json`, `review.json`, `nav.json`, `run_trace.jsonl`, and `run_manifest.json`. The manifest records SHA-256 hashes of each input to bind generated results to an exact pack. Extra source-provenance fields in `transactions.json` support auditability. `marks.json` is the artifact received through A2A without modification.

## Product next steps

For a production multi-fund platform, add authenticated TLS service-to-service transport, durable task storage and idempotency keys, encrypted document/object storage, a reviewed chart-of-accounts mapping and human exception queues, immutable audit retention, tenant isolation, secret management, and an OpenTelemetry exporter wired to the organization's chosen trace backend. Maintain a human-labelled, versioned LLM evaluation corpus and require model approval before changing model/provider. Those systems require operational choices not specified in this exercise.
