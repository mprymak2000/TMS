---
name: tms-roadmap
description: TMS's backlog — known bugs, missing logic, planned improvements, and features under evaluation (moved out of CLAUDE.md's always-loaded content). Load when discussing what to build next, prioritizing work, checking whether something is a known gap, or planning a new feature.
---

## Priority Order — Path to Single-Tenant Launch

Decided sequence for the current push toward going live (single-tenant; multi-tenant is later,
not a launch blocker). Each open item has its own design doc in `.claude/plans/`:

**Denormalization boundary, stated once here since items 6/8 below both depend on it**: `Tutor`,
`Contact`/`Student`, and `Policy` are always live FKs on `Booking`/`BookingSeries` — never copied
field values. Editing any one of them (a tutor's name, a contact's phone number, a policy's notice
window) is instantly reflected on every booking/series that references it, past and future alike.
`EventType` is the one exception, and only for `name`/`description` — those get frozen (copied) at
creation, because `EventType` is a construction-time blueprint, not an ongoing identity nothing
else needs to keep resolving. This is why `EventType` can become freely deletable (item 6) while
`Tutor`/`Contact`/`Policy` all need delete guards instead (items 5/6/8) — the two are opposite
resolutions of the same question: does anything still need this row to keep existing?

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
6. **Cancellation policy separation + finish the `EventType` blueprint denormalization — two
   changes together fully eliminate any need for an `EventType` delete guard, neither one alone
   does:**
   - Break `cancel_mode`/`cancel_notice_minutes`/`reschedule_mode`/`reschedule_notice_minutes` out
     of `EventType` into their own `Policy` entity. Admin creates a policy first; `EventType`
     selects one at creation, or none. `Booking`/`BookingSeries` get their own `policy_id` FK, set
     at creation — `get_cancel_action`/`get_reschedule_action` read the policy straight off the
     booking, no `EventType` join at all (this is the part that makes the *policy* live-editable
     without breaking existing bookings, same reasoning as before, just resolved through a
     dedicated FK instead of `EventType`).
   - **Still freeze `name`/`description` onto `Booking`/`BookingSeries` at creation, exactly as
     Pass 2 §3 originally designed — this part of §3 was correct and is not superseded.** Checked
     against the actual code: `_reschedule_booking` (`routers/bookings.py:476-477`) currently reads
     `db_booking.event_type.name`/`.description` live to build the new calendar event — if
     `EventType` is ever deleted and that FK goes `NULL`, this crashes unless the booking has its
     own frozen copy to read instead. Once it does, this is the last live dependency gone too.
   - With both pieces done — policy resolved via its own FK, `name`/`description` frozen — no
     existing `Booking`/`BookingSeries` (active or historical) depends on `EventType` continuing to
     exist for anything. `EventType` becomes freely, unconditionally deletable — no RESTRICT, no
     guard, no active-vs-historical distinction needed. `event_type_id` still goes nullable with
     `ondelete="SET NULL"`, purely for referential cleanliness. No separate "EventType delete
     guard" step exists in this plan — a guard would only be needed if one of the two pieces above
     were skipped. No plan doc yet — needs its own design pass before implementing.
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
- **Delete-protection gaps across Tutor/Schedule/EventType/Booking** — surfaced while designing the `BookingSeries` lifecycle-field cleanup. `EventType` and `Tutor` turned out to need genuinely different treatment (one's a one-time blueprint a booking is built from, the other's an ongoing identity referenced from many tables), settled as follows.
  **Current (unfixed) behavior, for reference**: nothing here is silently working correctly today, but the *way* each one is broken differs — worth knowing before touching any of it. `Tutor` and `EventType` deletion are both accidentally-blocked-but-ugly: no app-level check exists, so if any referencing `Schedule`/`Booking`/`BookingSeries` row exists, Postgres itself rejects the delete with a raw, unhandled `IntegrityError` (500 crash), not a clean 409 — the delete doesn't go through, but it fails loud and ugly instead of failing cleanly. `Schedule` deletion is the opposite and more dangerous failure mode: `EventTypeAvailability.schedule_id` has `ondelete="CASCADE"`, so deleting a schedule still linked to an `EventType` **succeeds silently** — no crash, no warning, the link just vanishes and that event type quietly loses its availability. So: two things currently fail loud when they shouldn't be allowed to run at all, and one thing currently succeeds silently when it should be blocked outright.
  - **`EventType` is a blueprint for display text, but its cancellation/reschedule policy needs to stay live and centrally editable — these two facts pull in different directions, and the resolution splits them.** Full history, since this went through two rejected drafts: initially concluded "denormalize everything (`name`, `description`, `cancel_mode`, `cancel_notice_minutes`, `reschedule_mode`, `reschedule_notice_minutes`) onto `Booking`/`BookingSeries` at creation, then let `EventType` be hard-deleted freely, no guard at all" — construction-blueprint reasoning, same freeze-the-terms pattern as `Lesson.fee`. That broke on a real case: a policy change (e.g. 24hr notice → 48hr notice) is a rule about *future* behavior, not a fact about a past transaction — if frozen per-booking at creation, an admin's policy edit would never reach any already-materialized future occurrence of any existing series, with no way to fix that short of hand-editing every row. **Landed design**: `cancel_mode`/`cancel_notice_minutes`/`reschedule_mode`/`reschedule_notice_minutes` are never denormalized at all — `get_cancel_action`/`get_reschedule_action` (`routers/bookings.py`) keep reading them off a live `EventType` join, unchanged from today, so a policy edit is instantly live everywhere it applies. This is fully safe specifically because these fields have zero value on a *past* booking anyway (nobody needs to know what the notice window *was* once a booking is done/cancelled/finished) — unlike `Lesson.fee`/`count`, there's no historical fact being protected by freezing them, so not freezing them costs nothing. Only `name`/`description` still get denormalized onto both `Booking` and `BookingSeries` at creation — pure display text, safe to freeze, and what lets a historical booking under an eventually-deleted `EventType` still render correctly. `event_type_id` becomes nullable with `ondelete="SET NULL"` regardless — needed for the historical-reference case. **`EventType` deletion goes back to a real RESTRICT (409)**, not "freely deletable": blocked while any *active* `Booking` (`status='confirmed' AND start >= now()`) or active `BookingSeries` (`until` null/future, `status` not `'cancelled'`/`'rescheduled'`) references it; freely deletable once only historical references remain, regardless of volume. Same hard-block shape as the `Schedule`→`EventTypeAvailability` gap below — both exist because something still actively depends on the source, not because of what it did in the past. `delete_event_type` (`routers/event_types.py:81-88`) currently has no guard at all — today, deleting an `EventType` with existing bookings raises a raw unhandled `IntegrityError` (500) rather than either a clean 409 or a correct RESTRICT scoped to "active only." Full reasoning and backend sketch: `.claude/plans/booking-series-recurrence-fields-and-event-type-denormalization.md`.
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
  - Skipped: `dtstamp` (generation-time fact, not persisted), `duration` (redundant with the `duration` property), `summary`/`description` (handled via `EventType` denormalization instead, not `BookingSeries` fields), `location` (no concept exists yet).
- ~~`_ensure_occurrence`/`extend_single_series` don't check `until`/`status` before materializing an occurrence~~ — done. `_ensure_occurrence` now checks `status`/`until` itself before materializing (closes the gap for every caller, not just the ones that pre-check). `extend_single_series` also checks `is_series_active` and returns cleanly (no exception) if the series became inactive since being enqueued — otherwise Procrastinate would see a failed job and retry it forever against a permanent state.
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
- **Allow rescheduling past events** — let bookers reschedule events that have already passed
- **Recurring schedule tab** — dedicated UI for setting up recurring booking patterns (separate from the `recurring` boolean)
