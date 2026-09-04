import { useState, useEffect } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import type { Booking, BookingLink } from './types'
import { formatDate, formatTime, extractError } from './utils'

interface LoadErrors {
    booking?: string
    bookingLink?: string
}

const ManageOccurrence = () => {
    const { ref } = useParams<{ ref: string }>()
    const navigate = useNavigate()
    const [booking, setBooking] = useState<Booking | null>(null)
    const [bookingLink, setBookingLink] = useState<BookingLink | null>(null)
    const [isLoading, setIsLoading] = useState(true)
    const [isSubmitting, setIsSubmitting] = useState(false)
    const [loadErrors, setLoadErrors] = useState<LoadErrors>({})
    const [error, setError] = useState<string | null>(null)
    const [done, setDone] = useState<'cancelled' | 'rescheduled' | 'requested_cancel' | 'requested_reschedule' | null>(null)
    const [confirming, setConfirming] = useState<'cancel' | null>(null)

    const loadBooking = async () => {
        setIsLoading(true)
        try {
            const res = await fetch(`${import.meta.env.VITE_API_URL}/bookings/manage-occurrence/${ref}`)
            if (!res.ok) {
                const err = await res.json()
                setLoadErrors(prev => ({ ...prev, booking: extractError(err, 'Failed to load booking') }))
                return
            }
            const bookingData: Booking = await res.json()
            setBooking(bookingData)

            // include_archived: this page must keep working for a booking whose link was retired —
            // cancel doesn't need the link at all, and reschedule needs it only to explain itself.
            const bookingLinkRes = await fetch(`${import.meta.env.VITE_API_URL}/booking_links/${bookingData.booking_link_id}?include_archived=true`)
            if (!bookingLinkRes.ok) {
                const err = await bookingLinkRes.json()
                setLoadErrors(prev => ({ ...prev, bookingLink: extractError(err, 'Failed to load the booking link') }))
                return
            }
            setBookingLink(await bookingLinkRes.json())
        } catch (err) {
            console.error('Error loading booking:', err)
            setError('An unknown error occurred while loading the booking')
        } finally {
            setIsLoading(false)
        }
    }

    useEffect(() => { loadBooking() },
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [ref])

    // Server already decided the verdict — frontend just reads it and authors its own copy,
    // no re-derivation of *why* (that would need minutes_until, which stays server-side).
    const cancelAction = booking?.cancel_action ?? 'blocked'
    const rescheduleAction = booking?.reschedule_action ?? 'blocked'
    // Cancel and reschedule render independently, so when both land on the same verdict
    // (commonly both 'request'), showing each one's own near-identical explanation reads as
    // a duplicate — collapse into one shared line then, fall back to per-action wording
    // otherwise.
    const sameAction = cancelAction === rescheduleAction
    const cancelNoticeHours = Math.round((bookingLink?.cancel_notice_minutes ?? 0) / 60)
    const rescheduleNoticeHours = Math.round((bookingLink?.reschedule_notice_minutes ?? 0) / 60)

    const handleCancel = () => setConfirming('cancel')

    const confirmCancel = async () => {
        setIsSubmitting(true)
        try {
            const res = await fetch(`${import.meta.env.VITE_API_URL}/bookings/manage-occurrence/${ref}/cancel`, { method: 'POST' })
            if (!res.ok) {
                const err = await res.json()
                setError(extractError(err, cancelAction === 'auto' ? 'Failed to cancel booking' : 'Failed to submit cancellation request'))
                return
            }
            const data: Booking = await res.json()
            setDone(data.request ? 'requested_cancel' : 'cancelled')
        } catch (err) {
            console.error(err)
            setError(cancelAction === 'auto' ? 'An unknown error occurred while cancelling the booking' : 'An unknown error occurred while submitting the cancellation request')
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

    if (loadErrors.booking || loadErrors.bookingLink) {
        return (
            <div className="min-h-screen bg-gray-50 flex items-center justify-center p-6">
                <div className="bg-white rounded-2xl shadow border border-gray-100 p-8 max-w-md w-full text-center">
                    <button onClick={() => navigate('/my-bookings')} className="text-sm text-indigo-500 hover:text-indigo-700 mb-4 inline-flex items-center gap-1 transition-colors">
                        ← My bookings
                    </button>
                    <p className="text-gray-500">{loadErrors.booking ?? loadErrors.bookingLink}</p>
                </div>
            </div>
        )
    }

    if (done) {
        const messages = {
            cancelled: { title: 'Booking cancelled', body: 'Your booking has been successfully cancelled.' },
            rescheduled: { title: 'Booking rescheduled', body: 'Your booking has been moved to the new time.' },
            requested_cancel: { title: 'Request submitted', body: 'You\'re within the cancellation window. Your request has been submitted and an admin will review it.' },
            requested_reschedule: { title: 'Request submitted', body: 'You\'re within the reschedule window. Your request has been submitted and an admin will review it.' },
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

    if (!booking || !bookingLink) return null

    return (
        <div className="min-h-screen bg-gray-50 flex items-center justify-center p-6">
            <div className="bg-white rounded-2xl shadow border border-gray-100 p-8 max-w-md w-full">

                <button onClick={() => navigate('/my-bookings')} className="text-sm text-indigo-500 hover:text-indigo-700 mb-5 inline-flex items-center gap-1 transition-colors">
                    ← My bookings
                </button>

                <h1 className="text-xl font-bold text-gray-900 mb-1">Manage your booking</h1>
                <p className="text-sm text-indigo-600 font-medium mb-6">{bookingLink.slug}</p>

                {/* booking details */}
                <div className="bg-gray-50 rounded-xl px-5 py-4 mb-6">
                    <p className="text-sm font-semibold text-gray-900">{formatDate(booking.start)}</p>
                    <p className="text-sm text-gray-500">{formatTime(booking.start)} – {formatTime(booking.end)}</p>
                    <p className="text-sm text-gray-400 mt-1">{booking.student_first} {booking.student_last}</p>
                </div>

                {booking.status !== 'confirmed' ? (
                    <p className="text-sm text-gray-400 capitalize">This booking is {booking.status}.</p>
                ) : (
                    <>
                        {sameAction && cancelAction === 'request' && !confirming && (
                            <p className="text-sm text-amber-600 mb-3">
                                Cancelling or rescheduling isn't available directly right now — you can submit a request for admin review below.
                            </p>
                        )}
                        {sameAction && cancelAction === 'blocked' && !confirming && (
                            <p className="text-sm text-gray-400 mb-3">
                                Neither cancelling nor rescheduling is available for this booking right now.
                            </p>
                        )}

                        {/* cancel */}
                        {!confirming && (
                            cancelAction === 'auto' ? (
                                <div className="mb-4">
                                    <button
                                        onClick={handleCancel}
                                        className="w-full py-2.5 rounded-xl border border-red-200 text-red-600 text-sm font-medium hover:bg-red-50 transition-colors"
                                    >
                                        Cancel booking
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
                                        <p className="text-sm text-gray-400 mb-2">Cancellation isn't available for this booking right now.</p>
                                    )}
                                    <button disabled className="w-full py-2.5 rounded-xl border border-gray-200 text-gray-300 text-sm font-medium cursor-not-allowed">
                                        Cancel booking
                                    </button>
                                </div>
                            )
                        )}

                        {/* confirm cancel */}
                        {confirming === 'cancel' && (
                            <div className="mb-4 p-4 rounded-xl border border-red-100 bg-red-50">
                                <p className="text-sm font-medium text-red-800 mb-3">
                                    {cancelAction === 'auto' ? 'Are you sure you want to cancel this booking?' : 'Submit a cancellation request for admin review?'}
                                </p>
                                {error && <p className="text-sm text-red-600 mb-2">{error}</p>}
                                <div className="flex gap-2">
                                    <button
                                        onClick={confirmCancel}
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

                        {/* reschedule */}
                        {!confirming && (
                            rescheduleAction === 'auto' ? (
                                <div className="mb-4">
                                    <button
                                        onClick={() => navigate(`/book/${bookingLink.slug}`, { state: {
                                            rescheduleFromId: booking.id,
                                            originalStart: booking.start,
                                            originalEnd: booking.end,
                                            studentFirst: booking.student_first,
                                            studentLast: booking.student_last,
                                            studentEmail: booking.student_email,
                                            studentPhone: booking.student_phone,
                                            parentEmail: booking.parent_email,
                                            parentPhone: booking.parent_phone,
                                        }})}
                                        className="w-full py-2.5 rounded-xl border border-indigo-200 text-indigo-600 text-sm font-medium hover:bg-indigo-50 transition-colors"
                                    >
                                        Reschedule
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
                                            requestRescheduleRef: ref,
                                            originalStart: booking.start,
                                            originalEnd: booking.end,
                                            studentFirst: booking.student_first,
                                            studentLast: booking.student_last,
                                            studentEmail: booking.student_email,
                                            studentPhone: booking.student_phone,
                                            parentEmail: booking.parent_email,
                                            parentPhone: booking.parent_phone,
                                        }})}
                                        className="w-full py-2.5 rounded-xl border border-amber-200 text-amber-700 text-sm font-medium hover:bg-amber-50 transition-colors"
                                    >
                                        Request reschedule
                                    </button>
                                </div>
                            ) : (
                                <div className="mb-4">
                                    {!sameAction && (
                                        <p className="text-sm text-gray-400 mb-2">Rescheduling isn't available for this booking right now.</p>
                                    )}
                                    <button disabled className="w-full py-2.5 rounded-xl border border-gray-200 text-gray-300 text-sm font-medium cursor-not-allowed">
                                        Reschedule
                                    </button>
                                </div>
                            )
                        )}

                        {error && !confirming && <p className="text-sm text-red-500 mt-2">{error}</p>}
                    </>
                )}

                {/* series link */}
                {booking.series_id && (
                    <div className="mt-6 pt-5 border-t border-gray-100">
                        <Link to={`/manage-series/${booking.series_id}`} className="text-sm text-indigo-500 hover:text-indigo-700 transition-colors">
                            Manage entire series →
                        </Link>
                    </div>
                )}
            </div>
        </div>
    )
}

export default ManageOccurrence
