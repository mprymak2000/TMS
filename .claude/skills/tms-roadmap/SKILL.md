---
name: tms-roadmap
description: TMS's backlog — known bugs, missing logic, planned improvements, and features under evaluation (moved out of CLAUDE.md's always-loaded content). Load when discussing what to build next, prioritizing work, checking whether something is a known gap, or planning a new feature.
---

## Priority Order — Path to Single-Tenant Launch

Decided sequence for the current push toward going live (single-tenant; multi-tenant is later,
not a launch blocker). Each open item has its own design doc in `.claude/plans/`:

**Denormalization boundary, stated once here since several items below depend on it**: whether a
field is a live FK or a frozen copy is decided by *when it gets read*, not by what it's about.
`Tutor` and `Contact`/`Student` stay live FKs — editing one is instantly reflected on every booking
referencing it, past and future. `EventType` is also a live FK (`bookings.event_type_id` is the
system's only filter key) but is soft-deleted rather than hard-deleted so it always resolves, and
gets *forked* when its identity genuinely changes. What gets frozen onto the booking instead:
cancel/reschedule policy (a promise to one client at one moment — resolved once from the `Settings`
business default plus any per-type override), contact info, intake answers, and `calendar_rules_id`. Full reasoning and the four-bucket split: CLAUDE.md's "EventType data model".

1. ~~Finish the filters/facets work — move the "keep a selected filter visible" logic from
   frontend to backend.~~ — done. Design: `.claude/plans/facets-selection-kept-backend-move.md`.
2. ~~Cursor-based pagination cleanup~~ — done. Design: `.claude/plans/cursor-pagination-and-endpoint-split.md`.
3. ~~Split `Bookings.tsx`'s three tabs into separate routed components~~ — done
   (`BookingsLayout.tsx` + `ScheduleTab`/`RecurringTab`/`RequestsTab`). Design:
   `.claude/plans/bookings-tabs-to-subroutes.md`.
4. ~~`BookingSeries` lifecycle field collapse + immutable reschedule (Pass 1 of the field
   cleanup)~~ — done (`dtstart`/`dtend`/`until`/`status`/`created`/`last_modified`, reschedule now
   inserts a new row instead of mutating in place). Design: `.claude/plans/booking-series-end-date-cleanup.md`
   — that doc's own "Status: not implemented" header is stale, ignore it; the work landed and is tested.
5. ~~**Deletion safety, part 1**~~ — done, all four pieces:
   - `Schedule`→`EventTypeAvailability` delete guard (`routers/schedules.py`) — 409 if any
     `EventTypeAvailability` row still references the schedule.
   - `Tutor` hard-delete guard (`routers/tutors.py`) — extended the existing `Lesson`-only check to
     also 409 on any referencing `Booking`/`BookingSeries`.
   - `Tutor.is_active=False` enforcement — `create_booking`/`_reschedule_booking`/`_reschedule_series`
     (`routers/bookings.py`) now 400 on an inactive `tutor_id`; `get_available_slots`
     (`routers/available_slots.py`) excludes inactive tutors; `extend_single_series` (`tasks.py`)
     skips materializing further occurrences once a series' tutor goes inactive.
   - Admin permanent-delete endpoint for `BookingSeries` (`DELETE /booking-series/{id}/permanent`,
     `cascade` param) — modeled directly on `Booking`'s existing `.../permanent` endpoint, same
     predecessor-chain confirm flow. No frontend wiring yet (backend-only this pass, matching how
     this item was scoped).
6. **`EventType` data-model rework — split into three passes.** Full design: CLAUDE.md's "EventType data model" (four buckets: Identity / Calendar rules / Policy / Client info) — that's the source of truth, read it first. Each pass leaves the system green and shippable; each needs its own `docker compose down -v && up -d` (no Alembic).
   - **6a — Soft delete.** `.claude/plans/event-type-pass-1-soft-delete.md`. `EventType.status` (`active`/`archived`), archived enforcement at the same three points already wired for `Tutor.is_active`, partial unique index on `name` scoped to active rows, hard delete kept only for the zero-reference mistake-entry case. Smallest, and fixes a live bug — `delete_event_type` currently has no guard at all and 500s on a raw `IntegrityError`.
   - **6b — Policy.** `.claude/plans/event-type-pass-2-policy.md`. Four fields on the `Settings` singleton as the business default; `EventType`'s existing four columns become inherit-on-`NULL`; per-pair override granularity; `resolve_policy()` at creation freezing onto **both** `Booking` and `BookingSeries`; `get_cancel_action`/`get_reschedule_action` read the row's own frozen columns; backfill on policy edit. Needs a Settings page, which doesn't exist yet and is needed for `business_timezone` anyway. **No `Policy` table** — see Decisions.
   - **6c — Calendar rules + fork.** `.claude/plans/event-type-pass-3-calendar-rules-and-fork.md`. `event_type_calendar_rules` table, `calendar_rules_id` on `EventType`/`Booking`/`BookingSeries`, `predecessor_id`, the two distinct admin edit actions, availability row copying on fork, slot generator reading from the rules table, caps enforcement with the advisory lock. Extraction and forking land together because the extraction exists *specifically* so a fork doesn't fork calendar behavior.
   - **Ordering:** 6a and 6b are independent, either order. 6c needs 6a (forking needs `status`). 6c carries almost all the test migration — every `setup_standalone`/`setup_recurring` helper and event-type payload across four test files — so budget it at roughly double the others.
   - **Superseded, do not implement:** frozen `type`/`name` text-column grouping, dropping `event_type_id` from bookings, freely/unconditionally deletable `EventType`, and a standalone `Policy` table with its own CRUD/route. `bookings.event_type_id` stays the one and only filter key, as a live FK.
7. **iCal fields on `BookingSeries`** — `freq`/`interval`/`count` columns, `byday`/`wkst` as
   comments only (Pass 2 §1-2; §3 is superseded by item 6 above). Design:
   `.claude/plans/booking-series-recurrence-fields-and-event-type-denormalization.md`. Two open
   items to resolve before implementing `count`: (a) whether it still applies once a booker
   overrides the `booker_can_set_recur_until` picker away from the `recur_weeks` default — see the
   plan doc's "Open questions"; (b) confirm "N-session package" pricing is an actual feature being
   launched with — `count` only earns its keep if that's real, since "N-week course" is already
   covered by `until`.
8. **Contact/Student identity split (guest id / student id flow) — must land before auth, not
   after.** Auth needs a real `Student`/`Tutor` identity row to attach login credentials to, and
   `Booking.student_id` is currently unreliable — see the identity-split note in Known TODOs below
   for the full `Contact`+`Student`/`Tutor` design (2-layer, not 3-layer). No plan doc yet.
   - **Client intake fields belong here, not in the `EventType` rework (item 6) where they were
     originally scoped.** `form_fields` (shared reusable question library), `event_type_fields`
     (live join: which questions a type asks, in what order, required or not), and
     `event_field_responses` (one answer row per question per booking, with a `label_snapshot` of
     the question text *as it was asked*, so renaming a question never rewrites an old answer's
     meaning). Moved because answers attach to a **person**, and there's no `Contact` entity yet —
     building them before the identity split means building on sand. Schema sketch is in CLAUDE.md's
     "EventType data model" under Client info.
   - **Open question carried over from item 6**: for a series the booker fills the form once but
     occurrences are many — do responses attach to the `BookingSeries` (occurrences resolve through
     it), or get copied onto each `Booking` at materialization (consistent with every other frozen
     field, but duplicated N times)? Not yet decided.
9. **Email + auth** — last. Auth gates at the route level (protected-route wrappers), so doing
    this after the subroute split (3) means gating the final route structure once, not redoing it
    after a later refactor.
    - **Email**: build only the minimal sending capability (pick a transactional provider, a
      thin `send_email(to, subject, body)` wrapper) — not the full confirmation/reminder email
      *feature* (see "Background jobs" below), which is separate, larger, and not needed for
      auth. The capability is shared infrastructure either way.
    - **Auth**: OTP-based (email a one-time code), not password-based — avoids building/managing
      password storage and reset flows, and reuses the email capability above rather than adding
      a second thing to build. Scope narrow for v1: one (or a small handful of) seeded admin
      user(s), no public self-signup, no forgot-password flow (not applicable — there's no
      password). Do budget real time for rate-limiting on the code-verify endpoint — an
      unthrottled short numeric code is brute-forceable in seconds, not a corner to cut under
      deadline pressure.
    - **Multi-tenant scoping rule**: once `tenant_id` exists, it must always be derived from the
      authenticated session server-side and applied to every query independently — never trusted
      from client-supplied input (cursor content, query params, body fields). Came up while
      designing cursor pagination (`cursor-pagination-and-endpoint-split.md`) — an unsigned cursor
      is fine precisely because tenant scope will never be sourced from it.

## Known TODOs / Planned Work

Concrete bugs, missing logic, and planned improvements — not yet implemented.

### Backend

- ~~A booking/series blocks itself from its own reschedule slot picker~~ — **done.** `get_available_slots` takes `exclude_booking_id`/`exclude_series_id`, filtered out of both `booking_q` and the `inf_rules` query (the series case mattered more — `thin_schedule_dateless` was subtracting the whole `(weekday, time)` band before any date resolved). The endpoint accepts `exclude_ref`/`exclude_series_ref` as `public_id`s and resolves them to internal PKs, no-op'ing on an unresolvable ref so a virtual occurrence doesn't 404. `BookingPage.tsx` passes them from `location.state` on both reschedule paths. Overlapping the original slot is now allowed; rescheduling to the *identical* slot is rejected by new guards in `_reschedule_booking` (exact `start`/`end`) and `_reschedule_series` (same weekday + time-of-day + tutor, since a series' identity is its pattern, not an instant).
- **Delete-protection gaps across Tutor/Schedule/EventType/Booking** — surfaced while designing the `BookingSeries` lifecycle-field cleanup. `EventType` and `Tutor` turned out to need genuinely different treatment (one's a one-time blueprint a booking is built from, the other's an ongoing identity referenced from many tables), settled as follows.
  **Current (unfixed) behavior, for reference**: nothing here is silently working correctly today, but the *way* each one is broken differs — worth knowing before touching any of it. `Tutor` and `EventType` deletion are both accidentally-blocked-but-ugly: no app-level check exists, so if any referencing `Schedule`/`Booking`/`BookingSeries` row exists, Postgres itself rejects the delete with a raw, unhandled `IntegrityError` (500 crash), not a clean 409 — the delete doesn't go through, but it fails loud and ugly instead of failing cleanly. `Schedule` deletion is the opposite and more dangerous failure mode: `EventTypeAvailability.schedule_id` has `ondelete="CASCADE"`, so deleting a schedule still linked to an `EventType` **succeeds silently** — no crash, no warning, the link just vanishes and that event type quietly loses its availability. So: two things currently fail loud when they shouldn't be allowed to run at all, and one thing currently succeeds silently when it should be blocked outright.
  - **`EventType` deletion design — see Priority Order item 6 above, and CLAUDE.md's "EventType data model" for the full reasoning.** Short version: soft delete only (`status='archived'`), never hard-deleted, because `bookings.event_type_id` is the system's only filter key and must always resolve. Several earlier drafts (RESTRICT-on-active-references, then freely-deletable-with-frozen-`type`-strings) are superseded — kept as a pointer only, so this doesn't drift out of sync again.
  - ~~`Tutor` hard-delete guard only checked `Lesson` rows~~ — done. `DELETE /tutors/{id}`
    (`routers/tutors.py`) now also 409s on any referencing `Booking` or `BookingSeries`, past or
    future — hard delete only succeeds when the tutor has zero bookings of any kind; the only path
    forward otherwise is `is_active=False`.
  - ~~`Tutor.is_active=False` had zero enforcement anywhere~~ — done. `create_booking`,
    `_reschedule_booking`, `_reschedule_series` (`routers/bookings.py`) 400 on an inactive
    `tutor_id`; `get_available_slots` (`routers/available_slots.py`) excludes inactive tutors from
    the query; `extend_single_series` (`tasks.py`) returns cleanly without materializing further
    occurrences once a series' tutor has gone inactive. Already-confirmed/already-materialized
    occurrences are untouched, as designed. No "tutor inactive" indicator added to the admin view
    yet — cosmetic, not tracked as blocking.
  - **`Schedule.tutor_id` still has no delete cascade — now a live, easily-hit gap, not just
    theoretical.** No `ondelete` on the FK, no `cascade=` on `Tutor.schedules`. Now that the
    `Tutor`→`Booking`/`BookingSeries` RESTRICT above is in place, hard-delete's only remaining way
    to fail is a tutor who has `Schedule` rows but zero bookings — a very ordinary state for a
    newly-configured tutor — which still raises a raw unhandled `IntegrityError` (500) today. Not
    fixed this pass; fix is to wire `Schedule.tutor_id` as `ondelete="CASCADE"` now that the
    precondition (zero-booking guarantee) actually holds.
  - ~~`delete_schedule` had no block for a schedule still wired to an `EventType`~~ — done.
    `DELETE /schedules/{id}` (`routers/schedules.py`) now 409s if any `EventTypeAvailability` row
    still references the schedule, same shape as the existing `is_default` guard.
  - ~~Admin permanent-delete endpoint for `BookingSeries` didn't exist~~ — done.
    `DELETE /booking-series/{id}/permanent` (`routers/bookings.py`), `cascade` param, modeled
    directly on `Booking`'s `.../permanent` endpoint — same predecessor-chain confirm flow, walks
    `rescheduled_to`, deletes each series' `Booking` rows and Google Calendar master event. No
    frontend wiring yet (backend-only this pass).
- **`BookingSeries`'s full iCalendar (RFC5545) field mapping — deliberate per-field, not an oversight if something looks missing.** Full design: `.claude/plans/booking-series-recurrence-fields-and-event-type-denormalization.md`.
  - Kept (`created`/`last_modified`/`dtstart`/`dtend`/`until`/`status` already implemented): `freq` (hardcoded `'WEEKLY'`), `interval` (hardcoded `1`), `count` (nullable, from `EventType.recur_weeks` — needed to correctly recompute `until` on reschedule). Not yet implemented.
  - Comment-only, not real columns: `byday`, `wkst` — redundant with `dtstart.weekday()` today, would need to be array-typed to ever matter.
  - Skipped: `dtstamp` (generation-time fact, not persisted), `duration` (redundant with the `duration` property), `summary`/`description` (resolved live through `event_type_id`, not stored on `BookingSeries`), `location` (no concept exists yet).
  - **This field-mapping pass only covered `BookingSeries` — `Booking` (the occurrence level) never got the same treatment and is missing `created`/`last_modified` entirely.** Confirmed: `Booking` has no `created`/`last_modified` columns at all today, unlike `BookingSeries` which has both (`onupdate=func.now()`, already verified working correctly). `Booking.start`/`.end` intentionally keep their own names rather than becoming `dtstart`/`dtend` — that's not an oversight, `dtstart`/`dtend` specifically means "naive local time-of-day describing a recurring pattern" (see `ScheduleDay`), whereas `Booking.start`/`.end` are absolute UTC datetimes for one concrete occurrence, a genuinely different concept. Fix: add `created`/`last_modified` to `Booking`, same pattern as `BookingSeries`.
- **`BookingSeries.status` uses `NULL` to mean "active" — should be an explicit value instead, matching `Booking.status`/`BookingRequest.status`.** Both of those are `nullable=False` with an explicit default (`"confirmed"`/`"pending"`); only `BookingSeries.status` (`nullable=True`, `null` = active) does it differently, for no clear reason other than history. This isn't just a style inconsistency — it already caused a real bug: `active_series_filter`'s original `status.notin_(['cancelled', 'rescheduled'])` silently evaluated to SQL `NULL` (not `TRUE`) for a fresh series, excluding it, until explicitly special-cased with `OR status IS NULL`. Fix: `nullable=False`, explicit default (`"confirmed"` or `"active"`, matching whichever label reads better next to `'cancelled'`/`'rescheduled'`). Needs a real migration (backfill existing `NULL` rows to the explicit value before flipping `nullable=False`) and updating every `status == None`/`status IS NULL` check in `booking_utils.py`/`routers/bookings.py` to compare against the explicit string instead.
  - **Whatever the "active" label ends up being (`"confirmed"`/`"active"`/etc), annotate it clearly in the model that this value alone does NOT mean the series is currently ongoing** — it only means the series hasn't been explicitly cancelled or rescheduled. Whether it's *actually* ongoing right now is a separate question answered by comparing `until` against the current date. `is_series_active`/`is_active` is the union of both facts: `status` is `"confirmed"` (not cancelled/rescheduled) AND `until` is null or in the future → active/ongoing; `status` is `"confirmed"` AND `until` is in the past → naturally finished. The column name and a comment should make this two-part relationship obvious to whoever next reads the model, not just to whoever remembers this conversation.
- ~~`_ensure_occurrence`/`extend_single_series` don't check `until`/`status` before materializing an occurrence~~ — done. `_ensure_occurrence` now checks `status`/`until` itself before materializing (closes the gap for every caller, not just the ones that pre-check). `extend_single_series` also checks `is_series_active` and returns cleanly (no exception) if the series became inactive since being enqueued — otherwise Procrastinate would see a failed job and retry it forever against a permanent state.
- **Policy edits need to backfill already-materialized future occurrences — lower priority.** Policy is frozen onto each `Booking`/`BookingSeries` at creation, so editing an `EventType`'s policy only reaches *new* bookings. Existing future rows keep the old frozen values, so an admin tightening a cancel window would silently miss all of them. The backfill has to cover **three** targets, and missing the third is the subtle one:
  1. **Standalone future `Booking` rows** — straightforward `UPDATE ... WHERE start >= now()`.
  2. **Finite series** — all occurrences are generated upfront at creation, so every future `Booking` row already exists and needs updating. Bounded, one pass.
  3. **Infinite series — must update the `BookingSeries` row itself, not just its materialized `Booking`s.** Only a sparse rolling window is materialized; `extend_single_series` (`tasks.py`) → `_ensure_occurrence` (`booking_utils.py`) generates the rest later, copying fields **off the series row**. So if the backfill only touches `Booking`s, the series silently regresses to the old policy on the very next occurrence Procrastinate generates, forever. Update the series row *and* its existing future occurrences.
  In all three cases: future rows only — never past ones, whose terms are settled and shouldn't be rewritten. Worth pairing with a notification to affected series holders that their cancel/reschedule terms changed going forward. Deliberately *not* solved by making policy a live join — the freeze is correct (see CLAUDE.md's "EventType data model"); this is an explicit, bounded, auditable backfill instead.
- **Pending `BookingRequest` approval has no re-validation at approve time** — `approve_pending_request` (`routers/bookings.py`) sets `status='approved'` and calls straight into `_cancel_booking`/`_reschedule_booking`/`_cancel_series`/`_reschedule_series` with no re-check that: (a) the underlying booking/series is still in a valid state (`Booking.status` still `confirmed` / series still active per `is_series_active` — could have changed via another path while the request sat pending), (b) the original booking's start time hasn't already passed (the same `minutes_until <= 0` floor the direct-action routes enforce via `get_cancel_action`/`get_reschedule_action` is never re-run here), or (c) for reschedule requests, that `requested_start` itself hasn't already passed into the past by approval time. No expiry mechanism exists either — `BookingRequest.created_at` is set but nothing reads it, no cron denies stale requests. Fix: re-run the floor check (and the active-series check) inside `approve_pending_request` before invoking the saga; reject/require-reconfirmation if the original event or requested new time is no longer in the future; consider a UI-visible "stale" indicator for pending requests whose window has passed.
- **`only_show_first_slot`** — stored and returned by EventType but ignored in `get_available_slots`. Fix: after generating full slot set, group by `(tutor_id, date)` and keep only the earliest per group.
- **`limit_future_bookings_days`** — stored but not applied. Fix: cap `time_max = min(time_max, now + timedelta(days=N))` before slot generation.
- **`buffer_minutes`** — stored but not applied. Fix: expand busy overlap check to `[slot_start - buffer, slot_end + buffer]`.
- **`limit_per_day / per_week / per_month`** — stored but not applied in `get_available_slots`. Fix: count existing bookings per tutor per period and exclude saturated slots.
- **`limit_per_booker`** — stored but not enforced in `create_booking`. Fix: count existing bookings for `(event_type_id, student_email)` and reject if at limit.
- **Slug field on EventType** — add `slug: str` (unique, auto-generated from name, user-editable). New endpoint `GET /event_types/slug/{slug}`. Update BookingPage route from `/book/:eventTypeId` → `/book/:slug`. Requires DB migration.
- **Surface the manage link on BookingPage's "done" step** — `ManageOccurrence.tsx`/`ManageSeries.tsx` and their `public_id`-keyed endpoints already exist and work (see Booking system above), but the confirmation ("done") screen on `BookingPage.tsx` doesn't yet show/link the customer to their own manage-occurrence/manage-series page after booking.
- **Google Calendar attendees** — IMPORTANT: current "Add to calendar" buttons on BookingPage create a standalone unlinked copy in the student's calendar. Correct fix: add `attendees: [{"email": student_email or parent_email}]` to the event body in `create_booking`, with `sendUpdates="all"` on all API calls. Google then auto-invites, auto-updates, and auto-cancels on the student's end. Remove the manual "Add to calendar" buttons once this is live.
- **Calendar abstraction layer** — Google Calendar is currently hardcoded throughout. Goal: extract all Google API calls into `GoogleCalendarService(CalendarService)` with an abstract base class (`create_event`, `update_event`, `delete_event`, `get_freebusy`, `move_event`). Booking router injects the service and never references Google directly. Also rename `Booking.google_event_id` → `external_event_id` (requires DB migration).
- **Per-occurrence `google_event_id` currently a copy-of-series marker, not a real instance id** — for a normal (never individually rescheduled) occurrence, `Booking.google_event_id` is just set equal to `series.google_event_id` (the master RRULE event's id). `_cancel_booking`/`_reschedule_booking`/`_reschedule_series` all compare `booking.google_event_id != series.google_event_id` to detect whether an occurrence is an "exception" (individually modified before) vs. normal, and for normal occurrences do a live `events().instances(timeMin=, timeMax=)` lookup to find the real per-instance id before acting. Google Calendar recurring events have a documented deterministic per-instance id format (`{recurringEventId}_{yyyyMMdd'T'HHmmss'Z'}`, the original UTC start time) — same mechanism as this app's own `public_id` composite refs. Idea: store the real (computed or fetched) instance id on `Booking.google_event_id` at materialization time instead of the series copy, which would let the `is_exception` branching and live `events().instances()` lookup in all three action functions be deleted entirely — always just act on whatever's stored. Deliberately not done yet: needs verifying the documented format against this app's actual Calendar behavior first (this touches live calendar-mutating code, not just reads), and eagerly fetching at materialization time would mean *every* occurrence pays an API call instead of only the ones someone actually reschedules/cancels — worth checking that computing the id locally (zero extra calls) is reliable before switching.
- **GET /tutors `?ids=`** — filter endpoint to fetch only specific tutor IDs. Avoids loading all tutors on BookingPage.
- ~~`GET /booking-series` needs `tutor_ids`/`event_type_ids` filter support~~ — done. Both endpoints now share `apply_scope_filters` (`booking_utils.py`), see CLAUDE.md's "Filtering / facets" note.
- ~~Student name filter on the Schedule/Bookings view~~ — done, via a different mechanism than originally sketched here: exact `(first_name, last_name)` pair matching against `Booking.student_first`/`student_last` (not a `Student.id`-keyed filter — that FK is still unreliable, see the identity-split note below), self-excluding facets same as Tutors/Event-types. See CLAUDE.md's "Filtering / facets" note.
- **Background jobs** — task runner is Procrastinate (`tasks.py`/`worker.py`), not APScheduler — already chosen and in use. Two of four originally-planned jobs are implemented: (1) `draft_lessons` (Sunday 6am cron) — creates draft `Lesson` rows from past confirmed bookings with no lesson yet (`Lesson.booking_id` FK set, checks `~Booking.lesson.has()` for idempotency); has a `# TODO: review before enabling` comment in the code itself — not yet trusted to run for real. (2) `extend_all_series`/`extend_single_series` (daily 2am cron) — the indefinite-series materialization job described above. **Still not implemented**: (3) daily 24hr reminder emails; (4) confirmation email with manage link on booking create.
- **`Lesson.booking_id`** — nullable FK → `bookings.id`. Set by Sunday scheduler. `Booking.lesson` back-relationship (`uselist=False`). Manual lessons have `booking_id=None`. Column exists in models.py — not yet used.
- **Identity model needs a `Contact`/`Student` split — `Booking.student_id` is currently unreliable, not just nullable** — `Booking.student_id` (nullable FK → `students.id`, `Booking.student_record` relationship) is null for essentially every real booking today: `BookingCreate` accepts it as an optional input, but `BookingPage.tsx` — the only place `POST /bookings/` is actually called from — never sends it. The public booking flow is just a plain contact form; there's no "select which existing Student you are" step. So `Booking.student_first`/`student_last`/`student_email`/`student_phone` (denormalized text, duplicated on every `Booking`/`BookingSeries` row) are currently the *only* reliable way to know who a booking is for — any feature wanting to filter/group by student (e.g. a Bookings-page student filter, analogous to the existing tutor/event-type filters) has to text-match against those instead of a clean `.in_(ids)`.

  Planned fix — a 2-layer identity split, `Contact` + `Student`/`Tutor` (a 3-layer `Contact → Student → User` split, with auth broken out separately, was considered and rejected — see reasoning below):
  - New `Contact` table: barebones identity only (`first_name`, `last_name`, `email`, `phone`). Look-up-or-create (dedupe by email/phone) for *every* booker, guest or enrolled, at booking-creation time. This is what `Booking`/`BookingSeries` point at.
  - `Booking.student_id` → renamed `Booking.contact_id`, made **required** (`NOT NULL`) — every booking always resolves to a real identity regardless of enrollment or auth status. Needs a backfill migration first: create a `Contact` row for every existing booking's denormalized name/email/phone, point `contact_id` at it, *then* add the constraint.
  - Once `contact_id` is reliable, `student_first`/`student_last`/`student_email`/`student_phone` on `Booking`/`BookingSeries` become redundant (read name/email/phone off the linked `Contact` instead) — not removed as part of this note, a later cleanup once the FK is proven reliable.
  - `Student` keeps its existing enrollment fields (`rate`, `grade`, `birthday`, `start_date`, `is_active`), gains a `contact_id` FK, and gains whatever auth ends up needing (password hash, etc.) once auth is built — auth and enrollment merge into one table rather than a separate `User` table. Same idea for `Tutor`: gets its own auth fields directly: no shared `User` table between the two roles, since student and tutor portals are different enough (different permissions) that sharing one wasn't buying anything.
  - No `is_guest` flag needed anywhere — "is this Contact an enrolled student" is a derived fact (does a `Student` row exist with this `contact_id`), not a stored one.
  - Why not 3 layers: that pattern (`Contact` → `Student` → separate `User`) only earns its keep when "has an account" and "has enrolled" are genuinely independent, *repeatable* states — e.g. Coursera, one account with many independent per-course enrollments. TMS's `Student` is a single state per person, not a repeatable one-to-many relationship, so merging auth into `Student` loses nothing.
  - Sunday scheduler (`draft_lessons`) currently uses `Booking.student_id` directly if set, falls back to email/name lookup if null — once `contact_id` is required, that fallback branch becomes dead code.
- **Known limitation: one global `Settings.business_timezone` assumes a single-location business — not addressed now, purely a noted consideration.** Surfaced while designing the `BookingSeries` lifecycle-field cleanup, discussing why `dtstart`/`dtend` don't need a per-row timezone: today every series/schedule in the app shares one canonical zone, which is correct for a single-tenant, single-location deployment. It stops being correct for a franchise-style business — one tenant, multiple physical branches, each in a different timezone (e.g. an NYC location and an LA location under the same account). That's a different axis of granularity than multi-tenancy (different *businesses* each still having one zone) — this is one business needing *several* zones at once. If it's ever built: timezone would need to move from `Settings` down to `Tutor` (or a future `Location` entity tutors belong to), and every `Booking`/`BookingSeries` would need its zone denormalized at creation time from whichever tutor produced it — same freeze-at-creation pattern as the planned `EventType` terms denormalization above. Deliberately not designed further than this — no multi-location feature is currently planned or scoped.
- **Planned: drop `Booking.timezone` (the booker's own captured zone) from a stored column to a schema-input-only field — decided, not conditional anymore.** It's only ever consumed once today, as a write-time UTC-conversion input (`BookingCreate`/`BookingReschedule`'s `model_validator` uses it to correctly interpret the raw start/end the frontend submits) — nothing currently reads it back off an existing `Booking` row. CLAUDE.md's "future email use" framing for it is superseded: confirmation/reminder emails (Known TODOs, Background jobs) will render in `Settings.business_timezone` uniformly, not each booker's own captured zone, so there's no future reader for the stored value either. Drop it from `model.py` (accept as `BookingCreate`/`BookingReschedule` schema input only, use once, discard — don't persist). **One caveat, unlikely but worth naming**: if multi-location support (see the single-`business_timezone` limitation noted above) ever actually gets built, a stored per-booking zone becomes relevant again for a different reason — knowing which zone/location a booking belongs to. That's not currently planned, so it doesn't block this removal — just re-check this note first if multi-location ever gets picked up.
- **Payment/billing tracking — separate from the identity work above, a later concern** — planned to support both hourly and subscription payment options. Likely `Student.payment_type: 'hourly' | 'subscription'` (or a dedicated `Subscription` table if plans need their own lifecycle — billing cycle, price, start/end dates — independent of the `Student` row), plus per-lesson payment status (e.g. `Lesson.paid` or a `payment_status` field on the existing `Lesson` model, which already computes `fee`/`tutor_payout` per session). Orthogonal to the `Contact`/`Student` identity split — a lesson or subscription's paid status is a fact about a transaction, not about who the person is.
- **Pagination is "regenerate more and slice," not a real cursor** — full analysis (algorithmic cost, the resource-exhaustion gap it also causes, and the chosen fix) now lives in CLAUDE.md's Pagination section and `.claude/plans/cursor-pagination-and-endpoint-split.md` — see "Priority Order" above, item 2.

### Frontend
- **Realtime validation in EventTypePage** — each field still gates re-validation behind a `touched.<field>` check (only calls `setErrors(validate(form))` if the field was already touched); call it unconditionally on change/blur instead for immediate feedback on first interaction too.
- **BookingPage custom duration** — if `eventType.allow_custom_duration`, show duration slider/NumberInput on contact step (between min and max). `selectedDuration` state, defaults to `min_duration_minutes`. `end = new Date(slot.start + selectedDuration * 60000)`.
- **Slug field in EventTypePage** — text input auto-populated from name (slugified), editable, shown in the `details` tab once slug backend column exists.
- **EventTypes/Availability pattern divergence** — `EventTypes.tsx` moved to a separate routed page (`EventTypePage.tsx`, tab-based) while `Availability.tsx` still uses the older inline card-swap-to-form pattern. Worth a deliberate decision on whether to bring Availability in line with the routed-page pattern, or leave them intentionally different — currently just an artifact of when each was last touched, not a designed choice.

## Future Features to Evaluate

Observed from Cal.com. ⭐ = high priority for TMS.

- **"Changes" tab (low priority)** — repurpose/extend the Requests tab into a general changes-for-a-time-period view: pending requests as one category, plus cancellations and reschedules. Fully queryable off data that already exists, no new audit table or timestamp needed: for a window `[time_min, time_max]`, cancelled items show under the week of their own `start`/`dtstart` (`status='cancelled' AND start BETWEEN ...`); rescheduled items show under *both* the old row's week (`status='rescheduled' AND start BETWEEN ...`, "moved away," pointing forward via `rescheduled_to`) and the new row's week (`rescheduled_from IS NOT NULL AND start BETWEEN ...`, "arrived here," pointing back via `rescheduled_from`) — same item appears twice only if the move actually crossed a week boundary. Smaller than it first looks — new query filters against existing columns, not a new feature's worth of schema.
- ⭐ **Admin calendar view** — use `react-big-calendar` (MIT, fully free, unstyled — plays well with Tailwind) to render bookings as Google Calendar-style events on the admin bookings page. Month/week/day views, click event → detail panel with actions. Also overlay confirmed bookings as greyed blocks on the public BookingPage slot picker. Add after Milestone 1 recurring materialization is stable. Preferred over FullCalendar which has opinionated styles that fight Tailwind.

- ⭐ **Booking questions / custom fields** — per-event-type dynamic form fields (text, phone, notes, etc.) shown to the booker at booking time; each field configurable as required/optional/hidden
- ⭐ **Requires confirmation** — manual approval step before a booking is confirmed; booker sees "pending" state until host accepts; requires `status` column on Booking
- ⭐ **Email notifications** — automated confirmation, reminder, and cancellation emails to booker and tutor; likely via a Workflows/queue system
- **Require cancellation reason** — prompt booker for a reason when cancelling; store on the booking record
- **Redirect on booking** — send booker to a custom URL after successful booking (e.g. payment page, onboarding form)
- **Optimized slots** — prefer slot arrangements that consolidate bookings and minimize tutor gaps
- **Lock timezone on booking page** — force a specific timezone (useful for in-person events)
- **Offer seats / group bookings** — allow N bookings per slot for group lessons; `max_attendees` column on EventType
- **Event type color** — color tag for visual differentiation in the admin card list
- **Limit total booking duration** — cap total booked hours per period (e.g. max 10 hrs/month for this event type)
- **Webhooks** — HTTP callbacks to external systems on booking create/update/cancel
- **Booker email verification** — require email confirmation before booking is accepted
- **Private links** — per-booker booking URLs with configurable expiry and usage limits
- **Allow rescheduling past events** — let bookers reschedule events that have already passed (admins already can — see the Decisions section below)
- **Recurring schedule tab** — dedicated UI for setting up recurring booking patterns (separate from the `recurring` boolean)

## Decisions — Considered and Rejected

Things that were designed, thought through, and deliberately *not* built. Recorded so they don't get
re-proposed later without the reasoning that killed them. Each notes what would change the answer.

- **Standalone `Policy` entity (own table, `EventType.policy_id` FK, optional admin route) — designed twice, dropped twice. Final: no table.**
  - **First rejection**: it was introduced to make `EventType` freely deletable (policy couldn't live on a row that might vanish). That premise turned out false — reschedule needs the type for slot computation, so `EventType` is soft-deleted and always resolves. No tension left for a table to relieve.
  - **Briefly revived** for the one thing that genuinely justified it independently: reusing a policy across event types (edit "24h notice" once, applies to five).
  - **Dropped again** once the `Settings`-default model landed, which delivers that same edit-once-applies-everywhere without a table, a `/policies` route, or copy-vs-share and fork semantics. A business-level default with per-type overrides is also how practice-management software (Jane, SimplePractice, Acuity) and hospitality actually model it — Cal.com/Calendly keep it per-event-type only, but their policy support is thin enough not to be useful precedent.
  - **What would change the answer**: policies that need to be *named, listed, and independently managed* as first-class objects — many distinct policies applied in overlapping combinations, more than a single default plus scattered exceptions can express.
- **Procrastinate cleanup job to delete orphaned `CalendarRules` rows once all their bookings are in the past — rejected.** The idea: `CalendarRules` (buffer/limits/interval, extracted off `EventType` into its own table, with `Booking`/`BookingSeries` holding a `calendar_rules_id` FK captured at creation) deliberately survives its `EventType` being deleted, frozen and uneditable, so already-created future bookings can still resolve the rules they need to reschedule themselves. Proposal was a periodic job to reclaim those rows once every booking pointing at one had passed.
  - **Why rejected — it's function-breaking.** Admins can already reschedule *past* bookings today: `reschedule_booking` (`routers/bookings.py`) checks only `status != "confirmed"`, never whether the start is in the past, and that's deliberate (CLAUDE.md: the past-time notice floor is a booker-facing rule that intentionally doesn't apply to admin). Deleting the rules would silently remove a capability that currently exists. Note this is distinct from the "Allow rescheduling past events" future-feature above, which is about *bookers* — admins are already unrestricted.
  - **Why the storage argument doesn't hold.** `CalendarRules` row count is bounded by *how many event types have ever been deleted* — not by booking volume or traffic. At ~100 bytes/row, a thousand deleted event types over a decade is ~100KB. That's rounding error, not a problem to solve.
  - **General principle this reflects**: cleanup jobs are for data that grows proportionally to *activity* (logs, sessions, webhook deliveries, event streams) — that genuinely runs away. Config/reference data is kept indefinitely, because something still points at it and it doesn't scale with traffic. Same reason Stripe retains old prices so historical invoices resolve, and Shopify keeps deleted products so past orders still render.
  - **Time-based retention (e.g. "older than 3 years") was also considered and rejected** — it only narrows the window in which admin reschedule breaks, without changing the trade: still giving up real functionality for storage that was never scarce.
  - **What would change the answer**: if `CalendarRules` ever starts being written per-booking rather than per-event-type (making its growth traffic-proportional rather than bounded), or if admin reschedule of past bookings is deliberately removed as a capability. Neither is currently true or planned.
  - **The one safe variant, if a cleanup is ever wanted anyway**: delete a `CalendarRules` row only when *zero* bookings reference it at all — i.e. the bookings themselves were hard-deleted via the permanent-delete endpoints. That's the standard orphan-cleanup pattern and can't break anything by construction, since nothing points at the row by definition. In practice it will rarely fire, which is the correct outcome.
