from __future__ import annotations

import getpass
import re
import sys
from pathlib import Path

import mysql.connector
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.db_config import DB_CONFIG


def main() -> int:
    username = input("Initial admin username: ").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{3,80}", username):
        print("Username must be 3–80 characters and use letters, numbers, _ or -.", file=sys.stderr)
        return 2
    password = getpass.getpass("Admin password (at least 10 characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if len(password) < 10 or password != confirmation:
        print("Passwords must match and contain at least 10 characters.", file=sys.stderr)
        return 2

    connection = mysql.connector.connect(**DB_CONFIG)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM users")
            if cursor.fetchone()[0]:
                print("Users already exist. Create further accounts from the admin page.", file=sys.stderr)
                return 1
            cursor.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (%s,%s,'admin')",
                (username, generate_password_hash(password)),
            )
        connection.commit()
    except mysql.connector.Error as error:
        connection.rollback()
        print(f"Could not create the admin account: {error}", file=sys.stderr)
        return 1
    finally:
        connection.close()
    print(f"Admin account {username} created. Start the dashboard and log in.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
