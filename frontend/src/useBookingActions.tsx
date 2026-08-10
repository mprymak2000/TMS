import { useState, useEffect } from 'react'
import { Modal, Button, Menu, TextInput } from '@mantine/core'
import { IconCalendarEvent, IconRefresh, IconPencil, IconBan, IconTrash, IconUserOff, IconAlertCircle } from '@tabler/icons-react'
import { useNavigate } from 'react-router-dom'
import type { Booking, EventType } from './types'
import { formatDate, extractError } from './utils'

interface ContactForm {
    studentEmail: string
    studentPhone: string
    parentEmail:  string
    parentPhone:  string
}

// All the state/handlers/menu-items/modals behind a booking row's "manage" affordance — shared
// by BookingRow's own dots-menu and SeriesRow's occurrence pills, so both trigger the exact same
// actions (reschedule/modify contact/no-show/cancel/delete) without duplicating any of this logic.
export const useBookingActions = (
    booking: Booking,
    eventType: EventType,
    onRefresh: (msg: string) => void,
    onError: (msg: string) => void,
    onReviewRequest?: (booking: Booking) => void,
) => {
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

    const menuItems = (
        <>
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
        </>
    )

    const modals = (
        <>
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

    return { isPast, menuItems, modals }
}
