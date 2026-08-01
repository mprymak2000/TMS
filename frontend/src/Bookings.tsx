import { useState, useEffect } from 'react'
import { Select, TextInput, Loader, Input, Button, Modal, Menu } from '@mantine/core'
import { IconSearch, IconChevronDown, IconChevronUp, IconDotsVertical, IconBan, IconCalendarStats, IconRepeat, IconCalendar } from '@tabler/icons-react'
import type { Tutor, EventType, Booking } from './types'
import { extractError, formatDate, formatTime, tutorBubbleClass, tutorInitials } from './utils'
import { useToast } from './useToast'
import { useNavigate } from 'react-router-dom'
import BookingRow from './BookingRow'
import Toast from './Toast'

interface BookingFilters {
    tutorId: string | null
    eventTypeId: string | null
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

type SeriesItem = { kind: 'series'; seriesId: number; bookings: Booking[]; sortKey: string }
type StandaloneItem = { kind: 'standalone'; dateLabel: string; bookings: Booking[]; sortKey: string }
type DisplayItem = SeriesItem | StandaloneItem

const TABS = [
    { key: 'upcoming', label: 'Upcoming' },
    { key: 'past', label: 'Past' },
    { key: 'all', label: 'All' },
    { key: 'requests', label: 'Requests' },
] as const

const Bookings = ({ isCustomer = false }: { isCustomer?: boolean }) => {
    const navigate = useNavigate()
    const [bookings, setBookings] = useState<Booking[]>([])
    const [tutors, setTutors] = useState<Tutor[]>([])
    const [eventTypes, setEventTypes] = useState<EventType[]>([])
    const [activeTab, setActiveTab] = useState<'upcoming' | 'past' | 'all' | 'requests'>('upcoming')
    const [filters, setFilters] = useState<BookingFilters>({ tutorId: null, eventTypeId: null, dateFrom: null, dateTo: null, searchQuery: '', pendingOnly: false })
    const [expandedId, setExpandedId] = useState<number | null>(null)
    const [expandedSeries, setExpandedSeries] = useState<Set<number>>(new Set())
    const [cancellingSeriesId, setCancellingSeriesId] = useState<number | null>(null)
    const [isCancelling, setIsCancelling] = useState(false)
    const [isSubmitting, setIsSubmitting] = useState(false)
    const [processingRequest, setProcessingRequest] = useState<Booking | null>(null)
    const [loadErrors, setLoadErrors] = useState<LoadErrors>({})
    const [isLoading, setIsLoading] = useState(false)
    const [email, setEmail] = useState(() => isCustomer ? (sessionStorage.getItem('customer_email') ?? '') : '')
    const [submitted, setSubmitted] = useState(() => isCustomer ? !!sessionStorage.getItem('customer_email') : false)
    const { toast, showToast } = useToast()

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

    const loadData = async (emailFilter?: string) => {
        setIsLoading(true)
        try {
            const bookingsUrl = emailFilter
                ? `${import.meta.env.VITE_API_URL}/bookings?email=${encodeURIComponent(emailFilter)}`
                : `${import.meta.env.VITE_API_URL}/bookings`
            const [bookingsRes, tutorsRes, eventTypesRes] = await Promise.all([
                fetch(bookingsUrl),
                fetch(`${import.meta.env.VITE_API_URL}/tutors`),
                fetch(`${import.meta.env.VITE_API_URL}/event_types`),
            ])
            if (!bookingsRes.ok) {
                const err = await bookingsRes.json()
                setLoadErrors(prev => ({ ...prev, bookings: extractError(err, 'Failed to load bookings.') }))
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
            setBookings(await bookingsRes.json())
            setTutors(await tutorsRes.json())
            setEventTypes(await eventTypesRes.json())
        } catch (error) {
            console.error(error)
            setLoadErrors(prev => ({ ...prev, bookings: 'An unknown error occurred while loading bookings.' }))
        } finally {
            setIsLoading(false)
        }
    }

    useEffect(() => {
        if (!isCustomer) { loadData(); return }
        const saved = sessionStorage.getItem('customer_email')
        if (saved) loadData(saved)
    }, [])

    const handleEmailSubmit = () => {
        if (!email.trim()) return
        sessionStorage.setItem('customer_email', email.trim())
        setSubmitted(true)
        loadData(email.trim())
    }

    const handleCancelSeries = async (seriesId: number) => {
        setIsCancelling(true)
        try {
            const res = await fetch(`${import.meta.env.VITE_API_URL}/bookings/booking-series/${seriesId}`, { method: 'DELETE' })
            if (!res.ok) {
                showToast(extractError(await res.json(), 'Failed to cancel series.'), 'error')
                setCancellingSeriesId(null)
                return
            }
            setCancellingSeriesId(null)
            loadData()
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
            loadData()
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
            loadData()
            showToast('Request denied')
        } catch (error) {
            console.error(error)
            showToast('Failed to deny request.', 'error')
        } finally {
            setIsSubmitting(false)
        }
    }

    const displayed = bookings
        .filter(b => !filters.tutorId || b.tutor_id === Number(filters.tutorId))
        .filter(b => !filters.eventTypeId || b.event_type_id === Number(filters.eventTypeId))
        .filter(b => !filters.dateFrom || b.start.slice(0, 10) >= filters.dateFrom)
        .filter(b => !filters.dateTo || b.start.slice(0, 10) <= filters.dateTo)
        .filter(b => getSearchString(b).includes(filters.searchQuery.trim().toLowerCase()))
        .filter(b => !filters.pendingOnly || b.request?.status === 'pending')
        .filter(b => activeTab === 'all' || activeTab === 'requests' || (activeTab === 'upcoming'
            ? b.start > new Date().toISOString()
            : b.start <= new Date().toISOString()))
        .sort((a, b) => a.start.localeCompare(b.start))

    const pendingBookings = bookings.filter(b => b.request?.status === 'pending')

    // Group recurring bookings by series; standalones into date groups
    const seriesMap = new Map<number, Booking[]>()
    const standaloneList: Booking[] = []
    for (const b of displayed) {
        if (b.series_id !== null) {
            const arr = seriesMap.get(b.series_id) ?? []
            arr.push(b)
            seriesMap.set(b.series_id, arr)
        } else {
            standaloneList.push(b)
        }
    }

    const seriesItems: DisplayItem[] = [...seriesMap.entries()].map(([seriesId, bookings]) => ({
        kind: 'series', seriesId, bookings, sortKey: bookings[0].start,
    }))

    const standaloneGrouped = standaloneList.reduce<{ dateLabel: string; bookings: Booking[] }[]>((acc, b) => {
        const label = new Date(b.start).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })
        const last = acc[acc.length - 1]
        if (last?.dateLabel === label) last.bookings.push(b)
        else acc.push({ dateLabel: label, bookings: [b] })
        return acc
    }, [])

    const displayItems: DisplayItem[] = [
        ...seriesItems,
        ...standaloneGrouped.map(g => ({ kind: 'standalone' as const, dateLabel: g.dateLabel, bookings: g.bookings, sortKey: g.bookings[0].start })),
    ].sort((a, b) => a.sortKey.localeCompare(b.sortKey))

    const toggleSeriesExpand = (seriesId: number) =>
        setExpandedSeries(prev => {
            const next = new Set(prev)
            if (next.has(seriesId)) next.delete(seriesId)
            else next.add(seriesId)
            return next
        })

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
                                onClick={() => setActiveTab(tab.key)}
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

                    {isLoading && <div className="flex justify-center py-16"><Loader size="sm" /></div>}

                    {!isLoading && (
                        <div className="flex flex-col gap-4">
                            {displayItems.map(item => {
                                if (item.kind === 'series') {
                                    const rep = item.bookings[0]
                                    const tutor = tutors.find(t => t.id === rep.tutor_id)!
                                    const eventType = eventTypes.find(e => e.id === rep.event_type_id)!
                                    const isExpanded = expandedSeries.has(item.seriesId)
                                    const firstStart = new Date(rep.start)
                                    const dayName = firstStart.toLocaleDateString('en-US', { weekday: 'long' })
                                    const timeStr = firstStart.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
                                    const lastStart = new Date(item.bookings[item.bookings.length - 1].start)
                                    const dateRange = `${firstStart.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })} – ${lastStart.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}`
                                    const now = new Date()
                                    const nextBooking = item.bookings.find(b => b.status === 'confirmed' && new Date(b.start) >= now)
                                    const nextDateStr = nextBooking
                                        ? new Date(nextBooking.start).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
                                        : null

                                    return (
                                        <div key={`series-${item.seriesId}`} className="bg-white rounded-xl shadow-sm border border-gray-200 border-l-4 border-l-indigo-400 overflow-hidden">
                                            <div
                                                className="flex items-center px-5 py-4 gap-4 cursor-pointer hover:bg-gray-50 transition-colors"
                                                onClick={() => toggleSeriesExpand(item.seriesId)}
                                            >
                                                <div className="w-8 h-8 rounded-lg bg-indigo-50 flex items-center justify-center shrink-0">
                                                    <IconRepeat size={16} className="text-indigo-500" />
                                                </div>
                                                {tutor && (
                                                    <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold shrink-0 ${tutorBubbleClass(tutor)}`}>
                                                        {tutorInitials(tutor)}
                                                    </div>
                                                )}
                                                <div className="flex-1 min-w-0">
                                                    <div className="flex items-center gap-2 mb-0.5">
                                                        <span className="font-medium text-gray-800 truncate">{rep.student_first} {rep.student_last}</span>
                                                        <span className="shrink-0 text-xs bg-indigo-50 text-indigo-600 px-2 py-0.5 rounded-full font-medium">{eventType?.name}</span>
                                                    </div>
                                                    <p className="text-xs text-gray-400">{dayName}s · {timeStr} · {item.bookings.length} sessions · {dateRange}</p>
                                                </div>
                                                {nextDateStr ? (
                                                    <div className="text-right shrink-0">
                                                        <div className="text-[10px] text-gray-400 uppercase tracking-wide">Next</div>
                                                        <div className="text-sm font-medium text-gray-700">{nextDateStr}</div>
                                                    </div>
                                                ) : (
                                                    <span className="text-xs text-gray-400 shrink-0">No upcoming</span>
                                                )}
                                                <div className="flex items-center gap-0.5 shrink-0" onClick={e => e.stopPropagation()}>
                                                    {rep.series_manage_token && (
                                                        <button
                                                            onClick={() => navigate(`/manage-series/${rep.series_manage_token}`)}
                                                            className="text-xs text-indigo-500 hover:text-indigo-700 px-2 py-1 rounded-lg hover:bg-indigo-50 transition-colors"
                                                        >
                                                            Manage
                                                        </button>
                                                    )}
                                                    <span className="flex items-center justify-center w-7 h-7 text-gray-400 pointer-events-none">
                                                        {isExpanded ? <IconChevronUp size={16} /> : <IconChevronDown size={16} />}
                                                    </span>
                                                </div>
                                            </div>
                                            {isExpanded && (
                                                <div className="divide-y divide-gray-100 border-t border-gray-100">
                                                    {item.bookings.map(b => (
                                                        <BookingRow
                                                            key={b.id}
                                                            booking={b}
                                                            tutor={tutors.find(t => t.id === b.tutor_id)!}
                                                            eventType={eventTypes.find(e => e.id === b.event_type_id)!}
                                                            expanded={expandedId === b.id}
                                                            onExpand={() => setExpandedId(expandedId === b.id ? null : b.id)}
                                                            onRefresh={msg => { loadData(); showToast(msg) }}
                                                            onError={msg => showToast(msg, 'error')}
                                                            isCustomer={true}
                                                            compact={true}
                                                        />
                                                    ))}
                                                </div>
                                            )}
                                        </div>
                                    )
                                }

                                return (
                                    <div key={`standalone-${item.dateLabel}`}>
                                        <div className="flex items-center gap-3 mb-2.5">
                                            <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider whitespace-nowrap">{item.dateLabel}</span>
                                            <div className="flex-1 border-t border-gray-100" />
                                        </div>
                                        <div className="flex flex-col gap-2.5">
                                            {item.bookings.map(b => (
                                                <BookingRow
                                                    key={b.id}
                                                    booking={b}
                                                    tutor={tutors.find(t => t.id === b.tutor_id)!}
                                                    eventType={eventTypes.find(e => e.id === b.event_type_id)!}
                                                    expanded={expandedId === b.id}
                                                    onExpand={() => setExpandedId(expandedId === b.id ? null : b.id)}
                                                    onRefresh={msg => { loadData(); showToast(msg) }}
                                                    onError={msg => showToast(msg, 'error')}
                                                    isCustomer={true}
                                                />
                                            ))}
                                        </div>
                                    </div>
                                )
                            })}

                            {displayItems.length === 0 && (
                                <div className="text-center py-16">
                                    <div className="w-12 h-12 rounded-xl bg-gray-100 flex items-center justify-center mx-auto mb-4">
                                        <IconCalendar size={22} className="text-gray-400" />
                                    </div>
                                    <p className="text-sm font-medium text-gray-500">No bookings found</p>
                                    <p className="text-xs text-gray-400 mt-1">
                                        {activeTab === 'upcoming' ? 'No upcoming sessions.' : activeTab === 'past' ? 'No past sessions.' : 'No sessions found.'}
                                    </p>
                                </div>
                            )}
                        </div>
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
                    {!isLoading && (
                        <span className="text-xs bg-gray-100 text-gray-500 px-2 py-0.5 rounded-full font-medium">
                            {displayed.length}
                        </span>
                    )}
                </div>
            </div>

            {/* tabs */}
            <div className="flex gap-1 bg-gray-100 rounded-lg p-1 w-fit mb-5">
                {TABS.filter(t => !isCustomer || t.key !== 'requests').map(tab => (
                    <button
                        key={tab.key}
                        onClick={() => setActiveTab(tab.key)}
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

            {/* filter bar */}
            {activeTab !== 'requests' && !isCustomer && <div className="flex flex-wrap gap-3 mb-5">
                <TextInput
                    placeholder="Search..."
                    leftSection={<IconSearch size={14} />}
                    value={filters.searchQuery}
                    onChange={e => setFilters(f => ({ ...f, searchQuery: e.target.value }))}
                    styles={{ input: { borderRadius: '8px', fontFamily: 'inherit', borderColor: '#e5e7eb' } }}
                    size="sm"
                />
                <Select
                    placeholder="All tutors"
                    data={tutors.map(t => ({ value: String(t.id), label: `${t.first_name} ${t.last_name}` }))}
                    value={filters.tutorId}
                    onChange={v => setFilters(f => ({ ...f, tutorId: v }))}
                    clearable
                    size="sm"
                    styles={{ input: { borderRadius: '8px', fontFamily: 'inherit', borderColor: '#e5e7eb' } }}
                />
                <Select
                    placeholder="All event types"
                    data={eventTypes.map(e => ({ value: String(e.id), label: e.name }))}
                    value={filters.eventTypeId}
                    onChange={v => setFilters(f => ({ ...f, eventTypeId: v }))}
                    clearable
                    size="sm"
                    styles={{ input: { borderRadius: '8px', fontFamily: 'inherit', borderColor: '#e5e7eb' } }}
                />
                <Input
                    type="date"
                    size="sm"
                    onChange={e => setFilters(f => ({ ...f, dateFrom: e.target.value || null }))}
                    styles={{ input: { borderRadius: '8px', fontFamily: 'inherit', borderColor: '#e5e7eb' } }}
                />
                <span className="text-gray-400 text-sm self-center">to</span>
                <Input
                    type="date"
                    size="sm"
                    onChange={e => setFilters(f => ({ ...f, dateTo: e.target.value || null }))}
                    styles={{ input: { borderRadius: '8px', fontFamily: 'inherit', borderColor: '#e5e7eb' } }}
                />
                <button
                    onClick={() => setFilters(f => ({ ...f, pendingOnly: !f.pendingOnly }))}
                    className={`px-3 py-1.5 rounded-lg text-sm font-medium border transition-colors ${
                        filters.pendingOnly
                            ? 'bg-amber-50 text-amber-700 border-amber-200'
                            : 'bg-white text-gray-500 border-gray-200 hover:border-gray-300'
                    }`}
                >
                    Pending requests
                </button>
            </div>}

            {/* spinner */}
            {isLoading && <div className="flex justify-center py-12"><Loader size="sm" /></div>}

            {/* requests tab */}
            {!isLoading && activeTab === 'requests' && (
                <div className="flex flex-col gap-3">
                    {pendingBookings.map(b => {
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
                    {pendingBookings.length === 0 && (
                        <p className="text-sm text-gray-400 text-center py-12">No pending requests.</p>
                    )}
                </div>
            )}

            {/* booking groups */}
            {!isLoading && activeTab !== 'requests' && (
                <div className="flex flex-col gap-4">
                    {displayItems.map(item => {
                        if (item.kind === 'series') {
                            const rep = item.bookings[0]
                            const tutor = tutors.find(t => t.id === rep.tutor_id)!
                            const eventType = eventTypes.find(e => e.id === rep.event_type_id)!
                            const isExpanded = expandedSeries.has(item.seriesId)
                            const firstStart = new Date(rep.start)
                            const dayName = firstStart.toLocaleDateString('en-US', { weekday: 'long' })
                            const timeStr = firstStart.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
                            const lastStart = new Date(item.bookings[item.bookings.length - 1].start)
                            const dateRange = `${firstStart.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })} – ${lastStart.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}`
                            const now = new Date()
                            const nextBooking = item.bookings.find(b => b.status === 'confirmed' && new Date(b.start) >= now)
                            const nextDateStr = nextBooking
                                ? new Date(nextBooking.start).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
                                : null

                            return (
                                <div key={`series-${item.seriesId}`} className="bg-white rounded-xl shadow-sm border border-gray-200 border-l-4 border-l-indigo-400 overflow-hidden">

                                    {/* series header — full row is clickable */}
                                    <div
                                        className="flex items-center px-5 py-4 gap-4 cursor-pointer hover:bg-gray-50 transition-colors"
                                        onClick={() => toggleSeriesExpand(item.seriesId)}
                                    >
                                        {/* recurring icon */}
                                        <div className="w-8 h-8 rounded-lg bg-indigo-50 flex items-center justify-center shrink-0">
                                            <IconRepeat size={16} className="text-indigo-500" />
                                        </div>

                                        {/* two-line info */}
                                        <div className="flex-1 min-w-0">
                                            <div className="flex items-center gap-2 mb-0.5">
                                                <span className="font-medium text-gray-800 truncate">
                                                    {rep.student_first} {rep.student_last}
                                                </span>
                                                <span className="shrink-0 text-xs bg-indigo-50 text-indigo-600 px-2 py-0.5 rounded-full font-medium">
                                                    {eventType?.name}
                                                </span>
                                            </div>
                                            <p className="text-xs text-gray-400">
                                                {dayName}s · {timeStr} · {item.bookings.length} sessions · {dateRange}
                                            </p>
                                        </div>

                                        {/* tutor bubble */}
                                        <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold shrink-0 ${tutorBubbleClass(tutor)}`}>
                                            {tutor?.first_name[0]}{tutor?.last_name[0]}
                                        </div>

                                        {/* next session */}
                                        <div className="text-right shrink-0 w-24">
                                            {nextDateStr ? (
                                                <>
                                                    <div className="text-[10px] text-gray-400 uppercase tracking-wide">Next</div>
                                                    <div className="text-sm font-medium text-gray-700">{nextDateStr}</div>
                                                </>
                                            ) : (
                                                <span className="text-xs text-gray-400">No upcoming</span>
                                            )}
                                        </div>

                                        {/* actions */}
                                        <div className="flex items-center gap-0.5 shrink-0" onClick={e => e.stopPropagation()}>
                                            {isCustomer ? (
                                                rep.series_manage_token && (
                                                    <button
                                                        onClick={() => navigate(`/manage-series/${rep.series_manage_token}`)}
                                                        className="text-xs text-indigo-500 hover:text-indigo-700 px-2 py-1 rounded-lg hover:bg-indigo-50 transition-colors"
                                                    >
                                                        Manage series
                                                    </button>
                                                )
                                            ) : (
                                                <Menu shadow="md" width={210} position="bottom-end">
                                                    <Menu.Target>
                                                        <button className="flex items-center justify-center w-7 h-7 rounded-md text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors">
                                                            <IconDotsVertical size={16} />
                                                        </button>
                                                    </Menu.Target>
                                                    <Menu.Dropdown>
                                                        <Menu.Item
                                                            leftSection={<IconCalendarStats size={14} />}
                                                            onClick={() => navigate(`/book/${eventType.id}`, {
                                                                state: {
                                                                    rescheduleSeriesId: item.seriesId,
                                                                    tutorId: rep.tutor_id,
                                                                    originalStart: rep.start,
                                                                    originalEnd: rep.end,
                                                                    studentFirst: rep.student_first,
                                                                    studentLast: rep.student_last,
                                                                    studentEmail: rep.student_email,
                                                                    studentPhone: rep.student_phone,
                                                                    parentEmail: rep.parent_email,
                                                                    parentPhone: rep.parent_phone,
                                                                }
                                                            })}
                                                        >
                                                            Change schedule
                                                        </Menu.Item>
                                                        <Menu.Divider />
                                                        <Menu.Item
                                                            leftSection={<IconBan size={14} />}
                                                            color="red"
                                                            onClick={() => setCancellingSeriesId(item.seriesId)}
                                                        >
                                                            Cancel series
                                                        </Menu.Item>
                                                    </Menu.Dropdown>
                                                </Menu>
                                            )}
                                            <span className="flex items-center justify-center w-7 h-7 text-gray-400 pointer-events-none">
                                                {isExpanded ? <IconChevronUp size={16} /> : <IconChevronDown size={16} />}
                                            </span>
                                        </div>
                                    </div>

                                    {/* occurrence rows — compact flat rows, no nested cards */}
                                    {isExpanded && (
                                        <div className="divide-y divide-gray-100 border-t border-gray-100">
                                            {item.bookings.map(b => (
                                                <BookingRow
                                                    key={b.id}
                                                    booking={b}
                                                    tutor={tutors.find(t => t.id === b.tutor_id)!}
                                                    eventType={eventTypes.find(e => e.id === b.event_type_id)!}
                                                    expanded={expandedId === b.id}
                                                    onExpand={() => setExpandedId(expandedId === b.id ? null : b.id)}
                                                    onRefresh={msg => { loadData(); showToast(msg) }}
                                                    onError={msg => showToast(msg, 'error')}
                                                    isCustomer={isCustomer}
                                                    compact={true}
                                                />
                                            ))}
                                        </div>
                                    )}
                                </div>
                            )
                        }

                        // standalone date group
                        return (
                            <div key={`standalone-${item.dateLabel}`}>
                                {/* date divider */}
                                <div className="flex items-center gap-3 mb-2.5">
                                    <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider whitespace-nowrap">
                                        {item.dateLabel}
                                    </span>
                                    <div className="flex-1 border-t border-gray-100" />
                                </div>
                                <div className="flex flex-col gap-2.5">
                                    {item.bookings.map(b => (
                                        <BookingRow
                                            key={b.id}
                                            booking={b}
                                            tutor={tutors.find(t => t.id === b.tutor_id)!}
                                            eventType={eventTypes.find(e => e.id === b.event_type_id)!}
                                            expanded={expandedId === b.id}
                                            onExpand={() => setExpandedId(expandedId === b.id ? null : b.id)}
                                            onRefresh={msg => { loadData(); showToast(msg) }}
                                            onError={msg => showToast(msg, 'error')}
                                            isCustomer={isCustomer}
                                        />
                                    ))}
                                </div>
                            </div>
                        )
                    })}

                    {displayItems.length === 0 && (
                        <p className="text-sm text-gray-400 text-center py-12">No bookings found.</p>
                    )}
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
