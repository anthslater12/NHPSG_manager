"""Create the Version 1 staff leave-request table."""

import sqlite3


DB_NAME = "nhpsg.db"

LEAVE_REQUEST_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS leave_requests (
    leave_request_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(user_id),
    leave_type TEXT NOT NULL CHECK (leave_type IN (
        'Vacation',
        'Personal Illness',
        'Family Responsibility',
        'Bereavement',
        'Medical Appointment',
        'Leave Without Pay',
        'Other'
    )),
    other_reason TEXT,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    day_part TEXT NOT NULL CHECK (day_part IN ('FULL_DAY', 'PARTIAL_DAY')),
    start_time TEXT,
    end_time TEXT,
    requested_days REAL,
    requested_hours REAL,
    employee_comments TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (
        status IN ('PENDING', 'APPROVED', 'DECLINED', 'CANCELLED')
    ),
    submitted_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    reviewed_by_user_id INTEGER REFERENCES users(user_id),
    reviewed_at_utc TEXT,
    management_comments TEXT,
    cancelled_at_utc TEXT,
    cancelled_by_user_id INTEGER REFERENCES users(user_id),
    submission_token TEXT NOT NULL UNIQUE
)
"""

INDEXES = (
    ("idx_leave_requests_user_status", "leave_requests(user_id, status)"),
    ("idx_leave_requests_status_dates", "leave_requests(status, start_date, end_date)"),
    ("idx_leave_requests_start_date", "leave_requests(start_date)"),
)


def migrate(conn):
    """Create the leave-request schema idempotently in one transaction."""
    conn.execute("PRAGMA foreign_keys = ON")
    if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise RuntimeError("SQLite foreign-key enforcement could not be enabled.")

    started_transaction = not conn.in_transaction
    if started_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(LEAVE_REQUEST_TABLE_SQL)
        required = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(leave_requests)"
            )
        }
        expected = {
            "leave_request_id", "user_id", "leave_type", "other_reason",
            "start_date", "end_date", "day_part", "start_time", "end_time",
            "requested_days", "requested_hours", "employee_comments", "status",
            "submitted_at_utc", "updated_at_utc", "reviewed_by_user_id",
            "reviewed_at_utc", "management_comments", "cancelled_at_utc",
            "cancelled_by_user_id", "submission_token",
        }
        if not expected.issubset(required):
            raise RuntimeError("Existing leave_requests schema is incompatible.")
        for index_name, indexed_columns in INDEXES:
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS {index_name} "
                f"ON {indexed_columns}"
            )
        if started_transaction:
            conn.commit()
    except BaseException:
        if started_transaction and conn.in_transaction:
            conn.rollback()
        raise


def main():
    conn = sqlite3.connect(DB_NAME)
    try:
        migrate(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
