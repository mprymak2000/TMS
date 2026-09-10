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
  models.py        # SQLAlchemy ORM models (Student, Tutor, Lesson, Schedule, ScheduleDay, BookingLink, BookingType, BookingLinkAvailability, BookingSeries, Booking)
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
    booking_links.py          # CRUD for bookable links; slug uniqueness among non-archived; archive/pause/resume; /impact
    booking_types.py          # CRUD for the kind label (label + color) — created inline from the link editor, no page of its own
    booking_link_availability.py  # Links tutor+schedule to a booking link (junction table); PUT only updates schedule_id
    available_slots.py        # GET /available-slots — separate router, own prefix (see Key Business Logic below)
    bookings.py               # Full booking CRUD + Google Calendar integration
    settings.py                # Business-wide singleton settings (business_timezone); get-or-create pattern
    cancellation_policies.py  # Unregistered — policy fields moved onto BookingLink directly, kept for reference
frontend/
  src/
    main.tsx           # Entry point, wraps App in BrowserRouter
    App.tsx            # Root component, sidebar + header layout, React Router routes; /book/:slug and /my-bookings outside admin layout
    LessonsTable.tsx   # Lessons page: view toggle (All/Month/Week), toolbar, table with inline edit/delete/selection
    LessonRow.tsx      # Extracted row component: main row + expanded detail/edit/delete confirm
    LessonAddModal.tsx # Add lesson modal with dirty-check discard confirmation
    BulkAddCard.tsx    # Bulk add form card (per-row validation, atomic submit)
    useLessons.ts      # Custom hook: all lesson state, filters, derived groupings, handlers
    Availability.tsx   # Availability page: schedule cards + inline create/edit/delete; success toast
    ScheduleForm.tsx   # Schedule create/edit form (extracted component); multi-period days, timezone, dirty check
    Links.tsx          # Links admin page: cards list only, navigates to LinkPage for create/edit (diverged from Availability's inline pattern); slug headline + copy button, pause/archive confirms
    LinkPage.tsx       # Routed page (/links/:id, ?tab=) — multi-tab link editor (details/duration/recurrence/hosts/cancellation/limits/booking), per-tab error indicators
    BookingsLayout.tsx # Layout route mounted at /bookings and /my-bookings — tab bar (NavLink), roster fetch (tutors/booking links, once), toast; renders <Outlet context={BookingsOutletContext}>
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
    BookingPage.tsx    # Public booking page at /book/:slug — no sidebar, 3 steps: pick/contact/done
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
- **Tutor** — referenced by `Lesson`, `Booking`, `BookingSeries` (all app-level RESTRICT, 409 if any exist — hard delete only succeeds with zero references of any kind, past or future; `is_active=False` is the normal offboarding path otherwise), `Schedule` (DB-level `CASCADE` — safe only because the three RESTRICTs above already guarantee zero bookings exist by the time a tutor delete reaches Postgres), `BookingLinkAvailability` (DB-level `CASCADE` — junction row, nothing worth preserving).
- **Schedule** — referenced by `BookingLinkAvailability` (DB FK is `CASCADE`; `delete_schedule` 409s first while any **non-archived** link references it, so it behaves as a RESTRICT from the outside). Archived links are excluded from that check deliberately: their calendar rules are inert so the rows guard nothing, and since archive is terminal, counting them would make any schedule a since-archived link ever used permanently undeletable. That exclusion is the one case where the `CASCADE` actually fires — deleting a schedule held only by archived links drops those inert rows, which is also why the FK can't be RESTRICT; the *default* schedule additionally can't be deleted at all until another schedule is made default first.
- **BookingLink** — **archive only, no hard delete at any child count.** `DELETE /booking_links/{id}` sets `status='archived'` (409s if already archived); there is no hard-delete route and no restore. Archiving makes the URL 404, calendar rules go inert, the row goes read-only, and the slug is released for reuse — but the row never leaves, so `Booking.booking_link_id` stays NOT NULL and non-dangling forever. That permanence is what lets bookings be grouped by *source* and, more importantly, bulk-reassigned to a live link to make them reschedulable again; the booking's own `booking_type_id` separately carries its *kind*. `paused` is the reversible middle state. Because nothing is ever hard-deleted, the `BookingLinkAvailability` `CASCADE` on this FK is dead code — no delete ever reaches Postgres.
- **BookingType** — plain hard delete, unguarded at any usage count. It carries no rules and nothing branches on it, so referring rows just lose their label: every `booking_type_id` FK is `ON DELETE SET NULL`. The frontend warns first using `GET /booking_types/{id}/usage`, which counts links, bookings, and series separately (a booking losing a label off a historical record is a different loss from a link needing a new type picked).
- **Student** — referenced by `Lesson` (app-level RESTRICT, 409; cascade delete intentionally removed to protect financial records). `Booking.student_id`/`BookingSeries.student_id` are rarely populated today (see the `tms-roadmap` skill's `Contact`/`Student` identity-split note) and have no delete guard.

**Tutor `is_active`**: Both Student and Tutor have `is_active`. Retiring a student/tutor means setting `is_active=False`, not deleting them. For Tutor specifically, this is enforced, not just a display flag: `create_booking`/`_reschedule_booking`/`_reschedule_series` (`routers/bookings.py`) reject an inactive `tutor_id`, `get_available_slots` excludes inactive tutors, and `extend_single_series` (`tasks.py`) stops materializing new occurrences for a series whose tutor has gone inactive. Already-confirmed/already-materialized bookings are untouched either way.

**Booking system** (`routers/bookings.py`):
- `POST /bookings/` — creates Google Calendar event first, then DB record atomically. Recurring event types: creates one RRULE Google Calendar event + one `BookingSeries` row + one `Booking` row per occurrence (generated inline via a loop). Standalone: one event + one `Booking` row. If DB fails, compensating delete on Calendar. If compensating delete also fails, logs warning.
- `PUT /bookings/{id}` — contact-info-only update (student/parent name, email, phone, is_no_show). No Google Calendar calls. Plain DB write. `is_no_show=True` is how no-show is recorded — no separate PATCH endpoint.
- `POST /bookings/{booking_id}/reschedule` — atomic saga: for series bookings, patches specific RRULE instance via `events().instances()`; for standalone, creates new event + deletes old. Inserts new `Booking` row, soft-deletes old (`status='rescheduled'`, `rescheduled_to=new_id`).
- `DELETE /bookings/{id}` — soft-delete one occurrence (`status='cancelled'`). For series: patches specific RRULE instance cancelled via `events().instances()`. For standalone: deletes calendar event.
- `DELETE /bookings/{id}/permanent` — hard delete with optional `cascade` param to also delete predecessor (booking that `rescheduled_to` this one).
- `DELETE /booking-series/{id}` — truncates the RRULE to today, bulk-deletes future occurrence rows, soft-deletes the series (`status='cancelled'`). Truncating sets `until=today` **and clears `count`** — a truncated rule ends on a date whatever it ended on before, and the two can't coexist.
- `POST /booking-series/{id}/reschedule` — the series saga. Truncates the old RRULE, creates a new RRULE event on the (possibly new) tutor's calendar, drops future occurrence rows, and inserts a **new** `BookingSeries` row rather than mutating the old one (closed with `status='rescheduled'`, `rescheduled_to=new_id`). A POST, not a PUT, because it creates a row with a new `public_id`.
  - **Everything after the pivot dies, individually-rescheduled occurrences included.** The pivot is always `now` — never a chosen occurrence — so every `Booking` with `start >= now` is deleted, even one a client had personally moved. This matches Google: *"Changing all following instances resets any exceptions happening after the target instance."* Past rows are untouched; a past row whose future replacement is deleted keeps `status='rescheduled'` with `rescheduled_to` SET NULL, which is accurate — the move happened, its destination was later removed.
  - Google's API doesn't clean up orphaned exception *events* when an RRULE is truncated, so `_reschedule_series` deletes them explicitly. Doing that work by hand is what makes our end state match theirs.
  - **How the new series' end is carried over**: `until` passes through unchanged; `count` becomes `original − consumed`. See the counting rules under "Recurrence" below.
- `PUT /booking-series/{id}` — plain-column update of the wiring fields (contact info, `booking_type_id`, `booking_link_id`). No calendar calls. Deliberately excludes `dtstart`/`dtend`: moving a series creates a new row, so it can't be a PUT.
- `GET /bookings/available-slots` — three-mode branched algorithm in `routers/available_slots.py` (see module docstring for full complexity analysis and edge cases). Modes: `standalone` (non-recurring), `finite` (`expires_on` or `count` set), `infinite` (neither). Infinite mode runs `thin_schedule_dateless` first — subtracts existing infinite series from the schedule in (weekday, time) space O(S+R), resolves survivors to concrete dates once. All modes then run a shrinking-batch week-over-week two-pointer sweep O(N×(C+R+B)) vs naive O(C×N×(R+B)). Edge cases: pre-time_min busy spill, midnight-crossing sessions, Sun→Mon dateless seam, UTC time_min shifting to prior local day. All times in business canonical timezone (Settings.business_timezone).
- **Self-exclusion on reschedule** — a booking must not count as busy against itself, or it can't be nudged into a slot overlapping its own current time (4:00–5:30 → 4:15–5:45). `get_available_slots` takes `exclude_booking_id`/`exclude_series_id`; the endpoint accepts them as `exclude_ref`/`exclude_series_ref` (`public_id`s, resolved to internal PKs, no-op'd if unresolvable since a virtual occurrence has no row and isn't in `busy_dict` anyway). `BookingPage.tsx` passes them from `location.state` on both reschedule paths. Both former gaps here are now closed: the booking's *exact* current slot is withheld server-side so the picker never offers a no-op (the 400 guards in `_reschedule_booking`/`_reschedule_series` remain as a backstop), and rescheduling a single occurrence of an *infinite* series works — that path runs in `standalone` mode, since moving one instance isn't a claim on the whole weekly band.
- Recurring bookings: `recurring=True` adds an RRULE built by `build_rrule()` (no BYDAY — day inferred from DTSTART by Google, so a twice-a-week client is two series today). It emits `COUNT` or `UNTIL`, never both. `UNTIL` is a **UTC date-time**, as RFC5545 requires when DTSTART is one: a date resolves to local end-of-day first, so Dec 9 in ET is `20261210T045959Z` — the naive `20261209T235959Z` is Dec 9 18:59 local and would drop that evening's session.
- Indefinite series materialization: for finite series (`until` or `count` set), all occurrence rows are generated at creation — including on series reschedule, where the new series inherits `count = original − consumed`. For indefinite series (both null), only a sparse rolling window is kept materialized — `tasks.py`'s `extend_all_series` (Procrastinate `@app.periodic`, daily cron `0 2 * * *`) finds active indefinite series and fans out one `extend_single_series` job per series, which calls `_ensure_occurrence` (`booking_utils.py`) to materialize the next occurrence as needed. No large pre-generated buffer, no `generated_through` column (doesn't exist — this state is implicit).
- **Recurrence — `UNTIL` and `COUNT` are separate facts, never two spellings of one.** `UNTIL` is a fixed date; `COUNT` is a quota of occurrences. RFC5545 forbids both in one rule and Google enforces the same, so both `booking_links` and `booking_series` carry CHECK constraints making them mutually exclusive; both null means indefinite. We store them decomposed into columns rather than as an RRULE string (which is what Google does) because availability computation has to reason in `(weekday, time)` space — nothing here ever parses an RRULE back, the string is write-only, built by `build_rrule()` purely to hand to Google.
  - **What a `count` counts: occurrences of the rule, not sessions attended.** A cancelled occurrence still fills its slot — cancelled is cancelled, and the client doesn't get it back. Whether a cancellation should be forgiven is a billing question, not a recurrence one, so status stays out of the arithmetic. Same as Google.
  - **The one status that *is* excluded is `'rescheduled'`, and not as an exemption.** When a single occurrence is individually rescheduled, the original row stays in place at its old time (soft-deleted) *and* a replacement row is inserted. Both belong to the series. Counting the original as consumed alongside its replacement would spend one slot twice, so `consumed` skips it — the slot travelled to the replacement, it wasn't forgiven. Worked example: COUNT=10, five delivered, #6 moved into the future, then the whole series is rescheduled → `consumed = 5`, so the new series gets `count = 5` (moved-#6 plus #7–10), which is exactly what's still owed.
  - **Asymmetry between the two, accepted.** An occurrence moved *past* `until` is lost when the series is rescheduled — a date has nowhere to represent it. A `count` self-heals, because a quota can. Not worth equalising: the alternatives are silently extending the promised end date, or keeping an orphan row whose calendar event was already deleted.
  - **Where the rule ends is a different question from whether the series is still running**, answered by different code. `series_last_date()` resolves the rule's own end (arithmetic off `dtstart` for `count`) and drives *generation bounds only*. "Still running" is derived from the **bookings** — `active_series_filter` / `is_series_active` — because an occurrence rescheduled past the rule's end should keep its series alive rather than stranding it. This is also why no end date is ever stored: a denormalized one goes stale the moment an occurrence moves.
- Opaque ids: `Booking`/`BookingSeries` have a `public_id` (UUID) alongside their internal integer PK; API responses (`id`, `series_id`, `rescheduled_to`) expose only `public_id` — the integer PK is never serialized. For a series-bound `Booking`, `public_id` is set explicitly at creation/materialization time (not computed at read time) to the composite form `"{series.public_id}:{unix_timestamp}"`, mirroring Google Calendar's recurring-instance-id scheme (`{baseEventId}_{timestamp}`) — write-once, not recomputed on every serialize. Standalone bookings just get a plain generated UUID.
- `resolve_ref(ref, db, settings)` (`booking_utils.py`): resolves either form of the ref above to a `Booking` row — tries a direct `public_id ==` lookup first (covers standalone bookings and any already-materialized series occurrence), and only on a miss falls back to parsing the composite form and calling `_ensure_occurrence` to materialize it. **Materialization only happens on routes representing genuine write intent** (`reschedule`, cancel/`DELETE`, `DELETE .../permanent`, contact-info `PUT`) — `GET /{booking_id}` deliberately does a plain non-materializing lookup and 404s on a ref that isn't a real row yet. Browsing virtual (not-yet-materialized) occurrences — e.g. a paginated customer booking list — must construct them in memory without persisting; only an actual client action should turn a virtual occurrence into a real row.
- Route ordering: `/available-slots`, `/booking-series/{id}` must appear before `/{booking_id}` in bookings.py to avoid FastAPI matching literals as ints.
- **Pagination (`GET /bookings/`, `GET /booking-series`, `GET /booking-series/{id}/occurrences`) — cursor-based, current behavior:**
  - `cursor` (opaque, base64) is the only pagination input besides `page_size`. `encode_cursor`/`decode_cursor` (`booking_utils.py`) pack `start` timestamp + `public_id` tiebreaker + a fingerprint of the filters/time-window the cursor was minted under; a cursor replayed against a different filter combination is rejected rather than silently returning a mismatched page.
  - The materialized query seeks via the cursor's `(start, public_id)` tuple instead of re-fetching the whole window every call; `_virtual_occurrences`/`scoped_virtual_occurrences` start walking from the cursor instead of always from `series.dtstart`. Cost is `O(page_size)` regardless of depth.
  - Response carries `next_cursor` (null once exhausted), no `total`/`has_more`.
  - `GET /bookings/pages` is the separate, still-available `total`/page-number-returning endpoint (bounded `time_max`, `BookingPagedListResponse`) — kept for API completeness, not called by the frontend, which uses cursor-based Next/Prev everywhere.
  - Design: `.claude/plans/done-cursor-pagination-and-endpoint-split.md` (implemented).
- **Filtering / facets** (`booking_utils.py`): `GET /bookings/` and `GET /booking-series` both take `tutor_ids`/`booking_link_ids`/`booking_type_ids`/`student` filters and return `facets` alongside `items` in the same response — the self-excluding, Google-Flights-style filter-checklist options, not just the filtered results.
  - `apply_scope_filters(query, model, tutor_ids, booking_link_ids, booking_type_ids, student_pairs, email=None, exclude=None)` — the one shared filter-applier for both `Booking` and `BookingSeries` (same column names on both). `exclude` skips one dimension's own clause — this is what makes self-exclusion possible. Students match on `(student_first, student_last)` exact pairs via `tuple_(...).in_(...)`, not independent `.in_()` calls on each name — matching first/last separately would cross-match two different guests who happen to share only one name. Pairs travel over the wire as a pipe-delimited `"First|Last"` string (same composite-string convention as `public_id`), decoded server-side.
  - `compute_timeline_facets(...)` / `compute_series_facets(...)` — call `apply_scope_filters` three times per request, once per facet dimension, each time excluding that one dimension so its own filter never narrows its own options. For the timeline version, series facets are existence-checked via `_virtual_occurrences(series, time_min, time_max, count=1, settings)` — reuses the real occurrence-window walk rather than reimplementing date math, `count=1` since facets only need "does this series contribute anything here," not the full list. Scoped to indefinite series (`indefinite_series_filter()`), since a bounded series is fully materialized and already contributed through the `Booking` queries.
  - **Self-exclusion only protects a facet from its own filter** — it does nothing about *other* active filters (or the time window) legitimately narrowing a selected value out of the response. Handled backend-side now: `compute_timeline_facets`/`compute_series_facets` union the currently-selected values back into each facet's own response before returning, so a selection never silently disappears from its own facet — no frontend fallback needed anymore.
- Datetimes stored as UTC (`DateTime(timezone=True)`). `BookingCreate`/`BookingReschedule` schemas convert client-local time to UTC via `model_validator(mode="after")` using `zoneinfo`. `Booking.timezone` is the booker's timezone captured at booking time — consumed once as a write-time UTC-conversion input, then not read again by any current frontend display path (`utils.ts`'s `formatDate`/`formatTime` and `BookingPage.tsx` both format using the *current* viewer's live-detected timezone, ignoring the stored value, which is strictly more correct for a live browser session). **The column is slated to be dropped** — it's a request-time conversion input, not state. Emails will render in business time with the zone stated explicitly ("4:00 PM ET"), which is both unambiguous and immune to the booker having moved since; guessing their current zone from a value captured months ago is worse than naming the zone outright. It should live only on `BookingCreate`/`BookingReschedule` and be excluded before reaching the ORM, the same way `fee_override` already is. See the roadmap.
- **SQLite drops tzinfo on read** (tests only — Postgres preserves it): a `DateTime(timezone=True)` column comes back naive from SQLite even though it's tz-aware in prod. Two failure modes this causes if unguarded: comparing a naive value against an aware one raises `TypeError`, and calling `.timestamp()` on a naive `datetime` silently uses the *local system clock's* timezone instead of UTC. Standard guard used throughout `booking_utils.py`: `dt if dt.tzinfo else dt.replace(tzinfo=UTC)` before any comparison or `.timestamp()` call — assumes naive means UTC, which matches how everything here is actually stored.
- Manage links (`/manage-occurrence/:ref`, `/manage-series/:ref`) are keyed directly off `public_id` — no separate token concept. See the `public_id`/`resolve_ref` entries above.
- `google_event_id` is non-nullable on `Booking` — a booking without a calendar event is a broken record.

**Schedule system**:
- `Schedule` — belongs to a tutor (`tutor_id`), has a `name`, `is_default` flag, `timezone`, and a list of `ScheduleDay` rows. Name is unique per tutor (`UniqueConstraint("tutor_id", "name")`).
- `ScheduleDay` — one row per time period per day (`day_of_week` 0–6, `start_time`/`end_time`). Multiple rows per day allowed to support non-contiguous periods (e.g. 9–12, 2–5).
- `BookingLinkAvailability` — junction table linking `booking_link_id` + `tutor_id` + `schedule_id`. Unique on `(booking_link_id, tutor_id)`. PUT on this endpoint only allows changing the `schedule_id` (tutor reassignment not supported). This is what available-slots queries to know which schedule applies per tutor per link.
- `is_default` flip: creating/updating a schedule with `is_default=True` automatically sets all other schedules for that tutor to `is_default=False`. Updating an existing default to `is_default=False` is blocked (must set another as default first). Cannot delete the default schedule, and `delete_schedule` also 409s if the schedule is still linked to a `BookingLink` via `BookingLinkAvailability` — must reassign those links to a different schedule first.
- Deleting a tutor cascades to `BookingLinkAvailability` rows (`ondelete="CASCADE"` on both FKs). Deleting a tutor also cascades to their `Schedule` rows (and each schedule's `ScheduleDay` rows) — `Schedule.tutor_id`/`ScheduleDay.schedule_id` are both `ondelete="CASCADE"`, safe only because `DELETE /tutors/{id}` already 409s while any `Booking`/`BookingSeries` references the tutor, so by the time the cascade reaches Postgres zero bookings exist. The `CASCADE` on `BookingLinkAvailability.booking_link_id` is dead code — a link is archived, never hard-deleted, so no delete ever reaches Postgres on that side.
- `available-slots`: all schedule and series times resolve to the business canonical timezone (`Settings.business_timezone`). `BookingSeries.timezone` was **already dropped** as redundant with it. `Schedule.timezone` is **not** redundant and stays — a tutor in another zone enters their hours in their own local time, and that column is the only thing that says which zone to convert from (its model TODO claiming otherwise reflects today's single-tutor data, not the design). On timezone change: shift all stored times by old→new offset using current DST state, then update Settings.
- **Known limitation, not being addressed now**: one global `Settings.business_timezone` assumes a single-location business. A business spanning multiple physical locations in different timezones (a franchise model) isn't representable — that would need timezone scoped per-tutor (or a future `Location` entity) rather than one app-wide value, and every booking would need its zone denormalized at creation from whichever tutor produced it, same freeze-at-creation pattern as the planned per-booking policy denormalization ("BookingLink data model" below). Deliberately deferred — no multi-location feature is currently planned. See `tms-roadmap` skill.

**Two entry points, two rule regimes.** Every change to a booking arrives through one of two doors, governed completely differently. Conflating them is where the superseded invariant below went wrong.

- **The booking page — customer-facing, fully rule-governed.** Availability, buffers, caps, lead time, booking horizon, cancel/reschedule notice floors. This is the only path that reads a `BookingLink`'s calendar rules.
- **The admin surface — unrestricted.** An admin can put a booking anywhere: overlapping another, outside any schedule, past the caps, in the past. If a practitioner wants two sessions at once, that's their call; the program doesn't second-guess it.

**Today the admin has no native surface and borrows the booking page**, so they inherit its rules by accident rather than by design. Planned (see the `tms-roadmap` skill): direct-add and edit-in-place writing `dtstart`/`dtend` straight onto the row, sidestepping the picker entirely — the Google Calendar model, where clicking the grid makes an event and no rules intervene. Both still patch Google Calendar; they skip validation, not side effects.

The consequence that matters: **bringing a past booking forward is an admin edit, not a trip through the slot picker.** So a retired link's calendar rules are never read again by anything, which is exactly what makes archiving a link safe.

**Superseded — an earlier version of this file asserted the opposite** ("a past booking is never inert," therefore link rules and availability rows must resolve *forever*, therefore scope by reference existence and never by time). That was wrong. It generalized from a true fact — `reschedule_booking` checks only `status != "confirmed"`, never whether `start` is in the past — to the false conclusion that admin moves are rule-governed reschedules. They aren't; they only look that way because the admin currently has no other door. Don't reintroduce a forever-resolution requirement on that basis.

What *is* true, and independent of timing: policy and the rest of the wiring are frozen onto the booking at creation and **copy forward on reschedule** (the original booking's terms, never the link's current ones), so they resolve correctly no matter when a move happens or what state the link is in.

**BookingLink data model** — **shipped, across three passes.** Pass 1 landed the rename off `EventType`, the `status` lifecycle, `slug`, and reassignment. Pass 2 landed the `booking_types` table and the kind facet. Pass 3 landed the policy freeze: the occurrence-level four and the series-level two are copied onto `Booking`/`BookingSeries` at creation, and `cancel_action`/`reschedule_action` read the row rather than the link. Still unbuilt: the intake-form tables (`form_fields`, `booking_link_fields`, `event_field_responses`) — contact info remains fixed columns on `Booking`/`BookingSeries` — and a business-wide default policy that links inherit from, which is a separate future feature rather than a missing half of Pass 3.

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
| **Kind** | what it was *sold as* | `booking_type_id` — FK frozen at creation, repointable per row | the link changing type never moves it; an admin repointing that row does |

Source works because archiving guarantees the FK never dangles and never needs nulling — "everything under link 47" stays answerable forever, which is what makes it both a filter and the handle for bulk repair. Renaming a link relabels the source group (correct — same source, new name); it can never fragment it.

**Nothing automatic ever changes this FK** — not creation, not reschedule, not materialization of a series occurrence. But an admin can **explicitly reassign** it, and must be able to: a booking under an archived link has inert rules, so pointing it at a live link is the only way to make it customer-reschedulable again. Reassignment discards the original attribution, which is accepted — it's a deliberate, visible admin action. So the column is best read as *which link governs this booking*, not as an immutable audit record of what produced it.

Kind is the mutable classification. It drifts from the link on purpose: a link that switches from stamping "Intro Consult" to stamping "Cheer Practice" leaves every earlier booking still saying "Intro Consult," and an admin can re-classify any single row without touching what produced it.

**Freeze the pointer, never the row.** `booking_type_id` is frozen in the sense that nothing automatic ever repoints it — but what it *resolves to* is live, because the `booking_types` row it points at is editable. That distinction is the whole design:

- **rename the type row** → every booking pointing at it relabels, past included. A typo fix is a correction, not a fork.
- **change which type a link stamps** → future generations only. Existing rows keep their pointer.

A frozen *string* could do the second but never the first: renaming would leave old rows holding the old text, splitting one group into two with no way to reunite them. This is the same rename-fragmentation argument that makes archiving (rather than deleting) the right call for links — a live FK follows a rename for free, a copied string never can.

**Accepted cost:** you can't bulk-change wiring by editing the link. For standalone bookings that's per-row editing only, which is fine — standalone bookings are meant to be standalone. For series, scoped edits cover the bulk case (this occurrence / this and following / all — the Google Calendar model); that's backlog, not part of the first cut.

**Two naming fields on the link, doing unrelated jobs:**

| Field | Job | Unique? | Effect of editing |
|---|---|---|---|
| `slug` | the public URL, and nothing else — semantically empty | **among non-archived links only** | changes the URL; nothing downstream inherits it |
| `booking_type_id` | which **kind** it stamps onto what it generates | n/a — an FK; the *label* it points at is globally unique | future generations only |

**`slug` — unique among active, released on archive.** A partial unique index scoped to `status = 'active'`. Collision → **reject** with a 400, which is a good error precisely because the blocking link is live and visible: the admin can go rename or archive it. Archiving *releases* the name — the row keeps its slug value for display, it simply stops being uniqueness-enforced and stops routing. **Routing resolves only among active links**, which is what keeps duplicate values unambiguous.

The consequence, accepted deliberately: a stale `/book/consultation` URL saved by a client can later reach a *different* link that took the name. This affects only strangers arriving cold to make a **new** booking — never an existing booking, whose manage link is keyed on its own `public_id` and whose rules are reached through the FK. And the admin controls whether that reuse is semantically legitimate, since they choose to reuse the name. See "Two entry points" above.

**`booking_types` — a table, not a string column.** One row per kind: `label` (globally unique) and `color`. Nothing else — no rules, nothing that branches on it. **Many links may point at one type**, and that's the point: it's what lets two different links group their bookings together. Uniqueness is on the *label*, so "one kind is one row" holds structurally and `Consultation` can't fragment into `Consult` / `consultation`.

`booking_type` rather than bare `type`: `type` is a Python builtin and too generic for a column.

**The picker is the CRUD.** There's no page for types — one combobox component, reused in the link editor and on booking/series rows, where each row has rename and delete on hover plus a "+ New type" at the bottom (the shape Notion's select-properties and Airtable's single-select editor both use). Rename needs a home *somewhere*, since propagating renames is the entire reason this is a table rather than a string; putting it in the picker is what stops a separate management route from being necessary. Delete is unguarded — referring rows just lose their label via `ON DELETE SET NULL` — but the frontend warns first with `GET /booking_types/{id}/usage`.

**Nullable everywhere.** A link with no type, and a booking with no kind, are both fine — it's a label, not a requirement, and forcing one would mean creating a type before you can create your first link.

**Not in the first cut:** a per-booking generated display name (built from a link-supplied template like `{first} {last} — {duration}`). Bookings and series have no name of their own yet. When it lands, keep the template pointed at that field and **never** at the type — templating the grouping key would produce a distinct bucket per booking, fragmenting grouping by construction.

Schema sketch:

```
booking_types                         [just a label -- nothing branches on it]
  id
  label                               globally unique; renaming relabels every row
                                      pointing here, past bookings included
  color                               hex, nullable

booking_links                         [the factory -- archive only, row lives forever]
  id, description
  slug                                public URL only; unique among non-archived
                                      (partial index) -- released on archive
  status                              active | paused | archived
  archived_at                         audit metadata, not a second state field

  -- CALENDAR RULES (live while active; inert once archived -- nothing reads them)
  duration_minutes | min_/max_duration_minutes
  buffer_before, buffer_after, slot_increment
  max_per_day, max_per_week, min_lead_time, booking_horizon
  recurring, freq, interval                 WEEKLY/1 only today, CHECK-constrained
  expires_on, count                         mutually exclusive: a date or a quota, never both
  booker_can_set_recur_until                 only valid on an indefinite link
  booker_can_set_count                       only valid on a count link

  -- WIRING (copied onto each booking at creation, never propagated after)
  booking_type_id -> booking_types.id   which kind it stamps; nullable, SET NULL
                                        many links may share one type -- that is how
                                        their bookings group together
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
  booking_type_id -> booking_types.id       nullable, SET NULL   <- KIND facet
                                            POINTER frozen at creation, but what it
                                            resolves to is live -- renaming the type
                                            relabels this row too. Admin-repointable
                                            per row; series occurrences copy off the
                                            SERIES, not the link. Indexed (facet key).
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

**The test for which bucket a field belongs in: is it a promise to the booker, or an operational setting?** Policy, the kind label, and contact info were told to a specific client at a specific moment — rewriting them retroactively changes what someone was promised, so they freeze. Calendar rules are nobody's promise; they govern the volume and shape of slots you're willing to offer *now*. Freezing them would only mean an old booking reschedules against constraints you've since abandoned.

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

**Wiring (copied at creation)** — `booking_type_id`, policy, contact fields, intake answers. Written once by the link, then owned entirely by the row. An admin edits them on the booking or series directly; the link has no bulk-edit path into them by design.

**Why the kind is a table and not a frozen string.** A frozen string column was designed first and dropped. It does everything the table does *except* propagate a rename — and that turns out to be the operation that matters. Fix a typo (`Consultaton` → `Consultation`) and a string doesn't correct anything, it **splits the bucket**: past bookings keep the typo forever, and reuniting them means a bulk `UPDATE` across `bookings` and `booking_series` matched on exact string equality. A typo fix becomes a data migration.

That's the same rename-fragmentation argument used twice elsewhere in this document — against snapshotting a link's slug on delete, and for grouping source on the FK. A live FK follows a rename by construction; a copied string never can.

Rebuilding propagation on top of strings means reinventing the table informally: a canonical option list (`SELECT DISTINCT`, which surfaces every typo ever made as a legitimate menu item), a bulk rename, and nothing preventing `Consult` / `consultation` drift. And the moment a kind needs a second attribute — a color, which it does — a string can't carry it at all. Two fields is an entity.

What the string design *would* have been right for is a different requirement: if a rename should mean "this is a new kind now, old bookings keep what they were sold as." That's coherent, and it's what Shopify's frozen `line_item.title` does. It just isn't what's wanted here.

**Scoped edits are one UPDATE with a different predicate**, uniformly, at both layers — `id = X` (this booking), `series_id = X AND start >= pivot` (this and following), `booking_link_id = X` (all from a link), `booking_type_id = 5` (everything of one kind). Output only; the link is edited on its own page. Note the typo case is *not* in that list any more — it's a rename on the type row, which propagates on its own.

**Policy** — **frozen onto the booking at creation, never live-referenced afterward.** Copied from the link onto both `Booking` and `BookingSeries`, then owned entirely by the row. Not versioned.

Policy is a promise made to one client at one moment, so it's **wiring** in the sense used above: reading it live would mean tightening a cancel window next week retroactively strips rights from someone who already booked under the old terms. `get_cancel_action`/`get_reschedule_action` (`policy.py`) take the row's own columns; nothing consults the link after creation.

**Two independent levels, because ending an engagement isn't the same promise as dropping one session:**

| | Columns | Vocabulary | Governs |
|---|---|---|---|
| **Occurrence** | `cancel_mode`, `cancel_notice_minutes`, `reschedule_mode`, `reschedule_notice_minutes` | full set incl. window modes | cancelling/moving one booking |
| **Series** | `series_cancel_mode`, `series_reschedule_mode` | `blocked` / `auto` / `request` only | cancelling/moving a whole series |

The series pair carries **no notice window** on purpose. "24 hours' notice" is about warning someone off a specific session; ending a six-month arrangement has no obvious instant to measure against. The old behaviour measured it from the next occurrence, which meant a session tomorrow blocked you from ending the arrangement at all. With no timing, the mode *is* the verdict — which is why `blocked` replaced `not_allowed` throughout, so mode and verdict share one vocabulary, and why `BookingSeriesResponse` aliases `cancel_action` straight onto the column rather than computing anything.

**Where the columns live and who copies what:**
- `BookingLink` holds all six as the settings a booking is stamped from. All modes `NOT NULL` with `server_default='auto'` — there's no sentinel `NULL` meaning "auto" any more.
- `BookingSeries` holds all six: the occurrence four are the **template** each occurrence copies, the series two govern acting on the series itself.
- `Booking` holds the occurrence four only.
- `create_booking` copies off the link; `_ensure_occurrence` copies off the **series** (so an occurrence Procrastinate materializes next year gets the terms the client agreed to, not the link's current ones); both reschedule paths copy off the **old row**, never a fresh read.

**The verdict is computed in the response schema**, not on the model — a `@computed_field` on `BookingResponse`. That's what puts virtual occurrences (built in memory, never loaded from a row) on the same path as materialized ones; previously `_virtual_occurrences` called the policy functions by hand, which is exactly the shape that let `booking_type_id` go missing there in the prior pass. Enforcement is separate and stays in the router, because the client can't be trusted and a cancel arrives with no DTO.

**Editing an existing booking's terms** is the row's own `PUT` (`/bookings/{id}`, `/booking-series/{id}`) — the same plain-column update that carries contact info and `booking_type_id`. The link has no path into an existing row by design. Series-scoped edits (this / this-and-following / all) are backlog.

**No `Policy` entity, and no business-wide default yet.** A standalone shared table was designed and dropped twice — see the `tms-roadmap` skill's Decisions. A business-wide default on `Settings` that links inherit from is a separate future feature (`tms-roadmap`, Future Features), not a deferred half of this: policy is set per link today, and nothing here anticipates inheritance.

**Client info** — two distinct things, not to be conflated:
- **Fixed contact fields** (`first_name`, `last_name`, `email`, `phone`) — plain columns on **both `Booking` and `BookingSeries`** (as they already are today), email/phone nullable. For logged-in users, copy their profile name/contact *at booking time* rather than referencing `user_id` live — consistent with the freeze-on-creation pattern elsewhere, since an old booking's client-facing info shouldn't drift when someone edits their profile later.
- **Custom intake questions** — a reusable label:answer system. `form_fields` is the shared question library (name, input type, options); `booking_link_fields` is the live join saying which questions a link asks, in what order, required or not; `event_field_responses` stores one answer row per question per booking, with a `label_snapshot` of the question text *as it was asked*, so later renaming a question never rewrites the meaning of an old answer.

**Deletion rules under this model:**
- **Links** — **archive is the only delete, at any child count.** `status='archived'`: URL 404s, rules go inert, row goes read-only, and it drops out of the admin list and every create-flow. **Permanent in behavior — no restore**; a booking stuck on an archived link is rescued by reassigning *the booking*, never by resurrecting the link. This is what keeps `booking_link_id` NOT NULL and non-dangling forever, which is both the source facet and the bulk-repair handle. (It also removes guards rather than adding them: no zero-children check, no `ON DELETE` behavior to choose, no nullable FK branches.)
- **Bookings** — never hard-deleted in normal operation. Cancel via `status='cancelled'` + `cancelled_at`/`cancelled_by`, needed for cap counting and no-show history. (The admin `.../permanent` endpoints remain as a deliberate escape hatch.)
- **Form fields** — soft delete via `archived_at`. Removing one from a link's form just drops it from `booking_link_fields`; existing `event_field_responses` keep their `label_snapshot` regardless.
- **Slugs** — unique among `active` links only (partial index), **released on archive**. The archived row keeps its slug value for display; it just stops being uniqueness-enforced and stops routing. A new link may take the freed string, so a stale bookmarked URL can reach the successor rather than 404ing — deliberate, since that's the desirable outcome when you retire and relaunch the same offering, and it only ever affects strangers arriving cold for a *new* booking.
- **Booking types** — plain hard delete at any usage count, `ON DELETE SET NULL` on every referring FK. Nothing to guard: a type carries no rules, so deleting one just unlabels rows. `GET /booking_types/{id}/usage` gives the frontend the counts to warn with first. No archive state — with a globally unique label and no unarchive UI in a create/edit/delete picker, archiving would permanently burn the name.

**Precedents** — **Google Calendar Appointment Schedules** is the closest match and, under this model, no longer a negative example: its schedule is a generator whose settings are live, and what it produces are ordinary calendar events you edit individually. **Google Calendar recurring-event edits** (this event / this and following / all events) is the model for scoped wiring edits on a series, and its click-the-grid event creation is the model for the planned admin direct-add. **Stripe Product/Price** is instructive as the *contrasting* shape — Price is immutable and invoices reference the specific one active at the time, which is what you build when the parent row carries identity that its output must inherit. Here the parent carries *provenance*, not inheritable identity, so the output copies its own kind label instead of pointing at a frozen version.

**Terminology used above** — *live FK*: the child reads the current value through a shared row, no freezing (here: `booking_link_id` for calendar rules, and for the source facet's display label). *Governing FK*: a NOT NULL FK that always resolves (because the parent is archived, never hard-deleted) and that nothing automatic ever repoints — but which an admin may deliberately reassign. Safe to group on because it never dangles, and usable as a repair handle because it's reassignable. Distinct from true provenance, which would be immutable; this trades that for the ability to rescue a booking whose link has gone inert. *Inert*: stored but no longer read by any code path (a deleted link's slot rules) — the reason freezing them from edits costs nothing. *Frozen copy (denormalized)*: value copied onto the booking at creation and never touched by the parent again, though an admin can edit it on the row itself; no history of prior values kept. *Versioned*: normalized, edits insert new rows instead of updating, old rows persist and stay inspectable — **not used in this model**; it was in an earlier draft (archive-and-relaunch forking) and was removed along with the identity-on-the-link premise. *Override*: nullable field on the child that inherits the parent's live value unless explicitly set — used for policy against `Settings`, and nowhere else.

**BookingLink recurrence modes** — how a series ends. Modelled directly on iCal: `UNTIL` (a fixed date) and `COUNT` (a quota) are **different facts, not two spellings**, and RFC5545 forbids both in one rule. The link stores them as `expires_on` and `count`; `BookingSeries` gets `until` and `count`. Three mutually exclusive modes on `recurring=True` links:
- **Fixed date** (`expires_on` set): every series from this link ends on that date. Neither booker override is allowed — that date is the admin's decision. `BookingSeries.until = expires_on`.
- **Session count** (`count` set): every series runs for N occurrences. `booker_can_set_count=True` lets the booker shorten it via `BookingCreate.recur_count`. `BookingSeries.count = N`.
- **Indefinite** (both null): `until` and `count` both null. `booker_can_set_recur_until=True` offers an optional end-date picker; left blank it runs forever.

`count` counts occurrences **of the rule**, not attended sessions — a cancelled occurrence still fills its slot, same as Google. Whether a cancellation should be forgiven is a billing question, not a recurrence one.

**Where the rule ends vs. whether the series is still running are separate questions**, deliberately answered by different code. `series_last_date()` (`booking_utils.py`) resolves the rule's own end — for `count` that's arithmetic off `dtstart`, since a rule is a fixed progression — and drives *generation bounds* only. "Is it still running" is derived from the **bookings** (`active_series_filter` / `is_series_active`): a finite series is running while any occurrence hasn't passed. That's what makes an occurrence rescheduled past the rule's end keep its series alive instead of stranding it, and it's why no end date is ever stored — a denormalized one goes stale the moment an occurrence moves.

**A bounded series materializes every occurrence at creation**; only indefinite ones are kept sparse for `extend_all_series` to roll forward. `_virtual_occurrences` *asserts* this rather than handling both, and its callers scope with `indefinite_series_filter()`.

**Backend validation** (schema + CHECK constraints on both tables): `count` and `expires_on` are mutually exclusive; the two booker overrides are mutually exclusive and each is only valid for its own mode (`booker_can_set_recur_until` with an indefinite link, `booker_can_set_count` with a count link); `expires_on` allows neither. `count` must be ≥ 2, `expires_on` must be in the future, and bookings can't be created after `expires_on`.

**`freq`/`interval` are real columns** on both the link and the series, but constrained by `models.FREQ_DAYS` / `SUPPORTED_INTERVALS` and CHECK constraints to `WEEKLY`/`1` — the only combination generation is tested for. Occurrence arithmetic is algebraic (`series_step()` = `interval * FREQ_DAYS[freq]`), so widening a constant is most of the change when biweekly or daily lands. The exception is `available_slots.py`, whose `WeekdayTime` bands still read *existing* series as weekly — see its module docstring.

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

**Links page pattern** (`Links.tsx` + `LinkPage.tsx`) — diverged from Availability's inline-swap pattern into a separate routed page instead:
- `Links.tsx` is list-only — cards with a link/button that `navigate()`s to `/links/:id` (or `/links/new`), no inline form swap.
- `LinkPage.tsx` is a full routed page, tab-driven via `?tab=` search param (`details`/`duration`/`recurrence`/`hosts`/`cancellation`/`limits`/`booking`). Each tab in the nav shows a small red dot (`tabHasError`) if that section currently has a validation error, computed from the same `errors` object across all tabs at once.
- Tutor rows: array of `{ tutorId, scheduleId }`. When tutor changes, schedule auto-resets to that tutor's default.
- Duration: Switch toggles between fixed `durationMinutes` and custom `minDurationMinutes` / `maxDurationMinutes`.
- Validation: name required; duration > 0 (or min/max valid and min < max); at least one tutor row with both fields set. Same `validate(form): FormErrors` shape as before, just now surfaced per-tab instead of in one form.
- No `isDirty`/discard-confirm pattern here (unlike ScheduleForm) — navigating away from a routed page doesn't have the same in-place "close without saving" concern as swapping a card back.
- `extractError` is a module-level pure function (not inside component).

## Future Integrations

- **Discord bot** — monitor voice channels to auto-calculate lesson duration. Bot detects tutor + student join/leave a designated channel, calculates actual hours, calls TMS API to populate `hrs` on the lesson record. Requires: `discord_user_id` on Tutor, Booking→Lesson FK link, TMS endpoint to receive duration from bot. Must be a bot (not webhook) — webhooks can't monitor voice state.

See the `tms-roadmap` skill for the backlog (known TODOs, planned improvements, and future features under evaluation) — moved out of this always-loaded file since it's reference material, not needed every session.

**BookingPage** (`BookingPage.tsx`):
- Public-facing route `/book/:slug`, outside the admin layout — no sidebar or header.
- Two-panel: dark left panel (event info, tutor selector if multiple, selected slot summary), light right panel (calendar or contact form).
- Three steps: `pick` → slot browser, `contact` → student/parent form, `done` → confirmation + calendar links.
- Slot browser: month view (7-col calendar grid + right-side slot panel) or week view (7-col day grid). `slotsByDate` filters past slots via `new Date(slot.start) < now` and groups by local date string.
- Timezone: auto-detected via `Intl.DateTimeFormat().resolvedOptions().timeZone`. Displayed in left panel. All display uses `Intl.DateTimeFormat` with the detected timezone.
- Calendar links on done step: Google Calendar (template URL), Outlook.com, Office 365 (both via `buildOutlookUrl`), Other (ICS blob generated client-side via `buildIcsBlobUrl` + `URL.createObjectURL`). Outlook icon loaded from `assets/outlook-icon.svg`.
- Module-level helpers: `toCalDate`, `buildGoogleCalUrl`, `buildOutlookUrl`, `buildIcsBlobUrl`, `localTimeOf`, `localLongDateOf`, `localDateOf`, `addDays`, `startOfWeek`, `startOfMonth`, `endOfMonth`, `toLocalDateStr`.
- `loadSlots` fetches `/bookings/available-slots` with `tutor_ids`, `booking_link_id`, `time_min`, `time_max`. Re-runs when `bookingLink`, `currentDate`, `view`, or `selectedTutorId` changes.
- Contact form validation: student first/last required; at least one email AND at least one phone (student or parent).
- `submitting` state disables/shows spinner on "Confirm booking" button via Mantine `Button loading` prop.
- **Reschedule flow** (`rescheduleFromId` in `location.state`): slot picker works identically; on slot select shows confirm screen instead of contact step; submit calls `POST /bookings/{id}/reschedule`. Contact pre-filled from state, locked.
- **Change series schedule flow** (`rescheduleSeriesId` in `location.state`): same slot picker; confirm screen shows "Confirm series change" copy; submit calls `PUT /bookings/booking-series/{id}` with `buildReschedulePayload()`. No calendar links on done step. Tutor pre-selected from `location.state.tutorId` but changeable.
- State keys passed via `navigate()` are camelCase: `rescheduleFromId`, `rescheduleSeriesId`, `tutorId`, `originalStart`, `originalEnd`, `studentFirst`, `studentLast`, `studentEmail`, `studentPhone`, `parentEmail`, `parentPhone`.
