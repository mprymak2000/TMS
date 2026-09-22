import type { ReactNode } from 'react'
import { IconX } from '@tabler/icons-react'

interface Props {
    open: boolean
    onClose: () => void
    title: ReactNode
    children: ReactNode
    footer?: ReactNode
}

// Floats over the right edge of the content shell, full height, header included (the Cal.com
// shape) rather than reflowing the list, so the rows keep their place. Fixed to the viewport with
// the same 12px inset as the shell's frame, so it reads as a second card on the dark background.
// Knows nothing about what it holds — the bookings panel reuses it.
const SidePanel = ({ open, onClose, title, children, footer }: Props) => (
    <div
        className={`fixed top-3 bottom-3 right-3 w-[420px] bg-white border border-gray-200 rounded-2xl shadow-2xl
                    flex flex-col transition-transform duration-200
                    ${open ? 'translate-x-0' : 'translate-x-[calc(100%+1rem)]'}`}
        aria-hidden={!open}
    >
        <div className="flex items-start justify-between gap-4 px-6 pt-6 pb-4 border-b border-gray-100">
            <div className="min-w-0 flex-1">{title}</div>
            <button
                onClick={onClose}
                className="shrink-0 p-1.5 -mr-1.5 rounded-md text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors"
                aria-label="Close"
            >
                <IconX size={18} />
            </button>
        </div>
        <div className="flex-1 overflow-y-auto px-6 py-5">{children}</div>
        {footer && <div className="px-6 py-4 border-t border-gray-100">{footer}</div>}
    </div>
)

export default SidePanel
