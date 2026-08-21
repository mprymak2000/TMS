import { useState, useEffect, useRef } from 'react'
import type { ReactNode } from 'react'
import { TextInput, Loader, Button, Modal, Popover, Switch } from '@mantine/core'
import { IconSearch, IconCalendar, IconX, IconChevronDown, IconChevronLeft, IconChevronRight, IconSortAscending, IconSortDescending, IconRepeat } from '@tabler/icons-react'
import type { Tutor, EventType, Booking, BookingSeries, TutorFacetOption, EventTypeFacetOption, StudentFacetOption } from './types'
import { DatePicker } from '@mantine/dates'
import { extractError, formatDate, formatTime, tutorBubbleClass, addDays, startOfWeek, startOfMonth, endOfMonth, toLocalDateStr, parseLocalDateStr, DAY_NAMES } from './utils'
import { useToast } from './useToast'
import BookingRow from './BookingRow'
import SeriesRow from './SeriesRow'
import Toast from './Toast'

interface BookingFilters {
    tutorIds: string[]
    eventTypeIds: string[]
    students: string[]
    dateFrom: string | null
    dateTo: string | null
    searchQuery: string
    includeCancelled: boolean
}

interface LoadErrors {
    bookings?: string
    tutors?: string
    eventTypes?: string
}

type BookingsTab = 'timeline' | 'recurring' | 'requests'
type Granularity = 'day' | 'week' | 'month'
// 'custom' = an arbitrary pick via the date-range pill, not one of the three presets.
type Scope = Granularity | 'custom'

const TABS = [
    { key: 'timeline', label: 'Schedule' },
    { key: 'recurring', label: 'Recurring' },
    { key: 'requests', label: 'Requests' },
] as const

const PAGE_SIZE = 10  // explicit override for Range/Requests; Day/Week/Month omit page_size, backend default applies

// Exact-day comparison key (local calendar date, not UTC — must match dateLabelOf's timezone
// or two events on different local days can share a UTC-sliced date and get bundled together)
// vs. the subtle display label shown above the first booking of each new day — WhatsApp-style
// date pill, not a full grouped card.
const dateKeyOf = (iso: string) => {
    const d = new Date(iso)
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
const dateLabelOf = (iso: string) => new Date(iso).toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' })

// Day/Week/Month bounds for the API call — always both ends, so always bounded.
const periodBounds = (scope: Granularity, anchor: Date): { dateFrom: string; dateTo: string } => {
    if (scope === 'day') { const s = toLocalDateStr(anchor); return { dateFrom: s, dateTo: s } }
    if (scope === 'week') { const start = startOfWeek(anchor); return { dateFrom: toLocalDateStr(start), dateTo: toLocalDateStr(addDays(start, 6)) } }
    return { dateFrom: toLocalDateStr(startOfMonth(anchor)), dateTo: toLocalDateStr(endOfMonth(anchor)) }
}

const MONTH_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

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
    students,
    tutors,
    eventTypes,
    includeCancelled,
    onTutorRemove,
    onEventTypeRemove,
    onStudentRemove,
    onIncludeCancelledRemove,
}: {
    tutorIds: string[]
    eventTypeIds: string[]
    students: string[]
    tutors: Tutor[]
    eventTypes: EventType[]
    includeCancelled: boolean
    onTutorRemove: (id: string) => void
    onEventTypeRemove: (id: string) => void
    onStudentRemove: (value: string) => void
    onIncludeCancelledRemove: () => void
}) => {
    if (tutorIds.length === 0 && eventTypeIds.length === 0 && students.length === 0 && includeCancelled) return null
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
            {students.map(pair => (
                <FilterChip
                    key={`student-${pair}`}
                    label={`Student: ${pair.replace('|', ' ')}`}
                    onRemove={() => onStudentRemove(pair)}
                />
            ))}
            {!includeCancelled && (
                <FilterChip label="Hide cancelled" onRemove={onIncludeCancelledRemove} />
            )}
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
    studentOptions,
    studentSelected,
    onStudentToggle,
    includeCancelled,
    onIncludeCancelledToggle,
}: {
    tutorOptions: FilterOption[]
    tutorSelected: string[]
    onTutorToggle: (value: string) => void
    eventTypeOptions: FilterOption[]
    eventTypeSelected: string[]
    onEventTypeToggle: (value: string) => void
    studentOptions: FilterOption[]
    studentSelected: string[]
    onStudentToggle: (value: string) => void
    includeCancelled: boolean
    onIncludeCancelledToggle: () => void
}) => {
    const [opened, setOpened] = useState(false)
    // Independent toggles, not a single-open accordion — expanding Students shouldn't collapse
    // Tutors if it's already open.
    const [expandedSections, setExpandedSections] = useState<Set<'tutors' | 'eventTypes' | 'students'>>(new Set())
    const toggleSection = (key: 'tutors' | 'eventTypes' | 'students') => {
        setExpandedSections(prev => {
            const next = new Set(prev)
            if (next.has(key)) next.delete(key)
            else next.add(key)
            return next
        })
    }
    const activeCount = tutorSelected.length + eventTypeSelected.length + studentSelected.length + (includeCancelled ? 0 : 1)

    return (
        <Popover
            opened={opened}
            onChange={(v) => { setOpened(v); if (!v) setExpandedSections(new Set()) }}
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
                    expanded={expandedSections.has('tutors')}
                    onToggleExpand={() => toggleSection('tutors')}
                    onToggleOption={onTutorToggle}
                />
                <div className="border-t border-gray-100" />
                <FilterAccordionSection
                    label="Event types"
                    options={eventTypeOptions}
                    selected={eventTypeSelected}
                    expanded={expandedSections.has('eventTypes')}
                    onToggleExpand={() => toggleSection('eventTypes')}
                    onToggleOption={onEventTypeToggle}
                />
                <div className="border-t border-gray-100" />
                <FilterAccordionSection
                    label="Students"
                    options={studentOptions}
                    selected={studentSelected}
                    expanded={expandedSections.has('students')}
                    onToggleExpand={() => toggleSection('students')}
                    onToggleOption={onStudentToggle}
                />
                <div className="border-t border-gray-100" />
                <button
                    type="button"
                    onClick={onIncludeCancelledToggle}
                    className="w-full flex items-center justify-between gap-2 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50 transition-colors text-left"
                >
                    <span>Hide cancelled</span>
                    <Switch checked={!includeCancelled} onChange={() => {}} color="indigo" size="sm" style={{ pointerEvents: 'none' }} />
                </button>
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


const Bookings = ({ isCustomer = false }: { isCustomer?: boolean }) => {
    const [bookings, setBookings] = useState<Booking[]>([])
    const [tutors, setTutors] = useState<Tutor[]>([])
    const [eventTypes, setEventTypes] = useState<EventType[]>([])
    const [tutorFacetOptions, setTutorFacetOptions] = useState<TutorFacetOption[]>([])
    const [eventTypeFacetOptions, setEventTypeFacetOptions] = useState<EventTypeFacetOption[]>([])
    const [studentFacetOptions, setStudentFacetOptions] = useState<StudentFacetOption[]>([])
    // outter tab state — timeline, recurring, requests
    const [activeTab, setActiveTab] = useState<BookingsTab>('timeline')
    // What's driving the timeline view — one of the three presets, or 'custom' via the date-range
    // pill. Single source of truth; no separate toggle layered on top.
    const [scope, setScope] = useState<Scope>('week')
    // Anchor date for Day/Week/Month bounds, preserved across scope switches (Day ->
    // Month lands on the month containing the day you were on, not "today's" month). handleToday() resets to now.
    const [periodAnchor, setPeriodAnchor] = useState<Date>(() => new Date())
    // Client side display order, does not influence backend fetch order (load more still gets future dates, despite them now being on the bottom)
    const [order, setOrder] = useState<'asc' | 'desc'>('asc')
    const [filters, setFilters] = useState<BookingFilters>(() => ({
        tutorIds: [], eventTypeIds: [], students: [], searchQuery: '', includeCancelled: true,
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
    const [cursor, setCursor] = useState<string | null>(null)

    // recurring tab — separate state, separate shape (BookingSeries, not Booking), unpaginated
    const [seriesList, setSeriesList] = useState<BookingSeries[]>([])
    const [isLoadingSeries, setIsLoadingSeries] = useState(false)

    // Resolution roster — session-scoped, loaded once, never touched by filters/dates/tabs.
    const [isLoadingRoster, setIsLoadingRoster] = useState(false)

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

    const loadRoster = async () => {
        setIsLoadingRoster(true)
        try {
            const [tutorResponse, eventTypeResponse] = await Promise.all([
                fetch(`${import.meta.env.VITE_API_URL}/tutors`),
                fetch(`${import.meta.env.VITE_API_URL}/event_types`),
            ])
            if (!tutorResponse.ok) {
                const err = await tutorResponse.json()
                setLoadErrors(prev => ({ ...prev, tutors: extractError(err, 'Failed to load tutors.') }))
                return
            }
            if (!eventTypeResponse.ok) {
                const err = await eventTypeResponse.json()
                setLoadErrors(prev => ({ ...prev, eventTypes: extractError(err, 'Failed to load event types.') }))
                return
            }
            setTutors(await tutorResponse.json())
            setEventTypes(await eventTypeResponse.json())
        } catch (error) {
            console.error(error)
            setLoadErrors(prev => ({
                ...prev,
                tutors: 'An unknown error occurred while loading tutors.',
                eventTypes: 'An unknown error occurred while loading event types.'
            }))
        } finally {
            setIsLoadingRoster(false)
        }
    }

    const loadBookings = async ({
        tutorIds = filters.tutorIds,
        eventTypeIds = filters.eventTypeIds,
        students = filters.students,
        tab = activeTab === 'requests' ? 'requests' : 'timeline',
        dateFrom = filters.dateFrom,
        dateTo = filters.dateTo,
        includeCancelled = filters.includeCancelled,
        isRange = scope === 'custom',
        emailFilter,
        cursor: cursorParam = null,
        append = false,
    }: {
        tutorIds?: string[]
        eventTypeIds?: string[]
        students?: string[]
        tab?: 'timeline' | 'requests'
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
        // Day/Week/Month omit page_size — backend default applies. Range/Requests override it.
        const pageSize = tab === 'requests' || isRange ? PAGE_SIZE : undefined
        // can be loading on mount or be called to load more data from backend and append... set correct loading state
        if (append) setIsLoadingMore(true)
        else setIsLoading(true)
        try {
            const base = `${import.meta.env.VITE_API_URL}/bookings/`
            const emailParam = emailFilter ? `&email=${encodeURIComponent(emailFilter)}` : ''
            const pendingParam = tab === 'requests' ? `&pending_only=true` : ''
            const timeMinParam = timeMin ? `&time_min=${timeMin}` : ''
            const timeMaxParam = timeMax ? `&time_max=${timeMax}` : ''
            const orderParam = `&order=${fetchOrder}`
            const tutorParams = tutorIds.map(id => `&tutor_ids=${id}`).join('')
            const eventTypeParams = eventTypeIds.map(id => `&event_type_ids=${id}`).join('')
            const studentParams = students.map(pair => `&student=${encodeURIComponent(pair)}`).join('')
            const includeCancelledParam = includeCancelled ? `&include_cancelled=true` : ''
            const pageSizeParam = pageSize !== undefined ? `&page_size=${pageSize}` : ''
            const cursorParamStr = cursorParam ? `&cursor=${encodeURIComponent(cursorParam)}` : ''

            const response = await fetch(`${base}?${pageSizeParam}${cursorParamStr}${pendingParam}${timeMinParam}${timeMaxParam}${orderParam}${tutorParams}${eventTypeParams}${studentParams}${includeCancelledParam}${emailParam}`)
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
            setEventTypeFacetOptions(body.facets.event_types)
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
        // Top-level load every subtab depends on — fires independently, no need to await it.
        loadRoster()
        if (!isCustomer) { loadBookings({ tab: 'timeline' }); return }
        const saved = sessionStorage.getItem('customer_email')
        if (saved) loadBookings({ emailFilter: saved, tab: 'timeline' })
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])

    const refresh = () => {
        if (activeTab === 'recurring') loadBookingSeries({ emailFilter: isCustomer ? email : undefined })
        else loadBookings({ emailFilter: isCustomer ? email : undefined, tab: activeTab === 'requests' ? 'requests' : 'timeline' })
    }

    // Search/Tutors/Event-types/Show-cancelled are scoped to whichever tab you're looking at —
    // switching tabs starts that tab's filtering fresh rather than carrying over a selection that
    // may not even apply there (e.g. a non-recurring event type picked on Schedule). Date scope
    // isn't touched — Recurring/Requests don't use it, and Timeline should keep whatever
    // day/week/month/range you had.
    const handleTabChange = (tab: BookingsTab) => {
        setActiveTab(tab)
        setFilters(f => ({ ...f, tutorIds: [], eventTypeIds: [], students: [], searchQuery: '', includeCancelled: true }))
        if (tab === 'recurring') loadBookingSeries({ emailFilter: isCustomer ? email : undefined, tutorIds: [], eventTypeIds: [], students: [] })
        else loadBookings({ emailFilter: isCustomer ? email : undefined, tab, tutorIds: [], eventTypeIds: [], students: [], includeCancelled: true })
    }

    const handleScopeChange = (g: Granularity) => {
        setScope(g)
        // Reuses the current anchor (not "now") — switching Day -> Month while looking at a
        // specific day lands on that day's month, not the current month.
        const bounds = periodBounds(g, periodAnchor)
        setFilters(f => ({ ...f, dateFrom: bounds.dateFrom, dateTo: bounds.dateTo }))
        loadBookings({ emailFilter: isCustomer ? email : undefined, tab: 'timeline', isRange: false, dateFrom: bounds.dateFrom, dateTo: bounds.dateTo })
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
            loadBookings({ emailFilter: isCustomer ? email : undefined, tab: 'timeline', isRange: false, dateFrom: pickedFrom, dateTo: pickedFrom })
            return
        }
        if (matchesFullWeek(pickedFrom, pickedTo)) {
            setScope('week')
            setPeriodAnchor(parseLocalDateStr(pickedFrom))
            setFilters(f => ({ ...f, dateFrom: pickedFrom, dateTo: pickedTo }))
            loadBookings({ emailFilter: isCustomer ? email : undefined, tab: 'timeline', isRange: false, dateFrom: pickedFrom, dateTo: pickedTo })
            return
        }
        if (matchesFullMonth(pickedFrom, pickedTo)) {
            setScope('month')
            setPeriodAnchor(parseLocalDateStr(pickedFrom))
            setFilters(f => ({ ...f, dateFrom: pickedFrom, dateTo: pickedTo }))
            loadBookings({ emailFilter: isCustomer ? email : undefined, tab: 'timeline', isRange: false, dateFrom: pickedFrom, dateTo: pickedTo })
            return
        }
        setScope('custom')
        setFilters(f => ({ ...f, dateFrom: pickedFrom, dateTo: pickedTo }))
        loadBookings({ emailFilter: isCustomer ? email : undefined, tab: 'timeline', isRange: true, dateFrom: pickedFrom, dateTo: pickedTo })
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
        loadBookings({ emailFilter: isCustomer ? email : undefined, tab: 'timeline', dateFrom: bounds.dateFrom, dateTo: bounds.dateTo })
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
        loadBookings({ emailFilter: isCustomer ? email : undefined, tab: 'timeline', isRange: false, dateFrom: bounds.dateFrom, dateTo: bounds.dateTo })
    }

    const handleStudentFilterToggle = (value: string) => {
        const next = filters.students.includes(value) ? filters.students.filter(x => x !== value) : [...filters.students, value]
        setFilters(f => ({ ...f, students: next }))
        if (activeTab === 'recurring') { loadBookingSeries({ emailFilter: isCustomer ? email : undefined, students: next }); return }
        loadBookings({ emailFilter: isCustomer ? email : undefined, tab: activeTab === 'requests' ? 'requests' : 'timeline', students: next })
    }

    // Pure display flip — displayed() re-sorts fully every render, so no refetch needed.
    const handleOrderToggle = () => setOrder(o => o === 'asc' ? 'desc' : 'asc')

    const handleLoadMore = () => loadBookings({ emailFilter: isCustomer ? email : undefined, cursor, append: true })

    // Server-side filters now (not client-side) — same reasoning as time range: filtering an
    // already-paginated set client-side can silently hide real matches sitting on unfetched pages.
    const handleTutorFilterToggle = (id: string) => {
        const next = filters.tutorIds.includes(id) ? filters.tutorIds.filter(x => x !== id) : [...filters.tutorIds, id]
        setFilters(f => ({ ...f, tutorIds: next }))
        if (activeTab === 'recurring') { loadBookingSeries({ emailFilter: isCustomer ? email : undefined, tutorIds: next }); return }
        loadBookings({ emailFilter: isCustomer ? email : undefined, tab: activeTab === 'requests' ? 'requests' : 'timeline', tutorIds: next })
    }

    const handleEventTypeFilterToggle = (id: string) => {
        const next = filters.eventTypeIds.includes(id) ? filters.eventTypeIds.filter(x => x !== id) : [...filters.eventTypeIds, id]
        setFilters(f => ({ ...f, eventTypeIds: next }))
        if (activeTab === 'recurring') { loadBookingSeries({ emailFilter: isCustomer ? email : undefined, eventTypeIds: next }); return }
        loadBookings({ emailFilter: isCustomer ? email : undefined, tab: activeTab === 'requests' ? 'requests' : 'timeline', eventTypeIds: next })
    }

    // Recurring tab doesn't refetch here — BookingSeries rows carry no status of their own;
    // SeriesRow reads includeCancelled directly (via RecurringList) and reloads its own
    // occurrences whenever the flag changes.
    const handleIncludeCancelledToggle = () => {
        const next = !filters.includeCancelled
        setFilters(f => ({ ...f, includeCancelled: next }))
        if (activeTab === 'recurring') return
        loadBookings({ emailFilter: isCustomer ? email : undefined, tab: activeTab === 'requests' ? 'requests' : 'timeline', includeCancelled: next })
    }

    const handleEmailSubmit = () => {
        if (!email.trim()) return
        sessionStorage.setItem('customer_email', email.trim())
        setSubmitted(true)
        loadRoster()
        loadBookings({ emailFilter: email.trim(), tab: 'timeline' })
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

    // Tutor/event-type narrowing happens server-side via loadBookingSeries (see get_booking_series) —
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
                            {(isLoadingSeries || isLoadingRoster) && <div className="flex justify-center py-16"><Loader size="sm" /></div>}
                            {!isLoadingSeries && !isLoadingRoster && (
                                <RecurringList
                                    seriesByDay={orderedSeriesByDay}
                                    tutors={tutors}
                                    eventTypes={eventTypes}
                                    isCustomer={true}
                                    includeCancelled={filters.includeCancelled}
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
                                    scope={scope}
                                    periodAnchor={periodAnchor}
                                    dateFrom={filters.dateFrom}
                                    dateTo={filters.dateTo}
                                    onScopeChange={handleScopeChange}
                                    onPeriodShift={handlePeriodShift}
                                    onToday={handleToday}
                                    onCustomSelect={handleCustomSelect}
                                    leftContent={<OrderToggle order={order} onToggle={handleOrderToggle} />}
                                />
                            </div>

                            {(isLoading || isLoadingRoster) && <div className="flex justify-center py-16"><Loader size="sm" /></div>}

                            {!isLoading && !isLoadingRoster && (
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
                                                No bookings found
                                            </p>
                                            <p className="text-xs text-gray-400 mt-1">
                                                {scope === 'custom' ? 'No sessions in this range.' : `No sessions this ${scope}.`}
                                            </p>
                                        </div>
                                    )}

                                    {cursor !== null && (
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
        <div className="h-full flex flex-col">
            <div className="shrink-0">
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
                            </>
                        }
                    />

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
                            tutorOptions={tutorFacetOptions.map(t => ({ value: String(t.id), label: `${t.first_name} ${t.last_name}` }))}
                            tutorSelected={filters.tutorIds}
                            onTutorToggle={handleTutorFilterToggle}
                            // No more manual eventTypes.filter(e => e.recurring) special-case needed —
                            // on the Recurring tab, eventTypeFacetOptions comes from get_booking_series's
                            // facets, which are already recurring-only by construction (a BookingSeries
                            // only ever exists for a recurring=true event type).
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
            {/* spinner */}
            {((activeTab === 'recurring' ? isLoadingSeries : isLoading) || isLoadingRoster) && <div className="flex justify-center py-12"><Loader size="sm" /></div>}

            {/* requests tab */}
            {!isLoading && !isLoadingRoster && activeTab === 'requests' && (
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
            {!isLoadingSeries && !isLoadingRoster && activeTab === 'recurring' && (
                <RecurringList
                    seriesByDay={orderedSeriesByDay}
                    tutors={tutors}
                    eventTypes={eventTypes}
                    isCustomer={isCustomer}
                    includeCancelled={filters.includeCancelled}
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

                    {cursor !== null && (
                        <div className="border-t border-gray-100">
                            <LoadMoreSentinel onVisible={handleLoadMore} loading={isLoadingMore} />
                        </div>
                    )}
                </div>
            )}

            {!isLoading && !isLoadingRoster && activeTab === 'requests' && cursor !== null && (
                <LoadMoreSentinel onVisible={handleLoadMore} loading={isLoadingMore} />
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