import { useState } from 'react'
import { Button, Menu, Select } from '@mantine/core'
import AppModal, { ModalFooter } from './AppModal'
import { IconCalendarEvent, IconRefresh, IconBan, IconTrash, IconUserOff, IconAlertCircle, IconLink, IconShieldCog } from '@tabler/icons-react'
import { useNavigate } from 'react-router-dom'
import type { Booking, BookingLink, BookingType } from './types'
import { formatDate, extractError, attendeeName, bookingPayload } from './utils'
import type { OccurrencePolicyFields } from './utils'
import { BookingPolicyModal } from './PolicyModal'

// All the state/handlers/menu-items/modals behind a booking row's "manage" affordance — shared
// by BookingRow's own dots-menu and SeriesRow's occurrence pills, so both trigger the exact same
// actions (reschedule/no-show/cancel/delete) without duplicating any of this logic.
interface UseBookingActionsOptions {
    booking: Booking
    bookingLink: BookingLink | undefined    // resolved from the roster, which can miss
    bookingLinks: BookingLink[]             // the roster, for reassigning off an archived link
    bookingTypes?: BookingType[]            // the roster, for the type picker
    reloadBookingTypes?: () => void
    onRefresh: (msg: string) => void
    onError: (msg: string) => void
    onReviewRequest?: (booking: Booking) => void
    onBookingUpdated?: (booking: Booking) => void   // replace one row instead of refetching the list
}

export const useBookingActions = ({
    booking,
    bookingLink,
    bookingLinks,
    bookingTypes = [],
    reloadBookingTypes = () => {},
    onRefresh,
    onError,
    onReviewRequest,
    onBookingUpdated,
}: UseBookingActionsOptions) => {
    const navigate = useNavigate()
    const [confirmingDelete, setConfirmingDelete] = useState(false)
    const [confirmingPermanentDelete, setConfirmingPermanentDelete] = useState(false)
    const [confirmingCascadeDelete, setConfirmingCascadeDelete] = useState(false)
    const [isSubmitting, setIsSubmitting] = useState(false)
    const [reassigning, setReassigning] = useState(false)
    const [reassignTarget, setReassignTarget] = useState<string | null>(null)
    const [editingPolicy, setEditingPolicy] = useState(false)

    // The roster includes archived links so existing rows can resolve their source — but an
    // archived link is never a valid target to move a booking onto.
    const reassignOptions = (bookingLinks ?? []).filter(l => l.status !== 'archived')

    const openReassign = () => {
        setReassignTarget(null)
        setReassigning(true)
    }

    // Editing a virtual occurrence materializes it first (resolve_ref), so this row becomes a real
    // exception with its own terms while the rest of the series keeps the series' template.
    const handlePolicySave = async (policy: OccurrencePolicyFields) => {
        setIsSubmitting(true)
        try {
            const res = await fetch(`${import.meta.env.VITE_API_URL}/bookings/${booking.id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ...bookingPayload(booking), ...policy }),
            })
            if (!res.ok) {
                onError(extractError(await res.json(), 'Failed to update policy'))
                return
            }
            const updated = await res.json()
            setEditingPolicy(false)
            // Update this row in place rather than refetching the list — nothing else changed.
            if (onBookingUpdated) onBookingUpdated(updated)
            else onRefresh('Policy updated')
        } catch {
            onError('An unknown error occurred while updating policy')
        } finally {
            setIsSubmitting(false)
        }
    }

    const handleReassign = async () => {
        if (!reassignTarget) return
        setIsSubmitting(true)
        try {
            const res = await fetch(`${import.meta.env.VITE_API_URL}/bookings/${booking.id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ...bookingPayload(booking), booking_link_id: Number(reassignTarget) }),
            })
            if (!res.ok) {
                onError(extractError(await res.json(), 'Failed to reassign booking link'))
                return
            }
            setReassigning(false)
            onRefresh('Booking link reassigned')
        } catch {
            onError('An unknown error occurred while reassigning')
        } finally {
            setIsSubmitting(false)
        }
    }

    const isPast = new Date(booking.start) < new Date()

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

    const handleReclassify = async (bookingTypeId: number | null) => {
        try {
            const res = await fetch(`${import.meta.env.VITE_API_URL}/bookings/${booking.id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ...bookingPayload(booking), booking_type_id: bookingTypeId }),
            })
            if (!res.ok) {
                onError(extractError(await res.json(), 'Failed to change type.'))
                return
            }
            // Patch the one row rather than onRefresh()-ing the whole list. The caller follows up
            // with a silent revalidation, so the facet options still reconcile — the list just
            // never gets replaced by a spinner on the way.
            const updated = await res.json()
            if (onBookingUpdated) onBookingUpdated(updated)
            else onRefresh('Type updated')
        } catch (error) {
            console.error(error)
            onError('Failed to change type.')
        }
    }

    const handleNoShow = async () => {
        try {
            const res = await fetch(`${import.meta.env.VITE_API_URL}/bookings/${booking.id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ...bookingPayload(booking), is_no_show: true }),
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
                        navigate(`/book/${bookingLink?.slug}`, {
                            state: {
                                rescheduleFromId: booking.id,
                                originalStart: booking.start,
                                originalEnd: booking.end,
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
                            navigate(`/book/${bookingLink?.slug}`, {
                                state: {
                                    rescheduleFromId: booking.id,
                                    tutorId: booking.tutor_id,
                                    originalStart: booking.start,
                                    originalEnd: booking.end,
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
            {bookingLink?.status === 'archived' && (
                <Menu.Item leftSection={<IconLink size={14} />} onClick={openReassign}>
                    Reassign booking link
                </Menu.Item>
            )}
            <Menu.Item leftSection={<IconShieldCog size={14} />} disabled={booking.status !== 'confirmed'} onClick={() => setEditingPolicy(true)}>
                Change policy
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
            {/* Standalone only. A cancelled occurrence row is what stops the series regenerating
                that date, so hard-deleting it would just bring the occurrence back. */}
            {!booking.series_id && (
                <Menu.Item leftSection={<IconTrash size={14} />} color="red" onClick={() => setConfirmingPermanentDelete(true)}>
                    Delete permanently
                </Menu.Item>
            )}
        </>
    )

    const modals = (
        <>
            <AppModal opened={reassigning} onClose={() => setReassigning(false)}
                title="Reassign booking link"
                caption="This booking's link was archived, so its scheduling rules no longer apply and it can't be rescheduled. Pointing it at an active link restores that. Nothing else about the booking changes.">
                <Select
                    label="Booking link"
                    placeholder="Pick an active link"
                    data={reassignOptions.map(l => ({ value: String(l.id), label: l.slug }))}
                    value={reassignTarget}
                    onChange={setReassignTarget}
                    searchable
                />
                <ModalFooter>
                    <Button variant="subtle" color="gray" onClick={() => setReassigning(false)}>Cancel</Button>
                    <Button loading={isSubmitting} disabled={!reassignTarget} onClick={handleReassign}>Reassign</Button>
                </ModalFooter>
            </AppModal>

            {/* key flips on open, so the draft re-seeds off the booking and an abandoned edit
                is discarded — without unmounting the modal mid-transition. */}
            <BookingPolicyModal
                key={String(editingPolicy)}
                booking={booking}
                opened={editingPolicy}
                saving={isSubmitting}
                onClose={() => setEditingPolicy(false)}
                onSave={handlePolicySave}
            />

            <AppModal opened={confirmingDelete} onClose={() => setConfirmingDelete(false)}
                title={`Cancel ${attendeeName(booking)}'s booking on ${formatDate(booking.start)}?`}>
                <ModalFooter>
                    <Button variant="subtle" color="gray" onClick={() => setConfirmingDelete(false)}>Keep it</Button>
                    <Button color="red" loading={isSubmitting} onClick={handleDelete}>Cancel booking</Button>
                </ModalFooter>
            </AppModal>

            <AppModal opened={confirmingPermanentDelete} onClose={() => setConfirmingPermanentDelete(false)}
                title={`Permanently delete ${attendeeName(booking)}'s booking?`}
                caption="This cannot be undone. The calendar event will also be removed.">
                <ModalFooter>
                    <Button variant="subtle" color="gray" onClick={() => setConfirmingPermanentDelete(false)}>Keep it</Button>
                    <Button color="red" loading={isSubmitting} onClick={() => handlePermanentDelete()}>Delete permanently</Button>
                </ModalFooter>
            </AppModal>

            <AppModal opened={confirmingCascadeDelete} onClose={() => setConfirmingCascadeDelete(false)}
                title="Delete entire reschedule chain?"
                caption="This booking was created by rescheduling an earlier one. All bookings in the reschedule chain will be permanently deleted.">
                <ModalFooter>
                    <Button variant="subtle" color="gray" onClick={() => setConfirmingCascadeDelete(false)}>Cancel</Button>
                    <Button color="red" loading={isSubmitting} onClick={() => handlePermanentDelete(true)}>Delete all</Button>
                </ModalFooter>
            </AppModal>
        </>
    )

    return { isPast, menuItems, modals, handleReclassify, bookingTypes, reloadBookingTypes }
}
