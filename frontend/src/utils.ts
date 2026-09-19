import type { Booking, BookingSeries, Contact } from './types'

export const formatDate = (iso: string) =>
    new Date(iso).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })

export const formatTime = (iso: string) =>
    new Date(iso).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })

// Same as formatDate but without the weekday — used where the weekday is already established
// by surrounding context (e.g. a day-of-week section header or series card).
export const formatShortDate = (iso: string) =>
    new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })

// For bare "HH:MM:SS" time strings (no date) stored as UTC
export const formatUTCTime = (timeStr: string) =>
    new Date(`1970-01-01T${timeStr}Z`).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', timeZone: 'UTC' })

// dtstart/dtend are naive local datetime strings (no Z) — series' own business-local wall-clock
// time, not the viewer's. Appending Z is just a neutral, dependency-free way to read the digits
// back out unchanged (any consistent frame round-trips correctly; UTC needs no extra library).
export const weekdayOf = (dtstart: string) => (new Date(dtstart + 'Z').getUTCDay() + 6) % 7  // Mon=0..Sun=6
export const timeOf = (dtstart: string) => dtstart.split('T')[1]

export const extractError = (err: any, fallback: string): string => {
    if (typeof err.detail === 'string') return err.detail
    console.error(err.detail?.map((e: any) => `${e.loc.join('.')} - ${e.msg}`).join('\n'))
    return fallback
}

// Deterministic, non-identity-based color assignment — every tutor consistently gets
// the same color (keyed off their id, not their name), so no specific person is
// hardcoded into the app's source code.
const TUTOR_BUBBLE_COLORS = [
    'bg-emerald-500 text-white',
    'bg-yellow-400 text-gray-800',
    'bg-sky-500 text-white',
    'bg-rose-400 text-white',
    'bg-violet-500 text-white',
    'bg-orange-400 text-white',
    'bg-teal-500 text-white',
    'bg-pink-400 text-white',
]

export const tutorBubbleClass = (t: { id: number }) =>
    TUTOR_BUBBLE_COLORS[t.id % TUTOR_BUBBLE_COLORS.length]

// Whoever the session is for, as shown on a row. Reads through the FK, so a correction on the
// contact reaches every booking that person has — there's nothing frozen on the booking to go stale.
export const attendeeName = (r: { attendee: Contact }): string =>
    `${r.attendee.first_name} ${r.attendee.last_name}`

// What the two policy dialogs hand back. The occurrence four are on both Booking and
// BookingSeries; the series two only on BookingSeries.
export interface OccurrencePolicyFields {
    cancel_mode: string
    cancel_notice_minutes: number | null
    reschedule_mode: string
    reschedule_notice_minutes: number | null
}

export interface SeriesPolicyFields {
    series_cancel_mode: string
    series_reschedule_mode: string
}

// Both PUTs are full replacements, not patches: a field left out is either rejected as missing or
// silently reset to its default (booking_type_id -> null). So every caller sends the whole row and
// overrides only what it means to change. These mirror BookingUpdate / BookingSeriesUpdate in
// schemas.py — keep them in step.
export const bookingPayload = (b: Booking) => ({
    booking_link_id: b.booking_link_id,
    booking_type_id: b.booking_type_id,
    cancel_mode: b.cancel_mode,
    cancel_notice_minutes: b.cancel_notice_minutes,
    reschedule_mode: b.reschedule_mode,
    reschedule_notice_minutes: b.reschedule_notice_minutes,
    is_no_show: b.is_no_show,
})

export const seriesPayload = (s: BookingSeries) => ({
    booking_link_id: s.booking_link_id,
    booking_type_id: s.booking_type_id,
    cancel_mode: s.cancel_mode,
    cancel_notice_minutes: s.cancel_notice_minutes,
    reschedule_mode: s.reschedule_mode,
    reschedule_notice_minutes: s.reschedule_notice_minutes,
    series_cancel_mode: s.series_cancel_mode,
    series_reschedule_mode: s.series_reschedule_mode,
})

export const tutorInitials = (t: { first_name: string; last_name: string }) =>
    `${t.first_name[0]}${t.last_name[0]}`.toUpperCase()

// Date-math primitives shared by BookingPage.tsx (slot-picker calendar), useLessons.ts
// (week/month grouping), and Bookings.tsx (Day/Week/Month timeline nav).
export const addDays = (d: Date, n: number) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n)
export const startOfWeek = (d: Date) => { const day = d.getDay() || 7; return addDays(d, 1 - day) } // Monday-start
export const startOfMonth = (d: Date) => new Date(d.getFullYear(), d.getMonth(), 1)
export const endOfMonth = (d: Date) => new Date(d.getFullYear(), d.getMonth() + 1, 0)
export const toLocalDateStr = (d: Date) => d.toLocaleDateString('en-CA') // YYYY-MM-DD

// Inverse of toLocalDateStr. Deliberately not `new Date(s)` — a date-only ISO string parses as
// UTC midnight per spec, which shifts to the previous local day west of UTC.
export const parseLocalDateStr = (s: string): Date => {
    const [y, m, d] = s.split('-').map(Number)
    return new Date(y, m - 1, d)
}

// weekdayOf's convention (and BookingSeries.dtstart's derived weekday): 0 = Monday .. 6 = Sunday.
export const DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
export const DAY_LABELS = DAY_NAMES.map(d => d.slice(0, 3))
