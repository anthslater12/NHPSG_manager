import functools
import sqlite3

from add_staff_notice_acknowledgement_invalidation import (
    quote_identifier,
    schema_is_current as acknowledgement_schema_is_current,
    sql_token_signature,
)


DB_NAME = "nhpsg.db"

TABLE_SQL = {
    "staff_notices": """
        CREATE TABLE staff_notices (
            notice_id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            notice_text TEXT NOT NULL,
            priority TEXT NOT NULL DEFAULT 'Normal'
                CHECK (priority IN ('Normal', 'Important', 'Urgent')),
            client_id INTEGER NULL,
            status TEXT NOT NULL DEFAULT 'Draft'
                CHECK (status IN ('Draft', 'Published', 'Withdrawn', 'Replaced')),
            draft_active INTEGER NOT NULL DEFAULT 1
                CHECK (draft_active IN (0, 1)),
            effective_start_at_utc TEXT NULL,
            expires_at_utc TEXT NULL,
            until_withdrawn INTEGER NOT NULL DEFAULT 0
                CHECK (until_withdrawn IN (0, 1)),
            version_number INTEGER NOT NULL DEFAULT 1
                CHECK (version_number >= 1),
            replaces_notice_id INTEGER NULL,
            created_by_user_id INTEGER NOT NULL,
            created_at_utc TEXT NOT NULL,
            updated_by_user_id INTEGER NULL,
            updated_at_utc TEXT NULL,
            published_by_user_id INTEGER NULL,
            published_at_utc TEXT NULL,
            withdrawn_by_user_id INTEGER NULL,
            withdrawn_at_utc TEXT NULL,
            withdrawal_reason TEXT NULL,
            replaced_by_user_id INTEGER NULL,
            replaced_at_utc TEXT NULL,
            replacement_reason TEXT NULL,
            FOREIGN KEY (client_id) REFERENCES clients(client_id),
            FOREIGN KEY (replaces_notice_id) REFERENCES staff_notices(notice_id),
            FOREIGN KEY (created_by_user_id) REFERENCES users(user_id),
            FOREIGN KEY (updated_by_user_id) REFERENCES users(user_id),
            FOREIGN KEY (published_by_user_id) REFERENCES users(user_id),
            FOREIGN KEY (withdrawn_by_user_id) REFERENCES users(user_id),
            FOREIGN KEY (replaced_by_user_id) REFERENCES users(user_id),
            CHECK (
                status = 'Draft'
                OR (
                    effective_start_at_utc IS NOT NULL
                    AND published_at_utc IS NOT NULL
                    AND (
                        (until_withdrawn = 1 AND expires_at_utc IS NULL)
                        OR
                        (until_withdrawn = 0 AND expires_at_utc IS NOT NULL)
                    )
                )
            ),
            CHECK (
                expires_at_utc IS NULL
                OR effective_start_at_utc IS NULL
                OR expires_at_utc >= effective_start_at_utc
            )
        )
    """,
    "staff_notice_audiences": """
        CREATE TABLE staff_notice_audiences (
            audience_id INTEGER PRIMARY KEY AUTOINCREMENT,
            notice_id INTEGER NOT NULL UNIQUE,
            created_at_utc TEXT NOT NULL,
            FOREIGN KEY (notice_id) REFERENCES staff_notices(notice_id)
        )
    """,
    "staff_notice_audience_rules": """
        CREATE TABLE staff_notice_audience_rules (
            audience_rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
            audience_id INTEGER NOT NULL,
            rule_type TEXT NOT NULL
                CHECK (rule_type IN (
                    'Core Organization',
                    'All Support Workers',
                    'Selected Role',
                    'Selected Individual',
                    'Applicable Shift Staff'
                )),
            role_name TEXT NULL,
            user_id INTEGER NULL,
            created_at_utc TEXT NOT NULL,
            FOREIGN KEY (audience_id) REFERENCES staff_notice_audiences(audience_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            CHECK (
                (rule_type = 'Selected Role' AND role_name IS NOT NULL AND user_id IS NULL)
                OR
                (rule_type = 'Selected Individual' AND role_name IS NULL AND user_id IS NOT NULL)
                OR
                (rule_type IN ('Core Organization', 'All Support Workers', 'Applicable Shift Staff')
                    AND role_name IS NULL AND user_id IS NULL)
            )
        )
    """,
    "staff_notice_audience_eligibility_periods": """
        CREATE TABLE staff_notice_audience_eligibility_periods (
            eligibility_period_id INTEGER PRIMARY KEY AUTOINCREMENT,
            audience_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            eligible_from_at_utc TEXT NOT NULL,
            eligible_until_at_utc TEXT NULL,
            eligibility_source_summary TEXT NOT NULL,
            opened_by_user_id INTEGER NULL,
            closed_by_user_id INTEGER NULL,
            close_reason TEXT NULL,
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NULL,
            FOREIGN KEY (audience_id) REFERENCES staff_notice_audiences(audience_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (opened_by_user_id) REFERENCES users(user_id),
            FOREIGN KEY (closed_by_user_id) REFERENCES users(user_id),
            CHECK (
                eligible_until_at_utc IS NULL
                OR eligible_until_at_utc >= eligible_from_at_utc
            )
        )
    """,
    "staff_notice_schedules": """
        CREATE TABLE staff_notice_schedules (
            schedule_id INTEGER PRIMARY KEY AUTOINCREMENT,
            notice_id INTEGER NOT NULL UNIQUE,
            occurrence_basis TEXT NOT NULL
                CHECK (occurrence_basis IN ('One Time', 'Calendar', 'Shift')),
            recurrence_pattern TEXT NOT NULL
                CHECK (recurrence_pattern IN ('Once', 'Daily', 'Interval Days', 'Selected Weekdays')),
            shift_applicability TEXT NOT NULL DEFAULT 'None'
                CHECK (shift_applicability IN ('None', 'Every Shift', 'Selected Shift Types', 'Specific Shift')),
            interval_days INTEGER NULL
                CHECK (interval_days IS NULL OR interval_days >= 2),
            recurrence_anchor_date TEXT NULL,
            specific_calendar_date TEXT NULL,
            specific_shift_client_id INTEGER NULL,
            specific_shift_date TEXT NULL,
            specific_shift_type TEXT NULL
                CHECK (specific_shift_type IS NULL OR specific_shift_type IN ('Day', 'Afternoon', 'Overnight')),
            one_time_due_at_utc TEXT NULL,
            created_at_utc TEXT NOT NULL,
            FOREIGN KEY (notice_id) REFERENCES staff_notices(notice_id),
            FOREIGN KEY (specific_shift_client_id) REFERENCES clients(client_id),
            CHECK (recurrence_pattern = 'Interval Days' OR interval_days IS NULL),
            CHECK (
                occurrence_basis <> 'One Time'
                OR (
                    recurrence_pattern = 'Once'
                    AND shift_applicability = 'None'
                    AND specific_calendar_date IS NULL
                    AND specific_shift_client_id IS NULL
                    AND specific_shift_date IS NULL
                    AND specific_shift_type IS NULL
                )
            ),
            CHECK (occurrence_basis <> 'Calendar' OR shift_applicability = 'None'),
            CHECK (
                occurrence_basis <> 'Calendar'
                OR recurrence_pattern <> 'Once'
                OR specific_calendar_date IS NOT NULL
            ),
            CHECK (occurrence_basis <> 'Shift' OR shift_applicability <> 'None'),
            CHECK (
                shift_applicability <> 'Specific Shift'
                OR (
                    occurrence_basis = 'Shift'
                    AND recurrence_pattern = 'Once'
                    AND specific_shift_client_id IS NOT NULL
                    AND specific_shift_date IS NOT NULL
                    AND specific_shift_type IS NOT NULL
                )
            ),
            CHECK (recurrence_pattern = 'Once' OR specific_calendar_date IS NULL)
        )
    """,
    "staff_notice_schedule_shift_types": """
        CREATE TABLE staff_notice_schedule_shift_types (
            schedule_shift_type_id INTEGER PRIMARY KEY AUTOINCREMENT,
            schedule_id INTEGER NOT NULL,
            shift_type TEXT NOT NULL
                CHECK (shift_type IN ('Day', 'Afternoon', 'Overnight')),
            FOREIGN KEY (schedule_id) REFERENCES staff_notice_schedules(schedule_id),
            UNIQUE (schedule_id, shift_type)
        )
    """,
    "staff_notice_schedule_weekdays": """
        CREATE TABLE staff_notice_schedule_weekdays (
            schedule_weekday_id INTEGER PRIMARY KEY AUTOINCREMENT,
            schedule_id INTEGER NOT NULL,
            weekday_number INTEGER NOT NULL
                CHECK (weekday_number BETWEEN 0 AND 6),
            FOREIGN KEY (schedule_id) REFERENCES staff_notice_schedules(schedule_id),
            UNIQUE (schedule_id, weekday_number)
        )
    """,
    "staff_notice_occurrences": """
        CREATE TABLE staff_notice_occurrences (
            occurrence_id INTEGER PRIMARY KEY AUTOINCREMENT,
            schedule_id INTEGER NOT NULL,
            occurrence_kind TEXT NOT NULL
                CHECK (occurrence_kind IN ('One Time', 'Calendar', 'Shift')),
            occurrence_date TEXT NULL,
            planned_client_id INTEGER NULL,
            planned_shift_type TEXT NULL
                CHECK (planned_shift_type IS NULL OR planned_shift_type IN ('Day', 'Afternoon', 'Overnight')),
            shift_id INTEGER NULL,
            is_specific_shift_occurrence INTEGER NOT NULL DEFAULT 0
                CHECK (is_specific_shift_occurrence IN (0, 1)),
            visible_from_at_utc TEXT NULL,
            due_at_utc TEXT NULL,
            due_at_is_provisional INTEGER NOT NULL DEFAULT 0
                CHECK (due_at_is_provisional IN (0, 1)),
            due_at_updated_at_utc TEXT NULL,
            occurrence_status TEXT NOT NULL DEFAULT 'Scheduled'
                CHECK (occurrence_status IN (
                    'Pending Shift',
                    'Scheduled',
                    'Active',
                    'Closed',
                    'No Shift Occurred',
                    'Cancelled'
                )),
            status_reason TEXT NULL,
            created_at_utc TEXT NOT NULL,
            shift_bound_at_utc TEXT NULL,
            status_changed_at_utc TEXT NULL,
            status_changed_by_user_id INTEGER NULL,
            FOREIGN KEY (schedule_id) REFERENCES staff_notice_schedules(schedule_id),
            FOREIGN KEY (planned_client_id) REFERENCES clients(client_id),
            FOREIGN KEY (shift_id) REFERENCES shifts(shift_id),
            FOREIGN KEY (status_changed_by_user_id) REFERENCES users(user_id),
            CHECK (occurrence_kind = 'One Time' OR occurrence_date IS NOT NULL),
            CHECK (
                occurrence_kind <> 'Shift'
                OR (
                    occurrence_date IS NOT NULL
                    AND planned_client_id IS NOT NULL
                    AND planned_shift_type IS NOT NULL
                )
            ),
            CHECK (
                is_specific_shift_occurrence = 0
                OR occurrence_kind = 'Shift'
            )
        )
    """,
    "staff_notice_deliveries": """
        CREATE TABLE staff_notice_deliveries (
            delivery_id INTEGER PRIMARY KEY AUTOINCREMENT,
            occurrence_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            requirement_status TEXT NOT NULL DEFAULT 'Required'
                CHECK (requirement_status IN ('Required', 'No Longer Required', 'Cancelled')),
            assigned_at_utc TEXT NOT NULL,
            eligibility_cutoff_at_utc TEXT NOT NULL,
            first_viewed_at_utc TEXT NULL,
            viewed_by_user_id INTEGER NULL,
            recipient_access INTEGER NOT NULL DEFAULT 1
                CHECK (recipient_access IN (0, 1)),
            status_changed_at_utc TEXT NULL,
            status_changed_by_user_id INTEGER NULL,
            current_reason_code TEXT NULL,
            current_reason_text TEXT NULL,
            access_revoked_at_utc TEXT NULL,
            FOREIGN KEY (occurrence_id) REFERENCES staff_notice_occurrences(occurrence_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (viewed_by_user_id) REFERENCES users(user_id),
            FOREIGN KEY (status_changed_by_user_id) REFERENCES users(user_id),
            UNIQUE (occurrence_id, user_id),
            CHECK (
                (first_viewed_at_utc IS NULL AND viewed_by_user_id IS NULL)
                OR
                (first_viewed_at_utc IS NOT NULL AND viewed_by_user_id IS NOT NULL)
            )
        )
    """,
    "staff_notice_delivery_history": """
        CREATE TABLE staff_notice_delivery_history (
            delivery_history_id INTEGER PRIMARY KEY AUTOINCREMENT,
            delivery_id INTEGER NOT NULL,
            event_type TEXT NOT NULL
                CHECK (event_type IN (
                    'Assigned',
                    'No Longer Required',
                    'Reinstated',
                    'Cancelled',
                    'Access Revoked',
                    'Access Restored'
                )),
            previous_requirement_status TEXT NULL,
            new_requirement_status TEXT NULL,
            previous_recipient_access INTEGER NULL
                CHECK (previous_recipient_access IS NULL OR previous_recipient_access IN (0, 1)),
            new_recipient_access INTEGER NULL
                CHECK (new_recipient_access IS NULL OR new_recipient_access IN (0, 1)),
            reason_code TEXT NULL,
            reason_text TEXT NULL,
            changed_by_user_id INTEGER NULL,
            changed_at_utc TEXT NOT NULL,
            FOREIGN KEY (delivery_id) REFERENCES staff_notice_deliveries(delivery_id),
            FOREIGN KEY (changed_by_user_id) REFERENCES users(user_id)
        )
    """,
}

INDEX_SQL = {
    "idx_staff_notices_status_effective": "CREATE INDEX idx_staff_notices_status_effective ON staff_notices(status, effective_start_at_utc, expires_at_utc)",
    "idx_staff_notices_client": "CREATE INDEX idx_staff_notices_client ON staff_notices(client_id)",
    "idx_staff_notices_priority_published": "CREATE INDEX idx_staff_notices_priority_published ON staff_notices(priority, published_at_utc)",
    "ux_staff_notices_replaces": "CREATE UNIQUE INDEX ux_staff_notices_replaces ON staff_notices(replaces_notice_id) WHERE replaces_notice_id IS NOT NULL",
    "ux_staff_notice_audience_broad_rule": "CREATE UNIQUE INDEX ux_staff_notice_audience_broad_rule ON staff_notice_audience_rules(audience_id, rule_type) WHERE rule_type IN ('Core Organization', 'All Support Workers', 'Applicable Shift Staff')",
    "ux_staff_notice_audience_role": "CREATE UNIQUE INDEX ux_staff_notice_audience_role ON staff_notice_audience_rules(audience_id, role_name) WHERE rule_type = 'Selected Role'",
    "ux_staff_notice_audience_user": "CREATE UNIQUE INDEX ux_staff_notice_audience_user ON staff_notice_audience_rules(audience_id, user_id) WHERE rule_type = 'Selected Individual'",
    "idx_staff_notice_audience_rules_audience": "CREATE INDEX idx_staff_notice_audience_rules_audience ON staff_notice_audience_rules(audience_id)",
    "idx_staff_notice_audience_rules_user": "CREATE INDEX idx_staff_notice_audience_rules_user ON staff_notice_audience_rules(user_id)",
    "ux_staff_notice_open_eligibility": "CREATE UNIQUE INDEX ux_staff_notice_open_eligibility ON staff_notice_audience_eligibility_periods(audience_id, user_id) WHERE eligible_until_at_utc IS NULL",
    "idx_staff_notice_eligibility_at_time": "CREATE INDEX idx_staff_notice_eligibility_at_time ON staff_notice_audience_eligibility_periods(audience_id, eligible_from_at_utc, eligible_until_at_utc)",
    "idx_staff_notice_eligibility_user": "CREATE INDEX idx_staff_notice_eligibility_user ON staff_notice_audience_eligibility_periods(user_id, eligible_from_at_utc)",
    "idx_staff_notice_schedule_specific_shift": "CREATE INDEX idx_staff_notice_schedule_specific_shift ON staff_notice_schedules(specific_shift_client_id, specific_shift_date, specific_shift_type)",
    "idx_staff_notice_schedule_recurrence": "CREATE INDEX idx_staff_notice_schedule_recurrence ON staff_notice_schedules(occurrence_basis, recurrence_pattern, recurrence_anchor_date)",
    "ux_staff_notice_occurrence_one_time": "CREATE UNIQUE INDEX ux_staff_notice_occurrence_one_time ON staff_notice_occurrences(schedule_id) WHERE occurrence_kind = 'One Time'",
    "ux_staff_notice_occurrence_calendar": "CREATE UNIQUE INDEX ux_staff_notice_occurrence_calendar ON staff_notice_occurrences(schedule_id, occurrence_date) WHERE occurrence_kind = 'Calendar'",
    "ux_staff_notice_occurrence_bound_shift": "CREATE UNIQUE INDEX ux_staff_notice_occurrence_bound_shift ON staff_notice_occurrences(schedule_id, shift_id) WHERE occurrence_kind = 'Shift' AND shift_id IS NOT NULL",
    "ux_staff_notice_occurrence_specific_shift": "CREATE UNIQUE INDEX ux_staff_notice_occurrence_specific_shift ON staff_notice_occurrences(schedule_id, planned_client_id, occurrence_date, planned_shift_type) WHERE occurrence_kind = 'Shift' AND is_specific_shift_occurrence = 1",
    "idx_staff_notice_occurrence_pending_shift": "CREATE INDEX idx_staff_notice_occurrence_pending_shift ON staff_notice_occurrences(planned_client_id, occurrence_date, planned_shift_type, occurrence_status)",
    "idx_staff_notice_occurrence_visibility": "CREATE INDEX idx_staff_notice_occurrence_visibility ON staff_notice_occurrences(occurrence_status, visible_from_at_utc)",
    "idx_staff_notice_occurrence_due": "CREATE INDEX idx_staff_notice_occurrence_due ON staff_notice_occurrences(due_at_utc)",
    "idx_staff_notice_delivery_user_access": "CREATE INDEX idx_staff_notice_delivery_user_access ON staff_notice_deliveries(user_id, recipient_access, requirement_status)",
    "idx_staff_notice_delivery_occurrence": "CREATE INDEX idx_staff_notice_delivery_occurrence ON staff_notice_deliveries(occurrence_id)",
    "idx_staff_notice_delivery_viewed": "CREATE INDEX idx_staff_notice_delivery_viewed ON staff_notice_deliveries(first_viewed_at_utc)",
    "idx_staff_notice_delivery_assigned": "CREATE INDEX idx_staff_notice_delivery_assigned ON staff_notice_deliveries(assigned_at_utc)",
    "idx_staff_notice_delivery_history_delivery": "CREATE INDEX idx_staff_notice_delivery_history_delivery ON staff_notice_delivery_history(delivery_id, changed_at_utc)",
    "idx_staff_notice_delivery_history_event": "CREATE INDEX idx_staff_notice_delivery_history_event ON staff_notice_delivery_history(event_type, changed_at_utc)",
}

TABLE_NAMES = tuple(TABLE_SQL)
INDEX_NAMES = tuple(INDEX_SQL)

PREREQUISITE_COLUMNS = {
    "users": {"user_id", "role", "active"},
    "clients": {"client_id"},
    "shifts": {"shift_id", "client_id", "shift_date", "shift_type"},
    "shift_staff": {"shift_staff_id", "shift_id", "user_id", "active"},
    "activity_log": {
        "activity_id", "activity_class", "activity_type", "user_id",
        "client_id", "shift_id", "related_table", "related_id",
        "summary", "details", "success",
    },
}


def table_exists(conn, table_name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone() is not None


def validate_prerequisites(conn):
    for table_name, required_columns in PREREQUISITE_COLUMNS.items():
        if not table_exists(conn, table_name):
            raise RuntimeError(
                f'Required prerequisite table "{table_name}" is missing.'
            )

        columns = {
            row[1]: row
            for row in conn.execute(
                f"PRAGMA table_info({quote_identifier(table_name)})"
            ).fetchall()
        }
        missing = required_columns - set(columns)

        if missing:
            raise RuntimeError(
                f'Required prerequisite table "{table_name}" is missing '
                f'column(s): {", ".join(sorted(missing))}.'
            )

    for table_name, id_column in (
        ("users", "user_id"),
        ("clients", "client_id"),
        ("shifts", "shift_id"),
    ):
        rows = conn.execute(
            f"PRAGMA table_info({quote_identifier(table_name)})"
        ).fetchall()
        column = next(item for item in rows if item[1] == id_column)
        primary_key_columns = [item for item in rows if item[5] != 0]

        if (
            column[2].upper() != "INTEGER"
            or column[5] != 1
            or len(primary_key_columns) != 1
        ):
            raise RuntimeError(
                f'Prerequisite key {table_name}.{id_column} must be an '
                "INTEGER single-column primary key."
            )

    if not acknowledgement_schema_is_current(conn):
        raise RuntimeError(
            "The approved acknowledgement invalidation schema must be "
            "applied before the Staff Notice tables."
        )


def create_schema(conn):
    for sql in TABLE_SQL.values():
        conn.execute(sql)

    for sql in INDEX_SQL.values():
        conn.execute(sql)


def index_metadata(conn, index_name):
    quoted_name = quote_identifier(index_name)
    return tuple(
        (row[1], row[2], row[3], row[4], row[5])
        for row in conn.execute(
            f"PRAGMA index_xinfo({quoted_name})"
        ).fetchall()
    )


def schema_metadata(conn):
    tables = []

    for table_name in TABLE_NAMES:
        row = conn.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()

        if row is None or row[0] != table_name:
            return None

        columns = tuple(
            (item[1], item[2].upper(), item[3], item[4], item[5])
            for item in conn.execute(
                f"PRAGMA table_info({quote_identifier(table_name)})"
            ).fetchall()
        )
        foreign_keys = tuple(
            (item[2], item[3], item[4], item[5], item[6], item[7])
            for item in conn.execute(
                f"PRAGMA foreign_key_list({quote_identifier(table_name)})"
            ).fetchall()
        )
        indexes = []

        for item in conn.execute(
            f"PRAGMA index_list({quote_identifier(table_name)})"
        ).fetchall():
            index_name = item[1]
            sql_row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'index' "
                "AND name = ?",
                (index_name,),
            ).fetchone()
            indexes.append((
                index_name,
                item[2],
                item[3],
                item[4],
                index_metadata(conn, index_name),
                (
                    None
                    if sql_row is None or sql_row[0] is None
                    else sql_token_signature(sql_row[0])
                ),
            ))

        triggers = tuple(
            (item[0], sql_token_signature(item[1]))
            for item in conn.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type = 'trigger' AND tbl_name = ? ORDER BY name",
                (table_name,),
            ).fetchall()
        )
        tables.append((
            table_name,
            sql_token_signature(row[1]),
            columns,
            foreign_keys,
            tuple(sorted(indexes)),
            triggers,
        ))

    return tuple(tables)


@functools.lru_cache(maxsize=1)
def expected_schema_metadata():
    conn = sqlite3.connect(":memory:")

    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE clients (client_id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE shifts (shift_id INTEGER PRIMARY KEY)")
        create_schema(conn)
        return schema_metadata(conn)
    finally:
        conn.close()


def schema_is_current(conn):
    return (
        not unexpected_staff_notice_objects(conn)
        and schema_metadata(conn) == expected_schema_metadata()
    )


def is_staff_notice_namespace(identifier):
    return "staff_notice" in identifier.casefold()


def find_staff_notice_objects(conn):
    approved_names = {
        name.casefold()
        for name in TABLE_NAMES + INDEX_NAMES
    }
    objects = []

    for schema_name, master_table in (
        ("main", "main.sqlite_master"),
        ("temp", "temp.sqlite_temp_master"),
    ):
        for row in conn.execute(
            f"SELECT type, name, tbl_name, sql FROM {master_table} "
            "WHERE type IN ('table', 'view', 'index', 'trigger')"
        ).fetchall():
            if (
                row[1].casefold() in approved_names
                or is_staff_notice_namespace(row[1])
                or is_staff_notice_namespace(row[2])
            ):
                objects.append((schema_name,) + row)

    return tuple(objects)


def unexpected_staff_notice_objects(conn):
    table_names = {name.casefold() for name in TABLE_NAMES}
    index_names = {name.casefold() for name in INDEX_NAMES}
    unexpected = []

    for row in find_staff_notice_objects(conn):
        schema_name, object_type, name, table_name, _ = row
        folded_name = name.casefold()
        folded_table_name = table_name.casefold()
        approved = False

        if schema_name != "main":
            unexpected.append(row)
            continue

        if object_type == "table":
            approved = folded_name in table_names
        elif object_type == "index":
            approved = (
                folded_name in index_names
                or (
                    folded_name.startswith("sqlite_autoindex_")
                    and folded_table_name in table_names
                )
            )

        if not approved:
            unexpected.append(row)

    return tuple(unexpected)


def run_database_checks(conn):
    integrity_rows = conn.execute("PRAGMA integrity_check").fetchall()

    if integrity_rows != [("ok",)]:
        raise RuntimeError(
            "SQLite integrity_check failed: "
            + "; ".join(str(row[0]) for row in integrity_rows)
        )

    foreign_key_errors = conn.execute(
        "PRAGMA foreign_key_check"
    ).fetchall()

    if foreign_key_errors:
        raise RuntimeError(
            "SQLite foreign_key_check reported violations: "
            f"{foreign_key_errors}"
        )


def verify_database(conn):
    if not schema_is_current(conn):
        raise RuntimeError("The Staff Notice schema is not current.")

    run_database_checks(conn)


def set_foreign_keys(conn, enabled):
    conn.execute(
        f"PRAGMA foreign_keys = {'ON' if enabled else 'OFF'}"
    )
    actual = conn.execute("PRAGMA foreign_keys").fetchone()[0]

    if actual != int(enabled):
        raise RuntimeError(
            "Unable to set the required SQLite foreign-key mode."
        )


def migrate(conn):
    if conn.in_transaction:
        raise RuntimeError(
            "The Staff Notice migration must start outside an existing "
            "transaction."
        )

    incoming_foreign_keys = conn.execute(
        "PRAGMA foreign_keys"
    ).fetchone()[0]
    migration_error = None

    try:
        set_foreign_keys(conn, True)
        validate_prerequisites(conn)
        existing_objects = find_staff_notice_objects(conn)

        if existing_objects:
            if schema_is_current(conn):
                verify_database(conn)
                return False

            raise RuntimeError(
                "A partial or incompatible Staff Notice schema already "
                "exists. No changes were made."
            )

        conn.execute("BEGIN IMMEDIATE")
        validate_prerequisites(conn)

        if find_staff_notice_objects(conn):
            raise RuntimeError(
                "Staff Notice schema objects appeared after validation."
            )

        create_schema(conn)

        if not schema_is_current(conn):
            raise RuntimeError(
                "The Staff Notice schema did not pass final verification."
            )

        run_database_checks(conn)
        conn.commit()
        return True
    except BaseException as error:
        migration_error = error

        if conn.in_transaction:
            conn.rollback()

        raise
    finally:
        try:
            set_foreign_keys(conn, bool(incoming_foreign_keys))
        except BaseException as setting_error:
            if migration_error is not None:
                migration_error.add_note(str(setting_error))
            else:
                raise


def main():
    conn = sqlite3.connect(DB_NAME)

    try:
        changed = migrate(conn)
        verify_database(conn)
    finally:
        conn.close()

    if changed:
        print("Staff Notice table migration completed.")
    else:
        print("Staff Notice schema is already current.")


if __name__ == "__main__":
    main()
