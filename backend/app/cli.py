"""Small admin helpers.   python -m app.cli hash-password   |   python -m app.cli gen-secret"""

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
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
