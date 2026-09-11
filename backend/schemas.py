from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator
from datetime import date, time, datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from policy import get_cancel_action, get_reschedule_action, minutes_until

#todo: consider patch instead of put for updates, as it allows for partial updates and is more flexible but more complex to implement. put requires the entire object to be sent, which can be simpler but less efficient for updates that only change a few fields.
#todo: change student rate Field(gt=0) to Field(ge=0) in StudentCreate and StudentUpdate — rate=0 should be allowed (e.g. a family member). Also check tutor_payout logic for division by zero when student.rate=0.


class SettingsUpdate(BaseModel):
    business_timezone: str


class SettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    business_timezone: str


class TutorCreate(BaseModel):
    first_name: str
    last_name: str
    pay_rate: float = Field(ge=0)
    is_active: bool = True
    calendar_id: str | None = None
    check_calendar_conflicts: bool = False


class TutorUpdate(BaseModel):
    pay_rate: float = Field(ge=0)
    is_active: bool
    calendar_id: str | None = None
    check_calendar_conflicts: bool = False


class TutorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    first_name: str
    last_name: str
    pay_rate: float
    is_active: bool
    calendar_id: str | None = None
    check_calendar_conflicts: bool = False

class StudentCreate(BaseModel):
    first_name: str
    last_name: str
    rate: float = Field(gt=0)
    start_date: date
    is_active: bool = True
    grade: int | None = None
    birthday: date | None = None
    email: str | None = None


#todo: add auto grade incrementing every summer
class StudentUpdate(BaseModel):
    rate: float = Field(gt=0)
    start_date: date
    is_active: bool
    grade: int | None = None
    email: str | None = None


class StudentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    first_name: str
    last_name: str
    rate: float
    start_date: date
    is_active: bool
    grade: int | None = None
    birthday: date | None = None
    email: str | None = None


class LessonCreate(BaseModel):
    student_id: int
    tutor_id: int
    date: date
    hrs: float | None = Field(default=None, ge=0)
    fee_override: float | None = Field(default=None, ge=0)
    tutor_pay_override: float | None = Field(default=None, ge=0)
    pay_status: bool = False
    notes: str | None = None


class LessonUpdate(BaseModel):
    date: date
    hrs: float | None = Field(default=None, ge=0)
    fee_override: float | None = Field(default=None, ge=0)
    tutor_pay_override: float | None = Field(default=None, ge=0)
    pay_status: bool
    notes: str | None = None


class LessonResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    student_id: int
    tutor_id: int
    booking_id: int | None = None
    date: date
    hrs: float | None = None
    pay_status: bool
    notes: str | None = None
    fee: float
    tutor_payout: float
    is_fee_overridden: bool
    is_tutor_payout_overridden: bool


class CalendarEventCreate(BaseModel):
    tutor_id: int
    summary: str
    start: str
    end: str
    timezone: str


class CalendarEventResponse(BaseModel):
    id: str
    summary: str
    start: dict
    end: dict
    htmlLink: str | None = None


class ScheduleDayCreate(BaseModel):
    day_of_week: int = Field(ge=0, le=6)
    start_time: time
    end_time: time

    @model_validator(mode="after")
    def validate_times(self):
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class ScheduleDayResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    day_of_week: int
    start_time: time
    end_time: time


class ScheduleCreate(BaseModel):
    tutor_id: int
    name: str
    is_default: bool = False
    days: list[ScheduleDayCreate] = Field(min_length=1)
    timezone: str | None = None  # redundant — always Settings.business_timezone; nullable pending refactor


class ScheduleUpdate(BaseModel):
    name: str
    is_default: bool = False
    timezone: str | None = None  # redundant — always Settings.business_timezone; nullable pending refactor
    days: list[ScheduleDayCreate] = Field(min_length=1)


class ScheduleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tutor_id: int
    name: str
    is_default: bool
    timezone: str | None = None  # redundant — always Settings.business_timezone; nullable pending refactor
    days: list[ScheduleDayResponse]


class BookingLinkAvailabilityCreate(BaseModel):
    booking_link_id: int
    tutor_id: int
    schedule_id: int


class BookingLinkAvailabilityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    booking_link_id: int
    tutor_id: int
    schedule_id: int


class TutorAvailability(BaseModel):
    tutor_id: int
    schedule_id: int


_HEX_COLOR_PATTERN = r'^#[0-9a-fA-F]{6}$'


class BookingLinkStatusUpdate(BaseModel):
    """Pause and resume only. Archiving is DELETE — it's the irreversible "get rid of this" action,
    so it stays out of reach of a status write."""
    status: Literal["active", "paused"]


class BookingTypeCreate(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    color: str | None = Field(default=None, pattern=_HEX_COLOR_PATTERN)


class BookingTypeUpdate(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    color: str | None = Field(default=None, pattern=_HEX_COLOR_PATTERN)


class BookingTypeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    label: str
    color: str | None = None


class BookingTypeUsage(BaseModel):
    """Counted on delete-click only, never on picker open. Split by referrer because the losses differ:
    a booking or series loses a label off a historical record, a link just needs a new type picked."""
    links: int
    bookings: int
    series: int


# Lowercase alphanumerics with single hyphens between — a slug goes straight into a URL, and the
# frontend's slugify is a convenience, not a guarantee.
_SLUG_PATTERN = r'^[a-z0-9]+(?:-[a-z0-9]+)*$'

# Matches the String(500) column. Bounded because it renders on the public booking page and into
# calendar invites; 500 is the common ceiling for a short description field (Stripe uses the same).
DESCRIPTION_MAX_LENGTH = 500

_VALID_MODES = ('blocked', 'auto', 'auto_window_block', 'auto_window_request', 'request', 'request_window')
_WINDOW_MODES = ('auto_window_block', 'auto_window_request', 'request_window')
# Series-level actions have no notice window, so the mode is already the verdict.
_SERIES_MODES = ('blocked', 'auto', 'request')

# policy fields moved directly onto BookingLink — CancellationPolicy schemas kept for reference
# class CancellationPolicyCreate(BaseModel):
#     name: str
#     description: str | None = None
#     cancel_mode: str
#     cancel_notice_minutes: int | None = None
#     reschedule_mode: str
#     reschedule_notice_minutes: int | None = None
#
#     @model_validator(mode="after")
#     def validate_modes(self):
#         if self.cancel_mode not in _VALID_MODES:
#             raise ValueError(f"cancel_mode must be one of {_VALID_MODES}")
#         if self.reschedule_mode not in _VALID_MODES:
#             raise ValueError(f"reschedule_mode must be one of {_VALID_MODES}")
#         return self
#
# class CancellationPolicyUpdate(BaseModel):
#     name: str
#     description: str | None = None
#     cancel_mode: str | None = None
#     cancel_notice_minutes: int | None = None
#     reschedule_mode: str | None = None
#     reschedule_notice_minutes: int | None = None
#
#     @model_validator(mode="after")
#     def validate_modes(self):
#         if self.cancel_mode is not None and self.cancel_mode not in _VALID_MODES:
#             raise ValueError(f"cancel_mode must be one of {_VALID_MODES}")
#         if self.reschedule_mode is not None and self.reschedule_mode not in _VALID_MODES:
#             raise ValueError(f"reschedule_mode must be one of {_VALID_MODES}")
#         return self
#
# class CancellationPolicyResponse(BaseModel):
#     model_config = ConfigDict(from_attributes=True)
#     id: int
#     name: str
#     description: str | None = None
#     cancel_mode: str
#     cancel_notice_minutes: int | None = None
#     reschedule_mode: str
#     reschedule_notice_minutes: int | None = None


def _validate_recurrence(count, expires_on, booker_can_set_recur_until, booker_can_set_count):
    if count is not None and expires_on is not None:
        raise ValueError("count and expires_on are mutually exclusive")
    if booker_can_set_recur_until and booker_can_set_count:
        raise ValueError("booker_can_set_recur_until and booker_can_set_count are mutually exclusive")
    if expires_on is not None and booker_can_set_recur_until:
        raise ValueError("booker_can_set_recur_until must be False when expires_on is set")
    if expires_on is not None and booker_can_set_count:
        raise ValueError("booker_can_set_count must be False when expires_on is set")
    if count is not None and booker_can_set_recur_until:
        raise ValueError("booker_can_set_recur_until must be False when count is set — that link ends on a count, not a date")
    if count is not None and count < 2:
        raise ValueError("count must be at least 2")
    if expires_on is not None and expires_on <= date.today():
        raise ValueError("expires_on must be in the future")


class BookingLinkCreate(BaseModel):
    slug: str = Field(pattern=_SLUG_PATTERN, max_length=100)
    booking_type_id: int | None = None
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX_LENGTH)
    recurring: bool = True
    # Narrowed to what generation is tested for — the DB CHECKs say the same thing, this just makes
    # it a 422 instead of an IntegrityError. Widen alongside models.FREQ_DAYS/SUPPORTED_INTERVALS.
    freq: Literal["WEEKLY"] = "WEEKLY"
    interval: Literal[1] = 1
    duration_minutes: int
    min_duration_minutes: int | None = None
    max_duration_minutes: int | None = None
    count: int | None = None
    expires_on: date | None = None
    booker_can_set_recur_until: bool = False
    booker_can_set_count: bool = False

    price: float | None = None
    # 'auto' rather than None — the columns are NOT NULL, so there's no "unset" to fall back to.
    cancel_mode: str = 'auto'
    cancel_notice_minutes: int | None = None
    reschedule_mode: str = 'auto'
    reschedule_notice_minutes: int | None = None
    series_cancel_mode: str = 'auto'
    series_reschedule_mode: str = 'auto'

    buffer_minutes: int | None = None
    interval_minutes: int | None = None
    limit_duration_minutes: int | None = None
    limit_per_day: int | None = None
    limit_per_week: int | None = None
    limit_per_month: int | None = None
    limit_per_booker: int | None = None
    limit_future_bookings_days: int | None = None
    only_show_first_slot: bool | None = None

    availability: list[TutorAvailability] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_recurrence(self):
        _validate_recurrence(self.count, self.expires_on, self.booker_can_set_recur_until, self.booker_can_set_count)
        if self.cancel_mode not in _VALID_MODES:
            raise ValueError(f"cancel_mode must be one of {_VALID_MODES}")
        if self.reschedule_mode not in _VALID_MODES:
            raise ValueError(f"reschedule_mode must be one of {_VALID_MODES}")
        if self.series_cancel_mode not in _SERIES_MODES:
            raise ValueError(f"series_cancel_mode must be one of {_SERIES_MODES}")
        if self.series_reschedule_mode not in _SERIES_MODES:
            raise ValueError(f"series_reschedule_mode must be one of {_SERIES_MODES}")
        if self.cancel_mode in _WINDOW_MODES and not (self.cancel_notice_minutes and self.cancel_notice_minutes > 0):
            raise ValueError("cancel_notice_minutes must be > 0 when cancel_mode is a window mode")
        if self.reschedule_mode in _WINDOW_MODES and not (self.reschedule_notice_minutes and self.reschedule_notice_minutes > 0):
            raise ValueError("reschedule_notice_minutes must be > 0 when reschedule_mode is a window mode")
        return self


# same as Create — all fields are mutable
class BookingLinkUpdate(BaseModel):
    slug: str = Field(pattern=_SLUG_PATTERN, max_length=100)
    booking_type_id: int | None = None
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX_LENGTH)
    recurring: bool = True
    # Narrowed to what generation is tested for — the DB CHECKs say the same thing, this just makes
    # it a 422 instead of an IntegrityError. Widen alongside models.FREQ_DAYS/SUPPORTED_INTERVALS.
    freq: Literal["WEEKLY"] = "WEEKLY"
    interval: Literal[1] = 1
    duration_minutes: int
    min_duration_minutes: int | None = None
    max_duration_minutes: int | None = None
    count: int | None = None
    expires_on: date | None = None
    booker_can_set_recur_until: bool = False
    booker_can_set_count: bool = False

    price: float | None = None
    cancel_mode: str
    cancel_notice_minutes: int | None = None
    reschedule_mode: str
    reschedule_notice_minutes: int | None = None
    series_cancel_mode: str
    series_reschedule_mode: str

    buffer_minutes: int | None = None
    interval_minutes: int | None = None
    limit_duration_minutes: int | None = None
    limit_per_day: int | None = None
    limit_per_week: int | None = None
    limit_per_month: int | None = None
    limit_per_booker: int | None = None
    limit_future_bookings_days: int | None = None
    only_show_first_slot: bool | None = None

    availability: list[TutorAvailability] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_recurrence(self):
        _validate_recurrence(self.count, self.expires_on, self.booker_can_set_recur_until, self.booker_can_set_count)
        if self.cancel_mode not in _VALID_MODES:
            raise ValueError(f"cancel_mode must be one of {_VALID_MODES}")
        if self.reschedule_mode not in _VALID_MODES:
            raise ValueError(f"reschedule_mode must be one of {_VALID_MODES}")
        if self.series_cancel_mode not in _SERIES_MODES:
            raise ValueError(f"series_cancel_mode must be one of {_SERIES_MODES}")
        if self.series_reschedule_mode not in _SERIES_MODES:
            raise ValueError(f"series_reschedule_mode must be one of {_SERIES_MODES}")
        if self.cancel_mode in _WINDOW_MODES and not (self.cancel_notice_minutes and self.cancel_notice_minutes > 0):
            raise ValueError("cancel_notice_minutes must be > 0 when cancel_mode is a window mode")
        if self.reschedule_mode in _WINDOW_MODES and not (self.reschedule_notice_minutes and self.reschedule_notice_minutes > 0):
            raise ValueError("reschedule_notice_minutes must be > 0 when reschedule_mode is a window mode")
        return self


class BookingLinkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    booking_type_id: int | None = None
    status: str
    archived_at: datetime | None = None
    description: str | None = None
    recurring: bool
    freq: str
    interval: int

    duration_minutes: int
    min_duration_minutes: int | None = None
    max_duration_minutes: int | None = None
    count: int | None = None
    expires_on: date | None = None
    booker_can_set_recur_until: bool

    price: float | None = None
    cancel_mode: str
    cancel_notice_minutes: int | None = None
    reschedule_mode: str
    reschedule_notice_minutes: int | None = None
    series_cancel_mode: str
    series_reschedule_mode: str

    buffer_minutes: int | None = None
    interval_minutes: int | None = None
    limit_duration_minutes: int | None = None
    limit_per_day: int | None = None
    limit_per_week: int | None = None
    limit_per_month: int | None = None
    limit_per_booker: int | None = None
    limit_future_bookings_days: int | None = None
    only_show_first_slot: bool | None = None

    availability: list[BookingLinkAvailabilityResponse] = []


_VALID_REQUEST_TYPES = ('cancel_occurrence', 'reschedule_occurrence', 'cancel_series', 'reschedule_series')
_RESCHEDULE_TYPES = ('reschedule_occurrence', 'reschedule_series')
_SERIES_TYPES = ('cancel_series', 'reschedule_series')


class BookingRequestCreate(BaseModel):
    type: str
    # exactly one of these must be provided — mirrors the DB constraint
    booking_id: int | None = None
    booking_series_id: int | None = None
    requested_start: datetime | None = None
    requested_end: datetime | None = None
    requested_timezone: str | None = None  # booker's timezone at time of request; stored as local time, UTC conversion deferred to approve
    requested_tutor_id: int | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def validate_request(self):
        if self.type not in _VALID_REQUEST_TYPES:
            raise ValueError(f"type must be one of {_VALID_REQUEST_TYPES}")

        has_booking = self.booking_id is not None
        has_series = self.booking_series_id is not None
        if has_booking == has_series:  # both set or neither set
            raise ValueError("exactly one of booking_id or booking_series_id must be provided")

        series_type = self.type in _SERIES_TYPES
        if has_series and not series_type:
            raise ValueError("booking_series_id requires type 'cancel_series' or 'reschedule_series'")
        if has_booking and series_type:
            raise ValueError("booking_id requires type 'cancel_occurrence' or 'reschedule_occurrence'")

        if self.type in _RESCHEDULE_TYPES:
            if not self.requested_start or not self.requested_end or not self.requested_tutor_id or not self.requested_timezone:
                raise ValueError("requested_start, requested_end, requested_tutor_id, and requested_timezone are required for reschedule requests")
            if self.requested_end <= self.requested_start:
                raise ValueError("requested_end must be after requested_start")
            # note: requested_start is in booker's local time — no UTC conversion here
        return self


class BookingRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    booking_id: int | None = None
    booking_series_id: int | None = None
    type: str
    status: str
    requested_start: datetime | None = None
    requested_end: datetime | None = None
    requested_timezone: str | None = None
    requested_tutor_id: int | None = None
    reason: str | None = None
    created_at: datetime


class BookingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str = Field(validation_alias="public_id")
    series_id: str | None = Field(default=None, validation_alias="series_public_id")
    tutor_id: int
    booking_link_id: int
    booking_type_id: int | None = None
    student_id: int | None = None
    start: datetime
    end: datetime
    timezone: str
    status: str
    is_no_show: bool
    rescheduled_to: str | None = Field(default=None, validation_alias="rescheduled_to_public_id")
    rescheduled_from: str | None = Field(default=None, validation_alias="rescheduled_from_public_id")
    google_event_id: str
    cancel_mode: str
    cancel_notice_minutes: int | None = None
    reschedule_mode: str
    reschedule_notice_minutes: int | None = None

    # On the schema, not the model, so virtual occurrences get it too — they're built in memory and
    # never have a row to read a property off.
    @computed_field
    @property
    def cancel_action(self) -> str:
        return get_cancel_action(self, minutes_until(self.start))

    @computed_field
    @property
    def reschedule_action(self) -> str:
        return get_reschedule_action(self, minutes_until(self.start))

    student_first: str
    student_last: str
    student_email: str | None = None
    student_phone: str | None = None
    parent_email: str | None = None
    parent_phone: str | None = None
    request: BookingRequestResponse | None = None


class BookingSeriesResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str = Field(validation_alias="public_id")
    tutor_id: int
    booking_link_id: int
    booking_type_id: int | None = None
    student_id: int | None = None
    created: datetime
    last_modified: datetime
    dtstart: datetime
    dtend: datetime
    status: str | None = None
    until: date | None = None
    rescheduled_to: str | None = Field(default=None, validation_alias="rescheduled_to_public_id")
    rescheduled_from: str | None = Field(default=None, validation_alias="rescheduled_from_public_id")
    # Not read from the ORM object (no such attribute exists there) - always set explicitly by the
    # router via is_series_active(series, db), which queries the series' occurrences.
    # See routers/bookings.py response-construction sites.
    is_active: bool | None = None
    google_event_id: str | None = None
    # The frozen policy itself, so an admin can edit it. The occurrence four are the template each
    # future occurrence copies; the series two govern acting on the series.
    cancel_mode: str
    cancel_notice_minutes: int | None = None
    reschedule_mode: str
    reschedule_notice_minutes: int | None = None
    series_cancel_mode: str
    series_reschedule_mode: str
    # Series modes carry no notice window, so the mode IS the verdict — aliased rather than computed,
    # so the frontend reads the same field name on a series as on a booking.
    cancel_action: str = Field(validation_alias="series_cancel_mode")
    reschedule_action: str = Field(validation_alias="series_reschedule_mode")

    student_first: str
    student_last: str
    student_email: str | None = None
    student_phone: str | None = None
    parent_email: str | None = None
    parent_phone: str | None = None
    request: BookingRequestResponse | None = None


def _convert_to_utc(dt: datetime, tz_str: str) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=ZoneInfo(tz_str)).astimezone(timezone.utc)
    return dt.astimezone(timezone.utc)


class BookingCreate(BaseModel):
    tutor_id: int
    booking_link_id: int
    student_id: int | None = None
    start: datetime
    end: datetime
    timezone: str
    recur_until: date | None = None  # only honoured when booking_link.booker_can_set_recur_until=True
    recur_count: int | None = Field(default=None, ge=2)  # only honoured when booking_link.booker_can_set_count=True

    student_first: str
    student_last: str
    student_email: str | None = None
    student_phone: str | None = None
    parent_email: str | None = None
    parent_phone: str | None = None

    @model_validator(mode="after")
    def validate_and_convert(self):
        self.start = _convert_to_utc(self.start, self.timezone)
        self.end = _convert_to_utc(self.end, self.timezone)
        if self.end <= self.start:
            raise ValueError("end must be after start")
        if self.start < datetime.now(timezone.utc):
            raise ValueError("start must be in the future")
        if self.recur_until is not None and self.recur_until < self.start.date():
            raise ValueError("recur_until must be on or after the booking start date")
        if self.student_email is None and self.parent_email is None:
            raise ValueError("at least one of student_email or parent_email is required")
        if self.student_phone is None and self.parent_phone is None:
            raise ValueError("at least one of student_phone or parent_phone is required")
        return self


# Plain-column updates. Everything here is a column write with at most a validation — no side
# effects, no new rows. Anything that creates a resource or runs a calendar saga gets its own route
# (see reschedule), so these never have to branch on which fields changed.
class BookingUpdate(BaseModel):
    booking_link_id: int         # which link's rules govern future reschedules; rejects archived
    booking_type_id: int | None = None  # null clears the kind label — it's optional
    # Frozen at creation, so this PUT is the only way they ever change.
    cancel_mode: str
    cancel_notice_minutes: int | None = None
    reschedule_mode: str
    reschedule_notice_minutes: int | None = None
    student_first: str
    student_last: str
    student_email: str | None = None
    student_phone: str | None = None
    parent_email: str | None = None
    parent_phone: str | None = None
    is_no_show: bool = False

    @model_validator(mode="after")
    def validate_policy(self):
        if self.cancel_mode not in _VALID_MODES:
            raise ValueError(f"cancel_mode must be one of {_VALID_MODES}")
        if self.reschedule_mode not in _VALID_MODES:
            raise ValueError(f"reschedule_mode must be one of {_VALID_MODES}")
        if self.cancel_mode in _WINDOW_MODES and not (self.cancel_notice_minutes and self.cancel_notice_minutes > 0):
            raise ValueError("cancel_notice_minutes must be > 0 when cancel_mode is a window mode")
        if self.reschedule_mode in _WINDOW_MODES and not (self.reschedule_notice_minutes and self.reschedule_notice_minutes > 0):
            raise ValueError("reschedule_notice_minutes must be > 0 when reschedule_mode is a window mode")
        return self

    @model_validator(mode="after")
    def validate_contact(self):
        if self.student_email is None and self.parent_email is None:
            raise ValueError("at least one of student_email or parent_email is required")
        if self.student_phone is None and self.parent_phone is None:
            raise ValueError("at least one of student_phone or parent_phone is required")
        return self


class BookingSeriesUpdate(BaseModel):
    """The series equivalent of BookingUpdate, which a series never had — its bare PUT was the
    reschedule saga until that moved to POST .../reschedule. Excludes dtstart/dtend deliberately:
    moving a series creates a new row, so it can't be a PUT."""
    booking_link_id: int
    booking_type_id: int | None = None
    # The occurrence pair is the template each future occurrence copies; the series pair governs
    # acting on the series itself. Both frozen at creation, so this PUT is the only way they change.
    cancel_mode: str
    cancel_notice_minutes: int | None = None
    reschedule_mode: str
    reschedule_notice_minutes: int | None = None
    series_cancel_mode: str
    series_reschedule_mode: str
    student_first: str
    student_last: str
    student_email: str | None = None
    student_phone: str | None = None
    parent_email: str | None = None
    parent_phone: str | None = None

    @model_validator(mode="after")
    def validate_policy(self):
        if self.cancel_mode not in _VALID_MODES:
            raise ValueError(f"cancel_mode must be one of {_VALID_MODES}")
        if self.reschedule_mode not in _VALID_MODES:
            raise ValueError(f"reschedule_mode must be one of {_VALID_MODES}")
        if self.series_cancel_mode not in _SERIES_MODES:
            raise ValueError(f"series_cancel_mode must be one of {_SERIES_MODES}")
        if self.series_reschedule_mode not in _SERIES_MODES:
            raise ValueError(f"series_reschedule_mode must be one of {_SERIES_MODES}")
        if self.cancel_mode in _WINDOW_MODES and not (self.cancel_notice_minutes and self.cancel_notice_minutes > 0):
            raise ValueError("cancel_notice_minutes must be > 0 when cancel_mode is a window mode")
        if self.reschedule_mode in _WINDOW_MODES and not (self.reschedule_notice_minutes and self.reschedule_notice_minutes > 0):
            raise ValueError("reschedule_notice_minutes must be > 0 when reschedule_mode is a window mode")
        return self

    @model_validator(mode="after")
    def validate_contact(self):
        if self.student_email is None and self.parent_email is None:
            raise ValueError("at least one of student_email or parent_email is required")
        if self.student_phone is None and self.parent_phone is None:
            raise ValueError("at least one of student_phone or parent_phone is required")
        return self


class BookingReschedule(BaseModel):
    tutor_id: int
    start: datetime
    end: datetime
    timezone: str

    @model_validator(mode="after")
    def validate_and_convert(self):
        self.start = _convert_to_utc(self.start, self.timezone)
        self.end = _convert_to_utc(self.end, self.timezone)
        if self.end <= self.start:
            raise ValueError("end must be after start")
        if self.start < datetime.now(timezone.utc):
            raise ValueError("start must be in the future")
        return self


class AvailableSlotResponse(BaseModel):
    tutor_id: int
    start: datetime
    end: datetime


class TutorFacetOption(BaseModel):
    id: int
    first_name: str
    last_name: str


class BookingLinkFacetOption(BaseModel):
    id: int
    slug: str


class BookingTypeFacetOption(BaseModel):
    id: int
    label: str
    color: str | None = None


class StudentFacetOption(BaseModel):
    first_name: str
    last_name: str


class BookingFacets(BaseModel):
    tutors: list[TutorFacetOption]
    booking_links: list[BookingLinkFacetOption]
    booking_types: list[BookingTypeFacetOption]
    students: list[StudentFacetOption]


class BookingListResponse(BaseModel):
    items: list[BookingResponse]
    next_cursor: str | None
    facets: BookingFacets

class BookingPagedListResponse(BaseModel):
    items: list[BookingResponse]
    total: int | None
    has_more: bool
    page_size: int
    facets: BookingFacets


class BookingSeriesListResponse(BaseModel):
    items: list[BookingSeriesResponse]
    facets: BookingFacets


class BookingSeriesOccurrencesResponse(BaseModel):
    """Cursor-paginated occurrence list for one series."""
    items: list[BookingResponse]
    next_cursor: str | None