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
    Bookings.tsx       # Admin bookings page AND customer /my-bookings page (isCustomer prop) — tabs, series/standalone grouping, request approve/deny (admin only)
    BookingRow.tsx     # Shared row component for Bookings.tsx — admin menu (reschedule/cancel/delete/no-show) vs customer-mode compact view
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

**Delete protection**: Deleting a student or tutor with existing lessons returns 409. Lessons must be deleted first (or done directly in DB). Cascade delete is intentionally removed to protect financial records.

**Tutor `is_active`**: Both Student and Tutor have `is_active`. Retiring a student/tutor means setting `is_active=False`, not deleting them.

**Booking system** (`routers/bookings.py`):
- `POST /bookings/` — creates Google Calendar event first, then DB record atomically. Recurring event types: creates one RRULE Google Calendar event + one `BookingSeries` row + one `Booking` row per occurrence (generated inline via a loop). Standalone: one event + one `Booking` row. If DB fails, compensating delete on Calendar. If compensating delete also fails, logs warning.
- `PUT /bookings/{id}` — contact-info-only update (student/parent name, email, phone, is_no_show). No Google Calendar calls. Plain DB write. `is_no_show=True` is how no-show is recorded — no separate PATCH endpoint.
- `POST /bookings/{booking_id}/reschedule` — atomic saga: for series bookings, patches specific RRULE instance via `events().instances()`; for standalone, creates new event + deletes old. Inserts new `Booking` row, soft-deletes old (`status='rescheduled'`, `rescheduled_to=new_id`).
- `DELETE /bookings/{id}` — soft-delete one occurrence (`status='cancelled'`). For series: patches specific RRULE instance cancelled via `events().instances()`. For standalone: deletes calendar event.
- `DELETE /bookings/{id}/permanent` — hard delete with optional `cascade` param to also delete predecessor (booking that `rescheduled_to` this one).
- `DELETE /booking-series/{id}` — truncates RRULE to today (`UNTIL=YYYYMMDD`), bulk-deletes future occurrence rows, soft-deletes series (`is_active=False`).
- `PUT /booking-series/{id}` — truncates old RRULE, creates new RRULE event on (potentially new) tutor's calendar, drops future occurrence rows, updates series metadata, regenerates **only occurrence 1** (not the whole series) — later occurrences resolve implicitly from the series' updated pattern, same as any indefinite series.
- `GET /bookings/available-slots` — three-mode branched algorithm in `routers/available_slots.py` (see module docstring for full complexity analysis and edge cases). Modes: `standalone` (non-recurring), `finite` (expires_on or recur_weeks set), `infinite` (neither). Infinite mode runs `thin_schedule_dateless` first — subtracts existing infinite series from the schedule in (weekday, time) space O(S+R), resolves survivors to concrete dates once. All modes then run a shrinking-batch week-over-week two-pointer sweep O(N×(C+R+B)) vs naive O(C×N×(R+B)). Edge cases: pre-time_min busy spill, midnight-crossing sessions, Sun→Mon dateless seam, UTC time_min shifting to prior local day. All times in business canonical timezone (Settings.business_timezone).
- Recurring bookings: `recurring=True` adds `RRULE:FREQ=WEEKLY` (no BYDAY — day inferred from DTSTART by Google). `UNTIL=YYYYMMDD` appended when truncating or when series has a `recur_until` date.
- Indefinite series materialization: for finite series, all occurrence rows are generated at creation time up to `recur_until`. For indefinite series (`recur_until IS NULL`), only a sparse rolling window is kept materialized — `tasks.py`'s `extend_all_series` (Procrastinate `@app.periodic`, daily cron `0 2 * * *`) finds active indefinite series and fans out one `extend_single_series` job per series, which calls `_ensure_occurrence` (`booking_utils.py`) to materialize the next occurrence as needed. No large pre-generated buffer, no `generated_through` column (doesn't exist — this state is implicit).
- Opaque ids: `Booking`/`BookingSeries` have a `public_id` (UUID) alongside their internal integer PK; API responses (`id`, `series_id`, `rescheduled_to`) expose only `public_id` — the integer PK is never serialized. For a series-bound `Booking`, `public_id` is set explicitly at creation/materialization time (not computed at read time) to the composite form `"{series.public_id}:{unix_timestamp}"`, mirroring Google Calendar's recurring-instance-id scheme (`{baseEventId}_{timestamp}`) — write-once, not recomputed on every serialize. Standalone bookings just get a plain generated UUID.
- `resolve_ref(ref, db, settings)` (`booking_utils.py`): resolves either form of the ref above to a `Booking` row — tries a direct `public_id ==` lookup first (covers standalone bookings and any already-materialized series occurrence), and only on a miss falls back to parsing the composite form and calling `_ensure_occurrence` to materialize it. **Materialization only happens on routes representing genuine write intent** (`reschedule`, cancel/`DELETE`, `DELETE .../permanent`, contact-info `PUT`) — `GET /{booking_id}` deliberately does a plain non-materializing lookup and 404s on a ref that isn't a real row yet. Browsing virtual (not-yet-materialized) occurrences — e.g. a paginated customer booking list — must construct them in memory without persisting; only an actual client action should turn a virtual occurrence into a real row.
- Route ordering: `/available-slots`, `/booking-series/{id}` must appear before `/{booking_id}` in bookings.py to avoid FastAPI matching literals as ints.
- **Pagination (`GET /bookings/`) — current behavior:**
  - Two modes: **bounded** (`time_max` given — Day/Week/Month/Range) returns `total`, no `has_more`. **Unbounded** (`time_max` omitted — Requests, via `pending_only`) returns `has_more` (over-fetch-by-one), no `total`.
  - `page_size` defaults to `DEFAULT_PAGE_SIZE` (250, Day/Week/Month omit it) or is overridden smaller (Range/Requests, `PAGE_SIZE=10`).
  - Frontend usage, concretely:
    - **Day/Week/Month:** always bounded, default `page_size`. `PageNav` basically never triggers (`total` rarely exceeds 250).
    - **Range, dates picked:** bounded, `page_size=10` — the one bounded view `PageNav` actually gets exercised on regularly.
    - **Range, no dates picked yet:** `dateFrom`/`dateTo` are both `null` → `time_max` is `undefined` → silently falls into the *unbounded* branch. A real edge case, worth remembering when touching this code.
    - **Requests:** always unbounded (`pending_only=true`), drives `LoadMoreSentinel`/"Load More", not `PageNav`.
  - No pagination metadata in response headers — all of it (`total`/`has_more`/`page_size`) lives in the response body (`BookingListResponse`).
- **Known inefficiency:** every page request regenerates the whole result set from scratch — no resume point exists today. The materialized `Booking` query re-fetches everything in the window every call, and `_virtual_occurrences` (`booking_utils.py`) always walks forward from `series.start_date`/`time_min`, never picking up where the previous page left off. This is expensive specifically *because of virtual occurrences* — they aren't real DB rows, so there's no index to `OFFSET` into; the only way to know what occurrence N looks like is to walk there week by week from the start. Unbounded cost scales with `page * page_size`; bounded pays a constant full-window cost on every click regardless of depth (Range feels this the most). Also a real resource-exhaustion gap: `page` has no upper bound, and bounded queries have no cap on `time_max - time_min` width — nothing stops a 200-year window from being requested.
  - **Planned fix:** let the client pass a starting point — a resume marker (an occurrence's `start` timestamp, already available via the existing `public_id` composite form, `"{series_id}:{unix_timestamp}"`, so no new identifier is needed). When present: the materialized query seeks via `WHERE start > resume_point` instead of re-fetching everything, and `_virtual_occurrences`'s `floor_date` starts from it instead of always from `series.start_date`. Cost becomes `O(page_size)` regardless of depth or window width — fixes the inefficiency and the resource-exhaustion gap as the same change. `total` would only get computed on the *first* request for a given filter set (no starting point given yet) — later requests just seek and reuse it, since an honest total requires walking the whole window at least once and that cost can't be avoided on that one call.
  - **Planned hardening — turn the starting point into a token:** a plain client-passed date/id has two problems, both solved by making it opaque instead: (1) a plain value invites clients to hand-construct it themselves, which breaks the moment the encoded shape needs to grow (e.g. adding a tiebreaker for two occurrences sharing the same `start`); (2) nothing would stop a token minted under one filter combination (say `tutor=Alice`) from being replayed against a different one (`tutor=Bob`) and silently returning a mismatched page — a token needs to be scoped to the specific query it came from. The token should encode the resume timestamp, a tiebreaker, and a fingerprint of the filters it was generated under, and the server should reject it if replayed against a different query.
  - **Planned direction for `PageNav` itself:** leaning toward dropping page-number jump entirely in favor of simple Next/Prev (cursor-based, no `total` needed) — but building the bounded/`total`-returning endpoint as a separate, still-available option rather than deleting it outright, in case a real need for exact totals/page-jump shows up later. Two endpoints, only one actively wired up at first.
  - Full design tracked in `.claude/plans/cursor-pagination-and-endpoint-split.md`. Not implemented — do not assume this behavior exists in the current code.
- **Filtering / facets** (`booking_utils.py`): `GET /bookings/` and `GET /booking-series` both take `tutor_ids`/`event_type_ids`/`student` filters and return `facets` alongside `items` in the same response — the self-excluding, Google-Flights-style filter-checklist options, not just the filtered results.
  - `apply_scope_filters(query, model, tutor_ids, event_type_ids, student_pairs, email=None, exclude=None)` — the one shared filter-applier for both `Booking` and `BookingSeries` (same column names on both). `exclude` skips one dimension's own clause — this is what makes self-exclusion possible. Students match on `(student_first, student_last)` exact pairs via `tuple_(...).in_(...)`, not independent `.in_()` calls on each name — matching first/last separately would cross-match two different guests who happen to share only one name. Pairs travel over the wire as a pipe-delimited `"First|Last"` string (same composite-string convention as `public_id`), decoded server-side.
  - `compute_timeline_facets(...)` / `compute_series_facets(...)` — call `apply_scope_filters` three times per request, once per facet dimension, each time excluding that one dimension so its own filter never narrows its own options. For the timeline version, series facets are existence-checked via `_virtual_occurrences(series, time_min, time_max, count=1, settings)` — reuses the real occurrence-window walk rather than reimplementing date math, `count=1` since facets only need "does this series contribute anything here," not the full list.
  - **Self-exclusion only protects a facet from its own filter** — it does nothing about *other* active filters (or the time window) legitimately narrowing a selected value out of the response. Today the frontend handles that (`withSelectionKept` in `Bookings.tsx` — keeps a selected-but-now-zero-match value visible via a fallback label instead of letting it silently disappear). **Next pass:** move this to the backend instead — union the currently-selected values back into each facet's own response (resolved via a direct id lookup, or for students just echoing the given pairs back) — makes "a selection never disappears from its own facet" part of the API contract itself rather than a per-client patch every consumer has to reimplement.
- Datetimes stored as UTC (`DateTime(timezone=True)`). `BookingCreate`/`BookingReschedule` schemas convert client-local time to UTC via `model_validator(mode="after")` using `zoneinfo`. `Booking.timezone` is the booker's timezone captured at booking time — consumed once as a write-time UTC-conversion input, then not read again by any current frontend display path (`utils.ts`'s `formatDate`/`formatTime` and `BookingPage.tsx` both format using the *current* viewer's live-detected timezone, ignoring the stored value, which is strictly more correct for a live browser session). The stored value still earns its keep for a context with no live browser to detect from — the not-yet-built confirmation/reminder emails (see Known TODOs) — where it's the only available signal for which zone to render in, accepting that it can go stale if the booker has since moved/traveled.
- **SQLite drops tzinfo on read** (tests only — Postgres preserves it): a `DateTime(timezone=True)` column comes back naive from SQLite even though it's tz-aware in prod. Two failure modes this causes if unguarded: comparing a naive value against an aware one raises `TypeError`, and calling `.timestamp()` on a naive `datetime` silently uses the *local system clock's* timezone instead of UTC. Standard guard used throughout `booking_utils.py`: `dt if dt.tzinfo else dt.replace(tzinfo=UTC)` before any comparison or `.timestamp()` call — assumes naive means UTC, which matches how everything here is actually stored.
- Manage links (`/manage-occurrence/:ref`, `/manage-series/:ref`) are keyed directly off `public_id` — no separate token concept. See the `public_id`/`resolve_ref` entries above.
- `google_event_id` is non-nullable on `Booking` — a booking without a calendar event is a broken record.

**Schedule system**:
- `Schedule` — belongs to a tutor (`tutor_id`), has a `name`, `is_default` flag, `timezone`, and a list of `ScheduleDay` rows. Name is unique per tutor (`UniqueConstraint("tutor_id", "name")`).
- `ScheduleDay` — one row per time period per day (`day_of_week` 0–6, `start_time`/`end_time`). Multiple rows per day allowed to support non-contiguous periods (e.g. 9–12, 2–5).
- `EventTypeAvailability` — junction table linking `event_type_id` + `tutor_id` + `schedule_id`. Unique on `(event_type_id, tutor_id)`. PUT on this endpoint only allows changing the `schedule_id` (tutor reassignment not supported). This is what available-slots queries to know which schedule applies per tutor per event type.
- `is_default` flip: creating/updating a schedule with `is_default=True` automatically sets all other schedules for that tutor to `is_default=False`. Updating an existing default to `is_default=False` is blocked (must set another as default first). Cannot delete the default schedule.
- Deleting a tutor cascades to their schedules and availability rows. Deleting an event type cascades to its availability rows.
- `available-slots`: all schedule and series times stored in business canonical timezone (`Settings.business_timezone`). `Schedule.timezone` and `BookingSeries.timezone` are nullable/redundant — pending refactor to load from Settings directly. On timezone change: shift all stored times by old→new offset using current DST state, then update Settings.

**EventType**: duration can be fixed (`duration_minutes`) or custom range (`min/max_duration_minutes`). Name is unique. Business logic validated at router layer.

**EventType recurrence modes** — three mutually exclusive modes on `recurring=True` event types:
- **Fixed expiry** (`expires_on` set, `recur_weeks` null): all series end on that date. `booker_can_set_recur_until` must be False — backend enforces. BookingSeries gets `recur_until = expires_on` (all rows generated at creation).
- **Relative duration** (`recur_weeks` set, `expires_on` null): series runs N weeks from booking start. `booker_can_set_recur_until=False` → `recur_until = start + N weeks`, locked. `booker_can_set_recur_until=True` → booker sees date picker pre-filled to start + N weeks, can override.
- **Indefinite** (both null): `recur_until = null` on BookingSeries. `booker_can_set_recur_until=False` → infinite, no picker. `booker_can_set_recur_until=True` → optional picker, infinite if left blank.

Backend validation: `expires_on` and `recur_weeks` cannot both be set. `expires_on` + `booker_can_set_recur_until=True` is rejected. Bookings cannot be created after `expires_on`.

**Booking contact rules**: at least one email (student or parent) AND at least one phone required. Enforced at both router and DB (`CheckConstraint`) level.

## Frontend Patterns

**UI stack**: Mantine v7 for form controls (Select, NumberInput, Modal, etc.), Tailwind for layout/spacing. Native `<input>` elements used inside the inline edit form in `LessonRow.tsx`; Mantine components used in `LessonAddModal.tsx` and `BulkAddCard.tsx`.

**Layout**: Dark sidebar (`bg-gray-900`, collapsible via `sidebarCollapsed` state + `transition-all`), header with hamburger toggle. React Router routes: `/` → LessonsTable, `/tutors` → Tutors, `/bookings` → Bookings, `/my-bookings` → Bookings (customer mode).

**LessonsTable view model**: Three views — `'All' | 'Month' | 'Week'`. `useLessons` hook exposes `ungrouped` (filtered+sorted), `byMonth`/`months`, `byWeek`/`weeks`. `lessonsToDisplay` is derived in the component based on view + `periodIndex`. Switching views resets selection and `periodIndex`.

**useLessons hook**: Owns all lesson state, filter state, selection state, and handlers. Exports derived groupings (`byMonth`, `months`, `byWeek`, `weeks`, `selectionSummary`). `ungrouped` = filtered + sorted lessons.

**Table structure**: Single `<table>` always rendered. `tableRows` is pre-computed (before JSX return) via `flatMap` — inserts a day-header `<tr>` before the first row of each date group with alternating amber/indigo color tabs. `dayIndex` prop drives the left color tab on each `LessonRow`.

**Tutor bubble**: renders a colored circle with initials, color auto-assigned per tutor via `tutorBubbleClass`/`tutorInitials` in `utils.ts` — deterministic on `tutor.id` (not name), so no specific person is hardcoded. Same tutor always gets the same color across `LessonRow`, `BookingRow`, `Bookings`, and `Tutors`.

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
