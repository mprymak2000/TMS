import { useState, useRef } from 'react'
import { Button, Loader, Menu } from '@mantine/core'
import { IconChevronDown, IconChevronUp, IconDotsVertical, IconCalendarStats, IconBan, IconRepeat } from '@tabler/icons-react'
import { useNavigate } from 'react-router-dom'
import type { Booking, BookingSeries, Tutor, EventType } from './types'
import { extractError, formatDate, formatUTCTime } from './utils'
import BookingRow from './BookingRow'

const DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
const PAGE_SIZE = 10

interface SeriesRowProps {
    series: BookingSeries
    tutor: Tutor
    eventType: EventType
    onRefresh: (msg: string) => void
    onError: (msg: string) => void
    onCancelSeries: (seriesId: string) => void
    isCustomer?: boolean
}

const SeriesRow = ({ series, tutor, eventType, onRefresh, onError, onCancelSeries, isCustomer = false }: SeriesRowProps) => {
    const navigate = useNavigate()
    const [expanded, setExpanded] = useState(false)
    const [loaded, setLoaded] = useState(false)
    const [occurrences, setOccurrences] = useState<Booking[]>([])
    const [page, setPage] = useState(1)
    const [hasMore, setHasMore] = useState(false)
    const [isLoading, setIsLoading] = useState(false)
    const [isLoadingMore, setIsLoadingMore] = useState(false)
    const [expandedOccurrenceId, setExpandedOccurrenceId] = useState<string | null>(null)
    // frozen at mount — a series row only has one pagination lifetime per mount, no tab/scope
    // switching within it, so this stays fixed for the row's whole expanded lifetime
    const boundaryRef = useRef<string>(new Date().toISOString())

    const loadOccurrences = async (pageNum: number, append: boolean) => {
        if (append) setIsLoadingMore(true)
        else setIsLoading(true)
        try {
            const res = await fetch(`${import.meta.env.VITE_API_URL}/bookings/booking-series/${series.id}/occurrences?time_min=${boundaryRef.current}&page=${pageNum}`)
            if (!res.ok) {
                onError(extractError(await res.json(), 'Failed to load occurrences.'))
                return
            }
            const items: Booking[] = await res.json()
            setOccurrences(prev => append ? [...prev, ...items] : items)
            setPage(pageNum)
            setHasMore(items.length === PAGE_SIZE)
            setLoaded(true)
        } catch (error) {
            console.error(error)
            onError('Failed to load occurrences.')
        } finally {
            setIsLoading(false)
            setIsLoadingMore(false)
        }
    }

    const toggleExpand = () => {
        const next = !expanded
        setExpanded(next)
        if (next && !loaded) loadOccurrences(1, false)
    }

    const dayName = DAY_NAMES[series.start_day_of_week]
    const timeStr = formatUTCTime(series.start_time)

    return (
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 border-l-4 border-l-indigo-400 overflow-hidden mt-4 first:mt-0">
            <div
                className="flex items-center px-5 py-4 gap-4 cursor-pointer hover:bg-gray-50 transition-colors"
                onClick={toggleExpand}
            >
                <div className="w-8 h-8 rounded-lg bg-indigo-50 flex items-center justify-center shrink-0">
                    <IconRepeat size={16} className="text-indigo-500" />
                </div>
                <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-0.5">
                        <span className="font-medium text-gray-800 truncate">
                            {tutor.first_name} {tutor.last_name} · {series.student_first} {series.student_last}
                        </span>
                        <span className="shrink-0 text-xs bg-indigo-50 text-indigo-600 px-2 py-0.5 rounded-full font-medium">
                            {eventType.name}
                        </span>
                    </div>
                    <p className="text-xs text-gray-400">
                        {dayName}s · {timeStr}{series.recur_until ? ` · until ${formatDate(series.recur_until)}` : ''}
                    </p>
                </div>
                <div className="flex items-center gap-0.5 shrink-0" onClick={e => e.stopPropagation()}>
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
                    <span className="flex items-center justify-center w-7 h-7 text-gray-400 pointer-events-none">
                        {expanded ? <IconChevronUp size={16} /> : <IconChevronDown size={16} />}
                    </span>
                </div>
            </div>

            {expanded && (
                <div className="border-t border-gray-100">
                    {isLoading && <div className="flex justify-center py-6"><Loader size="sm" /></div>}
                    {!isLoading && (
                        <div className="divide-y divide-gray-100">
                            {occurrences.map(b => (
                                <BookingRow
                                    key={b.id}
                                    booking={b}
                                    tutor={tutor}
                                    eventType={eventType}
                                    expanded={expandedOccurrenceId === b.id}
                                    onExpand={() => setExpandedOccurrenceId(expandedOccurrenceId === b.id ? null : b.id)}
                                    onRefresh={onRefresh}
                                    onError={onError}
                                    isCustomer={isCustomer}
                                    compact={true}
                                />
                            ))}
                            {occurrences.length === 0 && (
                                <p className="text-xs text-gray-400 text-center py-6">No upcoming occurrences.</p>
                            )}
                        </div>
                    )}
                    {hasMore && (
                        <div className="flex justify-center py-3">
                            <Button variant="default" size="xs" loading={isLoadingMore} onClick={() => loadOccurrences(page + 1, true)}>
                                Load more
                            </Button>
                        </div>
                    )}
                </div>
            )}
        </div>
    )
}

export default SeriesRow
