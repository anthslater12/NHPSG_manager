# NHPSG Manager

# Management Engagement Framework Version 1 Specification

**Status:** Complete  
**Version:** 1.0  
**Framework date:** July 2026  
**Application:** NHPSG Manager  

---

## 1. Purpose

The Management Engagement Framework provides a consistent application-wide structure for management oversight of operational records.

It separates the following responsibilities:

1. Operational documentation completed by staff.
2. Confirmation that management has reviewed an operational record.
3. Ongoing management discussion and observations.
4. Follow-up work requiring assignment or completion.
5. Auditable recording of significant management activity.

The framework is intended to prevent individual operational modules from creating separate or incompatible review, management-note, follow-up, or audit systems.

All future operational modules must reuse this framework unless a formally approved framework revision requires otherwise.

---

## 2. Framework Lifecycle

The standard lifecycle is:

```text
Operational Record
        ↓
Management Review
        ↓
Management Notes
        ↓
Linked Action, when required
        ↓
Activity Log
```
---

## 3. Permission Model

Management Engagement features are currently available only to the following roles:

* Admin
* Program Manager
* Director

Support Workers must not be permitted to access:

* The Management Review Hub
* Care Management Review pages
* Housekeeping Management Review pages
* Management Notes
* Management-linked Action creation controls
* Future management-review pages for operational modules

Access restrictions must be enforced in application routes.

Hiding navigation links is not sufficient security.

A user who attempts to open a restricted management URL directly must receive an access-denied response or the application's intentional equivalent.

### Future Permission Matrix

A configurable Permission Matrix may be added in a future version.

Only the Admin role may view or modify the Permission Matrix.

The following roles must not be permitted to modify application permissions:

* Program Manager
* Director
* Behaviour Consultant
* Support Worker
* Any other privileged operational role

Permission administration must remain separate from normal management-review permissions.

---

## 4. Review Architecture

Management Reviews use the shared `acknowledgements` table.

The relevant table structure is:

```sql
CREATE TABLE acknowledgements (
    acknowledgement_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_table TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    acknowledged_at TEXT DEFAULT CURRENT_TIMESTAMP,
    comment TEXT,
    acknowledgement_type TEXT DEFAULT 'Read',
    active INTEGER DEFAULT 1,
    UNIQUE(source_table, source_id, user_id)
);
```

The shared acknowledgement helper is:

```python
def create_acknowledgement(
    conn,
    source_table,
    source_id,
    user_id,
    acknowledgement_type="Read",
    comment=None
):
```

Operational modules must use the shared helper rather than inserting acknowledgement records directly.

### Review Rules

The following rules apply:

1. Multiple authorized managers may review the same operational record.
2. Each user may review a particular operational record only once.
3. Duplicate same-user reviews must be prevented.
4. Each review must display the reviewer name and review timestamp.
5. Reviews must be created using a POST request.
6. Reviews must be recorded in `activity_log`.
7. A review must not contain ongoing management discussion.
8. Review comments must not be used as a substitute for Management Notes.

Care and Housekeeping use:

```text
acknowledgement_type = Review
```

Shift Notes currently use the shared acknowledgement framework and may use the default acknowledgement type unless separately configured.

### Review Activity Log Entry

The acknowledgement helper records:

```text
activity_class = ACKNOWLEDGEMENT
activity_type = record_acknowledged
```

Read-only access to a review page does not normally require an Activity Log entry.

---

## 3. Permission Model

Management Engagement features are currently available only to the following roles:

* Admin
* Program Manager
* Director

Support Workers must not be permitted to access:

* The Management Review Hub
* Care Management Review pages
* Housekeeping Management Review pages
* Management Notes
* Management-linked Action creation controls
* Future management-review pages for operational modules

Access restrictions must be enforced in application routes.

Hiding navigation links is not sufficient security.

A user who attempts to open a restricted management URL directly must receive an access-denied response or the application's intentional equivalent.

### Future Permission Matrix

A configurable Permission Matrix may be added in a future version.

Only the Admin role may view or modify the Permission Matrix.

The following roles must not be permitted to modify application permissions:

* Program Manager
* Director
* Behaviour Consultant
* Support Worker
* Any other privileged operational role

Permission administration must remain separate from normal management-review permissions.

---

## 4. Review Architecture

Management Reviews use the shared `acknowledgements` table.

The relevant table structure is:

```sql
CREATE TABLE acknowledgements (
    acknowledgement_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_table TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    acknowledged_at TEXT DEFAULT CURRENT_TIMESTAMP,
    comment TEXT,
    acknowledgement_type TEXT DEFAULT 'Read',
    active INTEGER DEFAULT 1,
    UNIQUE(source_table, source_id, user_id)
);
```

The shared acknowledgement helper is:

```python
def create_acknowledgement(
    conn,
    source_table,
    source_id,
    user_id,
    acknowledgement_type="Read",
    comment=None
):
```

Operational modules must use the shared helper rather than inserting acknowledgement records directly.

### Review Rules

The following rules apply:

1. Multiple authorized managers may review the same operational record.
2. Each user may review a particular operational record only once.
3. Duplicate same-user reviews must be prevented.
4. Each review must display the reviewer name and review timestamp.
5. Reviews must be created using a POST request.
6. Reviews must be recorded in `activity_log`.
7. A review must not contain ongoing management discussion.
8. Review comments must not be used as a substitute for Management Notes.

Care and Housekeeping use:

```text
acknowledgement_type = Review
```

Shift Notes currently use the shared acknowledgement framework and may use the default acknowledgement type unless separately configured.

### Review Activity Log Entry

The acknowledgement helper records:

```text
activity_class = ACKNOWLEDGEMENT
activity_type = record_acknowledged
```

Read-only access to a review page does not normally require an Activity Log entry.

---

## 5. Management Notes Framework

Management Notes provide a chronological record of management observations, decisions, analysis, and discussion relating to an operational record.

Management Notes are intentionally separate from:

* Operational comments
* Reviews
* Action comments
* General Shift Notes

The current implementation uses the shared `management_notes` table.

```sql
CREATE TABLE management_notes (
    management_note_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_table TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    note_text TEXT NOT NULL,
    visibility TEXT NOT NULL DEFAULT 'management_only',
    created_by_user_id INTEGER NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    active INTEGER NOT NULL DEFAULT 1,
    shared_at TEXT,
    shared_by_user_id INTEGER
);
```

The shared helper functions are:

```python
get_management_notes()
add_management_note()
get_management_note_count()
```

### Management Note Rules

The following principles apply:

1. Notes are append-oriented and should not overwrite previous management observations.
2. Each note displays its author and timestamp.
3. Notes are maintained in chronological order.
4. Notes are currently visible only to management roles.
5. The database structure supports future controlled sharing without schema redesign.
6. Operational records remain unchanged when Management Notes are added.

### Visibility

Current visibility value:

```text
management_only
```

Future versions may support additional visibility levels while preserving backward compatibility.

### Activity Log

Creating a Management Note records:

```text
activity_class = MANAGEMENT_NOTE
activity_type = management_note_added
```

---

## 6. Linked Action Framework

Operational records requiring follow-up use the shared Action Framework.

Actions are not stored within operational modules.

Instead, operational modules create linked Actions using the generic Action helper.

The shared helper is:

```python
def create_action(
    conn,
    title,
    description=None,
    source_table=None,
    source_id=None,
    shift_id=None,
    created_by_user_id=None,
    assigned_to_user_id=None,
    priority="Medium",
    due_date=None
):
```

Operational modules must not duplicate Action insertion logic.

### Action Sources

Current source tables include:

| Operational Module | Source Table                      |
| ------------------ | --------------------------------- |
| Care               | `shift_care_task_entries`         |
| Housekeeping       | `shift_housekeeping_task_entries` |

Future operational modules should register their own source table while continuing to use the same Action Framework.

### Activity Log

Creating an Action records:

```text
activity_class = ACTION
activity_type = action_created
```

Status changes, completion, and closure continue to follow the Action Framework specification rather than this document.

---

## 7. User Interface Standards

The Management Engagement Framework establishes common user interface standards across all operational modules.

Future modules should present management information in a consistent manner so that managers do not need to learn different review interfaces for different operational areas.

### Standard Detail Page Layout

Management detail pages should present information in the following order:

1. Operational Record
2. Staff Working on Shift
3. Reviews
4. Management Notes
5. Linked Actions

This layout is currently implemented for:

* Care
* Housekeeping

Future operational modules should follow the same structure.

---

### Standard Terminology

The following terminology is standardized throughout the application.

| Preferred Term         | Do Not Use            |
| ---------------------- | --------------------- |
| Operational Record     | Worker Record         |
| Operational Comment    | Worker Comment        |
| Staff Working on Shift | Assigned Worker       |
| Reviews                | Acknowledgements (UI) |
| Management Notes       | Manager Comments      |
| Linked Actions         | Follow-up Tasks       |
| Source Record          | Original Record       |

Database table names and helper function names may continue to use existing terminology where appropriate.

---

### Management Detail Tables

Management detail pages use the shared CSS class:

```text
management-detail-table
```

This provides:

* Consistent label-column widths
* Improved readability
* Uniform appearance across operational modules

Create Action source-record tables also use this class.

---

### Wide Table Standard

Management review lists use the shared table wrapper:

```text
table-scroll
```

This prevents large review tables from extending outside the application card layout.

Future management-review pages should use this wrapper whenever tables may exceed the available page width.

---

### Link-style POST Buttons

Review actions use POST forms styled as hyperlinks.

The shared classes are:

```text
inline-form
link-button
```

These classes provide:

* Accessible keyboard navigation
* Consistent appearance
* Protection against accidental GET-based state changes

Future review actions should continue using this approach.

---

### Viewport Position Preservation

Management review pages preserve the manager's viewing position after POST operations.

The shared implementation stores:

* Current page path
* Selected row identifier
* Row position within the viewport

After page reload, JavaScript restores the reviewed row to approximately the same position on the screen.

The current implementation uses:

```text
preserve-review-position
```

Future versions may rename this class to a more generic name if the implementation is expanded beyond management review pages.

All affected templates and JavaScript selectors must be updated together if this occurs.

---

### Styling Principle

Shared framework styling should always be preferred over module-specific CSS whenever practical.

New operational modules should reuse existing shared classes before introducing additional styles.

---

## 8. Activity Log Standards

The Activity Log provides the application's permanent audit trail.

Significant management operations must create Activity Log entries.

Typical examples include:

* Management Reviews
* Management Notes
* Action creation
* Action status changes
* Session timeouts
* User logins
* User logouts
* Password resets
* Password changes

Read-only operations normally do not require Activity Log entries.

Future operational modules must follow the existing Activity Log conventions rather than creating module-specific audit mechanisms.

---

## 9. Framework Integration Requirements

Every future operational module must integrate with the Management Engagement Framework.

Each module should provide:

* Operational administration
* Operational recording during shifts
* Management Review
* Management Notes
* Linked Actions
* Reporting

Modules must reuse the existing shared frameworks wherever possible.

Future modules must not introduce separate:

* Review tables
* Review helper functions
* Management Note systems
* Action systems
* Permission architectures
* Viewport restoration mechanisms
* Activity logging systems
* CSS frameworks for management pages

Instead, modules should integrate with the existing generic framework.

Examples of future operational modules include:

* Medication
* Behaviour Monitoring
* Appointment Tracking
* Community Access
* Goal Tracking
* Financial Support Records

Each new module should implement only its operational functionality while relying on the shared management architecture.

---

## 10. Version History

### Version 1.0 – July 2026

Initial framework release.

Major components:

* Management Review Framework
* Management Notes Framework
* Shared Action Framework integration
* Shared Activity Log integration
* Standard management permissions
* Standard page layouts
* Shared CSS conventions
* Viewport position preservation
* Session Security Framework compatibility

Operational modules implemented under Version 1:

* Care
* Housekeeping
* Shift Notes (review workflow)

---

# Framework Declaration

Management Engagement Framework Version 1 is declared complete.

The framework establishes the standard architecture for management oversight throughout the NHPSG Manager application.

Future operational modules should extend this framework rather than creating independent management-review, management-note, action, permission, or audit systems.

This document should be updated only when a formally approved framework revision requires changes to the underlying architecture.
