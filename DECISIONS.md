# Decisions

## Decision 1: Version-pinned A2A implementation

**Decision:** Implement the required JSON-RPC `message/send` conversation and agent cards for A2A 0.3 using a small local HTTP transport.

**Alternatives:** Call Agent B as a Python function, or adopt a newer A2A protocol version.

**Why:** The brief explicitly requires actual HTTP JSON-RPC under A2A 0.3. A focused adapter has a small dependency surface and preserves the required task/context continuation.

## Decision 2: Deterministic accounting boundary

**Decision:** Use the LLM only for transaction categorization and identity extraction; validate its response and calculate all money, holdings, and NAV deterministically with `Decimal`.

**Alternatives:** Let the LLM create financial outputs or use rules alone for all categorization.

**Why:** The brief requires LLM classification, but monetary correctness and validation must be repeatable, exact, and auditable.

## Decision 3: Fail closed on reconciliation errors

**Decision:** Treat every non-zero balance or NAV-bridge difference as an error that stops final output finalization.

**Alternatives:** Apply a materiality tolerance or automatically post a balancing adjustment.

**Why:** The brief requires cent-level reconciliation. Silent adjustments make an accounting error harder to locate and audit.

## Decision 4: Embedded durable workflow state machine

**Decision:** Implement workflow orchestration as an embedded, ACID-compliant activity state machine backed by SQLite in WAL mode (`app/workflow.py`) with per-batch checkpointing and automatic resumption.

**Alternatives:** Distributed orchestrators such as Temporal or Celery + Redis / RabbitMQ.

**Why:** Temporal and Celery introduce heavy external daemon processes (Docker clusters, Redis brokers, open ports) that break zero-dependency local CLI execution (`python main.py ./pack`). The embedded WAL engine provides the exact same architectural resilience guarantees (idempotent activities, per-batch LLM checkpointing, failure retry, and instant resumption after crashes) with zero external infrastructure.

