from sqlalchemy import Column, Integer, String, Float, Numeric, Boolean, Date, Text, ForeignKey, UniqueConstraint, CheckConstraint, Time, DateTime, Index, text, func
from sqlalchemy.orm import relationship, backref
from database import Base
from datetime import datetime, timedelta, UTC
from uuid import uuid4

# todo: factor out repeated CheckConstraint shapes into helpers (ie the amount/unit trio written
# out twice for rate/rate_unit and price/price_unit)


class Contact(Base):
    """A person. Created by the booking that names them, never by signing up.

    Everyone is here: the payer, the attendee, and the adult who is both. Guest bookings create
    contacts like any other, which is what makes the client list complete and makes "registering"
    later a single write rather than a string-matched backfill. Staff are NOT here. Tutor is its own
    table with its own permission model.
    """
    __tablename__ = "contacts"
    # The roster's default ordering. Without it every page of a large practice's client list does a
    # filesort over the whole table.
    __table_args__ = (Index("ix_contacts_name", "first_name", "last_name"),)

    id = Column(Integer, primary_key=True, index=True)
    created = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # Nullable because a child attendee has no address of their own. Postgres and SQLite both treat
    # NULLs as distinct under UNIQUE, so many contacts share the null without a partial index.
    # Stored lowercased and stripped. Plus-addressing is left intact, jane+x@ is a different address.
    email = Column(String, nullable=True, unique=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    phone = Column(String, nullable=True)
    # Set once someone proves control of the inbox. Nothing writes it yet, auth will. Until then
    # every contact is unverified, so the profile is last-write-wins from the newest booking.
    verified_at = Column(DateTime(timezone=True), nullable=True)

    # Every stint, newest first. passive_deletes: let the DB cascade instead of SQLAlchemy nulling
    # the children's FK first. foreign_keys: Enrollment points here twice, contact_id and payer_id.
    enrollments = relationship(
        "Enrollment", back_populates="contact", passive_deletes=True,
        foreign_keys="Enrollment.contact_id",
        order_by="Enrollment.started_on.desc()",
    )

    @property
    def current_enrollment(self):
        """The open stint, if they're a client right now. At most one — see the partial index."""
        return next((e for e in self.enrollments if e.ended_on is None), None)


class ContactManager(Base):
    """Who may book for whom. A link table, so two parents can share a child and one parent can have
    several. Nobody needs a row pointing at themselves, being yourself is not a grant.
    """
    __tablename__ = "contact_managers"
    __table_args__ = (
        UniqueConstraint("manager_id", "managed_id", name="uq_contact_manager_pair"),
        CheckConstraint("manager_id <> managed_id", name="chk_contact_manager_not_self"),
    )

    id = Column(Integer, primary_key=True, index=True)
    manager_id = Column(Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True)
    managed_id = Column(Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True)

    manager = relationship("Contact", foreign_keys=[manager_id])
    managed = relationship("Contact", foreign_keys=[managed_id])


# A rate belongs to a person, a price to an offering. PRICE_UNITS is narrower because a per_month
# amount means nothing as a per-booking price; enforced where a link's price is set.
RATE_UNITS = ("per_session", "per_hour", "per_month")
PRICE_UNITS = ("per_session", "per_hour")
_RATE_UNITS_SQL = str(RATE_UNITS)
_PRICE_UNITS_SQL = str(PRICE_UNITS)


class Price(Base):
    """One amount at one unit. Immutable — a change inserts a new row and repoints referrers.

    Referrers point at the row in effect when they were created, so a raise can't reprice a past
    session, and anyone left on the old row is grandfathered. Deduped by (amount, unit) so clients
    on the same price share a row, which is what makes a bulk change one UPDATE. No label: naming
    versions needs a second table, and nothing needs the name yet.
    """
    __tablename__ = "prices"
    __table_args__ = (
        CheckConstraint(f"unit IN {_RATE_UNITS_SQL}", name="chk_price_unit"),
        CheckConstraint("amount >= 0", name="chk_price_non_negative"),
        # Live rows only — superseded prices may collide freely.
        Index("uq_price_amount_unit_live", "amount", "unit", unique=True,
              sqlite_where=text("archived_at IS NULL"), postgresql_where=text("archived_at IS NULL")),
    )

    id = Column(Integer, primary_key=True, index=True)
    amount = Column(Numeric(10, 2), nullable=False)
    unit = Column(String, nullable=False)
    # Hides a superseded price from pickers. The only column here ever updated.
    archived_at = Column(DateTime(timezone=True), nullable=True)
    created = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Enrollment(Base):
    """One stint of being a client here. Many per contact, at most one open.

    Clients leave for the summer and come back, sometimes at a new rate, so this is a relationship
    over time rather than a property of the person — employment records, not a subclass. Terms sit on
    the stint so a past one knows what it charged. The open stint is edited in place; a rate change
    doesn't fork it. New row only when someone actually left and came back.
    """
    __tablename__ = "enrollments"
    __table_args__ = (
        # One open stint per contact. Partial, so closed ones pile up freely.
        Index("uq_enrollment_open_per_contact", "contact_id", unique=True,
              sqlite_where=text("ended_on IS NULL"), postgresql_where=text("ended_on IS NULL")),
        CheckConstraint("ended_on IS NULL OR ended_on >= started_on", name="chk_enrollment_dates_order"),
    )

    id = Column(Integer, primary_key=True, index=True)
    contact_id = Column(Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True)
    started_on = Column(Date, nullable=False)
    # Null means current. Replaces an is_active flag — the date derives the boolean, and two fields
    # for one fact drift apart.
    ended_on = Column(Date, nullable=True)

    # Null = enrolled, no terms agreed yet; bookings fall back to the link's price meanwhile.
    rate_id = Column(Integer, ForeignKey("prices.id"), nullable=True, index=True)
    # Who gets the invoice; null means they pay for themselves. Not contact_managers, which grants
    # permission to book — a grandparent can pay while a parent books, and two parents on one child
    # leave that table with no way to pick. Per stint, so it can change year to year.
    payer_id = Column(Integer, ForeignKey("contacts.id"), nullable=True, index=True)

    # Here rather than on Contact because they're only ever collected for a student, never a payer.
    grade = Column(Integer, nullable=True)
    birthday = Column(Date, nullable=True)

    rate = relationship("Price")
    contact = relationship("Contact", foreign_keys=[contact_id], back_populates="enrollments")
    payer = relationship("Contact", foreign_keys=[payer_id])
    lessons = relationship("Lesson", back_populates="enrollment")

    @property
    def is_active(self) -> bool:
        return self.ended_on is None


class Tutor(Base):
    __tablename__ = "tutors"
    __table_args__ = (UniqueConstraint('first_name', 'last_name', name='uq_tutor_name'),)

    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    pay_rate = Column(Numeric(10, 2), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    calendar_id = Column(String, nullable=True)
    check_calendar_conflicts = Column(Boolean, nullable=False, default=False)

    lessons = relationship("Lesson", back_populates="tutor")
    schedules = relationship("Schedule", back_populates="tutor", passive_deletes=True)
    bookings = relationship("Booking", back_populates="tutor")
    availability = relationship("BookingLinkAvailability", back_populates="tutor", passive_deletes=True)
    series = relationship("BookingSeries", back_populates="tutor")


class Lesson(Base):
    __tablename__ = "lessons"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, nullable=False)
    hrs = Column(Float, nullable=True)
    fee = Column(Numeric(10, 2), nullable=False)
    is_fee_overridden = Column(Boolean, nullable=False, default=False)
    tutor_payout = Column(Numeric(10, 2), nullable=False)
    is_tutor_payout_overridden = Column(Boolean, nullable=False, default=False)
    pay_status = Column(Boolean, nullable=False, default=False)
    notes = Column(Text, nullable=True)

    enrollment_id = Column(Integer, ForeignKey("enrollments.id"), nullable=False)
    tutor_id = Column(Integer, ForeignKey("tutors.id"), nullable=False)
    booking_id = Column(Integer, ForeignKey("bookings.id"), nullable=True)  # set by Sunday scheduler when lesson is generated from a booking

    enrollment = relationship("Enrollment", back_populates="lessons")
    tutor = relationship("Tutor", back_populates="lessons")
    booking = relationship("Booking", back_populates="lesson")


class ScheduleDay(Base):
    __tablename__ = "schedule_days"

    id = Column(Integer, primary_key=True, index=True)
    schedule_id = Column(Integer, ForeignKey("schedules.id", ondelete="CASCADE"), nullable=False)
    day_of_week = Column(Integer, nullable=False)  # 0=Monday, 6=Sunday
    # stored as local time — intentionally NOT converted to UTC. Time-only fields have no date, so UTC offset
    # is indeterminate across DST transitions (e.g. "4pm EST" = 21:00 UTC in winter, 20:00 UTC in summer).
    # conversion happens at query time using the actual occurrence date + Schedule.timezone.
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)

    schedule = relationship("Schedule", back_populates="days")

class Schedule(Base):
    __tablename__ = "schedules"
    __table_args__ = (UniqueConstraint("tutor_id", "name", name="uq_schedule_tutor_name"),)

    id = Column(Integer, primary_key=True, index=True)
    tutor_id = Column(Integer, ForeignKey("tutors.id", ondelete="CASCADE"), nullable=False)  # CASCADES only if other tutor 
    name = Column(String, nullable=False) # e.g. "Regular Hours", "Summer Hours"
    is_default = Column(Boolean, nullable=False, default=False) # if true, this is the default schedule for a new event type
    # TODO: redundant — always equals Settings.business_timezone. Drop and load from Settings everywhere.
    timezone = Column(String, nullable=True)

    tutor = relationship("Tutor", back_populates="schedules")
    availability = relationship("BookingLinkAvailability", back_populates="schedule", passive_deletes=True)
    days = relationship("ScheduleDay", back_populates="schedule", cascade="all, delete-orphan", passive_deletes=True)


#todo: consider making duration variable (1hr, 1.5hr, 2hr) instead of fixed 1hr, which would allow for more flexible scheduling

_WINDOW_MODES_SQL = "('auto_window_block', 'auto_window_request', 'request_window')"
_ALL_MODES_SQL = "('blocked', 'auto', 'auto_window_block', 'auto_window_request', 'request', 'request_window')"
# Series-level actions: no notice window — there's no instant to measure it against.
_SERIES_MODES_SQL = "('blocked', 'auto', 'request')"
# active   — bookable; calendar rules live and editable
# paused   — not bookable; rules stay live and editable, existing bookings still reschedule. Reversible.
# archived — not bookable; rules inert, row read-only. Terminal, no restore.
#
# None of these touch an existing BookingSeries: a series is its own booking template and generates
# occurrences from its own row (see _ensure_occurrence), never from the link. The only thing a link's
# status governs is whether customers can get *slots* from it — new bookings, and reschedules.
_LINK_STATUSES_SQL = "('active', 'paused', 'archived')"

# iCal recurrence vocabulary, shared by BookingLink (which configures it) and BookingSeries (which
# is stamped with it). Days between occurrences is `interval * FREQ_DAYS[freq]` — every occurrence
# walk derives its step from that, so nothing hardcodes a week.
# TODO: DAILY and MONTHLY are planned; add them here once their generation paths are covered.
FREQ_DAYS = {"WEEKLY": 7}
# Only 1 is offered today. Biweekly and beyond are planned and the arithmetic already handles any N
# — this tuple is what stops an untested value being stored.
# TODO: widen as each interval gets tested end to end.
SUPPORTED_INTERVALS = (1,)


class BookingType(Base):
    """A name and a color for the kind of thing a booking is. Purely a label — nothing branches on it.

    A link points at one live (the kind it stamps); bookings and series point at one frozen at
    creation. Pointing rather than copying the string is what makes a rename a correction: editing
    this row relabels every booking pointing at it, past included, instead of splitting the group
    into old-name and new-name buckets. Managed entirely from the type picker — no page of its own.
    """
    __tablename__ = "booking_types"

    id = Column(Integer, primary_key=True, index=True)
    label = Column(String, nullable=False, unique=True)
    color = Column(String, nullable=True)  # hex; null = neutral


class BookingLink(Base):
    """A factory bookings are generated from.

    Calendar rules on it (duration, buffers, limits, interval, availability) are read LIVE on every
    slot computation, including a customer rescheduling an existing booking — so the row must always
    resolve, which is why archive is the only delete. Wiring it stamps onto a booking is frozen at
    creation and never propagates. See CLAUDE.md's "BookingLink data model".
    """
    __tablename__ = "booking_links"
    __table_args__ = (
        CheckConstraint(
            f"cancel_mode IN {_ALL_MODES_SQL}",
            name="chk_booking_link_cancel_mode"
        ),
        CheckConstraint(
            f"reschedule_mode IN {_ALL_MODES_SQL}",
            name="chk_booking_link_reschedule_mode"
        ),
        CheckConstraint(
            f"series_cancel_mode IN {_SERIES_MODES_SQL}",
            name="chk_booking_link_series_cancel_mode"
        ),
        CheckConstraint(
            f"series_reschedule_mode IN {_SERIES_MODES_SQL}",
            name="chk_booking_link_series_reschedule_mode"
        ),
        CheckConstraint(
            f"cancel_mode NOT IN {_WINDOW_MODES_SQL} OR (cancel_notice_minutes IS NOT NULL AND cancel_notice_minutes > 0)",
            name="chk_booking_link_cancel_notice_required"
        ),
        CheckConstraint(
            f"reschedule_mode NOT IN {_WINDOW_MODES_SQL} OR (reschedule_notice_minutes IS NOT NULL AND reschedule_notice_minutes > 0)",
            name="chk_booking_link_reschedule_notice_required"
        ),
        CheckConstraint(
            f"status IN {_LINK_STATUSES_SQL}",
            name="chk_booking_link_status"
        ),
        CheckConstraint(
            f"freq IN ({', '.join(repr(f) for f in FREQ_DAYS)})",
            name="chk_booking_link_freq",
        ),
        CheckConstraint("count IS NULL OR expires_on IS NULL", name="chk_booking_link_not_both_count_and_until"),
        CheckConstraint("NOT (booker_can_set_recur_until AND booker_can_set_count)", name="chk_booking_link_one_booker_override"),
        CheckConstraint("expires_on IS NULL OR NOT (booker_can_set_recur_until OR booker_can_set_count)", name="chk_booking_link_fixed_date_no_override"),
        CheckConstraint("count IS NULL OR NOT booker_can_set_recur_until", name="chk_booking_link_count_override_is_count"),
        CheckConstraint(
            f"interval IN ({', '.join(str(i) for i in SUPPORTED_INTERVALS)})",
            name="chk_booking_link_interval",
        ),
        # Slug is unique among ACTIVE links only — archiving releases the name for reuse. Both
        # Postgres and SQLite support partial indexes, so tests and prod agree.
        Index(
            "uq_booking_link_slug_active", "slug", unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    status = Column(String, nullable=False, default="active")
    archived_at = Column(DateTime(timezone=True), nullable=True)  # audit metadata; nothing branches on it
    # Governs one occurrence. Copied onto each Booking/BookingSeries at creation, never propagated after.
    cancel_mode = Column(String, nullable=False, server_default="auto")  # see _ALL_MODES_SQL
    cancel_notice_minutes = Column(Integer, nullable=True)  # only set for the window modes
    reschedule_mode = Column(String, nullable=False, server_default="auto")
    reschedule_notice_minutes = Column(Integer, nullable=True)
    # Governs acting on a whole series. No notice window — see _SERIES_MODES_SQL.
    series_cancel_mode = Column(String, nullable=False, server_default="auto")
    series_reschedule_mode = Column(String, nullable=False, server_default="auto")
    #basic info
    slug = Column(String, nullable=False)  # public URL only; uniqueness enforced by the partial index above
    # The kind this link stamps onto what it generates. Live — editing it reaches future bookings
    # only. Two links may share a type; that's how their bookings group together.
    booking_type_id = Column(Integer, ForeignKey("booking_types.id", ondelete="SET NULL"), nullable=True)
    description = Column(String(500), nullable=True)  # bounded — see DESCRIPTION_MAX_LENGTH in schemas
    duration_minutes = Column(Integer, nullable=False)
    min_duration_minutes = Column(Integer, nullable=True)  # null = fixed duration; 0+ = custom duration on
    max_duration_minutes = Column(Integer, nullable=True)
    # recurrence
    recurring = Column(Boolean, nullable=False, default=True)
    # The recurrence shape this link stamps onto every series it creates. Only WEEKLY/1 is offered
    # today — DAILY, MONTHLY and biweekly are planned, and the generation arithmetic already
    # handles them; the CHECKs and the schema Literal are what hold the line until each is tested.
    freq = Column(String, nullable=False, default="WEEKLY")
    interval = Column(Integer, nullable=False, default=1)
    count = Column(Integer, nullable=True)             # mutually exclusive with expires_on; N occurrences, stamped onto BookingSeries.count
    expires_on = Column(Date, nullable=True)           # mutually exclusive with count; booker_can_set_recur_until must be false, all series from this type end on this date
    # Booker picks the end instead of the admin. Exclusive with each other; each only valid for its
    # own form — until with an indefinite link, count with a count link.
    booker_can_set_recur_until = Column(Boolean, nullable=False, default=False)
    booker_can_set_count = Column(Boolean, nullable=False, default=False)
    #optional advanced limits
    # Charged when the attendee has no rate of their own, and for a monthly client's extras. Live —
    # edits reach later bookings only; existing ones froze their own pointer.
    price_id = Column(Integer, ForeignKey("prices.id"), nullable=True, index=True)
    # limits 
    buffer_minutes = Column(Integer, nullable=True)
    limit_duration_minutes = Column(Integer, nullable=True) # set max duration for events if variable
    limit_per_day = Column(Integer, nullable=True)
    limit_per_week = Column(Integer, nullable=True)
    limit_per_month = Column(Integer, nullable=True)
    limit_per_booker = Column(Integer, nullable=True)
    limit_future_bookings_days = Column(Integer, nullable=True) #how many days in advance this event can be booked
    only_show_first_slot = Column(Boolean, nullable=True)
    interval_minutes = Column(Integer, nullable=True)  # step between slot start times; null = fall back to duration_minutes (slots don't overlap). e.g. 3hr window + 90min session + 30min interval = 3 possible start times

    booking_type = relationship("BookingType")
    availability = relationship("BookingLinkAvailability", back_populates="booking_link", cascade="all, delete-orphan")


class BookingLinkAvailability(Base):
    __tablename__ = "booking_link_availability"
    __table_args__ = (UniqueConstraint("booking_link_id", "tutor_id", name="uq_booking_link_tutor"),)

    id = Column(Integer, primary_key=True, index=True)
    booking_link_id = Column(Integer, ForeignKey("booking_links.id", ondelete="CASCADE"), nullable=False)
    tutor_id = Column(Integer, ForeignKey("tutors.id", ondelete="CASCADE"), nullable=False)
    schedule_id = Column(Integer, ForeignKey("schedules.id", ondelete="CASCADE"), nullable=False)

    booking_link = relationship("BookingLink", back_populates="availability")
    tutor = relationship("Tutor", back_populates="availability")
    schedule = relationship("Schedule", back_populates="availability")


# class CancellationPolicy(Base):  # policy fields moved directly onto BookingLink
#     __tablename__ = "cancellation_policies"
#   __table_args__ = (
#     CheckConstraint(
#            "cancel_mode IN ('blocked', 'auto', 'auto_window_block', 'auto_window_request', 'request', 'request_window')",
#            name="chk_cancellation_policy_cancel_mode"
#        ),
#        CheckConstraint(
#            "reschedule_mode IN ('blocked', 'auto', 'auto_window_block', 'auto_window_request', 'request', 'request_window')",
#            name="chk_cancellation_policy_reschedule_mode"
#        )
#    )
#     id = Column(Integer, primary_key=True, index=True)
#     name = Column(String, nullable=False, unique=True)
#     description = Column(Text, nullable=True)
#     cancel_mode = Column(String, nullable=False)
#     cancel_notice_minutes = Column(Integer, nullable=True)
#     reschedule_mode = Column(String, nullable=False)
#     reschedule_notice_minutes = Column(Integer, nullable=True)
#     booking_links = relationship("BookingLink", back_populates="cancellation_policy", passive_deletes=True)


class BookingSeries(Base):
    __tablename__ = "booking_series"
    __table_args__ = (
        # Both constrained to what generation is tested for, not to what iCal allows.
        CheckConstraint(
            f"freq IN ({', '.join(repr(f) for f in FREQ_DAYS)})",
            name="chk_booking_series_freq",
        ),
        CheckConstraint(
            f"interval IN ({', '.join(str(i) for i in SUPPORTED_INTERVALS)})",
            name="chk_booking_series_interval",
        ),
        # RFC5545: a rule carries one or the other, never both.
        CheckConstraint("until IS NULL OR count IS NULL", name="chk_booking_series_not_both_until_and_count"),
        CheckConstraint(f"cancel_mode IN {_ALL_MODES_SQL}", name="chk_booking_series_cancel_mode"),
        CheckConstraint(f"reschedule_mode IN {_ALL_MODES_SQL}", name="chk_booking_series_reschedule_mode"),
        CheckConstraint(
            f"cancel_mode NOT IN {_WINDOW_MODES_SQL} OR (cancel_notice_minutes IS NOT NULL AND cancel_notice_minutes > 0)",
            name="chk_booking_series_cancel_notice_required",
        ),
        CheckConstraint(
            f"reschedule_mode NOT IN {_WINDOW_MODES_SQL} OR (reschedule_notice_minutes IS NOT NULL AND reschedule_notice_minutes > 0)",
            name="chk_booking_series_reschedule_notice_required",
        ),
        CheckConstraint(f"series_cancel_mode IN {_SERIES_MODES_SQL}", name="chk_booking_series_series_cancel_mode"),
        CheckConstraint(f"series_reschedule_mode IN {_SERIES_MODES_SQL}", name="chk_booking_series_series_reschedule_mode"),
    )

    id = Column(Integer, primary_key=True, index=True)
    public_id = Column(String, unique=True, nullable=False, default=lambda: str(uuid4()))  # for public-facing links
    created = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_modified = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    # Facet keys, filtered on every list request. Postgres doesn't index foreign keys on its own.
    tutor_id = Column(Integer, ForeignKey("tutors.id"), nullable=False, index=True)
    booking_link_id = Column(Integer, ForeignKey("booking_links.id"), nullable=False, index=True)
    booking_type_id = Column(Integer, ForeignKey("booking_types.id", ondelete="SET NULL"), nullable=True, index=True)
    dtstart = Column(DateTime, nullable=False, index=True)  # naive local time, not UTC — see ScheduleDay.start_time
    dtend = Column(DateTime, nullable=False)
    status = Column(String, nullable=True)  # 'cancelled' | 'rescheduled' | null (active/finished derived, see is_active)
    # iCal RRULE shape. until and count are the two mutually exclusive ways to end a recurrence
    # (RFC5545 forbids both in one rule): until is a fixed date, count is a quota of occurrences.
    # Both null = indefinite. count counts occurrences of the rule, not attended sessions — a
    # cancelled occurrence still fills its slot, same as Google.
    freq = Column(String, nullable=False, default="WEEKLY")      # WEEKLY today; DAILY/MONTHLY planned — see FREQ_DAYS
    interval = Column(Integer, nullable=False, default=1)        # every N periods; 1 today, biweekly and beyond planned — see SUPPORTED_INTERVALS
    until = Column(Date, nullable=True)
    count = Column(Integer, nullable=True)
    # Frozen from the link at creation. The cancel_*/reschedule_* pair is the template each
    # occurrence copies; the series_* pair governs acting on the series itself.
    cancel_mode = Column(String, nullable=False, server_default="auto")
    cancel_notice_minutes = Column(Integer, nullable=True)
    reschedule_mode = Column(String, nullable=False, server_default="auto")
    reschedule_notice_minutes = Column(Integer, nullable=True)
    series_cancel_mode = Column(String, nullable=False, server_default="auto")
    series_reschedule_mode = Column(String, nullable=False, server_default="auto")
    # Frozen at creation: price off the link, rate off the attendee's enrollment. Occurrences copy
    # these off the series, never off the link.
    price_id = Column(Integer, ForeignKey("prices.id"), nullable=True)
    rate_id = Column(Integer, ForeignKey("prices.id"), nullable=True)
    # Which enrollment's monthly plan already pays for these, if any. Null by default so a second
    # series bills — over-billing gets reported, under-billing is silent.
    covered_by_enrollment_id = Column(Integer, ForeignKey("enrollments.id", ondelete="SET NULL"), nullable=True)
    # byday: omitted, derivable from dtstart.weekday() until multi-day-per-series is supported.
    # Real support needs an array column, not a scalar, so a placeholder now would just be replaced.
    # wkst: omitted, only defines week boundaries when grouping multi-day recurrence — moot without byday.
    rescheduled_to = Column(Integer, ForeignKey("booking_series.id", ondelete="SET NULL"), nullable=True)
    google_event_id = Column(String, nullable=True) # google calendar series master event

    # Who pays and who attends. Both NOT NULL, and allowed to be the same contact: an adult booking
    # for themselves is the row where they match, a parent booking for a child is where they differ.
    # Facet keys, so both are indexed.
    payer_id = Column(Integer, ForeignKey("contacts.id"), nullable=False, index=True)
    attendee_id = Column(Integer, ForeignKey("contacts.id"), nullable=False, index=True)
    # Template each occurrence copies, same as the policy columns above.
    sms_opt_in = Column(Boolean, nullable=False, default=False)
    guest_reminder_phone = Column(String, nullable=True)

    tutor = relationship("Tutor", back_populates="series")
    price = relationship("Price", foreign_keys=[price_id])
    rate = relationship("Price", foreign_keys=[rate_id])
    booking_link = relationship("BookingLink")
    booking_type = relationship("BookingType")
    payer = relationship("Contact", foreign_keys=[payer_id])
    attendee = relationship("Contact", foreign_keys=[attendee_id])
    bookings = relationship("Booking", back_populates="series")
    request = relationship("BookingRequest", back_populates="series", uselist=False)
    covered_by_enrollment = relationship("Enrollment")
    # backref: rescheduled_from_series (uselist=False) — the predecessor series that got
    # rescheduled into this one, if any. Not a stored column, resolved on access.
    rescheduled_to_series = relationship(
        "BookingSeries",
        remote_side=[id],
        foreign_keys=[rescheduled_to],
        backref=backref("rescheduled_from_series", uselist=False),
    )

    @property
    def rescheduled_to_public_id(self) -> str | None:
        return self.rescheduled_to_series.public_id if self.rescheduled_to_series else None

    @property
    def rescheduled_from_public_id(self) -> str | None:
        return self.rescheduled_from_series.public_id if self.rescheduled_from_series else None

    @property
    def duration(self) -> timedelta:
        return self.dtend - self.dtstart




class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        UniqueConstraint("series_id", "start", name="uq_booking_series_occurence"),
        CheckConstraint(f"cancel_mode IN {_ALL_MODES_SQL}", name="chk_booking_cancel_mode"),
        CheckConstraint(f"reschedule_mode IN {_ALL_MODES_SQL}", name="chk_booking_reschedule_mode"),
        CheckConstraint(
            f"cancel_mode NOT IN {_WINDOW_MODES_SQL} OR (cancel_notice_minutes IS NOT NULL AND cancel_notice_minutes > 0)",
            name="chk_booking_cancel_notice_required",
        ),
        CheckConstraint(
            f"reschedule_mode NOT IN {_WINDOW_MODES_SQL} OR (reschedule_notice_minutes IS NOT NULL AND reschedule_notice_minutes > 0)",
            name="chk_booking_reschedule_notice_required",
        ),
        # Matches how every list query reads: filter a time window, order by (start, public_id), seek
        # from the cursor's tuple. Composite so the ORDER BY needs no sort step at all — with a page
        # size of 50 the planner walks 50 index entries instead of sorting the table. Leftmost-prefix
        # means it also serves plain `start` filters, so no separate index on that column.
        Index("ix_bookings_start_public_id", "start", "public_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    public_id = Column(String, unique=True, nullable=False, default=lambda: str(uuid4()))  # for public-facing links
    # Server-managed, never accepted from a request.
    created = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_modified = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    series_id = Column(Integer, ForeignKey("booking_series.id"), nullable=True) # for recurrent bookings (series means every wed at 5pm for 5 months)
    # Facet keys — same reasoning as BookingSeries above.
    tutor_id = Column(Integer, ForeignKey("tutors.id"), nullable=False, index=True)
    booking_link_id = Column(Integer, ForeignKey("booking_links.id"), nullable=False, index=True)
    booking_type_id = Column(Integer, ForeignKey("booking_types.id", ondelete="SET NULL"), nullable=True, index=True)
    start = Column(DateTime(timezone=True), nullable=False)
    end = Column(DateTime(timezone=True), nullable=False)
    timezone = Column(String, nullable=False, default="America/New_York")  # booker's timezone — display/email only, all scheduling logic uses UTC
    google_event_id = Column(String, nullable=False) #derived after google creates the event, not passed in
    status = Column(String, nullable=False, default="confirmed")
    is_no_show = Column(Boolean, nullable=False, default=False)
    # Beats both pointers below. Null bills normally; 0 is a deliberate freebie, hence no default.
    charge = Column(Numeric(10, 2), nullable=True)
    # Frozen at creation, off the series for an occurrence. Resolution is charge ?? rate ?? price.
    price_id = Column(Integer, ForeignKey("prices.id"), nullable=True)
    rate_id = Column(Integer, ForeignKey("prices.id"), nullable=True)
    # Null means unbilled. Set once, when line generation includes this booking, same claim pattern
    # InvoiceItem already uses. Never cleared automatically, including on void.
    invoice_id = Column(Integer, ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True, index=True)
    # Frozen at creation — off the series for an occurrence, off the link otherwise. Never propagated after.
    cancel_mode = Column(String, nullable=False, server_default="auto")
    cancel_notice_minutes = Column(Integer, nullable=True)
    reschedule_mode = Column(String, nullable=False, server_default="auto")
    reschedule_notice_minutes = Column(Integer, nullable=True)
    # ondelete="SET NULL": cascade hard-delete in permanently_delete_booking walks the predecessor chain and
    # deletes rows in order [immediate_predecessor, ..., furthest_predecessor, primary]. When the immediate
    # predecessor is deleted first, the next row still has rescheduled_to pointing at it — FK RESTRICT would
    # block the delete. SET NULL lets Postgres null that column automatically so the order doesn't matter.
    # Alternative: remove SET NULL and collect predecessors with insert(0, ...) instead of append() so the
    # list is [furthest, ..., immediate] and deletes go referencing-side first — no FK violations, no SET NULL needed.
    rescheduled_to = Column(Integer, ForeignKey("bookings.id", ondelete="SET NULL"), nullable=True)
    # Same pair as BookingSeries, same rules. An occurrence copies both off its series.
    payer_id = Column(Integer, ForeignKey("contacts.id"), nullable=False, index=True)
    attendee_id = Column(Integer, ForeignKey("contacts.id"), nullable=False, index=True)
    sms_opt_in = Column(Boolean, nullable=False, default=False)
    # The phone a guest typed, frozen: unverified input shouldn't follow a later profile edit.
    # Nulled for every booking when the contact registers, so send time reads contact.phone live.
    guest_reminder_phone = Column(String, nullable=True)

    tutor = relationship("Tutor", back_populates="bookings")
    price = relationship("Price", foreign_keys=[price_id])
    rate = relationship("Price", foreign_keys=[rate_id])
    booking_link = relationship("BookingLink")
    booking_type = relationship("BookingType")
    series = relationship("BookingSeries", back_populates="bookings")
    invoice = relationship("Invoice")
    payer = relationship("Contact", foreign_keys=[payer_id])
    attendee = relationship("Contact", foreign_keys=[attendee_id])
    lesson = relationship("Lesson", back_populates="booking", uselist=False)
    request = relationship("BookingRequest", back_populates="booking", uselist=False)
    # backref: rescheduled_from_booking (uselist=False) — the predecessor booking that got
    # rescheduled into this one, if any. Not a stored column; SQLAlchemy resolves it as
    # `SELECT * FROM bookings WHERE rescheduled_to = <this booking's id>` on access.
    rescheduled_to_booking = relationship(
        "Booking",
        remote_side=[id],
        foreign_keys=[rescheduled_to],
        backref=backref("rescheduled_from_booking", uselist=False),
    )

    # allow pydantic to inherit parent's (booking's series) public_id field from the
# model's relationship by @
# ty and getattr(model_obj, field_name).
    @property
    def series_public_id(self) -> str | None:
        return self.series.public_id if self.series else None

    @property
    def rescheduled_to_public_id(self) -> str | None:
        return self.rescheduled_to_booking.public_id if self.rescheduled_to_booking else None

    @property
    def rescheduled_from_public_id(self) -> str | None:
        return self.rescheduled_from_booking.public_id if self.rescheduled_from_booking else None


INVOICE_STATUSES = ("draft", "finalized", "void")
_INVOICE_STATUSES_SQL = str(INVOICE_STATUSES)

PAYMENT_STATUSES = ("unpaid", "paid")
_PAYMENT_STATUSES_SQL = str(PAYMENT_STATUSES)


class Invoice(Base):
    """One period's bill for one payer. Generated from bookings, never stored as it accrues.

    `status` describes the document (draft -> finalized -> void); `payment_status` describes the
    money (unpaid -> paid) and moves independently. A finalized invoice can sit unpaid for weeks,
    and marking it paid doesn't change its document state. Nothing collects payment automatically;
    `payment_status` is set by hand when the money lands.
    """
    __tablename__ = "invoices"
    __table_args__ = (
        # Generation is re-runnable, so a payer can only have one invoice per period. NULLs are
        # never equal to each other in SQL, so this already lets any number of ad-hoc invoices
        # (period_start NULL) coexist per payer for free — no partial index needed.
        UniqueConstraint("payer_id", "period_start", name="uq_invoice_payer_period"),
        CheckConstraint(f"status IN {_INVOICE_STATUSES_SQL}", name="chk_invoice_status"),
        CheckConstraint(f"payment_status IN {_PAYMENT_STATUSES_SQL}", name="chk_invoice_payment_status"),
        # Both set or both null — an ad-hoc invoice has no period at all, never half of one.
        CheckConstraint(
            "(period_start IS NULL) = (period_end IS NULL)", name="chk_invoice_period_both_or_neither",
        ),
        CheckConstraint(
            "period_start IS NULL OR period_end > period_start", name="chk_invoice_period_order",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    public_id = Column(String, unique=True, nullable=False, default=lambda: str(uuid4()))
    payer_id = Column(Integer, ForeignKey("contacts.id"), nullable=False, index=True)
    # Half-open [start, end); null means ad-hoc, not tied to any period.
    period_start = Column(Date, nullable=True)
    period_end = Column(Date, nullable=True)
    status = Column(String, nullable=False, default="draft")
    payment_status = Column(String, nullable=False, default="unpaid")
    # Null while draft. Allocated once, at the draft -> finalized transition, so deleting a draft
    # never leaves a gap in the sequence.
    number = Column(String, unique=True, nullable=True)
    total = Column(Numeric(10, 2), nullable=False, default=0)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    paid_at = Column(DateTime(timezone=True), nullable=True)
    created = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_modified = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    payer = relationship("Contact")
    lines = relationship("InvoiceLine", back_populates="invoice", cascade="all, delete-orphan")

    @property
    def payer_name(self) -> str:
        return f"{self.payer.first_name} {self.payer.last_name}"


class InvoiceLine(Base):
    """One charge on an invoice. Generated once and frozen — nothing ever re-derives a line from its
    source after creation.

    `description` and `amount` are the frozen, as-computed values. They're never overwritten. A correction
    is always layered on top: `adjustment_amount` (flat) or `adjustment_percent` (0-100), mutually exclusive, both nullable,
    both default unset. The total charge is 'amount' with the adjustment applied on top, so client can display 
    the calculation and show before and after
    """
    __tablename__ = "invoice_lines"
    __table_args__ = (
        CheckConstraint(
            "adjustment_amount IS NULL OR adjustment_percent IS NULL",
            name="chk_invoice_line_adjustment_exclusive",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    # Recurring lines point at the enrollment, one-offs at the booking, swept charges at the item.
    # Exactly one set — purely provenance, never read by the generator itself (which already has the
    # source object in hand while building the line).
    enrollment_id = Column(Integer, ForeignKey("enrollments.id", ondelete="SET NULL"), nullable=True)
    booking_id = Column(Integer, ForeignKey("bookings.id", ondelete="SET NULL"), nullable=True)
    invoice_item_id = Column(Integer, ForeignKey("invoice_items.id", ondelete="SET NULL"), nullable=True)
    description = Column(String, nullable=False)
    amount = Column(Numeric(10, 2), nullable=False)
    adjustment_amount = Column(Numeric(10, 2), nullable=True)
    adjustment_percent = Column(Numeric(5, 2), nullable=True)

    invoice = relationship("Invoice", back_populates="lines")


class InvoiceItem(Base):
    """A charge recorded before any invoice exists to put it on.

    No booking_id: a charge about a specific booking is booking.charge instead, read live by the
    same period-scoped query that generates every other booking line — it lands in the right period
    for free, no date logic needed here. This table is only for charges with nothing to hang off —
    materials, a goodwill credit — so *when* one is recorded never matters, only that it eventually
    gets swept.

    Like a hotel tab: charges accrue as they happen, the bill is printed at checkout. invoice_id is
    null while pending; set once an invoice sweeps it, which is also what stops it being deleted.
    """
    __tablename__ = "invoice_items"

    id = Column(Integer, primary_key=True, index=True)
    payer_id = Column(Integer, ForeignKey("contacts.id"), nullable=False, index=True)
    description = Column(String, nullable=False)
    amount = Column(Numeric(10, 2), nullable=False)   # negative for a credit
    invoice_id = Column(Integer, ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True, index=True)
    created = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    payer = relationship("Contact")
    invoice = relationship("Invoice")


class Settings(Base):
    __tablename__ = "settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="chk_settings_singleton"),
    )

    id = Column(Integer, primary_key=True, default=1)
    business_timezone = Column(String, nullable=False, default="America/New_York")
    billing_automation_enabled = Column(Boolean, nullable=False, default=False)


class BookingRequest(Base):
    __tablename__ = "booking_requests"
    __table_args__ = (
        CheckConstraint(
            "type IN ('cancel_occurrence', 'reschedule_occurrence', 'cancel_series', 'reschedule_series')",
            name="chk_booking_request_type"
        ),
        CheckConstraint("status IN ('pending', 'approved', 'denied')", name="chk_booking_request_status"),
        # exactly one of booking_id or booking_series_id must be set
        CheckConstraint(
            "CASE WHEN booking_id IS NOT NULL THEN 1 ELSE 0 END + CASE WHEN booking_series_id IS NOT NULL THEN 1 ELSE 0 END = 1",
            name="chk_booking_request_target"
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    # occurrence-level request (cancel / reschedule)
    booking_id = Column(Integer, ForeignKey("bookings.id", ondelete="CASCADE"), nullable=True, unique=True)
    # series-level request (cancel_series / reschedule_series)
    booking_series_id = Column(Integer, ForeignKey("booking_series.id", ondelete="CASCADE"), nullable=True, unique=True)

    type = Column(String, nullable=False)  # 'cancel_occurrence' | 'reschedule_occurrence' | 'cancel_series' | 'reschedule_series'
    status = Column(String, nullable=False, default="pending")  # 'pending' | 'approved' | 'denied'
    # for reschedule requests: the slot the booker picked, stored as UTC-aware (booking_in.start/end are
    # already UTC after BookingReschedule.validate_and_convert runs). Stored with tzinfo so _convert_to_utc
    # hits the dt.tzinfo-is-not-None branch at approve time — no double conversion.
    requested_start = Column(DateTime(timezone=True), nullable=True)
    requested_end = Column(DateTime(timezone=True), nullable=True)
    requested_timezone = Column(String, nullable=True)
    requested_tutor_id = Column(Integer, ForeignKey("tutors.id"), nullable=True)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))

    booking = relationship("Booking", back_populates="request")
    series = relationship("BookingSeries", back_populates="request")
    requested_tutor = relationship("Tutor")