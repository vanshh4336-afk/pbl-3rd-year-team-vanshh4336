from __future__ import annotations

import sys
from pathlib import Path

import mysql.connector

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.db_config import DB_CONFIG


def upgrade_scheduling_results(cursor) -> None:
    cursor.execute("SHOW COLUMNS FROM scheduling_results")
    columns = {row[0] for row in cursor.fetchall()}
    if "response_time" not in columns:
        cursor.execute(
            "ALTER TABLE scheduling_results "
            "ADD COLUMN response_time INT UNSIGNED NULL AFTER completion_time"
        )
    cursor.execute(
        "UPDATE scheduling_results sr JOIN processes p "
        "ON p.run_id=sr.run_id AND p.process_id=sr.process_id "
        "SET sr.response_time=sr.start_time-p.arrival_time WHERE sr.response_time IS NULL"
    )
    cursor.execute(
        "ALTER TABLE scheduling_results MODIFY response_time INT UNSIGNED NOT NULL"
    )
    cursor.execute(
        "SELECT COUNT(*) FROM information_schema.table_constraints "
        "WHERE constraint_schema=DATABASE() AND table_name='scheduling_results' "
        "AND constraint_name='chk_result_response'"
    )
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            "ALTER TABLE scheduling_results ADD CONSTRAINT chk_result_response "
            "CHECK (response_time <= waiting_time)"
        )
    cursor.execute("SHOW COLUMNS FROM scheduling_results LIKE 'algorithm'")
    algorithm_type = cursor.fetchone()[1]
    if "SJF" not in algorithm_type:
        cursor.execute(
            "ALTER TABLE scheduling_results MODIFY algorithm "
            "ENUM('FCFS','ROUND_ROBIN','PRIORITY','SJF') NOT NULL"
        )


def main() -> int:
    schema_path = Path(__file__).with_name("schema.sql")
    schema = "\n".join(
        line for line in schema_path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("--")
    )
    statements = [statement.strip() for statement in schema.split(";") if statement.strip()]
    connection = mysql.connector.connect(**DB_CONFIG)
    try:
        with connection.cursor() as cursor:
            for number, statement in enumerate(statements, start=1):
                try:
                    cursor.execute(statement)
                    if statement.startswith("CREATE TABLE IF NOT EXISTS scheduling_results"):
                        upgrade_scheduling_results(cursor)
                except mysql.connector.Error as error:
                    print(f"Schema statement {number}/{len(statements)} failed: {error}", file=sys.stderr)
                    connection.rollback()
                    return 1
        connection.commit()
    finally:
        connection.close()
    print(f"Applied {len(statements)} schema statements from {schema_path.name}.")
    print("Existing tables were retained; old simulation status history was backfilled where missing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
