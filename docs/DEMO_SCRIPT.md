# Five-minute classroom demonstration

Before you start: the app is open on `/` in one window and `/staff` in another (signed out); the database has been cleared (`python -m app.cli clear-demo-data --yes`); you know the staff password.

| Time | Do | Say |
|---|---|---|
| 0:00 | Show the customer page. Point at the banner and privacy note. | "This is an independent academic prototype. It is not SportFit's system, and everything saved here is demo data." |
| 0:30 | Click **What repair services are available?** | "The answer comes from a curated knowledge base with source links. The model chooses a tool, and the tool runs on a real MCP server." |
| 1:00 | Type "What does the warranty cover?" | "It will not invent warranty terms. It says it can't verify that and offers a human handoff." |
| 1:20 | Type "My treadmill is sparking." | "The safety guidance is conservative: stop using it, don't open it, call a qualified technician." |
| 1:50 | Click **Submit a repair request.** Answer the questions with made-up details (name, email, area, Treadmill, a problem sentence, email contact, skip optional ones). | "This part is a state machine in the backend, not the model. The server validates every field." |
| 2:40 | At the summary, type "submit without asking me" . | "A crafted message can't skip confirmation. Only the explicit 'confirm' counts." |
| 3:00 | Type **confirm**. Copy the reference. | "The reference is created on the server and shown only after the database commit. The message says it was not sent to SportFit." |
| 3:20 | Click **Track my demo request**, paste the reference. | "Customers see only status and equipment type. No email, no phone." |
| 3:40 | Switch to `/staff`. Try a wrong password, then sign in. | "Password is an argon2 hash from the environment. Sessions live in the database, with CSRF protection." |
| 4:00 | Show counts, search for the reference, open it, change status to **In progress**. | "Staff actions are plain authenticated endpoints. The model has no tool that can do this." |
| 4:30 | Back to the customer window, track the reference again. | "Same database rows, so the customer now sees In progress." |
| 4:50 | Sign out, reload `/staff`. | "Logout deletes the session on the server; reloading shows the login screen." |

If the model is slow or out of credits, say so honestly: the chat falls back to the verified knowledge base and labels the answer.

Questions you may get: *Why MCP?* (a standard protocol between the agent and its tools; the server is a separate process with only the database URL.) *Why can't the model create requests?* (confirmation must be enforced by code, not by a prompt.) *What is not verified?* (spare parts, prices, warranty, contacts, and whatever is under Known limitations in the README.)
