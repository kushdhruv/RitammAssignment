# Decisions

## Decision 1: Version-pinned A2A 0.3 protocol implementation

**Decision:** Implement the required A2A protocol strictly adhering to version 0.3 over local HTTP using JSON-RPC 2.0 `message/send`, public agent cards at `/.well-known/agent-card.json`, and explicit `taskId` / `contextId` continuation over two conversational turns.

**Alternatives:** 
1. In-process Python direct function calls between Agent A and Agent B, bypassing HTTP.
2. Adopting a draft or newer A2A specification (e.g. A2A 0.4+ or custom streaming WebSocket protocol).

**Why:** The brief explicitly mandates HTTP JSON-RPC 2.0 under A2A 0.3. A focused, standard-library HTTP transport implements the exact agent card discovery, `input-required` state progression, multiset activity validation, and artifact payload transfer required by the evaluator without introducing heavy external networking frameworks or version drift.

## Decision 2: Deterministic accounting boundary & guardrails

**Decision:** Confine the Large Language Model (LLM) strictly to perceptual classification and entity extraction from unstructured bank narrative strings. All ledger balances, cash direction checks, multiset comparisons, investment holdings roll-forwards, unrealized/realized gains, and NAV calculations are calculated deterministically using Python `Decimal`.

**Alternatives:**
1. End-to-end LLM orchestration where the model directly synthesizes the NAV bridge and accounting summaries.
2. Pure heuristic/regex pattern matching for all transaction classifications without an LLM.

**Why:** While LLMs excel at understanding diverse, messy, and wrapped bank transaction descriptions, financial reporting requires mathematical certitude and auditability. Section 4 and 8 of the brief require an LLM classifier, but allowing non-deterministic model completions to perform financial calculations risks hallucinated numbers and broken bridge balances. The deterministic accounting boundary ensures 100% mathematical auditability down to the cent.

## Decision 3: Fail-closed cent-exact reconciliation policy

**Decision:** Enforce a zero-tolerance, fail-closed policy across all balance checks: every transaction running balance must match, and the NAV bridge difference must equal exactly USD 0.00. Any non-zero difference halts the quarter close and aborts output publication.

**Alternatives:**
1. Applying a materiality tolerance (e.g. allowing variance under $100 or 1 cent).
2. Automatically booking an artificial "plug" or balancing adjustment line item.

**Why:** Private equity fund accounting requires exact reconciliation between cash movements, balance-sheet accounts, and partner capital balances. Section 7 of the brief stipulates: *"If the bridge does not balance, a transaction is in the wrong category. Fix the cause, not the number."* Silent adjustments conceal categorization mistakes; fail-closed enforcement guarantees that published outputs are true to the cent.

## Decision 4: Embedded durable workflow state machine

**Decision:** Implement workflow orchestration as an embedded, ACID-compliant activity state machine backed by SQLite in WAL mode (`app/workflow.py`) with per-batch checkpointing, input hashing, and automatic resumption.

**Alternatives:**
1. In-process, ephemeral execution where an interruption or network timeout restarts the pipeline from scratch.
2. Heavyweight distributed orchestrators such as Temporal or Celery + Redis / RabbitMQ.

**Why:** Distributed orchestrators require external daemon services (Docker containers, Redis brokers, open ports) that break zero-dependency local CLI execution (`python main.py ./pack`). The embedded WAL engine provides the exact same architectural resilience guarantees—idempotent activities, per-batch LLM checkpointing, failure retry, and instant resumption after crashes—using only the Python standard library with zero external infrastructure.

## Decision 5: Entity canonicalization and token-aware dynamic LLM batching

**Decision:** Implement portfolio company canonicalization (`canonicalize_company`) to resolve complex M&A deal structures (e.g., mapping `"INWARD WIRE NORDIC ACQUISITIONS AS SPA FJORD PAYMENTS AS COMPLETION"` to `"Fjord Payments AS"`), and dynamically pack transactions into small batches of 6–8 items sized against a conservative token budget.

**Alternatives:**
1. Passing all 39 bank transactions in a single LLM prompt.
2. Relying on raw LLM output strings directly without canonical validation against Agent B's verified portfolio company registry.

**Why:** Free and developer-tier LLM endpoints (such as Groq's `openai/gpt-oss-20b` with an 8,000 TPM limit) silently truncate completions when fed 39 transactions at once, causing incomplete batches. Sizing requests dynamically at 6–8 items guarantees that token limits are never breached. Furthermore, raw LLM extractions from complex acquisition wires often conflate the acquirer and the asset; canonicalizing against known investments ensures Agent B's multiset validation succeeds reliably.
