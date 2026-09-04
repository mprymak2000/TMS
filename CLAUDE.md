# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TMS (Session Management System) — a commercial SaaS platform for recurring-session service businesses: tutoring, therapy, personal training, coaching, and similar. One-on-one or small-group sessions. Manages practitioners, clients, scheduling, and bookings. Currently single-tenant (one business per deployment); multi-tenant is the intended direction, not yet built. The current user/owner is also a practitioner with `pay_rate=0`. Treat all architectural decisions as you would for a real startup shipping to paying customers.

## Stack

- **Backend**: FastAPI + SQLAlchemy ORM + Pydantic v2 + PostgreSQL
- **Frontend**: React + TypeScript + Vite + Tailwind CSS
- **DB**: PostgreSQL 16 via Docker
- **Dependency management**: Poetry (`backend/pyproject.toml`) — cross-platform; on macOS via Colima (`colima start` before `docker compose up`), on other machines whatever Docker runtime is installed.

## Running the Backend

```bash
# Start the database
docker compose up -d

# From backend/
poetry install
poetry run uvicorn main:app --reload
```

API docs available at `http://localhost:8000/docs`.

To reach the backend from another device on the local network (e.g. testing the booking page on a phone), bind to all interfaces instead:

```bash
poetry run uvicorn main:app --reload --host 0.0.0.0
```

Frontend's `npm run dev` already runs `vite --host`, so it's LAN-reachable by default. `main.py` CORS is pre-configured with `allow_origin_regex=r"http://192\.168\.\d+\.\d+:\d+"` to accept requests from LAN clients.

## Running Tests

```bash
# From backend/
poetry run pytest

# Single test file
poetry run pytest tests/test_students.py

# Single test
poetry run pytest tests/test_students.py::test_create_student
```

Tests use SQLite in-memory — no Docker required. Test setup is in `backend/tests/conftest.py` which overrides the `get_db` dependency. `backend/conftest.py` adds the backend folder to the Python path.

## Resetting the Database

When model columns change, wipe and recreate the volume:

```bash
docker compose down -v && docker compose up -d
```

`create_all` only creates missing tables — it does not ALTER existing ones.

## Running the Frontend

```bash
# From frontend/
npm run dev
```

Frontend runs at `http://localhost:5173`. CORS is configured in `main.py` to allow this origin.

## Google Calendar Integration

- Service account: a dedicated Google Cloud service account, shared access to the owner's Google Calendar (see your own GCP project for the exact service account email)
- Credentials stored as `GOOGLE_SERVICE_ACCOUNT_JSON` in `backend/.env` (full JSON inlined, not a file path)
- Load pattern:
  ```python
  import json, os
  from google.oauth2 import service_account
  creds = service_account.Credentials.from_service_account_info(
      json.loads(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")),
      scopes=["https://www.googleapis.com/auth/calendar"]
  )
  ```
- Service account has been shared access to owner's Google Calendar

## Architecture

```
backend/
  main.py          # App entry point, registers routers, CORS middleware, runs create_all on startup
  database.py      # Engine, SessionLocal, Base, get_db() dependency
  models.py        # SQLAlchemy ORM models (Student, Tutor, Lesson, Schedule, ScheduleDay, EventType, EventTypeAvailability, BookingSeries, Booking)
  schemas.py       # Pydantic schemas: *Create, *Update, *Response per entity
  initialize_database.py  # Local-only seed script (gitignored, real data) — see initialize_database.example.py for the committed template
  booking_utils.py # _ensure_occurrence / _ensure_occurrence_by_token — materializes a series occurrence on demand, idempotent
  tasks.py         # Procrastinate app + periodic jobs (extend_all_series daily cron, weekly reminder job)
  worker.py        # Procrastinate worker entry point
  routers/
    students.py
    tutors.py
    lessons.py
    schedules.py              # CRUD for tutor schedules; is_default flip logic; 409 on duplicate name per tutor
    event_types.py            # CRUD for bookable event types; 409 on duplicate name; duration validation
    event_type_availability.py  # Links tutor+schedule to an event type (junction table); PUT only updates schedule_id
    available_slots.py        # GET /available-slots — separate router, own prefix (see Key Business Logic below)
    bookings.py               # Full booking CRUD + Google Calendar integration
    settings.py                # Business-wide singleton settings (business_timezone); get-or-create pattern
    cancellation_policies.py  # Unregistered — policy fields moved onto EventType directly, kept for reference
frontend/
  src/
    main.tsx           # Entry point, wraps App in BrowserRouter
    App.tsx            # Root component, sidebar + header layout, React Router routes; /book/:eventTypeId and /my-bookings outside admin layout
    LessonsTable.tsx   # Lessons page: view toggle (All/Month/Week), toolbar, table with inline edit/delete/selection
    LessonRow.tsx      # Extracted row component: main row + expanded detail/edit/delete confirm
    LessonAddModal.tsx # Add lesson modal with dirty-check discard confirmation
    BulkAddCard.tsx    # Bulk add form card (per-row validation, atomic submit)
    useLessons.ts      # Custom hook: all lesson state, filters, derived groupings, handlers
    Availability.tsx   # Availability page: schedule cards + inline create/edit/delete; success toast
    ScheduleForm.tsx   # Schedule create/edit form (extracted component); multi-period days, timezone, dirty check
    EventTypes.tsx     # EventTypes admin page: cards list only, navigates to EventTypePage for create/edit (diverged from Availability's inline pattern)
    EventTypePage.tsx  # Routed page (/event-types/:id, ?tab=) — multi-tab event type editor (details/duration/recurrence/hosts/cancellation/limits/booking), per-tab error indicators
    BookingsLayout.tsx # Layout route mounted at /bookings and /my-bookings — tab bar (NavLink), roster fetch (tutors/eventTypes, once), toast; renders <Outlet context={BookingsOutletContext}>
    ScheduleTab.tsx    # /bookings and /my-bookings index route — Day/Week/Month/custom date-range pill, paginated booking list
    RecurringTab.tsx   # /bookings/recurring and /my-bookings/recurring — RecurringList (day-grouped SeriesRow cards), cancel-series modal
    RequestsTab.tsx    # /bookings/requests only (no customer route) — pending-request cards, approve/deny modal; no isCustomer/email needed
    BookingToolbar.tsx # Shared by all three tabs above: FiltersMenu, ActiveFilterChips, OrderToggle, LoadMoreSentinel, BookingFilters/LoadErrors types
    BookingRow.tsx     # Shared row component — admin menu (reschedule/cancel/delete/no-show) vs customer-mode compact view
    Tutors.tsx         # Tutors admin page: cards list, calendar_id + check_calendar_conflicts inline edit
    ManageOccurrence.tsx # Standalone page at /manage-occurrence/:token — customer email-link cancel/reschedule for one occurrence
    ManageSeries.tsx   # Standalone page at /manage-series/:token — customer email-link cancel/reschedule for a whole series
    Toast.tsx / useToast.ts # Shared toast notification component + hook
    utils.ts           # Shared helpers: formatDate/formatTime, extractError, tutorBubbleClass/tutorInitials
    BookingPage.tsx    # Public booking page at /book/:eventTypeId — no sidebar, 3 steps: pick/contact/done
    types.ts           # TypeScript interfaces matching API response schemas
    index.css          # Global styles: Tailwind, Inter font, Mantine focus overrides
    assets/
      outlook-icon.svg # Official Microsoft Outlook 2025 icon (gradient SVG from Wikimedia Commons)
docker-compose.yml # PostgreSQL service with named volume
```

## Key Business Logic

**Lesson fee & tutor payout** (in `routers/lessons.py`):
- Fee: use `fee_override` if provided, else `hrs * student.rate`
- Tutor payout — three cases in priority order:
  1. `tutor_pay_override` provided → use it directly
  2. `hrs == 0` and `fee_override` provided (cancelled lesson with penalty) → `fee_override * (tutor.pay_rate / student.rate)`
  3. Otherwise → `hrs * tutor.pay_rate` (normal lesson; fee_override does not affect tutor payout)
- `is_fee_overridden` and `is_tutor_payout_overridden` flags are set accordingly
- `Lesson.fee`/`Lesson.tutor_payout` are stored columns, computed once at creation from whatever `student.rate`/`tutor.pay_rate` was *at that time* — never re-derived live from the student/tutor's current rate. This is why a later rate change doesn't retroactively alter past lessons' recorded fee/payout: the financial history is denormalized onto each `Lesson` row, not looked up dynamically. Relevant to the planned `Contact`/`Student` identity split (see `tms-roadmap` skill) — `Student.rate` can safely stay a single current-value field (no versioning/history needed on it, no need for `Student` to become a multi-row per-enrollment-stint table) because past financial records are already immune to rate edits at the `Lesson` level.

**Schema conventions**:
- `*Create` — fields required to create a record
- `*Update` — fields sent in PUT (full object, required fields stay required)
- `*Response` — what the API returns; includes computed/DB fields like `id`, `fee`, `tutor_payout`
- `fee_override` / `tutor_pay_override` exist only in schemas (excluded from `model_dump` before passing to ORM)
- `birthday`, `first_name`, `last_name` on Student are immutable — set on create only, not in `StudentUpdate`
- `start_date` and `is_active` are on Student — `start_date` uses first-of-month convention for month/year tracking
- `first_name`/`last_name` are not in `TutorUpdate` either (immutable)

**Bulk create** (`POST /lessons/bulk_create`): validates all student/tutor IDs upfront using dict lookups (O(1)), then `db.add_all()` atomically. Returns 404 if any ID is invalid — entire batch rejected.

**Naming conventions**: `_in` suffix for input params (`lesson_in`), `db_` prefix for queried ORM objects (`db_student`).

**Delete protection, by referenced entity** — what's allowed to reference what, and what happens on delete:
- **Tutor** — referenced by `Lesson`, `Booking`, `BookingSeries` (all app-level RESTRICT, 409 if any exist — hard delete only succeeds with zero references of any kind, past or future; `is_active=False` is the normal offboarding path otherwise), `Schedule` (DB-level `CASCADE` — safe only because the three RESTRICTs above already guarantee zero bookings exist by the time a tutor delete reaches Postgres), `EventTypeAvailability` (DB-level `CASCADE` — junction row, nothing worth preserving).
- **Schedule** — referenced by `EventTypeAvailability` (DB FK is `CASCADE`, but `delete_schedule` 409s first while any link exists — the cascade only ever fires for an already-unlinked schedule, so this behaves as a RESTRICT from the outside); the *default* schedule additionally can't be deleted at all until another schedule is made default first.
- **EventType** (planned: `BookingLink`) — **archive only, no hard delete at any child count** (planned — see "BookingLink data model" below; not yet implemented, `delete_event_type` currently has no guard at all and raises a raw unhandled `IntegrityError`/500 if any bookings exist). Retiring one sets `status='archived'`: URL 404s, calendar rules go inert, row goes read-only, slug released for reuse, gone from the admin list (a reversible `paused` state sits between the two) — but the row never leaves, so `Booking.booking_link_id` stays NOT NULL and non-dangling forever. That permanence is what lets bookings be grouped by *source* and, more importantly, bulk-reassigned to a live link to make them reschedulable again; the booking's own frozen `event_type` string separately carries its *kind*. Because nothing is ever hard-deleted, the `EventTypeAvailability` `CASCADE` on this FK becomes dead code — no delete ever reaches Postgres.
- **Student** — referenced by `Lesson` (app-level RESTRICT, 409; cascade delete intentionally removed to protect financial records). `Booking.student_id`/`BookingSeries.student_id` are rarely populated today (see the `tms-roadmap` skill's `Contact`/`Student` identity-split note) and have no delete guard.

**Tutor `is_active`**: Both Student and Tutor have `is_active`. Retiring a student/tutor means setting `is_active=False`, not deleting them. For Tutor specifically, this is enforced, not just a display flag: `create_booking`/`_reschedule_booking`/`_reschedule_series` (`routers/bookings.py`) reject an inactive `tutor_id`, `get_available_slots` excludes inactive tutors, and `extend_single_series` (`tasks.py`) stops materializing new occurrences for a series whose tutor has gone inactive. Already-confirmed/already-materialized bookings are untouched either way.

**Booking system** (`routers/bookings.py`):
- `POST /bookings/` — creates Google Calendar event first, then DB record atomically. Recurring event types: creates one RRULE Google Calendar event + one `BookingSeries` row + one `Booking` row per occurrence (generated inline via a loop). Standalone: one event + one `Booking` row. If DB fails, compensating delete on Calendar. If compensating delete also fails, logs warning.
- `PUT /bookings/{id}` — contact-info-only update (student/parent name, email, phone, is_no_show). No Google Calendar calls. Plain DB write. `is_no_show=True` is how no-show is recorded — no separate PATCH endpoint.
- `POST /bookings/{booking_id}/reschedule` — atomic saga: for series bookings, patches specific RRULE instance via `events().instances()`; for standalone, creates new event + deletes old. Inserts new `Booking` row, soft-deletes old (`status='rescheduled'`, `rescheduled_to=new_id`).
- `DELETE /bookings/{id}` — soft-delete one occurrence (`status='cancelled'`). For series: patches specific RRULE instance cancelled via `events().instances()`. For standalone: deletes calendar event.
- `DELETE /bookings/{id}/permanent` — hard delete with optional `cascade` param to also delete predecessor (booking that `rescheduled_to` this one).
- `DELETE /booking-series/{id}` — truncates RRULE to today (`UNTIL=YYYYMMDD`), bulk-deletes future occurrence rows, soft-deletes series (`is_active=False`).
- `PUT /booking-series/{id}` — truncates old RRULE, creates new RRULE event on (potentially new) tutor's calendar, drops future occurrence rows, updates series metadata, regenerates **only occurrence 1** (not the whole series) — later occurrences resolve implicitly from the series' updated pattern, same as any indefinite series.
- `GET /bookings/available-slots` — three-mode branched algorithm in `routers/available_slots.py` (see module docstring for full complexity analysis and edge cases). Modes: `standalone` (non-recurring), `finite` (expires_on or recur_weeks set), `infinite` (neither). Infinite mode runs `thin_schedule_dateless` first — subtracts existing infinite series from the schedule in (weekday, time) space O(S+R), resolves survivors to concrete dates once. All modes then run a shrinking-batch week-over-week two-pointer sweep O(N×(C+R+B)) vs naive O(C×N×(R+B)). Edge cases: pre-time_min busy spill, midnight-crossing sessions, Sun→Mon dateless seam, UTC time_min shifting to prior local day. All times in business canonical timezone (Settings.business_timezone).
- **Self-exclusion on reschedule** — a booking must not count as busy against itself, or it can't be nudged into a slot overlapping its own current time (4:00–5:30 → 4:15–5:45). `get_available_slots` takes `exclude_booking_id`/`exclude_series_id`; the endpoint accepts them as `exclude_ref`/`exclude_series_ref` (`public_id`s, resolved to internal PKs, no-op'd if unresolvable since a virtual occurrence has no row and isn't in `busy_dict` anyway). `BookingPage.tsx` passes them from `location.state` on both reschedule paths. Two things this deliberately does **not** cover, both tracked in the `tms-roadmap` skill: the returned set still includes the booking's *exact* current slot (rejected at submit by guards in `_reschedule_booking`/`_reschedule_series` — should be dropped server-side instead so the picker never offers it), and rescheduling a single occurrence of an *infinite* series is still blocked, because the block comes from that series' `(weekday, time)` band in `inf_rules`, not from the booking row — the fix is to add that occurrence's date to the rule's `dev_starts` holes.
- Recurring bookings: `recurring=True` adds `RRULE:FREQ=WEEKLY` (no BYDAY — day inferred from DTSTART by Google). `UNTIL=YYYYMMDD` appended when truncating or when series has a `recur_until` date.
- Indefinite series materialization: for finite series, all occurrence rows are generated at creation time up to `recur_until`. For indefinite series (`recur_until IS NULL`), only a sparse rolling window is kept materialized — `tasks.py`'s `extend_all_series` (Procrastinate `@app.periodic`, daily cron `0 2 * * *`) finds active indefinite series and fans out one `extend_single_series` job per series, which calls `_ensure_occurrence` (`booking_utils.py`) to materialize the next occurrence as needed. No large pre-generated buffer, no `generated_through` column (doesn't exist — this state is implicit).
- Opaque ids: `Booking`/`BookingSeries` have a `public_id` (UUID) alongside their internal integer PK; API responses (`id`, `series_id`, `rescheduled_to`) expose only `public_id` — the integer PK is never serialized. For a series-bound `Booking`, `public_id` is set explicitly at creation/materialization time (not computed at read time) to the composite form `"{series.public_id}:{unix_timestamp}"`, mirroring Google Calendar's recurring-instance-id scheme (`{baseEventId}_{timestamp}`) — write-once, not recomputed on every serialize. Standalone bookings just get a plain generated UUID.
- `resolve_ref(ref, db, settings)` (`booking_utils.py`): resolves either form of the ref above to a `Booking` row — tries a direct `public_id ==` lookup first (covers standalone bookings and any already-materialized series occurrence), and only on a miss falls back to parsing the composite form and calling `_ensure_occurrence` to materialize it. **Materialization only happens on routes representing genuine write intent** (`reschedule`, cancel/`DELETE`, `DELETE .../permanent`, contact-info `PUT`) — `GET /{booking_id}` deliberately does a plain non-materializing lookup and 404s on a ref that isn't a real row yet. Browsing virtual (not-yet-materialized) occurrences — e.g. a paginated customer booking list — must construct them in memory without persisting; only an actual client action should turn a virtual occurrence into a real row.
- Route ordering: `/available-slots`, `/booking-series/{id}` must appear before `/{booking_id}` in bookings.py to avoid FastAPI matching literals as ints.
- **Pagination (`GET /bookings/`, `GET /booking-series`, `GET /booking-series/{id}/occurrences`) — cursor-based, current behavior:**
  - `cursor` (opaque, base64) is the only pagination input besides `page_size`. `encode_cursor`/`decode_cursor` (`booking_utils.py`) pack `start` timestamp + `public_id` tiebreaker + a fingerprint of the filters/time-window the cursor was minted under; a cursor replayed against a different filter combination is rejected rather than silently returning a mismatched page.
  - The materialized query seeks via the cursor's `(start, public_id)` tuple instead of re-fetching the whole window every call; `_virtual_occurrences`/`scoped_virtual_occurrences` start walking from the cursor instead of always from `series.dtstart`. Cost is `O(page_size)` regardless of depth.
  - Response carries `next_cursor` (null once exhausted), no `total`/`has_more`.
  - `GET /bookings/pages` is the separate, still-available `total`/page-number-returning endpoint (bounded `time_max`, `BookingPagedListResponse`) — kept for API completeness, not called by the frontend, which uses cursor-based Next/Prev everywhere.
  - Design: `.claude/plans/cursor-pagination-and-endpoint-split.md` (implemented).
- **Filtering / facets** (`booking_utils.py`): `GET /bookings/` and `GET /booking-series` both take `tutor_ids`/`event_type_ids`/`student` filters and return `facets` alongside `items` in the same response — the self-excluding, Google-Flights-style filter-checklist options, not just the filtered results.
  - `apply_scope_filters(query, model, tutor_ids, event_type_ids, student_pairs, email=None, exclude=None)` — the one shared filter-applier for both `Booking` and `BookingSeries` (same column names on both). `exclude` skips one dimension's own clause — this is what makes self-exclusion possible. Students match on `(student_first, student_last)` exact pairs via `tuple_(...).in_(...)`, not independent `.in_()` calls on each name — matching first/last separately would cross-match two different guests who happen to share only one name. Pairs travel over the wire as a pipe-delimited `"First|Last"` string (same composite-string convention as `public_id`), decoded server-side.
  - `compute_timeline_facets(...)` / `compute_series_facets(...)` — call `apply_scope_filters` three times per request, once per facet dimension, each time excluding that one dimension so its own filter never narrows its own options. For the timeline version, series facets are existence-checked via `_virtual_occurrences(series, time_min, time_max, count=1, settings)` — reuses the real occurrence-window walk rather than reimplementing date math, `count=1` since facets only need "does this series contribute anything here," not the full list.
  - **Self-exclusion only protects a facet from its own filter** — it does nothing about *other* active filters (or the time window) legitimately narrowing a selected value out of the response. Handled backend-side now: `compute_timeline_facets`/`compute_series_facets` union the currently-selected values back into each facet's own response before returning, so a selection never silently disappears from its own facet — no frontend fallback needed anymore.
- Datetimes stored as UTC (`DateTime(timezone=True)`). `BookingCreate`/`BookingReschedule` schemas convert client-local time to UTC via `model_validator(mode="after")` using `zoneinfo`. `Booking.timezone` is the booker's timezone captured at booking time — consumed once as a write-time UTC-conversion input, then not read again by any current frontend display path (`utils.ts`'s `formatDate`/`formatTime` and `BookingPage.tsx` both format using the *current* viewer's live-detected timezone, ignoring the stored value, which is strictly more correct for a live browser session). The stored value still earns its keep for a context with no live browser to detect from — the not-yet-built confirmation/reminder emails (see Known TODOs) — where it's the only available signal for which zone to render in, accepting that it can go stale if the booker has since moved/traveled.
- **SQLite drops tzinfo on read** (tests only — Postgres preserves it): a `DateTime(timezone=True)` column comes back naive from SQLite even though it's tz-aware in prod. Two failure modes this causes if unguarded: comparing a naive value against an aware one raises `TypeError`, and calling `.timestamp()` on a naive `datetime` silently uses the *local system clock's* timezone instead of UTC. Standard guard used throughout `booking_utils.py`: `dt if dt.tzinfo else dt.replace(tzinfo=UTC)` before any comparison or `.timestamp()` call — assumes naive means UTC, which matches how everything here is actually stored.
- Manage links (`/manage-occurrence/:ref`, `/manage-series/:ref`) are keyed directly off `public_id` — no separate token concept. See the `public_id`/`resolve_ref` entries above.
- `google_event_id` is non-nullable on `Booking` — a booking without a calendar event is a broken record.

**Schedule system**:
- `Schedule` — belongs to a tutor (`tutor_id`), has a `name`, `is_default` flag, `timezone`, and a list of `ScheduleDay` rows. Name is unique per tutor (`UniqueConstraint("tutor_id", "name")`).
- `ScheduleDay` — one row per time period per day (`day_of_week` 0–6, `start_time`/`end_time`). Multiple rows per day allowed to support non-contiguous periods (e.g. 9–12, 2–5).
- `EventTypeAvailability` — junction table linking `event_type_id` + `tutor_id` + `schedule_id`. Unique on `(event_type_id, tutor_id)`. PUT on this endpoint only allows changing the `schedule_id` (tutor reassignment not supported). This is what available-slots queries to know which schedule applies per tutor per event type.
- `is_default` flip: creating/updating a schedule with `is_default=True` automatically sets all other schedules for that tutor to `is_default=False`. Updating an existing default to `is_default=False` is blocked (must set another as default first). Cannot delete the default schedule, and `delete_schedule` also 409s if the schedule is still linked to an `EventType` via `EventTypeAvailability` — must reassign those event types to a different schedule first.
- Deleting a tutor or event type cascades to `EventTypeAvailability` rows (`ondelete="CASCADE"` on both FKs). Deleting a tutor also cascades to their `Schedule` rows (and each schedule's `ScheduleDay` rows) — `Schedule.tutor_id`/`ScheduleDay.schedule_id` are both `ondelete="CASCADE"`, safe only because `DELETE /tutors/{id}` already 409s while any `Booking`/`BookingSeries` references the tutor, so by the time the cascade reaches Postgres zero bookings exist. See `tms-roadmap` skill's delete-protection backlog entry and the "Delete protection" note above for the full picture, including the planned `EventType`→`BookingLink` rename and soft-delete-only redesign ("BookingLink data model" below), after which this entity's `CASCADE` never fires.
- `available-slots`: all schedule and series times stored in business canonical timezone (`Settings.business_timezone`). `Schedule.timezone` and `BookingSeries.timezone` are nullable/redundant — pending refactor to load from Settings directly. On timezone change: shift all stored times by old→new offset using current DST state, then update Settings.
- **Known limitation, not being addressed now**: one global `Settings.business_timezone` assumes a single-location business. A business spanning multiple physical locations in different timezones (a franchise model) isn't representable — that would need timezone scoped per-tutor (or a future `Location` entity) rather than one app-wide value, and every booking would need its zone denormalized at creation from whichever tutor produced it, same freeze-at-creation pattern as the planned per-booking policy denormalization ("BookingLink data model" below). Deliberately deferred — no multi-location feature is currently planned. See `tms-roadmap` skill.

**Two entry points, two rule regimes.** Every change to a booking arrives through one of two doors, governed completely differently. Conflating them is where the superseded invariant below went wrong.

- **The booking page — customer-facing, fully rule-governed.** Availability, buffers, caps, lead time, booking horizon, cancel/reschedule notice floors. This is the only path that reads a `BookingLink`'s calendar rules.
- **The admin surface — unrestricted.** An admin can put a booking anywhere: overlapping another, outside any schedule, past the caps, in the past. If a practitioner wants two sessions at once, that's their call; the program doesn't second-guess it.

**Today the admin has no native surface and borrows the booking page**, so they inherit its rules by accident rather than by design. Planned (see the `tms-roadmap` skill): direct-add and edit-in-place writing `dtstart`/`dtend` straight onto the row, sidestepping the picker entirely — the Google Calendar model, where clicking the grid makes an event and no rules intervene. Both still patch Google Calendar; they skip validation, not side effects.

The consequence that matters: **bringing a past booking forward is an admin edit, not a trip through the slot picker.** So a retired link's calendar rules are never read again by anything, which is exactly what makes archiving a link safe.

**Superseded — an earlier version of this file asserted the opposite** ("a past booking is never inert," therefore link rules and availability rows must resolve *forever*, therefore scope by reference existence and never by time). That was wrong. It generalized from a true fact — `reschedule_booking` checks only `status != "confirmed"`, never whether `start` is in the past — to the false conclusion that admin moves are rule-governed reschedules. They aren't; they only look that way because the admin currently has no other door. Don't reintroduce a forever-resolution requirement on that basis.

What *is* true, and independent of timing: policy and the rest of the wiring are frozen onto the booking at creation and **copy forward on reschedule** (the original booking's terms, never the link's current ones), so they resolve correctly no matter when a move happens or what state the link is in.

**BookingLink data model** — **planned, not yet implemented.** Today this entity is `EventType`, one flat table conflating everything below: no lifecycle state, no wiring/rules split, and no frozen type label. Current shipped behavior: duration is either fixed (`duration_minutes`) or a custom range (`min`/`max_duration_minutes`), name is unique, business logic validated at the router layer.

A **`BookingLink`** ("Link" in the UI) is a **factory**. Its fields split by whether they answer a **NOW** question or a **THEN** question:

| Bucket | Question it answers | Resolution |
|---|---|---|
| **Slot rules** | How should bookings be generated *right now* | **Live.** Read off the link on every slot computation. Edits take effect immediately, including for existing bookings' customer-initiated reschedules. |
| **Wiring** | What gets stamped onto a booking at the moment it's created | **Copied once, never propagates.** Edits reach only future generations. Existing rows are historical records — editable in place by an admin, never by the link. |

The split is not arbitrary. Slot rules answer questions about the present — which tutors host this, what schedules they work, how many bookings per day, what buffer sits between them. Those *must* be current: a booking rescheduled today should respect today's capacity and today's roster, not whatever existed when it was first booked. Wiring is the opposite — a promise made to one client at one moment, which later edits must not rewrite.

**Lifecycle — `status`, plus `archived_at`. Archive is the only delete.** There is no hard delete at any child count. A link row, once created, exists forever:

| State | Public URL | New bookings | Calendar rules | Editable | Existing bookings | Slug |
|---|---|---|---|---|---|---|
| `active` | serves | yes | live | yes | self-reschedulable | held |
| `paused` | resolves, but not bookable | no | **live** | **yes** | **self-reschedulable** | held |
| `archived` | 404 | no | **inert** | no | not self-reschedulable until reassigned | **released** |

*Inert* means literally nothing reads those columns anymore — the public URL 404s and customer reschedule 404s, so no code path consults the duration, buffers, caps, or availability rows. That's what makes read-only coherent: there is no scenario where you'd need to change a rule that no longer governs anything. (An earlier draft had retired links stay self-reschedulable *and* read-only — rules simultaneously live and unmanageable, which is why the rules go inert here.)

**No link status touches an existing `BookingSeries`.** A series is its own booking template: `_ensure_occurrence` copies from the series row, never from the link, so occurrences keep generating whatever the link's status is. `extend_single_series` deliberately has **no** link check. The only thing a link's status governs is whether customers can get *slots* from it — new bookings, and reschedules.

**What archiving does *not* touch.** The bookings themselves still happen, still display, still group by source. **Cancel still works** — policy is frozen on the booking, so no link is needed. An admin can still move a booking (reassign, or direct-edit once that exists). Archiving means "stop offering this," never "cancel these sessions."

**Archived is terminal** — no restore, and `archived_at` is audit metadata that nothing branches on. **Paused is the reversible one**: it exists because retiring a service and pausing one are different intents, and only pause keeps existing clients self-serving. An enum rather than an `is_active` boolean because three states don't fit a boolean, and two orthogonal flags would permit nonsense like paused-and-archived. Whatever the admin UI labels the button, the stored state is `archived` — the row remains an active participant in filtering and repair, so calling it deleted would misdescribe it.

**Why archive rather than hard-delete-and-null the children.** Not mainly to preserve history — because the FK is the **handle you repair with**. A booking under an archived link isn't customer-reschedulable (its rules are inert), so reviving one means pointing it at a live link. With the FK intact, "every booking from link 47" stays a selectable set that can be filtered and bulk-reassigned in one action. Nulled out, the same bookings are an undifferentiated pile of orphans and repair is row-by-row. `SET NULL` also makes the column nullable, so every read path grows a NULL branch — it isn't even the simpler option, just simpler in one spot.

Archiving costs nothing on the naming side — the slug is *released* for reuse (see below), so recreating the same service can take the original string back.

**Two grouping dimensions, not one.** Both are filterable, and they answer different questions:

| | Groups by | Column | Changes when |
|---|---|---|---|
| **Source** | which link governs this booking | `booking_link_id` — **NOT NULL**; stable by default, admin-reassignable | only by explicit admin action; its display label follows the link live |
| **Kind** | what it was *sold as* | `event_type` — frozen string, editable per row | only when an admin edits that row |

Source works because archiving guarantees the FK never dangles and never needs nulling — "everything under link 47" stays answerable forever, which is what makes it both a filter and the handle for bulk repair. Renaming a link relabels the source group (correct — same source, new name); it can never fragment it.

**Nothing automatic ever changes this FK** — not creation, not reschedule, not materialization of a series occurrence. But an admin can **explicitly reassign** it, and must be able to: a booking under an archived link has inert rules, so pointing it at a live link is the only way to make it customer-reschedulable again. Reassignment discards the original attribution, which is accepted — it's a deliberate, visible admin action. So the column is best read as *which link governs this booking*, not as an immutable audit record of what produced it.

Kind is the mutable classification. It drifts from the link on purpose: a booking sold in 2024 as "Intro Consult" keeps saying so after the link is renamed, and an admin can re-classify any single row without touching what produced it.

**Accepted cost:** you can't bulk-change wiring by editing the link. For standalone bookings that's per-row editing only, which is fine — standalone bookings are meant to be standalone. For series, scoped edits cover the bulk case (this occurrence / this and following / all — the Google Calendar model); that's backlog, not part of the first cut.

**Two string fields on the link, doing unrelated jobs:**

| Field | Job | Unique? | Effect of editing |
|---|---|---|---|
| `slug` | the public URL, and nothing else — semantically empty | **among `active` links only** | changes the URL; nothing downstream inherits it |
| `event_type` | the **kind** label stamped onto every booking it generates | **no, none at all** | future generations only |

**`slug` — unique among active, released on archive.** A partial unique index scoped to `status = 'active'`. Collision → **reject** with a 400, which is a good error precisely because the blocking link is live and visible: the admin can go rename or archive it. Archiving *releases* the name — the row keeps its slug value for display, it simply stops being uniqueness-enforced and stops routing. **Routing resolves only among active links**, which is what keeps duplicate values unambiguous.

The consequence, accepted deliberately: a stale `/book/consultation` URL saved by a client can later reach a *different* link that took the name. This affects only strangers arriving cold to make a **new** booking — never an existing booking, whose manage link is keyed on its own `public_id` and whose rules are reached through the FK. And the admin controls whether that reuse is semantically legitimate, since they choose to reuse the name. See "Two entry points" above.

**`event_type` — free string, deliberately not unique.** Non-uniqueness is the feature, not an oversight: it's what lets two different links both stamp "Consultation" so their bookings group together. Enforce uniqueness and the kind facet becomes a copy of the source facet.

Uniqueness would also be unenforceable in any useful sense — a link renamed away from "Consultation," with a new link later taking it, produces two links whose bookings share that kind without the constraint ever being violated. It would buy admin-UI tidiness, not a guarantee.

`event_type` rather than bare `type`: `type` is a Python builtin and too generic for a column. **Chosen from a picker, never free text** — `SELECT DISTINCT event_type FROM booking_links` plus an explicit create-new path. Free text silently fragments groups into "Consult" / "consult" / "Consultation". The picker is soft enforcement; there is deliberately no FK or vocabulary table behind it (see "Why a string, not a table" below).

**Not in the first cut:** a per-booking generated display name (built from a link-supplied template like `{first} {last} — {duration}`). Bookings and series have no name of their own yet. When it lands, keep the template pointed at that field and **never** at `event_type` — templating the grouping key would produce a distinct bucket per booking, fragmenting grouping by construction.

Schema sketch:

```
booking_links                         [the factory -- archive only, row lives forever]
  id, description
  slug                                public URL only; unique among status='active'
                                      (partial index) -- released on archive
  status                              active | paused | archived
  archived_at                         audit metadata, not a second state field

  -- CALENDAR RULES (live while active; inert once archived -- nothing reads them)
  duration_minutes | min_/max_duration_minutes
  buffer_before, buffer_after, slot_increment
  max_per_day, max_per_week, min_lead_time, booking_horizon
  recurring, expires_on, recur_weeks, booker_can_set_recur_until

  -- WIRING (copied onto each booking at creation, never propagated after)
  event_type                          the "kind" stamped on output; picker, NOT unique
  cancel_mode, cancel_notice_minutes         NULL => inherit Settings
  reschedule_mode, reschedule_notice_minutes NULL => inherit Settings

booking_link_availability             [SLOT RULE — live junction]
  booking_link_id, tutor_id, schedule_id
  unique (booking_link_id, tutor_id)

booking_link_fields                   [WIRING — live definition of what to ask]
  field_id -> form_fields.id          shared reusable question library
  booking_link_id, sort_order, is_required, visible_to

bookings          -- and booking_series: EVERY field below exists on BOTH
  booking_link_id -> booking_links.id       NOT NULL              <- SOURCE facet
                                            never dangles (archive, never hard delete);
                                            nothing automatic changes it, but an admin
                                            may reassign it to rescue a booking
  event_type                                frozen string        <- KIND facet
                                            copied verbatim at creation; admin-editable
                                            per row; series occurrences copy off the
                                            SERIES, not the link
  start_at, end_at                          duration, frozen by construction
    (booking_series uses dtstart/dtend for the same job -- naive local pattern,
     not an absolute instant; see the BookingSeries notes above)
  cancel_allowed, cancel_cutoff_min         policy, frozen at creation
  reschedule_allowed, reschedule_cutoff_min, max_reschedules
  status, reschedule_count
  first_name, last_name, email (nullable), phone (nullable)

event_field_responses                 [WIRING — frozen answers]
  booking_id, field_id (nullable)
  label_snapshot                      question text as it was asked
  value
```

**Calendar rules (live)** — duration, buffers, slot increment, caps, lead time, horizon, recurrence config, and the availability junction. Read live by the slot generator whenever the link is `active`.

**The test for which bucket a field belongs in: is it a promise to the booker, or an operational setting?** Policy, the `event_type` label, and contact info were told to a specific client at a specific moment — rewriting them retroactively changes what someone was promised, so they freeze. Calendar rules are nobody's promise; they govern the volume and shape of slots you're willing to offer *now*. Freezing them would only mean an old booking reschedules against constraints you've since abandoned.

Three refinements:
- **Caps are never a stored value.** `max_per_day`/`max_per_week` are storable *settings*, but "are we at capacity" is always a live `COUNT(*)` against real `bookings` rows. Under concurrency, gate the check-then-insert with a Postgres advisory lock keyed on `(link_id, date)`.
- **Duration is the one slot rule a reschedule overrides.** A booking's length is already frozen as `start_at`/`end_at`, and rescheduling moves the same booking rather than creating a new one, so the picker takes an explicit override — `generate_slots(link_id, duration=X, exclude_id=Z)` for reschedule vs plain `generate_slots(link_id)` for new bookings. (`exclude_id` is the already-shipped self-exclusion; see the available-slots notes above.) Every other rule — buffers, caps, roster — is read live even on reschedule.
- **Deliberate consequence:** dropping a tutor from a link means bookings already made with that tutor can no longer be *customer*-rescheduled with them, since the picker reads the current roster. Correct under "rules answer NOW questions" — the alternative freezes a stale roster onto every booking forever — and the admin surface can always place it directly anyway.

**Status is checked at the entry points, never inside the slot generator.** `get_available_slots` has **no status awareness at all** — its job is to generate slots, and it only ever runs for a link that already resolved.

Two guards, not one `!= 'active'` check, because paused and archived differ: a paused link can't take new bookings but its rules are still live, so it can still serve a reschedule. Both live in `booking_utils.py`:

| Guard | Blocks | Used by |
|---|---|---|
| `require_link_bookable` | paused **and** archived, each with its own message | `create_booking` — **authoritative**, admin-initiated creates included |
| `require_link_not_archived` | archived only | `_reschedule_booking`, `_reschedule_series` (so every route funnels through one check), `update_booking_link`, reassignment targets |

Advisory checks that only fail earlier and more legibly: the public slug lookup (404s archived; **resolves paused**, so the page can say why it isn't bookable) and `available_slots_endpoint` (404s archived only — paused must still serve reschedules).

Deliberately unguarded: **cancel** (policy is frozen on the row) and **`extend_single_series`** (a series generates from its own row, never the link).

**Wiring (copied at creation)** — `event_type`, policy, contact fields, intake answers. Written once by the link, then owned entirely by the row. An admin edits them on the booking or series directly; the link has no bulk-edit path into them by design.

**Why `event_type` is a plain string and not a table.** An `event_types` table with an FK was designed and dropped. Append-only (rename inserts a new row, the link repoints, old bookings keep the old row) forks correctly — but it needs find-or-create on insert, or renaming away and back produces two rows with the same name and *fragments* a group that should merge. With a string, `'Consultation' = 'Consultation'` is one bucket, always, structurally. Adding find-or-create to a table is building machinery to reproduce what `=` already does.

The table's genuine advantage is renaming a type everywhere in one write. That's explicitly *not* wanted: a scoped edit acts on output only and never mutates the template, so fixing a typo is two deliberate acts — edit the link, then run the scoped edit. What's given up is referential enforcement; the picker is the guard. If types ever need to become first-class objects (colors, descriptions, ordering), the migration is easy since the distinct strings *are* the rows.

**Scoped edits are one UPDATE with a different predicate**, uniformly, at both layers — `id = X` (this booking), `series_id = X AND start >= pivot` (this and following), `booking_link_id = X` (all from a link), `event_type = 'Consultaton'` (fix a typo everywhere). Output only; the link is edited on its own page.

**Policy** — **two-level: business default with an optional per-link override, resolved once at creation and frozen onto both `Booking` and `BookingSeries`.** Not versioned, never live-referenced afterward.

Cancellation policy is a *business* policy, not a per-service one — "we require 24 hours notice" is what you tell clients on your site and in intake paperwork. Per-link exceptions exist (a free intro consult is flexible, a 10-session package is strict) but they're exceptions, not the primary axis. So the four fields live on the `Settings` singleton as the business default, and `BookingLink` keeps the same four columns where **`NULL` means "inherit the business default"** (a semantic change from today's `null = auto`).

**Override granularity is per-pair, not per-field.** `(cancel_mode, cancel_notice_minutes)` and `(reschedule_mode, reschedule_notice_minutes)` each inherit or override as a unit, keyed off whether the *mode* is `NULL`. That prevents incoherent mixes ("inherit `auto` mode but override the notice window" is meaningless — `auto` has no window) while keeping cancellation and rescheduling independent, so "default cancellation, but this package can't be rescheduled at all" still works. Enforced with `CheckConstraint("cancel_mode IS NOT NULL OR cancel_notice_minutes IS NULL")` per pair. The existing notice-required constraints already tolerate a `NULL` mode — a CHECK passes when it evaluates to `NULL`, only `FALSE` fails.

Resolution happens exactly once, at creation, in a single helper (`resolve_policy(link, settings)`), because the freeze *is* the resolution point. After that `get_cancel_action`/`get_reschedule_action` read the booking's own frozen columns — no live two-level lookup in any hot path, no chance of fallback logic drifting between call sites.

**No `Policy` entity.** A standalone shared table was designed and dropped twice: the only thing it bought over columns was "edit once, applies to many," and the `Settings` default delivers exactly that without a new table, a new route, or copy-vs-share semantics to reason about. See the `tms-roadmap` skill's Decisions section. The series row isn't decorative: `_ensure_occurrence` copies fields *off the series* when Procrastinate materializes each new occurrence of an infinite series, so the series row governs everything not yet generated. Changing a client's terms means editing the series (optionally scoped to this-and-following), never editing the link.

**Client info** — two distinct things, not to be conflated:
- **Fixed contact fields** (`first_name`, `last_name`, `email`, `phone`) — plain columns on **both `Booking` and `BookingSeries`** (as they already are today), email/phone nullable. For logged-in users, copy their profile name/contact *at booking time* rather than referencing `user_id` live — consistent with the freeze-on-creation pattern elsewhere, since an old booking's client-facing info shouldn't drift when someone edits their profile later.
- **Custom intake questions** — a reusable label:answer system. `form_fields` is the shared question library (name, input type, options); `booking_link_fields` is the live join saying which questions a link asks, in what order, required or not; `event_field_responses` stores one answer row per question per booking, with a `label_snapshot` of the question text *as it was asked*, so later renaming a question never rewrites the meaning of an old answer.

**Deletion rules under this model:**
- **Links** — **archive is the only delete, at any child count.** `status='archived'`: URL 404s, rules go inert, row goes read-only, and it drops out of the admin list and every create-flow. **Permanent in behavior — no restore**; a booking stuck on an archived link is rescued by reassigning *the booking*, never by resurrecting the link. This is what keeps `booking_link_id` NOT NULL and non-dangling forever, which is both the source facet and the bulk-repair handle. (It also removes guards rather than adding them: no zero-children check, no `ON DELETE` behavior to choose, no nullable FK branches.)
- **Bookings** — never hard-deleted in normal operation. Cancel via `status='cancelled'` + `cancelled_at`/`cancelled_by`, needed for cap counting and no-show history. (The admin `.../permanent` endpoints remain as a deliberate escape hatch.)
- **Form fields** — soft delete via `archived_at`. Removing one from a link's form just drops it from `booking_link_fields`; existing `event_field_responses` keep their `label_snapshot` regardless.
- **Slugs** — unique among `active` links only (partial index), **released on archive**. The archived row keeps its slug value for display; it just stops being uniqueness-enforced and stops routing. A new link may take the freed string, so a stale bookmarked URL can reach the successor rather than 404ing — deliberate, since that's the desirable outcome when you retire and relaunch the same offering, and it only ever affects strangers arriving cold for a *new* booking. `event_type` has no uniqueness constraint at any scope.

**Precedents** — **Google Calendar Appointment Schedules** is the closest match and, under this model, no longer a negative example: its schedule is a generator whose settings are live, and what it produces are ordinary calendar events you edit individually. **Google Calendar recurring-event edits** (this event / this and following / all events) is the model for scoped wiring edits on a series, and its click-the-grid event creation is the model for the planned admin direct-add. **Stripe Product/Price** is instructive as the *contrasting* shape — Price is immutable and invoices reference the specific one active at the time, which is what you build when the parent row carries identity that its output must inherit. Here the parent carries *provenance*, not inheritable identity, so the output copies its own kind label instead of pointing at a frozen version.

**Terminology used above** — *live FK*: the child reads the current value through a shared row, no freezing (here: `booking_link_id` for calendar rules, and for the source facet's display label). *Governing FK*: a NOT NULL FK that always resolves (because the parent is archived, never hard-deleted) and that nothing automatic ever repoints — but which an admin may deliberately reassign. Safe to group on because it never dangles, and usable as a repair handle because it's reassignable. Distinct from true provenance, which would be immutable; this trades that for the ability to rescue a booking whose link has gone inert. *Inert*: stored but no longer read by any code path (a deleted link's slot rules) — the reason freezing them from edits costs nothing. *Frozen copy (denormalized)*: value copied onto the booking at creation and never touched by the parent again, though an admin can edit it on the row itself; no history of prior values kept. *Versioned*: normalized, edits insert new rows instead of updating, old rows persist and stay inspectable — **not used in this model**; it was in an earlier draft (archive-and-relaunch forking) and was removed along with the identity-on-the-link premise. *Override*: nullable field on the child that inherits the parent's live value unless explicitly set — used for policy against `Settings`, and nowhere else.

**EventType recurrence modes** — three mutually exclusive modes on `recurring=True` event types:
- **Fixed expiry** (`expires_on` set, `recur_weeks` null): all series end on that date. `booker_can_set_recur_until` must be False — backend enforces. BookingSeries gets `recur_until = expires_on` (all rows generated at creation).
- **Relative duration** (`recur_weeks` set, `expires_on` null): series runs N weeks from booking start. `booker_can_set_recur_until=False` → `recur_until = start + N weeks`, locked. `booker_can_set_recur_until=True` → booker sees date picker pre-filled to start + N weeks, can override.
- **Indefinite** (both null): `recur_until = null` on BookingSeries. `booker_can_set_recur_until=False` → infinite, no picker. `booker_can_set_recur_until=True` → optional picker, infinite if left blank.

Backend validation: `expires_on` and `recur_weeks` cannot both be set. `expires_on` + `booker_can_set_recur_until=True` is rejected. Bookings cannot be created after `expires_on`.

**Booking contact rules**: at least one email (student or parent) AND at least one phone required. Enforced at both router and DB (`CheckConstraint`) level.

## Frontend Patterns

**UI stack**: Mantine v7 for form controls (Select, NumberInput, Modal, etc.), Tailwind for layout/spacing. Native `<input>` elements used inside the inline edit form in `LessonRow.tsx`; Mantine components used in `LessonAddModal.tsx` and `BulkAddCard.tsx`.

**Layout**: Dark sidebar (`bg-gray-900`, collapsible via `sidebarCollapsed` state + `transition-all`) and header share one visual shell (rounded card floating on the dark shell, no hard right angle between them). React Router routes: `/` → LessonsTable, `/tutors` → Tutors, `/bookings` and `/my-bookings` → nested routes through `BookingsLayout` (see below).

**Bookings nested routing**: `/bookings` and `/my-bookings` (customer mode, no `requests` child route) are both parent routes rendering `BookingsLayout`, which stays mounted across tab switches and owns everything shared — roster fetch (once), toast, tab bar. Its `<Outlet context={... satisfies BookingsOutletContext}>` is the slot React Router fills with whichever child route matched (`ScheduleTab`/`RecurringTab`/`RequestsTab`); each reads that context via `useOutletContext<BookingsOutletContext>()` instead of fetching the roster itself. A tab switch is real navigation (URL changes, browser back/forward works), not local `useState` toggling — switching tabs unmounts/remounts the tab component, so each tab's own filter/pagination state resets on revisit by design (not persisted).

**LessonsTable view model**: Three views — `'All' | 'Month' | 'Week'`. `useLessons` hook exposes `ungrouped` (filtered+sorted), `byMonth`/`months`, `byWeek`/`weeks`. `lessonsToDisplay` is derived in the component based on view + `periodIndex`. Switching views resets selection and `periodIndex`.

**useLessons hook**: Owns all lesson state, filter state, selection state, and handlers. Exports derived groupings (`byMonth`, `months`, `byWeek`, `weeks`, `selectionSummary`). `ungrouped` = filtered + sorted lessons.

**Table structure**: Single `<table>` always rendered. `tableRows` is pre-computed (before JSX return) via `flatMap` — inserts a day-header `<tr>` before the first row of each date group with alternating amber/indigo color tabs. `dayIndex` prop drives the left color tab on each `LessonRow`.

**Tutor bubble**: renders a colored circle with initials, color auto-assigned per tutor via `tutorBubbleClass`/`tutorInitials` in `utils.ts` — deterministic on `tutor.id` (not name), so no specific person is hardcoded. Same tutor always gets the same color across `LessonRow`, `BookingRow`, `RequestsTab`, and `Tutors`.

**Selection mode**: Toggled via checkbox in table toolbar. Cancel (✕) and Delete (trash icon) appear in toolbar when active. Selection summary KPI row slides in inside `<thead>` between toolbar and column headers.

**Error handling pattern**: Field-level errors use `LessonEditErrors` (per-field optional strings) driving red borders + a shared error line. Backend/network errors use a separate `editSubmitError` / `deleteError` string state. Modal uses a flat `string[]` errors array shown as a list.

**Inline confirmation pattern**: destructive actions (delete, discard unsaved changes) swap the button row in-place rather than opening a second modal.

**Success toast**: fixed bottom-center div with green checkmark, auto-dismisses after 5s. Managed in the parent page component (`Availability.tsx`) via `showToast(msg)` helper + `useRef` timer. Re-triggering resets the timer.

**Availability page pattern** (`Availability.tsx` + `ScheduleForm.tsx`):
- Cards list schedules; edit replaces the card inline using `editingSchedule?.id === s.id` in the map.
- `ScheduleForm` state is initialized directly from `editingSchedule` in `useState` (no `useEffect`) — works because the component is freshly mounted per edit via `key={s.id}`.
- `isDirty` compares current state against a plain `initial` object (derived once from `editingSchedule`); uses `JSON.stringify` for the days array.
- `TIME_OPTIONS`: 96 × 15-min slots (00:00–23:45) + `'23:59'` appended. Index arithmetic used for add/cascade logic.
- Multi-period cascade: when `to` changes, each subsequent period's `from` is pushed 15 min past the previous `to`; if that breaches the period's own `to`, `to` is also pushed forward; `splice(i)` removes unresolvable periods.
- `canAddPeriod`: disabled only when first period starts at `TIME_OPTIONS[0]` AND last period ends at `TIME_OPTIONS[last]`. If only the end is full, new period prepends before the first.

**Mantine focus override**: `index.css` overrides `.m_8fb7ebe7:focus` to use indigo-400 border color instead of default purple.

**EventTypes page pattern** (`EventTypes.tsx` + `EventTypePage.tsx`) — diverged from Availability's inline-swap pattern into a separate routed page instead:
- `EventTypes.tsx` is list-only now — cards with a link/button that `navigate()`s to `/event-types/:id` (or `/event-types/new`), no inline form swap.
- `EventTypePage.tsx` is a full routed page, tab-driven via `?tab=` search param (`details`/`duration`/`recurrence`/`hosts`/`cancellation`/`limits`/`booking`). Each tab in the nav shows a small red dot (`tabHasError`) if that section currently has a validation error, computed from the same `errors` object across all tabs at once.
- Tutor rows: array of `{ tutorId, scheduleId }`. When tutor changes, schedule auto-resets to that tutor's default.
- Duration: Switch toggles between fixed `durationMinutes` and custom `minDurationMinutes` / `maxDurationMinutes`.
- Validation: name required; duration > 0 (or min/max valid and min < max); at least one tutor row with both fields set. Same `validate(form): FormErrors` shape as before, just now surfaced per-tab instead of in one form.
- No `isDirty`/discard-confirm pattern here (unlike ScheduleForm) — navigating away from a routed page doesn't have the same in-place "close without saving" concern as swapping a card back.
- `extractError` is a module-level pure function (not inside component).

## Future Integrations

- **Discord bot** — monitor voice channels to auto-calculate lesson duration. Bot detects tutor + student join/leave a designated channel, calculates actual hours, calls TMS API to populate `hrs` on the lesson record. Requires: `discord_user_id` on Tutor, Booking→Lesson FK link, TMS endpoint to receive duration from bot. Must be a bot (not webhook) — webhooks can't monitor voice state.

See the `tms-roadmap` skill for the backlog (known TODOs, planned improvements, and future features under evaluation) — moved out of this always-loaded file since it's reference material, not needed every session.

**BookingPage** (`BookingPage.tsx`):
- Public-facing route `/book/:eventTypeId`, outside the admin layout — no sidebar or header.
- Two-panel: dark left panel (event info, tutor selector if multiple, selected slot summary), light right panel (calendar or contact form).
- Three steps: `pick` → slot browser, `contact` → student/parent form, `done` → confirmation + calendar links.
- Slot browser: month view (7-col calendar grid + right-side slot panel) or week view (7-col day grid). `slotsByDate` filters past slots via `new Date(slot.start) < now` and groups by local date string.
- Timezone: auto-detected via `Intl.DateTimeFormat().resolvedOptions().timeZone`. Displayed in left panel. All display uses `Intl.DateTimeFormat` with the detected timezone.
- Calendar links on done step: Google Calendar (template URL), Outlook.com, Office 365 (both via `buildOutlookUrl`), Other (ICS blob generated client-side via `buildIcsBlobUrl` + `URL.createObjectURL`). Outlook icon loaded from `assets/outlook-icon.svg`.
- Module-level helpers: `toCalDate`, `buildGoogleCalUrl`, `buildOutlookUrl`, `buildIcsBlobUrl`, `localTimeOf`, `localLongDateOf`, `localDateOf`, `addDays`, `startOfWeek`, `startOfMonth`, `endOfMonth`, `toLocalDateStr`.
- `loadSlots` fetches `/bookings/available-slots` with `tutor_ids`, `event_type_id`, `time_min`, `time_max`. Re-runs when `eventType`, `currentDate`, `view`, or `selectedTutorId` changes.
- Contact form validation: student first/last required; at least one email AND at least one phone (student or parent).
- `submitting` state disables/shows spinner on "Confirm booking" button via Mantine `Button loading` prop.
- **Reschedule flow** (`rescheduleFromId` in `location.state`): slot picker works identically; on slot select shows confirm screen instead of contact step; submit calls `POST /bookings/{id}/reschedule`. Contact pre-filled from state, locked.
- **Change series schedule flow** (`rescheduleSeriesId` in `location.state`): same slot picker; confirm screen shows "Confirm series change" copy; submit calls `PUT /bookings/booking-series/{id}` with `buildReschedulePayload()`. No calendar links on done step. Tutor pre-selected from `location.state.tutorId` but changeable.
- State keys passed via `navigate()` are camelCase: `rescheduleFromId`, `rescheduleSeriesId`, `tutorId`, `originalStart`, `originalEnd`, `studentFirst`, `studentLast`, `studentEmail`, `studentPhone`, `parentEmail`, `parentPhone`.
