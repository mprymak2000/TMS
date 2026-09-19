import { useState, useEffect } from 'react'
import type { ReactNode } from 'react'
import { useOutletContext } from 'react-router-dom'
import { TextInput, Loader, Button } from '@mantine/core'
import AppModal, { ModalFooter } from './AppModal'
import { IconSearch } from '@tabler/icons-react'
import type { Tutor, BookingLink, BookingType, BookingSeries, BookingFacets } from './types'
import { extractError, DAY_NAMES, weekdayOf, timeOf } from './utils'
import SeriesRow from './SeriesRow'
import type { BookingsOutletContext } from './BookingsLayout'
import { FiltersMenu, ActiveFilterChips, OrderToggle, EMPTY_FACETS } from './BookingToolbar'
import type { BookingFilters, LoadErrors } from './BookingToolbar'

// Shared by admin and customer Recurring tab — Monday..Sunday section headers (only for days
// with something to show), full-width SeriesRow cards underneath each, one connected card.
// Mirrors Schedule's proven day-header-bar pattern instead of a cramped 7-column grid.
const RecurringList = ({
    seriesByDay,
    tutors,
    bookingLinks,
    bookingTypes,
    reloadBookingTypes,
    onSeriesUpdated,
    includeCancelled,
    onRefresh,
    onError,
    onCancelSeries,
    onPermanentDeleteSeries,
    emptyState,
    expandedSeriesId,
    onToggleExpand,
}: {
    seriesByDay: { day: number; name: string; series: BookingSeries[] }[]
    tutors: Tutor[]
    bookingTypes: BookingType[]
    reloadBookingTypes: () => void
    onSeriesUpdated: (series: BookingSeries) => void
    bookingLinks: BookingLink[]
    includeCancelled: boolean
    onRefresh: (msg: string) => void
    onError: (msg: string) => void
    onCancelSeries: (seriesId: string) => void
    onPermanentDeleteSeries: (seriesId: string) => void
    emptyState: ReactNode
    expandedSeriesId: string | null
    onToggleExpand: (seriesId: string) => void
}) => {
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
                                tutor={tutors.find(t => t.id === s.tutor_id)}
                                tutors={tutors}
                                bookingLink={bookingLinks.find(e => e.id === s.booking_link_id)}
                                bookingTypes={bookingTypes}
                                reloadBookingTypes={reloadBookingTypes}
                                onSeriesUpdated={onSeriesUpdated}
                                bookingLinks={bookingLinks}
                                onRefresh={onRefresh}
                                onError={onError}
                                onCancelSeries={onCancelSeries}
                                onPermanentDeleteSeries={onPermanentDeleteSeries}
                                expanded={expandedSeriesId === s.id}
                                onToggleExpand={() => onToggleExpand(s.id)}
                                includeCancelled={includeCancelled}
                            />
                        </div>
                    ))}
                </div>
            ))}
        </div>
    )
}

const RecurringTab = () => {
    const { tutors, bookingLinks, bookingTypes, reloadBookingTypes, isLoadingRoster, showToast } = useOutletContext<BookingsOutletContext>()

    const [seriesList, setSeriesList] = useState<BookingSeries[]>([])
    // Only one series open at a time across the whole list — expanding one collapses whichever other
    // row was open. Owned here rather than by RecurringList, which the loading branch below unmounts
    // on every non-silent reload, taking the open row with it.
    const [expandedSeriesId, setExpandedSeriesId] = useState<string | null>(null)
    const [isLoadingSeries, setIsLoadingSeries] = useState(false)
    const [facets, setFacets] = useState<BookingFacets>(EMPTY_FACETS)
    const [order, setOrder] = useState<'asc' | 'desc'>('asc')
    const [filters, setFilters] = useState<BookingFilters>({
        tutorIds: [], bookingLinkIds: [], bookingTypeIds: [], attendeeIds: [], searchQuery: '', includeCancelled: true,
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
        const bookingLink = bookingLinks.find(e => e.id === s.booking_link_id)
        return [
            s.attendee.first_name, s.attendee.last_name, s.attendee.email, s.attendee.phone,
            s.payer.first_name, s.payer.last_name, s.payer.email, s.payer.phone,
            tutor?.first_name, tutor?.last_name,
            bookingLink?.slug,
        ].filter(Boolean).join(' ').toLowerCase()
    }

    const loadBookingSeries = async ({
        tutorIds = filters.tutorIds,
        bookingLinkIds = filters.bookingLinkIds,
        bookingTypeIds = filters.bookingTypeIds,
        attendeeIds = filters.attendeeIds,
        silent = false,
    }: {
        tutorIds?: string[]
        bookingLinkIds?: string[]
        bookingTypeIds?: string[]
        attendeeIds?: string[]
        // Background revalidation after an inline edit — no spinner, no list swap.
        silent?: boolean
    } = {}) => {
        if (!silent) setIsLoadingSeries(true)
        try {
            const base = `${import.meta.env.VITE_API_URL}/bookings/booking-series`
            const tutorParams = tutorIds.map(id => `&tutor_ids=${id}`).join('')
            const bookingLinkParams = bookingLinkIds.map(id => `&booking_link_ids=${id}`).join('')
            const bookingTypeParams = bookingTypeIds.map(id => `&booking_type_ids=${id}`).join('')
            const attendeeParams = attendeeIds.map(id => `&attendee_ids=${id}`).join('')

            const response = await fetch(`${base}?${tutorParams}${bookingLinkParams}${bookingTypeParams}${attendeeParams}`)
            if (!response.ok) {
                const err = await response.json()
                setLoadErrors(prev => ({ ...prev, bookings: extractError(err, 'Failed to load series.') }))
                return
            }
            const body = await response.json()
            // A silent revalidation keeps only the facets. The caller has already patched the edited
            // row from the PUT response, so writing the list here would overwrite that with the list
            // endpoint's copy — which, if the edit changed a filtered field, no longer includes it.
            if (!silent) setSeriesList(body.items)
            setFacets(body.facets)
            setLoadErrors({})
        } catch (error) {
            console.error(error)
            setLoadErrors(prev => ({ ...prev, bookings: 'An unknown error occurred while loading series.' }))
        } finally {
            setIsLoadingSeries(false)
        }
    }

    useEffect(() => {
        loadBookingSeries()
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])

    const refresh = () => loadBookingSeries()

    const handleAttendeeFilterToggle = (id: string) => {
        const next = filters.attendeeIds.includes(id) ? filters.attendeeIds.filter(x => x !== id) : [...filters.attendeeIds, id]
        setFilters(f => ({ ...f, attendeeIds: next }))
        loadBookingSeries({ attendeeIds: next })
    }

    // Pure display flip — displayed() re-sorts fully every render, so no refetch needed.
    const handleOrderToggle = () => setOrder(o => o === 'asc' ? 'desc' : 'asc')

    // Server-side filters now (not client-side) — same reasoning as time range: filtering an
    // already-paginated set client-side can silently hide real matches sitting on unfetched pages.
    const handleTutorFilterToggle = (id: string) => {
        const next = filters.tutorIds.includes(id) ? filters.tutorIds.filter(x => x !== id) : [...filters.tutorIds, id]
        setFilters(f => ({ ...f, tutorIds: next }))
        loadBookingSeries({ tutorIds: next })
    }

    const handleBookingLinkFilterToggle = (id: string) => {
        const next = filters.bookingLinkIds.includes(id) ? filters.bookingLinkIds.filter(x => x !== id) : [...filters.bookingLinkIds, id]
        setFilters(f => ({ ...f, bookingLinkIds: next }))
        loadBookingSeries({ bookingLinkIds: next })
    }

    const handleBookingTypeFilterToggle = (id: string) => {
        const next = filters.bookingTypeIds.includes(id) ? filters.bookingTypeIds.filter(x => x !== id) : [...filters.bookingTypeIds, id]
        setFilters(f => ({ ...f, bookingTypeIds: next }))
        loadBookingSeries({ bookingTypeIds: next })
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
                                tutorOptions={facets.tutors.map(t => ({ value: String(t.id), label: `${t.first_name} ${t.last_name}` }))}
                                tutorSelected={filters.tutorIds}
                                onTutorToggle={handleTutorFilterToggle}
                                // No manual bookingLinks.filter(e => e.recurring) special-case needed —
                                // facets.booking_links comes from get_booking_series's facets, which
                                // are already recurring-only by construction (a BookingSeries only
                                // ever exists for a recurring=true booking link).
                                bookingLinkOptions={facets.booking_links.map(e => ({ value: String(e.id), label: e.slug }))}
                                bookingLinkSelected={filters.bookingLinkIds}
                                onBookingLinkToggle={handleBookingLinkFilterToggle}
                            bookingTypeOptions={facets.booking_types.map(t => ({ value: String(t.id), label: t.label }))}
                            bookingTypeSelected={filters.bookingTypeIds}
                            onBookingTypeToggle={handleBookingTypeFilterToggle}
                                attendeeOptions={facets.attendees.map(a => ({ value: String(a.id), label: `${a.first_name} ${a.last_name}` }))}
                                attendeeSelected={filters.attendeeIds}
                                onAttendeeToggle={handleAttendeeFilterToggle}
                                includeCancelled={filters.includeCancelled}
                                onIncludeCancelledToggle={handleIncludeCancelledToggle}
                            />
                            <div className="w-px h-6 bg-gray-200 mx-1" />
                            <OrderToggle order={order} onToggle={handleOrderToggle} />
                        </div>

                        <ActiveFilterChips
                            tutorIds={filters.tutorIds}
                            bookingLinkIds={filters.bookingLinkIds}
                        bookingTypeIds={filters.bookingTypeIds}
                            attendeeIds={filters.attendeeIds}
                            facets={facets}
                            includeCancelled={filters.includeCancelled}
                            onTutorRemove={handleTutorFilterToggle}
                            onBookingLinkRemove={handleBookingLinkFilterToggle}
                        onBookingTypeRemove={handleBookingTypeFilterToggle}
                            onAttendeeRemove={handleAttendeeFilterToggle}
                            onIncludeCancelledRemove={handleIncludeCancelledToggle}
                        />
                </div>
            </div>

            <div className="flex-1 min-h-0 overflow-y-auto">
                {(isLoadingSeries || isLoadingRoster) && <div className="flex justify-center py-12"><Loader size="sm" /></div>}

                {!isLoadingSeries && !isLoadingRoster && (
                    <RecurringList
                        seriesByDay={orderedSeriesByDay}
                        tutors={tutors}
                        bookingLinks={bookingLinks}
                        bookingTypes={bookingTypes}
                        reloadBookingTypes={reloadBookingTypes}
                        onSeriesUpdated={updated => {
                            setSeriesList(prev => prev.map(x => x.id === updated.id ? updated : x))
                            loadBookingSeries({ silent: true })
                        }}
                        includeCancelled={filters.includeCancelled}
                        onRefresh={msg => { refresh(); showToast(msg) }}
                        onError={msg => showToast(msg, 'error')}
                        onCancelSeries={setCancellingSeriesId}
                        onPermanentDeleteSeries={setPermanentDeleteSeriesId}
                        emptyState={<p className="text-sm text-gray-400 text-center py-12">No recurring series.</p>}
                        expandedSeriesId={expandedSeriesId}
                        onToggleExpand={id => setExpandedSeriesId(prev => prev === id ? null : id)}
                    />
                )}
            </div>

            {/* cancel series confirm modal */}
            <AppModal
                opened={cancellingSeriesId !== null}
                onClose={() => setCancellingSeriesId(null)}
                title="Cancel entire series?"
                caption="All future occurrences will be removed and the recurring calendar event will be cancelled."
            >
                <ModalFooter>
                    <Button variant="subtle" color="gray" onClick={() => setCancellingSeriesId(null)}>Keep it</Button>
                    <Button color="red" loading={isCancelling} onClick={() => cancellingSeriesId !== null && handleCancelSeries(cancellingSeriesId)}>
                        Cancel series
                    </Button>
                </ModalFooter>
            </AppModal>

            {/* permanent delete confirm modal */}
            <AppModal
                opened={permanentDeleteSeriesId !== null}
                onClose={() => setPermanentDeleteSeriesId(null)}
                title="Permanently delete this series?"
                caption="This cannot be undone. Every booking in this series, past and future, will be permanently deleted along with the recurring calendar event."
            >
                <ModalFooter>
                    <Button variant="subtle" color="gray" onClick={() => setPermanentDeleteSeriesId(null)}>Keep it</Button>
                    <Button color="red" loading={isPermanentDeleting} onClick={() => permanentDeleteSeriesId !== null && handlePermanentDeleteSeries(permanentDeleteSeriesId)}>
                        Delete permanently
                    </Button>
                </ModalFooter>
            </AppModal>

            {/* cascade delete confirm modal — shown when the series has a rescheduled predecessor chain */}
            <AppModal
                opened={confirmingCascadeDeleteSeriesId !== null}
                onClose={() => setConfirmingCascadeDeleteSeriesId(null)}
                title="Delete entire reschedule chain?"
                caption="This series was created by rescheduling an earlier one. All series in the reschedule chain, and every booking in each, will be permanently deleted."
            >
                <ModalFooter>
                    <Button variant="subtle" color="gray" onClick={() => setConfirmingCascadeDeleteSeriesId(null)}>Cancel</Button>
                    <Button color="red" loading={isPermanentDeleting} onClick={() => confirmingCascadeDeleteSeriesId !== null && handlePermanentDeleteSeries(confirmingCascadeDeleteSeriesId, true)}>
                        Delete all
                    </Button>
                </ModalFooter>
            </AppModal>
        </div>
    )
}

export default RecurringTab
