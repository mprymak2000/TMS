import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Button, NumberInput, SegmentedControl, Loader, Menu, Checkbox } from '@mantine/core'
import { IconArrowLeft, IconDotsVertical, IconTrash, IconPencil } from '@tabler/icons-react'
import AppModal, { ModalFooter } from './AppModal'
import type { Booking, Invoice, InvoiceItem, InvoiceLine } from './types'
import { extractError } from './utils'
import Toast from './Toast'
import { useToast } from './useToast'

const API = import.meta.env.VITE_API_URL

const money = (n: number) => `$${n.toFixed(2)}`

const Section = ({ title, action, children }: { title: string; action?: React.ReactNode; children: React.ReactNode }) => (
    <div className="bg-white rounded-lg border border-gray-100 mb-4">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-50">
            <h2 className="text-sm font-medium text-gray-700">{title}</h2>
            {action}
        </div>
        {children}
    </div>
)

// What a line is owed after its adjustment, and what it was before. Both come off the response —
// `amount` is frozen at generation and never rewritten, which is what makes was/now readable.
const LineRow = ({ line, editable, onAdjust, onRemove }: {
    line: InvoiceLine
    editable: boolean
    onAdjust: () => void
    onRemove: () => void
}) => {
    const adjusted = line.adjustment_amount !== null || line.adjustment_percent !== null
    return (
        <tr className="border-b border-gray-50 last:border-0 group">
            <td className="px-5 py-3 text-gray-800">
                {line.description}
                {/* A null booking_id on a booking line means the booking was hard-deleted (series
                    cancel). The line still bills, so say so rather than leaving it looking normal. */}
                {line.booking_id === null && line.enrollment_id === null && line.invoice_item_id === null && (
                    <span className="ml-2 text-xs text-gray-400">· source deleted</span>
                )}
            </td>
            <td className="px-5 py-3 text-right whitespace-nowrap">
                {adjusted ? (
                    <>
                        <span className="text-gray-300 line-through mr-2">{money(line.amount)}</span>
                        <span className="text-gray-800">{money(line.charged_amount)}</span>
                    </>
                ) : (
                    <span className="text-gray-800">{money(line.amount)}</span>
                )}
            </td>
            <td className="px-2 py-3 w-10">
                {editable && (
                    <Menu position="bottom-end" withinPortal>
                        <Menu.Target>
                            <button className="p-1 rounded text-gray-300 hover:text-gray-600 hover:bg-gray-100 opacity-0 group-hover:opacity-100 transition-opacity">
                                <IconDotsVertical size={15} />
                            </button>
                        </Menu.Target>
                        <Menu.Dropdown>
                            <Menu.Item leftSection={<IconPencil size={14} />} onClick={onAdjust}>
                                {adjusted ? 'Change adjustment' : 'Adjust amount'}
                            </Menu.Item>
                            <Menu.Item leftSection={<IconTrash size={14} />} color="red" onClick={onRemove}>
                                Remove line
                            </Menu.Item>
                        </Menu.Dropdown>
                    </Menu>
                )}
            </td>
        </tr>
    )
}

const InvoicePage = () => {
    const { id } = useParams<{ id: string }>()
    const navigate = useNavigate()
    const [invoice, setInvoice] = useState<Invoice | null>(null)
    const [loading, setLoading] = useState(true)
    const [loadError, setLoadError] = useState<string | null>(null)
    const { toast, showToast } = useToast()

    const [adjusting, setAdjusting] = useState<InvoiceLine | null>(null)
    const [picking, setPicking] = useState(false)
    const [confirming, setConfirming] = useState<'void' | 'delete' | 'finalize' | null>(null)
    const [removingLine, setRemovingLine] = useState<InvoiceLine | null>(null)
    const [actionError, setActionError] = useState<string | null>(null)

    const load = async () => {
        setLoading(true)
        try {
            const res = await fetch(`${API}/invoices/${id}`)
            if (!res.ok) throw new Error(extractError(await res.json(), 'Failed to load invoice'))
            setInvoice(await res.json())
        } catch (e) {
            setLoadError(e instanceof Error ? e.message : 'Failed to load invoice')
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => {
        load()
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [id])

    // Every write returns the whole invoice, so there's nothing to patch by hand.
    const write = async (path: string, method: string, body?: unknown) => {
        setActionError(null)
        const res = await fetch(`${API}/invoices/${id}${path}`, {
            method,
            headers: body ? { 'Content-Type': 'application/json' } : undefined,
            body: body ? JSON.stringify(body) : undefined,
        })
        if (!res.ok) throw new Error(extractError(await res.json(), 'Request failed'))
        return res.json()
    }

    const runWrite = async (path: string, method: string, body: unknown, success: string) => {
        try {
            setInvoice(await write(path, method, body))
            showToast(success)
            return true
        } catch (e) {
            setActionError(e instanceof Error ? e.message : 'Failed')
            return false
        }
    }

    if (loading) return <div className="p-6 flex justify-center"><Loader size="sm" /></div>
    if (loadError || !invoice) return <div className="p-6 text-sm text-red-500">{loadError ?? 'Not found'}</div>

    const isDraft = invoice.status === 'draft'
    const lineTotal = invoice.lines.reduce((sum, l) => sum + l.charged_amount, 0)

    return (
        <div className="p-6 max-w-3xl">
            <button onClick={() => navigate('/invoices')} className="flex items-center gap-1.5 text-sm text-gray-400 hover:text-gray-600 mb-4">
                <IconArrowLeft size={15} /> Invoices
            </button>

            <div className="flex items-start justify-between mb-5">
                <div>
                    <h1 className="text-xl font-semibold text-gray-800">
                        {invoice.number ?? 'Draft invoice'}
                    </h1>
                    <p className="text-sm text-gray-400 mt-0.5">
                        {invoice.payer_name}
                        {invoice.period_start && ` · ${invoice.period_start} → ${invoice.period_end}`}
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    {isDraft && (
                        <Button size="sm" onClick={() => setConfirming('finalize')}>Finalize</Button>
                    )}
                    {/* Payment is a separate axis from the document's state, so it's its own control
                        and only appears once there's something to be paid. */}
                    {invoice.status !== 'draft' && (
                        <SegmentedControl
                            size="xs"
                            value={invoice.payment_status}
                            data={[{ value: 'unpaid', label: 'Unpaid' }, { value: 'paid', label: 'Paid' }]}
                            onChange={val => runWrite('/payment', 'PUT', { payment_status: val }, `Marked ${val}.`)}
                        />
                    )}
                    <Menu position="bottom-end" withinPortal>
                        <Menu.Target>
                            <button className="p-1.5 rounded text-gray-400 hover:text-gray-600 hover:bg-gray-100">
                                <IconDotsVertical size={16} />
                            </button>
                        </Menu.Target>
                        <Menu.Dropdown>
                            {invoice.status !== 'void' && (
                                <Menu.Item color="red" onClick={() => setConfirming('void')}>Void invoice</Menu.Item>
                            )}
                            {isDraft && (
                                <Menu.Item color="red" leftSection={<IconTrash size={14} />} onClick={() => setConfirming('delete')}>
                                    Delete draft
                                </Menu.Item>
                            )}
                        </Menu.Dropdown>
                    </Menu>
                </div>
            </div>

            {actionError && <p className="text-sm text-red-500 mb-3">{actionError}</p>}

            {isDraft && (
                <p className="text-xs text-gray-400 mb-4">
                    A draft isn't sent and isn't owed. Amounts were frozen when it was generated, so a
                    later rate or charge change won't appear here — delete and generate again for that.
                </p>
            )}

            <Section
                title="Lines"
                action={isDraft && (
                    <Button variant="subtle" size="compact-xs" onClick={() => setPicking(true)}>
                        Change what's included
                    </Button>
                )}
            >
                <table className="w-full text-sm">
                    <tbody>
                        {invoice.lines.length === 0 ? (
                            <tr><td className="px-5 py-8 text-center text-gray-400">No lines.</td></tr>
                        ) : invoice.lines.map(line => (
                            <LineRow
                                key={line.id}
                                line={line}
                                editable={isDraft}
                                onAdjust={() => setAdjusting(line)}
                                onRemove={() => setRemovingLine(line)}
                            />
                        ))}
                    </tbody>
                    <tfoot className="border-t border-gray-100">
                        <tr>
                            <td className="px-5 py-3 text-sm font-medium text-gray-700">Total</td>
                            <td className="px-5 py-3 text-right text-sm font-semibold text-gray-900">{money(invoice.total)}</td>
                            <td />
                        </tr>
                    </tfoot>
                </table>
            </Section>

            {/* The stored total and the sum of the lines should never disagree. If they do it's a bug
                worth seeing rather than hiding behind a recomputed figure. */}
            {Math.abs(lineTotal - invoice.total) > 0.005 && (
                <p className="text-xs text-amber-600 mb-4">
                    Stored total {money(invoice.total)} doesn't match the lines ({money(lineTotal)}).
                </p>
            )}

            {picking && (
                <PickModal
                    invoice={invoice}
                    onClose={() => setPicking(false)}
                    onSaved={updated => { setInvoice(updated); setPicking(false); showToast('Invoice updated.') }}
                />
            )}

            {adjusting && (
                <AdjustModal
                    line={adjusting}
                    onClose={() => setAdjusting(null)}
                    onSave={async body => {
                        const ok = await runWrite(`/lines/${adjusting.id}`, 'PUT', body, 'Line updated.')
                        if (ok) setAdjusting(null)
                    }}
                />
            )}

            {removingLine && (
                <AppModal opened onClose={() => setRemovingLine(null)} title="Remove this line?"
                    caption="Whatever it bills goes back to unbilled, so a later invoice can pick it up.">
                    <p className="text-sm text-gray-600">{removingLine.description} · {money(removingLine.charged_amount)}</p>
                    <ModalFooter>
                        <Button variant="default" onClick={() => setRemovingLine(null)}>Cancel</Button>
                        <Button color="red" onClick={async () => {
                            const ok = await runWrite(`/lines/${removingLine.id}`, 'DELETE', undefined, 'Line removed.')
                            if (ok) setRemovingLine(null)
                        }}>Remove</Button>
                    </ModalFooter>
                </AppModal>
            )}

            {confirming && (
                <AppModal opened onClose={() => setConfirming(null)}
                    title={{
                        finalize: 'Finalize this invoice?',
                        void: 'Void this invoice?',
                        delete: 'Delete this draft?',
                    }[confirming]}
                    caption={{
                        finalize: "It gets a number and becomes a record. There's no going back to draft.",
                        void: 'It stays in the list with its number, but is no longer owed.',
                        delete: 'Its lines go back to unbilled. Nothing is kept.',
                    }[confirming]}
                >
                    <ModalFooter>
                        <Button variant="default" onClick={() => setConfirming(null)}>Cancel</Button>
                        <Button
                            color={confirming === 'finalize' ? undefined : 'red'}
                            onClick={async () => {
                                if (confirming === 'delete') {
                                    try {
                                        await write('', 'DELETE')
                                        navigate('/invoices')
                                    } catch (e) {
                                        setActionError(e instanceof Error ? e.message : 'Failed')
                                        setConfirming(null)
                                    }
                                    return
                                }
                                const status = confirming === 'finalize' ? 'finalized' : 'void'
                                const ok = await runWrite('/status', 'PUT', { status },
                                    confirming === 'finalize' ? 'Finalized.' : 'Voided.')
                                if (ok) setConfirming(null)
                            }}
                        >
                            {{ finalize: 'Finalize', void: 'Void', delete: 'Delete' }[confirming]}
                        </Button>
                    </ModalFooter>
                </AppModal>
            )}

            <Toast toast={toast} />
        </div>
    )
}

// The complete desired membership, not a delta: the server diffs it, claims what's newly ticked
// and releases what isn't. The monthly plan line isn't in here — it isn't optional on a period
// invoice — so it survives untouched.
const PickModal = ({ invoice, onClose, onSaved }: {
    invoice: Invoice
    onClose: () => void
    onSaved: (updated: Invoice) => void
}) => {
    // Sessions come from the bookings list filtered to unbilled work, items from the pending-items
    // list. Both are the normal collection endpoints: there's no invoice-scoped candidates route,
    // the same way Stripe and Cliniko filter their own collections by customer + billed state.
    const [sessions, setSessions] = useState<Booking[] | null>(null)
    const [items, setItems] = useState<InvoiceItem[] | null>(null)
    // Refs, not ids: a virtual occurrence has no row yet, so there's nothing but its ref to send.
    const [refs, setRefs] = useState<string[]>(
        invoice.lines.filter(l => l.booking_ref !== null).map(l => l.booking_ref!))
    const [itemIds, setItemIds] = useState<number[]>(
        invoice.lines.filter(l => l.invoice_item_id !== null).map(l => l.invoice_item_id!))
    const [error, setError] = useState<string | null>(null)
    const [saving, setSaving] = useState(false)

    useEffect(() => {
        (async () => {
            try {
                // One wide window rather than the invoice's own period: a period is stored in
                // business time and the client doesn't know that zone, so deriving UTC bounds from
                // it here would clip the first and last few hours. Every row shows its date, so the
                // admin can see what they're ticking.
                const query = new URLSearchParams({
                    unbilled: 'true',
                    payer_ids: String(invoice.payer_id),
                    page_size: '200',
                    time_max: new Date(Date.now() + 365 * 864e5).toISOString(),
                })
                const [bookingsRes, itemsRes] = await Promise.all([
                    fetch(`${API}/bookings/?${query}`),
                    fetch(`${API}/invoice-items/?payer_id=${invoice.payer_id}&pending=true`),
                ])
                if (!bookingsRes.ok) throw new Error(extractError(await bookingsRes.json(), 'Failed to load sessions'))
                if (!itemsRes.ok) throw new Error(extractError(await itemsRes.json(), 'Failed to load charges'))
                setSessions((await bookingsRes.json()).items)
                setItems(await itemsRes.json())
            } catch (e) {
                setError(e instanceof Error ? e.message : 'Failed to load')
            }
        })()
    }, [invoice.id, invoice.payer_id])

    const loading = sessions === null || items === null

    // The unbilled list holds only what's unclaimed, so whatever is already on this invoice has to
    // be merged back in or it would look addable rather than removable.
    const sessionRows = [
        ...invoice.lines
            .filter(l => l.booking_ref !== null)
            .map(l => ({ ref: l.booking_ref!, label: l.description, amount: l.amount, when: null as string | null, future: false })),
        ...(sessions ?? [])
            .filter(b => !invoice.lines.some(l => l.booking_ref === b.id))
            .map(b => ({
                ref: b.id,
                label: `${b.attendee.first_name} ${b.attendee.last_name}`,
                amount: b.would_bill,
                when: new Date(b.start).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }),
                future: new Date(b.start) > new Date(),
            })),
    ]

    const itemRows = [
        ...invoice.lines
            .filter(l => l.invoice_item_id !== null)
            .map(l => ({ id: l.invoice_item_id!, label: l.description, amount: l.amount })),
        ...(items ?? [])
            .filter(i => !invoice.lines.some(l => l.invoice_item_id === i.id))
            .map(i => ({ id: i.id, label: i.description, amount: i.amount })),
    ]

    const toggle = <T,>(list: T[], set: (v: T[]) => void, key: T) =>
        set(list.includes(key) ? list.filter(x => x !== key) : [...list, key])

    const submit = async () => {
        setSaving(true)
        setError(null)
        try {
            const res = await fetch(`${API}/invoices/${invoice.id}/lines`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ booking_ids: refs, item_ids: itemIds }),
            })
            if (!res.ok) throw new Error(extractError(await res.json(), 'Failed to save'))
            onSaved(await res.json())
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Failed to save')
        } finally {
            setSaving(false)
        }
    }

    return (
        <AppModal opened onClose={onClose} title="What's on this invoice" size="lg"
            caption="Untick to drop a line and make it billable again. Ticking a session that hasn't happened yet bills it ahead.">
            {loading ? (
                <div className="py-6 flex justify-center"><Loader size="sm" /></div>
            ) : (
                <>
                    {sessionRows.length > 0 && (
                        <div className="mb-4">
                            <p className="text-xs font-medium text-gray-400 uppercase tracking-wide mb-2">Sessions</p>
                            <div className="space-y-1.5">
                                {sessionRows.map(row => (
                                    <label key={row.ref} className="flex items-center gap-2.5 text-sm cursor-pointer">
                                        <Checkbox size="xs" checked={refs.includes(row.ref)}
                                            onChange={() => toggle(refs, setRefs, row.ref)} />
                                        <span className="text-gray-700 flex-1">
                                            {row.label}
                                            {row.when && <span className="text-gray-400 text-xs ml-1.5">{row.when}</span>}
                                            {row.future && <span className="ml-1.5 text-xs text-amber-600">not yet delivered</span>}
                                        </span>
                                        <span className="text-gray-500 text-xs">
                                            {row.amount === null ? 'unpriced' : money(row.amount)}
                                        </span>
                                    </label>
                                ))}
                            </div>
                        </div>
                    )}
                    {itemRows.length > 0 && (
                        <div className="mb-4">
                            <p className="text-xs font-medium text-gray-400 uppercase tracking-wide mb-2">Other charges</p>
                            <div className="space-y-1.5">
                                {itemRows.map(row => (
                                    <label key={row.id} className="flex items-center gap-2.5 text-sm cursor-pointer">
                                        <Checkbox size="xs" checked={itemIds.includes(row.id)}
                                            onChange={() => toggle(itemIds, setItemIds, row.id)} />
                                        <span className="text-gray-700 flex-1">{row.label}</span>
                                        <span className="text-gray-500 text-xs">{money(row.amount)}</span>
                                    </label>
                                ))}
                            </div>
                        </div>
                    )}
                    {sessionRows.length === 0 && itemRows.length === 0 && (
                        <p className="text-sm text-gray-400 py-4">Nothing available to add.</p>
                    )}
                </>
            )}
            {error && <p className="text-sm text-red-500 mt-3">{error}</p>}
            <ModalFooter>
                <Button variant="default" onClick={onClose}>Cancel</Button>
                <Button onClick={submit} loading={saving} disabled={loading}>Save</Button>
            </ModalFooter>
        </AppModal>
    )
}


// Flat or percent, never both — the backend has a CHECK and rejects the pair. Clearing both reverts
// the line to its generated amount, so "no adjustment" is a real choice rather than a deletion.
const AdjustModal = ({ line, onClose, onSave }: {
    line: InvoiceLine
    onClose: () => void
    onSave: (body: Record<string, number | null>) => void
}) => {
    const initialMode = line.adjustment_percent !== null ? 'percent' : 'flat'
    const [mode, setMode] = useState<'flat' | 'percent'>(initialMode)
    const [flat, setFlat] = useState<number | string>(line.adjustment_amount ?? '')
    const [percent, setPercent] = useState<number | string>(line.adjustment_percent ?? '')
    const [clear, setClear] = useState(false)

    const preview = clear
        ? line.amount
        : mode === 'flat'
            ? line.amount + (flat === '' ? 0 : Number(flat))
            : line.amount * (1 - (percent === '' ? 0 : Number(percent)) / 100)

    return (
        <AppModal opened onClose={onClose} title="Adjust this line"
            caption="The generated amount stays as it is; the adjustment sits on top.">
            <p className="text-sm text-gray-500 mb-4">{line.description} · generated at {money(line.amount)}</p>

            <Checkbox
                label="No adjustment"
                description="Bill the generated amount"
                checked={clear}
                onChange={e => setClear(e.currentTarget.checked)}
                mb="md"
            />

            {!clear && (
                <>
                    <SegmentedControl
                        fullWidth size="xs" mb="md"
                        value={mode}
                        onChange={val => setMode(val as 'flat' | 'percent')}
                        data={[{ value: 'flat', label: 'Amount off / on' }, { value: 'percent', label: 'Percent off' }]}
                    />
                    {mode === 'flat' ? (
                        <NumberInput
                            label="Adjustment ($)"
                            description="Negative discounts, positive adds"
                            value={flat} onChange={setFlat} decimalScale={2}
                        />
                    ) : (
                        <NumberInput
                            label="Discount (%)"
                            value={percent} onChange={setPercent} min={0} max={100} decimalScale={2}
                        />
                    )}
                    <p className="text-sm text-gray-500 mt-3">Charged: <span className="font-medium text-gray-800">{money(preview)}</span></p>
                </>
            )}

            <ModalFooter>
                <Button variant="default" onClick={onClose}>Cancel</Button>
                <Button onClick={() => onSave(
                    clear ? {}
                        : mode === 'flat' ? { adjustment_amount: flat === '' ? null : Number(flat) }
                            : { adjustment_percent: percent === '' ? null : Number(percent) }
                )}>Save</Button>
            </ModalFooter>
        </AppModal>
    )
}

export default InvoicePage
