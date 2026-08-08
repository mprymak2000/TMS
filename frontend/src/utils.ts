export const formatDate = (iso: string) =>
    new Date(iso).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })

export const formatTime = (iso: string) =>
    new Date(iso).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })

// For bare "HH:MM:SS" time strings (no date) stored as UTC
export const formatUTCTime = (timeStr: string) =>
    new Date(`1970-01-01T${timeStr}Z`).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', timeZone: 'UTC' })

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

// start_day_of_week convention used by BookingSeries: 0 = Monday .. 6 = Sunday.
export const DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
export const DAY_LABELS = DAY_NAMES.map(d => d.slice(0, 3))
