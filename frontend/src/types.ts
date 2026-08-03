export interface Student {
  id: number
  first_name: string
  last_name: string
  rate: number
  start_date: string
  is_active: boolean
  grade: number | null
  birthday: string | null
  email: string | null
}

export interface Tutor {
  id: number
  first_name: string
  last_name: string
  pay_rate: number
  is_active: boolean
  calendar_id: string | null
  check_calendar_conflicts: boolean
}

export interface Lesson {
  id: number
  student_id: number
  tutor_id: number
  date: string
  hrs: number | null
  fee: number
  tutor_payout: number
  is_fee_overridden: boolean
  is_tutor_payout_overridden: boolean
  pay_status: boolean
  notes: string | null
}


export interface ScheduleDay {
  id: number
  day_of_week: number
  start_time: string
  end_time: string
}

export interface Schedule {
  id: number
  tutor_id: number
  name: string
  is_default: boolean
  timezone: string
  days: ScheduleDay[]
}

export interface LessonEdit {
  date: string
  hrs: number | string
  feeOverride: number | string
  tutorPayOverride: number | string
  payStatus: boolean
  notes: string | null
}

export interface EventTypeAvailability {
  id: number
  event_type_id: number
  tutor_id: number
  schedule_id: number
}

export interface EventType {
  id: number
  name: string
  description: string | null
  recurring: boolean
  duration_minutes: number
  min_duration_minutes: number | null
  max_duration_minutes: number | null
  recur_weeks: number | null
  expires_on: string | null
  booker_can_set_recur_until: boolean
  price: number | null
  buffer_minutes: number | null
  interval_minutes: number | null
  cancel_mode: string | null
  cancel_notice_minutes: number | null
  reschedule_mode: string | null
  reschedule_notice_minutes: number | null
  limit_duration_minutes: number | null
  limit_per_day: number | null
  limit_per_week: number | null
  limit_per_month: number | null
  limit_per_booker: number | null
  limit_future_bookings_days: number | null
  only_show_first_slot: boolean | null
  availability: EventTypeAvailability[]
}

export interface AvailableSlot {
  tutor_id: number
  start: string
  end: string
}

export interface Booking {
  id: string
  series_id: string | null
  tutor_id: number
  event_type_id: number
  student_id: number | null
  start: string
  end: string
  timezone: string
  status: string
  is_no_show: boolean
  rescheduled_to: string | null
  manage_token: string | null
  google_event_id: string
  series_manage_token: string | null
  student_first: string
  student_last: string
  student_email: string | null
  student_phone: string | null
  parent_email: string | null
  parent_phone: string | null
  request: BookingRequest | null
}

export interface BookingSeries {
  id: string
  tutor_id: number
  event_type_id: number
  student_id: number | null
  is_active: boolean
  day_of_week: number
  start_time: string
  end_time: string
  recur_until: string | null
  google_event_id: string | null
  manage_token: string | null
  student_first: string
  student_last: string
  student_email: string | null
  student_phone: string | null
  parent_email: string | null
  parent_phone: string | null
  bookings: Booking[]
  request: BookingRequest | null
}

export interface BookingRequest {
  id: number
  booking_id: number | null
  booking_series_id: number | null
  type: string // 'cancel_occurrence' | 'reschedule_occurrence' | 'cancel_series' | 'reschedule_series'
  status: string // 'pending' | 'approved' | 'denied'
  requested_start: string | null
  requested_end: string | null
  requested_timezone: string | null
  requested_tutor_id: number | null
  reason: string | null
  created_at: string
}