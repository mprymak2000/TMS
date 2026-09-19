import { useState } from 'react'
import { Button } from '@mantine/core'
import AppModal, { ModalFooter } from './AppModal'
import PolicyModeField from './PolicyModeField'
import type { Booking, BookingSeries } from './types'
import type { OccurrencePolicyFields, SeriesPolicyFields } from './utils'

// The two field rows sit on dividers with even breathing room, the same in both dialogs.
const FIELDS = 'divide-y divide-gray-100 [&>*]:py-5 [&>*:first-child]:pt-0 [&>*:last-child]:pb-0'

// Both dialogs own their draft rather than the caller: nothing outside reads a half-edited policy.
// Callers pass a `key` that changes on open, so React discards the old instance and the draft
// re-seeds off the row — no reset effect, while the modal stays mounted long enough to animate out.

export const BookingPolicyModal = ({ booking, opened, saving, onClose, onSave }: {
    booking: Booking
    opened: boolean
    saving: boolean
    onClose: () => void
    onSave: (policy: OccurrencePolicyFields) => void
}) => {
    const [policy, setPolicy] = useState<OccurrencePolicyFields>({
        cancel_mode: booking.cancel_mode,
        cancel_notice_minutes: booking.cancel_notice_minutes,
        reschedule_mode: booking.reschedule_mode,
        reschedule_notice_minutes: booking.reschedule_notice_minutes,
    })

    return (
        <AppModal
            opened={opened}
            onClose={onClose}
            title="Booking reschedule & cancel policy"
            caption="Manage an attendee's permission to cancel and reschedule this booking."
        >
            <div className={FIELDS}>
                <PolicyModeField
                    label="Cancelling"
                    mode={policy.cancel_mode}
                    noticeMinutes={policy.cancel_notice_minutes}
                    onChange={(mode, notice) => setPolicy(p => ({ ...p, cancel_mode: mode, cancel_notice_minutes: notice }))}
                />
                <PolicyModeField
                    label="Rescheduling"
                    mode={policy.reschedule_mode}
                    noticeMinutes={policy.reschedule_notice_minutes}
                    onChange={(mode, notice) => setPolicy(p => ({ ...p, reschedule_mode: mode, reschedule_notice_minutes: notice }))}
                />
            </div>
            <ModalFooter>
                <Button variant="subtle" color="gray" onClick={onClose}>Cancel</Button>
                <Button loading={saving} onClick={() => onSave(policy)}>Save</Button>
            </ModalFooter>
        </AppModal>
    )
}

// `simple` on both rows: ending an arrangement has no obvious instant to measure notice against,
// so at the series level the mode is the whole verdict.
export const SeriesPolicyModal = ({ series, opened, saving, onClose, onSave }: {
    series: BookingSeries
    opened: boolean
    saving: boolean
    onClose: () => void
    onSave: (policy: SeriesPolicyFields) => void
}) => {
    const [policy, setPolicy] = useState<SeriesPolicyFields>({
        series_cancel_mode: series.series_cancel_mode,
        series_reschedule_mode: series.series_reschedule_mode,
    })

    return (
        <AppModal
            opened={opened}
            onClose={onClose}
            title="Series reschedule & cancel policy"
            caption="Manage an attendee's permission to cancel and reschedule all future bookings of the series."
        >
            <div className={FIELDS}>
                <PolicyModeField
                    label="Cancelling"
                    simple
                    mode={policy.series_cancel_mode}
                    noticeMinutes={null}
                    onChange={mode => setPolicy(p => ({ ...p, series_cancel_mode: mode }))}
                />
                <PolicyModeField
                    label="Rescheduling"
                    simple
                    mode={policy.series_reschedule_mode}
                    noticeMinutes={null}
                    onChange={mode => setPolicy(p => ({ ...p, series_reschedule_mode: mode }))}
                />
            </div>
            <ModalFooter>
                <Button variant="subtle" color="gray" onClick={onClose}>Cancel</Button>
                <Button loading={saving} onClick={() => onSave(policy)}>Save</Button>
            </ModalFooter>
        </AppModal>
    )
}
