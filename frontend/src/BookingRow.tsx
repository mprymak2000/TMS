import { Menu } from '@mantine/core'
import { IconDotsVertical, IconRepeat } from '@tabler/icons-react'
import { useNavigate } from 'react-router-dom'
import type { Booking, Tutor, BookingLink, BookingType } from './types'
import { formatTime, tutorBubbleClass, tutorInitials } from './utils'
import { useBookingActions } from './useBookingActions'
import BookingTypePicker from './BookingTypePicker'

interface BookingRowProps {
    booking: Booking
    // Resolved by `.find()` against the roster, which can miss — model that rather than asserting.
    tutor: Tutor | undefined
    bookingLink: BookingLink | undefined
    bookingType: BookingType | undefined
    bookingTypes: BookingType[]          // roster, for the picker
    reloadBookingTypes: () => void
    bookingLinks: BookingLink[]
    expanded: boolean
    onExpand: () => void
    onRefresh: (msg: string) => void
    onError: (msg: string) => void
    onReviewRequest?: (booking: Booking) => void
    onBookingUpdated?: (booking: Booking) => void
    isCustomer?: boolean
    compact?: boolean
}

// isPast only dulls a status once its time has actually gone by — a rescheduled/cancelled row
// whose original slot is still upcoming reads as "moved/removed" (full color), not "already
// handled" (dulled), same rule a plain confirmed booking already followed.
export const statusConfig = (b: Booking, isPast: boolean) => {
    if (b.is_no_show) return { dot: 'bg-orange-400', text: 'text-orange-600', name: 'text-gray-800', label: 'No-show', chip: 'bg-orange-50 text-orange-500' }
    if (b.status === 'cancelled' || b.status === 'rescheduled') {
        const label = b.status === 'cancelled' ? 'Cancelled' : 'Rescheduled'
        if (isPast) return { dot: 'bg-red-500/40', text: 'text-red-500/70', name: 'text-gray-400', label, chip: 'bg-red-50 text-red-500/70' }
        return { dot: 'bg-red-400', text: 'text-red-600', name: 'text-gray-800', label, chip: 'bg-red-50 text-red-500' }
    }
    if (isPast) return { dot: 'bg-gray-300', text: 'text-gray-400', name: 'text-gray-400', label: null, chip: '' }
    return { dot: 'bg-emerald-400', text: 'text-emerald-600', name: 'text-gray-800', label: null, chip: '' }
}

const BookingRow = ({ booking, tutor, bookingLink, bookingType, bookingTypes, reloadBookingTypes, bookingLinks, expanded, onExpand, onRefresh, onError, onReviewRequest, onBookingUpdated, isCustomer = false, compact = false }: BookingRowProps) => {
    const navigate = useNavigate()
    const { isPast, menuItems, modals, handleReclassify } = useBookingActions({
        booking, bookingLink, bookingLinks, bookingTypes, reloadBookingTypes,
        onRefresh, onError, onReviewRequest, onBookingUpdated,
    })
    const status = statusConfig(booking, isPast)
    const startDate = new Date(booking.start)
    const dayName = startDate.toLocaleDateString('en-US', { weekday: 'short' })
    const monthStr = startDate.toLocaleDateString('en-US', { month: 'short' })
    const dayNum = startDate.getDate()

    const rowMenu = (
        <Menu shadow="md" width={210} position="bottom-end">
            <Menu.Target>
                <button className="flex items-center justify-center w-7 h-7 rounded-md text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors">
                    <IconDotsVertical size={16} />
                </button>
            </Menu.Target>
            <Menu.Dropdown>{menuItems}</Menu.Dropdown>
        </Menu>
    )

    const actions = (
        <>
            {isCustomer ? (
                booking.id && (
                    <button
                        onClick={() => navigate(`/manage-occurrence/${booking.id}`)}
                        className="text-xs text-indigo-500 hover:text-indigo-700 px-2 py-1 rounded-lg hover:bg-indigo-50 transition-colors"
                    >
                        Manage
                    </button>
                )
            ) : rowMenu}
        </>
    )

    const inner = (
        <>
            {/* main row */}
            {compact ? (
                // Minimal agenda-line style — dot, time range, one-line title. Actions only
                // reveal on hover (or while expanded) to keep the resting state quiet; the
                // day grouping itself lives one level up (SeriesRow's own date header).
                <div
                    className={`group flex items-center px-5 py-1.5 cursor-pointer hover:bg-gray-50/60 transition-colors ${expanded ? 'bg-gray-50/60' : ''}`}
                    onClick={onExpand}
                >
                    <div className={`w-2 h-2 rounded-full shrink-0 ${status.dot}`} />
                    <span className={`flex-1 min-w-0 truncate ml-6 text-sm tabular-nums ${status.text}`}>
                        {formatTime(booking.start)} – {formatTime(booking.end)}
                        {booking.series_id && (
                            <IconRepeat size={13} stroke={2.2} className="inline-block ml-1.5 -mt-0.5 text-gray-400" aria-label="Recurring" />
                        )}
                    </span>
                    <span className={`flex-1 min-w-0 truncate ml-6 text-sm ${status.name}`}>
                        {tutor ? `${tutor.first_name} ${tutor.last_name}` : '—'} · {booking.student_first} {booking.student_last}
                    </span>
                    <span className="flex-1 min-w-0 truncate ml-6 text-xs">
                        {status.label && <span className="text-gray-400">{status.label}</span>}
                        {status.label && booking.request?.status === 'pending' && <span className="text-gray-300"> · </span>}
                        {booking.request?.status === 'pending' && (
                            <span className={`font-medium ${isPast ? 'text-red-300' : 'text-amber-500'}`}>Pending</span>
                        )}
                    </span>
                    {/* Kind and source get their own columns. Kind is editable in place — bare
                        until the row is hovered, so the list doesn't read as a row of inputs. */}
                    <span className="flex-1 min-w-0 ml-6 text-xs text-gray-500">
                        {isCustomer ? (
                            bookingType && (
                                <>
                                    <span
                                        className="inline-block w-2 h-2 rounded-full mr-1.5 align-middle shrink-0 border border-black/5"
                                        style={{ background: bookingType.color ?? '#d1d5db' }}
                                    />
                                    {bookingType.label}
                                </>
                            )
                        ) : (
                            <BookingTypePicker
                                variant="inline"
                                value={booking.booking_type_id}
                                onChange={handleReclassify}
                                types={bookingTypes}
                                onTypesChanged={reloadBookingTypes}
                                onError={onError}
                            />
                        )}
                    </span>
                    <span className="flex-1 min-w-0 truncate ml-6 text-xs text-gray-400">
                        {bookingLink?.slug}
                    </span>
                    <div
                        className={`flex items-center gap-0.5 shrink-0 ml-6 transition-opacity ${expanded ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'}`}
                        onClick={e => e.stopPropagation()}
                    >
                        {actions}
                    </div>
                </div>
            ) : (
                <div className="flex items-center gap-4 px-5 py-4 cursor-pointer" onClick={onExpand}>

                    {/* status dot */}
                    <div className={`w-2 h-2 rounded-full shrink-0 ${status.dot}`} />

                    {/* date/time block */}
                    <div className="w-20 shrink-0 text-center leading-tight">
                        <div className="text-[10px] font-semibold text-gray-400 uppercase tracking-wide">{dayName}, {monthStr} {dayNum}</div>
                        <div className="text-sm font-bold text-gray-800 mt-0.5">
                            {formatTime(booking.start)}
                            {booking.series_id && (
                                <IconRepeat size={13} stroke={2.2} className="inline-block ml-1 -mt-0.5 text-gray-400" aria-label="Recurring" />
                            )}
                        </div>
                        <div className="text-[10px] text-gray-400 mt-0.5">{formatTime(booking.end)}</div>
                    </div>

                    {/* tutor + student + event info */}
                    <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-1.5 flex-wrap">
                            <span className="font-medium text-gray-800">
                                {tutor ? `${tutor.first_name} ${tutor.last_name}` : '—'} · {booking.student_first} {booking.student_last}
                            </span>
                            {status.label && (
                                <span className={`text-xs px-2 py-0.5 rounded-full ${status.chip}`}>{status.label}</span>
                            )}
                            {booking.request?.status === 'pending' && (
                                <span className={`text-xs px-2 py-0.5 rounded-full font-medium border ${isPast ? 'bg-red-50 text-red-300 border-red-100' : 'bg-amber-100 text-amber-700 border-amber-300'}`}>Pending</span>
                            )}
                        </div>
                        <div className="text-xs text-gray-400 mt-0.5">
                            {bookingType && (
                                <>
                                    <span
                                        className="inline-block w-2 h-2 rounded-full mr-1.5 align-middle border border-black/5"
                                        style={{ background: bookingType.color ?? '#d1d5db' }}
                                    />
                                    <span className="text-gray-500">{bookingType.label}</span>
                                    <span className="text-gray-300"> · </span>
                                </>
                            )}
                            {bookingLink?.slug}
                        </div>
                    </div>

                    {/* tutor bubble */}
                    <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold shrink-0 ${tutor ? tutorBubbleClass(tutor) : 'bg-gray-100 text-gray-400'}`}>
                        {tutor ? tutorInitials(tutor) : '?'}
                    </div>

                    <div className="flex items-center gap-0.5 shrink-0" onClick={e => e.stopPropagation()}>
                        {actions}
                    </div>
                </div>
            )}

            {/* expanded: contact info — plain aligned label/value rows, matching the compact
                row's quiet typography rather than a boxed, uppercase-labeled grid. */}
            {expanded && (
                <div className="border-t border-gray-100 px-5 py-3 flex flex-col gap-1.5">
                    {booking.student_email && (
                        <div className="flex items-baseline gap-2 text-sm">
                            <span className="w-32 shrink-0 text-gray-400">Student email</span>
                            <span className="text-gray-700">{booking.student_email}</span>
                        </div>
                    )}
                    {booking.student_phone && (
                        <div className="flex items-baseline gap-2 text-sm">
                            <span className="w-32 shrink-0 text-gray-400">Student phone</span>
                            <span className="text-gray-700">{booking.student_phone}</span>
                        </div>
                    )}
                    {booking.parent_email && (
                        <div className="flex items-baseline gap-2 text-sm">
                            <span className="w-32 shrink-0 text-gray-400">Parent email</span>
                            <span className="text-gray-700">{booking.parent_email}</span>
                        </div>
                    )}
                    {booking.parent_phone && (
                        <div className="flex items-baseline gap-2 text-sm">
                            <span className="w-32 shrink-0 text-gray-400">Parent phone</span>
                            <span className="text-gray-700">{booking.parent_phone}</span>
                        </div>
                    )}
                </div>
            )}

            {/* modals */}
            {modals}
        </>
    )

    if (compact) return <div>{inner}</div>

    const cardAccent =
        booking.is_no_show       ? 'border-l-orange-400' :
        booking.status === 'cancelled'   ? 'border-l-red-400'    :
        booking.status === 'rescheduled' ? 'border-l-amber-400'  :
        'border-l-indigo-400'

    return (
        <div className={`bg-white border border-gray-200 border-l-4 ${cardAccent} shadow-sm rounded-xl overflow-hidden`}>
            {inner}
        </div>
    )
}

export default BookingRow
