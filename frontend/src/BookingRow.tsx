import { useState, useEffect } from 'react'
import { Button, Modal, Menu, TextInput } from '@mantine/core'
import { IconChevronDown, IconChevronUp, IconDotsVertical, IconCalendarEvent, IconRefresh, IconPencil, IconBan, IconTrash, IconUserOff, IconAlertCircle } from '@tabler/icons-react'
import { useNavigate } from 'react-router-dom'
import type { Booking, Tutor, EventType } from './types'
import { formatDate, formatTime, extractError, tutorBubbleClass } from './utils'

interface BookingRowProps {
    booking: Booking
    tutor: Tutor
    eventType: EventType
    expanded: boolean
    onExpand: () => void
    onRefresh: (msg: string) => void
    onError: (msg: string) => void
    onReviewRequest?: (booking: Booking) => void
    isCustomer?: boolean
    compact?: boolean
    // Compact mode's leading column shows a time range by default (Schedule's flat list, where
    // day is already established by the enclosing date header, so time is the varying fact).
    // Inside an expanded series, the opposite is true — every occurrence shares the same time,
    // date is what varies — so this swaps that column to show the date instead.
    showDate?: boolean
}

interface ContactForm {
    studentEmail: string
    studentPhone: string
    parentEmail:  string
    parentPhone:  string
}

const statusConfig = (b: Booking) => {
    if (b.is_no_show)             return { dot: 'bg-orange-400', text: 'text-orange-600', label: 'No-show',     chip: 'bg-orange-50 text-orange-500' }
    if (b.status === 'cancelled') return { dot: 'bg-red-400',    text: 'text-red-600',    label: 'Cancelled',   chip: 'bg-red-50 text-red-500' }
    if (b.status === 'rescheduled') return { dot: 'bg-amber-400', text: 'text-amber-600', label: 'Rescheduled', chip: 'bg-amber-50 text-amber-600' }
    return { dot: 'bg-emerald-400', text: 'text-emerald-600', label: null, chip: '' }
}

const BookingRow = ({ booking, tutor, eventType, expanded, onExpand, onRefresh, onError, onReviewRequest, isCustomer = false, compact = false, showDate = false }: BookingRowProps) => {
    const navigate = useNavigate()
    const [confirmingDelete, setConfirmingDelete] = useState(false)
    const [confirmingPermanentDelete, setConfirmingPermanentDelete] = useState(false)
    const [confirmingCascadeDelete, setConfirmingCascadeDelete] = useState(false)
    const [editingContact, setEditingContact] = useState(false)
    const [contact, setContact] = useState<ContactForm>({
        studentEmail: booking.student_email ?? '',
        studentPhone: booking.student_phone ?? '',
        parentEmail:  booking.parent_email  ?? '',
        parentPhone:  booking.parent_phone  ?? '',
    })
    const [contactError, setContactError]   = useState<string | null>(null)
    const [contactSaving, setContactSaving] = useState(false)
    const [isSubmitting, setIsSubmitting] = useState(false)

    const isPast = new Date(booking.start) < new Date()
    // Cancelled/rescheduled/no-show already have their own distinct color regardless of time —
    // only a plain confirmed booking needs a separate "this already happened" signal.
    const isPastConfirmed = isPast && booking.status === 'confirmed' && !booking.is_no_show

    const isDirty =
        contact.studentEmail !== (booking.student_email ?? '') ||
        contact.studentPhone !== (booking.student_phone ?? '') ||
        contact.parentEmail  !== (booking.parent_email  ?? '') ||
        contact.parentPhone  !== (booking.parent_phone  ?? '')

    useEffect(() => {
        if (editingContact) {
            setContact({
                studentEmail: booking.student_email ?? '',
                studentPhone: booking.student_phone ?? '',
                parentEmail:  booking.parent_email  ?? '',
                parentPhone:  booking.parent_phone  ?? '',
            })
            setContactError(null)
        }
    }, [editingContact])

    const handleDelete = async () => {
        setIsSubmitting(true)
        try {
            const res = await fetch(`${import.meta.env.VITE_API_URL}/bookings/${booking.id}`, { method: 'DELETE' })
            if (!res.ok) {
                onError(extractError(await res.json(), 'Failed to cancel booking.'))
                setConfirmingDelete(false)
                return
            }
            setConfirmingDelete(false)
            onRefresh('Booking cancelled')
        } catch (error) {
            console.error(error)
            onError('Failed to cancel booking.')
            setConfirmingDelete(false)
        } finally {
            setIsSubmitting(false)
        }
    }

    // Two-step cascade pattern: first call (cascade=false) returns 409 if a predecessor exists,
    // which triggers the cascade confirm modal. User confirms → second call (cascade=true) walks
    // and deletes the full predecessor chain.
    const handlePermanentDelete = async (cascade = false) => {
        setIsSubmitting(true)
        try {
            const url = `${import.meta.env.VITE_API_URL}/bookings/${booking.id}/permanent${cascade ? '?cascade=true' : ''}`
            const res = await fetch(url, { method: 'DELETE' })
            if (res.status === 409) {
                setConfirmingPermanentDelete(false)
                setConfirmingCascadeDelete(true)
                return
            }
            if (!res.ok) {
                onError(extractError(await res.json(), 'Failed to permanently delete booking.'))
                setConfirmingPermanentDelete(false)
                return
            }
            setConfirmingPermanentDelete(false)
            setConfirmingCascadeDelete(false)
            onRefresh('Booking deleted')
        } catch (error) {
            console.error(error)
            onError('Failed to permanently delete booking.')
            setConfirmingPermanentDelete(false)
            setConfirmingCascadeDelete(false)
        } finally {
            setIsSubmitting(false)
        }
    }

    const buildPayload = () => ({
        student_first: booking.student_first,
        student_last:  booking.student_last,
        student_email: contact.studentEmail || null,
        student_phone: contact.studentPhone || null,
        parent_email:  contact.parentEmail  || null,
        parent_phone:  contact.parentPhone  || null,
        is_no_show:    booking.is_no_show,
    })

    const handleNoShow = async () => {
        try {
            const res = await fetch(`${import.meta.env.VITE_API_URL}/bookings/${booking.id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ...buildPayload(), is_no_show: true }),
            })
            if (!res.ok) {
                onError(extractError(await res.json(), 'Failed to mark as no-show.'))
                return
            }
            onRefresh('Marked as no-show')
        } catch (error) {
            console.error(error)
            onError('Failed to mark as no-show.')
        }
    }

    const handleSaveContact = async () => {
        const hasEmail = contact.studentEmail || contact.parentEmail
        const hasPhone = contact.studentPhone || contact.parentPhone
        if (!hasEmail || !hasPhone) {
            setContactError('At least one email and one phone number are required.')
            return
        }
        setContactSaving(true)
        try {
            const res = await fetch(`${import.meta.env.VITE_API_URL}/bookings/${booking.id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(buildPayload()),
            })
            if (!res.ok) {
                setContactError(extractError(await res.json(), 'Failed to update contact info.'))
                return
            }
            setEditingContact(false)
            onRefresh('Contact updated')
        } catch (error) {
            console.error(error)
            setContactError('Failed to update contact info.')
        } finally {
            setContactSaving(false)
        }
    }

    const status = statusConfig(booking)
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
            <Menu.Dropdown>
                {booking.request?.status === 'pending' && (
                    <>
                        <Menu.Item
                            leftSection={<IconAlertCircle size={14} />}
                            color="amber"
                            onClick={() => onReviewRequest?.(booking)}
                        >
                            Review request
                        </Menu.Item>
                        <Menu.Divider />
                    </>
                )}
                {booking.series_id !== null ? (
                    <Menu.Item
                        leftSection={<IconCalendarEvent size={14} />}
                        disabled={booking.status !== 'confirmed'}
                        onClick={() => {
                            navigate(`/book/${eventType.id}`, {
                                state: {
                                    rescheduleFromId: booking.id,
                                    originalStart: booking.start,
                                    originalEnd: booking.end,
                                    studentFirst: booking.student_first,
                                    studentLast:  booking.student_last,
                                    studentEmail: booking.student_email,
                                    studentPhone: booking.student_phone,
                                    parentEmail:  booking.parent_email,
                                    parentPhone:  booking.parent_phone,
                                }
                            })
                        }}
                    >
                        Reschedule booking
                    </Menu.Item>
                ) : (
                    <>
                        <Menu.Item
                            leftSection={<IconCalendarEvent size={14} />}
                            disabled={booking.status !== 'confirmed'}
                            onClick={() => {
                                navigate(`/book/${eventType.id}`, {
                                    state: {
                                        rescheduleFromId: booking.id,
                                        tutorId: booking.tutor_id,
                                        originalStart: booking.start,
                                        originalEnd: booking.end,
                                        studentFirst: booking.student_first,
                                        studentLast:  booking.student_last,
                                        studentEmail: booking.student_email,
                                        studentPhone: booking.student_phone,
                                        parentEmail:  booking.parent_email,
                                        parentPhone:  booking.parent_phone,
                                    }
                                })
                            }}
                        >
                            Reschedule
                        </Menu.Item>
                        <Menu.Item leftSection={<IconRefresh size={14} />} disabled>
                            Request reschedule
                        </Menu.Item>
                    </>
                )}
                <Menu.Item leftSection={<IconPencil size={14} />} disabled={booking.status !== 'confirmed'} onClick={() => setEditingContact(true)}>
                    Modify contact
                </Menu.Item>
                <Menu.Item leftSection={<IconUserOff size={14} />} color="orange" disabled={booking.status !== 'confirmed'} onClick={handleNoShow}>
                    Mark as no-show
                </Menu.Item>
                <Menu.Divider />
                <Menu.Item
                    leftSection={<IconBan size={14} />}
                    color="red"
                    disabled={booking.status !== 'confirmed'}
                    onClick={() => setConfirmingDelete(true)}
                >
                    {isPast ? 'Mark as cancelled' : 'Cancel booking'}
                </Menu.Item>
                <Menu.Item leftSection={<IconTrash size={14} />} color="red" onClick={() => setConfirmingPermanentDelete(true)}>
                    Delete permanently
                </Menu.Item>
            </Menu.Dropdown>
        </Menu>
    )

    const actions = (
        <>
            <button
                className="flex items-center justify-center w-7 h-7 rounded-md text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors"
                onClick={onExpand}
            >
                {expanded ? <IconChevronUp size={16} /> : <IconChevronDown size={16} />}
            </button>
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
                <div className={`group flex items-center px-5 py-1.5 hover:bg-gray-50/60 transition-colors ${expanded ? 'bg-gray-50/60' : ''}`}>
                    <div className={`w-2 h-2 rounded-full shrink-0 ${isPastConfirmed ? 'bg-gray-300' : status.dot}`} />
                    <span className={`w-40 shrink-0 ml-3 text-sm tabular-nums ${isPastConfirmed ? 'text-gray-400' : status.text}`}>
                        {showDate
                            ? new Date(booking.start).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
                            : `${formatTime(booking.start)} – ${formatTime(booking.end)}`}
                    </span>
                    <span className={`w-56 shrink-0 truncate ml-5 text-sm ${isPastConfirmed ? 'text-gray-400' : 'text-gray-800'}`}>
                        {tutor.first_name} {tutor.last_name} · {booking.student_first} {booking.student_last}
                        {status.label && <span className="ml-1.5 text-xs text-gray-400">· {status.label}</span>}
                        {booking.request?.status === 'pending' && <span className="ml-1.5 text-xs text-amber-500 font-medium">· Pending</span>}
                    </span>
                    <span className="w-40 shrink-0 truncate ml-5 text-xs text-gray-400">
                        {eventType.name}
                    </span>
                    <div className="flex-1" />
                    <div className={`flex items-center gap-0.5 shrink-0 ml-3 transition-opacity ${expanded ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'}`}>
                        {actions}
                    </div>
                </div>
            ) : (
                <div className="flex items-center gap-4 px-5 py-4">

                    {/* status dot */}
                    <div className={`w-2 h-2 rounded-full shrink-0 ${status.dot}`} />

                    {/* date/time block */}
                    <div className="w-20 shrink-0 text-center leading-tight">
                        <div className="text-[10px] font-semibold text-gray-400 uppercase tracking-wide">{dayName}, {monthStr} {dayNum}</div>
                        <div className="text-sm font-bold text-gray-800 mt-0.5">{formatTime(booking.start)}</div>
                        <div className="text-[10px] text-gray-400 mt-0.5">{formatTime(booking.end)}</div>
                    </div>

                    {/* tutor + student + event info */}
                    <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-1.5 flex-wrap">
                            <span className="font-medium text-gray-800">
                                {tutor.first_name} {tutor.last_name} · {booking.student_first} {booking.student_last}
                            </span>
                            {status.label && (
                                <span className={`text-xs px-2 py-0.5 rounded-full ${status.chip}`}>{status.label}</span>
                            )}
                            {booking.request?.status === 'pending' && (
                                <span className="text-xs bg-amber-100 text-amber-700 border border-amber-300 px-2 py-0.5 rounded-full font-medium">Pending</span>
                            )}
                        </div>
                        <div className="text-xs text-gray-400 mt-0.5">
                            {eventType.name}
                        </div>
                    </div>

                    {/* tutor bubble */}
                    <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold shrink-0 ${tutorBubbleClass(tutor)}`}>
                        {tutor.first_name[0]}{tutor.last_name[0]}
                    </div>

                    {/* expand + actions */}
                    <div className="flex items-center gap-0.5 shrink-0">
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
            <Modal opened={confirmingDelete} onClose={() => setConfirmingDelete(false)}
                title={`Cancel ${booking.student_first}'s booking on ${formatDate(booking.start)}?`} centered size="sm">
                <div className="flex justify-end gap-2">
                    <Button variant="default" onClick={() => setConfirmingDelete(false)}>Keep it</Button>
                    <Button color="red" loading={isSubmitting} onClick={handleDelete}>Cancel booking</Button>
                </div>
            </Modal>

            <Modal opened={confirmingPermanentDelete} onClose={() => setConfirmingPermanentDelete(false)}
                title={`Permanently delete ${booking.student_first}'s booking?`} centered size="sm">
                <p className="text-sm text-gray-600 mb-4">This cannot be undone. The calendar event will also be removed.</p>
                <div className="flex justify-end gap-2">
                    <Button variant="default" onClick={() => setConfirmingPermanentDelete(false)}>Keep it</Button>
                    <Button color="red" loading={isSubmitting} onClick={() => handlePermanentDelete()}>Delete permanently</Button>
                </div>
            </Modal>

            <Modal opened={confirmingCascadeDelete} onClose={() => setConfirmingCascadeDelete(false)}
                title="Delete entire reschedule chain?" centered size="sm">
                <p className="text-sm text-gray-600 mb-4">
                    This booking was created by rescheduling an earlier one. All bookings in the reschedule chain will be permanently deleted.
                </p>
                <div className="flex justify-end gap-2">
                    <Button variant="default" onClick={() => setConfirmingCascadeDelete(false)}>Cancel</Button>
                    <Button color="red" loading={isSubmitting} onClick={() => handlePermanentDelete(true)}>Delete all</Button>
                </div>
            </Modal>

            <Modal opened={editingContact} onClose={() => setEditingContact(false)}
                title="Modify contact info" centered size="sm">
                <div className="flex flex-col gap-3">
                    <TextInput label="Student email" value={contact.studentEmail}
                        onChange={e => { setContact(c => ({ ...c, studentEmail: e.target.value })); setContactError(null) }} />
                    <TextInput label="Student phone" value={contact.studentPhone}
                        onChange={e => { setContact(c => ({ ...c, studentPhone: e.target.value })); setContactError(null) }} />
                    <TextInput label="Parent email" value={contact.parentEmail}
                        onChange={e => { setContact(c => ({ ...c, parentEmail: e.target.value })); setContactError(null) }} />
                    <TextInput label="Parent phone" value={contact.parentPhone}
                        onChange={e => { setContact(c => ({ ...c, parentPhone: e.target.value })); setContactError(null) }} />
                    {contactError && <p className="text-sm text-red-500">{contactError}</p>}
                    <div className="flex justify-end gap-2 mt-1">
                        <Button variant="default" onClick={() => setEditingContact(false)}>Cancel</Button>
                        <Button loading={contactSaving} disabled={!isDirty} onClick={handleSaveContact}>Save</Button>
                    </div>
                </div>
            </Modal>
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
