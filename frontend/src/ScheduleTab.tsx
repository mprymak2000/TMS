import { useState, useEffect } from 'react'
import { useOutletContext } from 'react-router-dom'
import { TextInput, Loader, Button, Modal, Popover } from '@mantine/core'
import { DatePicker } from '@mantine/dates'
import { IconSearch, IconChevronLeft, IconChevronRight } from '@tabler/icons-react'
import type { ReactNode } from 'react'
import type { Booking, TutorFacetOption, BookingLinkFacetOption, StudentFacetOption } from './types'
import { extractError, formatDate, formatTime, addDays, startOfWeek, startOfMonth, endOfMonth, toLocalDateStr, parseLocalDateStr } from './utils'
import BookingRow from './BookingRow'
import type { BookingsOutletContext } from './BookingsLayout'
import { FiltersMenu, ActiveFilterChips, OrderToggle, LoadMoreSentinel, PAGE_SIZE } from './BookingToolbar'
import type { BookingFilters, LoadErrors } from './BookingToolbar'

// ============================================================================
// TYPES
// ============================================================================

type Granularity = 'day' | 'week' | 'month'
// 'custom' = an arbitrary pick via the date-range pill, not one of the three presets.
type Scope = Granularity | 'custom'

// ============================================================================
// PERIOD / LABEL HELPERS — pure functions, feed ScopeSwitcher + DateRangePill below.
// ============================================================================

const MONTH_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

// Day/Week/Month bounds for the API call — always both ends, so always bounded.
const periodBounds = (scope: Granularity, anchor: Date): { dateFrom: string; dateTo: string } => {
    if (scope === 'day') { const s = toLocalDateStr(anchor); return { dateFrom: s, dateTo: s } }
    if (scope === 'week') { const start = startOfWeek(anchor); return { dateFrom: toLocalDateStr(start), dateTo: toLocalDateStr(addDays(start, 6)) } }
    return { dateFrom: toLocalDateStr(startOfMonth(anchor)), dateTo: toLocalDateStr(endOfMonth(anchor)) }
}

// Year only shown when it's not the current one - "Sep 1-7" most of the time, "Sep 1-7, 2027"
// when looking at a different year.
const yearIfNotCurrent = (year: number): string => year === new Date().getFullYear() ? '' : `${year}`

const periodLabel = (scope: Granularity, anchor: Date): string => {
    if (scope === 'day') {
        const year = yearIfNotCurrent(anchor.getFullYear())
        return `${MONTH_SHORT[anchor.getMonth()]} ${anchor.getDate()}${year ? `, ${year}` : ''}`
    }
    if (scope === 'month') {
        const lastDay = new Date(anchor.getFullYear(), anchor.getMonth() + 1, 0).getDate()
        const year = yearIfNotCurrent(anchor.getFullYear())
        return `${MONTH_SHORT[anchor.getMonth()]} 1-${lastDay}${year ? `, ${year}` : ''}`
    }
    const start = startOfWeek(anchor)
    const end = addDays(start, 6)
    const startStr = `${MONTH_SHORT[start.getMonth()]} ${start.getDate()}`
    const endStr = start.getMonth() === end.getMonth() ? `${end.getDate()}` : `${MONTH_SHORT[end.getMonth()]} ${end.getDate()}`
    const year = yearIfNotCurrent(end.getFullYear())
    return `${startStr}-${endStr}${year ? `, ${year}` : ''}`
}

// The weekday sub-line shown under the pill's main label when scope is 'day'.
const weekdayLabel = (anchor: Date): string => anchor.toLocaleDateString('en-US', { weekday: 'long' })

// A custom pick that exactly matches a calendar grid snaps to that preset instead of staying
// custom — same Monday-start convention as startOfWeek everywhere else in this codebase.
const matchesFullWeek = (dateFrom: string, dateTo: string): boolean => {
    const start = startOfWeek(parseLocalDateStr(dateFrom))
    return toLocalDateStr(start) === dateFrom && toLocalDateStr(addDays(start, 6)) === dateTo
}
const matchesFullMonth = (dateFrom: string, dateTo: string): boolean => {
    const d = parseLocalDateStr(dateFrom)
    return toLocalDateStr(startOfMonth(d)) === dateFrom && toLocalDateStr(endOfMonth(d)) === dateTo
}

// dateFrom === dateTo never reaches here in practice — handleCustomSelect snaps a single-date
// pick to scope='day' before this would be called — but the fallback stays cheap to keep anyway.
const customRangeLabel = (dateFrom: string, dateTo: string): string => {
    const start = parseLocalDateStr(dateFrom)
    const end = parseLocalDateStr(dateTo)
    if (dateFrom === dateTo) {
        const year = yearIfNotCurrent(start.getFullYear())
        return `${MONTH_SHORT[start.getMonth()]} ${start.getDate()}${year ? `, ${year}` : ''}`
    }
    const startStr = `${MONTH_SHORT[start.getMonth()]} ${start.getDate()}`
    const endStr = start.getMonth() === end.getMonth() ? `${end.getDate()}` : `${MONTH_SHORT[end.getMonth()]} ${end.getDate()}`
    const year = yearIfNotCurrent(end.getFullYear())
    return `${startStr}-${endStr}${year ? `, ${year}` : ''}`
}

// ============================================================================
// BOOKING-LIST HELPERS — used by the day-grouped list render, not the toolbar.
// ============================================================================

// Exact-day comparison key (local calendar date, not UTC — must match dateLabelOf's timezone
// or two events on different local days can share a UTC-sliced date and get bundled together)
// vs. the subtle display label shown above the first booking of each new day — WhatsApp-style
// date pill, not a full grouped card.
const dateKeyOf = (iso: string) => {
    const d = new Date(iso)
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
const dateLabelOf = (iso: string) => new Date(iso).toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' })

// Gap-between-sessions indicator - commented out for now, not deleted (see usage below).
// Same-day only — a gap indicator is only meaningful within one day's flow;
// crossing into a new day is already communicated by the date pill above.
// const gapMinutesBetween = (prevEndIso: string, nextStartIso: string): number =>
//     (new Date(nextStartIso).getTime() - new Date(prevEndIso).getTime()) / 60000
//
// const formatGap = (minutes: number): string => {
//     const hours = Math.floor(minutes / 60)
//     const mins = Math.round(minutes % 60)
//     if (hours === 0) return `${mins}m`
//     if (mins === 0) return `${hours}h`
//     return `${hours}h ${mins}m`
// }

// ============================================================================
// SUB-COMPONENTS — toolbar pieces local to this tab (Icon → Pill → Switcher).
// ============================================================================

// A literal desk-flip-calendar page turn: front page ("1") rotates down and away on hover,
// revealing the back page ("2") underneath — not just the whole icon spinning in place.
const FlipCalendarIcon = ({ active }: { active: boolean }) => {
    return (
        <div className="relative w-5 h-5 shrink-0 [perspective:200px]">
            {/* back page */}
            <div className={`absolute inset-0 rounded-[3px] border flex flex-col overflow-hidden ${active ? 'border-indigo-300' : 'border-gray-300'}`}>
                <div className="h-[6px] w-full border-b border-gray-300" />
                <div className={`flex-1 flex items-center justify-center text-[10px] font-bold leading-none ${active ? 'text-indigo-600' : 'text-gray-500'}`}>2</div>
            </div>
            {/* front page - flips down to reveal the back page underneath */}
            <div
                className={`absolute inset-0 rounded-[3px] border bg-white flex flex-col overflow-hidden origin-bottom [backface-visibility:hidden] transition-transform duration-500 group-hover:[transform:rotateX(-180deg)] ${active ? 'border-indigo-300' : 'border-gray-300'}`}
            >
                <div className="h-[6px] w-full border-b border-gray-300" />
                <div className={`flex-1 flex items-center justify-center text-[10px] font-bold leading-none ${active ? 'text-indigo-600' : 'text-gray-700'}`}>1</div>
            </div>
        </div>
    )
}

// Always-visible, always-live pill next to the Day/Week/Month segmented control. Displays
// whatever the current view's dates are regardless of what's driving it. Clicking it hands
// control to a calendar picker (Airbnb/Cal.com style): one date picked = a day, two = a range.
// Closing with only one date picked collapses to a single date, same as a range of one day.
const DateRangePill = ({
    label,
    sublabel,
    active,
    onSelect,
}: {
    label: string
    sublabel?: string
    active: boolean
    onSelect: (dateFrom: string, dateTo: string) => void
}) => {
    const [opened, setOpened] = useState(false)
    const [value, setValue] = useState<[string | null, string | null]>([null, null])
    const [displayDate, setDisplayDate] = useState(new Date())
    // The outgoing month, kept mounted just long enough to animate off-screen while the new one
    // (the in-flow DatePicker below) slides in from the opposite side - a real two-pane carousel
    // shift, not a fade-in-place.
    const [outgoing, setOutgoing] = useState<{ date: Date; direction: 1 | -1 } | null>(null)

    const handleChange = (v: boolean) => {
        setOpened(v)
        if (!v) setValue([null, null]) // dismissed without hitting Search — discard the pending pick
    }

    const handleSearch = () => {
        const [start, end] = value
        if (!start) return
        onSelect(start, end ?? start)
        setValue([null, null])
        setOpened(false)
    }

    const handleMonthChange = (direction: 1 | -1) => (date: string) => {
        setOutgoing({ date: displayDate, direction })
        setDisplayDate(parseLocalDateStr(date))
    }

    return (
        <Popover opened={opened} onChange={handleChange} position="bottom" shadow="lg" radius="lg">
            <Popover.Target>
                <button
                    onClick={() => setOpened(o => !o)}
                    className={`group flex items-center justify-center gap-2 leading-tight whitespace-nowrap text-center rounded-lg border px-4 h-9 min-w-[11rem] transition-all ${
                        active ? 'border-indigo-300 bg-indigo-50 text-indigo-700' : 'border-gray-200 bg-white text-gray-800 hover:border-gray-300 hover:shadow-sm'
                    }`}
                >
                    <FlipCalendarIcon active={active} />
                    <span className="flex flex-col items-start">
                        <span className="text-sm font-semibold">{label}</span>
                        {sublabel && <span className="text-[10px] font-medium text-gray-500 -mt-0.5">{sublabel}</span>}
                    </span>
                </button>
            </Popover.Target>
            <Popover.Dropdown className="!p-4">
                <div className="relative overflow-hidden">
                    {outgoing && (
                        <div
                            key={`out-${toLocalDateStr(outgoing.date)}`}
                            className={`absolute inset-0 pointer-events-none ${outgoing.direction === 1 ? 'animate-[pane-slide-out-left_0.3s_ease-out_forwards]' : 'animate-[pane-slide-out-right_0.3s_ease-out_forwards]'}`}
                            onAnimationEnd={() => setOutgoing(null)}
                        >
                            <DatePicker type="range" allowSingleDateInRange numberOfColumns={2} columnsToScroll={1} size="sm" date={outgoing.date} value={value} onChange={setValue} />
                        </div>
                    )}
                    <div
                        key={toLocalDateStr(displayDate)}
                        className={outgoing ? (outgoing.direction === 1 ? 'animate-[pane-slide-in-from-right_0.3s_ease-out]' : 'animate-[pane-slide-in-from-left_0.3s_ease-out]') : ''}
                    >
                        <DatePicker
                            type="range"
                            allowSingleDateInRange
                            numberOfColumns={2}
                            columnsToScroll={1}
                            size="sm"
                            date={displayDate}
                            onNextMonth={handleMonthChange(1)}
                            onPreviousMonth={handleMonthChange(-1)}
                            value={value}
                            onChange={setValue}
                        />
                    </div>
                </div>
                <div className="flex justify-end mt-3 pt-3 border-t border-gray-100">
                    <button
                        onClick={handleSearch}
                        disabled={!value[0]}
                        className="px-4 h-9 rounded-lg bg-indigo-600 text-white text-sm font-semibold hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                    >
                        Search
                    </button>
                </div>
            </Popover.Dropdown>
        </Popover>
    )
}

// Three-zone layout: left = Day/Week/Month + Today, center = prev/next + the date-range pill,
// right = search/filters. Prev/next are hidden on scope === 'custom' - no single right answer for
// "shift by what" on a range that doesn't align to a grid, so reopen the picker instead.
const ScopeSwitcher = ({
    scope,
    periodAnchor,
    dateFrom,
    dateTo,
    onScopeChange,
    onPeriodShift,
    onToday,
    onCustomSelect,
    leftContent,
}: {
    scope: Scope
    periodAnchor: Date
    dateFrom: string | null
    dateTo: string | null
    onScopeChange: (scope: Granularity) => void
    onPeriodShift: (direction: 1 | -1) => void
    onToday: () => void
    onCustomSelect: (dateFrom: string, dateTo: string) => void
    leftContent?: ReactNode
}) => {
    // Slide direction is a pure presentational concern (which way the label animates in), not
    // data — kept local rather than lifted to the parent.
    const [slideDirection, setSlideDirection] = useState<1 | -1>(1)
    const shift = (direction: 1 | -1) => { setSlideDirection(direction); onPeriodShift(direction) }

    const label = scope !== 'custom' ? periodLabel(scope, periodAnchor) : (dateFrom && dateTo ? customRangeLabel(dateFrom, dateTo) : 'Pick dates')
    const sublabel = scope === 'day' ? weekdayLabel(periodAnchor) : undefined

    return (
        <div className="flex items-center gap-3 flex-wrap">
            <div className="flex-1 flex items-center gap-3 min-w-0">
                <div className="flex items-center gap-1 bg-gray-100 rounded-lg p-1 h-9 w-fit shrink-0">
                    {(['day', 'week', 'month'] as const).map(g => (
                        <button
                            key={g}
                            onClick={() => onScopeChange(g)}
                            className={`h-full px-3.5 rounded-md text-sm font-medium transition-colors ${
                                scope === g ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-700'
                            }`}
                        >
                            {g === 'day' ? 'Day' : g === 'week' ? 'Week' : 'Month'}
                        </button>
                    ))}
                </div>
                <button onClick={onToday} className="px-3 h-9 rounded-lg bg-gray-100 text-gray-500 hover:text-gray-700 hover:bg-gray-200 transition-colors text-xs font-semibold shrink-0">
                    Today
                </button>
            </div>

            <div className="min-w-[220px] flex items-center justify-center gap-2">
                {/* No prev/next on a custom range - it doesn't align to a grid, so "shift by what"
                    is a judgment call with no single right answer. Reopen the picker instead. */}
                {scope !== 'custom' && (
                    <button onClick={() => shift(-1)} className="flex items-center justify-center w-9 h-9 rounded-lg bg-gray-100 text-gray-500 hover:text-gray-700 hover:bg-gray-200 transition-colors shrink-0">
                        <IconChevronLeft size={15} />
                    </button>
                )}
                {/* The date-range pill lives in the label's own slot — it IS the current-period
                    display, just clickable now, instead of a separate element elsewhere. */}
                <span
                    key={`${scope}-${dateFrom}-${dateTo}`}
                    className={slideDirection === 1 ? 'animate-[slide-in-right_0.35s_ease-out]' : 'animate-[slide-in-left_0.35s_ease-out]'}
                >
                    <DateRangePill label={label} sublabel={sublabel} active={scope === 'custom'} onSelect={onCustomSelect} />
                </span>
                {scope !== 'custom' && (
                    <button onClick={() => shift(1)} className="flex items-center justify-center w-9 h-9 rounded-lg bg-gray-100 text-gray-500 hover:text-gray-700 hover:bg-gray-200 transition-colors shrink-0">
                        <IconChevronRight size={15} />
                    </button>
                )}
            </div>

            <div className="flex-1 flex items-center justify-end gap-2 min-w-0">
                {leftContent}
            </div>
        </div>
    )
}

// ============================================================================
// MAIN COMPONENT
// ============================================================================

const ScheduleTab = ({ isCustomer = false }: { isCustomer?: boolean }) => {
    // shared context (from BookingsLayout)
    const { tutors, bookingLinks, isLoadingRoster, showToast } = useOutletContext<BookingsOutletContext>()

    // ---- STATE ----
    const [email] = useState('')

    // fetched data + facet options
    const [bookings, setBookings] = useState<Booking[]>([])
    const [tutorFacetOptions, setTutorFacetOptions] = useState<TutorFacetOption[]>([])
    const [bookingLinkFacetOptions, setBookingLinkFacetOptions] = useState<BookingLinkFacetOption[]>([])
    const [studentFacetOptions, setStudentFacetOptions] = useState<StudentFacetOption[]>([])
    const [loadErrors, setLoadErrors] = useState<LoadErrors>({})
    const [isLoading, setIsLoading] = useState(false)

    // what's currently selected/viewed
    // What's driving the schedule view — one of the three presets, or 'custom' via the date-range
    // pill. Single source of truth; no separate toggle layered on top.
    const [scope, setScope] = useState<Scope>('week')
    // Anchor date for Day/Week/Month bounds, preserved across scope switches (Day ->
    // Month lands on the month containing the day you were on, not "today's" month). handleToday() resets to now.
    const [periodAnchor, setPeriodAnchor] = useState<Date>(() => new Date())
    // Client side display order, does not influence backend fetch order (load more still gets future dates, despite them now being on the bottom)
    const [order, setOrder] = useState<'asc' | 'desc'>('asc')
    const [filters, setFilters] = useState<BookingFilters>(() => ({
        tutorIds: [], bookingLinkIds: [], students: [], searchQuery: '', includeCancelled: true,
        ...periodBounds('week', new Date()),
    }))

    // UI-only state
    const [expandedId, setExpandedId] = useState<string | null>(null)

    // pagination state
    const [isLoadingMore, setIsLoadingMore] = useState(false)
    const [cursor, setCursor] = useState<string | null>(null)

    // request-review modal state
    const [processingRequest, setProcessingRequest] = useState<Booking | null>(null)
    const [isSubmitting, setIsSubmitting] = useState(false)

    // ---- DATA FETCH ----
    const loadBookings = async ({
        tutorIds = filters.tutorIds,
        bookingLinkIds = filters.bookingLinkIds,
        students = filters.students,
        dateFrom = filters.dateFrom,
        dateTo = filters.dateTo,
        includeCancelled = filters.includeCancelled,
        isRange = scope === 'custom',
        emailFilter,
        cursor: cursorParam = null,
        append = false,
    }: {
        tutorIds?: string[]
        bookingLinkIds?: string[]
        students?: string[]
        dateFrom?: string | null
        dateTo?: string | null
        includeCancelled?: boolean
        isRange?: boolean
        emailFilter?: string
        cursor?: string | null
        append?: boolean
    } = {}) => {
        const timeMin = dateFrom ? `${dateFrom}T00:00:00` : undefined
        const timeMax = dateTo ? `${dateTo}T23:59:59` : undefined
        // Canonical order in which data is fetched from backend for a bounded page
        // `order` (state) only controls the client-side display sort of the already-fetched page
        const fetchOrder = 'asc'
        // Day/Week/Month omit page_size — backend default applies. Custom range overrides it.
        const pageSize = isRange ? PAGE_SIZE : undefined
        // can be loading on mount or be called to load more data from backend and append... set correct loading state
        if (append) setIsLoadingMore(true)
        else setIsLoading(true)
        try {
            const base = `${import.meta.env.VITE_API_URL}/bookings/`
            const emailParam = emailFilter ? `&email=${encodeURIComponent(emailFilter)}` : ''
            const timeMinParam = timeMin ? `&time_min=${timeMin}` : ''
            const timeMaxParam = timeMax ? `&time_max=${timeMax}` : ''
            const orderParam = `&order=${fetchOrder}`
            const tutorParams = tutorIds.map(id => `&tutor_ids=${id}`).join('')
            const bookingLinkParams = bookingLinkIds.map(id => `&booking_link_ids=${id}`).join('')
            const studentParams = students.map(pair => `&student=${encodeURIComponent(pair)}`).join('')
            const includeCancelledParam = includeCancelled ? `&include_cancelled=true` : ''
            const pageSizeParam = pageSize !== undefined ? `&page_size=${pageSize}` : ''
            const cursorParamStr = cursorParam ? `&cursor=${encodeURIComponent(cursorParam)}` : ''

            const response = await fetch(`${base}?${pageSizeParam}${cursorParamStr}${timeMinParam}${timeMaxParam}${orderParam}${tutorParams}${bookingLinkParams}${studentParams}${includeCancelledParam}${emailParam}`)
            if (!response.ok) {
                const err = await response.json()
                setLoadErrors(prev => ({ ...prev, bookings: extractError(err, 'Failed to load bookings.') }))
                return
            }
            const body = await response.json()
            // data
            setBookings(prev => append ? [...prev, ...body.items] : body.items)
            // filter options
            setTutorFacetOptions(body.facets.tutors)
            setBookingLinkFacetOptions(body.facets.booking_links)
            setStudentFacetOptions(body.facets.students)
            // pagination
            setCursor(body.next_cursor)
            // error state cleared on success
            setLoadErrors({})
        } catch (error) {
            console.error(error)
            setLoadErrors(prev => ({ ...prev, bookings: 'An unknown error occurred while loading bookings.' }))
        } finally {
            setIsLoading(false)
            setIsLoadingMore(false)
        }
    }

    const refresh = () => loadBookings({ emailFilter: isCustomer ? email : undefined })

    const handleLoadMore = () => loadBookings({ emailFilter: isCustomer ? email : undefined, cursor, append: true })

    // ---- EFFECTS ----
    useEffect(() => {
        if (!isCustomer) { loadBookings(); return }
        const saved = sessionStorage.getItem('customer_email')
        if (saved) loadBookings({ emailFilter: saved })
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])

    // ---- HANDLERS: scope / period navigation ----
    const handleScopeChange = (g: Granularity) => {
        setScope(g)
        // Reuses the current anchor (not "now") — switching Day -> Month while looking at a
        // specific day lands on that day's month, not the current month.
        const bounds = periodBounds(g, periodAnchor)
        setFilters(f => ({ ...f, dateFrom: bounds.dateFrom, dateTo: bounds.dateTo }))
        loadBookings({ emailFilter: isCustomer ? email : undefined, isRange: false, dateFrom: bounds.dateFrom, dateTo: bounds.dateTo })
    }

    // Called when the date-range pill's picker closes with a selection. A pick exactly matching
    // Mon-Sun or a full calendar month snaps to that preset instead of staying custom (fills the
    // Week/Month pill, same as picking it directly) — a single date snaps to Day. Anything else
    // stays a genuine custom range.
    const handleCustomSelect = (pickedFrom: string, pickedTo: string) => {
        if (pickedFrom === pickedTo) {
            setScope('day')
            setPeriodAnchor(parseLocalDateStr(pickedFrom))
            setFilters(f => ({ ...f, dateFrom: pickedFrom, dateTo: pickedFrom }))
            loadBookings({ emailFilter: isCustomer ? email : undefined, isRange: false, dateFrom: pickedFrom, dateTo: pickedFrom })
            return
        }
        if (matchesFullWeek(pickedFrom, pickedTo)) {
            setScope('week')
            setPeriodAnchor(parseLocalDateStr(pickedFrom))
            setFilters(f => ({ ...f, dateFrom: pickedFrom, dateTo: pickedTo }))
            loadBookings({ emailFilter: isCustomer ? email : undefined, isRange: false, dateFrom: pickedFrom, dateTo: pickedTo })
            return
        }
        if (matchesFullMonth(pickedFrom, pickedTo)) {
            setScope('month')
            setPeriodAnchor(parseLocalDateStr(pickedFrom))
            setFilters(f => ({ ...f, dateFrom: pickedFrom, dateTo: pickedTo }))
            loadBookings({ emailFilter: isCustomer ? email : undefined, isRange: false, dateFrom: pickedFrom, dateTo: pickedTo })
            return
        }
        setScope('custom')
        setFilters(f => ({ ...f, dateFrom: pickedFrom, dateTo: pickedTo }))
        loadBookings({ emailFilter: isCustomer ? email : undefined, isRange: true, dateFrom: pickedFrom, dateTo: pickedTo })
    }

    const handlePeriodShift = (direction: 1 | -1) => {
        // Unreachable in practice - the prev/next arrows are hidden for scope === 'custom' (see
        // ScopeSwitcher) - just here to narrow the type below.
        if (scope === 'custom') return
        const nextAnchor = scope === 'day' ? addDays(periodAnchor, direction)
            : scope === 'week' ? addDays(periodAnchor, direction * 7)
            : new Date(periodAnchor.getFullYear(), periodAnchor.getMonth() + direction, 1)
        setPeriodAnchor(nextAnchor)
        const bounds = periodBounds(scope, nextAnchor)
        setFilters(f => ({ ...f, dateFrom: bounds.dateFrom, dateTo: bounds.dateTo }))
        loadBookings({ emailFilter: isCustomer ? email : undefined, dateFrom: bounds.dateFrom, dateTo: bounds.dateTo })
    }

    // Preserves the current Day/Week/Month scope, just re-anchored to today. Custom mode has no
    // well-defined "today" of its own, so it collapses to Day rather than inventing "shift the
    // custom span to include today" semantics.
    const handleToday = () => {
        const anchor = new Date()
        setPeriodAnchor(anchor)
        const nextScope: Granularity = scope === 'custom' ? 'day' : scope
        setScope(nextScope)
        const bounds = periodBounds(nextScope, anchor)
        setFilters(f => ({ ...f, dateFrom: bounds.dateFrom, dateTo: bounds.dateTo }))
        loadBookings({ emailFilter: isCustomer ? email : undefined, isRange: false, dateFrom: bounds.dateFrom, dateTo: bounds.dateTo })
    }

    // ---- HANDLERS: filters ----
    // Server-side filters now (not client-side) — same reasoning as time range: filtering an
    // already-paginated set client-side can silently hide real matches sitting on unfetched pages.
    const handleTutorFilterToggle = (id: string) => {
        const next = filters.tutorIds.includes(id) ? filters.tutorIds.filter(x => x !== id) : [...filters.tutorIds, id]
        setFilters(f => ({ ...f, tutorIds: next }))
        loadBookings({ emailFilter: isCustomer ? email : undefined, tutorIds: next })
    }

    const handleBookingLinkFilterToggle = (id: string) => {
        const next = filters.bookingLinkIds.includes(id) ? filters.bookingLinkIds.filter(x => x !== id) : [...filters.bookingLinkIds, id]
        setFilters(f => ({ ...f, bookingLinkIds: next }))
        loadBookings({ emailFilter: isCustomer ? email : undefined, bookingLinkIds: next })
    }

    const handleStudentFilterToggle = (value: string) => {
        const next = filters.students.includes(value) ? filters.students.filter(x => x !== value) : [...filters.students, value]
        setFilters(f => ({ ...f, students: next }))
        loadBookings({ emailFilter: isCustomer ? email : undefined, students: next })
    }

    const handleIncludeCancelledToggle = () => {
        const next = !filters.includeCancelled
        setFilters(f => ({ ...f, includeCancelled: next }))
        loadBookings({ emailFilter: isCustomer ? email : undefined, includeCancelled: next })
    }

    // Pure display flip — displayed() re-sorts fully every render, so no refetch needed.
    const handleOrderToggle = () => setOrder(o => o === 'asc' ? 'desc' : 'asc')

    // ---- HANDLERS: request review ----
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

    // ---- DERIVED DATA ----
    const getSearchString = (b: Booking) => {
        const tutor = tutors.find(t => t.id === b.tutor_id)
        const bookingLink = bookingLinks.find(e => e.id === b.booking_link_id)
        return [
            b.student_first, b.student_last,
            b.student_email, b.student_phone,
            b.parent_email, b.parent_phone,
            b.start.slice(0, 10),
            tutor?.first_name, tutor?.last_name,
            bookingLink?.slug,
        ].filter(Boolean).join(' ').toLowerCase()
    }

    // Tutor/event-type are already narrowed server-side — search is the only genuinely
    // client-side filter.
    const displayed = bookings
        .filter(b => getSearchString(b).includes(filters.searchQuery.trim().toLowerCase()))
        .sort((a, b) => order === 'desc' ? b.start.localeCompare(a.start) : a.start.localeCompare(b.start))

    // ---- RENDER ----
    return (
        <div className="h-full flex flex-col">
            <div className="shrink-0">
                {/* load errors */}
                {loadErrors.bookings && <p className="text-sm text-red-500 mb-2">{loadErrors.bookings}</p>}

                <div className="mb-5">
                    <ScopeSwitcher
                        scope={scope}
                        periodAnchor={periodAnchor}
                        dateFrom={filters.dateFrom}
                        dateTo={filters.dateTo}
                        onScopeChange={handleScopeChange}
                        onPeriodShift={handlePeriodShift}
                        onToday={handleToday}
                        onCustomSelect={handleCustomSelect}
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
                                    tutorOptions={tutorFacetOptions.map(t => ({ value: String(t.id), label: `${t.first_name} ${t.last_name}` }))}
                                    tutorSelected={filters.tutorIds}
                                    onTutorToggle={handleTutorFilterToggle}
                                    bookingLinkOptions={bookingLinkFacetOptions.map(e => ({ value: String(e.id), label: e.slug }))}
                                    bookingLinkSelected={filters.bookingLinkIds}
                                    onBookingLinkToggle={handleBookingLinkFilterToggle}
                                    studentOptions={studentFacetOptions.map(s => ({ value: `${s.first_name}|${s.last_name}`, label: `${s.first_name} ${s.last_name}` }))}
                                    studentSelected={filters.students}
                                    onStudentToggle={handleStudentFilterToggle}
                                    includeCancelled={filters.includeCancelled}
                                    onIncludeCancelledToggle={handleIncludeCancelledToggle}
                                />
                                <div className="w-px h-6 bg-gray-200 mx-1" />
                                <OrderToggle order={order} onToggle={handleOrderToggle} />
                            </>
                        }
                    />

                    <ActiveFilterChips
                        tutorIds={filters.tutorIds}
                        bookingLinkIds={filters.bookingLinkIds}
                        students={filters.students}
                        tutors={tutors}
                        bookingLinks={bookingLinks}
                        includeCancelled={filters.includeCancelled}
                        onTutorRemove={handleTutorFilterToggle}
                        onBookingLinkRemove={handleBookingLinkFilterToggle}
                        onStudentRemove={handleStudentFilterToggle}
                        onIncludeCancelledRemove={handleIncludeCancelledToggle}
                    />
                </div>
            </div>

            <div className="flex-1 min-h-0 overflow-y-auto">
                {/* spinner */}
                {(isLoading || isLoadingRoster) && <div className="flex justify-center py-12"><Loader size="sm" /></div>}

                {/* booking list */}
                {!isLoading && (
                    <div className="bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden">
                        {displayed.length > 0 ? (
                            <div>
                                {displayed.map((b, i) => {
                                    const isNewDate = i === 0 || dateKeyOf(displayed[i - 1].start) !== dateKeyOf(b.start)
                                    // Gap-between-sessions indicator - commented out for now, not deleted.
                                    // const gapMinutes = !isNewDate ? (order === 'desc' ? gapMinutesBetween(b.end, displayed[i - 1].start) : gapMinutesBetween(displayed[i - 1].end, b.start)) : 0
                                    // const showGap = !isNewDate && gapMinutes > 30
                                    return (
                                        <div key={b.id}>
                                            {isNewDate ? (
                                                <div className={`px-5 py-2 bg-gray-50/80 text-[11px] font-semibold text-gray-500 uppercase tracking-wide ${i > 0 ? 'border-t border-gray-100' : ''}`}>
                                                    {dateLabelOf(b.start)}
                                                </div>
                                            ) : (
                                                // showGap ? (
                                                //     // Zero layout height — the label is absolutely positioned over the
                                                //     // 1px line, so it steals space from the padding of the rows above/
                                                //     // below instead of adding any of its own. Same row spacing always.
                                                //     <div className="relative px-5">
                                                //         <div className="border-t border-gray-100" />
                                                //         <span className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 bg-white px-2 text-[10px] text-gray-400 font-medium whitespace-nowrap">
                                                //             {formatGap(gapMinutes)} break
                                                //         </span>
                                                //     </div>
                                                // ) : (
                                                <div className="border-t border-gray-100" />
                                                // )
                                            )}
                                            <BookingRow
                                                booking={b}
                                                tutor={tutors.find(t => t.id === b.tutor_id)!}
                                                bookingLink={bookingLinks.find(e => e.id === b.booking_link_id)!}
                                                bookingLinks={bookingLinks}
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

                        {cursor !== null && (
                            <div className="border-t border-gray-100">
                                <LoadMoreSentinel onVisible={handleLoadMore} loading={isLoadingMore} />
                            </div>
                        )}
                    </div>
                )}
            </div>

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
        </div>
    )
}

export default ScheduleTab
