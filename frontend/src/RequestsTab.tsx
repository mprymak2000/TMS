import { useState, useEffect } from 'react'
import { useOutletContext } from 'react-router-dom'
import { TextInput, Loader, Button, Modal } from '@mantine/core'
import { IconSearch } from '@tabler/icons-react'
import type { Booking, TutorFacetOption, EventTypeFacetOption, StudentFacetOption } from './types'
import { extractError, formatDate, formatTime, tutorBubbleClass } from './utils'
import type { BookingsOutletContext } from './BookingsLayout'
import { FiltersMenu, ActiveFilterChips, OrderToggle, LoadMoreSentinel, PAGE_SIZE } from './BookingToolbar'
import type { BookingFilters, LoadErrors } from './BookingToolbar'

// Admin-only - no customer route exists for Requests (see App.tsx: /my-bookings has no
// "requests" child route), so unlike ScheduleTab/RecurringTab this never needs isCustomer/email.
const RequestsTab = () => {
    const { tutors, eventTypes, isLoadingRoster, showToast } = useOutletContext<BookingsOutletContext>()

    const [isSubmitting, setIsSubmitting] = useState(false)
    const [processingRequest, setProcessingRequest] = useState<Booking | null>(null)
    const [loadErrors, setLoadErrors] = useState<LoadErrors>({})
    const [isLoading, setIsLoading] = useState(false)

    const [bookings, setBookings] = useState<Booking[]>([])
    const [tutorFacetOptions, setTutorFacetOptions] = useState<TutorFacetOption[]>([])
    const [eventTypeFacetOptions, setEventTypeFacetOptions] = useState<EventTypeFacetOption[]>([])
    const [studentFacetOptions, setStudentFacetOptions] = useState<StudentFacetOption[]>([])
    const [order, setOrder] = useState<'asc' | 'desc'>('asc')
    const [filters, setFilters] = useState<BookingFilters>({
        tutorIds: [], eventTypeIds: [], students: [], searchQuery: '', includeCancelled: true,
        dateFrom: null, dateTo: null,
    })
    const [cursor, setCursor] = useState<string | null>(null)
    const [isLoadingMore, setIsLoadingMore] = useState(false)

    const getSearchString = (b: Booking) => {
        const tutor = tutors.find(t => t.id === b.tutor_id)
        const eventType = eventTypes.find(e => e.id === b.event_type_id)
        return [
            b.student_first, b.student_last,
            b.student_email, b.student_phone,
            b.parent_email, b.parent_phone,
            b.start.slice(0, 10),
            tutor?.first_name, tutor?.last_name,
            eventType?.name,
        ].filter(Boolean).join(' ').toLowerCase()
    }

    // Same shape as ScheduleTab's own loadBookings (pending_only=true here, no date range there) -
    // duplicated rather than shared since each sets different local state; see
    // .claude/plans/bookings-tabs-to-subroutes.md's follow-up note for the planned dedup.
    const loadBookings = async ({
        tutorIds = filters.tutorIds,
        eventTypeIds = filters.eventTypeIds,
        students = filters.students,
        includeCancelled = filters.includeCancelled,
        cursor: cursorParam = null,
        append = false,
    }: {
        tutorIds?: string[]
        eventTypeIds?: string[]
        students?: string[]
        includeCancelled?: boolean
        cursor?: string | null
        append?: boolean
    } = {}) => {
        if (append) setIsLoadingMore(true)
        else setIsLoading(true)
        try {
            const base = `${import.meta.env.VITE_API_URL}/bookings/`
            const orderParam = `&order=asc`
            const tutorParams = tutorIds.map(id => `&tutor_ids=${id}`).join('')
            const eventTypeParams = eventTypeIds.map(id => `&event_type_ids=${id}`).join('')
            const studentParams = students.map(pair => `&student=${encodeURIComponent(pair)}`).join('')
            const includeCancelledParam = includeCancelled ? `&include_cancelled=true` : ''
            const pageSizeParam = `&page_size=${PAGE_SIZE}`
            const cursorParamStr = cursorParam ? `&cursor=${encodeURIComponent(cursorParam)}` : ''

            const response = await fetch(`${base}?${pageSizeParam}${cursorParamStr}&pending_only=true${orderParam}${tutorParams}${eventTypeParams}${studentParams}${includeCancelledParam}`)
            if (!response.ok) {
                const err = await response.json()
                setLoadErrors(prev => ({ ...prev, bookings: extractError(err, 'Failed to load requests.') }))
                return
            }
            const body = await response.json()
            setBookings(prev => append ? [...prev, ...body.items] : body.items)
            setTutorFacetOptions(body.facets.tutors)
            setEventTypeFacetOptions(body.facets.event_types)
            setStudentFacetOptions(body.facets.students)
            setCursor(body.next_cursor)
            setLoadErrors({})
        } catch (error) {
            console.error(error)
            setLoadErrors(prev => ({ ...prev, bookings: 'An unknown error occurred while loading requests.' }))
        } finally {
            setIsLoading(false)
            setIsLoadingMore(false)
        }
    }

    const handleLoadMore = () => loadBookings({ cursor, append: true })

    useEffect(() => {
        loadBookings()
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])

    const refresh = () => loadBookings()

    const handleStudentFilterToggle = (value: string) => {
        const next = filters.students.includes(value) ? filters.students.filter(x => x !== value) : [...filters.students, value]
        setFilters(f => ({ ...f, students: next }))
        loadBookings({ students: next })
    }

    // Pure display flip — displayed() re-sorts fully every render, so no refetch needed.
    const handleOrderToggle = () => setOrder(o => o === 'asc' ? 'desc' : 'asc')

    // Server-side filters now (not client-side) — same reasoning as time range: filtering an
    // already-paginated set client-side can silently hide real matches sitting on unfetched pages.
    const handleTutorFilterToggle = (id: string) => {
        const next = filters.tutorIds.includes(id) ? filters.tutorIds.filter(x => x !== id) : [...filters.tutorIds, id]
        setFilters(f => ({ ...f, tutorIds: next }))
        loadBookings({ tutorIds: next })
    }

    const handleEventTypeFilterToggle = (id: string) => {
        const next = filters.eventTypeIds.includes(id) ? filters.eventTypeIds.filter(x => x !== id) : [...filters.eventTypeIds, id]
        setFilters(f => ({ ...f, eventTypeIds: next }))
        loadBookings({ eventTypeIds: next })
    }

    const handleIncludeCancelledToggle = () => {
        const next = !filters.includeCancelled
        setFilters(f => ({ ...f, includeCancelled: next }))
        loadBookings({ includeCancelled: next })
    }

    const handleApproveRequest = async (requestId: number) => {
        setIsSubmitting(true)
        try {
            const res = await fetch(`${import.meta.env.VITE_API_URL}/bookings/booking-request/${requestId}/approve`, { method: 'POST' })
            if (!res.ok) { showToast(extractError(await res.json(), 'Failed to approve request.'), 'error'); return }
            setProcessingRequest(null)
            refresh()
            showToast('Request approved')
        } catch (error) {
            console.error(error)
            showToast('Failed to approve request.', 'error')
        } finally {
            setIsSubmitting(false)
        }
    }

    const handleDenyRequest = async (requestId: number) => {
        setIsSubmitting(true)
        try {
            const res = await fetch(`${import.meta.env.VITE_API_URL}/bookings/booking-request/${requestId}/deny`, { method: 'POST' })
            if (!res.ok) { showToast(extractError(await res.json(), 'Failed to deny request.'), 'error'); return }
            setProcessingRequest(null)
            refresh()
            showToast('Request denied')
        } catch (error) {
            console.error(error)
            showToast('Failed to deny request.', 'error')
        } finally {
            setIsSubmitting(false)
        }
    }

    // Tutor/event-type are already narrowed server-side — search is the only genuinely
    // client-side filter.
    const displayed = bookings
        .filter(b => getSearchString(b).includes(filters.searchQuery.trim().toLowerCase()))
        // The backend hardcodes order=desc for pending_only requests regardless of what's sent,
        // so this sort is load-bearing here, not just cosmetic re-display.
        .sort((a, b) => order === 'desc' ? b.start.localeCompare(a.start) : a.start.localeCompare(b.start))

    return (
        <div className="h-full flex flex-col">
            <div className="shrink-0">
                {/* load errors */}
                {loadErrors.bookings && <p className="text-sm text-red-500 mb-2">{loadErrors.bookings}</p>}

                <div className="mb-5">
                    <div className="flex items-center gap-2 flex-wrap">
                        <TextInput
                            placeholder="Search..."
                            leftSection={<IconSearch size={14} />}
                            value={filters.searchQuery}
                            onChange={(e) => {
                                const value = e.target.value
                                setFilters(f => ({ ...f, searchQuery: value }))
                            }}
                            styles={{ input: { borderRadius: '8px', fontFamily: 'inherit', borderColor: '#e5e7eb' } }}
                            size="sm"
                        />
                        <FiltersMenu
                            tutorOptions={tutorFacetOptions.map(t => ({ value: String(t.id), label: `${t.first_name} ${t.last_name}` }))}
                            tutorSelected={filters.tutorIds}
                            onTutorToggle={handleTutorFilterToggle}
                            eventTypeOptions={eventTypeFacetOptions.map(e => ({ value: String(e.id), label: e.name }))}
                            eventTypeSelected={filters.eventTypeIds}
                            onEventTypeToggle={handleEventTypeFilterToggle}
                            studentOptions={studentFacetOptions.map(s => ({ value: `${s.first_name}|${s.last_name}`, label: `${s.first_name} ${s.last_name}` }))}
                            studentSelected={filters.students}
                            onStudentToggle={handleStudentFilterToggle}
                            includeCancelled={filters.includeCancelled}
                            onIncludeCancelledToggle={handleIncludeCancelledToggle}
                        />
                        <div className="w-px h-6 bg-gray-200 mx-1" />
                        <OrderToggle order={order} onToggle={handleOrderToggle} />
                    </div>

                    <ActiveFilterChips
                        tutorIds={filters.tutorIds}
                        eventTypeIds={filters.eventTypeIds}
                        students={filters.students}
                        tutors={tutors}
                        eventTypes={eventTypes}
                        includeCancelled={filters.includeCancelled}
                        onTutorRemove={handleTutorFilterToggle}
                        onEventTypeRemove={handleEventTypeFilterToggle}
                        onStudentRemove={handleStudentFilterToggle}
                        onIncludeCancelledRemove={handleIncludeCancelledToggle}
                    />
                </div>
            </div>

            <div className="flex-1 min-h-0 overflow-y-auto">
                {(isLoading || isLoadingRoster) && <div className="flex justify-center py-12"><Loader size="sm" /></div>}

                {!isLoading && !isLoadingRoster && (
                    <div className="flex flex-col gap-3">
                        {displayed.map(b => {
                            const req = b.request!
                            const tutor = tutors.find(t => t.id === b.tutor_id)
                            const eventType = eventTypes.find(e => e.id === b.event_type_id)
                            const isReschedule = req.type === 'reschedule_occurrence' || req.type === 'reschedule_series'
                            const isSeries = req.type === 'cancel_series' || req.type === 'reschedule_series'
                            const typeLabel = {
                                cancel_occurrence: 'Cancel occurrence',
                                reschedule_occurrence: 'Reschedule occurrence',
                                cancel_series: 'Cancel series',
                                reschedule_series: 'Reschedule series',
                            }[req.type] ?? req.type

                            return (
                                <div key={b.id} className="bg-white border border-gray-200 border-l-4 border-l-amber-400 rounded-xl shadow-sm overflow-hidden">
                                    <div className="flex items-center px-5 py-4 gap-4">
                                        <div className="flex-1 min-w-0">
                                            <div className="flex items-center gap-2 mb-1 flex-wrap">
                                                <span className="font-medium text-gray-800">{b.student_first} {b.student_last}</span>
                                                <span className="text-xs bg-amber-50 text-amber-700 border border-amber-200 px-2 py-0.5 rounded-full font-medium">{typeLabel}</span>
                                                {eventType && <span className="text-xs bg-indigo-50 text-indigo-600 px-2 py-0.5 rounded-full">{eventType.name}</span>}
                                                {isSeries && <span className="text-xs bg-gray-100 text-gray-500 px-2 py-0.5 rounded-full">Series</span>}
                                            </div>
                                            <p className="text-xs text-gray-400">
                                                Current: {formatDate(b.start)} · {formatTime(b.start)}
                                                {tutor && ` · ${tutor.first_name} ${tutor.last_name}`}
                                            </p>
                                            {isReschedule && req.requested_start && (
                                                <p className="text-xs text-indigo-500 mt-0.5">
                                                    Requested: {formatDate(req.requested_start)} · {formatTime(req.requested_start)}
                                                </p>
                                            )}
                                        </div>
                                        {tutor && (
                                            <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold shrink-0 ${tutorBubbleClass(tutor)}`}>
                                                {tutor.first_name[0]}{tutor.last_name[0]}
                                            </div>
                                        )}
                                        <Button size="xs" variant="light" color="amber" onClick={() => setProcessingRequest(b)}>
                                            Review
                                        </Button>
                                    </div>
                                </div>
                            )
                        })}
                        {displayed.length === 0 && (
                            <p className="text-sm text-gray-400 text-center py-12">No pending requests.</p>
                        )}
                    </div>
                )}

                {!isLoading && !isLoadingRoster && cursor !== null && (
                    <LoadMoreSentinel onVisible={handleLoadMore} loading={isLoadingMore} />
                )}
            </div>

            {/* approve/deny request modal */}
            <Modal
                opened={processingRequest !== null}
                onClose={() => setProcessingRequest(null)}
                title="Review request"
                centered
                size="sm"
            >
                {processingRequest && (() => {
                    const req = processingRequest.request!
                    const isReschedule = req.type === 'reschedule_occurrence' || req.type === 'reschedule_series'
                    const requestedTutor = req.requested_tutor_id ? tutors.find(t => t.id === req.requested_tutor_id) : null
                    return (
                        <div className="flex flex-col gap-3">
                            <div className="text-sm text-gray-700">
                                <p><span className="text-gray-400">Student:</span> {processingRequest.student_first} {processingRequest.student_last}</p>
                                <p><span className="text-gray-400">Type:</span> {req.type.replace(/_/g, ' ')}</p>
                                <p><span className="text-gray-400">Current slot:</span> {formatDate(processingRequest.start)} · {formatTime(processingRequest.start)}</p>
                                {isReschedule && req.requested_start && (
                                    <p><span className="text-gray-400">Requested slot:</span> {formatDate(req.requested_start)} · {formatTime(req.requested_start)}</p>
                                )}
                                {requestedTutor && (
                                    <p><span className="text-gray-400">Requested tutor:</span> {requestedTutor.first_name} {requestedTutor.last_name}</p>
                                )}
                                {req.reason && (
                                    <p><span className="text-gray-400">Reason:</span> {req.reason}</p>
                                )}
                            </div>
                            <div className="flex justify-end gap-2 mt-1">
                                <Button variant="default" disabled={isSubmitting} onClick={() => setProcessingRequest(null)}>Close</Button>
                                <Button color="red" variant="light" loading={isSubmitting} onClick={() => handleDenyRequest(req.id)}>Deny</Button>
                                <Button color="green" loading={isSubmitting} onClick={() => handleApproveRequest(req.id)}>Approve</Button>
                            </div>
                        </div>
                    )
                })()}
            </Modal>
        </div>
    )
}

export default RequestsTab
