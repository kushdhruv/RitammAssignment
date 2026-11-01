# LLM batching research: configured Groq GPT-OSS classifier

## Scope and safe configuration check

The local non-secret configuration identifies the provider as Groq (`LLM_BASE_URL=https://api.groq.com/openai/v1`) and the model as `openai/gpt-oss-20b`. `LLM_API_KEY` was not read, printed, or transmitted for this research.

This note separates provider facts from the recommended operating policy. It does not claim to know this organization's paid tier; Groq says that its published table is a base Developer-plan summary and that the account Limits page and response headers are authoritative for a particular organization.

## Verified provider facts

| Topic | Current official documentation |
| --- | --- |
| Context and completion ceiling | Groq lists a **131,072-token context window** and **65,536 maximum output tokens** for `openai/gpt-oss-20b`. [Groq model page](https://console.groq.com/docs/model/openai/gpt-oss-20b) |
| Request limit | Groq's rate-limit page publishes a base Developer-plan table of **30 RPM**, **1K RPD**, **8K TPM**, and **200K TPD** for this model. Limits apply at the organization level, so any threshold may be hit first. [Groq rate limits](https://console.groq.com/docs/rate-limits) |
| Per-account truth and overload handling | Groq exposes limit/remaining/reset headers, returns HTTP 429 on exhaustion, and supplies `retry-after` only on a 429. Use the account Limits page and these headers instead of treating the public table as the account's allocation. [Groq rate-limit headers](https://console.groq.com/docs/rate-limits) |
| Token controls | `max_completion_tokens` caps generated tokens; input plus generated tokens must fit the model context. `max_tokens` is deprecated. [Groq Chat Completions API reference](https://console.groq.com/docs/api-reference) |
| Structured output | `openai/gpt-oss-20b` supports strict JSON Schema Structured Outputs (`strict: true`), which uses constrained decoding for schema-compliant output. Groq recommends JSON Schema rather than the older JSON-object mode when supported. [Groq Structured Outputs](https://console.groq.com/docs/structured-outputs) and [API reference](https://console.groq.com/docs/api-reference) |
| Reasoning setting | The model accepts `low`, `medium`, or `high` reasoning effort; Groq documents `medium` as the default. [Groq API reference](https://console.groq.com/docs/api-reference) |

There is a material documentation discrepancy: Groq's [Supported Models page](https://console.groq.com/docs/models) instead shows **250K TPM / 1K RPM** for this model's Developer plan. That is further reason not to hard-code either public table; use the configured organization's current limits and headers. The model's 65,536 maximum completion is a provider ceiling, **not** an appropriate classification budget. A batch needs an explicit, small `max_completion_tokens`, or an unexpectedly verbose completion can consume the minute token allocation.

## Conservative token-budgeted batching policy

Do not choose a constant number of transactions per request. Transaction description lengths, the approved reference context, identifier lengths, and the expected JSON response all affect the request size.

For every candidate batch, serialize the exact request that will be sent (including messages and, when adopted, the response schema) and estimate its prompt-token count `I`. Maintain a conservative completion reservation `O` based on the bounded response shape, initially:

```text
O = 512 envelope/reasoning reserve + 256 × number_of_transactions
request reservation R = I + O
```

This is an output-token reservation, not a transaction-count cap. It should be calibrated only from recorded provider `usage` values: after a representative labelled corpus, retain a safety margin above the observed high-percentile completion use. Do not reduce it merely because a few short bank records succeeded. Set `max_completion_tokens` to the selected `O` for that particular batch. For this narrow, schema-only task, test `reasoning_effort: "low"` against the labelled corpus before adopting it; Groq documents `medium` as the default, so a lower setting is a quality/latency trade-off, not a free assumption.

Accept another transaction into a batch only while both conditions hold:

```text
R <= 0.80 × 131,072                 # context guard: 104,857 tokens
R <= 0.80 × current TPM allowance   # rate guard; 6,400 when only the published 8K TPM baseline is known
```

The rate guard normally dominates this workflow. Dispatch only when the rolling 60-second sum of reservations is within that same rate budget; also remain below 80% of the current RPM/RPD/TPD allowance. Prefer current `x-ratelimit-*` header values or the account Limits page. If Groq has split input/output minute limits for the organization, apply the input component to `I` and the output component to `O` separately.

On `429`, respect `retry-after` (or the reset headers), refresh the rate budget, and retry only the complete failed batch. On a length finish or an incomplete/invalid response, reject the entire batch, then retry with a revised token budget or split it. Never consume a partial classification result. Retain the application's independent completeness, enum, ID, cash-direction, and grounding validations: schema conformance cannot establish accounting correctness.

For a production classifier, measure `usage.prompt_tokens`, `usage.completion_tokens`, finish reason, and response-header limits for each call, with transaction text and credentials redacted. Those measurements make later increases in batch size evidence-based.

## Structured-output shape for this task

Use Groq's `response_format.type: "json_schema"` with `strict: true` for this exact configured model, after compatibility testing. Define the existing envelope and every classification item in the schema; make `transaction_id`, `category`, `lp_code`, and `company` required. Since `lp_code` and `company` can be absent, represent each as `string | null`, not as an omitted property. Groq's strict-schema guidance requires `additionalProperties: false` on all object schemas and uses a null union for optional-valued fields. [Groq schema requirements](https://console.groq.com/docs/structured-outputs)

Structured Outputs prevents malformed shapes; it does not make the selected category or entity factually correct. The current deterministic validation remains the financial control boundary.

## Decision

The present fixed eight-row batch is suitably small as a temporary safety measure, but it is not a provider-backed capacity policy. Replace it only with dynamic packing by serialized input tokens plus a bounded output reservation, paced against the actual Groq organization limits. Validate strict-schema behavior and quality on the labelled transaction set before relying on either a larger budget or reduced reasoning effort.
