# Manual setup checklist

Mark each item as you complete it.

**Accounts and tools**
- [ ] GitHub repository is public and contains no `.env` (run `git status --short`; `.env` must not appear)
- [ ] Python 3.10+ and Node.js LTS installed (`npm.cmd` works, or PowerShell policy set to RemoteSigned)
- [ ] Hugging Face account, fine-grained token with *Make calls to Inference Providers*, credits available
- [ ] `python -m scripts.verify_hf` shows all PASS (done once with `openai/gpt-oss-20b`)

**Local**
- [ ] `backend\.env` created from `.env.example` and filled in (DATABASE_URL, HF_TOKEN, SESSION_SECRET, STAFF_PASSWORD_HASH)
- [ ] `alembic upgrade head` succeeds
- [ ] `pytest` passes with `TEST_DATABASE_URL` set to a throwaway database
- [ ] `npm test` and `npm run build` pass in `frontend`
- [ ] App runs locally; customer flow and staff flow both work

**Deployment**
- [ ] External PostgreSQL created; connection string kept private
- [ ] Hugging Face Space created (Docker, Public); secrets and variables added (see docs/DEPLOYMENT.md)
- [ ] Code pushed to the Space; the build finishes; `/health/ready` is OK
- [ ] Full customer-to-staff workflow tested on the public URL from a fresh browser window
- [ ] README demo URL updated only after that test
- [ ] Instructor access prepared privately (not in the repository)

**Before presenting**
- [ ] Demo records cleared (`python -m app.cli clear-demo-data --yes`)
- [ ] Demo script rehearsed (docs/DEMO_SCRIPT.md)
- [ ] Staff password known and the staff window ready
