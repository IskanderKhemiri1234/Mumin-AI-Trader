# Mumin Operations Chat — Local and Offline

## What it does

Mumin Operations Chat is a bilingual Arabic/English operations assistant served by the existing FastAPI application and dashboard. It answers local operational questions about:

- Paper engine, worker and supervisor heartbeat.
- Paper cash, marked equity, realized and unrealized PnL, and fees.
- Open positions and their latest ledger marks.
- Safety alerts, risk halt and ledger reconciliation.
- Recent supervisor audit and Paper command events.

## Privacy and cost

- No hosted language model, API key, subscription, paid inference, or external AI service is used.
- Chat requests go only to the same-origin Mumin API.
- The backend reads the configured Paper SQLite database using SQLite `mode=ro` and enables `PRAGMA query_only=ON`.
- The chat does not write to SQLite, create commands, pause/resume the engine, or submit BUY/SELL/cancel orders.
- Dashboard and operations chat use the unified `X-Dashboard-Password` header, backed by the server-side `DASHBOARD_PASSWORD`.

## Language understanding

The first release uses a deterministic, local Arabic/English intent classifier and grounded response templates. It recognizes common questions and synonyms for engine, portfolio, positions, PnL, alerts, events, and overview. It is not a generative LLM and does not invent ledger values. Unknown questions receive a safe operational summary.

This deliberately keeps the feature small, auditable, dependency-free, and usable on a phone. A fully generative model is not required for this release.

## API

- `POST /api/operations-chat`
- JSON body: `{"message":"engine status"}`
- Header: `X-Dashboard-Token: <dashboard token>`
- Maximum message length: 500 characters.

The response includes a localized answer, detected intent, a read-only flag, an explicit `external_services_used=false` marker, and the underlying snapshot with alerts and recent events.

## Safety boundaries

This is a monitoring and explanation surface only. It has no trading or engine-control tools. Existing Paper command and control endpoints remain separate and retain their own authorization and confirmation requirements. Mainnet remains blocked and Testnet execution remains disabled.
