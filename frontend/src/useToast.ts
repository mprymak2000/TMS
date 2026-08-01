import { useState, useRef } from 'react'

export interface Toast {
    msg: string
    type: 'success' | 'error'
}

export const useToast = (duration = 5000) => {
    const [toast, setToast] = useState<Toast | null>(null)
    const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

    const showToast = (msg: string, type: 'success' | 'error' = 'success') => {
        if (timer.current) clearTimeout(timer.current)
        setToast({ msg, type })
        timer.current = setTimeout(() => setToast(null), duration)
    }

    return { toast, showToast }
}
