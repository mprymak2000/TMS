import { useState, useEffect, useRef } from 'react'
import { Loader, Button, Popover, Switch } from '@mantine/core'
import { IconX, IconChevronDown, IconSortAscending, IconSortDescending } from '@tabler/icons-react'
import type { Tutor, BookingLink } from './types'

// Shared toolbar pieces used by every Bookings tab (Schedule/Recurring/Requests) - filters,
// active-filter chips, sort toggle, and infinite-scroll trigger. Pulled out of any one tab's
// own file so nothing has to import "shared" code from a sibling tab component.

export interface BookingFilters {
    tutorIds: string[]
    bookingLinkIds: string[]
    students: string[]
    dateFrom: string | null
    dateTo: string | null
    searchQuery: string
    includeCancelled: boolean
}

export interface LoadErrors {
    bookings?: string
    tutors?: string
    bookingLinks?: string
}

export const PAGE_SIZE = 10  // explicit override for Recurring/Requests; Day/Week/Month (ScheduleTab) omit page_size, backend default applies

// Normal (ascending, dates going down the list) is the quiet default — no border. Inverted
// (descending) is the deviation from default, so it gets a visible border + accent color to
// signal "this is toggled on," same visual language as an active filter chip.
export const OrderToggle = ({ order, onToggle }: { order: 'asc' | 'desc'; onToggle: () => void }) => (
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

// Infinite scroll — no button. Scrolling this sentinel into view triggers the next page; a
// ref (not the `loading` prop itself) guards the observer callback so a burst of intersection
// events during a fast scroll can't fire multiple concurrent loads before state catches up.
export const LoadMoreSentinel = ({ onVisible, loading }: { onVisible: () => void; loading: boolean }) => {
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
export const ActiveFilterChips = ({
    tutorIds,
    bookingLinkIds,
    students,
    tutors,
    bookingLinks,
    includeCancelled,
    onTutorRemove,
    onBookingLinkRemove,
    onStudentRemove,
    onIncludeCancelledRemove,
}: {
    tutorIds: string[]
    bookingLinkIds: string[]
    students: string[]
    tutors: Tutor[]
    bookingLinks: BookingLink[]
    includeCancelled: boolean
    onTutorRemove: (id: string) => void
    onBookingLinkRemove: (id: string) => void
    onStudentRemove: (value: string) => void
    onIncludeCancelledRemove: () => void
}) => {
    if (tutorIds.length === 0 && bookingLinkIds.length === 0 && students.length === 0 && includeCancelled) return null
    return (
        <div className="flex flex-wrap gap-2 mt-3">
            {tutorIds.map(id => (
                <FilterChip
                    key={`tutor-${id}`}
                    label={`Tutor: ${tutors.find(t => String(t.id) === id)?.first_name ?? ''}`}
                    onRemove={() => onTutorRemove(id)}
                />
            ))}
            {bookingLinkIds.map(id => (
                <FilterChip
                    key={`event-${id}`}
                    label={`Link: ${bookingLinks.find(e => String(e.id) === id)?.slug ?? ''}`}
                    onRemove={() => onBookingLinkRemove(id)}
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

export interface FilterOption {
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
// Links expand as accordion sections inside the same panel — never a second popover.
export const FiltersMenu = ({
    tutorOptions,
    tutorSelected,
    onTutorToggle,
    bookingLinkOptions,
    bookingLinkSelected,
    onBookingLinkToggle,
    studentOptions,
    studentSelected,
    onStudentToggle,
    includeCancelled,
    onIncludeCancelledToggle,
}: {
    tutorOptions: FilterOption[]
    tutorSelected: string[]
    onTutorToggle: (value: string) => void
    bookingLinkOptions: FilterOption[]
    bookingLinkSelected: string[]
    onBookingLinkToggle: (value: string) => void
    studentOptions: FilterOption[]
    studentSelected: string[]
    onStudentToggle: (value: string) => void
    includeCancelled: boolean
    onIncludeCancelledToggle: () => void
}) => {
    const [opened, setOpened] = useState(false)
    // Independent toggles, not a single-open accordion — expanding Students shouldn't collapse
    // Tutors if it's already open.
    const [expandedSections, setExpandedSections] = useState<Set<'tutors' | 'bookingLinks' | 'students'>>(new Set())
    const toggleSection = (key: 'tutors' | 'bookingLinks' | 'students') => {
        setExpandedSections(prev => {
            const next = new Set(prev)
            if (next.has(key)) next.delete(key)
            else next.add(key)
            return next
        })
    }
    const activeCount = tutorSelected.length + bookingLinkSelected.length + studentSelected.length + (includeCancelled ? 0 : 1)

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
                    label="Links"
                    options={bookingLinkOptions}
                    selected={bookingLinkSelected}
                    expanded={expandedSections.has('bookingLinks')}
                    onToggleExpand={() => toggleSection('bookingLinks')}
                    onToggleOption={onBookingLinkToggle}
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
