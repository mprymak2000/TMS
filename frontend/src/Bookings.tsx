import { useState, useEffect, useRef } from 'react'
import type { ReactNode } from 'react'
import { TextInput, Loader, Input, Button, Modal, Popover } from '@mantine/core'
import { IconSearch, IconCalendar, IconX, IconChevronDown, IconChevronLeft, IconChevronRight, IconSortAscending, IconSortDescending, IconRepeat } from '@tabler/icons-react'
import type { Tutor, EventType, Booking, BookingSeries } from './types'
import { extractError, formatDate, formatTime, tutorBubbleClass, addDays, startOfWeek, startOfMonth, endOfMonth, toLocalDateStr, DAY_NAMES } from './utils'
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
type TimeScope = 'day' | 'week' | 'month' | 'range'
type FetchResult = { ok: true; items: Booking[]; hasMore: boolean; total: number | null } | { ok: false; error: any }

const TABS = [
    { key: 'timeline', label: 'Schedule' },
    { key: 'recurring', label: 'Recurring' },
    { key: 'requests', label: 'Requests' },
] as const

const PAGE_SIZE = 10
// Day/Week/Month are naturally bounded (a single practitioner's day/week/month is never going
// to have hundreds of bookings), so there's no real "page 2" to paginate — same idea as Google
// Calendar's events.list maxResults (default/typical 250): request a generous cap in one go and
// just render the whole thing, no page-number UI. Range keeps real PAGE_SIZE-based pagination
// since it can genuinely span a large, open-ended window.
const WIDE_PAGE_SIZE = 250

// Exact-day comparison key (local calendar date, not UTC — must match dateLabelOf's timezone
// or two events on different local days can share a UTC-sliced date and get bundled together)
// vs. the subtle display label shown above the first booking of each new day — WhatsApp-style
// date pill, not a full grouped card.
const dateKeyOf = (iso: string) => {
    const d = new Date(iso)
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
const dateLabelOf = (iso: string) => new Date(iso).toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' })

// Day/Week/Month bounds for the API call — always both ends, so always bounded (real
// X-Total-Count + PageNav, never the unbounded Load-More fallback).
const periodBounds = (scope: 'day' | 'week' | 'month', anchor: Date): { dateFrom: string; dateTo: string } => {
    if (scope === 'day') { const s = toLocalDateStr(anchor); return { dateFrom: s, dateTo: s } }
    if (scope === 'week') { const start = startOfWeek(anchor); return { dateFrom: toLocalDateStr(start), dateTo: toLocalDateStr(addDays(start, 6)) } }
    return { dateFrom: toLocalDateStr(startOfMonth(anchor)), dateTo: toLocalDateStr(endOfMonth(anchor)) }
}

const MONTH_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const MONTH_LONG = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']

const periodLabel = (scope: 'day' | 'week' | 'month', anchor: Date): string => {
    if (scope === 'day') return anchor.toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric', year: 'numeric' })
    if (scope === 'month') return `${MONTH_LONG[anchor.getMonth()]} ${anchor.getFullYear()}`
    const start = startOfWeek(anchor)
    const end = addDays(start, 6)
    const startStr = `${MONTH_SHORT[start.getMonth()]} ${start.getDate()}`
    const endStr = start.getMonth() === end.getMonth() ? `${end.getDate()}` : `${MONTH_SHORT[end.getMonth()]} ${end.getDate()}`
    return `${startStr} – ${endStr}, ${end.getFullYear()}`
}

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

const fetchMyBookings = async (tab: 'timeline' | 'requests', timeMin: string | undefined, timeMax: string | undefined, order: 'asc' | 'desc', pageNum: number, pageSize: number, tutorIds: string[], eventTypeIds: string[], emailFilter?: string): Promise<FetchResult> => {
    const base = `${import.meta.env.VITE_API_URL}/bookings/`
    const emailParam = emailFilter ? `&email=${encodeURIComponent(emailFilter)}` : ''
    const pendingParam = tab === 'requests' ? `&pending_only=true` : ''
    const timeMinParam = timeMin ? `&time_min=${timeMin}` : ''
    const timeMaxParam = timeMax ? `&time_max=${timeMax}` : ''
    const orderParam = `&order=${order}`
    const tutorParams = tutorIds.map(id => `&tutor_ids=${id}`).join('')
    const eventTypeParams = eventTypeIds.map(id => `&event_type_ids=${id}`).join('')

    const res = await fetch(`${base}?page=${pageNum}&page_size=${pageSize}${pendingParam}${timeMinParam}${timeMaxParam}${orderParam}${tutorParams}${eventTypeParams}${emailParam}`)
    if (!res.ok) { return { ok: false, error: await res.json() } }
    const items = (await res.json()) as Booking[]
    // X-Total-Count is only present for a genuinely bounded range — real page-number navigation
    // there instead of incremental Load More, since the true total is known.
    const totalHeader = res.headers.get('x-total-count')
    const total = totalHeader !== null ? Number(totalHeader) : null
    const hasMore = total !== null ? false : items.length === pageSize
    return { ok: true, items, hasMore, total }
}

const DateRangeInputs = ({
    dateFrom,
    dateTo,
    onDateFromChange,
    onDateToChange,
}: {
    dateFrom: string | null
    dateTo: string | null
    onDateFromChange: (value: string | null) => void
    onDateToChange: (value: string | null) => void
}) => (
    <div className="flex gap-2 items-center">
        <Input
            type="date"
            size="sm"
            value={dateFrom ?? ''}
            max={dateTo ?? undefined}
            onChange={(e) => onDateFromChange(e.target.value || null)}
            styles={{ input: { borderRadius: '8px', fontFamily: 'inherit', borderColor: '#e5e7eb' } }}
        />
        <span className="text-gray-400 text-sm shrink-0">to</span>
        <Input
            type="date"
            size="sm"
            value={dateTo ?? ''}
            min={dateFrom ?? undefined}
            onChange={(e) => onDateToChange(e.target.value || null)}
            styles={{ input: { borderRadius: '8px', fontFamily: 'inherit', borderColor: '#e5e7eb' } }}
        />
    </div>
)

// Normal (ascending, dates going down the list) is the quiet default — no border. Inverted
// (descending) is the deviation from default, so it gets a visible border + accent color to
// signal "this is toggled on," same visual language as an active filter chip.
const OrderToggle = ({ order, onToggle }: { order: 'asc' | 'desc'; onToggle: () => void }) => (
    <button
        onClick={onToggle}
        title={order === 'asc' ? 'Oldest first — click for newest first' : 'Newest first — click for oldest first'}
        className={`flex items-center justify-center w-9 h-9 rounded-lg border transition-colors shrink-0 ${
            order === 'desc'
                ? 'border-indigo-300 bg-indigo-50 text-indigo-700'
                : 'border-transparent bg-gray-100 text-gray-500 hover:text-gray-700 hover:bg-gray-200'
        }`}
    >
        {order === 'asc' ? <IconSortAscending size={16} /> : <IconSortDescending size={16} />}
    </button>
)

// Three-zone layout (classic centered-header trick — two equal-growing flex-1 sides pin their
// content to the outer edges, leaving the middle genuinely centered regardless of how wide either
// side's content is): left = the actual navigator (Today + prev/next), center = the current
// period's label (or the date inputs, in Range mode), right = the granularity switcher itself.
// Day/Week/Month are one kind of thing (granularity of the same live view) — grouped tightly as a
// segmented control. Date Range is a different kind of thing (an arbitrary, manually-picked
// window) — set apart with real spacing and its own bordered/pill styling.
const ScopeSwitcher = ({
    timeScope,
    periodAnchor,
    dateFrom,
    dateTo,
    onScopeChange,
    onRangeToggle,
    onPeriodShift,
    onToday,
    onDateFromChange,
    onDateToChange,
    leftContent,
}: {
    timeScope: TimeScope
    periodAnchor: Date
    dateFrom: string | null
    dateTo: string | null
    onScopeChange: (scope: TimeScope) => void
    onRangeToggle: () => void
    onPeriodShift: (direction: 1 | -1) => void
    onToday: () => void
    onDateFromChange: (value: string | null) => void
    onDateToChange: (value: string | null) => void
    leftContent?: ReactNode
}) => {
    // Slide direction is a pure presentational concern (which way the label animates in), not
    // data — kept local rather than lifted to the parent.
    const [slideDirection, setSlideDirection] = useState<1 | -1>(1)
    const shift = (direction: 1 | -1) => { setSlideDirection(direction); onPeriodShift(direction) }

    return (
        <div className="flex items-center gap-3 flex-wrap">
            <div className="flex-1 flex items-center gap-2 min-w-0">
                {leftContent}
            </div>

            <div className="min-w-[220px] flex items-center justify-center gap-2">
                {timeScope !== 'range' && (
                    <>
                        <button onClick={onToday} className="px-3 h-9 rounded-lg bg-gray-100 text-gray-500 hover:text-gray-700 hover:bg-gray-200 transition-colors text-xs font-semibold shrink-0">
                            Today
                        </button>
                        <button onClick={() => shift(-1)} className="flex items-center justify-center w-9 h-9 rounded-lg bg-gray-100 text-gray-500 hover:text-gray-700 hover:bg-gray-200 transition-colors shrink-0">
                            <IconChevronLeft size={15} />
                        </button>
                        <span
                            key={`${timeScope}-${dateFrom}-${dateTo}`}
                            className={`block text-sm font-semibold text-gray-800 whitespace-nowrap text-center ${
                                slideDirection === 1 ? 'animate-[slide-in-right_0.18s_ease-out]' : 'animate-[slide-in-left_0.18s_ease-out]'
                            }`}
                        >
                            {periodLabel(timeScope, periodAnchor)}
                        </span>
                        <button onClick={() => shift(1)} className="flex items-center justify-center w-9 h-9 rounded-lg bg-gray-100 text-gray-500 hover:text-gray-700 hover:bg-gray-200 transition-colors shrink-0">
                            <IconChevronRight size={15} />
                        </button>
                    </>
                )}
            </div>

            <div className="flex-1 flex items-center justify-end gap-3 min-w-0">
                {/* Day/Week/Month pills and the date inputs occupy the same slot — Date Range
                    swaps one for the other rather than the two coexisting. */}
                {timeScope === 'range' ? (
                    <DateRangeInputs dateFrom={dateFrom} dateTo={dateTo} onDateFromChange={onDateFromChange} onDateToChange={onDateToChange} />
                ) : (
                    <div className="flex items-center gap-1 bg-gray-100 rounded-lg p-1 h-9 w-fit shrink-0">
                        {(['day', 'week', 'month'] as const).map(scope => (
                            <button
                                key={scope}
                                onClick={() => onScopeChange(scope)}
                                className={`h-full px-3.5 rounded-md text-sm font-medium transition-colors ${
                                    timeScope === scope ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-700'
                                }`}
                            >
                                {scope === 'day' ? 'Day' : scope === 'week' ? 'Week' : 'Month'}
                            </button>
                        ))}
                    </div>
                )}

                <button
                    onClick={onRangeToggle}
                    className={`flex items-center gap-1.5 px-3.5 h-9 rounded-lg text-sm font-medium border transition-colors shrink-0 ${
                        timeScope === 'range'
                            ? 'border-indigo-300 bg-indigo-50 text-indigo-700'
                            : 'border-gray-200 text-gray-500 hover:text-gray-700 hover:border-gray-300'
                    }`}
                >
                    <IconCalendar size={15} />
                    Date Range
                </button>
            </div>
        </div>
    )
}

const PageNav = ({ page, total, pageSize, onJump }: { page: number; total: number; pageSize: number; onJump: (pageNum: number) => void }) => {
    const totalPages = Math.ceil(total / pageSize)
    return (
        <div className="flex items-center justify-center gap-3 pt-2">
            <Button variant="default" size="xs" disabled={page <= 1} onClick={() => onJump(page - 1)}>
                Previous
            </Button>
            <span className="text-xs text-gray-500">Page {page} of {totalPages} · {total} total</span>
            <Button variant="default" size="xs" disabled={page >= totalPages} onClick={() => onJump(page + 1)}>
                Next
            </Button>
        </div>
    )
}

// Infinite scroll — no button. Scrolling this sentinel into view triggers the next page; a
// ref (not the `loading` prop itself) guards the observer callback so a burst of intersection
// events during a fast scroll can't fire multiple concurrent loads before state catches up.
const LoadMoreSentinel = ({ onVisible, loading }: { onVisible: () => void; loading: boolean }) => {
    const ref = useRef<HTMLDivElement>(null)
    const loadingRef = useRef(loading)
    loadingRef.current = loading

    useEffect(() => {
        const el = ref.current
        if (!el) return
        const observer = new IntersectionObserver(
            entries => { if (entries[0].isIntersecting && !loadingRef.current) onVisible() },
            { rootMargin: '200px' } // start loading a bit before it's actually on screen
        )
        observer.observe(el)
        return () => observer.disconnect()
    }, [onVisible])

    return (
        <div ref={ref} className="flex justify-center py-4">
            {loading && <Loader size="sm" />}
        </div>
    )
}

const FilterChip = ({ label, onRemove }: { label: string; onRemove: () => void }) => (
    <span className="inline-flex items-center gap-1 bg-indigo-50 text-indigo-700 text-xs font-medium pl-2.5 pr-1.5 py-1 rounded-full">
        {label}
        <button onClick={onRemove} className="hover:text-indigo-900 transition-colors">
            <IconX size={12} />
        </button>
    </span>
)

// Shared by Schedule, Recurring, and Requests' toolbars — self-guards, renders nothing when
// there's nothing active, so callers don't need their own activeFilterCount check.
const ActiveFilterChips = ({
    tutorIds,
    eventTypeIds,
    tutors,
    eventTypes,
    onTutorRemove,
    onEventTypeRemove,
}: {
    tutorIds: string[]
    eventTypeIds: string[]
    tutors: Tutor[]
    eventTypes: EventType[]
    onTutorRemove: (id: string) => void
    onEventTypeRemove: (id: string) => void
}) => {
    if (tutorIds.length === 0 && eventTypeIds.length === 0) return null
    return (
        <div className="flex flex-wrap gap-2 mt-3">
            {tutorIds.map(id => (
                <FilterChip
                    key={`tutor-${id}`}
                    label={`Tutor: ${tutors.find(t => String(t.id) === id)?.first_name ?? ''}`}
                    onRemove={() => onTutorRemove(id)}
                />
            ))}
            {eventTypeIds.map(id => (
                <FilterChip
                    key={`event-${id}`}
                    label={`Event: ${eventTypes.find(e => String(e.id) === id)?.name ?? ''}`}
                    onRemove={() => onEventTypeRemove(id)}
                />
            ))}
        </div>
    )
}

interface FilterOption {
    value: string
    label: string
}

// One expandable row inside the Filters menu — clicking it expands/collapses its own option
// list in place (accordion), rather than opening a second popover.
const FilterAccordionSection = ({
    label,
    options,
    selected,
    expanded,
    onToggleExpand,
    onToggleOption,
}: {
    label: string
    options: FilterOption[]
    selected: string[]
    expanded: boolean
    onToggleExpand: () => void
    onToggleOption: (value: string) => void
}) => (
    <div>
        <button
            onClick={onToggleExpand}
            className="w-full flex items-center justify-between px-3 py-2 text-sm text-gray-700 hover:bg-gray-50 transition-colors"
        >
            <span>{label}{selected.length > 0 ? ` · ${selected.length}` : ''}</span>
            <IconChevronDown size={14} className={`text-gray-400 transition-transform ${expanded ? 'rotate-180' : ''}`} />
        </button>
        {expanded && (
            <div className="pb-1">
                {options.length === 0 && <p className="px-3 py-1.5 text-xs text-gray-400">None available</p>}
                {options.map(opt => {
                    const checked = selected.includes(opt.value)
                    return (
                        <button
                            key={opt.value}
                            onClick={() => onToggleOption(opt.value)}
                            className="w-full flex items-center gap-2 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50 transition-colors text-left"
                        >
                            <span className={`w-3 h-3 rounded-full border shrink-0 transition-colors ${checked ? 'bg-indigo-600 border-indigo-600' : 'border-gray-300'}`} />
                            {opt.label}
                        </button>
                    )
                })}
            </div>
        )}
    </div>
)

// One context menu (Google-Calendar-"Schedule"-dropdown style): a single trigger, and Tutors /
// Event types expand as accordion sections inside the same panel — never a second popover.
const FiltersMenu = ({
    tutorOptions,
    tutorSelected,
    onTutorToggle,
    eventTypeOptions,
    eventTypeSelected,
    onEventTypeToggle,
}: {
    tutorOptions: FilterOption[]
    tutorSelected: string[]
    onTutorToggle: (value: string) => void
    eventTypeOptions: FilterOption[]
    eventTypeSelected: string[]
    onEventTypeToggle: (value: string) => void
}) => {
    const [opened, setOpened] = useState(false)
    const [expandedSection, setExpandedSection] = useState<'tutors' | 'eventTypes' | null>(null)
    const activeCount = tutorSelected.length + eventTypeSelected.length

    return (
        <Popover
            opened={opened}
            onChange={(v) => { setOpened(v); if (!v) setExpandedSection(null) }}
            position="bottom-start"
            shadow="md"
            width={240}
        >
            <Popover.Target>
                <Button
                    variant="default"
                    size="sm"
                    rightSection={<IconChevronDown size={14} />}
                    onClick={() => setOpened(o => !o)}
                    styles={{ root: { borderRadius: '8px', borderColor: '#e5e7eb' }, label: { fontWeight: 400 } }}
                >
                    Filters{activeCount > 0 ? ` · ${activeCount}` : ''}
                </Button>
            </Popover.Target>
            <Popover.Dropdown styles={{ dropdown: { padding: 4 } }}>
                <FilterAccordionSection
                    label="Tutors"
                    options={tutorOptions}
                    selected={tutorSelected}
                    expanded={expandedSection === 'tutors'}
                    onToggleExpand={() => setExpandedSection(s => s === 'tutors' ? null : 'tutors')}
                    onToggleOption={onTutorToggle}
                />
                <div className="border-t border-gray-100" />
                <FilterAccordionSection
                    label="Event types"
                    options={eventTypeOptions}
                    selected={eventTypeSelected}
                    expanded={expandedSection === 'eventTypes'}
                    onToggleExpand={() => setExpandedSection(s => s === 'eventTypes' ? null : 'eventTypes')}
                    onToggleOption={onEventTypeToggle}
                />
            </Popover.Dropdown>
        </Popover>
    )
}

// Shared by admin and customer Recurring tab — Monday..Sunday section headers (only for days
// with something to show), full-width SeriesRow cards underneath each, one connected card.
// Mirrors Timeline's proven day-header-bar pattern instead of a cramped 7-column grid.
const RecurringList = ({
    seriesByDay,
    tutors,
    eventTypes,
    isCustomer,
    onRefresh,
    onError,
    onCancelSeries,
    emptyState,
}: {
    seriesByDay: { day: number; name: string; series: BookingSeries[] }[]
    tutors: Tutor[]
    eventTypes: EventType[]
    isCustomer: boolean
    onRefresh: (msg: string) => void
    onError: (msg: string) => void
    onCancelSeries: (seriesId: string) => void
    emptyState: ReactNode
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
                                tutor={tutors.find(t => t.id === s.tutor_id)!}
                                eventType={eventTypes.find(e => e.id === s.event_type_id)!}
                                onRefresh={onRefresh}
                                onError={onError}
                                onCancelSeries={onCancelSeries}
                                isCustomer={isCustomer}
                            />
                        </div>
                    ))}
                </div>
            ))}
        </div>
    )
}


const Bookings = ({ isCustomer = false }: { isCustomer?: boolean }) => {
    const [bookings, setBookings] = useState<Booking[]>([])
    const [tutors, setTutors] = useState<Tutor[]>([])
    const [eventTypes, setEventTypes] = useState<EventType[]>([])
    const [activeTab, setActiveTab] = useState<BookingsTab>('timeline')
    const [timeScope, setTimeScope] = useState<TimeScope>('week')
    // Remembers which granularity was active before switching into Range mode, so clicking
    // "Date Range" a second time (toggling off) restores exactly where the user was, not a
    // fixed default.
    const [lastGranularity, setLastGranularity] = useState<'day' | 'week' | 'month'>('week')
    // Anchor date driving Day/Week/Month bounds — preserved across granularity switches (Day ->
    // Month lands on the month containing the day you were on, not "today's" month), only reset
    // to now by handleToday or on tab entry.
    const [periodAnchor, setPeriodAnchor] = useState<Date>(() => new Date())
    // Display order — a within-session browsing preference only, not persisted across reloads
    // (a fresh page load always starts chronological/ascending, regardless of what was last set).
    const [order, setOrder] = useState<'asc' | 'desc'>('asc')
    const [filters, setFilters] = useState<BookingFilters>(() => ({
        tutorIds: [], eventTypeIds: [], searchQuery: '',
        ...periodBounds('week', new Date()),
    }))
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
    // Non-null whenever Timeline is loaded (always bounded now) — drives real page-number
    // navigation there; null only for the Requests tab, which uses incremental Load More instead.
    const [total, setTotal] = useState<number | null>(null)
    // Whichever page_size was actually used for the current fetch (PAGE_SIZE or WIDE_PAGE_SIZE)
    // — PageNav only needs to show when total exceeds this, not just because total is non-null.
    // Day/Week/Month effectively never hit this in practice, but if a month ever did have more
    // than WIDE_PAGE_SIZE occurrences, this is what keeps the overflow from being silently
    // truncated instead of gracefully falling back to real pagination.
    const [pageSizeUsed, setPageSizeUsed] = useState(PAGE_SIZE)

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

    const getSeriesSearchString = (s: BookingSeries) => {
        const tutor = tutors.find(t => t.id === s.tutor_id)
        const eventType = eventTypes.find(e => e.id === s.event_type_id)
        return [
            s.student_first, s.student_last,
            tutor?.first_name, tutor?.last_name,
            eventType?.name,
        ].filter(Boolean).join(' ').toLowerCase()
    }

    const loadData = async ({
        emailFilter,
        tab = activeTab === 'requests' ? 'requests' : 'timeline',
        scope = timeScope,
        dateFrom = filters.dateFrom,
        dateTo = filters.dateTo,
        tutorIds = filters.tutorIds,
        eventTypeIds = filters.eventTypeIds,
        pageNum = 1,
        append = false,
    }: {
        emailFilter?: string
        tab?: 'timeline' | 'requests'
        scope?: TimeScope
        dateFrom?: string | null
        dateTo?: string | null
        tutorIds?: string[]
        eventTypeIds?: string[]
        pageNum?: number
        append?: boolean
    } = {}) => {
        // Every Timeline scope (Day/Week/Month/Range) is bounded now — dateFrom/dateTo fully
        // determine the window, no scope branching needed here anymore.
        const timeMin = dateFrom ? `${dateFrom}T00:00:00` : undefined
        const timeMax = dateTo ? `${dateTo}T23:59:59` : undefined
        // Canonical fetch order for a bounded page — `order` (state) only controls the client-side
        // display sort of the already-fetched page (see displayed's .sort()), same as it always did.
        const fetchOrder = 'asc'
        // Day/Week/Month: one big page, no real pagination (see WIDE_PAGE_SIZE). Range and
        // Requests keep real PAGE_SIZE-based pagination — Range can genuinely be large, and
        // Requests is unbounded (no time_max), so page_size there controls actual walk depth.
        const pageSize = tab === 'requests' || scope === 'range' ? PAGE_SIZE : WIDE_PAGE_SIZE
        if (append) setIsLoadingMore(true)
        else setIsLoading(true)
        try {
            const [bookingsRes, tutorsRes, eventTypesRes] = await Promise.all([
                fetchMyBookings(tab, timeMin, timeMax, fetchOrder, pageNum, pageSize, tutorIds, eventTypeIds, emailFilter),
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
            setTotal(bookingsRes.total)
            setPageSizeUsed(pageSize)
            setLoadErrors({})
        } catch (error) {
            console.error(error)
            setLoadErrors(prev => ({ ...prev, bookings: 'An unknown error occurred while loading bookings.' }))
        } finally {
            setIsLoading(false)
            setIsLoadingMore(false)
        }
    }

    const loadSeries = async (emailFilter?: string, tutorIds: string[] = filters.tutorIds, eventTypeIds: string[] = filters.eventTypeIds) => {
        setIsLoadingSeries(true)
        try {
            const params = [
                emailFilter ? `email=${encodeURIComponent(emailFilter)}` : null,
                ...tutorIds.map(id => `tutor_ids=${id}`),
                ...eventTypeIds.map(id => `event_type_ids=${id}`),
            ].filter(Boolean)
            const query = params.length ? `?${params.join('&')}` : ''
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

    const handleScopeChange = (scope: TimeScope) => {
        setTimeScope(scope)
        if (scope === 'range') {
            // entering range mode — nothing to fetch until at least one date is picked
            setFilters(f => ({ ...f, dateFrom: null, dateTo: null }))
            setBookings([])
            setHasMore(false)
            setTotal(null)
            return
        }
        setLastGranularity(scope)
        // Reuses the current anchor (not "now") — switching Day -> Month while looking at a
        // specific day lands on that day's month, not the current month.
        const bounds = periodBounds(scope, periodAnchor)
        setFilters(f => ({ ...f, dateFrom: bounds.dateFrom, dateTo: bounds.dateTo }))
        // scope passed explicitly — setTimeScope above hasn't taken effect in this closure yet
        loadData({ emailFilter: isCustomer ? email : undefined, tab: 'timeline', scope, dateFrom: bounds.dateFrom, dateTo: bounds.dateTo })
    }

    // Toggle: Date Range button switches into range mode; clicking it again while already in
    // range mode restores whichever granularity (day/week/month) was active before.
    const handleRangeToggle = () => handleScopeChange(timeScope === 'range' ? lastGranularity : 'range')

    const handlePeriodShift = (direction: 1 | -1) => {
        const scope = timeScope as 'day' | 'week' | 'month' // only invoked when timeScope !== 'range'
        const nextAnchor = scope === 'day' ? addDays(periodAnchor, direction)
            : scope === 'week' ? addDays(periodAnchor, direction * 7)
            : new Date(periodAnchor.getFullYear(), periodAnchor.getMonth() + direction, 1)
        setPeriodAnchor(nextAnchor)
        const bounds = periodBounds(scope, nextAnchor)
        setFilters(f => ({ ...f, dateFrom: bounds.dateFrom, dateTo: bounds.dateTo }))
        loadData({ emailFilter: isCustomer ? email : undefined, tab: 'timeline', dateFrom: bounds.dateFrom, dateTo: bounds.dateTo })
    }

    const handleToday = () => {
        const anchor = new Date()
        const scope = timeScope as 'day' | 'week' | 'month'
        setPeriodAnchor(anchor)
        const bounds = periodBounds(scope, anchor)
        setFilters(f => ({ ...f, dateFrom: bounds.dateFrom, dateTo: bounds.dateTo }))
        loadData({ emailFilter: isCustomer ? email : undefined, tab: 'timeline', dateFrom: bounds.dateFrom, dateTo: bounds.dateTo })
    }

    const handleDateFromChange = (value: string | null) => {
        // A dateFrom later than the current dateTo would make the range inverted/nonsensical —
        // clear dateTo rather than silently allow it (the input's max attribute already blocks
        // most such picks in the UI itself; this covers anything that slips through).
        const dateTo = value && filters.dateTo && value > filters.dateTo ? null : filters.dateTo
        setFilters(f => ({ ...f, dateFrom: value, dateTo }))
        if (!value && !dateTo) {
            // Both dates cleared — loadData would otherwise treat this as "no bound on either
            // side" and fetch everything. Nothing should show until a date is picked again.
            setBookings([])
            setHasMore(false)
            setTotal(null)
            return
        }
        loadData({ emailFilter: isCustomer ? email : undefined, tab: 'timeline', dateFrom: value, dateTo })
    }

    const handleDateToChange = (value: string | null) => {
        // Ignore a dateTo earlier than the current dateFrom — same reasoning as above, just the
        // other direction; the input's min attribute is the primary guard, this is the backstop.
        if (value && filters.dateFrom && value < filters.dateFrom) return
        setFilters(f => ({ ...f, dateTo: value }))
        if (!filters.dateFrom && !value) {
            // Both dates cleared — same reasoning as handleDateFromChange above.
            setBookings([])
            setHasMore(false)
            setTotal(null)
            return
        }
        loadData({ emailFilter: isCustomer ? email : undefined, tab: 'timeline', dateTo: value })
    }

    // Pure display flip — displayed() re-sorts fully every render, so no refetch needed.
    const handleOrderToggle = () => setOrder(o => o === 'asc' ? 'desc' : 'asc')

    const handleLoadMore = () => loadData({ emailFilter: isCustomer ? email : undefined, tab: 'timeline', pageNum: page + 1, append: true })

    // Bounded range only (total is non-null) — jumps replace the page rather than appending,
    // since each page is a distinct slice of an already-fully-known set, not an incremental extension.
    const handlePageJump = (pageNum: number) => loadData({ emailFilter: isCustomer ? email : undefined, tab: 'timeline', pageNum, append: false })

    // Server-side filters now (not client-side) — same reasoning as time range: filtering an
    // already-paginated set client-side can silently hide real matches sitting on unfetched pages.
    const handleTutorFilterToggle = (id: string) => {
        const next = filters.tutorIds.includes(id) ? filters.tutorIds.filter(x => x !== id) : [...filters.tutorIds, id]
        setFilters(f => ({ ...f, tutorIds: next }))
        if (activeTab === 'recurring') { loadSeries(isCustomer ? email : undefined, next, filters.eventTypeIds); return }
        loadData({ emailFilter: isCustomer ? email : undefined, tab: activeTab === 'requests' ? 'requests' : 'timeline', tutorIds: next })
    }

    const handleEventTypeFilterToggle = (id: string) => {
        const next = filters.eventTypeIds.includes(id) ? filters.eventTypeIds.filter(x => x !== id) : [...filters.eventTypeIds, id]
        setFilters(f => ({ ...f, eventTypeIds: next }))
        if (activeTab === 'recurring') { loadSeries(isCustomer ? email : undefined, filters.tutorIds, next); return }
        loadData({ emailFilter: isCustomer ? email : undefined, tab: activeTab === 'requests' ? 'requests' : 'timeline', eventTypeIds: next })
    }

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

    // Tutor/event-type are already narrowed server-side (both Timeline and Requests send
    // tutor_ids/event_type_ids) — search is the only genuinely client-side filter.
    const displayed = bookings
        .filter(b => getSearchString(b).includes(filters.searchQuery.trim().toLowerCase()))
        // Requests' backend branch hardcodes order=desc regardless of what's sent, so this sort
        // is load-bearing there (not just cosmetic re-display like it is for Timeline).
        .sort((a, b) => order === 'desc' ? b.start.localeCompare(a.start) : a.start.localeCompare(b.start))

    // Tutor/event-type narrowing happens server-side via loadSeries (see get_booking_series) —
    // search is the only genuinely client-side filter, same convention as `displayed` above.
    const displayedSeries = seriesList
        .filter(s => getSeriesSearchString(s).includes(filters.searchQuery.trim().toLowerCase()))

    // Grouped Monday(0)..Sunday(6), skipping days with nothing to show, sorted by start_time
    // within each day — "HH:MM:SS" strings compare correctly lexicographically.
    const seriesByDay = DAY_NAMES
        .map((name, day) => ({
            day,
            name,
            series: displayedSeries
                .filter(s => s.start_day_of_week === day)
                .sort((a, b) => order === 'desc' ? b.start_time.localeCompare(a.start_time) : a.start_time.localeCompare(b.start_time)),
        }))
        .filter(({ series }) => series.length > 0)
    const orderedSeriesByDay = order === 'desc' ? [...seriesByDay].reverse() : seriesByDay

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
                                className={`group flex items-center gap-1 px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
                                    activeTab === tab.key
                                        ? 'bg-white text-gray-800 shadow-sm'
                                        : 'text-gray-500 hover:text-gray-700'
                                }`}
                            >
                                {tab.label}
                                {tab.key === 'recurring' && <IconRepeat size={13} className="transition-transform duration-500 group-hover:rotate-180" />}
                            </button>
                        ))}
                    </div>

                    {activeTab === 'recurring' ? (
                        <>
                            {isLoadingSeries && <div className="flex justify-center py-16"><Loader size="sm" /></div>}
                            {!isLoadingSeries && (
                                <RecurringList
                                    seriesByDay={orderedSeriesByDay}
                                    tutors={tutors}
                                    eventTypes={eventTypes}
                                    isCustomer={true}
                                    onRefresh={msg => { refresh(); showToast(msg) }}
                                    onError={msg => showToast(msg, 'error')}
                                    onCancelSeries={() => {}}
                                    emptyState={
                                        <div className="text-center py-16">
                                            <div className="w-12 h-12 rounded-xl bg-gray-100 flex items-center justify-center mx-auto mb-4">
                                                <IconCalendar size={22} className="text-gray-400" />
                                            </div>
                                            <p className="text-sm font-medium text-gray-500">No recurring series</p>
                                        </div>
                                    }
                                />
                            )}
                        </>
                    ) : (
                        <>
                            {/* time-scope toggle — inner axis, only relevant to the timeline view.
                                Order toggle lives in its left zone (no search/filters for customers). */}
                            <div className="mb-4">
                                <ScopeSwitcher
                                    timeScope={timeScope}
                                    periodAnchor={periodAnchor}
                                    dateFrom={filters.dateFrom}
                                    dateTo={filters.dateTo}
                                    onScopeChange={handleScopeChange}
                                    onRangeToggle={handleRangeToggle}
                                    onPeriodShift={handlePeriodShift}
                                    onToday={handleToday}
                                    onDateFromChange={handleDateFromChange}
                                    onDateToChange={handleDateToChange}
                                    leftContent={<OrderToggle order={order} onToggle={handleOrderToggle} />}
                                />
                            </div>

                            {isLoading && <div className="flex justify-center py-16"><Loader size="sm" /></div>}

                            {!isLoading && (
                                <div className="bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden">
                                    {displayed.length > 0 ? (
                                        <div>
                                            {displayed.map((b, i) => {
                                                const isNewDate = i === 0 || dateKeyOf(displayed[i - 1].start) !== dateKeyOf(b.start)
                                                const gapMinutes = !isNewDate ? (order === 'desc' ? gapMinutesBetween(b.end, displayed[i - 1].start) : gapMinutesBetween(displayed[i - 1].end, b.start)) : 0
                                                const showGap = !isNewDate && gapMinutes > 30
                                                return (
                                                    <div key={b.id}>
                                                        {isNewDate ? (
                                                            <div className={`px-5 py-2 bg-gray-50/80 text-[11px] font-semibold text-gray-500 uppercase tracking-wide ${i > 0 ? 'border-t border-gray-100' : ''}`}>
                                                                {dateLabelOf(b.start)}
                                                            </div>
                                                        ) : showGap ? (
                                                            // Zero layout height — the label is absolutely positioned over the
                                                            // 1px line, so it steals space from the padding of the rows above/
                                                            // below instead of adding any of its own. Same row spacing always.
                                                            <div className="relative px-5">
                                                                <div className="border-t border-gray-100" />
                                                                <span className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 bg-white px-2 text-[10px] text-gray-400 font-medium whitespace-nowrap">
                                                                    {formatGap(gapMinutes)} break
                                                                </span>
                                                            </div>
                                                        ) : (
                                                            <div className="border-t border-gray-100" />
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
                                                            compact={true}
                                                        />
                                                    </div>
                                                )
                                            })}
                                        </div>
                                    ) : (
                                        <div className="text-center py-16">
                                            <div className="w-12 h-12 rounded-xl bg-gray-100 flex items-center justify-center mx-auto mb-4">
                                                <IconCalendar size={22} className="text-gray-400" />
                                            </div>
                                            <p className="text-sm font-medium text-gray-500">
                                                {timeScope === 'range' && !filters.dateFrom && !filters.dateTo ? 'Pick a date range' : 'No bookings found'}
                                            </p>
                                            <p className="text-xs text-gray-400 mt-1">
                                                {timeScope === 'range'
                                                    ? (!filters.dateFrom && !filters.dateTo ? 'Choose a start and end date above.' : 'No sessions in this range.')
                                                    : `No sessions this ${timeScope}.`}
                                            </p>
                                        </div>
                                    )}

                                    {total !== null && total > pageSizeUsed ? (
                                        <div className="border-t border-gray-100 px-5 py-3">
                                            <PageNav page={page} total={total} pageSize={pageSizeUsed} onJump={handlePageJump} />
                                        </div>
                                    ) : total === null && hasMore && (
                                        <div className="border-t border-gray-100">
                                            <LoadMoreSentinel onVisible={handleLoadMore} loading={isLoadingMore} />
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

            {/* tabs */}
            <div className="flex gap-1 bg-gray-100 rounded-lg p-1 w-fit mb-5">
                {TABS.filter(t => !isCustomer || t.key !== 'requests').map(tab => (
                    <button
                        key={tab.key}
                        onClick={() => handleTabChange(tab.key)}
                        className={`group flex items-center gap-1 px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
                            activeTab === tab.key
                                ? 'bg-white text-gray-800 shadow-sm'
                                : 'text-gray-500 hover:text-gray-700'
                        }`}
                    >
                        {tab.label}
                        {tab.key === 'recurring' && <IconRepeat size={13} className="transition-transform duration-500 group-hover:rotate-180" />}
                    </button>
                ))}
            </div>

            {/* time-scope toggle — inner axis, only relevant to the timeline view. Filters/search
                live in its left zone, on the same line as the day/week/month/range navigator. */}
            {activeTab === 'timeline' && (
                <div className="mb-5">
                    <ScopeSwitcher
                        timeScope={timeScope}
                        periodAnchor={periodAnchor}
                        dateFrom={filters.dateFrom}
                        dateTo={filters.dateTo}
                        onScopeChange={handleScopeChange}
                        onRangeToggle={handleRangeToggle}
                        onPeriodShift={handlePeriodShift}
                        onToday={handleToday}
                        onDateFromChange={handleDateFromChange}
                        onDateToChange={handleDateToChange}
                        leftContent={
                            <>
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
                                    tutorOptions={tutors.map(t => ({ value: String(t.id), label: `${t.first_name} ${t.last_name}` }))}
                                    tutorSelected={filters.tutorIds}
                                    onTutorToggle={handleTutorFilterToggle}
                                    eventTypeOptions={eventTypes.map(e => ({ value: String(e.id), label: e.name }))}
                                    eventTypeSelected={filters.eventTypeIds}
                                    onEventTypeToggle={handleEventTypeFilterToggle}
                                />
                                <div className="w-px h-6 bg-gray-200 mx-1" />
                                <OrderToggle order={order} onToggle={handleOrderToggle} />
                            </>
                        }
                    />

                    <ActiveFilterChips
                        tutorIds={filters.tutorIds}
                        eventTypeIds={filters.eventTypeIds}
                        tutors={tutors}
                        eventTypes={eventTypes}
                        onTutorRemove={handleTutorFilterToggle}
                        onEventTypeRemove={handleEventTypeFilterToggle}
                    />
                </div>
            )}

            {/* Recurring/Requests toolbar — same Search + Filters + order toggle as Schedule,
                just without ScopeSwitcher's day/week/month/range navigator (neither tab has a
                time-period concept to navigate). */}
            {(activeTab === 'recurring' || activeTab === 'requests') && !isCustomer && (
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
                            tutorOptions={tutors.map(t => ({ value: String(t.id), label: `${t.first_name} ${t.last_name}` }))}
                            tutorSelected={filters.tutorIds}
                            onTutorToggle={handleTutorFilterToggle}
                            eventTypeOptions={eventTypes.map(e => ({ value: String(e.id), label: e.name }))}
                            eventTypeSelected={filters.eventTypeIds}
                            onEventTypeToggle={handleEventTypeFilterToggle}
                        />
                        <div className="w-px h-6 bg-gray-200 mx-1" />
                        <OrderToggle order={order} onToggle={handleOrderToggle} />
                    </div>

                    <ActiveFilterChips
                        tutorIds={filters.tutorIds}
                        eventTypeIds={filters.eventTypeIds}
                        tutors={tutors}
                        eventTypes={eventTypes}
                        onTutorRemove={handleTutorFilterToggle}
                        onEventTypeRemove={handleEventTypeFilterToggle}
                    />
                </div>
            )}

            {/* spinner */}
            {(activeTab === 'recurring' ? isLoadingSeries : isLoading) && <div className="flex justify-center py-12"><Loader size="sm" /></div>}

            {/* requests tab */}
            {!isLoading && activeTab === 'requests' && (
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

            {/* recurring tab */}
            {!isLoadingSeries && activeTab === 'recurring' && (
                <RecurringList
                    seriesByDay={orderedSeriesByDay}
                    tutors={tutors}
                    eventTypes={eventTypes}
                    isCustomer={isCustomer}
                    onRefresh={msg => { refresh(); showToast(msg) }}
                    onError={msg => showToast(msg, 'error')}
                    onCancelSeries={setCancellingSeriesId}
                    emptyState={<p className="text-sm text-gray-400 text-center py-12">No recurring series.</p>}
                />
            )}

            {/* booking list */}
            {!isLoading && activeTab !== 'requests' && activeTab !== 'recurring' && (
                <div className="bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden">
                    {displayed.length > 0 ? (
                        <div>
                            {displayed.map((b, i) => {
                                const isNewDate = i === 0 || dateKeyOf(displayed[i - 1].start) !== dateKeyOf(b.start)
                                const gapMinutes = !isNewDate ? (order === 'desc' ? gapMinutesBetween(b.end, displayed[i - 1].start) : gapMinutesBetween(displayed[i - 1].end, b.start)) : 0
                                const showGap = !isNewDate && gapMinutes > 30
                                return (
                                    <div key={b.id}>
                                        {isNewDate ? (
                                            <div className={`px-5 py-2 bg-gray-50/80 text-[11px] font-semibold text-gray-500 uppercase tracking-wide ${i > 0 ? 'border-t border-gray-100' : ''}`}>
                                                {dateLabelOf(b.start)}
                                            </div>
                                        ) : showGap ? (
                                            // Zero layout height — the label is absolutely positioned over the
                                            // 1px line, so it steals space from the padding of the rows above/
                                            // below instead of adding any of its own. Same row spacing always.
                                            <div className="relative px-5">
                                                <div className="border-t border-gray-100" />
                                                <span className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 bg-white px-2 text-[10px] text-gray-400 font-medium whitespace-nowrap">
                                                    {formatGap(gapMinutes)} break
                                                </span>
                                            </div>
                                        ) : (
                                            <div className="border-t border-gray-100" />
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
                                            compact={true}
                                        />
                                    </div>
                                )
                            })}
                        </div>
                    ) : (
                        <p className="text-sm text-gray-400 text-center py-12">No bookings found.</p>
                    )}

                    {total !== null && total > pageSizeUsed ? (
                        <div className="border-t border-gray-100 px-5 py-3">
                            <PageNav page={page} total={total} pageSize={pageSizeUsed} onJump={handlePageJump} />
                        </div>
                    ) : total === null && hasMore && (
                        <div className="border-t border-gray-100">
                            <LoadMoreSentinel onVisible={handleLoadMore} loading={isLoadingMore} />
                        </div>
                    )}
                </div>
            )}

            {!isLoading && activeTab === 'requests' && total === null && hasMore && (
                <LoadMoreSentinel onVisible={handleLoadMore} loading={isLoadingMore} />
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
