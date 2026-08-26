import { useState, useEffect } from 'react'
import type { ReactNode } from 'react'
import { useOutletContext } from 'react-router-dom'
import { TextInput, Loader, Button, Modal } from '@mantine/core'
import { IconSearch } from '@tabler/icons-react'
import type { Tutor, EventType, BookingSeries, TutorFacetOption, EventTypeFacetOption, StudentFacetOption } from './types'
import { extractError, DAY_NAMES, weekdayOf, timeOf } from './utils'
import SeriesRow from './SeriesRow'
import type { BookingsOutletContext } from './BookingsLayout'
import { FiltersMenu, ActiveFilterChips, OrderToggle } from './BookingToolbar'
import type { BookingFilters, LoadErrors } from './BookingToolbar'

// Shared by admin and customer Recurring tab — Monday..Sunday section headers (only for days
// with something to show), full-width SeriesRow cards underneath each, one connected card.
// Mirrors Schedule's proven day-header-bar pattern instead of a cramped 7-column grid.
const RecurringList = ({
    seriesByDay,
    tutors,
    eventTypes,
    isCustomer,
    includeCancelled,
    onRefresh,
    onError,
    onCancelSeries,
    emptyState,
}: {
    seriesByDay: { day: number; name: string; series: BookingSeries[] }[]
    tutors: Tutor[]
    eventTypes: EventType[]
    isCustomer: boolean
    includeCancelled: boolean
    onRefresh: (msg: string) => void
    onError: (msg: string) => void
    onCancelSeries: (seriesId: string) => void
    onPermanentDeleteSeries: (seriesId: string) => void
    emptyState: ReactNode
}) => {
    // Only one series open at a time across the whole list — expanding one collapses whichever
    // other row was open.
    const [expandedSeriesId, setExpandedSeriesId] = useState<string | null>(null)
    if (seriesByDay.length === 0) return <>{emptyState}</>
    return (
        <div className="bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden">
            {seriesByDay.map(({ day, name, series }, i) => (
                <div key={day}>
                    <div className={`px-5 py-2 bg-gray-50/80 text-[11px] font-semibold text-gray-500 uppercase tracking-wide ${i > 0 ? 'border-t border-gray-100' : ''}`}>
                        {name}
                    </div>
                    {series.map((s, j) => (
                        <div key={s.id} className={j > 0 ? 'border-t border-gray-100' : ''}>
                            <SeriesRow
                                series={s}
                                tutor={tutors.find(t => t.id === s.tutor_id)!}
                                tutors={tutors}
                                eventType={eventTypes.find(e => e.id === s.event_type_id)!}
                                onRefresh={onRefresh}
                                onError={onError}
                                onCancelSeries={onCancelSeries}
                                onPermanentDeleteSeries={onPermanentDeleteSeries}
                                expanded={expandedSeriesId === s.id}
                                onToggleExpand={() => setExpandedSeriesId(prev => prev === s.id ? null : s.id)}
                                isCustomer={isCustomer}
                                includeCancelled={includeCancelled}
                            />
                        </div>
                    ))}
                </div>
            ))}
        </div>
    )
}

const RecurringTab = ({ isCustomer = false }: { isCustomer?: boolean }) => {
    const { tutors, eventTypes, isLoadingRoster, showToast } = useOutletContext<BookingsOutletContext>()

    const [email] = useState('')
    const [seriesList, setSeriesList] = useState<BookingSeries[]>([])
    const [isLoadingSeries, setIsLoadingSeries] = useState(false)
    const [tutorFacetOptions, setTutorFacetOptions] = useState<TutorFacetOption[]>([])
    const [eventTypeFacetOptions, setEventTypeFacetOptions] = useState<EventTypeFacetOption[]>([])
    const [studentFacetOptions, setStudentFacetOptions] = useState<StudentFacetOption[]>([])
    const [order, setOrder] = useState<'asc' | 'desc'>('asc')
    const [filters, setFilters] = useState<BookingFilters>({
        tutorIds: [], eventTypeIds: [], students: [], searchQuery: '', includeCancelled: true,
        dateFrom: null, dateTo: null,
    })
    const [loadErrors, setLoadErrors] = useState<LoadErrors>({})
    const [cancellingSeriesId, setCancellingSeriesId] = useState<string | null>(null)
    const [isCancelling, setIsCancelling] = useState(false)
    const [permanentDeleteSeriesId, setPermanentDeleteSeriesId] = useState<string | null>(null)
    const [confirmingCascadeDeleteSeriesId, setConfirmingCascadeDeleteSeriesId] = useState<string | null>(null)
    const [isPermanentDeleting, setIsPermanentDeleting] = useState(false)

    const getSeriesSearchString = (s: BookingSeries) => {
        const tutor = tutors.find(t => t.id === s.tutor_id)
        const eventType = eventTypes.find(e => e.id === s.event_type_id)
        return [
            s.student_first, s.student_last,
            tutor?.first_name, tutor?.last_name,
            eventType?.name,
        ].filter(Boolean).join(' ').toLowerCase()
    }

    const loadBookingSeries = async ({
        tutorIds = filters.tutorIds,
        eventTypeIds = filters.eventTypeIds,
        students = filters.students,
        emailFilter,
    }: {
        tutorIds?: string[]
        eventTypeIds?: string[]
        students?: string[]
        emailFilter?: string
    } = {}) => {
        setIsLoadingSeries(true)
        try {
            const base = `${import.meta.env.VITE_API_URL}/bookings/booking-series`
            const emailParam = emailFilter ? `&email=${encodeURIComponent(emailFilter)}` : ''
            const tutorParams = tutorIds.map(id => `&tutor_ids=${id}`).join('')
            const eventTypeParams = eventTypeIds.map(id => `&event_type_ids=${id}`).join('')
            const studentParams = students.map(pair => `&student=${encodeURIComponent(pair)}`).join('')

            const response = await fetch(`${base}?${emailParam}${tutorParams}${eventTypeParams}${studentParams}`)
            if (!response.ok) {
                const err = await response.json()
                setLoadErrors(prev => ({ ...prev, bookings: extractError(err, 'Failed to load series.') }))
                return
            }
            const body = await response.json()
            setSeriesList(body.items)
            setTutorFacetOptions(body.facets.tutors)
            setEventTypeFacetOptions(body.facets.event_types)
            setStudentFacetOptions(body.facets.students)
            setLoadErrors({})
        } catch (error) {
            console.error(error)
            setLoadErrors(prev => ({ ...prev, bookings: 'An unknown error occurred while loading series.' }))
        } finally {
            setIsLoadingSeries(false)
        }
    }

    useEffect(() => {
        if (!isCustomer) { loadBookingSeries(); return }
        const saved = sessionStorage.getItem('customer_email')
        if (saved) loadBookingSeries({ emailFilter: saved })
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])

    const refresh = () => loadBookingSeries({ emailFilter: isCustomer ? email : undefined })

    const handleStudentFilterToggle = (value: string) => {
        const next = filters.students.includes(value) ? filters.students.filter(x => x !== value) : [...filters.students, value]
        setFilters(f => ({ ...f, students: next }))
        loadBookingSeries({ emailFilter: isCustomer ? email : undefined, students: next })
    }

    // Pure display flip — displayed() re-sorts fully every render, so no refetch needed.
    const handleOrderToggle = () => setOrder(o => o === 'asc' ? 'desc' : 'asc')

    // Server-side filters now (not client-side) — same reasoning as time range: filtering an
    // already-paginated set client-side can silently hide real matches sitting on unfetched pages.
    const handleTutorFilterToggle = (id: string) => {
        const next = filters.tutorIds.includes(id) ? filters.tutorIds.filter(x => x !== id) : [...filters.tutorIds, id]
        setFilters(f => ({ ...f, tutorIds: next }))
        loadBookingSeries({ emailFilter: isCustomer ? email : undefined, tutorIds: next })
    }

    const handleEventTypeFilterToggle = (id: string) => {
        const next = filters.eventTypeIds.includes(id) ? filters.eventTypeIds.filter(x => x !== id) : [...filters.eventTypeIds, id]
        setFilters(f => ({ ...f, eventTypeIds: next }))
        loadBookingSeries({ emailFilter: isCustomer ? email : undefined, eventTypeIds: next })
    }

    // BookingSeries rows carry no status of their own; SeriesRow reads includeCancelled directly
    // and reloads its own occurrences whenever the flag changes - no refetch needed here.
    const handleIncludeCancelledToggle = () => {
        setFilters(f => ({ ...f, includeCancelled: !f.includeCancelled }))
    }

    const handleCancelSeries = async (seriesId: string) => {
        setIsCancelling(true)
        try {
            const res = await fetch(`${import.meta.env.VITE_API_URL}/bookings/booking-series/${seriesId}`, { method: 'DELETE' })
            if (!res.ok) {
                showToast(extractError(await res.json(), 'Failed to cancel series.'), 'error')
                setCancellingSeriesId(null)
                return
            }
            setCancellingSeriesId(null)
            refresh()
            showToast('Series cancelled')
        } catch (error) {
            console.error(error)
            showToast('Failed to cancel series.', 'error')
            setCancellingSeriesId(null)
        } finally {
            setIsCancelling(false)
        }
    }

    // Two-step cascade pattern, same as useBookingActions.tsx's handlePermanentDelete: first call
    // (cascade=false) returns 409 if a rescheduled predecessor exists, which triggers the cascade
    // confirm modal. User confirms → second call (cascade=true) walks and deletes the whole chain.
    const handlePermanentDeleteSeries = async (seriesId: string, cascade = false) => {
        setIsPermanentDeleting(true)
        try {
            const url = `${import.meta.env.VITE_API_URL}/bookings/booking-series/${seriesId}/permanent${cascade ? '?cascade=true' : ''}`
            const res = await fetch(url, { method: 'DELETE' })
            if (res.status === 409) {
                setPermanentDeleteSeriesId(null)
                setConfirmingCascadeDeleteSeriesId(seriesId)
                return
            }
            if (!res.ok) {
                showToast(extractError(await res.json(), 'Failed to permanently delete series.'), 'error')
                setPermanentDeleteSeriesId(null)
                setConfirmingCascadeDeleteSeriesId(null)
                return
            }
            setPermanentDeleteSeriesId(null)
            setConfirmingCascadeDeleteSeriesId(null)
            refresh()
            showToast('Series deleted')
        } catch (error) {
            console.error(error)
            showToast('Failed to permanently delete series.', 'error')
            setPermanentDeleteSeriesId(null)
            setConfirmingCascadeDeleteSeriesId(null)
        } finally {
            setIsPermanentDeleting(false)
        }
    }

    // Tutor/event-type narrowing happens server-side via loadBookingSeries (see get_booking_series) —
    // search is the only genuinely client-side filter.
    const displayedSeries = seriesList
        .filter(s => getSeriesSearchString(s).includes(filters.searchQuery.trim().toLowerCase()))

    // Grouped Monday(0)..Sunday(6), skipping days with nothing to show, sorted by time-of-day
    // within each day — "HH:MM:SS" strings compare correctly lexicographically.
    const seriesByDay = DAY_NAMES
        .map((name, day) => ({
            day,
            name,
            series: displayedSeries
                .filter(s => weekdayOf(s.dtstart) === day)
                .sort((a, b) => order === 'desc' ? timeOf(b.dtstart).localeCompare(timeOf(a.dtstart)) : timeOf(a.dtstart).localeCompare(timeOf(b.dtstart))),
        }))
        .filter(({ series }) => series.length > 0)
    const orderedSeriesByDay = order === 'desc' ? [...seriesByDay].reverse() : seriesByDay

    return (
        <div className="h-full flex flex-col">
            <div className="shrink-0">
                {/* load errors */}
                {loadErrors.bookings && <p className="text-sm text-red-500 mb-2">{loadErrors.bookings}</p>}

                {!isCustomer && (
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
                                // No manual eventTypes.filter(e => e.recurring) special-case needed —
                                // eventTypeFacetOptions comes from get_booking_series's facets, which
                                // are already recurring-only by construction (a BookingSeries only
                                // ever exists for a recurring=true event type).
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
                )}
            </div>

            <div className="flex-1 min-h-0 overflow-y-auto">
                {(isLoadingSeries || isLoadingRoster) && <div className="flex justify-center py-12"><Loader size="sm" /></div>}

                {!isLoadingSeries && !isLoadingRoster && (
                    <RecurringList
                        seriesByDay={orderedSeriesByDay}
                        tutors={tutors}
                        eventTypes={eventTypes}
                        isCustomer={isCustomer}
                        includeCancelled={filters.includeCancelled}
                        onRefresh={msg => { refresh(); showToast(msg) }}
                        onError={msg => showToast(msg, 'error')}
                        onCancelSeries={setCancellingSeriesId}
                        onPermanentDeleteSeries={setPermanentDeleteSeriesId}
                        emptyState={<p className="text-sm text-gray-400 text-center py-12">No recurring series.</p>}
                    />
                )}
            </div>

            {/* cancel series confirm modal — admin only */}
            {!isCustomer && <Modal
                opened={cancellingSeriesId !== null}
                onClose={() => setCancellingSeriesId(null)}
                title="Cancel entire series?"
                centered
                size="sm"
            >
                <p className="text-sm text-gray-600 mb-4">
                    All future occurrences will be removed and the recurring calendar event will be cancelled.
                </p>
                <div className="flex justify-end gap-2">
                    <Button variant="default" onClick={() => setCancellingSeriesId(null)}>Keep it</Button>
                    <Button color="red" loading={isCancelling} onClick={() => cancellingSeriesId !== null && handleCancelSeries(cancellingSeriesId)}>
                        Cancel series
                    </Button>
                </div>
            </Modal>}

            {/* permanent delete confirm modal — admin only */}
            {!isCustomer && <Modal
                opened={permanentDeleteSeriesId !== null}
                onClose={() => setPermanentDeleteSeriesId(null)}
                title="Permanently delete this series?"
                centered
                size="sm"
            >
                <p className="text-sm text-gray-600 mb-4">
                    This cannot be undone. Every booking in this series, past and future, will be permanently deleted along with the recurring calendar event.
                </p>
                <div className="flex justify-end gap-2">
                    <Button variant="default" onClick={() => setPermanentDeleteSeriesId(null)}>Keep it</Button>
                    <Button color="red" loading={isPermanentDeleting} onClick={() => permanentDeleteSeriesId !== null && handlePermanentDeleteSeries(permanentDeleteSeriesId)}>
                        Delete permanently
                    </Button>
                </div>
            </Modal>}

            {/* cascade delete confirm modal — admin only, shown when the series has a rescheduled predecessor chain */}
            {!isCustomer && <Modal
                opened={confirmingCascadeDeleteSeriesId !== null}
                onClose={() => setConfirmingCascadeDeleteSeriesId(null)}
                title="Delete entire reschedule chain?"
                centered
                size="sm"
            >
                <p className="text-sm text-gray-600 mb-4">
                    This series was created by rescheduling an earlier one. All series in the reschedule chain, and every booking in each, will be permanently deleted.
                </p>
                <div className="flex justify-end gap-2">
                    <Button variant="default" onClick={() => setConfirmingCascadeDeleteSeriesId(null)}>Cancel</Button>
                    <Button color="red" loading={isPermanentDeleting} onClick={() => confirmingCascadeDeleteSeriesId !== null && handlePermanentDeleteSeries(confirmingCascadeDeleteSeriesId, true)}>
                        Delete all
                    </Button>
                </div>
            </Modal>}
        </div>
    )
}

export default RecurringTab
