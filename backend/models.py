from sqlalchemy import Column, Integer, String, Float, Boolean, Date, Text, ForeignKey, UniqueConstraint, CheckConstraint, Time, DateTime, func
from sqlalchemy.orm import relationship, backref
from database import Base
from datetime import datetime, timedelta, UTC
from uuid import uuid4
from policy import get_cancel_action, get_reschedule_action


def _minutes_until(start: datetime) -> float:
    """Defensive against naive datetimes (SQLite in tests doesn't preserve tz-awareness)."""
    start_tz = start if start.tzinfo else start.replace(tzinfo=UTC)
    return (start_tz - datetime.now(UTC)).total_seconds() / 60

class Student(Base):
    __tablename__ = "students"
    __table_args__ = (UniqueConstraint('first_name', 'last_name', name='uq_student_name'),)

    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    rate = Column(Float, nullable=False)
    start_date = Column(Date, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)

    grade = Column(Integer, nullable=True)
    birthday = Column(Date, nullable=True)
    email = Column(String, nullable=True)

    lessons = relationship("Lesson", back_populates="student")


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
    availability = relationship("EventTypeAvailability", back_populates="tutor", passive_deletes=True)
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

    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    tutor_id = Column(Integer, ForeignKey("tutors.id"), nullable=False)
    booking_id = Column(Integer, ForeignKey("bookings.id"), nullable=True)  # set by Sunday scheduler when lesson is generated from a booking

    student = relationship("Student", back_populates="lessons")
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
    availability = relationship("EventTypeAvailability", back_populates="schedule", passive_deletes=True)
    days = relationship("ScheduleDay", back_populates="schedule", cascade="all, delete-orphan", passive_deletes=True)


#todo: consider making duration variable (1hr, 1.5hr, 2hr) instead of fixed 1hr, which would allow for more flexible scheduling

_WINDOW_MODES_SQL = "('auto_window_block', 'auto_window_request', 'request_window')"

class EventType(Base):
    __tablename__ = "event_types"
    __table_args__ = (
        CheckConstraint(
            f"cancel_mode NOT IN {_WINDOW_MODES_SQL} OR (cancel_notice_minutes IS NOT NULL AND cancel_notice_minutes > 0)",
            name="chk_event_type_cancel_notice_required"
        ),
        CheckConstraint(
            f"reschedule_mode NOT IN {_WINDOW_MODES_SQL} OR (reschedule_notice_minutes IS NOT NULL AND reschedule_notice_minutes > 0)",
            name="chk_event_type_reschedule_notice_required"
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    cancel_mode = Column(String, nullable=True)  # not_allowed, auto, auto_window_block, auto_window_request, request, request_window; null = auto
    cancel_notice_minutes = Column(Integer, nullable=True)
    reschedule_mode = Column(String, nullable=True)  # same options; null = auto
    reschedule_notice_minutes = Column(Integer, nullable=True)
    #basic info
    name = Column(String, nullable=False, unique=True)
    description = Column(Text, nullable=True)
    duration_minutes = Column(Integer, nullable=False)
    min_duration_minutes = Column(Integer, nullable=True)  # null = fixed duration; 0+ = custom duration on
    max_duration_minutes = Column(Integer, nullable=True)
    # recurrence
    recurring = Column(Boolean, nullable=False, default=True)
    recur_weeks = Column(Integer, nullable=True)       # mutually exclusive with expires_on; N weeks from booking start date
    expires_on = Column(Date, nullable=True)           # mutually exclusive with recur_weeks; booker_can_set_recur_until must be false, all series from this type end on this date
    booker_can_set_recur_until = Column(Boolean, nullable=False, default=False)
    #optional advanced limits
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

    availability = relationship("EventTypeAvailability", back_populates="event_type", cascade="all, delete-orphan")


class EventTypeAvailability(Base):
    __tablename__ = "event_type_availability"
    __table_args__ = (UniqueConstraint("event_type_id", "tutor_id", name="uq_event_type_tutor"),)

    id = Column(Integer, primary_key=True, index=True)
    event_type_id = Column(Integer, ForeignKey("event_types.id", ondelete="CASCADE"), nullable=False)
    tutor_id = Column(Integer, ForeignKey("tutors.id", ondelete="CASCADE"), nullable=False)
    schedule_id = Column(Integer, ForeignKey("schedules.id", ondelete="CASCADE"), nullable=False)

    event_type = relationship("EventType", back_populates="availability")
    tutor = relationship("Tutor", back_populates="availability")
    schedule = relationship("Schedule", back_populates="availability")


# class CancellationPolicy(Base):  # policy fields moved directly onto EventType
#     __tablename__ = "cancellation_policies"
#   __table_args__ = (
#     CheckConstraint(
#            "cancel_mode IN ('not_allowed', 'auto', 'auto_window_block', 'auto_window_request', 'request', 'request_window')",
#            name="chk_cancellation_policy_cancel_mode"
#        ),
#        CheckConstraint(
#            "reschedule_mode IN ('not_allowed', 'auto', 'auto_window_block', 'auto_window_request', 'request', 'request_window')",
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
#     event_types = relationship("EventType", back_populates="cancellation_policy", passive_deletes=True)


class BookingSeries(Base):
    __tablename__ = "booking_series"

    id = Column(Integer, primary_key=True, index=True)
    public_id = Column(String, unique=True, nullable=False, default=lambda: str(uuid4()))  # for public-facing links
    created = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_modified = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    tutor_id = Column(Integer, ForeignKey("tutors.id"), nullable=False)
    event_type_id = Column(Integer, ForeignKey("event_types.id"), nullable=False)
    dtstart = Column(DateTime, nullable=False)  # naive local time, not UTC — see ScheduleDay.start_time
    dtend = Column(DateTime, nullable=False)
    status = Column(String, nullable=True)  # 'cancelled' | 'rescheduled' | null (active/finished derived, see is_active)
    until = Column(Date, nullable=True)      # null = indefinite
    rescheduled_to = Column(Integer, ForeignKey("booking_series.id", ondelete="SET NULL"), nullable=True)
    google_event_id = Column(String, nullable=True) # google calendar series master event

    student_id = Column(Integer, ForeignKey("students.id"), nullable=True)
    student_first = Column(String, nullable=False)
    student_last = Column(String, nullable=False)
    student_email = Column(String, nullable=True)
    student_phone = Column(String, nullable=True)
    parent_email = Column(String, nullable=True)
    parent_phone = Column(String, nullable=True)

    tutor = relationship("Tutor", back_populates="series")
    event_type = relationship("EventType")
    student_record = relationship("Student")
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


    @property
    def _next_upcoming_minutes_until(self) -> float:
        now = datetime.now(UTC)
        starts = (b.start if b.start.tzinfo else b.start.replace(tzinfo=UTC) for b in self.bookings if b.status == "confirmed")
        upcoming = [s for s in starts if s >= now]
        return (min(upcoming) - now).total_seconds() / 60 if upcoming else float('inf')

    @property
    def cancel_action(self) -> str:
        return get_cancel_action(self.event_type, self._next_upcoming_minutes_until)

    @property
    def reschedule_action(self) -> str:
        return get_reschedule_action(self.event_type, self._next_upcoming_minutes_until)


class Booking(Base):
    __tablename__ = "bookings"
    # below ensures on db level that at least one of student_email or parent_email is provided, and at least one of student_phone or parent_phone is provided
    __table_args__ = (
        CheckConstraint(
            "student_email IS NOT NULL OR parent_email IS NOT NULL",
            name="chk_booking_email"
        ),
        CheckConstraint(
            "student_phone IS NOT NULL OR parent_phone IS NOT NULL",
            name="chk_booking_phone"
        ),
        UniqueConstraint("series_id", "start", name="uq_booking_series_occurence")
    )

    id = Column(Integer, primary_key=True, index=True)
    public_id = Column(String, unique=True, nullable=False, default=lambda: str(uuid4()))  # for public-facing links
    series_id = Column(Integer, ForeignKey("booking_series.id"), nullable=True) # for recurrent bookings (series means every wed at 5pm for 5 months)
    tutor_id = Column(Integer, ForeignKey("tutors.id"), nullable=False)
    event_type_id = Column(Integer, ForeignKey("event_types.id"), nullable=False)
    start = Column(DateTime(timezone=True), nullable=False)
    end = Column(DateTime(timezone=True), nullable=False)
    timezone = Column(String, nullable=False, default="America/New_York")  # booker's timezone — display/email only, all scheduling logic uses UTC
    google_event_id = Column(String, nullable=False) #derived after google creates the event, not passed in
    status = Column(String, nullable=False, default="confirmed")
    is_no_show = Column(Boolean, nullable=False, default=False)
    # ondelete="SET NULL": cascade hard-delete in permanently_delete_booking walks the predecessor chain and
    # deletes rows in order [immediate_predecessor, ..., furthest_predecessor, primary]. When the immediate
    # predecessor is deleted first, the next row still has rescheduled_to pointing at it — FK RESTRICT would
    # block the delete. SET NULL lets Postgres null that column automatically so the order doesn't matter.
    # Alternative: remove SET NULL and collect predecessors with insert(0, ...) instead of append() so the
    # list is [furthest, ..., immediate] and deletes go referencing-side first — no FK violations, no SET NULL needed.
    rescheduled_to = Column(Integer, ForeignKey("bookings.id", ondelete="SET NULL"), nullable=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=True)  # null for new one-off customers; linked when student record exists

    student_first = Column(String, nullable=False)
    student_last = Column(String, nullable=False)
    student_email = Column(String, nullable=True)
    student_phone = Column(String, nullable=True)
    parent_email = Column(String, nullable=True)
    parent_phone = Column(String, nullable=True)

    tutor = relationship("Tutor", back_populates="bookings")
    event_type = relationship("EventType")
    series = relationship("BookingSeries", back_populates="bookings")
    student_record = relationship("Student")
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
    # model's relationship by @property and getattr(model_obj, field_name).
    @property
    def series_public_id(self) -> str | None:
        return self.series.public_id if self.series else None

    @property
    def rescheduled_to_public_id(self) -> str | None:
        return self.rescheduled_to_booking.public_id if self.rescheduled_to_booking else None

    @property
    def rescheduled_from_public_id(self) -> str | None:
        return self.rescheduled_from_booking.public_id if self.rescheduled_from_booking else None

    @property
    def cancel_action(self) -> str:
        return get_cancel_action(self.event_type, _minutes_until(self.start))

    @property
    def reschedule_action(self) -> str:
        return get_reschedule_action(self.event_type, _minutes_until(self.start))


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