import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import type { BookingSeries, BookingLink } from './types'
import { formatDate, formatUTCTime, extractError, DAY_NAMES, weekdayOf, timeOf } from './utils'

interface LoadErrors {
    series?: string
    bookingLink?: string
}

const ManageSeries = () => {
    const { ref } = useParams<{ ref: string }>()
    const navigate = useNavigate()
    const [series, setSeries] = useState<BookingSeries | null>(null)
    const [bookingLink, setBookingLink] = useState<BookingLink | null>(null)
    const [isLoading, setIsLoading] = useState(true)
    const [isSubmitting, setIsSubmitting] = useState(false)
    const [loadErrors, setLoadErrors] = useState<LoadErrors>({})
    const [error, setError] = useState<string | null>(null)
    const [done, setDone] = useState<'cancelled' | 'requested_cancel' | null>(null)
    const [confirming, setConfirming] = useState<'cancel' | null>(null)

    const loadSeries = async () => {
        setIsLoading(true)
        try {
            const res = await fetch(`${import.meta.env.VITE_API_URL}/bookings/manage-series/${ref}`)
            if (!res.ok) {
                const err = await res.json()
                setLoadErrors(prev => ({ ...prev, series: extractError(err, 'Failed to load series') }))
                return
            }
            const seriesData: BookingSeries = await res.json()
            setSeries(seriesData)

            // include_archived — same reason as ManageOccurrence: cancel must survive a retired link.
            const bookingLinkRes = await fetch(`${import.meta.env.VITE_API_URL}/booking_links/${seriesData.booking_link_id}?include_archived=true`)
            if (!bookingLinkRes.ok) {
                const err = await bookingLinkRes.json()
                setLoadErrors(prev => ({ ...prev, bookingLink: extractError(err, 'Failed to load booking link') }))
                return
            }
            setBookingLink(await bookingLinkRes.json())
        } catch (err) {
            console.error('Error loading series:', err)
            setError('An unknown error occurred while loading the series')
        } finally {
            setIsLoading(false)
        }
    }

    useEffect(() => { loadSeries() },
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [ref])

    // Server already decided the verdict — frontend just reads it and authors its own copy,
    // no re-derivation of *why* (that would need minutes_until, which stays server-side).
    const cancelAction = series?.cancel_action ?? 'blocked'
    const rescheduleAction = series?.reschedule_action ?? 'blocked'
    // Same fix as ManageOccurrence.tsx — collapse the two near-identical "within window,
    // submit for review" sentences into one shared line when both land on the same verdict.
    const sameAction = cancelAction === rescheduleAction
    const cancelNoticeHours = Math.round((bookingLink?.cancel_notice_minutes ?? 0) / 60)
    const rescheduleNoticeHours = Math.round((bookingLink?.reschedule_notice_minutes ?? 0) / 60)

    const handleCancel = () => setConfirming('cancel')

    const handleConfirmCancel = async () => {
        setIsSubmitting(true)
        try {
            const res = await fetch(`${import.meta.env.VITE_API_URL}/bookings/manage-series/${ref}/cancel`, { method: 'POST' })
            if (!res.ok) {
                const err = await res.json()
                setError(extractError(err, cancelAction === 'auto' ? 'Failed to cancel series' : 'Failed to submit cancellation request'))
                return
            }
            const data: BookingSeries = await res.json()
            setDone(data.request ? 'requested_cancel' : 'cancelled')
        } catch (err) {
            console.error('Error cancelling series:', err)
            setError(cancelAction === 'auto' ? 'An unknown error occurred while cancelling the series' : 'An unknown error occurred while submitting the cancellation request')
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

    if (loadErrors.series || loadErrors.bookingLink) {
        return (
            <div className="min-h-screen bg-gray-50 flex items-center justify-center p-6">
                <div className="bg-white rounded-2xl shadow border border-gray-100 p-8 max-w-md w-full text-center">
                    <button onClick={() => navigate('/my-bookings')} className="text-sm text-indigo-500 hover:text-indigo-700 mb-4 inline-flex items-center gap-1 transition-colors">
                        ← My bookings
                    </button>
                    <p className="text-gray-500">{loadErrors.series ?? loadErrors.bookingLink}</p>
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

    if (!series || !bookingLink) return null

    return (
        <div className="min-h-screen bg-gray-50 flex items-center justify-center p-6">
            <div className="bg-white rounded-2xl shadow border border-gray-100 p-8 max-w-md w-full">

                <button onClick={() => navigate('/my-bookings')} className="text-sm text-indigo-500 hover:text-indigo-700 mb-5 inline-flex items-center gap-1 transition-colors">
                    ← My bookings
                </button>

                <h1 className="text-xl font-bold text-gray-900 mb-1">Manage your series</h1>
                <p className="text-sm text-indigo-600 font-medium mb-6">{bookingLink.slug}</p>

                {/* series details */}
                <div className="bg-gray-50 rounded-xl px-5 py-4 mb-6">
                    <p className="text-sm font-semibold text-gray-900">
                        Every {DAY_NAMES[weekdayOf(series.dtstart)]} at {formatUTCTime(timeOf(series.dtstart))}
                    </p>
                    {series.until && (
                        <p className="text-sm text-gray-500 mt-0.5">Until {formatDate(series.until)}</p>
                    )}
                    <p className="text-sm text-gray-400 mt-1">{series.student_first} {series.student_last}</p>
                </div>

                {!series.is_active ? (
                    <p className="text-sm text-gray-400">This series has been cancelled.</p>
                ) : (
                    <>
                        {sameAction && cancelAction === 'request' && !confirming && (
                            <p className="text-sm text-amber-600 mb-3">
                                Cancelling or rescheduling isn't available directly right now — you can submit a request for admin review below.
                            </p>
                        )}
                        {sameAction && cancelAction === 'blocked' && !confirming && (
                            <p className="text-sm text-gray-400 mb-3">
                                Neither cancelling nor rescheduling is available for this series right now.
                            </p>
                        )}

                        {/* change schedule */}
                        {!confirming && (
                            rescheduleAction === 'auto' ? (
                                <div className="mb-4">
                                    <button
                                        onClick={() => navigate(`/book/${bookingLink.slug}`, { state: {
                                            rescheduleSeriesId: series.id,
                                            tutorId: series.tutor_id,
                                            originalDayOfWeek: weekdayOf(series.dtstart),
                                            originalStartTime: timeOf(series.dtstart),
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
                                </div>
                            ) : rescheduleAction === 'request' ? (
                                <div className="mb-4">
                                    {!sameAction && (
                                        <p className="text-sm text-amber-600 mb-2">
                                            You're within the reschedule window ({rescheduleNoticeHours}h notice required). You can submit a request for admin review.
                                        </p>
                                    )}
                                    <button
                                        onClick={() => navigate(`/book/${bookingLink.slug}`, { state: {
                                            rescheduleSeriesId: series.id,
                                            tutorId: series.tutor_id,
                                            originalDayOfWeek: weekdayOf(series.dtstart),
                                            originalStartTime: timeOf(series.dtstart),
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
                            ) : (
                                <div className="mb-4">
                                    {!sameAction && (
                                        <p className="text-sm text-gray-400 mb-2">Rescheduling isn't available for this series right now.</p>
                                    )}
                                    <button disabled className="w-full py-2.5 rounded-xl border border-gray-200 text-gray-300 text-sm font-medium cursor-not-allowed">
                                        Change schedule
                                    </button>
                                </div>
                            )
                        )}

                        {/* cancel */}
                        {!confirming && (
                            cancelAction === 'auto' ? (
                                <div className="mb-4">
                                    <button
                                        onClick={handleCancel}
                                        className="w-full py-2.5 rounded-xl border border-red-200 text-red-600 text-sm font-medium hover:bg-red-50 transition-colors"
                                    >
                                        Cancel series
                                    </button>
                                </div>
                            ) : cancelAction === 'request' ? (
                                <div className="mb-4">
                                    {!sameAction && (
                                        <p className="text-sm text-amber-600 mb-2">
                                            You're within the cancellation window ({cancelNoticeHours}h notice required). You can submit a request for admin review.
                                        </p>
                                    )}
                                    <button
                                        onClick={handleCancel}
                                        className="w-full py-2.5 rounded-xl border border-amber-200 text-amber-700 text-sm font-medium hover:bg-amber-50 transition-colors"
                                    >
                                        Request cancellation
                                    </button>
                                </div>
                            ) : (
                                <div className="mb-4">
                                    {!sameAction && (
                                        <p className="text-sm text-gray-400 mb-2">Cancellation isn't available for this series right now.</p>
                                    )}
                                    <button disabled className="w-full py-2.5 rounded-xl border border-gray-200 text-gray-300 text-sm font-medium cursor-not-allowed">
                                        Cancel series
                                    </button>
                                </div>
                            )
                        )}

                        {/* confirm cancel */}
                        {confirming === 'cancel' && (
                            <div className="mb-4 p-4 rounded-xl border border-red-100 bg-red-50">
                                <p className="text-sm font-medium text-red-800 mb-3">
                                    {cancelAction === 'auto' ? 'Are you sure you want to cancel this entire series? All future sessions will be removed.' : 'Submit a cancellation request for admin review?'}
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
