# Research audit: reliability and observability evidence

This note distinguishes verified, first-party evidence from recommendations. The system under review is a single-fund, local A2A 0.3 close workflow. It does not currently implement persistent agent memory, multi-tenant storage, or a hosted observability service, so sources about those topics are not treated as evidence that such components exist.

## Verified engineering cases

### Google SRE: data-integrity failures and checks

Google's SRE book records an actual 2011 Gmail incident and describes a Google Music deletion pipeline where a race condition became more likely as upstream processing times grew. The chapter's conclusion is that storage semantics alone are insufficient and that critical data needs defense in depth, out-of-band validation, alerting, and recovery exercises. [Google SRE, "Data Integrity: What You Read Is What You Wrote"](https://sre.google/sre-book/data-integrity/)

**Application:** The project already has a first-line running-balance reconciliation and a NAV bridge. A stronger production design needs an independent, scheduled validator over immutable source hashes, classifications, A2A artifacts, and final outputs, plus a tested recovery process. This is not needed to satisfy the narrow take-home run, but is needed before operating repeated closes.

### Google SRE: duplicate/incorrect data prevention

Google's SRE workbook recommends idempotent mutations to avoid duplicate or incorrect records in data-processing pipelines. [Google SRE workbook, "Improve and Optimize Data Processing Pipelines"](https://sre.google/workbook/data-processing/)

**Application:** The current A2A handler returns a repeatable result for a repeated continuation, but it does not persist message IDs or a durable idempotency key. A process restart loses the task map. Production deployment therefore needs durable task state keyed by an authenticated tenant/fund, task ID, and message ID.

## Official protocol and platform guidance

### A2A lifecycle and errors

The A2A 0.3 specification defines JSON-RPC 2.0 request/response requirements, `input-required` and terminal task states, `tasks/get`, `tasks/cancel`, and standard JSON-RPC error codes. [A2A 0.3 specification](https://a2a-protocol.org/v0.3.0/specification/)

**Application:** The take-home's two-turn `message/send` sequence is covered. The current project intentionally implements only the requested subset, so it is not a general A2A 0.3 server: it lacks `tasks/get`, `tasks/cancel`, authenticated agent cards, TLS, and durable state. Those omissions must remain explicit in any production-readiness claim.

### LLM output containment

OpenAI's current Structured Outputs guide states that strict JSON-schema mode enforces schema adherence for supported models and recommends it over JSON mode when possible. The guide also notes schema-support constraints. OpenAI's function-calling guidance says that when strict outputs cannot be used, applications should validate with a library and may retry invalid results. [Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs) and [Function Calling guidance](https://help.openai.com/en/articles/8555517-function-calling-in-the-openai-api)

**Application:** The project correctly treats LLM classification as untrusted and applies deterministic completeness, enum, identity, and cash-direction checks. Its generic OpenAI-compatible endpoint cannot assume strict-schema support. The right production evolution is capability negotiation: use strict schemas when the configured provider/model supports them, retain local Pydantic validation and bounded retries otherwise, and fail closed after retry exhaustion.

### Agent evaluation limits

Anthropic's agent-evaluation write-up argues for task-specific, rigorous evaluations rather than relying on plausible demonstrations. Its published risk material also documents recurring long-horizon model errors, including tool-use and arithmetic mistakes. [Anthropic, "Demystifying evals for AI agents"](https://www.anthropic.com/research/demystifying-evals-for-ai-agents) and [Anthropic sabotage risk report](https://alignment.anthropic.com/2025/sabotage-risk-report/2025_pilot_risk_report.pdf)

**Application:** A passing deterministic fixture suite cannot demonstrate external LLM classification quality. Maintain a versioned, human-labelled transaction corpus and measure category, LP, and company extraction accuracy by model/provider before allowing a model change. Financial calculations must remain deterministic.

### Observability conventions

OpenTelemetry specifies that trace context is propagated across process boundaries; its semantic-conventions guidance recommends spans for meaningful duration-bearing operations, explicit errors, and low-cardinality, useful attributes. [OpenTelemetry overview](https://opentelemetry.io/docs/specs/otel/overview/) and [semantic-conventions guidance](https://opentelemetry.io/docs/specs/semconv/how-to-write-conventions/)

**Application:** The current JSONL trace file provides local correlation only. A production implementation should emit OpenTelemetry spans for extraction, LLM calls, A2A client/server calls, and NAV calculation; propagate W3C trace context; record error types and timing; and redact transaction descriptions, LP names, bank references, and credentials from telemetry by default.

## Conclusion

The sources support deterministic validation, idempotency, independent integrity checks, durable task state, bounded LLM handling, and trace propagation. They do not support adding a memory store, Langfuse, Arize Phoenix, or a vector database to this narrow exercise without a concrete product requirement.
