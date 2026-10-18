"""Small admin helpers (run from the backend folder).

  python -m app.cli hash-password            create a STAFF_PASSWORD_HASH value
  python -m app.cli gen-secret               create a SESSION_SECRET value
  python -m app.cli clear-demo-data --yes    DELETE every request, status history and staff session
"""

import getpass
import secrets
import sys

from app.auth import hash_password


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "hash-password":
        pw = getpass.getpass("Staff password (not shown): ")
        if len(pw) < 12:
            print("Use at least 12 characters.", file=sys.stderr)
            return 1
        if pw != getpass.getpass("Repeat password: "):
            print("Passwords do not match.", file=sys.stderr)
            return 1
        print("\nPut this in STAFF_PASSWORD_HASH (keep the single quotes in a .env file):\n")
        print(f"'{hash_password(pw)}'")
        return 0
    if cmd == "gen-secret":
        print(secrets.token_urlsafe(48))
        return 0
    if cmd == "clear-demo-data":
        if "--yes" not in argv:
            print("This deletes ALL service requests, status history and staff sessions in the configured database.")
            print("Re-run with --yes to confirm.")
            return 1
        from sqlalchemy import text

        from app.db import session_scope

        with session_scope() as s:
            s.execute(text("TRUNCATE service_requests, status_history, staff_sessions RESTART IDENTITY CASCADE"))
        print("Demo data cleared.")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
