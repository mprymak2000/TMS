import { useState, useRef, useEffect } from 'react'
import { Loader, Menu } from '@mantine/core'
import { IconChevronDown, IconChevronUp, IconDotsVertical, IconCalendarStats, IconBan, IconArrowBackUp, IconPlus, IconMinus } from '@tabler/icons-react'
import { useNavigate } from 'react-router-dom'
import type { Booking, BookingSeries, Tutor, EventType } from './types'
import { extractError, formatDate, formatShortDate, formatTime, formatUTCTime } from './utils'
import { statusConfig } from './BookingRow'
import { useBookingActions } from './useBookingActions'

// Fallback page size before the container has been measured (first render, pre-layout).
const DEFAULT_PAGE_SIZE = 4

// One occurrence card — demo of a wrapping-grid layout instead of a full-width row: several per
// line, sized to their actual (small) content instead of stretching one item across the whole
// row. Same manage menu as before, same useBookingActions hook. `isNext` flags the chronologically
// first occurrence in the loaded set (occurrences are fetched oldest-first from "now", so index 0
// of the whole accumulated list is always the soonest one).
const OccurrenceCard = ({
    booking,
    eventType,
    expectedTutor,
    tutors,
    isCustomer,
    isNext,
    onRefresh,
    onError,
}: {
    booking: Booking
    eventType: EventType
    expectedTutor: Tutor
    tutors: Tutor[]
    isCustomer: boolean
    isNext: boolean
    onRefresh: (msg: string) => void
    onError: (msg: string) => void
}) => {
    const navigate = useNavigate()
    const { isPast, menuItems, modals } = useBookingActions(booking, eventType, onRefresh, onError)
    const cfg = statusConfig(booking, isPast)
    // Series-bound booking public_ids always encode their own start time as a trailing unix
    // timestamp (`{series_public_id}:{unix_timestamp}`, real or virtual — see CLAUDE.md) — so the
    // predecessor's date can be read straight off rescheduled_from without a second fetch.
    const rescheduledFromDate = booking.rescheduled_from
        ? new Date(Number(booking.rescheduled_from.split(':').pop()) * 1000)
        : null

    // Whether this card's content actually overflows its scroll area — drives a small "more
    // below" indicator so a scrollable card doesn't look identical to a non-scrollable one.
    const scrollRef = useRef<HTMLDivElement>(null)
    const [isScrollable, setIsScrollable] = useState(false)
    useEffect(() => {
        const el = scrollRef.current
        if (!el) return
        setIsScrollable(el.scrollHeight > el.clientHeight)
    }, [booking, isPast])

    // Whether this exception's weekday/tutor differ from the series' norm — decides whether those
    // facts are worth surfacing at all, not what to compare them against for display (we show the
    // new value plainly, not a before/after). Tutor compares against the series' own tutor
    // directly; weekday compares against the predecessor's real original instant (both rendered
    // the same way, viewer-local, so no business-timezone math needed).
    const actualTutor = tutors.find(t => t.id === booking.tutor_id)
    const tutorChanged = !!booking.rescheduled_from && !!actualTutor && actualTutor.id !== expectedTutor.id
    const weekdayChanged = !!rescheduledFromDate && rescheduledFromDate.getDay() !== new Date(booking.start).getDay()

    // isNext wins over the rescheduled colors — "this is the one coming up" is the more useful
    // signal to lead with than its reschedule history. rescheduled_to wins over rescheduled_from
    // if a card is somehow both (chain reschedule) — "this slot is gone" is the more urgent fact
    // than "this slot also happened to receive one."
    const borderClass = isNext
        ? 'border-indigo-400 hover:border-indigo-500'
        : booking.rescheduled_to
        ? 'border-red-300 hover:border-red-400'
        : booking.rescheduled_from
        ? 'border-emerald-300 hover:border-emerald-400'
        : 'border-gray-200 hover:border-gray-300'


    return (
        <div className={`relative flex flex-col justify-between w-full h-[6.5rem] rounded-lg border ${borderClass} px-3 pt-3 pb-2 transition-colors`}>
            {/* All badges share one corner so a card's "state" reads from one consistent spot
                instead of hunting different corners for different facts. */}
            {(isNext || booking.rescheduled_to || booking.rescheduled_from) && (
                <div className="absolute -top-2 left-3 flex items-center gap-1">
                    {isNext && (
                        <span className="bg-indigo-600 text-white text-[10px] font-semibold px-1.5 py-0.5 rounded-full">
                            Next
                        </span>
                    )}
                    {/* rescheduled_from: something was rescheduled TO this slot — it's the new arrival */}
                    {booking.rescheduled_from && (
                        <span className="flex items-center justify-center w-4 h-4 rounded-full bg-emerald-500 text-white">
                            <IconPlus size={10} stroke={3} />
                        </span>
                    )}
                    {/* rescheduled_to: this slot was itself rescheduled away — it's gone/cancelled */}
                    {booking.rescheduled_to && (
                        <span className="flex items-center justify-center w-4 h-4 rounded-full bg-red-500 text-white">
                            <IconMinus size={10} stroke={3} />
                        </span>
                    )}
                </div>
            )}
            {/* min-h-0 lets this shrink below its content size instead of pushing the card
                taller — flex-1 fills the space above the action row, overflow-y-auto scrolls
                internally when a card's exception facts don't all fit, so every card stays the
                same fixed height regardless of how much it has to show. isScrollable renders a
                small chevron in the corner so a scrollable card doesn't look identical to a
                non-scrollable one — otherwise there's no way to tell there's more below. */}
            <div ref={scrollRef} className="thin-scrollbar relative flex-1 min-h-0 overflow-y-auto flex flex-col gap-1.5">
                <div className="flex items-center gap-1.5">
                    <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${cfg.dot}`} />
                    <span className={`text-sm font-medium tabular-nums ${cfg.text}`}>
                        {formatShortDate(booking.start)}
                    </span>
                    {rescheduledFromDate && (
                        <span className="flex items-center gap-1 text-[10px] text-gray-500 shrink-0">
                            <IconArrowBackUp size={11} className="shrink-0" />
                            From {rescheduledFromDate.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                        </span>
                    )}
                </div>
                {booking.request?.status === 'pending' && (
                    <span className={`text-xs font-medium ${isPast ? 'text-red-300' : 'text-amber-500'}`}>Pending</span>
                )}
                {/* Exception facts, shown plainly (no before/after) — the new time always, the new
                    weekday/tutor only when they actually differ from the series' norm. */}
                {booking.rescheduled_from && (
                    <div className="flex flex-col gap-0.5 text-[10px] text-amber-600">
                        <span>{formatTime(booking.start)} – {formatTime(booking.end)}</span>
                        {weekdayChanged && (
                            <span>{new Date(booking.start).toLocaleDateString('en-US', { weekday: 'short' })}</span>
                        )}
                        {tutorChanged && (
                            <span>{actualTutor!.first_name} {actualTutor!.last_name}</span>
                        )}
                    </div>
                )}
                {isScrollable && (
                    <IconChevronDown size={10} className="sticky bottom-0 self-end text-gray-300 pointer-events-none" />
                )}
            </div>
            <div className="flex items-center justify-between gap-1 pt-1">
                <span className={`text-xs px-2 py-0.5 rounded-full w-fit shrink-0 ${cfg.label ? cfg.chip : 'bg-emerald-50 text-emerald-600'}`}>
                    {cfg.label ?? 'Confirmed'}
                </span>
                {isCustomer ? (
                    <button
                        onClick={() => navigate(`/manage-occurrence/${booking.id}`)}
                        className="text-xs text-indigo-500 hover:text-indigo-700 px-2 py-1 rounded-lg hover:bg-indigo-50 transition-colors"
                    >
                        Manage
                    </button>
                ) : (
                    <>
                        <Menu shadow="md" width={210} position="bottom-end">
                            <Menu.Target>
                                <button className="flex items-center justify-center w-7 h-7 rounded-md text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors">
                                    <IconDotsVertical size={16} />
                                </button>
                            </Menu.Target>
                            <Menu.Dropdown>{menuItems}</Menu.Dropdown>
                        </Menu>
                        {modals}
                    </>
                )}
            </div>
        </div>
    )
}

interface SeriesRowProps {
    series: BookingSeries
    tutor: Tutor
    tutors: Tutor[]
    eventType: EventType
    onRefresh: (msg: string) => void
    onError: (msg: string) => void
    onCancelSeries: (seriesId: string) => void
    // Expansion is controlled by the parent list (RecurringList) so only one series can be open
    // at a time — expanding one collapses whichever other row was open, instead of each row
    // managing its own independent open/closed state.
    expanded: boolean
    onToggleExpand: () => void
    isCustomer?: boolean
    includeCancelled?: boolean
}

const SeriesRow = ({ series, tutor, tutors, eventType, onRefresh, onError, onCancelSeries, expanded, onToggleExpand, isCustomer = false, includeCancelled = true }: SeriesRowProps) => {
    const navigate = useNavigate()
    const [loaded, setLoaded] = useState(false)
    const [occurrences, setOccurrences] = useState<Booking[]>([])
    const [page, setPage] = useState(1)
    const [hasMore, setHasMore] = useState(false)
    const [isLoading, setIsLoading] = useState(false)
    const [isLoadingMore, setIsLoadingMore] = useState(false)
    // frozen at mount — a series row only has one pagination lifetime per mount, no tab/scope
    // switching within it, so this stays fixed for the row's whole expanded lifetime
    const boundaryRef = useRef<string>(new Date().toISOString())

    // How many cards fit per row at the current width — measured on the outer wrapper (always
    // mounted, unlike the grid itself which only exists once occurrences have loaded), so it's
    // available in time to size the very first fetch, not just later "Load more" ones. No upper
    // cap on column count — just a floor on card width (CARD_MIN_PX), so however wide the screen
    // gets, cards keep adding columns rather than stretching indefinitely. Used both as the
    // occurrence grid's column count (see gridColumns below, capped further by however many cards
    // actually loaded) and as the page_size sent to the backend, so each page request asks for
    // exactly enough to fill whole rows instead of guessing a fixed number.
    const containerRef = useRef<HTMLDivElement>(null)
    const [fitColumns, setFitColumns] = useState(DEFAULT_PAGE_SIZE)
    useEffect(() => {
        const el = containerRef.current
        if (!el) return
        const CARD_MIN_PX = 216 // 13.5rem
        const GAP_PX = 32 // gap-8
        const H_PADDING_PX = 64 // px-8 on both sides of the grid
        const recompute = () => {
            const available = el.clientWidth - H_PADDING_PX
            const fit = Math.floor((available + GAP_PX) / (CARD_MIN_PX + GAP_PX))
            setFitColumns(Math.max(1, fit))
        }
        recompute()
        const observer = new ResizeObserver(recompute)
        observer.observe(el)
        return () => observer.disconnect()
    }, [])
    // Never more columns than there are cards to fill them — a partial batch shouldn't leave
    // empty trailing grid cells just because the row could technically fit more.
    const gridColumns = Math.max(1, Math.min(fitColumns, occurrences.length || 1))

    const loadOccurrences = async (pageNum: number, append: boolean) => {
        if (append) setIsLoadingMore(true)
        else setIsLoading(true)
        try {
            const includeCancelledParam = includeCancelled ? '&include_cancelled=true' : ''
            const res = await fetch(`${import.meta.env.VITE_API_URL}/bookings/booking-series/${series.id}/occurrences?time_min=${boundaryRef.current}&page=${pageNum}&page_size=${fitColumns}${includeCancelledParam}`)
            if (!res.ok) {
                onError(extractError(await res.json(), 'Failed to load occurrences.'))
                return
            }
            const body = await res.json()
            setOccurrences(prev => append ? [...prev, ...body.items] : body.items)
            setPage(pageNum)
            setHasMore(body.has_more)
            setLoaded(true)
        } catch (error) {
            console.error(error)
            onError('Failed to load occurrences.')
        } finally {
            setIsLoading(false)
            setIsLoadingMore(false)
        }
    }

    // Load on first expand, whichever row triggered it — expanded is now owned by the parent
    // list, so this can flip true either from clicking this row directly or (indirectly) from the
    // single-expansion logic collapsing/opening rows elsewhere.
    useEffect(() => {
        if (expanded && !loaded) loadOccurrences(1, false)
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [expanded])

    // Reload from page 1 whenever the "Show cancelled" filter flips while this row is already
    // loaded — same reasoning as any other filter change, just scoped to this one series' own
    // fetch instead of the parent list's.
    useEffect(() => {
        if (loaded) loadOccurrences(1, false)
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [includeCancelled])

    const timeStr = `${formatUTCTime(series.start_time)} – ${formatUTCTime(series.end_time)}`

    return (
        <div ref={containerRef}>
            <div
                className={`group flex items-center px-5 cursor-pointer hover:bg-gray-50 transition-all ${expanded ? 'py-3' : 'py-1.5'}`}
                onClick={onToggleExpand}
            >
                <div className="w-2 h-2 rounded-full shrink-0 bg-indigo-400" />
                <span className={`flex-1 min-w-0 truncate ml-6 text-indigo-600 tabular-nums transition-all ${expanded ? 'text-base font-medium' : 'text-sm'}`}>
                    {timeStr}
                </span>
                <span className={`flex-1 min-w-0 truncate ml-6 text-gray-800 transition-all ${expanded ? 'text-base font-medium' : 'text-sm'}`}>
                    {tutor.first_name} {tutor.last_name} · {series.student_first} {series.student_last}
                </span>
                <span className="flex-1 min-w-0 truncate ml-6 text-xs text-gray-400">
                    {eventType.name}{series.recur_until ? ` · until ${formatDate(series.recur_until)}` : ''}
                </span>
                <div className={`flex items-center gap-0.5 shrink-0 ml-6 transition-opacity ${expanded ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'}`} onClick={e => e.stopPropagation()}>
                    {isCustomer ? (
                        <button
                            onClick={() => navigate(`/manage-series/${series.id}`)}
                            className="text-xs text-indigo-500 hover:text-indigo-700 px-2 py-1 rounded-lg hover:bg-indigo-50 transition-colors"
                        >
                            Manage series
                        </button>
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
                                            rescheduleSeriesId: series.id,
                                            tutorId: series.tutor_id,
                                            originalDayOfWeek: series.start_day_of_week,
                                            originalStartTime: series.start_time,
                                            studentFirst: series.student_first,
                                            studentLast: series.student_last,
                                            studentEmail: series.student_email,
                                            studentPhone: series.student_phone,
                                            parentEmail: series.parent_email,
                                            parentPhone: series.parent_phone,
                                        }
                                    })}
                                >
                                    Change schedule
                                </Menu.Item>
                                <Menu.Divider />
                                <Menu.Item
                                    leftSection={<IconBan size={14} />}
                                    color="red"
                                    onClick={() => onCancelSeries(series.id)}
                                >
                                    Cancel series
                                </Menu.Item>
                            </Menu.Dropdown>
                        </Menu>
                    )}
                    <button
                        onClick={onToggleExpand}
                        className="flex items-center justify-center w-7 h-7 rounded-md text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors"
                    >
                        {expanded ? <IconChevronUp size={16} /> : <IconChevronDown size={16} />}
                    </button>
                </div>
            </div>

            {expanded && (
                <div className="border-t border-gray-100">
                    {isLoading && <div className="flex justify-center py-6"><Loader size="sm" /></div>}
                    {!isLoading && (
                        // gridColumns is measured on the outer container (see fitColumns above)
                        // rather than pure CSS auto-fit — that's what lets this both fill the full
                        // row width AND never leave empty trailing cells for a partial batch, two
                        // constraints CSS alone can't satisfy simultaneously.
                        <div
                            className="grid gap-8 px-8 pt-8 pb-4"
                            style={{ gridTemplateColumns: `repeat(${gridColumns}, 1fr)` }}
                        >
                            {occurrences.map((b, i) => (
                                <OccurrenceCard
                                    key={b.id}
                                    booking={b}
                                    eventType={eventType}
                                    expectedTutor={tutor}
                                    tutors={tutors}
                                    isCustomer={isCustomer}
                                    isNext={i === 0}
                                    onRefresh={onRefresh}
                                    onError={onError}
                                />
                            ))}
                            {occurrences.length === 0 && (
                                <p className="text-xs text-gray-400 text-center py-6 w-full">No upcoming occurrences.</p>
                            )}
                        </div>
                    )}
                    {hasMore && (
                        <div className="flex justify-center py-3">
                            <button
                                onClick={() => loadOccurrences(page + 1, true)}
                                disabled={isLoadingMore}
                                className="group flex items-center gap-1.5 px-4 py-1.5 rounded-full border border-gray-200 bg-white text-xs font-medium text-gray-600 hover:border-indigo-300 hover:text-indigo-600 hover:bg-indigo-50 transition-colors disabled:opacity-50 disabled:pointer-events-none"
                            >
                                {isLoadingMore ? (
                                    <Loader size={12} />
                                ) : (
                                    <IconChevronDown size={14} className="transition-transform duration-200 group-hover:translate-y-0.5" />
                                )}
                                {isLoadingMore ? 'Loading' : 'Load more'}
                            </button>
                        </div>
                    )}
                </div>
            )}
        </div>
    )
}

export default SeriesRow
