import { IconCheck, IconX } from '@tabler/icons-react'
import type { Toast as ToastType } from './useToast'

const Toast = ({ toast }: { toast: ToastType | null }) => {
    if (!toast) return null
    const isError = toast.type === 'error'
    return (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 flex items-center gap-2.5 bg-white border border-gray-200 rounded-xl shadow-lg px-4 py-3 text-sm text-gray-700 z-50">
            <span className={`flex items-center justify-center w-5 h-5 rounded-full shrink-0 ${isError ? 'bg-red-100 text-red-600' : 'bg-emerald-100 text-emerald-600'}`}>
                {isError ? <IconX size={12} strokeWidth={3} /> : <IconCheck size={12} strokeWidth={3} />}
            </span>
            {toast.msg}
        </div>
    )
}

export default Toast
