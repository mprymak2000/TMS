import { useState } from 'react'
import { Button, Modal, Popover, TextInput } from '@mantine/core'
import { IconCheck, IconPencil, IconPlus, IconTrash, IconX, IconChevronDown } from '@tabler/icons-react'
import type { BookingType } from './types'
import { extractError } from './utils'

// Fixed palette rather than a free colour picker — these are scanned at a glance in a list, so they
// need to stay distinguishable from each other.
const PALETTE = ['#6366f1', '#0ea5e9', '#10b981', '#f59e0b', '#ef4444', '#ec4899', '#8b5cf6', '#64748b']

const API = import.meta.env.VITE_API_URL

interface BookingTypeUsage {
    links: number
    bookings: number
    series: number
}

interface BookingTypePickerProps {
    value: number | null
    onChange: (bookingTypeId: number | null) => void
    types: BookingType[]
    onTypesChanged: () => void   // refetch the roster after create/rename/delete
    onError: (msg: string) => void
    disabled?: boolean
    // 'field' is a normal bordered form control. 'inline' sits in a table cell: bare until the
    // row is hovered, so a dense list doesn't read as a wall of inputs.
    variant?: 'field' | 'inline'
}

const Swatch = ({ color, size = 10 }: { color: string | null; size?: number }) => (
    <span
        className="rounded-full shrink-0 border border-black/5"
        style={{ width: size, height: size, background: color ?? '#d1d5db' }}
    />
)

/** The picker *is* the CRUD for booking types — there's no page for them. Rename lives here because
 *  renaming propagates to every booking pointing at the type, which is the whole reason a type is a
 *  row and not a string copied onto each booking. */
const BookingTypePicker = ({ value, onChange, types, onTypesChanged, onError, disabled = false, variant = 'field' }: BookingTypePickerProps) => {
    const [open, setOpen] = useState(false)
    const [editingId, setEditingId] = useState<number | null>(null)
    const [draftLabel, setDraftLabel] = useState('')
    const [draftColor, setDraftColor] = useState<string>(PALETTE[0])
    const [creating, setCreating] = useState(false)
    const [busy, setBusy] = useState(false)
    const [deleting, setDeleting] = useState<BookingType | null>(null)
    const [usage, setUsage] = useState<BookingTypeUsage | null>(null)

    const selected = types.find(t => t.id === value)

    const resetDrafts = () => {
        setEditingId(null)
        setCreating(false)
        setDraftLabel('')
        setDraftColor(PALETTE[0])
    }

    const submitDraft = async () => {
        const label = draftLabel.trim()
        if (!label) return
        setBusy(true)
        try {
            const isNew = editingId === null
            const res = await fetch(`${API}/booking_types/${isNew ? '' : editingId}`, {
                method: isNew ? 'POST' : 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ label, color: draftColor }),
            })
            if (!res.ok) {
                onError(extractError(await res.json(), `Failed to ${isNew ? 'create' : 'rename'} type`))
                return
            }
            if (isNew) onChange((await res.json()).id)
            resetDrafts()
            onTypesChanged()
        } catch (error) {
            console.error(error)
            onError('An unknown error occurred, please try again')
        } finally {
            setBusy(false)
        }
    }

    const startDelete = async (type: BookingType) => {
        setDeleting(type)
        setUsage(null)
        try {
            const res = await fetch(`${API}/booking_types/${type.id}/usage`)
            if (res.ok) setUsage(await res.json())
        } catch (error) {
            // Counts are advisory — the confirm still renders without them.
            console.error(error)
        }
    }

    const confirmDelete = async () => {
        if (!deleting) return
        setBusy(true)
        try {
            const res = await fetch(`${API}/booking_types/${deleting.id}`, { method: 'DELETE' })
            if (!res.ok) {
                onError(extractError(await res.json(), 'Failed to delete type'))
                return
            }
            if (value === deleting.id) onChange(null)
            setDeleting(null)
            onTypesChanged()
        } catch (error) {
            console.error(error)
            onError('An unknown error occurred while deleting')
        } finally {
            setBusy(false)
        }
    }

    const draftRow = (
        <div className="px-2 py-1.5 border-t border-gray-100">
            <TextInput
                size="xs"
                autoFocus
                placeholder="Type name"
                value={draftLabel}
                onChange={e => setDraftLabel(e.currentTarget.value)}
                onKeyDown={e => {
                    if (e.key === 'Enter') { e.preventDefault(); submitDraft() }
                    if (e.key === 'Escape') resetDrafts()
                }}
            />
            <div className="flex items-center gap-1.5 mt-2">
                {PALETTE.map(c => (
                    <button
                        key={c}
                        type="button"
                        onClick={() => setDraftColor(c)}
                        className={`w-4 h-4 rounded-full transition-transform ${draftColor === c ? 'ring-2 ring-offset-1 ring-gray-400 scale-110' : ''}`}
                        style={{ background: c }}
                        aria-label={c}
                    />
                ))}
                <div className="ml-auto flex items-center gap-1">
                    <button type="button" className="p-1 text-gray-400 hover:text-gray-600" onClick={resetDrafts} aria-label="Cancel">
                        <IconX size={14} />
                    </button>
                    <button type="button" className="p-1 text-indigo-500 hover:text-indigo-700 disabled:opacity-40" onClick={submitDraft} disabled={busy || !draftLabel.trim()} aria-label="Save">
                        <IconCheck size={14} />
                    </button>
                </div>
            </div>
        </div>
    )

    return (
        <>
            <Popover opened={open} onChange={o => { setOpen(o); if (!o) resetDrafts() }} position="bottom-start" width={260} shadow="md">
                <Popover.Target>
                    <button
                        type="button"
                        disabled={disabled}
                        onClick={e => { e.stopPropagation(); setOpen(o => !o) }}
                        className={variant === 'inline'
                            // Transparent until hovered — the border and chevron are the only
                            // signal it's editable, and they stay out of the way until then.
                            ? 'group/pick flex items-center gap-1.5 w-full max-w-full px-1.5 py-0.5 -mx-1.5 text-xs text-left rounded border border-transparent group-hover:border-gray-200 group-hover:bg-white transition-colors'
                            : 'flex items-center gap-2 w-full px-3 py-2 text-sm text-left bg-white border border-gray-300 rounded-md hover:border-gray-400 disabled:bg-gray-50 disabled:text-gray-400 transition-colors'}
                    >
                        {selected ? (
                            <>
                                <Swatch color={selected.color} size={variant === 'inline' ? 8 : 10} />
                                <span className="truncate">{selected.label}</span>
                            </>
                        ) : (
                            <span className={variant === 'inline' ? 'text-gray-300 opacity-0 group-hover:opacity-100 transition-opacity' : 'text-gray-400'}>
                                No type
                            </span>
                        )}
                        <IconChevronDown
                            size={variant === 'inline' ? 12 : 14}
                            className={variant === 'inline'
                                ? 'ml-auto shrink-0 text-gray-400 opacity-0 group-hover:opacity-100 transition-opacity'
                                : 'ml-auto shrink-0 text-gray-400'}
                        />
                    </button>
                </Popover.Target>

                <Popover.Dropdown p={0}>
                    <div className="max-h-64 overflow-y-auto py-1">
                        <button
                            type="button"
                            className="flex items-center gap-2 w-full px-3 py-1.5 text-sm text-gray-400 hover:bg-gray-50"
                            onClick={() => { onChange(null); setOpen(false) }}
                        >
                            No type
                            {value === null && <IconCheck size={13} className="ml-auto text-indigo-500" />}
                        </button>

                        {types.map(t => (
                            editingId === t.id ? (
                                <div key={t.id}>{draftRow}</div>
                            ) : (
                                <div key={t.id} className="group/type flex items-center gap-2 px-3 py-1.5 text-sm hover:bg-gray-50">
                                    <button type="button" className="flex items-center gap-2 flex-1 min-w-0 text-left" onClick={() => { onChange(t.id); setOpen(false) }}>
                                        <Swatch color={t.color} />
                                        <span className="truncate">{t.label}</span>
                                    </button>
                                    {value === t.id && <IconCheck size={13} className="shrink-0 text-indigo-500" />}
                                    <span className="flex items-center gap-0.5 shrink-0 opacity-0 group-hover/type:opacity-100 transition-opacity">
                                        <button
                                            type="button"
                                            className="p-1 text-gray-400 hover:text-gray-700"
                                            aria-label={`Rename ${t.label}`}
                                            onClick={() => { setEditingId(t.id); setCreating(false); setDraftLabel(t.label); setDraftColor(t.color ?? PALETTE[0]) }}
                                        >
                                            <IconPencil size={13} />
                                        </button>
                                        <button
                                            type="button"
                                            className="p-1 text-gray-400 hover:text-red-600"
                                            aria-label={`Delete ${t.label}`}
                                            onClick={() => startDelete(t)}
                                        >
                                            <IconTrash size={13} />
                                        </button>
                                    </span>
                                </div>
                            )
                        ))}
                    </div>

                    {creating ? draftRow : (
                        <button
                            type="button"
                            className="flex items-center gap-2 w-full px-3 py-2 text-sm text-indigo-600 border-t border-gray-100 hover:bg-gray-50"
                            onClick={() => { setCreating(true); setEditingId(null); setDraftLabel(''); setDraftColor(PALETTE[0]) }}
                        >
                            <IconPlus size={14} />
                            New type…
                        </button>
                    )}
                </Popover.Dropdown>
            </Popover>

            <Modal opened={deleting !== null} onClose={() => setDeleting(null)} title={`Delete "${deleting?.label}"?`} centered size="sm">
                {usage && (usage.bookings + usage.series + usage.links) > 0 ? (
                    <p className="text-sm text-gray-600 mb-5">
                        {usage.bookings + usage.series > 0 && (
                            <>{usage.bookings} booking{usage.bookings === 1 ? '' : 's'} and {usage.series} series will lose this label. </>
                        )}
                        {usage.links > 0 && (
                            <>{usage.links} link{usage.links === 1 ? '' : 's'} will need a new type picked.</>
                        )}
                    </p>
                ) : (
                    <p className="text-sm text-gray-600 mb-5">Nothing is using this type yet.</p>
                )}
                <div className="flex justify-end gap-2">
                    <Button variant="default" onClick={() => setDeleting(null)}>Cancel</Button>
                    <Button color="red" loading={busy} onClick={confirmDelete}>Delete</Button>
                </div>
            </Modal>
        </>
    )
}

export default BookingTypePicker
