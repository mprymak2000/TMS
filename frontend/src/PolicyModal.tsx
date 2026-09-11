import { Modal, Button } from '@mantine/core'
import PolicyModeField from './PolicyModeField'

// Shared by the booking and series policy editors. They differ only in which columns they edit —
// a booking edits its own cancel/reschedule pair, a series edits the series-level pair, which has
// no notice window — so the caller passes the fields and this owns the chrome.
export interface PolicyField {
    label: string
    mode: string
    noticeMinutes?: number | null
    simple?: boolean
    onChange: (mode: string, noticeMinutes: number | null) => void
}

interface Props {
    opened: boolean
    onClose: () => void
    title: string
    caption: string
    fields: PolicyField[]
    saving: boolean
    onSave: () => void
}

const PolicyModal = ({ opened, onClose, title, caption, fields, saving, onSave }: Props) => (
    <Modal
        opened={opened} onClose={onClose} title={title} centered size="lg" padding="lg"
        styles={{
            title: { fontSize: 22, fontWeight: 600 },
            header: { paddingBottom: 2 },
            body: { paddingTop: 0 },
        }}
    >
        <p className="text-[15px] text-gray-500 mb-6">{caption}</p>
        <div className="divide-y divide-gray-100">
            {fields.map(f => (
                <div key={f.label} className="py-5 first:pt-0 last:pb-0">
                    <PolicyModeField
                        label={f.label}
                        mode={f.mode}
                        noticeMinutes={f.noticeMinutes ?? null}
                        simple={f.simple}
                        onChange={f.onChange}
                    />
                </div>
            ))}
        </div>
        <div className="flex justify-end gap-2 mt-8">
            <Button variant="subtle" color="gray" onClick={onClose}>Cancel</Button>
            <Button loading={saving} onClick={onSave}>Save</Button>
        </div>
    </Modal>
)

export default PolicyModal
