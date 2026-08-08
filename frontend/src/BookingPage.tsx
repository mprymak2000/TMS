import { useEffect, useState } from 'react'
import outlookIcon from './assets/outlook-icon.svg'
import { TextInput, Select, Button } from '@mantine/core'
import type { Tutor, EventType, EventTypeAvailability, AvailableSlot } from './types'
import { useLocation, useParams } from 'react-router'
import { extractError, formatUTCTime, addDays, startOfWeek, startOfMonth, endOfMonth, toLocalDateStr, DAY_NAMES } from './utils'

/*
  BookingPage — Custom duration
  - If eventType.allow_custom_duration, show a duration slider/NumberInput on the contact step (between min and max)
  - selectedDuration state, defaults to min_duration_minutes
  - buildPayload computes end = new Date(selectedSlot.start + selectedDuration * 60000)
  */
interface LoadErrors {
    eventType?: string
    tutors?: string
    unknown?: string
}

interface ContactForm {
    studentFirst: string
    studentLast: string
    studentEmail: string
    studentPhone: string
    parentEmail: string
    parentPhone: string
}

interface ContactFormErrors {
    studentFirst?: string
    studentLast?: string
    contactEmail?: string
    contactPhone?: string
}

interface ContactFormTouched {
    studentFirst?: boolean
    studentLast?: boolean
    contactEmail?: boolean
    contactPhone?: boolean
}

const validate = (form: ContactForm): ContactFormErrors => {
    const errs: ContactFormErrors = {}
    if (!form.studentFirst.trim()) errs.studentFirst = 'First name is required'
    if (!form.studentLast.trim()) errs.studentLast = 'Last name is required'
    if (!form.studentEmail.trim() && !form.parentEmail.trim()) errs.contactEmail = 'At least one email is required'
    if (!form.studentPhone.trim() && !form.parentPhone.trim()) errs.contactPhone = 'At least one phone number is required'
    return errs
}

const toCalDate = (utcIso: string) =>
    new Date(utcIso).toISOString().replace(/[-:.]/g, '').slice(0, 15) + 'Z'

const buildGoogleCalUrl = (start: string, end: string, title: string, description: string | null) => {
    const p = new URLSearchParams({ action: 'TEMPLATE', text: title, dates: `${toCalDate(start)}/${toCalDate(end)}` })
    if (description) p.set('details', description)
    return `https://calendar.google.com/calendar/render?${p}`
}

const buildOutlookUrl = (base: string, start: string, end: string, title: string, description: string | null) => {
    const p = new URLSearchParams({ subject: title, startdt: new Date(start).toISOString(), enddt: new Date(end).toISOString(), allday: 'false' })
    if (description) p.set('body', description)
    return `${base}/calendar/0/action/compose?${p}`
}

const buildIcsBlobUrl = (start: string, end: string, title: string, description: string | null, uid: string | null) => {
    const fmt = (iso: string) => new Date(iso).toISOString().replace(/[-:.]/g, '').slice(0, 15) + 'Z'
    const lines = [
        'BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//TMS//TMS//EN', 'BEGIN:VEVENT',
        `UID:${uid ?? Date.now()}@tms`, `DTSTAMP:${fmt(new Date().toISOString())}`,
        `DTSTART:${fmt(start)}`, `DTEND:${fmt(end)}`, `SUMMARY:${title}`,
        ...(description ? [`DESCRIPTION:${description}`] : []),
        'END:VEVENT', 'END:VCALENDAR',
    ]
    return URL.createObjectURL(new Blob([lines.join('\r\n')], { type: 'text/calendar' }))
}

const WEEK_DAYS = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN']
const MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']

const localDateOf = (utcIso: string, tz: string) =>
    new Intl.DateTimeFormat('en-CA', { timeZone: tz }).format(new Date(utcIso))
const localTimeOf = (utcIso: string, tz: string) =>
    new Intl.DateTimeFormat('en-US', { timeZone: tz, hour: 'numeric', minute: '2-digit', hour12: true }).format(new Date(utcIso)).replace(' ', ' ')
const localLongDateOf = (utcIso: string, tz: string) =>
    new Intl.DateTimeFormat('en-US', { timeZone: tz, weekday: 'long', month: 'long', day: 'numeric' }).format(new Date(utcIso))

const BookingPage = () => {
    const { eventTypeId } = useParams<{ eventTypeId: string }>()
    const location = useLocation()
    const [rescheduleFromId, setRescheduleFromId] = useState<string | null>(location.state?.rescheduleFromId ?? null)
    const [rescheduleSeriesId, setRescheduleSeriesId] = useState<string | null>(location.state?.rescheduleSeriesId ?? null)
    const requestRescheduleRef = location.state?.requestRescheduleRef ?? null
    const originalStart = location.state?.originalStart ?? null
    const originalEnd = location.state?.originalEnd ?? null
    const originalDayOfWeek: number | null = location.state?.originalDayOfWeek ?? null
    const originalStartTime: string | null = location.state?.originalStartTime ?? null

    const [eventType, setEventType] = useState<EventType | null>(null)
    const [tutors, setTutors] = useState<Tutor[]>([])
    const [slots, setSlots] = useState<AvailableSlot[]>([])
    const [loadErrors, setLoadErrors] = useState<LoadErrors>({})
    const [loadSlotsError, setLoadSlotsError] = useState<string | null>(null)
    const [view, setView] = useState<'month' | 'week'>('month')
    const [timezone, setTimezone] = useState(Intl.DateTimeFormat().resolvedOptions().timeZone)
    const [selectedTutorId, setSelectedTutorId] = useState<string | null>(null)
    const [recurUntil, setRecurUntil] = useState<string | null>(null)
    const [currentDate, setCurrentDate] = useState(new Date())
    const [selectedDate, setSelectedDate] = useState<string | null>(null)
    const [selectedSlot, setSelectedSlot] = useState<AvailableSlot | null>(null)
    const [step, setStep] = useState<'pick' | 'contact' | 'done'>('pick')
    const [confirmingReschedule, setConfirmingReschedule] = useState(false)
    const [loading, setLoading] = useState(false)
    const [contact, setContact] = useState<ContactForm>({
        studentFirst: '', studentLast: '',
        studentEmail: '', studentPhone: '',
        parentEmail: '', parentPhone: '',
    })
    const [contactErrors, setContactErrors] = useState<ContactFormErrors>({})
    const [touched, setTouched] = useState<ContactFormTouched>({})
    const [submitError, setSubmitError] = useState<string | null>(null)
    const [submitting, setSubmitting] = useState(false)
    const [bookingId, setBookingId] = useState<string | null>(null)
    const [requestSubmitted, setRequestSubmitted] = useState(false)

    const loadData = async () => {
        try {
            //todo: once GET /tutors supports ?ids=... query param, fetch only linked tutors instead of all
            const [eventTypeRes, tutorsRes] = await Promise.all([
                fetch(`${import.meta.env.VITE_API_URL}/event_types/${eventTypeId}`),
                fetch(`${import.meta.env.VITE_API_URL}/tutors`),
            ])
            if (!eventTypeRes.ok) {
                const err = await eventTypeRes.json()
                setLoadErrors(prev => ({ ...prev, eventType: extractError(err, 'Failed to load event type') }))
                return
            }
            if (!tutorsRes.ok) {
                const err = await tutorsRes.json()
                setLoadErrors(prev => ({ ...prev, tutors: extractError(err, 'Failed to load tutors') }))
                return
            }
            const eventTypeData = await eventTypeRes.json()
            const tutorsData = await tutorsRes.json()
            const linkedIds = new Set(eventTypeData.availability.map((a: EventTypeAvailability) => a.tutor_id))
            setEventType(eventTypeData)
            setTutors(tutorsData.filter((t: Tutor) => linkedIds.has(t.id)))
        } catch (error) {
            console.error('Error loading data:', error)
            setLoadErrors(prev => ({ ...prev, unknown: 'An unknown error occurred' }))
        }
    }

    useEffect(() => { loadData() }, [eventTypeId])

    // autoselect tutor if there is only one choice
    useEffect(() => {
        if (tutors.length === 0 && eventType !== null) setLoadErrors(prev => ({ ...prev, tutors: 'No tutors available for this event type' }))
        else if (tutors.length === 1) setSelectedTutorId(String(tutors[0].id))
        else if ((rescheduleFromId || rescheduleSeriesId) && location.state?.tutorId) { setSelectedTutorId(String(location.state.tutorId)) }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [tutors])

    useEffect(() => {
        if (!rescheduleFromId && !rescheduleSeriesId) return
        setContact(contact => ({
            ...contact,
            studentFirst: location.state?.studentFirst ?? '',
            studentLast: location.state?.studentLast ?? '',
            studentEmail: location.state?.studentEmail ?? '',
            studentPhone: location.state?.studentPhone ?? '',
            parentEmail: location.state?.parentEmail ?? '',
            parentPhone: location.state?.parentPhone ?? '',
        }))
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])

    const loadSlots = async () => {
        if (!eventType || !selectedTutorId) return
        const dateFrom = view === 'month' ? startOfMonth(currentDate) : startOfWeek(currentDate)
        const dateTo = view === 'month' ? endOfMonth(currentDate) : addDays(startOfWeek(currentDate), 6)
        const timeMin = new Date(Date.UTC(dateFrom.getFullYear(), dateFrom.getMonth(), dateFrom.getDate())).toISOString()
        const timeMax = new Date(Date.UTC(dateTo.getFullYear(), dateTo.getMonth(), dateTo.getDate(), 23, 59, 59)).toISOString()
        const params = new URLSearchParams({ event_type_id: String(eventType.id), time_min: timeMin, time_max: timeMax })
        params.append('tutor_ids', selectedTutorId)
        setLoadSlotsError(null)
        setLoading(true)
        try {
            const res = await fetch(`${import.meta.env.VITE_API_URL}/available-slots?${params}`)
            if (!res.ok) {
                const err = await res.json()
                setSlots([])
                setLoadSlotsError(extractError(err, 'Failed to load available slots'))
                return
            }
            const data: AvailableSlot[] = await res.json()
            data.sort((a, b) => a.start.localeCompare(b.start))
            setSlots(data)
        } catch (error) {
            console.error('Error loading slots:', error)
            setLoadSlotsError('An unknown error occurred while loading slots')
        } finally { setLoading(false) }
    }

    useEffect(() => { loadSlots() }, 
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [eventType, currentDate, view, selectedTutorId])

    const now = new Date()
    const slotsByDate = slots.reduce((acc, slot) => {
        if (new Date(slot.start) < now) return acc
        const date = localDateOf(slot.start, timezone)
        if (!acc[date]) acc[date] = []
        acc[date].push(slot)
        return acc
    }, {} as Record<string, AvailableSlot[]>)

    const touchAll = () => setTouched({ studentFirst: true, studentLast: true, contactEmail: true, contactPhone: true })

    const buildSubmitPayload = () => ({
        tutor_id: selectedSlot!.tutor_id,
        event_type_id: eventType!.id,
        start: selectedSlot!.start,
        end: selectedSlot!.end,
        timezone: timezone,
        recur_until: recurUntil || null,
        student_first: contact.studentFirst,
        student_last: contact.studentLast,
        student_email: contact.studentEmail || null,
        student_phone: contact.studentPhone || null,
        parent_email: contact.parentEmail || null,
        parent_phone: contact.parentPhone || null,
    })

    const buildReschedulePayload = () => ({
        tutor_id: selectedSlot!.tutor_id,
        start: selectedSlot!.start,
        end: selectedSlot!.end,
        timezone: timezone,
    })

    const prevPeriod = () => {
        setSelectedDate(null)
        if (view === 'month') setCurrentDate(prev => new Date(prev.getFullYear(), prev.getMonth() - 1, 1))
        else setCurrentDate(prev => addDays(startOfWeek(prev), -7))
    }

    const nextPeriod = () => {
        setSelectedDate(null)
        if (view === 'month') setCurrentDate(prev => new Date(prev.getFullYear(), prev.getMonth() + 1, 1))
        else setCurrentDate(prev => addDays(startOfWeek(prev), 7))
    }

    const handleSlotSelect = (slot: AvailableSlot) => {
        setSelectedSlot(slot)
        if (rescheduleFromId || rescheduleSeriesId || requestRescheduleRef) setConfirmingReschedule(true)
        else setStep('contact')
    }

    const handleReschedule = async () => {
        if (!selectedSlot || !eventType) return
        setSubmitting(true)
        try {
            const res = await fetch(`${import.meta.env.VITE_API_URL}/bookings/${rescheduleFromId}/reschedule`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(buildReschedulePayload()),
            })
            if (!res.ok) { 
                const error = await res.json()
                setSubmitError(extractError(error, 'Failed to reschedule booking'))
                return 
            }
            const booking = await res.json()
            setBookingId(booking.id)
            setStep('done')
        } catch (error) {
            console.error(error)
            setSubmitError('An unknown error occurred')
        } finally {
            setSubmitting(false)
        }
    }

    const handleSeriesReschedule = async () => {
        if (!selectedSlot || !eventType) return
        setSubmitting(true)
        try {
            const res = await fetch(`${import.meta.env.VITE_API_URL}/bookings/booking-series/${rescheduleSeriesId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(buildReschedulePayload()),
            })
            if (!res.ok) {
                const error = await res.json()
                setSubmitError(extractError(error, 'Failed to reschedule booking series'))
                return
            }
            setStep('done')
        } catch (error) {
            console.error(error)
            setSubmitError('An unknown error occurred while rescheduling booking series')
        } finally {
            setSubmitting(false)
        }
    }

    const handleRefReschedule = async () => {
        if (!selectedSlot || !eventType) return
        setSubmitting(true)
        try {
            const res = await fetch(`${import.meta.env.VITE_API_URL}/bookings/manage-occurrence/${requestRescheduleRef}/reschedule`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(buildReschedulePayload()),
            })
            if (!res.ok) {
                const err = await res.json()
                setSubmitError(extractError(err, 'Failed to submit reschedule request'))
                return
            }
            setRequestSubmitted(true)
            setStep('done')
        } catch (error) {
            console.error(error)
            setSubmitError('An unknown error occurred while submitting reschedule request')
        } finally {
            setSubmitting(false)
        }
    }

    const handleSubmit = async () => {
        if (!selectedSlot || !eventType) return
        const errors = validate(contact)
        if (Object.keys(errors).length > 0) { setContactErrors(errors); touchAll(); return }
        setSubmitting(true)
        try {
            const res = await fetch(`${import.meta.env.VITE_API_URL}/bookings/`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(buildSubmitPayload()),
            })
            if (!res.ok) { 
                const error = await res.json();
                setSubmitError(extractError(error, 'Failed to create booking'));
                return 
            }
            const booking = await res.json()
            setBookingId(booking.id)
            setStep('done')
        } catch (error) {
            console.error(error)
            setSubmitError('An unknown error occurred, please try again')
        } finally {
            setSubmitting(false)
        }
    }

    const resetBooking = () => {
        setStep('pick'); setSelectedSlot(null); setSelectedDate(null); setConfirmingReschedule(false);
        setContact({ studentFirst: '', studentLast: '', studentEmail: '', studentPhone: '', parentEmail: '', parentPhone: '' })
        setContactErrors({}); setTouched({}); setSubmitError(null); setRecurUntil(null)
        setRescheduleFromId(null); setRescheduleSeriesId(null)
        setSlots([])
        loadSlots()
    }

    const today = toLocalDateStr(new Date())

    const calDays: Date[] = (() => {
        const calStart = startOfWeek(startOfMonth(currentDate))
        const calEnd = addDays(startOfWeek(endOfMonth(currentDate)), 6)
        const days: Date[] = []
        let d = calStart
        while (toLocalDateStr(d) <= toLocalDateStr(calEnd)) { days.push(d); d = addDays(d, 1) }
        return days
    })()

    const weekDays = Array.from({ length: 7 }, (_, i) => addDays(startOfWeek(currentDate), i))

    const periodLabel = view === 'month'
        ? `${MONTH_NAMES[currentDate.getMonth()]} ${currentDate.getFullYear()}`
        : (() => {
            const ws = startOfWeek(currentDate)
            const we = addDays(ws, 6)
            return ws.getMonth() === we.getMonth()
                ? `${MONTH_NAMES[ws.getMonth()]} ${ws.getFullYear()}`
                : `${MONTH_NAMES[ws.getMonth()]} – ${MONTH_NAMES[we.getMonth()]} ${we.getFullYear()}`
        })()

    const selectedTutor = tutors.find(t => String(t.id) === selectedTutorId)

    // ---- DONE ----
    if (step === 'done') {
        return (
            <div className="min-h-screen bg-gradient-to-br from-indigo-50 to-white flex items-center justify-center p-6">
                <div className="bg-white rounded-3xl shadow-lg border border-gray-100 px-12 py-14 max-w-md w-full text-center">
                    <div className="w-16 h-16 bg-green-500 rounded-full flex items-center justify-center mx-auto mb-6 shadow-md">
                        <svg className="w-8 h-8 text-white" fill="none" stroke="currentColor" strokeWidth={2.5} viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                        </svg>
                    </div>
                    <h1 className="text-3xl font-bold text-gray-900 mb-1">{rescheduleSeriesId ? 'Series updated!' : requestSubmitted ? 'Request submitted!' : rescheduleFromId ? 'Rescheduled!' : "You're booked!"}</h1>
                    {eventType && <p className="text-indigo-600 font-medium mb-4">{eventType.name}</p>}
                    {requestSubmitted && <p className="text-sm text-gray-500 mb-4">Your reschedule request has been submitted and an admin will review it shortly.</p>}
                    {selectedSlot && (
                        <div className="bg-gray-50 rounded-2xl px-6 py-4 mb-8 text-left">
                            <p className="text-sm text-gray-500 mb-0.5">Date & time</p>
                            <p className="text-sm font-semibold text-gray-900">{localLongDateOf(selectedSlot.start, timezone)}</p>
                            <p className="text-sm text-gray-600">{localTimeOf(selectedSlot.start, timezone)} – {localTimeOf(selectedSlot.end, timezone)}</p>
                            {selectedTutor && <p className="text-sm text-gray-500 mt-2">with {selectedTutor.first_name} {selectedTutor.last_name}</p>}
                        </div>
                    )}
                    <div className="flex flex-col items-center gap-3 w-full">
                        {selectedSlot && eventType && !rescheduleSeriesId && !requestSubmitted && (
                            <div className="flex flex-col items-center gap-2 w-full">
                                <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1">Add to calendar</p>
                                <div className="flex gap-3">
                                    <a href={buildGoogleCalUrl(selectedSlot.start, selectedSlot.end, eventType.name, eventType.description)} target="_blank" rel="noopener noreferrer" title="Google Calendar"
                                        className="w-11 h-11 flex items-center justify-center bg-white border-2 border-gray-200 rounded-xl hover:border-indigo-300 transition-all shadow-sm">
                                        <svg viewBox="0 0 24 24" className="w-5 h-5">
                                            <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
                                            <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
                                            <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.84z" fill="#FBBC05"/>
                                            <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
                                        </svg>
                                    </a>
                                    <a href={buildOutlookUrl('https://outlook.live.com', selectedSlot.start, selectedSlot.end, eventType.name, eventType.description)} target="_blank" rel="noopener noreferrer" title="Outlook.com"
                                        className="w-11 h-11 flex items-center justify-center bg-white border-2 border-gray-200 rounded-xl hover:border-indigo-300 transition-all shadow-sm">
                                        <img src={outlookIcon} className="w-5 h-5" alt="Outlook" />
                                    </a>
                                    <a href={buildOutlookUrl('https://outlook.office.com', selectedSlot.start, selectedSlot.end, eventType.name, eventType.description)} target="_blank" rel="noopener noreferrer" title="Office 365"
                                        className="w-11 h-11 flex items-center justify-center bg-white border-2 border-gray-200 rounded-xl hover:border-indigo-300 transition-all shadow-sm">
                                        <svg viewBox="0 0 24 24" className="w-5 h-5" fill="none">
                                            <rect x="1" y="1" width="10.5" height="10.5" fill="#F25022"/>
                                            <rect x="12.5" y="1" width="10.5" height="10.5" fill="#7FBA00"/>
                                            <rect x="1" y="12.5" width="10.5" height="10.5" fill="#00A4EF"/>
                                            <rect x="12.5" y="12.5" width="10.5" height="10.5" fill="#FFB900"/>
                                        </svg>
                                    </a>
                                    <a href={buildIcsBlobUrl(selectedSlot.start, selectedSlot.end, eventType.name, eventType.description, bookingId)} download="booking.ics" title="Other"
                                        className="w-11 h-11 flex items-center justify-center bg-white border-2 border-gray-200 rounded-xl hover:border-indigo-300 transition-all shadow-sm text-gray-500">
                                        <svg viewBox="0 0 24 24" className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
                                            <rect x="3" y="4" width="18" height="18" rx="2"/>
                                            <line x1="16" y1="2" x2="16" y2="6"/>
                                            <line x1="8" y1="2" x2="8" y2="6"/>
                                            <line x1="3" y1="10" x2="21" y2="10"/>
                                            <path d="M12 14v4M10 17l2 2 2-2"/>
                                        </svg>
                                    </a>
                                </div>
                            </div>
                        )}
                        <button onClick={resetBooking} className="text-sm text-gray-400 hover:text-gray-600 transition-colors mt-1">
                            Book another session →
                        </button>
                    </div>
                </div>
            </div>
        )
    }

    // ---- PICK + CONTACT ----
    return (
        <div className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-indigo-50/30 flex items-center justify-center p-4 md:p-8">
            <div className="w-full max-w-5xl bg-white rounded-3xl shadow-xl border border-gray-100 overflow-hidden md:flex min-h-[640px]">

                {/* ---- LEFT PANEL ---- */}
                <div className="md:w-72 bg-gray-950 text-white p-10 flex flex-col shrink-0">

                    {(loadErrors.eventType || loadErrors.tutors || loadErrors.unknown) && (
                        <p className="text-sm text-red-400 mb-4">{loadErrors.eventType || loadErrors.tutors || loadErrors.unknown}</p>
                    )}

                    {eventType ? (
                        <>
                            <p className="text-xs font-semibold text-indigo-400 uppercase tracking-widest mb-3">Schedule a session</p>
                            <h1 className="text-2xl font-bold text-white leading-tight mb-4">{eventType.name}</h1>
                            <div className="flex items-center gap-2 text-gray-400 text-sm mb-4">
                                <svg className="w-4 h-4 shrink-0 text-indigo-400" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                                </svg>
                                {eventType.duration_minutes} minutes
                            </div>
                            {eventType.description && (
                                <p className="text-sm text-gray-400 leading-relaxed">{eventType.description}</p>
                            )}
                            {(rescheduleFromId || rescheduleSeriesId) && (originalStart || originalDayOfWeek !== null) && (
                                <div className="mt-5 p-3 rounded-xl border border-gray-800">
                                    <p className="text-xs text-gray-500 uppercase tracking-widest mb-1.5">
                                        {rescheduleSeriesId ? 'Changing series from' : 'Rescheduling from'}
                                    </p>
                                    {rescheduleSeriesId && originalDayOfWeek !== null && originalStartTime ? (
                                        <p className="text-sm text-gray-500 line-through">
                                            {DAY_NAMES[originalDayOfWeek]}s at {formatUTCTime(originalStartTime)}
                                        </p>
                                    ) : originalStart && (
                                        <>
                                            <p className="text-sm text-gray-500 line-through">{localLongDateOf(originalStart, timezone)}</p>
                                            <p className="text-sm text-gray-500 line-through">
                                                {localTimeOf(originalStart, timezone)}{originalEnd ? ` – ${localTimeOf(originalEnd, timezone)}` : ''}
                                            </p>
                                        </>
                                    )}
                                </div>
                            )}
                        </>
                    ) : (
                        <div className="space-y-3">
                            <div className="h-3 bg-gray-800 rounded-full w-2/3 animate-pulse" />
                            <div className="h-6 bg-gray-800 rounded-xl w-full animate-pulse" />
                            <div className="h-3 bg-gray-800 rounded-full w-1/2 animate-pulse" />
                        </div>
                    )}

                    {/* tutor + timezone grouped below description */}
                    <div className="mt-8 pt-6 border-t border-gray-800 flex flex-col gap-4">
                        {step === 'pick' && tutors.length > 1 && (
                            <Select
                                label="Tutor"
                                placeholder="Select a tutor"
                                searchable
                                data={tutors.map(t => ({ value: String(t.id), label: `${t.first_name} ${t.last_name}` }))}
                                value={selectedTutorId}
                                onChange={val => { setSelectedTutorId(val); setSelectedDate(null) }}
                                size="sm"
                                styles={{ label: { color: '#9ca3af', fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 4 }, input: { backgroundColor: '#111827', border: '1px solid #374151', color: 'white', borderRadius: 8, '&:focus': { backgroundColor: '#111827' }, '&[data-focus]': { backgroundColor: '#111827' } }, dropdown: { background: '#111827', border: '1px solid #374151' }, option: { color: '#d1d5db' } }}
                            />
                        )}
                        <div>
                            <div className="flex items-center gap-1.5 mb-1.5">
                                <svg className="w-3.5 h-3.5 text-gray-500 shrink-0" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                                    <circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 000 20M12 2a14.5 14.5 0 010 20M2 12h20"/>
                                </svg>
                                <span className="text-xs font-semibold text-gray-500 uppercase tracking-widest">Timezone</span>
                            </div>
                            <Select
                                searchable
                                value={timezone}
                                onChange={val => val && setTimezone(val)}
                                data={Intl.supportedValuesOf('timeZone')}
                                size="sm"
                                styles={{
                                    input: { backgroundColor: '#111827', border: '1px solid #374151', color: '#e5e7eb', borderRadius: 8, fontSize: '12px', '&:focus': { backgroundColor: '#111827' }, '&[data-focus]': { backgroundColor: '#111827' } },
                                    dropdown: { background: '#111827', border: '1px solid #374151' },
                                    option: { color: '#d1d5db', fontSize: '12px' },
                                }}
                            />
                        </div>
                    </div>

                    <div className="flex-1" />

                    {/* selected slot summary on contact/confirm step */}
                    {(step === 'contact' || confirmingReschedule) && selectedSlot && (
                        <div className="pt-6 border-t border-gray-800">
                            <p className="text-xs text-gray-500 uppercase tracking-widest mb-2">Selected</p>
                            <p className="text-sm font-semibold text-white leading-snug">{localLongDateOf(selectedSlot.start, timezone)}</p>
                            <p className="text-indigo-400 text-sm font-medium mt-0.5">
                                {localTimeOf(selectedSlot.start, timezone)} – {localTimeOf(selectedSlot.end, timezone)}
                            </p>
                            {selectedTutor && <p className="text-gray-500 text-xs mt-2">with {selectedTutor.first_name} {selectedTutor.last_name}</p>}
                        </div>
                    )}
                </div>

                {/* ---- RIGHT PANEL ---- */}
                <div className="flex-1 p-10 overflow-y-auto">

                    {confirmingReschedule ? (
                        // ---- RESCHEDULE / SERIES CHANGE CONFIRM ----
                        <>
                            <button
                                onClick={() => { setConfirmingReschedule(false); setSelectedSlot(null) }}
                                className="flex items-center gap-1.5 text-sm text-gray-400 hover:text-gray-700 transition-colors mb-8 group"
                            >
                                <svg className="w-4 h-4 group-hover:-translate-x-0.5 transition-transform" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
                                </svg>
                                Back to calendar
                            </button>

                            <h2 className="text-xl font-bold text-gray-900 mb-1">
                                {rescheduleSeriesId ? 'Confirm series change' : requestRescheduleRef ? 'Request reschedule' : 'Confirm reschedule'}
                            </h2>
                            <p className="text-sm text-gray-400 mb-8">
                                {rescheduleSeriesId
                                    ? 'All future occurrences will move to the new time.'
                                    : requestRescheduleRef
                                    ? 'Your reschedule request will be submitted for admin review.'
                                    : 'Your booking will be moved to the new time below.'}
                            </p>

                            <div className="flex flex-col gap-3 max-w-sm">
                                <div className="rounded-2xl border border-gray-100 bg-gray-50 px-5 py-4">
                                    <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">From</p>
                                    {rescheduleSeriesId && originalDayOfWeek !== null && originalStartTime ? (
                                        <p className="text-sm text-gray-400 line-through">
                                            {DAY_NAMES[originalDayOfWeek]}s at {formatUTCTime(originalStartTime)}
                                        </p>
                                    ) : originalStart && (
                                        <>
                                            <p className="text-sm text-gray-400 line-through">{localLongDateOf(originalStart, timezone)}</p>
                                            <p className="text-sm text-gray-400 line-through">
                                                {localTimeOf(originalStart, timezone)}{originalEnd ? ` – ${localTimeOf(originalEnd, timezone)}` : ''}
                                            </p>
                                        </>
                                    )}
                                </div>

                                <div className="flex justify-center text-gray-300">
                                    <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                                        <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                                    </svg>
                                </div>

                                <div className="rounded-2xl border border-indigo-100 bg-indigo-50 px-5 py-4">
                                    <p className="text-xs font-semibold text-indigo-400 uppercase tracking-wide mb-2">To</p>
                                    {selectedSlot && (
                                        rescheduleSeriesId ? (
                                            <>
                                                <p className="text-sm font-semibold text-gray-900">
                                                    {new Intl.DateTimeFormat('en-US', { timeZone: timezone, weekday: 'long' }).format(new Date(selectedSlot.start))}s at {localTimeOf(selectedSlot.start, timezone)}
                                                </p>
                                                <p className="text-sm text-indigo-600 font-medium">
                                                    Starting {localLongDateOf(selectedSlot.start, timezone)}
                                                </p>
                                            </>
                                        ) : (
                                            <>
                                                <p className="text-sm font-semibold text-gray-900">{localLongDateOf(selectedSlot.start, timezone)}</p>
                                                <p className="text-sm text-indigo-600 font-medium">
                                                    {localTimeOf(selectedSlot.start, timezone)} – {localTimeOf(selectedSlot.end, timezone)}
                                                </p>
                                            </>
                                        )
                                    )}
                                </div>

                                {submitError && <p className="text-sm text-red-500 mt-1">{submitError}</p>}

                                <Button size="md" radius="xl" loading={submitting} className="mt-2"
                                    onClick={rescheduleSeriesId ? handleSeriesReschedule : requestRescheduleRef ? handleRefReschedule : handleReschedule}>
                                    {rescheduleSeriesId ? 'Update series' : requestRescheduleRef ? 'Submit reschedule request' : 'Confirm reschedule'}
                                </Button>
                            </div>
                        </>
                    ) : step === 'pick' ? (
                        <>
                            {/* toolbar */}
                            <div className="flex items-center justify-between mb-6">
                                <div className="flex items-center gap-1">
                                    <button onClick={prevPeriod} className="p-2 rounded-xl hover:bg-gray-100 text-gray-400 hover:text-gray-700 transition-colors">
                                        <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                                            <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
                                        </svg>
                                    </button>
                                    <span className="text-sm font-semibold text-gray-900 w-40 text-center flex items-center justify-center gap-2">
                                        {periodLabel}
                                        {loading && <div className="w-4 h-4 border-[3px] border-indigo-500 border-t-transparent rounded-full animate-spin shrink-0" />}
                                    </span>
                                    <button onClick={nextPeriod} className="p-2 rounded-xl hover:bg-gray-100 text-gray-400 hover:text-gray-700 transition-colors">
                                        <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                                            <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                                        </svg>
                                    </button>
                                </div>
                                <div className="flex items-center bg-gray-100 rounded-xl p-0.5 text-xs">
                                    {(['month', 'week'] as const).map(v => (
                                        <button
                                            key={v}
                                            onClick={() => { setView(v); setSelectedDate(null) }}
                                            className={`px-3 py-1.5 rounded-lg capitalize font-medium transition-all ${view === v ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
                                        >
                                            {v}
                                        </button>
                                    ))}
                                </div>
                            </div>

                            {!selectedTutorId && tutors.length > 1 && (
                                <p className="text-sm text-gray-400 mb-4">Select a tutor to see available times.</p>
                            )}

                            {view === 'month' ? (
                                <div className="flex gap-6">
                                    {/* calendar */}
                                    <div className="flex-1 min-w-0">
                                        <div className="grid grid-cols-7 mb-2">
                                            {WEEK_DAYS.map(d => (
                                                <div key={d} className="text-center text-xs font-semibold text-gray-400 py-1 tracking-wide">{d}</div>
                                            ))}
                                        </div>
                                        <div className="grid grid-cols-7 gap-1">
                                            {calDays.map((day, i) => {
                                                const dateStr = toLocalDateStr(day)
                                                const isCurrentMonth = day.getMonth() === currentDate.getMonth()
                                                const hasSlots = !!slotsByDate[dateStr]?.length
                                                const isSelected = dateStr === selectedDate
                                                const isToday = dateStr === today
                                                const isPast = dateStr < today
                                                const isAvailable = hasSlots && !isPast && isCurrentMonth && !loading
                                                return (
                                                    <button
                                                        key={i}
                                                        disabled={!isAvailable}
                                                        onClick={() => setSelectedDate(dateStr)}
                                                        className={[
                                                            'relative aspect-square flex items-center justify-center rounded-2xl text-sm transition-all duration-150 font-medium',
                                                            isSelected ? 'bg-indigo-600 text-white shadow-md shadow-indigo-200 scale-105' : '',
                                                            !isSelected && isAvailable ? 'hover:bg-indigo-50 hover:text-indigo-700 text-gray-800 cursor-pointer' : '',
                                                            !isSelected && !isAvailable ? 'text-gray-200 cursor-default' : '',
                                                            isToday && !isSelected ? 'ring-2 ring-indigo-300 text-indigo-700' : '',
                                                        ].join(' ')}
                                                    >
                                                        {day.getDate()}
                                                        {isAvailable && !isSelected && (
                                                            <span className="absolute bottom-1.5 left-1/2 -translate-x-1/2 w-1 h-1 rounded-full bg-indigo-400" />
                                                        )}
                                                    </button>
                                                )
                                            })}
                                        </div>
                                    </div>

                                    {/* slots panel */}
                                    <div className="w-36 shrink-0 border-l border-gray-100 pl-6">
                                        {selectedDate ? (
                                            <>
                                                <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3 whitespace-nowrap">
                                                    {new Date(selectedDate + 'T12:00:00').toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })}
                                                </p>
                                                {loading ? (
                                                    <div className="flex items-center gap-2 text-xs text-gray-400">
                                                        <div className="w-3 h-3 border-2 border-indigo-400 border-t-transparent rounded-full animate-spin" />
                                                        Loading...
                                                    </div>
                                                ) : (slotsByDate[selectedDate] ?? []).length === 0 ? (
                                                    <p className="text-xs text-gray-400">No times available.</p>
                                                ) : (
                                                    <div className="flex flex-col gap-1">
                                                        {(slotsByDate[selectedDate] ?? []).map((slot, i) => (
                                                            <button
                                                                key={i}
                                                                onClick={() => handleSlotSelect(slot)}
                                                                disabled={submitting}
                                                                className="py-1 border-2 border-indigo-100 bg-indigo-50 rounded-lg text-xs font-semibold text-indigo-700 hover:bg-indigo-600 hover:text-white hover:border-indigo-600 transition-all text-center whitespace-nowrap disabled:opacity-50 disabled:cursor-default"
                                                            >
                                                                {localTimeOf(slot.start, timezone)}
                                                            </button>
                                                        ))}
                                                    </div>
                                                )}
                                            </>
                                        ) : (
                                            <p className="text-xs text-gray-300 mt-8">Pick a day</p>
                                        )}
                                    </div>
                                </div>
                            ) : (
                                // week view
                                <div className="overflow-x-auto">
                                <div className="grid grid-cols-7 gap-2 min-w-[560px]">
                                    {weekDays.map((day, i) => {
                                        const dateStr = toLocalDateStr(day)
                                        const daySlots = slotsByDate[dateStr] ?? []
                                        const isToday = dateStr === today
                                        const isPast = dateStr < today
                                        return (
                                            <div key={i} className="flex flex-col">
                                                <div className={`text-center py-2.5 rounded-2xl mb-2 ${isToday ? 'bg-indigo-600' : 'bg-gray-50'}`}>
                                                    <p className={`text-xs font-semibold tracking-wide ${isToday ? 'text-indigo-200' : 'text-gray-400'}`}>{WEEK_DAYS[i]}</p>
                                                    <p className={`text-sm font-bold ${isToday ? 'text-white' : 'text-gray-700'}`}>{day.getDate()}</p>
                                                </div>
                                                {loading ? (
                                                    <div className="flex justify-center mt-2">
                                                        <div className="w-3 h-3 border-2 border-indigo-300 border-t-transparent rounded-full animate-spin" />
                                                    </div>
                                                ) : daySlots.length === 0 ? (
                                                    <p className="text-xs text-gray-200 text-center mt-1">—</p>
                                                ) : daySlots.map((slot, j) => (
                                                    <button
                                                        key={j}
                                                        disabled={isPast || submitting}
                                                        onClick={() => handleSlotSelect(slot)}
                                                        className="mb-1.5 py-1.5 border-2 border-indigo-100 bg-indigo-50 rounded-xl text-xs font-semibold text-indigo-700 hover:bg-indigo-600 hover:text-white hover:border-indigo-600 transition-all disabled:opacity-20 disabled:cursor-default text-center w-full whitespace-nowrap"
                                                    >
                                                        {localTimeOf(slot.start, timezone)}
                                                    </button>
                                                ))}
                                            </div>
                                        )
                                    })}
                                </div>
                                </div>
                            )}

                            {loadSlotsError && <p className="text-sm text-red-500 mt-4">{loadSlotsError}</p>}
                            {submitError && <p className="text-sm text-red-500 mt-4">{submitError}</p>}
                        </>
                    ) : (
                        // ---- CONTACT FORM ----
                        <>
                            <button
                                onClick={() => setStep('pick')}
                                className="flex items-center gap-1.5 text-sm text-gray-400 hover:text-gray-700 transition-colors mb-8 group"
                            >
                                <svg className="w-4 h-4 group-hover:-translate-x-0.5 transition-transform" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
                                </svg>
                                Back to calendar
                            </button>

                            <h2 className="text-xl font-bold text-gray-900 mb-1">Your details</h2>
                            <p className="text-sm text-gray-400 mb-7">Almost there — just a few details and you're all set.</p>

                            <div className="flex flex-col gap-5 max-w-md">
                                <div className="flex gap-3">
                                    <TextInput
                                        label="First name"
                                        placeholder="First"
                                        value={contact.studentFirst}
                                        onChange={e => setContact(prev => ({ ...prev, studentFirst: e.target.value }))}
                                        onBlur={() => setTouched(prev => ({ ...prev, studentFirst: true }))}
                                        error={touched.studentFirst ? contactErrors.studentFirst : undefined}
                                        className="flex-1"
                                        required
                                    />
                                    <TextInput
                                        label="Last name"
                                        placeholder="Last"
                                        value={contact.studentLast}
                                        onChange={e => setContact(prev => ({ ...prev, studentLast: e.target.value }))}
                                        onBlur={() => setTouched(prev => ({ ...prev, studentLast: true }))}
                                        error={touched.studentLast ? contactErrors.studentLast : undefined}
                                        className="flex-1"
                                        required
                                    />
                                </div>

                                <div className="rounded-2xl border border-gray-100 bg-gray-50 p-4 flex flex-col gap-4">
                                    <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">Student contact</p>
                                    <TextInput
                                        label="Email"
                                        placeholder="student@email.com"
                                        value={contact.studentEmail}
                                        onChange={e => setContact(prev => ({ ...prev, studentEmail: e.target.value }))}
                                        onBlur={() => setTouched(prev => ({ ...prev, contactEmail: true }))}
                                        error={touched.contactEmail ? contactErrors.contactEmail : undefined}
                                    />
                                    <TextInput
                                        label="Phone"
                                        placeholder="(555) 000-0000"
                                        value={contact.studentPhone}
                                        onChange={e => setContact(prev => ({ ...prev, studentPhone: e.target.value }))}
                                        onBlur={() => setTouched(prev => ({ ...prev, contactPhone: true }))}
                                        error={touched.contactPhone ? contactErrors.contactPhone : undefined}
                                    />
                                </div>

                                <div className="rounded-2xl border border-gray-100 bg-gray-50 p-4 flex flex-col gap-4">
                                    <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">Parent contact <span className="normal-case font-normal text-gray-300">(optional)</span></p>
                                    <TextInput
                                        label="Email"
                                        placeholder="parent@email.com"
                                        value={contact.parentEmail}
                                        onChange={e => setContact(prev => ({ ...prev, parentEmail: e.target.value }))}
                                    />
                                    <TextInput
                                        label="Phone"
                                        placeholder="(555) 000-0000"
                                        value={contact.parentPhone}
                                        onChange={e => setContact(prev => ({ ...prev, parentPhone: e.target.value }))}
                                    />
                                </div>

                                {eventType?.booker_can_set_recur_until && (
                                    <div className="flex flex-col gap-1">
                                        <label className="text-sm font-medium text-gray-700">
                                            Series end date <span className="text-gray-400 font-normal">(optional)</span>
                                        </label>
                                        <input
                                            type="date"
                                            value={recurUntil ?? ''}
                                            min={new Date(Date.now() + 14 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10)}
                                            onChange={e => setRecurUntil(e.target.value || null)}
                                            className="border border-gray-300 rounded-lg px-3 py-2 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-400"
                                        />
                                        <p className="text-xs text-gray-400">Leave blank for an indefinite weekly series</p>
                                    </div>
                                )}

                                {submitError && <p className="text-sm text-red-500">{submitError}</p>}

                                <Button size="md" radius="xl" onClick={handleSubmit} loading={submitting} className="mt-1">
                                    Confirm booking
                                </Button>
                            </div>
                        </>
                    )}
                </div>
            </div>
        </div>
    )
}

export default BookingPage

