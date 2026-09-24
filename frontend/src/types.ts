// A person. Created by the booking that names them, never by signing up. Payer, attendee, or both.
export interface Contact {
  id: number
  first_name: string
  last_name: string
  email: string | null   // null for a dependent who has none of their own
  phone: string | null
  verified_at: string | null   // set once they prove the inbox; nothing writes it until auth
}

// What GET /contacts/ returns: a Contact plus how they've actually been used. Roles are derived
// from the bookings at read time, never stored on the person — the same human is a payer on one
// booking and an attendee on another. 0/0 means added by hand, not yet booked.
export interface ContactListRow extends Contact {
  created: string
  enrollment: Enrollment | null   // null for anyone not enrolled
}

// Page numbers, not a cursor: the roster is jumped around and shows a total, which is the random
// access a cursor trades away. Opposite call from bookings, for the opposite access pattern.
export interface ContactPagedResponse {
  items: ContactListRow[]
  total: number
}

export interface ContactRelationships {
  manages: Contact[]
  managed_by: Contact[]
}

// One stint of being a client here. Many per contact, at most one open — clients leave for the
// summer and come back, sometimes at a new rate, and terms belong to the stint rather than the
// person. `ended_on` null means it's the current one.
export interface Enrollment {
  id: number
  contact_id: number
  started_on: string
  ended_on: string | null
  rate_unit: 'per_session' | 'per_hour' | 'per_month' | null
  rate: number | null
  payer_id: number | null
  grade: number | null
  birthday: string | null
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
  enrollment_id: number
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

export interface BookingLinkAvailability {
  id: number
  booking_link_id: number
  tutor_id: number
  schedule_id: number
}

export type BookingLinkStatus = 'active' | 'paused' | 'archived'

export interface BookingType {
  id: number
  label: string
  color: string | null
}

export interface BookingLink {
  id: number
  slug: string
  booking_type_id: number | null
  status: BookingLinkStatus
  archived_at: string | null
  description: string | null
  recurring: boolean
  duration_minutes: number
  min_duration_minutes: number | null
  max_duration_minutes: number | null
  count: number | null
  expires_on: string | null
  booker_can_set_recur_until: boolean
  booker_can_set_count: boolean
  price: number | null
  buffer_minutes: number | null
  interval_minutes: number | null
  cancel_mode: string
  cancel_notice_minutes: number | null
  reschedule_mode: string
  reschedule_notice_minutes: number | null
  // Acting on a whole series — no notice window, so the mode is the verdict.
  series_cancel_mode: string
  series_reschedule_mode: string
  limit_duration_minutes: number | null
  limit_per_day: number | null
  limit_per_week: number | null
  limit_per_month: number | null
  limit_per_booker: number | null
  limit_future_bookings_days: number | null
  only_show_first_slot: boolean | null
  availability: BookingLinkAvailability[]
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
  booking_link_id: number
  booking_type_id: number | null
  // Who is responsible and who attends. The same contact when someone books for themselves.
  payer: Contact
  attendee: Contact
  sms_opt_in: boolean
  guest_reminder_phone: string | null   // frozen for guests; null once the contact is claimed
  start: string
  end: string
  timezone: string
  status: string
  is_no_show: boolean
  rescheduled_to: string | null
  rescheduled_from: string | null
  google_event_id: string
  // Frozen at creation; cancel_action is the verdict computed from these plus time-until.
  cancel_mode: string
  cancel_notice_minutes: number | null
  reschedule_mode: string
  reschedule_notice_minutes: number | null
  cancel_action: 'auto' | 'request' | 'blocked'
  reschedule_action: 'auto' | 'request' | 'blocked'
  request: BookingRequest | null
}

export interface BookingSeries {
  id: string
  tutor_id: number
  booking_link_id: number
  booking_type_id: number | null
  payer: Contact
  attendee: Contact
  sms_opt_in: boolean
  guest_reminder_phone: string | null
  created: string
  last_modified: string
  dtstart: string
  dtend: string
  status: string | null
  until: string | null
  rescheduled_to: string | null
  rescheduled_from: string | null
  is_active: boolean
  google_event_id: string | null
  // The occurrence four are the template each occurrence copies; the series two govern acting on
  // the series itself, and cancel_action here is just series_cancel_mode (no notice window).
  cancel_mode: string
  cancel_notice_minutes: number | null
  reschedule_mode: string
  reschedule_notice_minutes: number | null
  series_cancel_mode: string
  series_reschedule_mode: string
  cancel_action: 'auto' | 'request' | 'blocked'
  reschedule_action: 'auto' | 'request' | 'blocked'
  request: BookingRequest | null
}

export interface TutorFacetOption {
  id: number
  first_name: string
  last_name: string
}

export interface BookingLinkFacetOption {
  id: number
  slug: string
}

export interface BookingTypeFacetOption {
  id: number
  label: string
  color: string | null
}

// An ordinary FK facet now that a booking points at a contact — no more "First|Last" pairs.
export interface AttendeeFacetOption {
  id: number
  first_name: string
  last_name: string
}

export interface BookingFacets {
  tutors: TutorFacetOption[]
  booking_links: BookingLinkFacetOption[]
  booking_types: BookingTypeFacetOption[]
  attendees: AttendeeFacetOption[]
}

export interface BookingListResponse {
  items: Booking[]
  total: number | null
  has_more: boolean
  page_size: number
  facets: BookingFacets
}

export interface BookingSeriesListResponse {
  items: BookingSeries[]
  facets: BookingFacets
}

export interface BookingSeriesOccurrencesResponse {
  items: Booking[]
  total: number | null
  has_more: boolean
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