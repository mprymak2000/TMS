import { useState, useEffect } from 'react'
import { TextInput, Loader, Input, Button, Modal, Popover, Menu, Switch } from '@mantine/core'
import { IconSearch, IconCalendar, IconFilter, IconX, IconCheck, IconChevronDown } from '@tabler/icons-react'
import type { Tutor, EventType, Booking, BookingSeries } from './types'
import { extractError, formatDate, formatTime, tutorBubbleClass } from './utils'
import { useToast } from './useToast'
import BookingRow from './BookingRow'
import SeriesRow from './SeriesRow'
import Toast from './Toast'

interface BookingFilters {
    tutorIds: string[]
    eventTypeIds: string[]
    dateFrom: string | null
    dateTo: string | null
    searchQuery: string
    pendingOnly: boolean
}

interface LoadErrors {
    bookings?: string
    tutors?: string
    eventTypes?: string
}

// Two independent axes, not one flat list: which VIEW (row shape/interaction model — flat
// occurrence feed vs. series-grouped-with-manage vs. pending-decisions) is the outer tab,
// since it changes the whole interaction paradigm; TIME SCOPE is just a filter within a view,
// so it's an inner toggle instead — Requests has no time scope at all (nothing to filter).
type BookingsTab = 'timeline' | 'recurring' | 'requests'
type TimeScope = 'upcoming' | 'past'
type FetchResult = { ok: true; items: Booking[]; hasMore: boolean } | { ok: false; error: any }

const TABS = [
    { key: 'timeline', label: 'Timeline' },
    { key: 'recurring', label: 'Recurring' },
    { key: 'requests', label: 'Requests' },
] as const

const PAGE_SIZE = 10

// Exact-day comparison key (ISO date, unambiguous across years) vs. the subtle
// display label shown above the first booking of each new day — WhatsApp-style
// date pill, not a full grouped card.
const dateKeyOf = (iso: string) => iso.slice(0, 10)
const dateLabelOf = (iso: string) => new Date(iso).toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' })

// Same-day only — a gap indicator is only meaningful within one day's flow;
// crossing into a new day is already communicated by the date pill above.
const gapMinutesBetween = (prevEndIso: string, nextStartIso: string): number =>
    (new Date(nextStartIso).getTime() - new Date(prevEndIso).getTime()) / 60000

const formatGap = (minutes: number): string => {
    const hours = Math.floor(minutes / 60)
    const mins = Math.round(minutes % 60)
    if (hours === 0) return `${mins}m`
    if (mins === 0) return `${hours}h`
    return `${hours}h ${mins}m`
}

const fetchMyBookings = async (tab: 'timeline' | 'requests', timeScope: TimeScope, pageNum: number, emailFilter?: string): Promise<FetchResult> => {
    const base = `${import.meta.env.VITE_API_URL}/bookings/my-bookings`
    const emailParam = emailFilter ? `&email=${encodeURIComponent(emailFilter)}` : ''
    const query = tab === 'requests' ? `pending_only=true` : `status=${timeScope}`

    const res = await fetch(`${base}?${query}&page=${pageNum}${emailParam}`)
    if (!res.ok) { return { ok: false, error: await res.json() } }
    const items = (await res.json()).items as Booking[]
    // If the number of items returned is less than PAGE_SIZE, it means there are no more items to fetch.
    // TODO: this behavior needs to be revisited and formalized but ok for now.
    return { ok: true, items, hasMore: items.length === PAGE_SIZE }
}

const FilterChip = ({ label, onRemove }: { label: string; onRemove: () => void }) => (
    <span className="inline-flex items-center gap-1 bg-indigo-50 text-indigo-700 text-xs font-medium pl-2.5 pr-1.5 py-1 rounded-full">
        {label}
        <button onClick={onRemove} className="hover:text-indigo-900 transition-colors">
            <IconX size={12} />
        </button>
    </span>
)

interface CheckDropdownOption {
    value: string
    label: string
}

const CheckDropdown = ({
    label,
    options,
    selected,
    onToggle,
}: {
    label: string
    options: CheckDropdownOption[]
    selected: string[]
    onToggle: (value: string) => void
}) => (
    <Menu closeOnItemClick={false} withinPortal={false} shadow="md" width={220} position="bottom-start">
        <Menu.Target>
            <Button
                variant="default"
                size="sm"
                rightSection={<IconChevronDown size={14} />}
                styles={{ root: { borderRadius: '8px', borderColor: '#e5e7eb' }, label: { fontWeight: 400 } }}
            >
                {label}{selected.length > 0 ? ` · ${selected.length}` : ''}
            </Button>
        </Menu.Target>
        <Menu.Dropdown>
            {options.length === 0 && <Menu.Item disabled>None available</Menu.Item>}
            {options.map(opt => {
                const checked = selected.includes(opt.value)
                return (
                    <Menu.Item
                        key={opt.value}
                        onClick={() => onToggle(opt.value)}
                        leftSection={checked ? <IconCheck size={14} className="text-indigo-600" /> : <span style={{ display: 'inline-block', width: 14 }} />}
                    >
                        {opt.label}
                    </Menu.Item>
                )
            })}
        </Menu.Dropdown>
    </Menu>
)


const Bookings = ({ isCustomer = false }: { isCustomer?: boolean }) => {
    const [bookings, setBookings] = useState<Booking[]>([])
    const [tutors, setTutors] = useState<Tutor[]>([])
    const [eventTypes, setEventTypes] = useState<EventType[]>([])
    const [activeTab, setActiveTab] = useState<BookingsTab>('timeline')
    const [timeScope, setTimeScope] = useState<TimeScope>('upcoming')
    const [filters, setFilters] = useState<BookingFilters>({ tutorIds: [], eventTypeIds: [], dateFrom: null, dateTo: null, searchQuery: '', pendingOnly: false })
    const [filtersOpen, setFiltersOpen] = useState(false)
    const [expandedId, setExpandedId] = useState<string | null>(null)
    const [cancellingSeriesId, setCancellingSeriesId] = useState<string | null>(null)
    const [isCancelling, setIsCancelling] = useState(false)
    const [isSubmitting, setIsSubmitting] = useState(false)
    const [processingRequest, setProcessingRequest] = useState<Booking | null>(null)
    const [loadErrors, setLoadErrors] = useState<LoadErrors>({})
    const [isLoading, setIsLoading] = useState(false)
    const [email, setEmail] = useState(() => isCustomer ? (sessionStorage.getItem('customer_email') ?? '') : '')
    const [submitted, setSubmitted] = useState(() => isCustomer ? !!sessionStorage.getItem('customer_email') : false)
    const { toast, showToast } = useToast()

    //pagination state
    const [isLoadingMore, setIsLoadingMore] = useState(false)
    const [page, setPage] = useState(1)
    const [hasMore, setHasMore] = useState(false)

    // recurring tab — separate state, separate shape (BookingSeries, not Booking), unpaginated
    const [seriesList, setSeriesList] = useState<BookingSeries[]>([])
    const [isLoadingSeries, setIsLoadingSeries] = useState(false)

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

    const loadData = async (options: { emailFilter?: string; tab?: 'timeline' | 'requests'; timeScope?: TimeScope; pageNum?: number; append?: boolean } = {}) => {
        const { emailFilter, tab = (activeTab === 'requests' ? 'requests' : 'timeline'), timeScope: scope = timeScope, pageNum = 1, append = false } = options
        if (append) setIsLoadingMore(true)
        else setIsLoading(true)
        try {
            const [bookingsRes, tutorsRes, eventTypesRes] = await Promise.all([
                fetchMyBookings(tab, scope, pageNum, emailFilter),
                fetch(`${import.meta.env.VITE_API_URL}/tutors`),
                fetch(`${import.meta.env.VITE_API_URL}/event_types`),
            ])
            if (!bookingsRes.ok) {
                setLoadErrors(prev => ({ ...prev, bookings: extractError(bookingsRes.error, 'Failed to load bookings.') }))
                return
            }
            if (!tutorsRes.ok) {
                const err = await tutorsRes.json()
                setLoadErrors(prev => ({ ...prev, tutors: extractError(err, 'Failed to load tutors.') }))
                return
            }
            if (!eventTypesRes.ok) {
                const err = await eventTypesRes.json()
                setLoadErrors(prev => ({ ...prev, eventTypes: extractError(err, 'Failed to load event types.') }))
                return
            }
            setBookings(prev => append ? [...prev, ...bookingsRes.items] : bookingsRes.items)
            setTutors(await tutorsRes.json())
            setEventTypes(await eventTypesRes.json())
            setPage(pageNum)
            setHasMore(bookingsRes.hasMore)
            setLoadErrors({})
        } catch (error) {
            console.error(error)
            setLoadErrors(prev => ({ ...prev, bookings: 'An unknown error occurred while loading bookings.' }))
        } finally {
            setIsLoading(false)
            setIsLoadingMore(false)
        }
    }

    const loadSeries = async (emailFilter?: string) => {
        setIsLoadingSeries(true)
        try {
            const query = emailFilter ? `?email=${encodeURIComponent(emailFilter)}` : ''
            const [seriesRes, tutorsRes, eventTypesRes] = await Promise.all([
                fetch(`${import.meta.env.VITE_API_URL}/bookings/booking-series${query}`),
                fetch(`${import.meta.env.VITE_API_URL}/tutors`),
                fetch(`${import.meta.env.VITE_API_URL}/event_types`),
            ])
            if (!seriesRes.ok) {
                const err = await seriesRes.json()
                setLoadErrors(prev => ({ ...prev, bookings: extractError(err, 'Failed to load series.') }))
                return
            }
            if (!tutorsRes.ok) {
                const err = await tutorsRes.json()
                setLoadErrors(prev => ({ ...prev, tutors: extractError(err, 'Failed to load tutors.') }))
                return
            }
            if (!eventTypesRes.ok) {
                const err = await eventTypesRes.json()
                setLoadErrors(prev => ({ ...prev, eventTypes: extractError(err, 'Failed to load event types.') }))
                return
            }
            setSeriesList(await seriesRes.json())
            setTutors(await tutorsRes.json())
            setEventTypes(await eventTypesRes.json())
            setLoadErrors({})
        } catch (error) {
            console.error(error)
            setLoadErrors(prev => ({ ...prev, bookings: 'An unknown error occurred while loading series.' }))
        } finally {
            setIsLoadingSeries(false)
        }
    }

    useEffect(() => {
        if (!isCustomer) { loadData({ tab: 'timeline' }); return }
        const saved = sessionStorage.getItem('customer_email')
        if (saved) loadData({ emailFilter: saved, tab: 'timeline' })
    }, [])

    const refresh = () => {
        if (activeTab === 'recurring') loadSeries(isCustomer ? email : undefined)
        else loadData({ emailFilter: isCustomer ? email : undefined, tab: activeTab === 'requests' ? 'requests' : 'timeline' })
    }

    const handleTabChange = (tab: BookingsTab) => {
        setActiveTab(tab)
        if (tab === 'recurring') loadSeries(isCustomer ? email : undefined)
        else loadData({ emailFilter: isCustomer ? email : undefined, tab })
    }

    const handleTimeScopeChange = (scope: TimeScope) => {
        setTimeScope(scope)
        loadData({ emailFilter: isCustomer ? email : undefined, tab: 'timeline', timeScope: scope })
    }

    const handleLoadMore = () => loadData({ emailFilter: isCustomer ? email : undefined, tab: 'timeline', timeScope, pageNum: page + 1, append: true })

    const handleEmailSubmit = () => {
        if (!email.trim()) return
        sessionStorage.setItem('customer_email', email.trim())
        setSubmitted(true)
        loadData({ emailFilter: email.trim(), tab: 'timeline' })
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

    const activeFilterCount = [
        filters.tutorIds.length > 0,
        filters.eventTypeIds.length > 0,
        filters.dateFrom !== null,
        filters.dateTo !== null,
        filters.pendingOnly,
    ].filter(Boolean).length

    const displayed = bookings
        .filter(b => filters.tutorIds.length === 0 || filters.tutorIds.includes(String(b.tutor_id)))
        .filter(b => filters.eventTypeIds.length === 0 || filters.eventTypeIds.includes(String(b.event_type_id)))
        .filter(b => !filters.dateFrom || b.start.slice(0, 10) >= filters.dateFrom)
        .filter(b => !filters.dateTo || b.start.slice(0, 10) <= filters.dateTo)
        .filter(b => getSearchString(b).includes(filters.searchQuery.trim().toLowerCase()))
        .filter(b => !filters.pendingOnly || b.request?.status === 'pending')
        // Past is reverse-chronological (most recent first) — matches the backend's own
        // ORDER BY start DESC for that scope. Without this, appending an older "Load more"
        // page and re-sorting ascending would put older items above newer ones, backwards.
        .sort((a, b) => timeScope === 'past' ? b.start.localeCompare(a.start) : a.start.localeCompare(b.start))

    if (isCustomer && !submitted) {
        return (
            <div className="min-h-screen bg-white flex flex-col">
                {/* brand bar */}
                <div className="border-b border-gray-100 px-8 h-14 flex items-center shrink-0">
                    <div className="flex items-center gap-2">
                        <div className="w-6 h-6 rounded-md bg-indigo-600 flex items-center justify-center">
                            <span className="text-white text-[10px] font-bold leading-none">T</span>
                        </div>
                        <span className="text-sm font-semibold text-gray-900">TMS</span>
                    </div>
                </div>

                {/* centered form */}
                <div className="flex-1 flex items-center justify-center px-6">
                    <div className="w-full max-w-sm py-12">
                        <div className="w-11 h-11 rounded-xl bg-indigo-50 flex items-center justify-center mb-6">
                            <IconCalendar size={22} className="text-indigo-500" />
                        </div>
                        <h1 className="text-2xl font-bold text-gray-900 mb-1">My bookings</h1>
                        <p className="text-gray-400 text-sm mb-8">Enter the email you used when booking to see your upcoming and past sessions.</p>
                        <div className="flex flex-col gap-3">
                            <TextInput
                                placeholder="your@email.com"
                                value={email}
                                onChange={e => setEmail(e.target.value)}
                                onKeyDown={e => e.key === 'Enter' && handleEmailSubmit()}
                                size="md"
                            />
                            {loadErrors.bookings && <p className="text-sm text-red-500">{loadErrors.bookings}</p>}
                            <Button onClick={handleEmailSubmit} disabled={!email.trim()} size="md" loading={isLoading}>
                                View my bookings
                            </Button>
                        </div>
                    </div>
                </div>
            </div>
        )
    }

    if (isCustomer) {
        return (
            <div className="min-h-screen bg-white flex flex-col">
                {/* brand + email bar */}
                <header className="shrink-0 border-b border-gray-100 px-8 h-14 flex items-center justify-between">
                    <div className="flex items-center gap-2">
                        <div className="w-6 h-6 rounded-md bg-indigo-600 flex items-center justify-center">
                            <span className="text-white text-[10px] font-bold leading-none">T</span>
                        </div>
                        <span className="text-sm font-semibold text-gray-900">TMS</span>
                    </div>
                    <div className="flex items-center gap-4 text-sm">
                        <span className="text-gray-400 hidden sm:block">{email}</span>
                        <button
                            onClick={() => { sessionStorage.removeItem('customer_email'); setSubmitted(false); setBookings([]) }}
                            className="text-indigo-500 hover:text-indigo-700 font-medium transition-colors"
                        >
                            Switch email
                        </button>
                    </div>
                </header>

                {/* content */}
                <div className="flex-1 max-w-2xl mx-auto w-full px-6 py-10">
                    {/* page header */}
                    <div className="mb-8">
                        <h1 className="text-2xl font-bold text-gray-900">My bookings</h1>
                        <p className="text-sm text-gray-400 mt-1">{email}</p>
                    </div>

                    {loadErrors.bookings && <p className="text-sm text-red-500 mb-4">{loadErrors.bookings}</p>}

                    {/* tabs */}
                    <div className="flex gap-1 bg-gray-100 rounded-lg p-1 w-fit mb-6">
                        {TABS.filter(t => t.key !== 'requests').map(tab => (
                            <button
                                key={tab.key}
                                onClick={() => handleTabChange(tab.key)}
                                className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
                                    activeTab === tab.key
                                        ? 'bg-white text-gray-800 shadow-sm'
                                        : 'text-gray-500 hover:text-gray-700'
                                }`}
                            >
                                {tab.label}
                            </button>
                        ))}
                    </div>

                    {activeTab === 'recurring' ? (
                        <>
                            {isLoadingSeries && <div className="flex justify-center py-16"><Loader size="sm" /></div>}
                            {!isLoadingSeries && (
                                <div className="flex flex-col">
                                    {seriesList.map(series => (
                                        <SeriesRow
                                            key={series.id}
                                            series={series}
                                            tutor={tutors.find(t => t.id === series.tutor_id)!}
                                            eventType={eventTypes.find(e => e.id === series.event_type_id)!}
                                            onRefresh={msg => { refresh(); showToast(msg) }}
                                            onError={msg => showToast(msg, 'error')}
                                            onCancelSeries={() => {}}
                                            isCustomer={true}
                                        />
                                    ))}
                                    {seriesList.length === 0 && (
                                        <div className="text-center py-16">
                                            <div className="w-12 h-12 rounded-xl bg-gray-100 flex items-center justify-center mx-auto mb-4">
                                                <IconCalendar size={22} className="text-gray-400" />
                                            </div>
                                            <p className="text-sm font-medium text-gray-500">No recurring series</p>
                                        </div>
                                    )}
                                </div>
                            )}
                        </>
                    ) : (
                        <>
                            {/* time-scope toggle — inner axis, only relevant to the timeline view */}
                            <div className="flex gap-1 bg-gray-100 rounded-lg p-1 w-fit mb-4">
                                {(['upcoming', 'past'] as const).map(scope => (
                                    <button
                                        key={scope}
                                        onClick={() => handleTimeScopeChange(scope)}
                                        className={`px-3 py-1 rounded-md text-xs font-medium transition-colors ${
                                            timeScope === scope
                                                ? 'bg-white text-gray-800 shadow-sm'
                                                : 'text-gray-500 hover:text-gray-700'
                                        }`}
                                    >
                                        {scope === 'upcoming' ? 'Upcoming' : 'Past'}
                                    </button>
                                ))}
                            </div>

                            {isLoading && <div className="flex justify-center py-16"><Loader size="sm" /></div>}

                            {!isLoading && (
                                <div className="flex flex-col">
                                    {displayed.map((b, i) => {
                                        const isNewDate = i === 0 || dateKeyOf(displayed[i - 1].start) !== dateKeyOf(b.start)
                                        const gapMinutes = !isNewDate ? (timeScope === 'past' ? gapMinutesBetween(b.end, displayed[i - 1].start) : gapMinutesBetween(displayed[i - 1].end, b.start)) : 0
                                        const showGap = !isNewDate && gapMinutes > 30
                                        return (
                                            <div key={b.id} className={isNewDate ? 'mt-5 first:mt-0' : 'mt-1.5'}>
                                                {isNewDate && (
                                                    <div className="flex justify-center mb-2">
                                                        <span className="text-[11px] text-gray-400 font-medium tracking-wide">{dateLabelOf(b.start)}</span>
                                                    </div>
                                                )}
                                                {showGap && (
                                                    <div className="flex justify-center my-1">
                                                        <span className="text-[10px] text-gray-400 font-medium tracking-wide">···· {formatGap(gapMinutes)} gap ····</span>
                                                    </div>
                                                )}
                                                <BookingRow
                                                    booking={b}
                                                    tutor={tutors.find(t => t.id === b.tutor_id)!}
                                                    eventType={eventTypes.find(e => e.id === b.event_type_id)!}
                                                    expanded={expandedId === b.id}
                                                    onExpand={() => setExpandedId(expandedId === b.id ? null : b.id)}
                                                    onRefresh={msg => { refresh(); showToast(msg) }}
                                                    onError={msg => showToast(msg, 'error')}
                                                    isCustomer={true}
                                                />
                                            </div>
                                        )
                                    })}

                                    {displayed.length === 0 && (
                                        <div className="text-center py-16">
                                            <div className="w-12 h-12 rounded-xl bg-gray-100 flex items-center justify-center mx-auto mb-4">
                                                <IconCalendar size={22} className="text-gray-400" />
                                            </div>
                                            <p className="text-sm font-medium text-gray-500">No bookings found</p>
                                            <p className="text-xs text-gray-400 mt-1">
                                                {timeScope === 'upcoming' ? 'No upcoming sessions.' : 'No past sessions.'}
                                            </p>
                                        </div>
                                    )}

                                    {hasMore && (
                                        <div className="flex justify-center pt-2">
                                            <Button variant="default" size="sm" loading={isLoadingMore} onClick={handleLoadMore}>
                                                Load more
                                            </Button>
                                        </div>
                                    )}
                                </div>
                            )}
                        </>
                    )}
                </div>
                <Toast toast={toast} />
            </div>
        )
    }

    return (
        <div>
            {/* load errors */}
            {loadErrors.bookings && <p className="text-sm text-red-500 mb-2">{loadErrors.bookings}</p>}
            {loadErrors.tutors && <p className="text-sm text-red-500 mb-2">{loadErrors.tutors}</p>}
            {loadErrors.eventTypes && <p className="text-sm text-red-500 mb-4">{loadErrors.eventTypes}</p>}

            {/* header */}
            <div className="flex items-center justify-between mb-6">
                <div className="flex items-center gap-3">
                    <h1 className="text-xl font-semibold text-gray-800">Bookings</h1>
                    {!isLoading && !isLoadingSeries && (
                        <span className="text-xs bg-gray-100 text-gray-500 px-2 py-0.5 rounded-full font-medium">
                            {activeTab === 'recurring' ? seriesList.length : displayed.length}
                        </span>
                    )}
                </div>
            </div>

            {/* tabs */}
            <div className="flex gap-1 bg-gray-100 rounded-lg p-1 w-fit mb-5">
                {TABS.filter(t => !isCustomer || t.key !== 'requests').map(tab => (
                    <button
                        key={tab.key}
                        onClick={() => handleTabChange(tab.key)}
                        className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
                            activeTab === tab.key
                                ? 'bg-white text-gray-800 shadow-sm'
                                : 'text-gray-500 hover:text-gray-700'
                        }`}
                    >
                        {tab.label}
                    </button>
                ))}
            </div>

            {/* time-scope toggle — inner axis, only relevant to the timeline view */}
            {activeTab === 'timeline' && (
                <div className="flex gap-1 bg-gray-100 rounded-lg p-1 w-fit mb-4">
                    {(['upcoming', 'past'] as const).map(scope => (
                        <button
                            key={scope}
                            onClick={() => handleTimeScopeChange(scope)}
                            className={`px-3 py-1 rounded-md text-xs font-medium transition-colors ${
                                timeScope === scope
                                    ? 'bg-white text-gray-800 shadow-sm'
                                    : 'text-gray-500 hover:text-gray-700'
                            }`}
                        >
                            {scope === 'upcoming' ? 'Upcoming' : 'Past'}
                        </button>
                    ))}
                </div>
            )}

            {/* filters */}
            {activeTab !== 'requests' && activeTab !== 'recurring' && !isCustomer && (
                <div className="mb-5">
                    <div className="flex flex-wrap gap-2">
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
                        <Popover opened={filtersOpen} onChange={setFiltersOpen} position="bottom-start" shadow="md" width={300}>
                            <Popover.Target>
                                <Button
                                    variant="default"
                                    size="sm"
                                    leftSection={<IconFilter size={14} />}
                                    onClick={() => setFiltersOpen(o => !o)}
                                    styles={{ root: { borderRadius: '8px', borderColor: '#e5e7eb' } }}
                                >
                                    Filters{activeFilterCount > 0 ? ` · ${activeFilterCount}` : ''}
                                </Button>
                            </Popover.Target>
                            <Popover.Dropdown>
                                <div className="flex flex-col gap-3">
                                    <CheckDropdown
                                        label="Tutors"
                                        options={tutors.map(t => ({ value: String(t.id), label: `${t.first_name} ${t.last_name}` }))}
                                        selected={filters.tutorIds}
                                        onToggle={id => setFilters(f => ({
                                            ...f,
                                            tutorIds: f.tutorIds.includes(id) ? f.tutorIds.filter(x => x !== id) : [...f.tutorIds, id],
                                        }))}
                                    />
                                    <CheckDropdown
                                        label="Event types"
                                        options={eventTypes.map(e => ({ value: String(e.id), label: e.name }))}
                                        selected={filters.eventTypeIds}
                                        onToggle={id => setFilters(f => ({
                                            ...f,
                                            eventTypeIds: f.eventTypeIds.includes(id) ? f.eventTypeIds.filter(x => x !== id) : [...f.eventTypeIds, id],
                                        }))}
                                    />
                                    <div className="flex gap-2 items-center">
                                        <Input
                                            type="date"
                                            size="sm"
                                            value={filters.dateFrom ?? ''}
                                            onChange={(e) => {
                                                const value = e.target.value || null
                                                setFilters(f => ({ ...f, dateFrom: value }))
                                            }}
                                            styles={{ input: { borderRadius: '8px', fontFamily: 'inherit', borderColor: '#e5e7eb' } }}
                                        />
                                        <span className="text-gray-400 text-sm shrink-0">to</span>
                                        <Input
                                            type="date"
                                            size="sm"
                                            value={filters.dateTo ?? ''}
                                            onChange={(e) => {
                                                const value = e.target.value || null
                                                setFilters(f => ({ ...f, dateTo: value }))
                                            }}
                                            styles={{ input: { borderRadius: '8px', fontFamily: 'inherit', borderColor: '#e5e7eb' } }}
                                        />
                                    </div>
                                    <Switch
                                        label="Pending requests only"
                                        checked={filters.pendingOnly}
                                        onChange={(e) => {
                                            const checked = e.currentTarget.checked
                                            setFilters(f => ({ ...f, pendingOnly: checked }))
                                        }}
                                        size="sm"
                                    />
                                </div>
                            </Popover.Dropdown>
                        </Popover>
                    </div>

                    {activeFilterCount > 0 && (
                        <div className="flex flex-wrap gap-2 mt-3">
                            {filters.tutorIds.map(id => (
                                <FilterChip
                                    key={`tutor-${id}`}
                                    label={`Tutor: ${tutors.find(t => String(t.id) === id)?.first_name ?? ''}`}
                                    onRemove={() => setFilters(f => ({ ...f, tutorIds: f.tutorIds.filter(x => x !== id) }))}
                                />
                            ))}
                            {filters.eventTypeIds.map(id => (
                                <FilterChip
                                    key={`event-${id}`}
                                    label={`Event: ${eventTypes.find(e => String(e.id) === id)?.name ?? ''}`}
                                    onRemove={() => setFilters(f => ({ ...f, eventTypeIds: f.eventTypeIds.filter(x => x !== id) }))}
                                />
                            ))}
                            {filters.dateFrom && (
                                <FilterChip label={`From: ${filters.dateFrom}`} onRemove={() => setFilters(f => ({ ...f, dateFrom: null }))} />
                            )}
                            {filters.dateTo && (
                                <FilterChip label={`To: ${filters.dateTo}`} onRemove={() => setFilters(f => ({ ...f, dateTo: null }))} />
                            )}
                            {filters.pendingOnly && (
                                <FilterChip label="Pending only" onRemove={() => setFilters(f => ({ ...f, pendingOnly: false }))} />
                            )}
                        </div>
                    )}
                </div>
            )}

            {/* spinner */}
            {(activeTab === 'recurring' ? isLoadingSeries : isLoading) && <div className="flex justify-center py-12"><Loader size="sm" /></div>}

            {/* requests tab */}
            {!isLoading && activeTab === 'requests' && (
                <div className="flex flex-col gap-3">
                    {bookings.map(b => {
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
                    {bookings.length === 0 && (
                        <p className="text-sm text-gray-400 text-center py-12">No pending requests.</p>
                    )}
                </div>
            )}

            {/* recurring tab */}
            {!isLoadingSeries && activeTab === 'recurring' && (
                <div className="flex flex-col">
                    {seriesList.map(series => (
                        <SeriesRow
                            key={series.id}
                            series={series}
                            tutor={tutors.find(t => t.id === series.tutor_id)!}
                            eventType={eventTypes.find(e => e.id === series.event_type_id)!}
                            onRefresh={msg => { refresh(); showToast(msg) }}
                            onError={msg => showToast(msg, 'error')}
                            onCancelSeries={setCancellingSeriesId}
                            isCustomer={isCustomer}
                        />
                    ))}
                    {seriesList.length === 0 && (
                        <p className="text-sm text-gray-400 text-center py-12">No recurring series.</p>
                    )}
                </div>
            )}

            {/* booking list */}
            {!isLoading && activeTab !== 'requests' && activeTab !== 'recurring' && (
                <div className="flex flex-col">
                    {displayed.map((b, i) => {
                        const isNewDate = i === 0 || dateKeyOf(displayed[i - 1].start) !== dateKeyOf(b.start)
                        const gapMinutes = !isNewDate ? (timeScope === 'past' ? gapMinutesBetween(b.end, displayed[i - 1].start) : gapMinutesBetween(displayed[i - 1].end, b.start)) : 0
                        const showGap = !isNewDate && gapMinutes > 30
                        return (
                            <div key={b.id} className={isNewDate ? 'mt-5 first:mt-0' : 'mt-1.5'}>
                                {isNewDate && (
                                    <div className="flex justify-center mb-2">
                                        <span className="text-[11px] text-gray-400 font-medium tracking-wide">{dateLabelOf(b.start)}</span>
                                    </div>
                                )}
                                {showGap && (
                                    <div className="flex justify-center my-1">
                                        <span className="text-[10px] text-gray-400 font-medium tracking-wide">···· {formatGap(gapMinutes)} gap ····</span>
                                    </div>
                                )}
                                <BookingRow
                                    booking={b}
                                    tutor={tutors.find(t => t.id === b.tutor_id)!}
                                    eventType={eventTypes.find(e => e.id === b.event_type_id)!}
                                    expanded={expandedId === b.id}
                                    onExpand={() => setExpandedId(expandedId === b.id ? null : b.id)}
                                    onRefresh={msg => { refresh(); showToast(msg) }}
                                    onError={msg => showToast(msg, 'error')}
                                    onReviewRequest={setProcessingRequest}
                                    isCustomer={isCustomer}
                                />
                            </div>
                        )
                    })}

                    {displayed.length === 0 && (
                        <p className="text-sm text-gray-400 text-center py-12">No bookings found.</p>
                    )}
                </div>
            )}

            {!isLoading && activeTab !== 'recurring' && hasMore && (
                <div className="flex justify-center pt-4">
                    <Button variant="default" size="sm" loading={isLoadingMore} onClick={handleLoadMore}>
                        Load more
                    </Button>
                </div>
            )}

            {/* approve/deny request modal — admin only */}
            {!isCustomer && <Modal
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
            </Modal>}

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

            <Toast toast={toast} />
        </div>
    )
}

export default Bookings
