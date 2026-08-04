# NHPSG Architecture Principles

## Document Status

This is a living architectural document for the NHPSG Manager application.

It records the agreed principles that guide product design, database design, user-interface design, implementation, testing, and deployment. It should evolve deliberately as the application and operational requirements mature.

---

## 1. Vision

NHPSG is a care operations platform designed around real support-worker workflows.

Operational information should be recorded once, accurately, at the point of care. The same authoritative information may then be presented differently for support workers, managers, directors, administrators, reviewers, and future reporting tools without duplicating or fragmenting the underlying record.

The application should remain:

- practical for frontline workers;
- understandable to managers;
- auditable;
- historically reliable;
- mobile-friendly;
- extensible;
- consistent across modules;
- simple wherever possible.

---

## 2. Architecture Principle No. 1: Record Once — Present Many

Every operational event must have one authoritative source record.

Information is entered once by the worker at the point of care. Other parts of the system present the same information according to the user’s task and role.

```text
Operational Entry
        |
        v
Authoritative Source Record
        |
        +----> Activity Log
        |          |
        |          v
        |      Client Storyline
        |
        +----> Weekly Review
        |
        +----> Reports and Analytics
        |
        +----> Management Review
```

### Rules

- Do not ask workers to enter the same operational information twice.
- Do not create competing authoritative copies.
- A presentation view is not the authoritative source.
- Reports and Storyline views must not silently rewrite source records.
- Management review must augment the operational record without replacing it.

---

## 3. Operational Entry Comes First

Every module begins with the real operational workflow.

The primary question is:

> What does the worker need to record accurately and efficiently while providing care?

Operational entry should be:

- quick;
- clear;
- mobile-friendly;
- validated;
- tied to the correct client, shift, and worker;
- resistant to duplicate submission;
- safe under failed writes;
- free of unnecessary management fields.

Examples include:

- Behaviour;
- Incidents;
- Sleep;
- Activities;
- Toileting;
- Food and Fluid;
- Care;
- Housekeeping;
- Shift Notes;
- Medication, when implemented.

Management features, reporting, and analytics come after reliable operational entry.

---

## 4. Authoritative Source Records

Each operational module should have a clearly defined authoritative source table or source-record structure.

The source record should contain the complete operational information required by that module.

Examples:

- `behaviour_occurrences` for Behaviour episodes;
- `incident_reports` for Incidents;
- `shift_notes` for shared Shift Notes;
- the applicable operational table for Sleep, Activities, Toileting, Food and Fluid, Care, and Housekeeping.

### Source-record principles

- Preserve authoritative data.
- Prefer append-only creation where operationally appropriate.
- Use voiding or controlled correction rather than destructive deletion.
- Keep management review data separate from operational data.
- Do not infer or invent historical values during migration.
- Add new fields in a backward-compatible manner.
- Existing records must remain readable after module evolution.

---

## 5. Activity Log

The Activity Log is the application’s append-only operational and administrative journal.

It records meaningful events such as:

- operational records created;
- records updated where editing is permitted;
- voids and cancellations;
- shift lifecycle events;
- assignments and reassignments;
- acknowledgements;
- management actions;
- status changes;
- administrative and security changes;
- other meaningful database mutations.

### Activity Log principles

- Successful operational saves should create the appropriate Activity Log event.
- Source and Activity Log writes should occur in the same transaction wherever possible.
- A failed Activity Log write should not leave a successful source mutation behind.
- Activity Log history should not be silently rewritten.
- Internal identifiers and technical metadata should not be exposed in worker-facing views.
- Worker-safe Storyline details should be stored when the Storyline requires them.
- The Activity Log may contain events that are not visible in the Client Storyline.
- `storyline_visible` determines Storyline eligibility, not whether an event exists in the Activity Log.

---

## 6. Client Storyline

The Client Storyline is the chronological narrative of the client’s day and recent history.

It answers:

> What happened?

The Storyline is not:

- a management review screen;
- a technical audit log;
- a report;
- a configuration screen;
- a substitute for the authoritative source record.

### Storyline principles

- Use chronological presentation.
- Show worker-relevant information.
- Show sufficient detail for handover.
- Preserve full worker-entered text when operationally useful.
- Omit management-only notes, review metadata, internal IDs, and technical fields.
- Preserve HTML escaping.
- Preserve multiline text.
- Allow long content to wrap without horizontal scrolling.
- Do not use unsafe rendering.
- Prefer Activity Log data over live source-table lookups when the required Storyline detail was written at event creation.
- Historical and legacy events must continue rendering safely.
- Hidden, failed, and other-client events must remain excluded.

### Storyline presentation language

Where structured detail exists:

- use clear section headings;
- use consistent indentation;
- keep spacing compact;
- preserve the module’s category label;
- avoid technical terminology;
- avoid unnecessary links in early versions.

---

## 7. Weekly Review

A Weekly Review is a domain-specific operational review page.

It answers:

> What happened this week in this operational area?

Examples:

- Behaviour Weekly Review;
- Medication Weekly Review, when implemented;
- Incident Weekly Review, if later required;
- Sleep Weekly Review, if later required.

### Weekly Review principles

- Read from authoritative source records.
- Present episodes chronologically.
- Group records logically by day.
- Show richer domain detail than the Storyline.
- Be management-oriented.
- Use responsive cards or lists rather than wide tables when records contain narrative detail.
- Preserve legacy records without invented backfill.
- Keep voided records visible and clearly marked.
- Avoid JavaScript complexity unless actual use demonstrates a need.
- Do not make Weekly Review an operational entry screen.
- Do not make Weekly Review a trends report.

---

## 8. Reports and Analytics

Reports answer:

> What patterns are emerging?

Reports may include:

- frequencies;
- trends;
- distributions;
- time-of-day analysis;
- duration analysis;
- response effectiveness;
- compliance;
- exports;
- charts;
- summaries.

### Reporting principles

- Reports are derived views.
- Reports never become authoritative records.
- Reports should distinguish active, voided, legacy, and current-format records where relevant.
- Voided records should normally be excluded from operational totals but retained for audit reporting.
- Reporting requirements should not make frontline entry unnecessarily complex.
- Structured data should be introduced when it serves an established operational or reporting need.

---

## 9. Management Review

Management Review is a generic cross-module framework.

It answers:

> Has management reviewed this record, and was any follow-up required?

Management Review may include:

- acknowledgement;
- review status;
- reviewer and timestamp;
- optional review comment;
- management-only notes;
- correction requests;
- linked actions;
- review history.

### Management Review principles

- Store management review separately from operational source records.
- Acknowledgement means the manager has read the record; it does not mean the matter is resolved.
- Review must not overwrite the worker’s original record.
- Review comments are not automatically worker-visible.
- Storyline visibility for review events should be designed generically across modules, not implemented inconsistently one module at a time.
- Review should be per manager or authorized user where the framework requires individual acknowledgement.

---

## 10. Preserve History

Historical reliability is a core requirement.

### Rules

- Prefer voiding over destructive deletion.
- Preserve the original operational record.
- Preserve Activity Log history.
- Corrections should be traceable.
- Existing records must remain readable after migrations.
- Do not manufacture historical values.
- Do not backfill structured data from free text unless a separate, controlled, reliable data-correction process is approved.
- Past schedules and closed shifts should normally be read-only unless explicitly reopened for correction.

---

## 11. Separate Operational and Management Information

Operational records describe what happened during care.

Management records describe review, oversight, decisions, and follow-up.

These concerns must remain distinct.

### Worker-facing views should normally exclude

- reviewer identity;
- management-only comments;
- acknowledgement mechanics;
- internal IDs;
- audit metadata;
- technical timestamps;
- permissions data;
- correction history;
- configuration data.

### Management views may include

- source-record details;
- recorder identity;
- status;
- void metadata;
- acknowledgements;
- management notes;
- linked actions;
- correction requests;
- review history.

---

## 12. Worker Safety and Role-Appropriate Access

The application should show each role what is necessary for its work.

### Support Workers

- record operational information;
- view worker-safe history;
- use the Storyline for handover;
- access only the shifts and clients they are authorized to work with;
- do not see unnecessary management information.

### Program Managers and Directors

- review operational records;
- acknowledge and review where authorized;
- void or correct according to module rules;
- access Weekly Review and management workflows.

### Administrators

- perform system and data administration;
- manage users and configuration;
- access the comprehensive Activity Log;
- perform operational actions only where explicitly authorized.

### Future specialist roles

Roles such as Behaviour Consultant should receive scoped access designed around their responsibilities rather than broad administrative access.

---

## 13. Shift Context Is Authoritative

Where an operational record originates from a shift:

- the shift must exist;
- the shift must be open;
- the worker must be actively assigned where required;
- the client must come from the shift;
- the worker must not choose or override the client;
- the Activity Log should include the authoritative shift linkage;
- source records should store shift linkage when the module schema supports it.

Do not infer actual staffing from the planned schedule. Shift sign-on and sign-off remain authoritative for who actually worked.

---

## 14. Mobile-First and Accessible User Interface

Operational screens should be usable on phones and tablets.

### Interface principles

- avoid wide tables for narrative records;
- prevent horizontal scrolling;
- allow text to wrap;
- place checkboxes next to their labels;
- make full label rows clickable;
- preserve native keyboard accessibility;
- use plain-language headings;
- use clear validation messages;
- preserve entered values after validation failure;
- minimize clicks;
- do not expose technical terminology to workers;
- keep forms visually structured but not cramped.

---

## 15. Simplicity Wins

When two designs meet the operational need, prefer the simpler design that preserves future extensibility.

Examples:

- one table where one table is sufficient;
- server-rendered pages before adding JavaScript complexity;
- fixed option lists before configurable administration when the options are stable;
- small independent checkpoints instead of large multi-module rewrites;
- additive migrations instead of destructive redesign.

Simplicity does not mean ignoring authorization, auditability, validation, rollback, or history.

---

## 16. Progressive Module Lifecycle

Each operational module should mature through predictable stages.

```text
Stage 1: Operational Entry
              |
              v
Stage 2: Storyline
              |
              v
Stage 3: Weekly Review
              |
              v
Stage 4: Reports
              |
              v
Stage 5: Management Review
```

A later stage should not destabilize a completed earlier stage.

Each stage should be independently reviewable, testable, and deployable.

---

## 17. Blueprint Before Code

Significant features should follow this lifecycle:

```text
Blueprint
    |
    v
Product Review
    |
    v
Checkpoint Plan
    |
    v
Focused Implementation
    |
    v
Automated Tests
    |
    v
Manual Verification
    |
    v
Git Commit
    |
    v
Production Deployment
    |
    v
Production Smoke Test
    |
    v
Next Checkpoint
```

### Development rules

- Define the product behavior before implementation.
- Resolve important product decisions explicitly.
- Use narrow checkpoints.
- Avoid unrelated refactoring.
- Review the complete diff.
- Use isolated temporary test databases.
- Protect the real development and production databases.
- Back up production before migrations.
- Do not deploy schema-dependent code before applying and verifying the migration.
- Verify production after restart.

---

## 18. Migration Principles

Migrations must be:

- additive where practical;
- idempotent;
- backward-compatible;
- safe to run more than once;
- tested against isolated temporary databases;
- able to upgrade an existing schema without data loss;
- reflected in the canonical fresh-database creation path.

### Production migration process

1. Confirm the deployed version.
2. Back up the production database.
3. Fetch and inspect the target commit.
4. Confirm the production working tree is clean.
5. Pull with fast-forward only.
6. Run the migration against the correct production data path.
7. Verify required tables or columns.
8. Restart the service.
9. Confirm service health.
10. Perform a focused production smoke test.

---

## 19. Testing Principles

Tests should prove both success and failure behavior.

Important areas include:

- authorization;
- authoritative client and shift linkage;
- validation;
- idempotency;
- rollback;
- correct Activity Log linkage;
- Storyline visibility;
- HTML escaping;
- multiline text;
- long-text wrapping;
- legacy compatibility;
- void behavior;
- hidden, failed, and other-client exclusions;
- migration idempotency;
- mixed old and new records;
- read-only review behavior.

Tests must not access or modify the real `nhpsg.db`.

---

## 20. Current Information-Presentation Model

| Layer | Primary Question | Main Audience | Source |
|---|---|---|---|
| Operational Entry | What must be recorded now? | Support Workers | Operational workflow |
| Authoritative Record | What is the complete source record? | Application | Module source table |
| Activity Log | What meaningful action occurred? | Audit, administration, Storyline | Append-only log |
| Client Storyline | What happened? | Workers and management | Worker-safe Activity Log events |
| Weekly Review | What happened this week in this domain? | Management | Authoritative source records |
| Reports | What patterns are emerging? | Management and specialists | Derived/aggregated data |
| Management Review | Has this been reviewed and acted upon? | Authorized management | Separate generic review records |

---

## 21. Current Module Maturity

This table is a living roadmap and must be updated as modules mature.

| Module | Operational Entry | Storyline | Weekly Review | Reports | Management Review |
|---|---:|---:|---:|---:|---:|
| Shifts | Implemented | Implemented where relevant | N/A | Future | Future |
| Shift Notes | Implemented | Implemented | Notes history exists | Future | Existing review pattern |
| Behaviour (ABC) | Implemented | Implemented | In progress | Future | Planned |
| Incidents | Implemented | Implemented | Existing module views | Future | Existing review pattern |
| Sleep | Implemented | Implemented | Future | Future | Future |
| Activities | Implemented | Implemented | Future | Future | Existing review pattern |
| Food and Fluid | Implemented | Implemented | Future | Future | Future |
| Toileting | Implemented | Implemented | Future | Future | Future |
| Care | Implemented | Partial/Future refinement | Existing manager review | Future | Existing review pattern |
| Housekeeping | Implemented | Partial/Future refinement | Existing manager review | Future | Existing review pattern |
| Staff Notices | Implemented | Not applicable | Management tracking exists | Future | Acknowledgement implemented |
| Medication | Planned | Planned | Planned | Planned | Planned |

---

## 22. Behaviour Support Information — V1 Transitional Approach

NHPSG currently operates with one active client. For Version 1, the Behaviour recording page may include a static, collapsible Behaviour Support Information panel containing client-specific guidance.

This is a transitional product decision.

### V1 rules

- The panel must be collapsed by default.
- It must not block or overwhelm Behaviour recording.
- It may contain client-specific Setting Events and Fast Triggers.
- It must clearly distinguish Setting Events from Antecedents.
- It must provide concise ABC recording guidance.
- It must not alter the Behaviour source record.
- It must not write Activity Log events merely because the panel was opened.
- It must not expose management-only review information.
- It must preserve mobile usability.
- The content should be treated as guidance, not as a replacement for recording what was actually observed.

### Setting Events

Setting events are environmental, health, routine, or social conditions that may affect the likelihood of behaviour even when they did not happen immediately before the episode.

### Antecedents

Antecedents are events that happen directly before the behaviour and may predict that the behaviour will occur.

### Future replacement

When NHPSG supports multiple clients, the static V1 panel should be replaced with authoritative client-specific Behaviour Support Information associated with the selected client.

The future structure may include:

- Setting Events;
- Fast Triggers;
- effective strategies;
- communication guidance;
- sensory information;
- safety considerations;
- behaviour support plans;
- supporting documents.

The recording form should then display the correct client’s guidance without changing the operational Behaviour record.

---

## 23. Behaviour Recording Guidance Standard

Behaviour recording should focus on observable facts.

### Before the Behaviour (A)

Record what happened immediately before the behaviour.

Do not substitute a distant Setting Event for the immediate antecedent.

### Behaviour Observed (B)

Record what was observed.

Prefer:

- hit staff;
- threw an object;
- screamed;
- cried;
- resisted a prompt.

Avoid unsupported interpretations such as:

- manipulative;
- attention seeking;
- angry;
- deliberately difficult.

### Staff Response (C)

Record what staff actually did immediately after or during the Behaviour episode.

### Outcome

Record:

- duration until calm;
- how the client calmed down;
- additional relevant observations.

---

## 24. Change Control

Changes to these principles should be intentional.

When a proposed feature conflicts with this document:

1. Identify the conflict.
2. Determine whether the feature should change.
3. If the architecture itself must evolve, update this document as part of the approved change.
4. Avoid silent architectural drift.

---

## Foreword

NHPSG is intentionally developed using a blueprint-first methodology. Features are designed before implementation, delivered in small checkpoints, tested with isolated data, manually verified, and deployed carefully.

The goal is not merely to add features. The goal is to build a coherent care operations platform that can evolve for many years while remaining understandable, maintainable, auditable, and centred on real operational workflows.
