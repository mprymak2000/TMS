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
