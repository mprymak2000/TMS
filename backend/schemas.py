from pydantic import BaseModel, ConfigDict, Field, model_validator
from datetime import date, time, datetime, timezone
from zoneinfo import ZoneInfo

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


class EventTypeAvailabilityCreate(BaseModel):
    event_type_id: int
    tutor_id: int
    schedule_id: int


class EventTypeAvailabilityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_type_id: int
    tutor_id: int
    schedule_id: int


class TutorAvailability(BaseModel):
    tutor_id: int
    schedule_id: int


_VALID_MODES = ('not_allowed', 'auto', 'auto_window_block', 'auto_window_request', 'request', 'request_window')
_WINDOW_MODES = ('auto_window_block', 'auto_window_request', 'request_window')

# policy fields moved directly onto EventType — CancellationPolicy schemas kept for reference
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


def _validate_recurrence(recur_weeks, expires_on, booker_can_set_recur_until):
    if recur_weeks is not None and expires_on is not None:
        raise ValueError("recur_weeks and expires_on are mutually exclusive")
    if expires_on is not None and booker_can_set_recur_until:
        raise ValueError("booker_can_set_recur_until must be False when expires_on is set")
    if recur_weeks is not None and recur_weeks < 2:
        raise ValueError("recur_weeks must be at least 2")
    if expires_on is not None and expires_on <= date.today():
        raise ValueError("expires_on must be in the future")


class EventTypeCreate(BaseModel):
    name: str
    description: str | None = None
    recurring: bool = True
    duration_minutes: int
    min_duration_minutes: int | None = None
    max_duration_minutes: int | None = None
    recur_weeks: int | None = None
    expires_on: date | None = None
    booker_can_set_recur_until: bool = False

    price: float | None = None
    cancel_mode: str | None = None
    cancel_notice_minutes: int | None = None
    reschedule_mode: str | None = None
    reschedule_notice_minutes: int | None = None

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
        _validate_recurrence(self.recur_weeks, self.expires_on, self.booker_can_set_recur_until)
        if self.cancel_mode is not None and self.cancel_mode not in _VALID_MODES:
            raise ValueError(f"cancel_mode must be one of {_VALID_MODES}")
        if self.reschedule_mode is not None and self.reschedule_mode not in _VALID_MODES:
            raise ValueError(f"reschedule_mode must be one of {_VALID_MODES}")
        if self.cancel_mode in _WINDOW_MODES and not (self.cancel_notice_minutes and self.cancel_notice_minutes > 0):
            raise ValueError("cancel_notice_minutes must be > 0 when cancel_mode is a window mode")
        if self.reschedule_mode in _WINDOW_MODES and not (self.reschedule_notice_minutes and self.reschedule_notice_minutes > 0):
            raise ValueError("reschedule_notice_minutes must be > 0 when reschedule_mode is a window mode")
        return self


# same as Create — all fields are mutable
class EventTypeUpdate(BaseModel):
    name: str
    description: str | None = None
    recurring: bool = True
    duration_minutes: int
    min_duration_minutes: int | None = None
    max_duration_minutes: int | None = None
    recur_weeks: int | None = None
    expires_on: date | None = None
    booker_can_set_recur_until: bool = False

    price: float | None = None
    cancel_mode: str | None = None
    cancel_notice_minutes: int | None = None
    reschedule_mode: str | None = None
    reschedule_notice_minutes: int | None = None

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
        _validate_recurrence(self.recur_weeks, self.expires_on, self.booker_can_set_recur_until)
        if self.cancel_mode is not None and self.cancel_mode not in _VALID_MODES:
            raise ValueError(f"cancel_mode must be one of {_VALID_MODES}")
        if self.reschedule_mode is not None and self.reschedule_mode not in _VALID_MODES:
            raise ValueError(f"reschedule_mode must be one of {_VALID_MODES}")
        if self.cancel_mode in _WINDOW_MODES and not (self.cancel_notice_minutes and self.cancel_notice_minutes > 0):
            raise ValueError("cancel_notice_minutes must be > 0 when cancel_mode is a window mode")
        if self.reschedule_mode in _WINDOW_MODES and not (self.reschedule_notice_minutes and self.reschedule_notice_minutes > 0):
            raise ValueError("reschedule_notice_minutes must be > 0 when reschedule_mode is a window mode")
        return self


class EventTypeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None
    recurring: bool

    duration_minutes: int
    min_duration_minutes: int | None = None
    max_duration_minutes: int | None = None
    recur_weeks: int | None = None
    expires_on: date | None = None
    booker_can_set_recur_until: bool

    price: float | None = None
    cancel_mode: str | None = None
    cancel_notice_minutes: int | None = None
    reschedule_mode: str | None = None
    reschedule_notice_minutes: int | None = None

    buffer_minutes: int | None = None
    interval_minutes: int | None = None
    limit_duration_minutes: int | None = None
    limit_per_day: int | None = None
    limit_per_week: int | None = None
    limit_per_month: int | None = None
    limit_per_booker: int | None = None
    limit_future_bookings_days: int | None = None
    only_show_first_slot: bool | None = None

    availability: list[EventTypeAvailabilityResponse] = []


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
    event_type_id: int
    student_id: int | None = None
    start: datetime
    end: datetime
    timezone: str
    status: str
    is_no_show: bool
    rescheduled_to: str | None = Field(default=None, validation_alias="rescheduled_to_public_id")
    google_event_id: str
    cancel_action: str
    cancel_blocked_reason: str | None = None
    reschedule_action: str
    reschedule_blocked_reason: str | None = None

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
    event_type_id: int
    student_id: int | None = None
    is_active: bool
    start_day_of_week: int
    end_day_of_week: int
    start_time: time
    end_time: time
    timezone: str | None = None  # redundant — always Settings.business_timezone; nullable pending refactor
    recur_until: date | None = None
    google_event_id: str | None = None
    cancel_action: str
    cancel_blocked_reason: str | None = None
    reschedule_action: str
    reschedule_blocked_reason: str | None = None

    student_first: str
    student_last: str
    student_email: str | None = None
    student_phone: str | None = None
    parent_email: str | None = None
    parent_phone: str | None = None

    bookings: list[BookingResponse]


class MyBookingsResponse(BaseModel):
    items: list[BookingResponse]
    request: BookingRequestResponse | None = None


def _convert_to_utc(dt: datetime, tz_str: str) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=ZoneInfo(tz_str)).astimezone(timezone.utc)
    return dt.astimezone(timezone.utc)


class BookingCreate(BaseModel):
    tutor_id: int
    event_type_id: int
    student_id: int | None = None
    start: datetime
    end: datetime
    timezone: str
    recur_until: date | None = None  # only honoured when event_type.booker_can_set_recur_until=True

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


class BookingUpdate(BaseModel):
    student_first: str
    student_last: str
    student_email: str | None = None
    student_phone: str | None = None
    parent_email: str | None = None
    parent_phone: str | None = None
    is_no_show: bool = False

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
