import type { ReactNode } from 'react'
import { Modal } from '@mantine/core'

interface Props {
    opened: boolean
    onClose: () => void
    title: string
    // The line under the heading. Optional — a short confirm ("Delete X?") often needs no second
    // sentence, and an absent one shouldn't leave a gap.
    caption?: string
    size?: string
    children: ReactNode
}

// Every overlay goes through here so headings and captions can't drift apart between pages.
// Mantine's default title is body-sized; these overrides give it the weight of a real dialog
// heading and pull the caption up underneath it.
// Defaults to lg — the policy dialogs' width, which reads as a real dialog rather than a cramped
// alert. A confirm with two buttons is still better with room than squeezed into sm.
//
// Padding is deliberately generous (32px, past Mantine's own lg) so content never runs to the
// edge, and the bottom carries extra so a footer row doesn't sit flush against the frame.
const AppModal = ({ opened, onClose, title, caption, size = 'lg', children }: Props) => (
    <Modal
        opened={opened} onClose={onClose} title={title} centered size={size}
        styles={{
            content: { borderRadius: 16 },
            header: { padding: '28px 32px 2px' },
            title: { fontSize: 22, fontWeight: 600, lineHeight: 1.25 },
            body: { padding: '0 32px 32px' },
        }}
    >
        {caption && <p className="text-[15px] leading-relaxed text-gray-500 mb-7">{caption}</p>}
        {children}
    </Modal>
)

// Action row. Its only job is the gap above it — button labels and colors differ per dialog, but
// the distance from the content shouldn't.
export const ModalFooter = ({ children }: { children: ReactNode }) => (
    <div className="flex justify-end gap-2 mt-8">{children}</div>
)

export default AppModal
