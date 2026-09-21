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
referencing it, past and future. `BookingLink` is a **factory**, and splits
down the middle: its *calendar rules* (duration, buffers, caps, lead time, horizon, tutor roster) are
read **live** on every slot computation — including a customer rescheduling an existing booking —
because they're nobody's promise, they govern the volume and shape of slots you'll offer *now*;
while its *wiring* (the `booking_type_id` kind pointer, cancel/reschedule policy, contact fields, intake
answers) is **copied once at creation and never propagated again**. The test for which bucket a field
belongs in: is it a promise made to a booker, or an operational setting?

Links are **archived only — no hard delete at any child count**, so `booking_link_id` on
`Booking`/`BookingSeries` is NOT NULL and never dangles (nothing automatic changes it; an admin may
reassign it to rescue a booking stranded on an archived link). That permanence is what makes it
safe to group on. Bookings therefore have **two** independent facet dimensions: *source*
(`booking_link_id` — what generated this; renaming a link relabels the group, never splits it) and
*kind* (`booking_type_id` — an FK into `booking_types`, frozen at creation so a link changing its type
never moves existing rows, but resolving live so renaming the type relabels every row pointing at it).
Full reasoning:
CLAUDE.md's "BookingLink data model", and "Two entry points, two rule regimes" directly above it —
the latter is what makes soft delete safe, since admin moves are direct `dtstart`/`dtend` edits
rather than trips through the slot picker, so a retired link's rules are read by nothing.

1. ~~Finish the filters/facets work — move the "keep a selected filter visible" logic from
   frontend to backend.~~ — done. Design: `.claude/plans/done-facets-selection-kept-backend-move.md`.
2. ~~Cursor-based pagination cleanup~~ — done. Design: `.claude/plans/done-cursor-pagination-and-endpoint-split.md`.
3. ~~Split `Bookings.tsx`'s three tabs into separate routed components~~ — done
   (`BookingsLayout.tsx` + `ScheduleTab`/`RecurringTab`/`RequestsTab`). Design:
   `.claude/plans/done-bookings-tabs-to-subroutes.md`.
4. ~~`BookingSeries` lifecycle field collapse + immutable reschedule (Pass 1 of the field
   cleanup)~~ — done (`dtstart`/`dtend`/`until`/`status`/`created`/`last_modified`, reschedule now
   inserts a new row instead of mutating in place). Design: `.claude/plans/done-booking-series-end-date-cleanup.md`
   — that doc's own "Status: not implemented" header is stale, ignore it; the work landed and is tested.
5. ~~**Deletion safety, part 1**~~ — done, all four pieces:
   - `Schedule`→`BookingLinkAvailability` delete guard (`routers/schedules.py`) — 409 if any
     `BookingLinkAvailability` row still references the schedule.
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
6. ~~**`EventType` → `BookingLink` rework — three passes.**~~ — **all three done.** Full design: CLAUDE.md's "BookingLink data model" — source of truth, read it first, along with "Two entry points, two rule regimes" above it. A link is a *factory*: its **calendar rules are live** (read through `booking_link_id` on every slot computation, reschedules included — they're nobody's promise, they govern the volume and shape of slots you'll offer *now*), while its **wiring is frozen** (copied at creation, never propagates). Because rules are live, the link must always resolve → archive is the only delete. Each pass leaves the system green; each needs its own `docker compose down -v && up -d` (no Alembic).
   - ~~**6a — Rename, archive, slug, reassignment.**~~ — **done.** `.claude/plans/done-booking-link-pass-1-rename-and-archive.md`. `EventType`→`BookingLink`, `BookingLinkAvailability`→`BookingLinkAvailability`, `booking_type_id`→`booking_link_id`, routers and pages renamed, route becomes `/book/:slug`. Plus `status` (`active`/`archived`, enum not boolean so `paused` slots in later) + `archived_at`; **no hard delete at any child count, no restore**; archived links read-only (403 on update). `slug` unique among **active** links only (partial index), **released on archive**, 400 on collision. **Four enforcement points, none in the slot generator**: public lookup 404s, customer reschedule 404s, `create_booking` requires active (admins included), `extend_single_series` **stops generating**. `get_available_slots` gets no status check at all — an earlier draft added one keyed on client-supplied `exclude_ref`; it was unnecessary and fail-open. Reassignment ships here because this pass introduces the thing that needs repairing. Carries most of the test migration; fixes a live 500 (the old `delete_event_type` had no guard).
   - ~~**6b — booking type label + second facet.**~~ — **done.** `.claude/plans/done-booking-link-pass-2-booking-type-and-facets.md`. A `booking_types` table (`label` globally unique, `color`) plus a nullable `booking_type_id` FK on `BookingLink`, `Booking`, and `BookingSeries` — `ON DELETE SET NULL` everywhere, indexed on the two facet-key columns. Stamped at creation off the **link**; series occurrences copy off the **series** in `_ensure_occurrence`; reschedule carries it forward. **Freeze the pointer, never the row** — a link switching type reaches future generations only, while renaming a type row relabels every booking pointing at it, past included. That propagation is the whole reason it's a table: a frozen string can't follow a rename, so a typo fix would split the bucket instead of correcting it (the same fragmentation argument that decided archive-vs-delete for links). **The picker is the CRUD** — one combobox reused in the link editor and on booking/series rows, with inline rename/delete and a create-at-the-bottom, so types never need a page of their own. **Additive, not a migration**: existing FK-based facet machinery is untouched, a second dimension goes in beside it. Cost: facets run one query per dimension, so this is a fourth query per request.
   - ~~**6c — Policy.**~~ — **done.** `.claude/plans/done-booking-link-pass-3-policy.md`. Policy frozen onto **both** `Booking` and `BookingSeries` at creation, copied off the **series** in `_ensure_occurrence` and off the **old row** on both reschedule paths; `get_cancel_action`/`get_reschedule_action` read the row's own columns. **No backfill.** Two independent levels, because ending an engagement isn't the same promise as dropping one session: the occurrence four (`cancel_mode`/`cancel_notice_minutes`/`reschedule_mode`/`reschedule_notice_minutes`, full vocabulary incl. window modes) and the series two (`series_cancel_mode`/`series_reschedule_mode`, `blocked`/`auto`/`request`, **no notice window** — there's no instant to measure against when ending a six-month arrangement). `not_allowed` was renamed `blocked` so mode and verdict share one vocabulary. **No sentinel `NULL`** — every mode column is `NOT NULL` with `server_default='auto'`. The verdict is a `@computed_field` on `BookingResponse` rather than a model property, which is what puts virtual occurrences on the same path as materialized ones. Shipped with a full policy editor UI (`PolicyModeField`, `PolicyModal`, the Links tab, per-row modals) — the plan's "frontend: nothing required" was wrong. **No `Policy` table** — see Decisions. The business-wide default on `Settings` that the original design bundled here was split out as a separate future feature.
   - **Ordering:** 6a first, everything touches renamed entities. 6b and 6c independent of each other.
   - **Superseded, do not implement:** the four-bucket model (Identity/Calendar rules/Policy/Client info); an `event_type_calendar_rules` table; archive-and-relaunch **forking** with `predecessor_id`; a standalone `Policy` table; any policy or identity **backfill**; **hard delete with `ON DELETE SET NULL`** and a nullable `booking_link_id`; the kind **replacing** the FK as the sole facet key; a **frozen `booking_type` string column** on bookings; an **immutable/append-only** `booking_types` table where a rename inserts a new row; a separate human "title"/nickname distinct from the slug; and a slug that is unique *forever* / burned on archive. Three are subtle.
     (1) **Rename fragmentation decides three separate questions the same way.** A live FK follows a rename by construction — one bucket, relabelled. A frozen string can't, so it *splits* one group into old-name and new-name halves with no way to reunite them. That's why source groups on `booking_link_id` (not a snapshotted slug), why links are archived rather than hard-deleted with a slug stamped on their children, and why the kind is a table rather than a string.
     (2) **The kind table must be mutable.** Append-only was designed and dropped: if editing a type inserts a new row and repoints the link, a link that changes what it produces would relabel *nothing*, and typo fixes would fork instead of correct. Rows are edited in place; only the **pointer** is frozen. Deletion is a plain hard delete with `SET NULL` — no archive state, because a globally unique label plus no unarchive path in a create/edit/delete picker would burn the name permanently.
     (3) **Releasing the slug on archive is deliberate**: it lets a retired offering's name be reused by its successor, and the misroute it enables reaches only strangers arriving cold at a stale URL for a **new** booking — never an existing booking, whose manage link is keyed on its own `public_id` and whose rules come through the FK.
7. ~~**iCal fields on `BookingSeries`**~~ — **done.** `freq`/`interval`/`count` columns on both
   `BookingSeries` and `BookingLink`, `byday`/`wkst` as comments only. §3 was superseded by item 6
   and never built. Design:
   `.claude/plans/done-booking-series-recurrence-fields-and-event-type-denormalization.md`.
   `COUNT` and `UNTIL` are mutually exclusive per RFC5545 (CHECK-enforced on both tables); occurrence
   walks are `interval * FREQ_DAYS[freq]` rather than a literal week; `_reschedule_series` now does
   Google's split, giving the new series `count = original − consumed` where consumed excludes
   `status='rescheduled'` rows (their slot moved to the replacement — counting both spends it twice).
   Both of the doc's open questions resolved: the booker-override ambiguity was designed out by
   making the two overrides mutually exclusive and mode-scoped, and the excused-cancellation
   exemption was dropped — cancelled is cancelled, forgiveness is a billing concern.
   Also landed, beyond scope: `recur_weeks` renamed to `count` full-stack, the link editor's "Ends"
   section rebuilt to Google's three radios with `booker_can_set_count` wired through, the
   `available_slots` inner sweep made step-driven, `require_slot_in_schedule` added to the write
   path, and `UNTIL` fixed to emit a UTC date-time as the spec requires.
8. ~~**Contact/Student identity split**~~ — **done, backend and frontend.** Plan:
   `.claude/plans/contact-identity-split.md`. Full model: CLAUDE.md's "Contact identity".
   `contacts` + `contact_managers` shipped; `Student` became enrollment on a `contact_id`;
   `Booking`/`BookingSeries` carry `payer_id`/`attendee_id` NOT NULL and the six `student_*`/
   `parent_*` columns are gone. The attendee facet replaced the `(first, last)` name-pair matching,
   and the unauthenticated `email` query param was removed. `routers/contacts.py` is the one place a
   person's details change. Also landed alongside: `sms_opt_in`/`guest_reminder_phone`, and
   `extra="forbid"` on every request schema.
   - **Frontend**: rows read through `attendee`/`payer`; `BookingRow`'s expanded panel shows one line
     per person (collapsing to "Client" when they're the same row) plus the booking's own SMS/reminder
     line; the dead contact-edit modal is gone; `/my-bookings` and every `isCustomer` branch are gone,
     leaving the two `public_id`-keyed Manage pages as the whole customer surface; `/clients` is a new
     roster page. Picked up en route: `bookingPayload`/`seriesPayload` (one carry-forward body per
     update schema instead of three hand-rolled copies), `PolicyModal` split into a shared chrome
     `Shell` plus two draft-owning dialogs, and filter chips resolving labels from facets rather than
     rosters.
   - **Deferred deliberately**: merge tooling (repoint the booking's attendee, then delete the stray
     contact); a `relationship` type on `contact_managers` (no reader); race-safety on the resolvers
     (see the TODO in `booking_utils.py` — find-then-insert can duplicate an emailless attendee under
     concurrency, and neither `ON CONFLICT` nor `SELECT FOR UPDATE` works on SQLite so tests wouldn't
     cover it); renaming `Student` to `Enrollment`.
   - **Immediate follow-ons**, all small and all now unblocked:
     - ~~Client detail view and an enrollments page~~ — **both folded into item 10a**, which settled
       them differently than these two bullets assumed: the detail is a **side panel**, not a route,
       and there is **no enrollments page** at all. Enrollment is a child of a client, edited on the
       client's own panel, with the list filtered to Students when you want just them.
     - **Payer facet** — `apply_scope_filters` filters on `attendee_id` only, inherited from the old
       name-pair facet. With one payer covering two dependents there's no way to ask "everything this
       payer is on," which is the invoicing question. Needs a `payer_ids` param and a fifth facet
       query. Open design question: two separate facets (precise, composes, but two near-identical
       name lists in the menu) or one "Client" facet matching either column (one list, loses the role
       distinction, and muddies self-exclusion since one id touches two columns).
     - **Stale Google Calendar summary on reassign/attendee change.** The event summary is
       `"{slug}: {attendee.first} and {tutor.first}"` (`bookings.py:408`), so changing
       `booking_link_id` or `attendee_id` through the plain PUT leaves the calendar title wrong. Not a
       saga — one `events().patch({summary, description})`. Live bug today, predates this pass.
   - **Client intake fields belong here, not in the `EventType` rework (item 6) where they were
     originally scoped.** `form_fields` (shared reusable question library), `booking_link_fields`
     (live join: which questions a link asks, in what order, required or not), and
     `event_field_responses` (one answer row per question per booking, with a `label_snapshot` of
     the question text *as it was asked*, so renaming a question never rewrites an old answer's
     meaning). Moved because answers attach to a **person**, and there's no `Contact` entity yet —
     building them before the identity split means building on sand. Schema sketch is in CLAUDE.md's
     "BookingLink data model" under Client info.
   - **Open question carried over from item 6**: for a series the booker fills the form once but
     occurrences are many — do responses attach to the `BookingSeries` (occurrences resolve through
     it), or get copied onto each `Booking` at materialization (consistent with every other frozen
     field, but duplicated N times)? Not yet decided.
9. **`created` / `last_modified` on `Booking`** — quick, do it first. `BookingSeries` has both;
   `Booking` has neither, purely because they arrived with the series lifecycle pass (item 4) and
   were never backfilled. Same declarations: `server_default=func.now()`, plus `onupdate=func.now()`
   on `last_modified`. "When was this booked" currently has no answer except the calendar event.
   **The second payoff is optimistic concurrency for the admin edit panel (item 12):** the panel
   sends back the `last_modified` it loaded and the server 409s if the row moved underneath, which
   is the clean fix for the stale-PUT hazard (a full-replacement PUT built from stale list data can
   otherwise silently move a booking). That fix was rejected while planning the panel *only* because
   the column didn't exist. Needs a DB wipe — no Alembic — so fold it in with the contact-split
   reseed rather than paying for a second one.
10. **`Student` → `Enrollment`, and enrollment gets a UI.** Two passes, deliberately split — see 10a
    and 10b. They touch the same tables but answer different questions, and keeping them apart keeps
    each commit legible.

    **Settled up front, shaping both:** there is **no separate Enrollments page**, and no new sidebar
    entry. `/clients` is the identity surface and enrollment is a *child* of a client — the
    characteristics of an identity, not a sibling entity. You filter the client list to the subset
    you want and edit enrollment on the client's own panel. Keeping this lightweight is an explicit
    goal: function-rich without a page per concept.

    **Three filters on `/clients`:**

    | filter | means | derived from |
    |---|---|---|
    | **Clients** | everyone | — |
    | **Students** | has an enrollment | join to `enrollments` |
    | **Payers** | manages someone | `contact_managers` |

    Payers comes from `contact_managers`, **not** from `bookings_as_payer` — the standing
    relationship is more stable than a side effect of having transacted, and it doesn't need the
    booking counts at all. These are overlapping sets, not nested: Rita manages Marcus and may have
    no enrollment of her own, so she appears under Payers and not Students.

    **Watch the conflation:** *attendee* ≠ *enrolled*. Attendee is derived from bookings
    (`attendee_id` appears on one); enrolled means an `Enrollment` row exists. Both gaps are real and
    deliberate — a one-off consultation attendee has no enrollment (which is what lets someone book
    without anyone inventing a rate), and an admin-enrolled client may have no bookings yet. Don't
    build "Students" as an attendee filter.

    **One detail view, with sections that appear when they apply — not a layout per role.** Payer and
    attendee are roles held on individual bookings, never properties of a person, so the panel shows
    whatever is true: *Manages* (if `contact_managers` rows exist), *Enrollment* (or an Enroll
    button), *Their sessions* (if ever an attendee), *Pays for* (if ever a payer). An adult booking
    for themselves populates both session sections — the two are answering different questions about
    the same rows, so nothing conflicts. Someone who booked for themselves last year and now pays for
    their child populates all of it. This is the payoff of roles-on-the-transaction: nothing ever has
    to decide "is this person a payer or an attendee," so the UI doesn't either.

    **Account and enrollment are independent axes.** `Contact.verified_at` means "has an account";
    an enrollment means "has a negotiated rate." All four combinations are valid and none is broken:
    guest-who-booked-once, admin-enrolled-without-an-account (today's normal case), account-without-
    enrollment (a payer who isn't billed a rate themselves), and both. Enrollment must not be made to
    require auth.

    - **10a — rename, client filters, client panel.** Plan:
      `.claude/plans/enrollment-pass-10a-rename-filters-panel.md`. Four steps, in order:
      1. **`Student` → `Enrollment`** throughout: model, schemas, router, `/students` →
         `/enrollments`, `Lesson.student_id` → `enrollment_id`, frontend types. Cheapest now — it's
         the first code written against it and the DB is already being wiped for the contact split.
      2. **The three filters** on `/clients`, server-side, in the URL like search/sort/page already
         are.
      3. **A generic `<SidePanel>`** — header/body/footer, knowing nothing about clients. The
         bookings panel (item 12) wants the same shell; whichever ships first provides it. It
         **overlays** the right of the list rather than reflowing it, Cal.com-style, so the rows'
         hover actions sit behind it and are simply unreachable while it's open — no conditional
         rendering needed.
      4. **Client detail content inside it** — read-only, with **Edit** flipping to a form covering
         identity *and* enrollment together, one Save. Enrolling is "set a rate" on a client you're
         already looking at. **This retires the current edit modal** on `/clients`; delete and enroll
         move into the panel too.

      Build the detail as a `<ClientDetail client={...} />` **component**, not as page markup — the
      container is ~10 lines either way, so if the panel feels cramped once enrollment and
      relationships are both in it, promote it to a `/clients/:id` route and the content moves across
      unchanged. That route would be reachable only from the list, like `/links/:id` today, and would
      need a back affordance the panel doesn't.

      Backend CRUD already exists in `routers/students.py` with the right guards (404 on missing
      contact, 409 on already-enrolled, 409 on delete-with-lessons), so this pass is mostly frontend
      plus the rename.

      **Keep `rate` a single float here.** 10b replaces it; doing both at once muddles the commit.
    - **10b — billing rates.** Replaces the bare `rate` float. Design settled in conversation, not
      built:

      ```
      enrollment:    rate_per_session  XOR  rate_per_hour  XOR  rate_per_month
      booking_link:  price_per_session XOR  price_per_hour      (today: one `price` column)
      ```

      Mutually exclusive nullable columns with a CHECK, **the same shape as `until`/`count`** on
      recurrence and for the same reason: they're *different facts, not two spellings of one*. An
      hourly rate and a monthly fee aren't one number measured differently — they generate invoices
      on different triggers. A `billing_mode` enum plus a single `rate` would also need a third state
      for "neither", which the nullable set expresses for free.

      Per-booking charge resolution:

      ```
      fee_override
        ?? enrollment.rate_per_session
        ?? enrollment.rate_per_hour  × hrs
        ?? link.price_per_session
        ?? link.price_per_hour × hrs
      ```

      **`rate_per_month` sits outside that chain entirely** — a monthly enrollment's bookings are
      free at booking time, and the money comes from a scheduled invoice line instead.

      **Name for the trigger, not the flatness.** "Flat" means two different things: flat *per
      session* on a link (still charged at booking time, just not multiplied by hours) versus flat
      *per month* on an enrollment (not charged at booking time at all). Three of those five columns
      charge per booking; one doesn't.

      **Invoicing: one bill per payer, one line per enrollment.** Rates live on the *attendee* —
      siblings genuinely differ — and billing rolls up to whoever pays, using `contact_managers` to
      find the household. This is what practice-management software does (Teachworks, TutorCruncher,
      SimplePractice), and it's **not** Miro's seat model: seats are fungible, students aren't, so
      `quantity × unit price` breaks the moment two children cost different amounts. Rita with two
      children on monthly plans has two enrollments billed together, which is also how Stripe models
      it — subscription *items* under one customer rather than a quantity.

      **Deliberately out of scope:** collecting payment, proration, dunning. Cancelling a monthly
      plan means it runs to the end of the paid period with no refund. This is invoice *generation*
      only, which is why it's a small feature rather than a payments integration.

      **Self-enrollment isn't a thing yet**, and may never be — a client can't set their own rate,
      that's the business's call. If plans ever become customer-selectable, an account requirement
      comes with it, because picking a plan is a commitment and you need to know who's committing.

      Note `Lesson`'s fee model predates the current architecture and should be treated as legacy
      internal tooling rather than a constraint on this design.
11. **Email + auth** — Auth gates at the route level (protected-route wrappers), so doing
    this after the subroute split (3) means gating the final route structure once, not redoing it
    after a later refactor.
    - **Unify human identity — before or during this pass, not after.** Today `Tutor` and `Contact`
      are unrelated tables that happen to both store a name. That's tolerable only because tutors
      have no email; the moment they log in, one email has to mean one identity across staff and
      clients, and bolting a second login table on is how you end up with two sources of truth.
      Auth is what forces it, which is why it belongs here rather than as its own pass.

      Target shape — a shared identity with two sibling extensions, class-table style (each child's
      PK **is** the parent's id, the same pattern item 10a establishes for enrollment):

      ```
      <person>     first_name, last_name, email, phone, verified_at
      employees    <person>_id PK, pay_rate, calendar_id, is_active
      enrollments  <person>_id PK, rate, start_date, grade, is_active
      ```

      **Name not settled** — `Person`, `Party` (the accounting term for any billable entity),
      `Individual`. Pick before starting; it renames a lot.

      **There is no `contacts` table in the middle.** Once `verified_at` moves up (staff need
      accounts too), Contact has no columns of its own, so it collapses into a *word* for a person
      rather than a table. Bookings' `payer_id`/`attendee_id` point at the identity table directly.

      **Employees and enrollments don't overlap** — a tutor is staff and is never enrolled. They're
      siblings, not a hierarchy, and nothing should permit both. (The owner-practitioner case is
      an employee with `pay_rate=0`, which is already how it works.)

      **Blast radius is why it isn't bundled with 10a:** every FK naming a human moves —
      `bookings.payer_id`/`attendee_id`/`tutor_id`, `booking_series` the same, `schedules.tutor_id`,
      `booking_link_availability.tutor_id`, `lessons.tutor_id`/`enrollment_id`. 10a's
      `enrollments.contact_id` → `<person>_id` is a mechanical rename once this lands.

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
12. **Admin booking edit — pass 1: occurrences.** Plan: `.claude/plans/admin-booking-edit-pass-1-occurrences.md`. A
    right-side detail panel on the bookings list, read-only until you hit Edit, then one Save. Backed
    by `PUT /bookings/{ref}` (full DTO, admin only, mutates in place) alongside the existing
    `POST /bookings/{ref}/reschedule` (saga, rules enforced, admin or customer). Quick actions become
    narrow `PATCH /bookings/{ref}` calls. Series *occurrences* are included except the tutor field,
    which is disabled pending the calendar bug in Known TODOs. Absorbs the policy modal, the reassign
    modal and the expanded-row contact panel.
13. **Admin booking edit — pass 2: series.** Plan:
    `.claude/plans/admin-booking-edit-pass-2-series.md` — read it for the full reasoning, including
    the unresolved `tutor_id` question that needs approval before implementation.

    **Three endpoints, five entry points.** The split is mutate-vs-fork, mirroring pass 1:

    | endpoint | scope | behaviour |
    |---|---|---|
    | `PUT /bookings/{ref}` | this occurrence | mutate in place (pass 1) |
    | `PUT /booking-series/{id}` | whole series | mutate in place, admin only |
    | `POST /booking-series/{id}/reschedule` | from a pivot onward | fork |

    - **`PUT /booking-series/{id}`** takes metadata *and* time. A time change is a genuine mutation:
      patch the Google **master** (which shifts every instance), mutate `dtstart`/`dtend` on the
      series row, rewrite the occurrence rows onto the new grid — past included. Same row, same
      `public_id`. This is Google's "all events". Reached from the series card *or* from an
      occurrence's **all** option — same endpoint, two entry points.
      **Today's `PUT /booking-series/{id}` is the fork**, which the API-cleanup entry already flags as
      backwards, so pass 2 swaps the two endpoints' meanings.
    - **`POST /booking-series/{id}/reschedule`** takes an optional **`pivot`**, defaulting to now.
      A customer rescheduling "the series" *is* "this and following from now", so it's one operation:
      customer → `pivot=now` with the link's rules enforced; admin "this and following" from an
      occurrence → `pivot` = that occurrence's date, rules skipped. The rules difference is
      authorization on one endpoint — the one legitimate case where role changes enforcement without
      changing what the operation does. The body describes the *new* series, so metadata changes ride
      along naturally. **New work:** `_reschedule_series` currently hard-codes the pivot at `now` —
      CLAUDE.md states "the pivot is always `now`, never a chosen occurrence" — so it needs a pivot
      parameter and that line needs updating.

    **Occurrences already moved individually keep their times** when the series time changes — an
    override stays overridden, matching Google. When rewriting occurrence rows onto the new grid,
    skip any whose `google_event_id` differs from the series' (the existing `is_exception` test).
    The panel warns: "N sessions were moved individually and will stay where they are."

    **Both `all` and a past `pivot` can rewrite delivered sessions**, which may have `Lesson` rows
    pointing at them. Warn, don't block — admin is king.

    **No scope prompt for customers.** Scoped edits are admin-only precisely because admin bypasses
    policy. A customer's "this and following" would span N bookings each with its own frozen policy
    and notice window, some past and unconditionally blocked — every rule for resolving that is
    arbitrary. That is exactly what the series-level policy pair
    (`series_cancel_mode`/`series_reschedule_mode`, no notice window) exists to avoid.

    **Considered and rejected: deleting `BookingSeries` entirely**, moving the recurrence columns onto
    `Booking` as nullables and grouping by `series_id`. Appealing — one policy model, no
    series-vs-occurrence split — but the rule is *one fact, not N*: copying `freq`/`interval`/`until`/
    `count`/`dtstart` onto every occurrence makes a recurrence change an N-row update that can go
    half-done, and indefinite series need a rule to generate *from* (`extend_all_series` walks series
    rows; `available_slots.py` reads them to subtract weekly bands in `(weekday, time)` space before
    any date resolves). Putting the rule only on the first booking makes that row a series row in
    disguise, orphaned when it's cancelled. And the "copy Google" argument cuts the other way —
    Google has a master event carrying the RRULE with instances generated from it, which *is* this
    two-level model.

    Still open: **C**, changing `tutor_id` on a single occurrence — see the separate bug entry in
    Known TODOs. Google won't move one instance between calendars, so it would mean detaching the
    occurrence into a standalone event, and a detached booking then outlives a series deletion while
    a normal exception dies with it. Leaning toward rejecting the operation outright (400) and making
    the admin cancel-and-rebook, which is explicit and what Google effectively forces.

## Known TODOs / Planned Work

Concrete bugs, missing logic, and planned improvements — not yet implemented.

### Backend

- **Split booking creation from series creation — `BookingCreate` vs `BookingSeriesCreate`, own endpoints.** Today `POST /bookings/` does both: it branches on `link.recurring` and either inserts one row or a `BookingSeries` plus N occurrences, off one schema carrying `recur_until`/`recur_count` that are meaningless for half its callers. **The read side already draws this line** — `GET /booking-series` and `GET /bookings/` are separate endpoints returning separate entities — so the write side reusing one schema is the inconsistency, not the split. A booking and a series are different things; that bookings can *originate* from either a standalone create or a series is fine and expected (it's what the timeline view merges).
  - **The objection that kept this open doesn't hold.** It was argued that the client can't know which endpoint to call, since `recurring` is server state — but `BookingPage` already fetches the link before it can render anything (it reads `duration_minutes`, `booker_can_set_recur_until`, `booker_can_set_count`), so `link.recurring` is in hand well before submit. No server rule gets replicated client-side.
  - **Concrete damage while unsplit**: three `model_dump(exclude={...})` call sites must each remember to strip the schema-only recurrence fields before hitting the ORM. Adding `recur_count` and missing the exclusions broke every create with a 500. A `BookingSeriesCreate` that only *has* the recurrence fields removes the exclusion problem rather than centralising it.
  - Cheap interim mitigation if the split is deferred again: one `orm_fields()` method on `BookingCreate` doing the exclusion once.

- **Wire the remaining calendar rules into the write path.** `require_slot_in_schedule` (`booking_utils.py`) now gates `create_booking`/`_reschedule_booking`/`_reschedule_series` so a slot must sit inside the tutor's schedule — previously **every** calendar rule lived only in `/available-slots`, which runs before the write and can just be skipped, so a direct POST booked a tutor at 3am on a day they don't work. Schedule is now covered; caps, buffers, lead time and horizon still aren't — though those are unimplemented rather than bypassed (see the six dead limit fields in the MVP list). Wire them at the same guard when they land.
  - **Known consequence**: this removed the admin's accidental ability to book outside a tutor's hours, since both entry points share the endpoint and there's no auth to tell them apart. That freedom returns deliberately with the admin-native surface below — which is now load-bearing, not just nice-to-have.

- **Deletion safety, part 2 — enforce "every non-archived link has ≥1 active host" as an invariant.** `availability` carries `min_length=1` on `BookingLinkCreate`/`Update`, so a link can never be *created or edited* down to zero hosts. Two tutor operations bypass that and silently leave a link resolving, looking active, and generating no slots at all:
  - **`DELETE /tutors/{id}`** — 409s on `Lesson`/`Booking`/`BookingSeries` but never checks `BookingLinkAvailability`, so a tutor with no bookings is hard-deleted and their availability rows cascade away with their schedules.
  - **`PUT /tutors/{id}` with `is_active=False`** — touches no rows at all, but `get_available_slots` excludes inactive tutors, so deactivating the last host has the same effect by a different mechanism. Easy to miss precisely because nothing is deleted.

  Guard the invariant rather than the operation: block either if the tutor is the last **active** host on any non-archived link. Ignore archived links for the same reason `delete_schedule` does — archive is terminal, so counting them would make the tutor permanently undeletable. Blocking delete also pushes the admin toward `is_active=False`, which is the intended offboarding path anyway; the deactivation guard then makes them reassign or archive the stranded link first. (There is no DELETE endpoint on `booking_link_availability` — only GET/POST/PUT — so those two are the whole surface.)

- **`BookingLinkAvailability.schedule_id` is `CASCADE`, so schedule deletion has no DB-level protection** — only the app guard in `delete_schedule`. Bypass the router and the rows vanish silently. It can't simply become `RESTRICT`: `delete_tutor` relies on the tutor→schedule→availability cascade chain, and RESTRICT would make that depend on whether Postgres clears the rows via `tutor_id` before it deletes the schedules. The honest fix is `RESTRICT` plus explicit availability cleanup inside `delete_tutor` — more code, but it stops correctness resting on cascade ordering. Low priority: the app guard covers every path that exists today.

- **Split `booking_utils.py` by topic.** ~700 lines holding five unrelated jobs: occurrence materialization (`_ensure_occurrence`, `_virtual_occurrences`), write-path guards (`require_slot_in_schedule`, `require_link_*`), recurrence arithmetic (`series_step`, `series_last_date`, `build_rrule`, `is_indefinite`), filters/facets, and cursor encoding. Suggested: `recurrence.py` and `cursors.py` peeled off, guards and materialization staying. Pure move plus import fixes, no behaviour change — worth doing on its own rather than folded into a feature pass.
  - Naming test that makes the split obvious: each new file should be describable in one word. An earlier attempt grouped functions by "has no imports" and wanted to call the result `rules.py` — a module named after its dependency profile rather than its contents, which is the sign the grouping is wrong.
  - `policy.py` is **not** part of this; it's a single-purpose module with an accurate name. It has to stay a leaf regardless: `schemas.py` imports it to compute `cancel_action`, and `booking_utils.py` imports `schemas.py`, so anything it imported back would close a cycle.

- **No `BYDAY` — a twice-a-week client is two series.** A Google weekly event recurs on several weekdays (`BYDAY=MO,WE,FR`); ours infers one weekday from `DTSTART`, so Mon+Wed sessions are two independent `BookingSeries` that cancel, reschedule and count down separately. That isn't what the customer means by "my sessions", and Mon/Wed or Tue/Thu is a common shape in tutoring and PT. Needs `byday` as an array column (a scalar placeholder would just get replaced — see the comment on `BookingSeries`), occurrence walks that step *within* a week rather than by one stride, and the same rework `available_slots`' `WeekdayTime` bands need for the biweekly case. The largest remaining gap against Google's recurrence model.

- **Admin-native booking surface — direct-add and edit-in-place.** Today the admin has no surface of their own and reschedules *through the booking page*, so they inherit its rules by accident: availability, buffers, caps, lead time. That's backwards — an admin should be able to place a booking anywhere, including overlapping another, outside any schedule, past the caps, or in the past. Two halves of one feature, the Google Calendar model: **direct-add** (click the grid, make a booking, no picker) and **edit-in-place** (write `dtstart`/`dtend` straight onto the row). Both **still patch Google Calendar** — they skip validation, not side effects — so for a series occurrence it's the existing `events().instances()` patch minus the guards. Not urgent (the booking page covers it for now), but it's the thing that makes "a retired link's rules are inert" true, so CLAUDE.md's model already assumes it. Guardrails: a soft "this overlaps an existing booking" warning the admin can proceed past — never a block.
  - **Field-by-field taxonomy, settled** (from the design pass alongside item 8). Three tiers, not two. **Pure DB write, no Google call**: `booking_type_id`, the four policy columns, `is_no_show`, `sms_opt_in`, `guest_reminder_phone`, `payer_id`. **DB write + a Google `patch`, same event id**: `booking_link_id` and `attendee_id`, since both appear in the event summary and the link also in the description — the stale-summary bug above. **Saga**: `start`/`end`, and `tutor_id` (the event lives on *that tutor's* `calendar_id`, so a tutor change is delete-there/create-here and yields a new `google_event_id`). **Never editable**: `id`, `public_id`, `series_id`, `google_event_id`, `rescheduled_to`, `timezone` (being dropped), and `status` — cancel is the DELETE route because it also patches Google, and there's no un-cancel path to put in a dropdown.
  - **Admin move reuses the reschedule saga; it does not mutate in place.** An earlier draft of this said mutate — wrong for a series occurrence. `_ensure_occurrence` keys on `(series_id, start)`, so moving `start` leaves the old grid date with no row and the next grid walk re-materializes it; the soft-deleted row *is* the tombstone. And an occurrence's `public_id` encodes its timestamp, so mutating `start` either desyncs it or breaks an already-emailed manage link. So the admin path is `_reschedule_booking` with the guards skipped, not a second write path. The rules layer is only two lines at the top of that function (`require_link_not_archived` + `require_slot_in_schedule`, `bookings.py:502-503`); everything below takes `start`/`end` as given and never asks where they came from. **Skipping validation and keeping the saga are independent choices** — skip the first, keep the second.
  - **Series recurrence has no edit endpoint at all.** `freq`/`interval`/`until`/`count` can't be changed — you can't extend or shorten a series without fully rescheduling it. RRULE patch, its own piece of work.
- **Make `PUT /bookings/{id}` and `PUT /booking-series/{id}` into PATCH.** These are full replacements but no caller wants replacement semantics: every one changes exactly one field and carries six others along out of obligation. That's why `bookingPayload`/`seriesPayload` exist, and PATCH would delete both — reclassify becomes `{booking_type_id: 5}`, no-show becomes `{is_no_show: true}` (and stays a field write rather than growing a `/no-show` subroute). Cost: both schemas go all-optional with `model_dump(exclude_unset=True)`, the router switches to `setattr` over the set fields, and the tests that PUT full bodies get updated. `extra="forbid"` still works and still catches removed fields. Worth doing before more callers accumulate. The subroutes stay subroutes — `POST .../reschedule` patches Google, inserts a row, soft-deletes another and sets `rescheduled_to`, which is nowhere near a field write.
- **Email notification on admin-initiated changes** — "your session has been moved," including the whole-series case. Pairs with the direct-move surface above: once an admin can silently relocate a booking without the customer initiating it, the customer needs telling. Depends on the minimal `send_email` capability from item 9.
- **`paused` as a third `BookingLink.status`** — URL 404s like `archived`, but calendar rules stay **live and editable**, existing bookings stay self-reschedulable, and series generation continues. Real jobs archiving can't do: seasonal links (off Sept–April, back in May with rules intact), "booked solid this month, pause new bookings," and an unpublished draft state. The enum from 6a makes this additive — no existing meaning changes. **Backend is nearly free** (`paused` lands correctly on both sides of every check already written: `create_booking` wants `== 'active'`, edit/reschedule/generation want `!= 'archived'`); the cost is UI — a toggle framed as a link *setting*, not a sibling of Archive, plus list grouping and badges. Open when built: can an admin manually create on a paused link? Current answer no — blocked means blocked, reopen it instead.
- **Per-booking policy editing needs the scope prompt before it's complete.** The series policy modal deliberately edits only the *series-level* pair (`series_cancel_mode`/`series_reschedule_mode`). The occurrence-level four are a template for occurrences not yet materialized — so on a **finite** series, where every occurrence is created up front, editing them silently changes nothing, and on an indefinite one it changes only rows the extender hasn't made yet. A control that works for half of series and no-ops for the rest is worse than none, so it was cut. **Consequence until scoped edits land**: an indefinite series' not-yet-materialized occurrences have no editable policy — they keep whatever the link stamped. Already-materialized ones are still editable one at a time from the booking row.

- **Series-scoped wiring edits — this / this-and-following / all.** Google Calendar's recurring-edit prompt, applied to the wiring fields on a series (`booking_type_id`, the four policy columns, contact fields, later `title`) — never to slot rules, which live on the link. This is the compensation for wiring being per-row: re-typing fifty occurrences by hand isn't a workflow. **The trap**: `following`/`all` must update the `BookingSeries` row itself, not just its materialized `Booking`s, because `_ensure_occurrence` copies wiring **off the series row** — miss it and every occurrence Procrastinate generates from the next day onward silently reverts, forever. `this` must *not* touch the series row. "All" including past occurrences is correct here: the admin picked "all", and it's explicit rather than an invisible cascade. Default the prompt to "this and following" (Calendar's default, and the safest — doesn't rewrite history, does fix the future).
- **`Booking`/`BookingSeries` need their own name — deferred.** Bookings and series carry no name of their own today; they'd get one generated at creation from a link-supplied template (a string-builder like `{first} {last} — {duration}`), so it varies per booking rather than being a shared label. Distinct from the kind, which is a shared label pointed at by many rows and used for grouping. **Point the template at the generated name, never at the type** — templating the grouping key produces a distinct bucket per booking, fragmenting grouping by construction. Not scoped: the template syntax, which variables it exposes (student first/last, tutor, duration, date?), and how literal text between tokens is handled. Also the natural home for the iCal `summary` field (see the `BookingSeries` iCal note above).
- ~~A booking/series blocks itself from its own reschedule slot picker~~ — **done.** `get_available_slots` takes `exclude_booking_id`/`exclude_series_id`, filtered out of both `booking_q` and the `inf_rules` query (the series case mattered more — `thin_schedule_dateless` was subtracting the whole `(weekday, time)` band before any date resolved). The endpoint accepts `exclude_ref`/`exclude_series_ref` as `public_id`s and resolves them to internal PKs, no-op'ing on an unresolvable ref so a virtual occurrence doesn't 404. `BookingPage.tsx` passes them from `location.state` on both reschedule paths. Overlapping the original slot is now allowed; rescheduling to the *identical* slot is rejected by new guards in `_reschedule_booking` (exact `start`/`end`) and `_reschedule_series` (same weekday + time-of-day + tutor, since a series' identity is its pattern, not an instant).
- **Drop `Booking.timezone`.** It's a request-time conversion input, not state: `BookingCreate`/`BookingReschedule` use it to turn client-local into UTC, and after that nothing reads it — both display paths use the viewer's live-detected zone instead. Its only claimed future use was rendering reminder emails in the booker's zone, and that's been rejected: emails render in business time with the zone named explicitly ("4:00 PM ET"), which is unambiguous and doesn't depend on a zone captured months ago still being right. Move it to schema-only (excluded before reaching the ORM, same as `fee_override`) and drop the column. Needs a migration, so it waits on Alembic. **Not to be confused with `Schedule.timezone`, which stays** — a tutor in another zone enters availability in their own local time, and that column is the only record of which zone to convert from; its model TODO calling it redundant describes today's single-local-tutor data, not the design.
- **BUG — rescheduling a series occurrence to a different tutor leaves the calendar event on the old tutor's calendar.** `_reschedule_booking` (`routers/bookings.py`, the `if is_series:` branch around line 537) patches the RRULE instance on `old_calendar_id`, derived from `db_booking.tutor.calendar_id` *before* the change, and never looks at the new tutor's calendar. The row gets the new `tutor_id`; Google keeps the session on the old tutor. Silent drift — no error, nothing logged. The standalone branch is correct: it inserts on `db_tutor.calendar_id` and deletes the old event.
  **Why it isn't a one-liner.** You can't move a single RRULE instance to another calendar. The fix is: cancel the instance on the old tutor's calendar, create a *standalone* event on the new tutor's, and store that new event id — which detaches the occurrence from the series' recurring event. That's the same end state Google reaches when you drag one instance of a recurring event to another calendar, so it's the right model, but it needs `google_event_id` to stop implying "belongs to the series' RRULE" and it interacts with `_ensure_occurrence` (which copies `series.google_event_id` onto new occurrences).
  **Found while scoping the admin edit panel**, which deliberately disables the tutor field for series occurrences rather than adding a second broken path. Fix this and the panel can enable it.

- **Let an admin reopen a cancelled booking.** `reschedule_booking` (`routers/bookings.py`) requires `status == 'confirmed'`, so a cancellation is terminal for everyone — an admin can't move a booking a client cancelled by mistake, they can only create a new one, which loses the link to the original. Fix is small: allow the admin PUT to set `status` back to `'confirmed'` (customer-facing routes stay locked). It also needs the Google Calendar side undone — cancelling patches the RRULE instance to `cancelled`, so reopening has to patch it back. Cancelled staying terminal is right as the *default*; this is the admin override, same shape as every other place where admin bypasses a booker-facing rule.
- **Adopt Alembic — no migration tool exists.** `create_all` only creates missing tables, so today every schema change is answered with `docker compose down -v && up -d`. That's fine while the only data is seed data and impossible the day someone is paying. Alembic autogenerates a migration by diffing models against the live DB, versioned and reversible. **Several backlog items already assume it exists** — the `BookingSeries.status` `NULL`→explicit backfill, `Booking.student_id`→`contact_id` `NOT NULL`, `google_event_id`→`external_event_id`. Each of those is written as "needs a real migration" with no tool to write one in. Should land before real data does, and ideally before whichever of those items goes first.
  - **Once it exists, move the Python-side column defaults to `server_default`.** `Column(..., default=...)` is applied by SQLAlchemy on ORM writes and bypassed entirely by raw SQL, so any NOT NULL column carrying one is a landmine for `initialize_database*.py`, which INSERTs directly. It has already gone off twice: `public_id` (Pass 1) and `freq`/`interval` (the recurrence pass) both left the example seed dying on a NOT NULL violation with nothing to catch it — no test covers those scripts. Current set: `bookings.public_id/timezone/status/is_no_show`, `booking_series.public_id/freq/interval`. `public_id` is the one exception worth keeping Python-side, since a series occurrence's is the composite `{series.public_id}:{ts}` rather than a bare UUID.
- **API shape cleanups — noticed while adding `booking_type_id` in 6b.** Individually cosmetic, worth doing together since they're the same judgment call.
  - **Fold `POST /{ref}/reassign` and `POST /booking-series/{id}/reassign` into the plain-column PUTs.** Setting an FK is a column write plus a validation, not an operation — it doesn't create a resource or run a saga, so it doesn't need a verb endpoint. The rule worth holding: one PUT for plain columns; separate routes only for things that create a resource, run a saga, or carry their own policy (reschedule earns it, relabelling doesn't). Left alone in 6b because they're shipped Pass 1 surface with frontend call sites attached.
  - **`PUT /booking-series/{id}` is the reschedule saga, not a plain update** — backwards. The bare PUT on a resource should be the ordinary field update; a saga that creates a new series row with a new `public_id` should be `POST /booking-series/{id}/reschedule` (it can't be a PUT at all: a GET afterwards returns the old row, so PUT's contract is broken). Rename, and give the series a real plain-column PUT — it has none today, which is why 6b's type picker on a series row needed a route invented for it.
  - **Foreign keys on `bookings`/`booking_series` are unindexed.** Postgres auto-indexes primary keys and unique constraints but *not* FKs (MySQL does, which is where the assumption comes from). `booking_link_id` and `tutor_id` are both facet keys hit by `IN (...)` and `SELECT DISTINCT` on every list request. `booking_type_id` got `index=True` in 6b; the older two didn't and should match.
- **RESTful endpoint cleanup on `bookings.py` — medium priority, design mostly settled, nothing implemented.** Came out of designing the admin edit panel; the routes grew one at a time and several are shaped wrong. Read this whole entry before touching any route, the pieces interact.

  **The rule that decides everything below**, worked out from how Stripe/GitHub/Google actually do it:
  1. *Does the operation create something you could `GET` afterwards?* → plural-noun sub-collection (Stripe `POST /v1/refunds`, GitHub `POST /repos/{o}/{r}/forks`).
  2. *If not, can it be stated as "make these fields equal these values"?* → `PATCH`/`PUT`.
  3. *Otherwise* → a verb is legitimate: a state transition with preconditions and side effects a field write can't express (Stripe `/capture`, `/finalize`, `/void`; GitHub `/merge`; Google Calendar `/move` — note `/move` stays a verb precisely because it returns the *same* event, creating nothing).

  **Side effects never decide the verb.** A `PATCH` that triggers a Google Calendar write is still a `PATCH`, the same way changing a user's email sends a verification mail and stays a `PATCH`. What decides it is what happens *to the resource*.

  **Decided:**
  - `DELETE /bookings/{ref}/permanent` → `DELETE /bookings/{ref}?permanent=true`. `/permanent` is an *adjective*, not a resource — the URL reads "delete the permanent of this booking." Clearest violation, cheapest fix, do it regardless of the rest. Same for the series twin.
  - Admin edit is `PATCH /bookings/{ref}` — it sets fields, so it earns no verb (see the admin-edit plan doc).
  - Leave `POST /booking-request/{id}/approve|deny` alone. Approving doesn't just set a status, it *applies* the change (reschedules the booking) — a transition with side effects, which is exactly when a verb is right.

  **Considered, NOT decided — the open question is whether `/reschedule` becomes `/reschedules`:**
  - The plural noun is only honest if `GET /bookings/{ref}/reschedules` also works, otherwise the URL promises a collection you can write to but never read. GitHub's `/forks` is honest because the GET lists forks.
  - It *would* work for us — the chain is derivable from `rescheduled_to`, so the GET returns the bookings descended from this one. That's also independently useful: the edit panel wants to show "moved from Sep 3."
  - So: implement the GET and rename to `/reschedules`, or keep the verb and accept it's an action. Leaning toward the former, undecided.

  **Considered and rejected, with reasons (don't re-litigate):**
  - **`POST /bookings/` with `rescheduled_from: <ref>`** instead of a separate reschedule route. Most RESTful on paper — a reschedule does create a booking. Killed by: three fields (`payer`, `attendee`, `booking_link_id`) become required-unless-rescheduling; the rule swap (create runs `require_link_bookable` which rejects paused, reschedule runs `require_link_not_archived` which allows it); and fatally, **rescheduling a series occurrence creates no calendar event at all** — it patches the existing RRULE instance and reuses that instance id. So "a reschedule is a creation" isn't even true at the calendar layer. Two routes sharing helpers underneath beats one route with a mode flag.
  - **An `is_admin` boolean on the reschedule endpoint** to skip the link's rules. Two problems: the flag would have to come from the request (there's no session yet), which is client-supplied authorization and no authorization at all; and it isn't only the *checks* that differ — admin edit mutates in place while reschedule forks a row, so the flag would branch the whole function body. **Role gates access; it must never change what an endpoint does.**

  **Deferred to the auth pass, where it collapses into something better rather than being renamed twice:**
  - The four `manage-occurrence/{ref}/*` and `manage-series/{ref}/*` routes are duplicates of the booking routes with a policy gate bolted on. But "may this caller cancel?" is an **authorization** question, not a behavioural one — so once sessions exist, `DELETE /bookings/{ref}` can serve both: admin session → allowed, capability-URL holder → policy-gated. That deletes four endpoints instead of renaming them. A guest holding an unguessable `public_id` is its own capability-auth model and doesn't need a session.

  **Also worth splitting while in here:** `POST /bookings/` currently creates both standalone bookings and whole series, branching on `booking_link.recurring`. The only reason it's shared is that the caller can't choose — the link decides — but the frontend already knows (it renders the recurrence picker off that flag). Splitting out `POST /booking-series/` removes ~60 lines of series-only logic (recurrence-bound resolution, `_gen_through`, the multi-occurrence conflict loop, N-row generation) from the standalone path. Genuinely shared parts move to helpers: tutor validation, link validation, `require_link_bookable`, `require_slot_in_schedule`, contact resolution, the guest-phone freeze, the compensating delete. **Independent of the admin PATCH** — that edits one `Booking` row either way.

- **Revisit the hand-rolled Google Calendar saga.** Every write path (`create_booking`, `_reschedule_booking`, `_reschedule_series`) calls Google **first**, then writes the DB, then issues a compensating delete/patch if the DB fails — and if the compensation *also* fails, logs a warning and moves on. The order isn't a preference: `Booking.google_event_id` is `NOT NULL`, so there's no row to insert until Google has answered.
  **What that invariant buys**, and it's real — a booking can never exist without a calendar event, so no row can end up permanently unreschedulable. Don't discard it casually.
  **Downsides of the current shape:**
  - **Availability is capped by Google's.** If their API is down, no bookings can be taken at all — the business stops because a third party is unavailable, for what is arguably a secondary feature.
  - **Compensation can itself fail**, leaving an orphaned calendar event that nothing tracks or cleans up. A log line is the only record.
  - **Google is called before the DB has validated anything** — a duplicate-occurrence violation or a failed CHECK is discovered only *after* the event exists, which is exactly the case needing remote compensation.
  - **Not actually atomic**, despite reading like it is. It's two systems and a best-effort undo.
  - **Duplicated three times**, each with its own compensation branch and its own failure logging.
  **Options, cheapest first:**
  1. **Reorder: flush before calling Google.** Insert rows with a placeholder id, `flush()` so every FK/unique/CHECK is validated, *then* call Google, set the real id, commit. A local `ROLLBACK` replaces the remote compensating delete — reliable in a way an HTTP DELETE isn't, and orphans become near-unreachable. Cost: holds an open transaction (and row locks) across a network call. Fine at one practitioner's volume, a known anti-pattern under write concurrency.
  2. **Add a reconciler.** A periodic job comparing calendar events against `google_event_id` values, cleaning up strays. Complements the saga rather than replacing it; the cheapest fix for the orphan branch specifically.
  3. **Outbox via Procrastinate** (already in the stack). Commit the booking and an outbox row in one transaction; a worker calls Google and retries. Atomic, no compensation in the request path, survives Google being down. Cost: `google_event_id` becomes nullable, 201 returns before the event exists, and permanent failures need a dead-letter someone actually watches. Converts silent loss into a visible retryable backlog — it does not *guarantee* success.
  4. **Demote Google to a projection** (the biggest change, and the one that actually fixes availability). TMS becomes the system of record; the calendar is a downstream mirror with its own sync state. Booking succeeds regardless of Google; the admin UI surfaces what hasn't synced. Customer-facing cost is smaller than it looks — `BookingPage.tsx` already builds its "add to calendar" links client-side (Google template URL, Outlook, ICS blob) with no API call. Complication: `check_calendar_conflicts` *reads* Google for busy times, so there's a read dependency too — needs a decision on what to do when the calendar can't be seen (assume free, or block).
  **Precedent — Cal.com does option 4, and documents its cost.** Their `Booking` has no calendar-event column at all; external refs live in a child table (`BookingReference` with `type`/`uid`/`externalCalendarId`, and even `bookingId` nullable), so a booking with zero references is structurally ordinary. The failure mode that produces: [#22192](https://github.com/calcom/cal.com/issues/22192) — when calendar creation fails the booking still exists, but every later reschedule fails too, because the reschedule path assumes a reference exists and patches an event that doesn't (sometimes with an empty `uid`). [#25009](https://github.com/calcom/cal.com/issues/25009) — v2 API bookings landing `ACCEPTED` with no calendar sync and no emails. **The lesson: making the column nullable is only half the work.** The row needs a sync state, and *every* path touching Google — reschedule, cancel, series patch — has to treat "no reference yet" as a normal case. Cal.com's bug is precisely that missing second half.
- **Delete-protection gaps across Tutor/Schedule/BookingLink/Booking** — surfaced while designing the `BookingSeries` lifecycle-field cleanup. The link and the tutor turned out to need genuinely different treatment (one's a factory a booking is generated from, the other's an ongoing identity referenced from many tables), settled as follows. **All of these are now fixed** — the sub-items below are marked individually.
  **Original (pre-fix) behavior, kept for reference**: none of it was silently correct, but the *way* each one broke differed. `Tutor` and link deletion were both accidentally-blocked-but-ugly: no app-level check existed, so if any referencing `Schedule`/`Booking`/`BookingSeries` row existed, Postgres itself rejected the delete with a raw, unhandled `IntegrityError` (500 crash) rather than a clean 409 — it didn't go through, but it failed loud and ugly instead of failing cleanly. `Schedule` deletion was the opposite and more dangerous: the availability junction's `schedule_id` has `ondelete="CASCADE"`, so deleting a schedule still linked to a link **succeeded silently** — no crash, no warning, the junction row just vanished and that link quietly lost its availability. Two things failing loud when they shouldn't have run at all, and one succeeding silently when it should have been blocked.
  - **Link deletion design — see Priority Order item 6 above, and CLAUDE.md's "BookingLink data model" for the full reasoning.** Short version: **archive only, no hard delete at any child count** (`status='archived'` + `archived_at`, permanent in behavior, no restore). The row never leaves, which keeps `booking_link_id` NOT NULL and non-dangling — that's both the *source* facet and, more importantly, the handle you filter and **bulk-reassign** with to rescue stranded bookings. An archived link's calendar rules go **inert** (URL 404s, reschedule 404s), which is what makes read-only coherent. Series generation is *not* affected by any link status — a series generates from its own row, never the link. Five earlier drafts are superseded — RESTRICT-on-active-references; freely-deletable-with-frozen-`type`-strings; soft-delete justified by "rules must resolve forever"; hard-delete-at-zero-children plus `ON DELETE SET NULL`; and archive-with-the-slug-burned-forever. Kept as a pointer only, so this doesn't drift out of sync again.
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
    `DELETE /schedules/{id}` (`routers/schedules.py`) now 409s if any `BookingLinkAvailability` row
    still references the schedule, same shape as the existing `is_default` guard.
  - ~~Admin permanent-delete endpoint for `BookingSeries` didn't exist~~ — done.
    `DELETE /booking-series/{id}/permanent` (`routers/bookings.py`), `cascade` param, modeled
    directly on `Booking`'s `.../permanent` endpoint — same predecessor-chain confirm flow, walks
    `rescheduled_to`, deletes each series' `Booking` rows and Google Calendar master event. No
    frontend wiring yet (backend-only this pass).
- **`BookingSeries`'s full iCalendar (RFC5545) field mapping — deliberate per-field, not an oversight if something looks missing.** Full design: `.claude/plans/done-booking-series-recurrence-fields-and-event-type-denormalization.md`.
  - Kept, all implemented: `created`/`last_modified`/`dtstart`/`dtend`/`until`/`status`, plus `freq` (`'WEEKLY'` only), `interval` (`1` only) and `count` (nullable, stamped from `BookingLink.count`). `freq`/`interval` are constrained to what generation is tested for by `models.FREQ_DAYS` / `SUPPORTED_INTERVALS` and CHECK constraints — widening a constant is most of the change when biweekly or daily lands, except in `available_slots`, whose `WeekdayTime` bands still read existing series as weekly. `count` and `until` are mutually exclusive (RFC5545).
  - Comment-only, not real columns: `byday`, `wkst` — redundant with `dtstart.weekday()` today, would need to be array-typed to ever matter.
  - Skipped: `dtstamp` (generation-time fact, not persisted), `duration` (redundant with the `duration` property), `summary`/`description` (planned as the resolved type label plus a generated `title` on the row itself — see item 6c and the booking-titles entry below), `location` (no concept exists yet).
  - **This field-mapping pass only covered `BookingSeries` — `Booking` (the occurrence level) never got the same treatment and is missing `created`/`last_modified` entirely.** Confirmed: `Booking` has no `created`/`last_modified` columns at all today, unlike `BookingSeries` which has both (`onupdate=func.now()`, already verified working correctly). `Booking.start`/`.end` intentionally keep their own names rather than becoming `dtstart`/`dtend` — that's not an oversight, `dtstart`/`dtend` specifically means "naive local time-of-day describing a recurring pattern" (see `ScheduleDay`), whereas `Booking.start`/`.end` are absolute UTC datetimes for one concrete occurrence, a genuinely different concept. Fix: add `created`/`last_modified` to `Booking`, same pattern as `BookingSeries`.
- **`BookingSeries.status` uses `NULL` to mean "active" — should be an explicit value instead, matching `Booking.status`/`BookingRequest.status`.** Both of those are `nullable=False` with an explicit default (`"confirmed"`/`"pending"`); only `BookingSeries.status` (`nullable=True`, `null` = active) does it differently, for no clear reason other than history. This isn't just a style inconsistency — it already caused a real bug: `active_series_filter`'s original `status.notin_(['cancelled', 'rescheduled'])` silently evaluated to SQL `NULL` (not `TRUE`) for a fresh series, excluding it, until explicitly special-cased with `OR status IS NULL`. Fix: `nullable=False`, explicit default (`"confirmed"` or `"active"`, matching whichever label reads better next to `'cancelled'`/`'rescheduled'`). Needs a real migration (backfill existing `NULL` rows to the explicit value before flipping `nullable=False`) and updating every `status == None`/`status IS NULL` check in `booking_utils.py`/`routers/bookings.py` to compare against the explicit string instead.
  - **Whatever the "active" label ends up being (`"confirmed"`/`"active"`/etc), annotate it clearly in the model that this value alone does NOT mean the series is currently ongoing** — it only means the series hasn't been explicitly cancelled or rescheduled. Whether it's *actually* ongoing right now is a separate question answered by comparing `until` against the current date. `is_series_active`/`is_active` is the union of both facts: `status` is `"confirmed"` (not cancelled/rescheduled) AND `until` is null or in the future → active/ongoing; `status` is `"confirmed"` AND `until` is in the past → naturally finished. The column name and a comment should make this two-part relationship obvious to whoever next reads the model, not just to whoever remembers this conversation.
- ~~`_ensure_occurrence`/`extend_single_series` don't check `until`/`status` before materializing an occurrence~~ — done. `_ensure_occurrence` now checks `status`/`until` itself before materializing (closes the gap for every caller, not just the ones that pre-check). `extend_single_series` also checks `is_series_active` and returns cleanly (no exception) if the series became inactive since being enqueued — otherwise Procrastinate would see a failed job and retry it forever against a permanent state.
- **Policy edits deliberately do *not* backfill — the old three-target backfill design is dropped.** Policy is frozen onto each `Booking`/`BookingSeries` at creation, and under the `BookingLink` model nothing edited on the link or on `Settings` ever reaches an existing row. Editing either changes what *future* bookings resolve to, and nothing else. The trade, stated plainly: tightening your business default will not reach already-booked future sessions — getting it to them is a deliberate, visible act (per-row edit, or the backlogged series-scoped edit, or the deferred admin multi-select bulk tool), never an invisible cascade. That's the correct default for a promise made to a client, and it removes the sharpest edge of the old design: the backfill had to update the `BookingSeries` row itself and not just its materialized `Booking`s, because `_ensure_occurrence` copies fields **off the series row** — miss that one target and every occurrence Procrastinate generates from the next day onward silently reverts to the old policy, forever. That trap now only exists inside the backlogged series-scoped edit's `following`/`all` scopes, where it's local and testable rather than global.
- **Pending `BookingRequest` approval has no re-validation at approve time** — `approve_pending_request` (`routers/bookings.py`) sets `status='approved'` and calls straight into `_cancel_booking`/`_reschedule_booking`/`_cancel_series`/`_reschedule_series` with no re-check that: (a) the underlying booking/series is still in a valid state (`Booking.status` still `confirmed` / series still active per `is_series_active` — could have changed via another path while the request sat pending), (b) the original booking's start time hasn't already passed (the same `minutes_until <= 0` floor the direct-action routes enforce via `get_cancel_action`/`get_reschedule_action` is never re-run here), or (c) for reschedule requests, that `requested_start` itself hasn't already passed into the past by approval time. No expiry mechanism exists either — `BookingRequest.created_at` is set but nothing reads it, no cron denies stale requests. Fix: re-run the floor check (and the active-series check) inside `approve_pending_request` before invoking the saga; reject/require-reconfirmation if the original event or requested new time is no longer in the future; consider a UI-visible "stale" indicator for pending requests whose window has passed.
- **`only_show_first_slot`** — stored and returned by `BookingLink` but ignored in `get_available_slots`. Fix: after generating full slot set, group by `(tutor_id, date)` and keep only the earliest per group.
- **`limit_future_bookings_days`** — stored but not applied. Fix: cap `time_max = min(time_max, now + timedelta(days=N))` before slot generation.
- **`buffer_minutes`** — stored but not applied. Fix: expand busy overlap check to `[slot_start - buffer, slot_end + buffer]`.
- **`limit_per_day / per_week / per_month`** — stored but not applied in `get_available_slots`. Fix: count existing bookings per tutor per period and exclude saturated slots.
- **`limit_per_booker`** — stored but not enforced in `create_booking`. Fix: count existing bookings for `(booking_link_id, student_email)` and reject if at limit.
- ~~**Slug field on `BookingLink`**~~ — **folded into item 6a**, not a standalone backlog item. Unique among **active** links only (partial index), released on archive, 400 on collision, replaces `EventType.name`. The route becomes `/book/:slug` in the same pass that already rewrites it. Open sub-question when building: whether the slug is derived live from what the admin types with an override, or typed independently.
- **Surface the manage link on BookingPage's "done" step** — `ManageOccurrence.tsx`/`ManageSeries.tsx` and their `public_id`-keyed endpoints already exist and work (see Booking system above), but the confirmation ("done") screen on `BookingPage.tsx` doesn't yet show/link the customer to their own manage-occurrence/manage-series page after booking.
- **Google Calendar attendees** — IMPORTANT: current "Add to calendar" buttons on BookingPage create a standalone unlinked copy in the student's calendar. Correct fix: add `attendees: [{"email": student_email or parent_email}]` to the event body in `create_booking`, with `sendUpdates="all"` on all API calls. Google then auto-invites, auto-updates, and auto-cancels on the student's end. Remove the manual "Add to calendar" buttons once this is live.
- **Calendar abstraction layer** — Google Calendar is currently hardcoded throughout. Goal: extract all Google API calls into `GoogleCalendarService(CalendarService)` with an abstract base class (`create_event`, `update_event`, `delete_event`, `get_freebusy`, `move_event`). Booking router injects the service and never references Google directly. Also rename `Booking.google_event_id` → `external_event_id` (requires DB migration).
- **Per-occurrence `google_event_id` currently a copy-of-series marker, not a real instance id** — for a normal (never individually rescheduled) occurrence, `Booking.google_event_id` is just set equal to `series.google_event_id` (the master RRULE event's id). `_cancel_booking`/`_reschedule_booking`/`_reschedule_series` all compare `booking.google_event_id != series.google_event_id` to detect whether an occurrence is an "exception" (individually modified before) vs. normal, and for normal occurrences do a live `events().instances(timeMin=, timeMax=)` lookup to find the real per-instance id before acting. Google Calendar recurring events have a documented deterministic per-instance id format (`{recurringEventId}_{yyyyMMdd'T'HHmmss'Z'}`, the original UTC start time) — same mechanism as this app's own `public_id` composite refs. Idea: store the real (computed or fetched) instance id on `Booking.google_event_id` at materialization time instead of the series copy, which would let the `is_exception` branching and live `events().instances()` lookup in all three action functions be deleted entirely — always just act on whatever's stored. Deliberately not done yet: needs verifying the documented format against this app's actual Calendar behavior first (this touches live calendar-mutating code, not just reads), and eagerly fetching at materialization time would mean *every* occurrence pays an API call instead of only the ones someone actually reschedules/cancels — worth checking that computing the id locally (zero extra calls) is reliable before switching.
- **GET /tutors `?ids=`** — filter endpoint to fetch only specific tutor IDs. Avoids loading all tutors on BookingPage.
- ~~`GET /booking-series` needs `tutor_ids`/`booking_link_ids` filter support~~ — done. Both endpoints now share `apply_scope_filters` (`booking_utils.py`), see CLAUDE.md's "Filtering / facets" note.
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
- **Pagination is "regenerate more and slice," not a real cursor** — full analysis (algorithmic cost, the resource-exhaustion gap it also causes, and the chosen fix) now lives in CLAUDE.md's Pagination section and `.claude/plans/done-cursor-pagination-and-endpoint-split.md` — see "Priority Order" above, item 2.

### Frontend
- **Realtime validation in LinkPage** — each field still gates re-validation behind a `touched.<field>` check (only calls `setErrors(validate(form))` if the field was already touched); call it unconditionally on change/blur instead for immediate feedback on first interaction too.
- **BookingPage custom duration** — if `bookingLink.allow_custom_duration`, show duration slider/NumberInput on contact step (between min and max). `selectedDuration` state, defaults to `min_duration_minutes`. `end = new Date(slot.start + selectedDuration * 60000)`.
- ~~**Slug field in LinkPage**~~ — done in 6a. Live `slugify` as you type, frozen `host/book/` prefix, copy button.
- **Links/Availability pattern divergence** — `Links.tsx` moved to a separate routed page (`LinkPage.tsx`, tab-based) while `Availability.tsx` still uses the older inline card-swap-to-form pattern. Worth a deliberate decision on whether to bring Availability in line with the routed-page pattern, or leave them intentionally different — currently just an artifact of when each was last touched, not a designed choice.

## Future Features to Evaluate

⭐ = high priority for TMS. Sources vary — several below were observed from Cal.com; where an idea
comes from a specific product, the entry says so.

- **Business-wide default policy, with per-link override.** Today cancel/reschedule policy is set per booking link and nowhere else, so a business with one house rule ("we require 24 hours notice") has to restate it on every link and keep them in sync by hand. Cancellation policy is genuinely a *business* policy — it's what you tell clients on your site and in intake paperwork — so per-link is better understood as the exception (a free intro consult is flexible, a 10-session package is strict) than as the primary axis. Add the four fields (`cancel_mode`, `cancel_notice_minutes`, `reschedule_mode`, `reschedule_notice_minutes`) to the `Settings` singleton as the default, and let a link inherit unless it overrides.
  - **Use an explicit `'inherit'` mode value rather than overloading `NULL`.** `NULL` already means `auto`; making it mean "inherit" instead would silently change the semantics of every existing link, needing `Settings` seeded with `auto` for it to be a no-op. An extra value in `_ALL_MODES_SQL` avoids all of that — no migration, nothing to reason about, and `cancel_mode = 'inherit'` reads honestly in the API and in queries where `NULL` doesn't. Cost is one enum value and one branch in `policy.py`.
  - **Override granularity should be per-pair**: `(cancel_mode, cancel_notice_minutes)` and `(reschedule_mode, reschedule_notice_minutes)` each inherit or override as a unit, keyed off the mode. Prevents incoherent mixes ("inherit `auto` mode but override the notice window" is meaningless — `auto` has no window) while keeping cancellation and rescheduling independent, so "default cancellation, but this package can't be rescheduled at all" still works.
  - **Resolve once, at booking creation**, where policy is already frozen onto `Booking`/`BookingSeries`. The two-level lookup collapses in a single helper at that one point and no hot path ever does it. Editing the default reaches future bookings only — never anything already sold.
  - **UI: Availability → Global Scheduling Limits, tabbed — not a new Settings page.** Acuity groups exactly this under Availability alongside Calendars, with Time Zone as a sibling tab and the subtitle *"These settings apply account-wide unless otherwise specified on the calendar-specific scheduling limits."* Grouping by domain beats a generic Settings bucket, and there's already an Availability page. `business_timezone` belongs there too — it currently has no UI at all. The link editor's Cancellation tab then needs inheritance made visible (`( • Business default — 24h notice ) ( Custom )`), or people will set it per-link every time and the default earns nothing.
  - Natural time to also wire two of the dead limit fields, which Acuity shows on the same screen and which share this exact shape: `min_lead_time` (their "Minimum Hours") and `limit_future_bookings_days` (their "Maximum Days").
  - **Not a `Policy` table** — see Decisions. A `Settings` default gives edit-once-applies-everywhere without a table, a route, or copy-vs-share semantics. Named policies (Airbnb's Flexible/Moderate/Strict shape) only start earning their keep with several *groups* of links sharing a non-default policy, and migrating to them stays cheap because policy freezes onto bookings anyway — group links by identical tuples, create a row per distinct tuple, repoint.

- **"Changes" tab (low priority)** — repurpose/extend the Requests tab into a general changes-for-a-time-period view: pending requests as one category, plus cancellations and reschedules. Fully queryable off data that already exists, no new audit table or timestamp needed: for a window `[time_min, time_max]`, cancelled items show under the week of their own `start`/`dtstart` (`status='cancelled' AND start BETWEEN ...`); rescheduled items show under *both* the old row's week (`status='rescheduled' AND start BETWEEN ...`, "moved away," pointing forward via `rescheduled_to`) and the new row's week (`rescheduled_from IS NOT NULL AND start BETWEEN ...`, "arrived here," pointing back via `rescheduled_from`) — same item appears twice only if the move actually crossed a week boundary. Smaller than it first looks — new query filters against existing columns, not a new feature's worth of schema.
- ⭐ **Admin calendar view** — use `react-big-calendar` (MIT, fully free, unstyled — plays well with Tailwind) to render bookings as Google Calendar-style events on the admin bookings page. Month/week/day views, click event → detail panel with actions. Also overlay confirmed bookings as greyed blocks on the public BookingPage slot picker. Add after Milestone 1 recurring materialization is stable. Preferred over FullCalendar which has opinionated styles that fight Tailwind.

- ⭐ **Booking questions / custom fields** — per-event-type dynamic form fields (text, phone, notes, etc.) shown to the booker at booking time; each field configurable as required/optional/hidden
- ⭐ **Requires confirmation** — manual approval step before a booking is confirmed; booker sees "pending" state until host accepts; requires `status` column on Booking
- ⭐ **Email notifications** — automated confirmation, reminder, and cancellation emails to booker and tutor; likely via a Workflows/queue system
- **Require cancellation reason** — prompt booker for a reason when cancelling; store on the booking record
- **Redirect on booking** — send booker to a custom URL after successful booking (e.g. payment page, onboarding form)
- **Optimized slots** — prefer slot arrangements that consolidate bookings and minimize tutor gaps
- **Lock timezone on booking page** — force a specific timezone (useful for in-person events)
- **Offer seats / group bookings** — allow N bookings per slot for group lessons; `max_attendees` column on `BookingLink`
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
  - **First rejection**: it was introduced to make `EventType` freely deletable (policy couldn't live on a row that might vanish). The premise dissolved from the other end — links are soft-deleted only, so the row never vanishes and nothing needs rescuing off it. (The *reason* given at the time, that reschedule must read the link's rules forever, was itself wrong; see the cleanup-job entry below. The conclusion held anyway.) No tension left for a table to relieve.
  - **Briefly revived** for the one thing that genuinely justified it independently: reusing a policy across event types (edit "24h notice" once, applies to five).
  - **Dropped again** once the `Settings`-default model landed, which delivers that same edit-once-applies-everywhere without a table, a `/policies` route, or copy-vs-share and fork semantics. A business-level default with per-link overrides is also how practice-management software (Jane, SimplePractice, Acuity) and hospitality actually model it — Cal.com/Calendly keep it per-event-type only, but their policy support is thin enough not to be useful precedent.
  - **What would change the answer**: policies that need to be *named, listed, and independently managed* as first-class objects — many distinct policies applied in overlapping combinations, more than a single default plus scattered exceptions can express.
- **Procrastinate cleanup job to reclaim retired `BookingLink` rows once all their bookings are in the past — rejected, but the original reasoning was wrong and has been replaced.**
  - **The idea**: soft-deleted links accumulate forever; reclaim one once every booking pointing at it has passed. Originally framed against a `CalendarRules` table (buffer/limits/interval extracted off `EventType`); that table no longer exists — slot rules live on the link itself.
  - **The original argument for rejecting it is dead.** It ran: admins reschedule *past* bookings through the slot picker, so a retired link's rules must resolve forever, so deleting them is function-breaking. That premise is gone — admin moves are direct `dtstart`/`dtend` edits, not trips through the picker (CLAUDE.md, "Two entry points, two rule regimes"), and a deleted link's rules are **inert** by design. Nothing reads them. Do not re-derive the rejection from that reasoning.
  - **Why it's still rejected, on the surviving grounds**: `booking_link_id` is NOT NULL and is the **source facet** key. Reclaiming a link row breaks referential integrity outright, and even with a nullable FK it would silently destroy the provenance of every booking that came from it — "everything that came from that retired link" stops being answerable. The row isn't retained for its rules (those are inert); it's retained because things point at it and that pointing is load-bearing.
  - **Why the storage argument doesn't hold.** Row count is bounded by *how many links have ever been retired* — not by booking volume or traffic. At ~100 bytes/row, a thousand retired links over a decade is ~100KB. Rounding error, not a problem to solve.
  - **General principle this reflects**: cleanup jobs are for data that grows proportionally to *activity* (logs, sessions, webhook deliveries, event streams) — that genuinely runs away. Config/reference data is kept indefinitely, because something still points at it and it doesn't scale with traffic. Same reason Stripe retains old prices so historical invoices resolve, and Shopify keeps deleted products so past orders still render.
  - **What would change the answer**: nothing short of dropping the source facet entirely and making `booking_link_id` nullable — i.e. deciding provenance isn't worth keeping. Not planned; it's half the grouping model.
  - **The one safe variant, if a cleanup is ever wanted anyway**: delete a link row only when *zero* bookings reference it at all — i.e. its bookings were themselves hard-deleted via the permanent-delete escape hatches. That's the standard orphan-cleanup pattern and can't break anything by construction. In practice it will almost never fire, which is the correct outcome.
