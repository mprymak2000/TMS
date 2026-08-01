import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import type { BookingSeries, EventType } from './types'
import { formatDate, formatUTCTime, extractError } from './utils'

const DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

interface LoadErrors {
    series?: string
    eventType?: string
}

const ManageSeries = () => {
    const { token } = useParams<{ token: string }>()
    const navigate = useNavigate()
    const [series, setSeries] = useState<BookingSeries | null>(null)
    const [eventType, setEventType] = useState<EventType | null>(null)
    const [isLoading, setIsLoading] = useState(true)
    const [isSubmitting, setIsSubmitting] = useState(false)
    const [loadErrors, setLoadErrors] = useState<LoadErrors>({})
    const [error, setError] = useState<string | null>(null)
    const [done, setDone] = useState<'cancelled' | 'requested_cancel' | null>(null)
    const [confirming, setConfirming] = useState<'cancel' | null>(null)

    const loadSeries = async () => {
        setIsLoading(true)
        try {
            const res = await fetch(`${import.meta.env.VITE_API_URL}/bookings/manage-series/${token}`)
            if (!res.ok) {
                const err = await res.json()
                setLoadErrors(prev => ({ ...prev, series: extractError(err, 'Failed to load series') }))
                return
            }
            const seriesData: BookingSeries = await res.json()
            setSeries(seriesData)

            const eventTypeRes = await fetch(`${import.meta.env.VITE_API_URL}/event_types/${seriesData.event_type_id}`)
            if (!eventTypeRes.ok) {
                const err = await eventTypeRes.json()
                setLoadErrors(prev => ({ ...prev, eventType: extractError(err, 'Failed to load event type') }))
                return
            }
            setEventType(await eventTypeRes.json())
        } catch (err) {
            console.error('Error loading series:', err)
            setError('An unknown error occurred while loading the series')
        } finally {
            setIsLoading(false)
        }
    }

    useEffect(() => { loadSeries() },
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [token])

    // start_time is "HH:MM:SS" UTC; day_of_week is 0=Mon..6=Sun
    const getNextOccurrenceMs = (dow: number, timeStr: string): number => {
        const [h, m] = timeStr.split(':').map(Number)
        const jsDay = dow === 6 ? 0 : dow + 1  // convert to JS 0=Sun..6=Sat
        const now = new Date()
        const candidate = new Date(now)
        candidate.setUTCHours(h, m, 0, 0)
        let daysUntil = (jsDay - now.getUTCDay() + 7) % 7
        if (daysUntil === 0 && now.getTime() >= candidate.getTime()) daysUntil = 7
        candidate.setUTCDate(candidate.getUTCDate() + daysUntil)
        return candidate.getTime()
    }
    const minutesUntilNext = series ? (getNextOccurrenceMs(series.day_of_week, series.start_time) - Date.now()) / 60000 : Infinity
    const WINDOW_MODES = ['auto_window_block', 'auto_window_request', 'request_window']
    const cancelMode = eventType?.cancel_mode ?? 'auto'
    const cancelInWindow = WINDOW_MODES.includes(cancelMode) && minutesUntilNext < (eventType?.cancel_notice_minutes ?? 0) 
    const canCancelDirectly = cancelMode === 'auto' || ((cancelMode === 'auto_window_block' || cancelMode === 'auto_window_request') && !cancelInWindow)
    const canRequestCancel = cancelMode === 'request' || (cancelMode === 'auto_window_request' && cancelInWindow) || (cancelMode === 'request_window' && !cancelInWindow)

    const rescheduleMode = eventType?.reschedule_mode ?? 'auto'
    const rescheduleInWindow = WINDOW_MODES.includes(rescheduleMode) && minutesUntilNext < (eventType?.reschedule_notice_minutes ?? 0)
    const canRescheduleDirectly = rescheduleMode === 'auto' || ((rescheduleMode === 'auto_window_block' || rescheduleMode === 'auto_window_request') && !rescheduleInWindow)
    const canRequestReschedule = rescheduleMode === 'request' || (rescheduleMode === 'auto_window_request' && rescheduleInWindow) || (rescheduleMode === 'request_window' && !rescheduleInWindow)

    const handleCancel = () => setConfirming('cancel')

    const handleConfirmCancel = async () => {
        setIsSubmitting(true)
        try {
            const res = await fetch(`${import.meta.env.VITE_API_URL}/bookings/manage-series/${token}/cancel`, { method: 'POST' })
            if (!res.ok) {
                const err = await res.json()
                setError(extractError(err, canCancelDirectly ? 'Failed to cancel series' : 'Failed to submit cancellation request'))
                return
            }
            const data: BookingSeries = await res.json()
            setDone(data.request ? 'requested_cancel' : 'cancelled')
        } catch (err) {
            console.error('Error cancelling series:', err)
            setError(canCancelDirectly ? 'An unknown error occurred while cancelling the series' : 'An unknown error occurred while submitting the cancellation request')
        } finally {
            setIsSubmitting(false)
            setConfirming(null)
        }
    }

    if (isLoading) {
        return (
            <div className="min-h-screen bg-gray-50 flex items-center justify-center">
                <div className="w-8 h-8 border-4 border-indigo-500 border-t-transparent rounded-full animate-spin" />
            </div>
        )
    }

    if (loadErrors.series || loadErrors.eventType) {
        return (
            <div className="min-h-screen bg-gray-50 flex items-center justify-center p-6">
                <div className="bg-white rounded-2xl shadow border border-gray-100 p-8 max-w-md w-full text-center">
                    <button onClick={() => navigate('/my-bookings')} className="text-sm text-indigo-500 hover:text-indigo-700 mb-4 inline-flex items-center gap-1 transition-colors">
                        ← My bookings
                    </button>
                    <p className="text-gray-500">{loadErrors.series ?? loadErrors.eventType}</p>
                </div>
            </div>
        )
    }

    if (done) {
        const messages = {
            cancelled: { title: 'Series cancelled', body: 'Your recurring series has been successfully cancelled.' },
            requested_cancel: { title: 'Request submitted', body: "You're within the cancellation window. Your request has been submitted and an admin will review it." },
        }
        const msg = messages[done]
        return (
            <div className="min-h-screen bg-gray-50 flex items-center justify-center p-6">
                <div className="bg-white rounded-2xl shadow border border-gray-100 p-8 max-w-md w-full text-center">
                    <div className="w-14 h-14 bg-green-500 rounded-full flex items-center justify-center mx-auto mb-5 shadow">
                        <svg className="w-7 h-7 text-white" fill="none" stroke="currentColor" strokeWidth={2.5} viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                        </svg>
                    </div>
                    <h1 className="text-xl font-bold text-gray-900 mb-2">{msg.title}</h1>
                    <p className="text-sm text-gray-500 mb-6">{msg.body}</p>
                    <button onClick={() => navigate('/my-bookings')} className="text-sm text-indigo-500 hover:text-indigo-700 transition-colors font-medium">
                        ← Back to my bookings
                    </button>
                </div>
            </div>
        )
    }

    if (!series || !eventType) return null

    return (
        <div className="min-h-screen bg-gray-50 flex items-center justify-center p-6">
            <div className="bg-white rounded-2xl shadow border border-gray-100 p-8 max-w-md w-full">

                <button onClick={() => navigate('/my-bookings')} className="text-sm text-indigo-500 hover:text-indigo-700 mb-5 inline-flex items-center gap-1 transition-colors">
                    ← My bookings
                </button>

                <h1 className="text-xl font-bold text-gray-900 mb-1">Manage your series</h1>
                <p className="text-sm text-indigo-600 font-medium mb-6">{eventType.name}</p>

                {/* series details */}
                <div className="bg-gray-50 rounded-xl px-5 py-4 mb-6">
                    <p className="text-sm font-semibold text-gray-900">
                        Every {DAY_NAMES[series.day_of_week]} at {formatUTCTime(series.start_time)}
                    </p>
                    {series.recur_until && (
                        <p className="text-sm text-gray-500 mt-0.5">Until {formatDate(series.recur_until)}</p>
                    )}
                    <p className="text-sm text-gray-400 mt-1">{series.student_first} {series.student_last}</p>
                </div>

                {!series.is_active ? (
                    <p className="text-sm text-gray-400">This series has been cancelled.</p>
                ) : (
                    <>
                        {/* change schedule */}
                        {(canRescheduleDirectly || canRequestReschedule) && !confirming && (
                            <div className="mb-4">
                                {canRescheduleDirectly ? (
                                    <button
                                        onClick={() => navigate(`/book/${series.event_type_id}`, { state: {
                                            rescheduleSeriesId: series.id,
                                            tutorId: series.tutor_id,
                                            originalDayOfWeek: series.day_of_week,
                                            originalStartTime: series.start_time,
                                            studentFirst: series.student_first,
                                            studentLast: series.student_last,
                                            studentEmail: series.student_email,
                                            studentPhone: series.student_phone,
                                            parentEmail: series.parent_email,
                                            parentPhone: series.parent_phone,
                                        }})}
                                        className="w-full py-2.5 rounded-xl border border-indigo-200 text-indigo-600 text-sm font-medium hover:bg-indigo-50 transition-colors"
                                    >
                                        Change schedule
                                    </button>
                                ) : (
                                    <div>
                                        <p className="text-sm text-amber-600 mb-2">
                                            You're within the reschedule window ({Math.round((eventType.reschedule_notice_minutes ?? 0) / 60)}h notice required). You can submit a request for admin review.
                                        </p>
                                        <button
                                            onClick={() => navigate(`/book/${series.event_type_id}`, { state: {
                                                rescheduleSeriesId: series.id,
                                                tutorId: series.tutor_id,
                                                originalDayOfWeek: series.day_of_week,
                                                originalStartTime: series.start_time,
                                                studentFirst: series.student_first,
                                                studentLast: series.student_last,
                                                studentEmail: series.student_email,
                                                studentPhone: series.student_phone,
                                                parentEmail: series.parent_email,
                                                parentPhone: series.parent_phone,
                                            }})}
                                            className="w-full py-2.5 rounded-xl border border-amber-200 text-amber-700 text-sm font-medium hover:bg-amber-50 transition-colors"
                                        >
                                            Request schedule change
                                        </button>
                                    </div>
                                )}
                            </div>
                        )}

                        {/* cancel */}
                        {(canCancelDirectly || canRequestCancel) && !confirming && (
                            <div className="mb-4">
                                {canCancelDirectly ? (
                                    <button
                                        onClick={handleCancel}
                                        className="w-full py-2.5 rounded-xl border border-red-200 text-red-600 text-sm font-medium hover:bg-red-50 transition-colors"
                                    >
                                        Cancel series
                                    </button>
                                ) : (
                                    <div>
                                        <p className="text-sm text-amber-600 mb-2">
                                            You're within the cancellation window ({Math.round((eventType.cancel_notice_minutes ?? 0) / 60)}h notice required). You can submit a request for admin review.
                                        </p>
                                        <button
                                            onClick={handleCancel}
                                            className="w-full py-2.5 rounded-xl border border-amber-200 text-amber-700 text-sm font-medium hover:bg-amber-50 transition-colors"
                                        >
                                            Request cancellation
                                        </button>
                                    </div>
                                )}
                            </div>
                        )}

                        {/* confirm cancel */}
                        {confirming === 'cancel' && (
                            <div className="mb-4 p-4 rounded-xl border border-red-100 bg-red-50">
                                <p className="text-sm font-medium text-red-800 mb-3">
                                    {canCancelDirectly ? 'Are you sure you want to cancel this entire series? All future sessions will be removed.' : 'Submit a cancellation request for admin review?'}
                                </p>
                                {error && <p className="text-sm text-red-600 mb-2">{error}</p>}
                                <div className="flex gap-2">
                                    <button
                                        onClick={handleConfirmCancel}
                                        disabled={isSubmitting}
                                        className="flex-1 py-2 rounded-lg bg-red-600 text-white text-sm font-medium hover:bg-red-700 disabled:opacity-50 transition-colors"
                                    >
                                        {isSubmitting ? 'Submitting...' : 'Confirm'}
                                    </button>
                                    <button
                                        onClick={() => { setConfirming(null); setError(null) }}
                                        disabled={isSubmitting}
                                        className="flex-1 py-2 rounded-lg border border-gray-200 text-gray-600 text-sm font-medium hover:bg-gray-50 disabled:opacity-50 transition-colors"
                                    >
                                        Go back
                                    </button>
                                </div>
                            </div>
                        )}

                        {error && !confirming && <p className="text-sm text-red-500 mt-2">{error}</p>}
                    </>
                )}
            </div>
        </div>
    )
}

export default ManageSeries
