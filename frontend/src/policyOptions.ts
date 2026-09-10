// Cancel/reschedule modes, shared by the link editor and the per-row policy editor.
// Mirrors _VALID_MODES / _SERIES_MODES in schemas.py.
export const WINDOW_MODES = ['auto_window_block', 'auto_window_request', 'request_window']

export const CANCEL_MODE_OPTIONS = [
    { value: 'auto', label: 'Always allowed' },
    { value: 'blocked', label: 'Not allowed' },
    { value: 'request', label: 'Request only' },
    { value: 'auto_window_block', label: 'Window - allow or block' },
    { value: 'auto_window_request', label: 'Window - allow or request' },
    { value: 'request_window', label: 'Window - request or block' },
]

// No notice window at series level, so the mode is already the verdict.
export const SERIES_MODE_OPTIONS = [
    { value: 'auto', label: 'Always allowed' },
    { value: 'blocked', label: 'Not allowed' },
    { value: 'request', label: 'Request only' },
]
