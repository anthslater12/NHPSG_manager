# Staff Notices Version 1

## Current Status and Implementation Checkpoint

**Checkpoint date:** July 27, 2026  
**Project:** NHPSG Manager Software  
**Workstream:** Staff Notices Version 1  
**Expected branch:** `integration/staff-notices-v1`  
**Current phase:** Read-only audit complete; correction is fully designed and awaiting implementation approval

---

## 1. Purpose of this file

This document preserves the exact Staff Notices Version 1 checkpoint so the work can continue in a new ChatGPT/Codex conversation without losing context.

It records:

- where the Staff Notices work currently stands;
- the authoritative blueprint;
- the defect found during the final implementation audit;
- the approved correction design;
- the exact application and test changes still required;
- transaction and rollback requirements;
- verification steps;
- safety rules for Git and `nhpsg.db`.

This is a continuation/handover document. It does not replace the approved Staff Notices blueprint.

---

## 2. Authoritative specification

The authoritative Staff Notices Version 1 specification is:

`Documentation/Staff Notices Version 1.0/Staff_Notices_V1_Final_Technical_Blueprint.docx`

The blueprint remains authoritative. The recent read-only audit compared the implementation against that blueprint and identified one remaining implementation gap in the initial publication workflow.

---

## 3. Approved Staff Notices business rules

The following decisions are approved and must remain unchanged:

1. Every Staff Notice requires acknowledgement.
2. Worker notice status must distinguish:
   - `Not Viewed`
   - `Viewed – Awaiting Acknowledgement`
   - `Acknowledged`
   - `Acknowledged Late`
   - `No Longer Required`
   - `Cancelled`, where applicable
3. Displaying a notice title on a dashboard does not count as viewing the notice.
4. Opening the full notice detail records `Viewed`, including the viewer and timestamp.
5. The recipient must explicitly acknowledge the notice.
6. Staff Notices may be directed flexibly to appropriate workers, roles, shifts, or broader audiences according to the approved blueprint.
7. Published notices and previously acknowledged notices must remain available through the appropriate history/archive interface.
8. Initial publication and subsequent reconciliation must both maintain complete assignment audit records.

---

## 4. Current project state

At this checkpoint:

- The Staff Notices Version 1 implementation has already progressed substantially.
- The final read-only implementation audit is complete.
- The audit found one specific inconsistency between initial publication and later delivery reconciliation.
- The correction has been fully designed.
- No implementation changes for this correction have yet been made.
- No files were modified during the audit.
- No database contents were changed during the audit.
- No branch was created or switched during the audit.
- Nothing was staged or committed during the audit.
- `nhpsg.db` was not modified, restored, staged, or otherwise altered during the audit.

Before implementation begins, confirm the actual repository state rather than assuming it has remained unchanged since this checkpoint.

---

## 5. Defect found by the final audit

### 5.1 Correct reconciliation behavior

When reconciliation creates a new Staff Notice delivery, the existing helper currently named:

```python
_assign_reconciled_staff_notice_delivery(...)
```

performs all three required operations:

1. Inserts the new row in `staff_notice_deliveries`.
2. Inserts an initial `Assigned` event in `staff_notice_delivery_history`.
3. Inserts a `staff_notice_delivery_assigned` event in `activity_log`.

This is the correct audit behavior.

### 5.2 Incorrect initial-publication behavior

The initial publication helper:

```python
_create_initial_staff_notice_deliveries(...)
```

currently inserts rows into `staff_notice_deliveries`, but it does not also create:

- the required initial `Assigned` row in `staff_notice_delivery_history`; or
- the corresponding `staff_notice_delivery_assigned` Activity Log event.

Therefore, initial publication and reconciliation currently produce inconsistent audit histories for the same logical operation: assigning a Staff Notice delivery.

### 5.3 Required correction

Initial publication and reconciliation must use one shared delivery-assignment helper so every newly created delivery consistently receives:

- the delivery row;
- its initial `Assigned` history row; and
- its assignment Activity Log event.

The correction must preserve the atomic, all-or-nothing publication transaction.

---

## 6. Existing correct assignment audit fields

The existing reconciliation assignment helper already writes the correct history transition.

For a newly assigned delivery, `staff_notice_delivery_history` must contain:

| Field | Required value |
|---|---|
| `delivery_id` | ID of the newly created delivery |
| `event_type` | `Assigned` |
| `previous_requirement_status` | `NULL` |
| `new_requirement_status` | `Required` |
| `previous_recipient_access` | `NULL` |
| `new_recipient_access` | `1` |
| `reason_code` | `NULL` |
| `reason_text` | `NULL` |
| `changed_by_user_id` | `NULL` |
| `changed_at_utc` | Same value as `assigned_at_utc` |

`changed_by_user_id` is intentionally `NULL` because the assignment is calculated by the system rather than manually performed by a user.

The matching Activity Log entry must contain:

| Field | Required value or rule |
|---|---|
| `activity_class` | `STAFF_NOTICE` |
| `activity_type` | `staff_notice_delivery_assigned` |
| `summary` | `Staff Notice delivery assigned: {notice title}` |
| `user_id` | `NULL` |
| `client_id` | The notice client ID |
| `shift_id` | `NULL` |
| `related_table` | `staff_notice_deliveries` |
| `related_id` | ID of the newly created delivery |
| `details` | Notice ID, occurrence ID, recipient user ID, and eligibility cutoff |
| `success` | `1` |
| `activity_datetime` | Must be populated |

Important timestamp detail:

- Delivery history uses the supplied UTC `assigned_at_utc`.
- `log_activity()` currently creates `activity_datetime` with the server's local `datetime.now()`.
- Tests must not require `activity_datetime` to equal `assigned_at_utc`.
- `log_activity()` must not be changed as part of this correction.

---

## 7. Required application changes

### File

`app.py`

### 7.1 Rename the shared helper

Rename:

```python
_assign_reconciled_staff_notice_delivery(...)
```

to:

```python
_assign_staff_notice_delivery(...)
```

The current definition was located at approximately `app.py:5801` during the audit. Line numbers may move, so locate it by function name.

### 7.2 Preserve the helper's existing atomic assignment behavior

The renamed helper must continue to:

1. Insert into `staff_notice_deliveries`.
2. Use duplicate protection:

   ```sql
   ON CONFLICT (occurrence_id, user_id) DO NOTHING
   ```

3. Return `0` if no new delivery was inserted.
4. For a new delivery, insert the initial `Assigned` history row.
5. Insert the `staff_notice_delivery_assigned` Activity Log event.
6. Return `1` only after the complete assignment audit operation succeeds.

Do not split these operations into separately committed work. They must participate in the caller's active transaction.

### 7.3 Use the shared helper during initial publication

Inside:

```python
_create_initial_staff_notice_deliveries(...)
```

replace the current raw delivery insertion with a call to:

```python
_assign_staff_notice_delivery(
    conn,
    notice,
    occurrence,
    user_id,
    assigned_at_utc,
    eligibility_cutoff_at_utc
)
```

The initial-publication function must continue to own:

- loading the publication occurrences;
- excluding `Pending Shift` occurrences;
- calculating `eligibility_cutoff_at_utc`;
- resolving the correct initial recipient user IDs;
- iterating through every intended assignment.

### 7.4 Preserve publication's fail-fast duplicate behavior

There is an important difference between publication and reconciliation:

- Reconciliation is idempotent. If a delivery already exists, helper return value `0` is a valid no-op.
- Initial publication must not silently accept an unexpected duplicate.

For each intended initial assignment, `_create_initial_staff_notice_deliveries()` must require the shared helper to return `1`.

Conceptually:

```python
assignment_created = _assign_staff_notice_delivery(
    conn,
    notice,
    occurrence,
    user_id,
    assigned_at_utc,
    eligibility_cutoff_at_utc
)

if assignment_created != 1:
    raise RuntimeError(...)
```

Use a clear Staff Notice-specific error message. An unexpected duplicate during initial publication must cause the entire publication transaction to roll back.

### 7.5 Update reconciliation call site

In:

```python
reconcile_staff_notice_deliveries(...)
```

replace the call to `_assign_reconciled_staff_notice_delivery(...)` with `_assign_staff_notice_delivery(...)`.

Reconciliation must continue adding the helper's `0` or `1` result to:

```python
result["deliveries_assigned"]
```

This preserves reconciliation's existing idempotency.

### 7.6 Do not change publication orchestration

The publication flow currently calls:

```python
_create_initial_staff_notice_occurrences(...)
_create_initial_staff_notice_deliveries(...)
```

before the guarded final `staff_notices` update and authoritative publication Activity Log event.

Keep that overall transaction structure. The new history and assignment Activity Log rows must be created inside the same publication transaction and must roll back if any later publication step fails.

### 7.7 Do not change `log_activity()`

Do not alter the general `log_activity()` timestamp convention as part of this focused correction.

---

## 8. Required publication test changes

### File

`tests/test_staff_notice_publication.py`

### 8.1 Correct `LATER_PUBLICATION_TABLES`

The current constant was identified as:

```python
LATER_PUBLICATION_TABLES = (
    "staff_notice_delivery_history",
    "acknowledgements"
)
```

After the correction, initial `Assigned` history is a required publication result and is no longer a later-stage record.

Change the constant to:

```python
LATER_PUBLICATION_TABLES = (
    "acknowledgements",
)
```

This allows `assert_no_later_publication_rows()` to continue checking genuinely later-stage records without incorrectly requiring delivery history to be empty.

### 8.2 Extend `PublicationTrackingConnection`

Add:

```python
self.delivery_history_insert_calls = 0
```

Extend `PublicationTrackingConnection.execute()` to increment that counter when it intercepts:

```sql
INSERT INTO staff_notice_delivery_history
```

The tracking wrapper already counts:

- eligibility inserts;
- occurrence inserts;
- delivery inserts;
- Activity Log inserts;
- final notice updates;
- commit, rollback, and close calls.

No separate `PublicationTrackingCursor` exists; interception happens in `PublicationTrackingConnection.execute()`.

### 8.3 Add an assignment-activity query helper

Add a focused helper, likely:

```python
delivery_assignment_activity_rows(notice_id)
```

It should select Activity Log records where:

```text
activity_type = staff_notice_delivery_assigned
```

and connect them to deliveries belonging to the specified notice.

Order results deterministically.

Keep the existing publication helper focused on:

```text
staff_notice_published
```

so the test suite can independently verify:

- one authoritative publication event; and
- one assignment event per newly created delivery.

### 8.4 Update successful one-delivery publication expectations

The successful single-delivery publication test currently expects only one Activity Log insert.

After the correction, it must expect:

```python
self.assertEqual(connection.delivery_history_insert_calls, 1)
self.assertEqual(connection.activity_insert_calls, 2)
```

The two Activity Log inserts are:

1. `staff_notice_delivery_assigned`
2. `staff_notice_published`

Replace any successful-publication assertion equivalent to:

```python
self.assertEqual(self.delivery_history_rows(notice_id), [])
```

with verification of exactly one initial `Assigned` history row.

### 8.5 Update the route-level successful publication test

In:

```python
test_post_route_publishes_with_current_database_authorization()
```

replace the expectation that delivery history is empty with an expectation for exactly one `Assigned` history row.

Retain the later-publication assertion after correcting `LATER_PUBLICATION_TABLES`.

### 8.6 Update the authoritative publication-event test

In:

```python
test_publication_writes_one_authoritative_activity_event()
```

the notice creates three deliveries.

Update the test to expect:

- three `Assigned` delivery-history rows;
- three `staff_notice_delivery_assigned` Activity Log entries; and
- exactly one `staff_notice_published` Activity Log entry.

The existing test name can remain because it refers to the one authoritative publication event, not to the total number of Activity Log rows in the transaction.

### 8.7 Add detailed field-level success coverage

Add a dedicated successful-publication test using one recipient.

Verify:

- the delivery fields;
- all initial `Assigned` delivery-history transition fields listed in Section 6;
- the assignment Activity Log class, type, summary, actor, client, related record, details, and success value;
- `activity_datetime` is populated, without comparing it with the fixed UTC publication timestamp.

The test fixture's fixed UTC time should deterministically control:

- publication time;
- eligibility timestamps;
- occurrence timestamps;
- delivery `assigned_at_utc`;
- history `changed_at_utc`.

It does not control `log_activity()`'s local `datetime.now()`.

### 8.8 Update final publication Activity Log failure expectations

In:

```python
test_activity_log_failure_rolls_back_complete_publication()
```

the configured failure occurs during the final `staff_notice_published` Activity Log insert.

After the correction, an assignment history and assignment Activity Log insert occur first.

Update:

```python
self.assertEqual(connection.activity_insert_calls, 1)
```

to:

```python
self.assertEqual(connection.delivery_history_insert_calls, 1)
self.assertEqual(connection.activity_insert_calls, 2)
```

The second Activity Log insert is counted before the trigger aborts it.

The entire transaction must roll back, including:

- eligibility;
- occurrences;
- deliveries;
- delivery history;
- assignment Activity Log;
- publication Activity Log;
- notice status changes.

### 8.9 Add rollback test: history insert failure

Add a test that forces failure while inserting the initial:

```text
staff_notice_delivery_history
```

record.

The test must prove:

- publication raises the expected database exception;
- rollback is called once;
- close is called once;
- no part of the publication survives;
- the database snapshot after failure equals the snapshot before publication;
- the notice remains `Draft`.

### 8.10 Add rollback test: assignment Activity Log failure

Add a test that forces failure specifically when inserting:

```text
staff_notice_delivery_assigned
```

into `activity_log`.

The failure mechanism must distinguish the assignment activity from the later `staff_notice_published` activity.

The test must prove complete rollback using:

```python
self.assertEqual(self.database_snapshot(), before)
```

and verify the appropriate transaction lifecycle counts.

### 8.11 Preserve snapshot design

`database_snapshot()` already captures every non-SQLite table, including:

- `staff_notice_delivery_history`;
- `activity_log`.

No snapshot architecture change is needed.

The existing before/after snapshot equality assertion is sufficient to prove that no partial audit or publication records survived a failure.

---

## 9. Existing failure tests and expected impact

The following established behaviors must remain valid:

### Eligibility failure

An eligibility failure occurs before occurrences or deliveries are created. Its existing counts should remain unchanged.

### Stale guarded notice update

The guarded final update may fail after initial assignment auditing has been attempted. Rollback must remove:

- the delivery;
- its initial history;
- its assignment Activity Log event;
- all other publication records.

### Commit failure

A controlled commit failure must roll back all publication work, including the new assignment audit records.

### Cleanup errors after a primary failure

Rollback and close cleanup errors must remain attached to the primary publication exception without replacing it.

### Close failure after a successful commit

A close failure after commit must continue to raise:

```python
StaffNoticePublicationCommittedCloseError
```

with:

- `committed = True`
- `retry_safe = False`

The committed database state should now include the delivery's:

- `Assigned` history row; and
- `staff_notice_delivery_assigned` Activity Log entry.

---

## 10. Reconciliation test considerations

### File

`tests/test_staff_notice_reconciliation.py`

The reconciliation suite already verifies part of the correct behavior:

- one reconciled delivery produces one delivery-history row;
- its `event_type` is `Assigned`;
- repeated reconciliation creates no duplicate history;
- the matching Activity Log type is `staff_notice_delivery_assigned`;
- before an occurrence becomes eligible, neither history nor assignment activity is created.

The helper rename may require only the application call-site update because the audit found no direct test references to the old private helper name.

The publication suite should provide the stronger field-level verification for the shared helper. Reconciliation tests must still pass unchanged unless a minor name-dependent detail is discovered during implementation.

---

## 11. Transaction guarantees that must not regress

Publication must remain one atomic transaction.

The following work must either all commit or all roll back:

1. Audience eligibility persistence.
2. Initial occurrence creation.
3. Initial delivery creation.
4. Initial `Assigned` delivery-history creation.
5. Initial `staff_notice_delivery_assigned` Activity Log creation.
6. Guarded transition of the notice from `Draft` to `Published`.
7. Authoritative `staff_notice_published` Activity Log creation.

There must be no intermediate commit.

If any of these operations fails before commit, the database must return to the exact pre-publication state.

---

## 12. Scope boundaries

This correction is deliberately narrow.

Do:

- share the existing correct assignment implementation between publication and reconciliation;
- add the missing initial assignment audit records;
- preserve publication's fail-fast behavior;
- update and extend focused tests;
- verify atomic rollback.

Do not:

- redesign Staff Notices;
- change approved business rules;
- change `log_activity()` globally;
- change unrelated modules;
- introduce unrelated schema changes or migrations;
- rewrite reconciliation;
- change acknowledgement behavior;
- change dashboard viewed-status behavior;
- change the authoritative publication event design.

No database migration is expected for this correction because the required tables and columns already exist.

---

## 13. Safe implementation sequence

When implementation is approved:

1. Confirm the repository root and current branch.
2. Confirm the expected branch is `integration/staff-notices-v1`.
3. Run `git status --short` and preserve all pre-existing changes.
4. Specifically record the current status of `nhpsg.db`.
5. Inspect the current implementations and tests by function/test name because line numbers may have shifted.
6. Rename the shared assignment helper.
7. Update the reconciliation call site.
8. Replace the raw initial-publication delivery insert with the shared helper call.
9. Add the required initial-publication `return == 1` guard.
10. Update test constants and the tracking connection.
11. Add assignment-activity query support.
12. Update successful-publication expectations.
13. Add detailed assignment audit assertions.
14. Add the two focused rollback tests.
15. Run focused tests.
16. Run the full Staff Notices suite.
17. Review `git diff`.
18. Confirm `nhpsg.db` was not changed by the implementation or tests.
19. Do not stage or commit until the user explicitly approves those Git actions.

---

## 14. Verification sequence

Use the repository's established Python/test environment and commands. At minimum, run:

1. Publication tests:

   ```text
   tests/test_staff_notice_publication.py
   ```

2. Reconciliation tests:

   ```text
   tests/test_staff_notice_reconciliation.py
   ```

3. The complete Staff Notices test suite.

Verification must confirm:

- initial publication creates one assignment history row per new delivery;
- initial publication creates one assignment Activity Log row per new delivery;
- there is still exactly one authoritative `staff_notice_published` event;
- reconciliation remains idempotent;
- unexpected initial-publication duplicates fail and roll back;
- history-insert failure rolls back everything;
- assignment-activity failure rolls back everything;
- final publication-activity failure rolls back earlier assignment audit rows;
- commit failure rolls back everything;
- post-commit close failure remains committed and unsafe to retry;
- no unrelated tests regress;
- `nhpsg.db` remains protected.

Use the project's actual test runner syntax after inspecting the repository. Do not invent or change the project's testing convention.

---

## 15. Git and database safety rules

The NHPSG project database requires special care.

Before and after implementation:

- inspect `git status`;
- do not discard unrelated user changes;
- do not restore or overwrite `nhpsg.db`;
- do not stage `nhpsg.db` unless the user explicitly directs it;
- do not run destructive Git commands;
- do not switch or create branches without approval;
- do not stage or commit without approval;
- do not run migrations for this correction unless a new, separately reviewed need is discovered;
- stop and ask if the repository state differs materially from this checkpoint.

If tests modify `nhpsg.db`, stop and investigate before continuing. Do not automatically restore it.

---

## 16. Exact resumption instruction for a new conversation

Use the following instruction together with this file:

> Continue the NHPSG Staff Notices Version 1 work from the attached current-status and implementation-checkpoint document. The read-only audit is complete. First verify the repository, branch, working-tree state, and `nhpsg.db` status. Do not modify anything until you have confirmed that the checkpoint still matches the code. Then summarize the exact implementation steps and wait for my approval before editing. Preserve all unrelated changes and do not stage, commit, switch branches, run migrations, or alter `nhpsg.db` without explicit approval.

If implementation is already approved, replace the final sentence requiring approval with:

> After verifying the checkpoint against the current code, implement only the documented assignment-audit correction, run the focused and full Staff Notices tests, inspect the final diff, and report the results without staging or committing.

---

## 17. Final checkpoint summary

The Staff Notices Version 1 blueprint remains authoritative.

The final read-only audit is complete and found one remaining implementation gap:

> Initial publication creates Staff Notice deliveries but does not create their required initial `Assigned` delivery-history records or `staff_notice_delivery_assigned` Activity Log records. Reconciliation already creates these records correctly.

The approved technical direction is to rename the reconciliation-specific assignment helper into a shared assignment helper and use it for both initial publication and reconciliation.

Initial publication must require every intended assignment to be newly created. Reconciliation must remain idempotent. All delivery, history, activity, publication, and status changes must remain in one transaction and roll back completely on any pre-commit failure.

The analysis and design are complete. The next phase is implementation and verification.
