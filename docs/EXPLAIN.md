# Architecture, security and limitations: short explanation

**The idea.** A customer chat answers questions and records demo repair requests; a protected staff page manages them. Both use one PostgreSQL database, so a staff change shows up in customer tracking.

**Who decides what.** The language model only interprets general questions and picks from four read-only tools. Everything with consequences (collecting fields, validation, confirmation, creating the record, staff login, status updates) is ordinary code. This is deliberate: a prompt cannot be trusted to enforce confirmation or permissions, so code does.

**Why MCP.** MCP is a standard protocol between an agent and its tools. Here a real MCP server (official Python SDK) runs as a subprocess over stdio and a real MCP client discovers and calls its tools. The server gets only the database URL, so a compromised model prompt still has no access to the Hugging Face token or staff secrets.

**Security decisions.** Argon2 password hash from the environment; database-backed sessions so logout is real; CSRF header for state-changing staff calls; narrow public tracking schema; identical 404 for unknown and malformed references; validation and size/rate limits; ORM only; no personal data in logs; no secrets in the repository or frontend; CSP and security headers.

**Honesty decisions.** The UI and every created request say this is an independent academic prototype and that nothing is sent to SportFit. Unverified facts (prices, warranty, spare parts, contacts) are never invented; the assistant says it cannot verify them.

**Limitations.** Single staff account; in-memory chat state and rate limits; small knowledge base; model availability depends on Hugging Face credits; the free hosting tier can restart or sleep; no notifications; the Docker image build and public deployment are verified only after you complete the deployment steps.
