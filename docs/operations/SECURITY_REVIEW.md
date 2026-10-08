# Production Security Review

This review covers the repository-level controls implemented for the current CareerAgentAI architecture. It does not certify any future third-party provider or hosting environment.

## External input

Provider, HTTP, persisted JSON, message, scheduling, RSS/Atom, and runtime environment values are validated at their boundaries. Domain models reject malformed identifiers, unsafe controls, lone Unicode surrogates, naive timestamps where timezone awareness is required, oversized content, and invalid state transitions.

SQLite writes use parameterized values for untrusted data. Migration SQL is trusted application code and is checksum-locked after application; it must never be built from provider or user input.

## Consequential actions

Application submission, recruiter communication, and calendar responses retain explicit human gates. Durable operation IDs, compare-and-swap state transitions, action claims, restart reconciliation, and provider-result validation prevent blind repetition after ambiguous outcomes.

A production runtime flag does not override these gates. Destructive database migrations separately require explicit approval.

## Secrets

No production credentials belong in the repository or tests. Runtime configuration contains no hard-coded provider secrets. Structured logging redacts mappings whose field names indicate passwords, secrets, tokens, authorization values, API keys, or cookies.

Do not place secrets in free-form log messages, exception text, entity identifiers, or business payloads intended for operational inspection. Provider adapters should map raw provider errors to bounded diagnostic messages before persistence.

The operational issues endpoint requires bearer authentication and must be deployed behind TLS. Health is intentionally limited to a minimal liveness response.

## Network

Network-capable providers remain behind explicit adapters and runtime configuration. Tests use fake/no-I/O providers. Production operators should allow outbound traffic only to configured provider hosts and apply DNS/egress controls outside the process.

## Persistence

Persisted JSON is decoded with standard JSON only; unsafe object deserialization is not used. Repositories validate restored domain state. The migration registry stores immutable checksums and refuses silently changed history.

Production SQLite must live on durable storage with backups. File permissions should restrict database access to the service account.

## Remaining environment responsibilities

Before enabling real external providers, operators must separately review provider OAuth/API scopes, credential rotation, webhook authenticity where applicable, TLS termination, host/firewall policy, backup encryption, access control for the operational API, retention requirements, and incident-response procedures.
