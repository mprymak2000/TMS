import { useState, useEffect } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { Button, Checkbox, Loader, Select, TextInput } from '@mantine/core'
import { IconChevronLeft, IconChevronRight, IconPlus } from '@tabler/icons-react'
import AppModal, { ModalFooter } from './AppModal'
import ContactPicker from './ContactPicker'
import type { Booking, Invoice, InvoiceItem, InvoicePagedResponse } from './types'
import { extractError } from './utils'
import Toast from './Toast'
import { useToast } from './useToast'

const API = import.meta.env.VITE_API_URL
const PAGE_SIZE = 50

const STATUS_STYLE = {
    draft: 'bg-gray-100 text-gray-600',
    finalized: 'bg-indigo-50 text-indigo-700',
    void: 'bg-gray-50 text-gray-400 line-through',
} as const

const PAYMENT_STYLE = {
    unpaid: 'bg-amber-50 text-amber-700',
    paid: 'bg-emerald-50 text-emerald-700',
} as const

const Pill = ({ label, className }: { label: string; className: string }) => (
    <span className={`px-2 py-0.5 rounded text-xs font-medium ${className}`}>{label}</span>
)

const money = (n: number) => `$${n.toFixed(2)}`

// Ad-hoc invoices have no period, so show what they are rather than an empty cell.
const periodLabel = (inv: Invoice) =>
    inv.period_start ? `${inv.period_start} → ${inv.period_end}` : 'Ad-hoc'

// First of last month through first of this month, which is what the monthly job uses.
const lastMonth = () => {
    const now = new Date()
    const end = new Date(now.getFullYear(), now.getMonth(), 1)
    const start = new Date(now.getFullYear(), now.getMonth() - 1, 1)
    const iso = (d: Date) => d.toISOString().slice(0, 10)
    return { start: iso(start), end: iso(end) }
}

const Invoices = () => {
    const navigate = useNavigate()
    const [invoices, setInvoices] = useState<Invoice[]>([])
    const [total, setTotal] = useState(0)
    const [loading, setLoading] = useState(true)
    const [loadError, setLoadError] = useState<string | null>(null)
    const { toast, showToast } = useToast()

    // Same as the client roster: the query string is what's being viewed, so a filtered list can be
    // linked and reached with the back button.
    const [params, setParams] = useSearchParams()
    const status = params.get('status') ?? ''
    const page = Number(params.get('page') ?? 1)

    const updateParams = (changes: Record<string, string | null>) => {
        setParams(prev => {
            const next = new URLSearchParams(prev)
            for (const [key, value] of Object.entries(changes)) {
                if (!value) next.delete(key)
                else next.set(key, value)
            }
            return next
        })
    }

    const [generating, setGenerating] = useState(false)
    const [creating, setCreating] = useState(false)

    const loadInvoices = async () => {
        setLoading(true)
        setLoadError(null)
        try {
            const query = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) })
            if (status) query.set('status', status)
            const res = await fetch(`${API}/invoices/?${query}`)
            if (!res.ok) throw new Error(extractError(await res.json(), 'Failed to load invoices'))
            const data: InvoicePagedResponse = await res.json()
            setInvoices(data.items)
            setTotal(data.total)
        } catch (e) {
            setLoadError(e instanceof Error ? e.message : 'Failed to load invoices')
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => {
        loadInvoices()
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [status, page])

    const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))

    return (
        <div className="p-6">
            <div className="flex items-center justify-between mb-5">
                <div>
                    <h1 className="text-xl font-semibold text-gray-800">Invoices</h1>
                    <p className="text-sm text-gray-400 mt-0.5">
                        {total} invoice{total === 1 ? '' : 's'}
                    </p>
                </div>
                <div className="flex gap-2">
                    <Button variant="default" size="sm" onClick={() => setGenerating(true)}>
                        Generate for a month
                    </Button>
                    <Button size="sm" leftSection={<IconPlus size={16} />} onClick={() => setCreating(true)}>
                        New invoice
                    </Button>
                </div>
            </div>

            <div className="flex gap-3 mb-4">
                <Select
                    size="sm" placeholder="Any status" clearable className="w-44"
                    data={[
                        { value: 'draft', label: 'Draft' },
                        { value: 'finalized', label: 'Finalized' },
                        { value: 'void', label: 'Void' },
                    ]}
                    value={status || null}
                    onChange={val => updateParams({ status: val, page: null })}
                />
            </div>

            {loadError && <p className="text-sm text-red-500 mb-3">{loadError}</p>}

            <div className="bg-white rounded-lg border border-gray-100 overflow-hidden">
                <table className="w-full text-sm">
                    <thead className="text-left text-xs text-gray-400 border-b border-gray-100">
                        <tr>
                            <th className="px-5 py-3 font-medium">Number</th>
                            <th className="px-5 py-3 font-medium">Payer</th>
                            <th className="px-5 py-3 font-medium">Period</th>
                            <th className="px-5 py-3 font-medium">Status</th>
                            <th className="px-5 py-3 font-medium">Payment</th>
                            <th className="px-5 py-3 font-medium text-right">Total</th>
                        </tr>
                    </thead>
                    <tbody>
                        {loading ? (
                            <tr><td colSpan={6} className="px-5 py-8 text-center text-gray-300">Loading…</td></tr>
                        ) : invoices.length === 0 ? (
                            <tr><td colSpan={6} className="px-5 py-8 text-center text-gray-400">
                                No invoices yet. Generate for a month, or bill one client.
                            </td></tr>
                        ) : invoices.map(inv => (
                            <tr
                                key={inv.id}
                                onClick={() => navigate(`/invoices/${inv.id}`)}
                                className="border-b border-gray-50 last:border-0 hover:bg-gray-50 cursor-pointer"
                            >
                                <td className="px-5 py-3 text-gray-500 font-mono text-xs">
                                    {inv.number ?? <span className="text-gray-300">—</span>}
                                </td>
                                <td className="px-5 py-3 text-gray-800">{inv.payer_name}</td>
                                <td className="px-5 py-3 text-gray-500 text-xs">{periodLabel(inv)}</td>
                                <td className="px-5 py-3"><Pill label={inv.status} className={STATUS_STYLE[inv.status]} /></td>
                                <td className="px-5 py-3">
                                    {/* A draft has nothing to be paid, so don't imply it's owed. */}
                                    {inv.status === 'draft'
                                        ? <span className="text-xs text-gray-300">—</span>
                                        : <Pill label={inv.payment_status} className={PAYMENT_STYLE[inv.payment_status]} />}
                                </td>
                                <td className="px-5 py-3 text-right text-gray-800">{money(inv.total)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>

            {pages > 1 && (
                <div className="flex items-center justify-end gap-2 mt-4 text-sm">
                    <button
                        disabled={page <= 1}
                        onClick={() => updateParams({ page: String(page - 1) })}
                        className="p-1.5 rounded hover:bg-gray-100 disabled:opacity-30 disabled:hover:bg-transparent"
                    ><IconChevronLeft size={16} /></button>
                    <span className="text-gray-400 text-xs">Page {page} of {pages}</span>
                    <button
                        disabled={page >= pages}
                        onClick={() => updateParams({ page: String(page + 1) })}
                        className="p-1.5 rounded hover:bg-gray-100 disabled:opacity-30 disabled:hover:bg-transparent"
                    ><IconChevronRight size={16} /></button>
                </div>
            )}

            {generating && (
                <GenerateModal
                    onClose={() => setGenerating(false)}
                    onDone={msg => { setGenerating(false); showToast(msg); loadInvoices() }}
                />
            )}
            {creating && (
                <CreateModal
                    onClose={() => setCreating(false)}
                    onCreated={inv => { setCreating(false); navigate(`/invoices/${inv.id}`) }}
                />
            )}
            <Toast toast={toast} />
        </div>
    )
}

// Generation is create-only: a payer who already has an invoice covering the period is skipped. So
// report how many came back against how many were asked for, or a run that skipped everyone looks
// like a failure.
const GenerateModal = ({ onClose, onDone }: { onClose: () => void; onDone: (msg: string) => void }) => {
    const { start, end } = lastMonth()
    const [periodStart, setPeriodStart] = useState(start)
    const [periodEnd, setPeriodEnd] = useState(end)
    const [error, setError] = useState<string | null>(null)
    const [saving, setSaving] = useState(false)

    const submit = async () => {
        setSaving(true)
        setError(null)
        try {
            const res = await fetch(`${API}/invoices/generate`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ period_start: periodStart, period_end: periodEnd }),
            })
            if (!res.ok) throw new Error(extractError(await res.json(), 'Failed to generate'))
            const made: Invoice[] = await res.json()
            onDone(made.length === 0
                ? 'Nothing to invoice — everyone with activity already has one for that period.'
                : `Drafted ${made.length} invoice${made.length === 1 ? '' : 's'}.`)
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Failed to generate')
        } finally {
            setSaving(false)
        }
    }

    return (
        <AppModal opened onClose={onClose} title="Generate invoices"
            caption="One draft per payer with activity in the period. Nothing is sent.">
            <div className="grid grid-cols-2 gap-3">
                <TextInput label="From" type="date" value={periodStart} onChange={e => setPeriodStart(e.target.value)} />
                <TextInput label="To" type="date" value={periodEnd} onChange={e => setPeriodEnd(e.target.value)}
                    description="Exclusive" />
            </div>
            {error && <p className="text-sm text-red-500 mt-3">{error}</p>}
            <ModalFooter>
                <Button variant="default" onClick={onClose}>Cancel</Button>
                <Button onClick={submit} loading={saving}>Generate</Button>
            </ModalFooter>
        </AppModal>
    )
}

// Ad-hoc: one payer, no period. Sweeps their delivered-but-unbilled sessions and pending charges.
// Billing ahead of a session is a separate, deliberate act — pick the booking from its own row.
// Picking a client shows what's outstanding for them rather than billing blind. Delivered sessions
// are ticked by default (that's what a plain sweep would take); future ones are listed unticked, so
// collecting ahead of a session is one click rather than a 409 telling you there's nothing to bill.
const CreateModal = ({ onClose, onCreated }: { onClose: () => void; onCreated: (inv: Invoice) => void }) => {
    const [payerId, setPayerId] = useState<number | null>(null)
    const [sessions, setSessions] = useState<Booking[] | null>(null)
    const [items, setItems] = useState<InvoiceItem[] | null>(null)
    const [refs, setRefs] = useState<string[]>([])
    const [itemIds, setItemIds] = useState<number[]>([])
    const [error, setError] = useState<string | null>(null)
    const [saving, setSaving] = useState(false)

    useEffect(() => {
        if (!payerId) { setSessions(null); setItems(null); setRefs([]); setItemIds([]); return }
        let cancelled = false
        ;(async () => {
            try {
                const query = new URLSearchParams({
                    unbilled: 'true',
                    payer_ids: String(payerId),
                    page_size: '200',
                    time_max: new Date(Date.now() + 365 * 864e5).toISOString(),
                })
                const [bookingsRes, itemsRes] = await Promise.all([
                    fetch(`${API}/bookings/?${query}`),
                    fetch(`${API}/invoice-items/?payer_id=${payerId}&pending=true`),
                ])
                if (!bookingsRes.ok) throw new Error(extractError(await bookingsRes.json(), 'Failed to load sessions'))
                if (!itemsRes.ok) throw new Error(extractError(await itemsRes.json(), 'Failed to load charges'))
                const loadedSessions: Booking[] = (await bookingsRes.json()).items
                const loadedItems: InvoiceItem[] = await itemsRes.json()
                if (cancelled) return
                setSessions(loadedSessions)
                setItems(loadedItems)
                const now = new Date()
                setRefs(loadedSessions.filter(b => new Date(b.start) <= now).map(b => b.id))
                setItemIds(loadedItems.map(i => i.id))
                setError(null)
            } catch (e) {
                if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load')
            }
        })()
        return () => { cancelled = true }
    }, [payerId])

    const toggle = <T,>(list: T[], set: (v: T[]) => void, key: T) =>
        set(list.includes(key) ? list.filter(x => x !== key) : [...list, key])

    const selectedTotal =
        (sessions ?? []).filter(b => refs.includes(b.id)).reduce((sum, b) => sum + (b.would_bill ?? 0), 0)
        + (items ?? []).filter(i => itemIds.includes(i.id)).reduce((sum, i) => sum + i.amount, 0)

    const submit = async () => {
        if (!payerId) { setError('Pick who this is for.'); return }
        if (refs.length === 0 && itemIds.length === 0) { setError('Tick at least one thing to bill.'); return }
        setSaving(true)
        setError(null)
        try {
            const res = await fetch(`${API}/invoices/`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ payer_id: payerId, booking_ids: refs, item_ids: itemIds }),
            })
            if (!res.ok) throw new Error(extractError(await res.json(), 'Failed to create'))
            onCreated(await res.json())
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Failed to create')
        } finally {
            setSaving(false)
        }
    }

    const loading = payerId !== null && (sessions === null || items === null)
    const nothing = !loading && payerId !== null && (sessions?.length ?? 0) === 0 && (items?.length ?? 0) === 0

    return (
        <AppModal opened onClose={onClose} title="New invoice" size="lg"
            caption="Everything unbilled for one client. Delivered sessions are ticked; tick a future one to collect ahead.">
            <ContactPicker value={payerId} onChange={setPayerId} label="Client" placeholder="Who is this for?" />

            {loading && <div className="py-6 flex justify-center"><Loader size="sm" /></div>}
            {nothing && <p className="text-sm text-gray-400 py-4">Nothing unbilled for this client.</p>}

            {!loading && (sessions?.length ?? 0) > 0 && (
                <div className="mt-4">
                    <p className="text-xs font-medium text-gray-400 uppercase tracking-wide mb-2">Sessions</p>
                    <div className="space-y-1.5 max-h-64 overflow-y-auto">
                        {sessions!.map(b => {
                            const future = new Date(b.start) > new Date()
                            return (
                                <label key={b.id} className="flex items-center gap-2.5 text-sm cursor-pointer">
                                    <Checkbox size="xs" checked={refs.includes(b.id)}
                                        onChange={() => toggle(refs, setRefs, b.id)} />
                                    <span className="text-gray-700 flex-1">
                                        {b.attendee.first_name} {b.attendee.last_name}
                                        <span className="text-gray-400 text-xs ml-1.5">
                                            {new Date(b.start).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
                                        </span>
                                        {future && <span className="ml-1.5 text-xs text-amber-600">not yet delivered</span>}
                                    </span>
                                    <span className="text-gray-500 text-xs">
                                        {b.would_bill === null ? 'unpriced' : money(b.would_bill)}
                                    </span>
                                </label>
                            )
                        })}
                    </div>
                </div>
            )}

            {!loading && (items?.length ?? 0) > 0 && (
                <div className="mt-4">
                    <p className="text-xs font-medium text-gray-400 uppercase tracking-wide mb-2">Other charges</p>
                    <div className="space-y-1.5">
                        {items!.map(i => (
                            <label key={i.id} className="flex items-center gap-2.5 text-sm cursor-pointer">
                                <Checkbox size="xs" checked={itemIds.includes(i.id)}
                                    onChange={() => toggle(itemIds, setItemIds, i.id)} />
                                <span className="text-gray-700 flex-1">{i.description}</span>
                                <span className="text-gray-500 text-xs">{money(i.amount)}</span>
                            </label>
                        ))}
                    </div>
                </div>
            )}

            {!loading && !nothing && payerId !== null && (
                <p className="text-sm text-gray-500 mt-4">
                    Total: <span className="font-medium text-gray-800">{money(selectedTotal)}</span>
                </p>
            )}

            {error && <p className="text-sm text-red-500 mt-3">{error}</p>}
            <ModalFooter>
                <Button variant="default" onClick={onClose}>Cancel</Button>
                <Button onClick={submit} loading={saving} disabled={loading || nothing}>Create</Button>
            </ModalFooter>
        </AppModal>
    )
}

export default Invoices
