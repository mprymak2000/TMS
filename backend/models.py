from sqlalchemy import Column, Integer, String, Float, Boolean, Date, Text, ForeignKey, UniqueConstraint, CheckConstraint, Time, DateTime, Index, text, func
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

    # uselist=False: the PK guarantees one row, but the ORM can't infer that from the key shape.
    # passive_deletes: let the DB cascade. Without it SQLAlchemy tries to null the child's FK, which
    # here is its primary key.
    # foreign_keys: Enrollment points here twice — its PK, and payer_id.
    enrollment = relationship(
        "Enrollment", back_populates="contact", uselist=False, passive_deletes=True,
        foreign_keys="Enrollment.id",
    )


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


# Amount and unit are separate columns, not one exclusive column per unit, so "plan picked, not yet
# priced" is expressible. A rate belongs to a person, a price to an offering — hence two names.
RATE_UNITS = ("per_session", "per_hour", "per_month")
PRICE_UNITS = ("per_session", "per_hour")
_RATE_UNITS_SQL = str(RATE_UNITS)
_PRICE_UNITS_SQL = str(PRICE_UNITS)


class Enrollment(Base):
    """What's true of a contact because they're enrolled here — an extension of the person, not a
    thing they have. Never reassigned, never swapped for a fresh one, so it has no identity of its
    own: `id` is the contact's id, which is the joined-table-inheritance pattern. Admin-created,
    since a guest can't supply a rate.
    """
    __tablename__ = "enrollments"
    __table_args__ = (
        CheckConstraint(
            f"rate_unit IS NULL OR rate_unit IN {_RATE_UNITS_SQL}",
            name="chk_enrollment_rate_unit",
        ),
        # An amount with no unit says nothing — is 400 per session or per month?
        CheckConstraint("rate IS NULL OR rate_unit IS NOT NULL", name="chk_enrollment_rate_needs_unit"),
        CheckConstraint("rate IS NULL OR rate >= 0", name="chk_enrollment_rate_non_negative"),
    )

    # PK and FK in one column. Being the PK enforces one-per-contact without a separate UNIQUE;
    # CASCADE because there's no key for the row to exist under once the contact is gone.
    id = Column(Integer, ForeignKey("contacts.id", ondelete="CASCADE"), primary_key=True)
    start_date = Column(Date, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)

    # Both nullable: enrolled with no terms agreed is a real state, and bookings fall back to the link's price meanwhile.
    rate_unit = Column(String, nullable=True)
    rate = Column(Float, nullable=True)
    # Who gets the invoice; null means they pay for themselves. Not contact_managers, which grants
    # permission to book — a grandparent can pay while a parent books, and two parents on one child
    # leave that table with no way to pick.
    payer_id = Column(Integer, ForeignKey("contacts.id"), nullable=True, index=True)

    # Here rather than on Contact because they're only ever collected for a student, never a payer.
    grade = Column(Integer, nullable=True)
    birthday = Column(Date, nullable=True)

    contact = relationship("Contact", foreign_keys=[id], back_populates="enrollment")
    payer = relationship("Contact", foreign_keys=[payer_id])
    lessons = relationship("Lesson", back_populates="enrollment")


class Tutor(Base):
    __tablename__ = "tutors"
    __table_args__ = (UniqueConstraint('first_name', 'last_name', name='uq_tutor_name'),)

    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    pay_rate = Column(Float, nullable=False)
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
    fee = Column(Float, nullable=False)
    is_fee_overridden = Column(Boolean, nullable=False, default=False)
    tutor_payout = Column(Float, nullable=False)
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
            f"price_unit IS NULL OR price_unit IN {_PRICE_UNITS_SQL}",
            name="chk_booking_link_price_unit"
        ),
        CheckConstraint("price IS NULL OR price_unit IS NOT NULL", name="chk_booking_link_price_needs_unit"),
        CheckConstraint("price IS NULL OR price >= 0", name="chk_booking_link_price_non_negative"),
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
    # What a booking from this link costs when the attendee has no rate of their own, and what a monthly client's extra sessions cost.
    price_unit = Column(String, nullable=True)
    price = Column(Float, nullable=True)
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
    # Does a monthly plan already pay for these? Only read for a per_month enrollment. Off by default
    # so a second series bills — over-billing gets reported, under-billing is silent.
    covered_by_subscription = Column(Boolean, nullable=False, server_default=text("false"))
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
    booking_link = relationship("BookingLink")
    booking_type = relationship("BookingType")
    payer = relationship("Contact", foreign_keys=[payer_id])
    attendee = relationship("Contact", foreign_keys=[attendee_id])
    bookings = relationship("Booking", back_populates="series")
    request = relationship("BookingRequest", back_populates="series", uselist=False)
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
    # Admin override for what this one session costs, beating both the client's rate and the link's
    # price. Null means bill it normally; 0 is a deliberate freebie, which is why it isn't a default.
    charge = Column(Float, nullable=True)
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
    booking_link = relationship("BookingLink")
    booking_type = relationship("BookingType")
    series = relationship("BookingSeries", back_populates="bookings")
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


INVOICE_STATUSES = ("draft", "sent", "paid", "void")
_INVOICE_STATUSES_SQL = str(INVOICE_STATUSES)


class Invoice(Base):
    """One period's bill for one payer. Generated from bookings, never stored as it accrues.

    Nothing collects payment — `paid` is marked by hand when the money lands.
    """
    __tablename__ = "invoices"
    __table_args__ = (
        # Generation is re-runnable, so a payer can only have one invoice per period.
        UniqueConstraint("payer_id", "period_start", name="uq_invoice_payer_period"),
        CheckConstraint(f"status IN {_INVOICE_STATUSES_SQL}", name="chk_invoice_status"),
        CheckConstraint("period_end > period_start", name="chk_invoice_period_order"),
    )

    id = Column(Integer, primary_key=True, index=True)
    public_id = Column(String, unique=True, nullable=False, default=lambda: str(uuid4()))
    payer_id = Column(Integer, ForeignKey("contacts.id"), nullable=False, index=True)
    # Half-open [start, end), so consecutive months can't double-count a booking on the boundary.
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    status = Column(String, nullable=False, default="draft")
    total = Column(Float, nullable=False, default=0)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    paid_at = Column(DateTime(timezone=True), nullable=True)
    created = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_modified = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
``
    payer = relationship("Contact")
    lines = relationship("InvoiceLine", back_populates="invoice", cascade="all, delete-orphan")

    @property
    def payer_name(self) -> str:
        return f"{self.payer.first_name} {self.payer.last_name}"


class InvoiceLine(Base):
    """One charge on an invoice.

    `description` and `amount` are frozen copies, not read through the FKs — an invoice asserts what
    was billed at the time, so a later rename must not rewrite it. That's also why both source FKs
    are nullable and SET NULL: the line outlives whatever produced it.
    """
    __tablename__ = "invoice_lines"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    # Recurring lines point at the enrollment, one-offs at the booking. Never both.
    enrollment_id = Column(Integer, ForeignKey("enrollments.id", ondelete="SET NULL"), nullable=True)
    booking_id = Column(Integer, ForeignKey("bookings.id", ondelete="SET NULL"), nullable=True)
    description = Column(String, nullable=False)
    amount = Column(Float, nullable=False)

    invoice = relationship("Invoice", back_populates="lines")


class Settings(Base):
    __tablename__ = "settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="chk_settings_singleton"),
    )

    id = Column(Integer, primary_key=True, default=1)
    business_timezone = Column(String, nullable=False, default="America/New_York")


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