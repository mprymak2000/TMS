import { useState, useEffect } from 'react'
import { Button, NumberInput, Checkbox, Loader } from '@mantine/core'
import { IconArrowUp } from '@tabler/icons-react'
import AppModal, { ModalFooter } from './AppModal'
import type { ContactPagedResponse, ContactListRow, Price } from './types'
import { extractError } from './utils'
import Toast from './Toast'
import { useToast } from './useToast'

const API = import.meta.env.VITE_API_URL

const UNIT_LABEL = { per_session: 'per session', per_hour: 'per hour', per_month: 'per month' } as const

const money = (n: number) => `$${n.toFixed(2)}`

interface Usage {
    enrollments: number
    booking_links: number
}

const Prices = () => {
    const [prices, setPrices] = useState<Price[]>([])
    const [loading, setLoading] = useState(true)
    const [loadError, setLoadError] = useState<string | null>(null)
    const [raising, setRaising] = useState<Price | null>(null)
    const { toast, showToast } = useToast()

    const load = async () => {
        setLoading(true)
        try {
            const res = await fetch(`${API}/prices/`)
            if (!res.ok) throw new Error(extractError(await res.json(), 'Failed to load prices'))
            setPrices(await res.json())
            setLoadError(null)
        } catch (e) {
            setLoadError(e instanceof Error ? e.message : 'Failed to load prices')
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => { load() }, [])

    return (
        <div className="p-6 max-w-3xl">
            <div className="mb-5">
                <h1 className="text-xl font-semibold text-gray-800">Prices</h1>
                <p className="text-sm text-gray-400 mt-0.5">
                    Every rate and link price in use. A price is never edited — raising one makes a new
                    entry and moves clients onto it, so past invoices keep what they charged.
                </p>
            </div>

            {loadError && <p className="text-sm text-red-500 mb-3">{loadError}</p>}

            <div className="bg-white rounded-lg border border-gray-100 overflow-hidden">
                <table className="w-full text-sm">
                    <thead className="text-left text-xs text-gray-400 border-b border-gray-100">
                        <tr>
                            <th className="px-5 py-3 font-medium">Amount</th>
                            <th className="px-5 py-3 font-medium">Billed</th>
                            <th className="px-5 py-3" />
                        </tr>
                    </thead>
                    <tbody>
                        {loading ? (
                            <tr><td colSpan={3} className="px-5 py-8 text-center text-gray-300">Loading…</td></tr>
                        ) : prices.length === 0 ? (
                            <tr><td colSpan={3} className="px-5 py-8 text-center text-gray-400">
                                No prices yet. One appears as soon as a client is given a rate or a link is priced.
                            </td></tr>
                        ) : prices.map(p => (
                            <tr key={p.id} className="border-b border-gray-50 last:border-0 group">
                                <td className="px-5 py-3 text-gray-800">{money(p.amount)}</td>
                                <td className="px-5 py-3 text-gray-500 text-xs">{UNIT_LABEL[p.unit]}</td>
                                <td className="px-2 py-3 text-right">
                                    <Button
                                        variant="subtle" size="compact-xs"
                                        leftSection={<IconArrowUp size={13} />}
                                        className="opacity-0 group-hover:opacity-100 transition-opacity"
                                        onClick={() => setRaising(p)}
                                    >
                                        Change
                                    </Button>
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>

            {raising && (
                <RaiseModal
                    price={raising}
                    onClose={() => setRaising(null)}
                    onDone={msg => { setRaising(null); showToast(msg); load() }}
                />
            )}
            <Toast toast={toast} />
        </div>
    )
}

// Supersede: a new price row, then everyone on the old one moves to it. Anyone left out stays on the
// old price, which keeps working — that's how grandfathering is done, so it's a visible control here
// rather than something hidden.
const RaiseModal = ({ price, onClose, onDone }: {
    price: Price
    onClose: () => void
    onDone: (msg: string) => void
}) => {
    const [amount, setAmount] = useState<number | string>(price.amount)
    const [usage, setUsage] = useState<Usage | null>(null)
    const [clients, setClients] = useState<ContactListRow[]>([])
    const [excluded, setExcluded] = useState<number[]>([])
    const [error, setError] = useState<string | null>(null)
    const [saving, setSaving] = useState(false)

    useEffect(() => {
        (async () => {
            try {
                const [usageRes, clientsRes] = await Promise.all([
                    fetch(`${API}/prices/${price.id}/usage`),
                    // Everyone enrolled, so the ones on this price can be found and named. The roster
                    // carries each client's current enrollment, which is where rate_id lives.
                    fetch(`${API}/contacts/?enrolled=true&page_size=200`),
                ])
                if (!usageRes.ok) throw new Error(extractError(await usageRes.json(), 'Failed to load usage'))
                setUsage(await usageRes.json())
                if (clientsRes.ok) {
                    const page: ContactPagedResponse = await clientsRes.json()
                    setClients(page.items.filter(c => c.enrollment?.rate_id === price.id))
                }
            } catch (e) {
                setError(e instanceof Error ? e.message : 'Failed to load usage')
            }
        })()
    }, [price.id])

    const submit = async () => {
        if (amount === '' || Number(amount) === price.amount) {
            setError('Enter a different amount.')
            return
        }
        setSaving(true)
        setError(null)
        try {
            const res = await fetch(`${API}/prices/${price.id}/supersede`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    amount: Number(amount),
                    exclude_enrollment_ids: excluded.length ? excluded : null,
                }),
            })
            if (!res.ok) throw new Error(extractError(await res.json(), 'Failed to change price'))
            const moved = (usage?.enrollments ?? 0) - excluded.length
            onDone(
                excluded.length
                    ? `Moved ${moved} client${moved === 1 ? '' : 's'} to ${money(Number(amount))}, ${excluded.length} kept on ${money(price.amount)}.`
                    : `Moved ${moved} client${moved === 1 ? '' : 's'} to ${money(Number(amount))}.`,
            )
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Failed to change price')
        } finally {
            setSaving(false)
        }
    }

    const toggle = (id: number) =>
        setExcluded(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id])

    return (
        <AppModal opened onClose={onClose} title={`Change ${money(price.amount)} ${UNIT_LABEL[price.unit]}`}
            caption="Past invoices and existing sessions keep the old amount. Only future billing changes.">
            {usage === null ? (
                <div className="py-6 flex justify-center"><Loader size="sm" /></div>
            ) : (
                <>
                    <NumberInput
                        label="New amount ($)" value={amount} onChange={setAmount}
                        min={0} decimalScale={2} className="w-44" mb="md"
                    />
                    <p className="text-sm text-gray-500 mb-1">
                        {usage.enrollments} client{usage.enrollments === 1 ? '' : 's'}
                        {usage.booking_links > 0 && ` and ${usage.booking_links} link${usage.booking_links === 1 ? '' : 's'}`}
                        {' '}on this price.
                    </p>
                    {clients.length > 0 && (
                        <div className="mt-3">
                            <p className="text-xs font-medium text-gray-400 uppercase tracking-wide mb-2">
                                Keep anyone on the old price
                            </p>
                            <div className="space-y-1.5 max-h-56 overflow-y-auto">
                                {clients.map(c => (
                                    <label key={c.id} className="flex items-center gap-2.5 text-sm cursor-pointer">
                                        <Checkbox
                                            size="xs"
                                            checked={excluded.includes(c.enrollment!.id)}
                                            onChange={() => toggle(c.enrollment!.id)}
                                        />
                                        <span className="text-gray-700">{c.first_name} {c.last_name}</span>
                                    </label>
                                ))}
                            </div>
                        </div>
                    )}
                </>
            )}
            {error && <p className="text-sm text-red-500 mt-3">{error}</p>}
            <ModalFooter>
                <Button variant="default" onClick={onClose}>Cancel</Button>
                <Button onClick={submit} loading={saving} disabled={usage === null}>Change price</Button>
            </ModalFooter>
        </AppModal>
    )
}

export default Prices
