# Schedule per-worker planned hours migration

Phase 6A adds nullable `planned_start_time` and `planned_end_time` columns to
`schedule_staff`. Existing assignment rows are backfilled from their parent
`schedule_shifts` defaults. Existing populated assignment values are preserved.

The migration validates strict `HH:MM` values and the current shift rules:
Day and Afternoon assignments must end after they start; Overnight assignments
may cross midnight but may not have equal start and end times. It is
transactional and idempotent. Tests use temporary databases only.

## Development

Back up the intended development database first. Resolve the target path
explicitly and verify the result before running the migration:

```powershell
venv\Scripts\python.exe add_schedule_staff_planned_hours.py .\nhpsg.db
```

The command prints the resolved absolute path. Verify the two columns, the
assignment count before and after, zero NULL assignment times, and sample
backfilled values.

`NHPSG_DB_PATH` may be used when a command-line path is not supplied. A
command-line path takes precedence over the environment variable. A missing
path fails without creating a new database.

## Production

After confirming the deployed commit, create and verify a SQLite backup. Run
the migration explicitly against the live data path, not a database beside
the application code:

```text
sudo -u nhpsg -H \
  /opt/nhpsg/venv/bin/python3 \
  /opt/nhpsg/app/add_schedule_staff_planned_hours.py \
  /opt/nhpsg/data/nhpsg.db
```

Before and after the migration, verify:

- the resolved path is `/opt/nhpsg/data/nhpsg.db`;
- the assignment row count is unchanged;
- both columns exist;
- zero assignment times are NULL;
- representative backfilled values match their parent shift defaults;
- existing assignment IDs and parent shift rows remain unchanged.

Restart the NHPSG service only after the schema-dependent application code is
later deployed. Do not run the migration from `/opt/nhpsg/app` without an
explicit live database path.
