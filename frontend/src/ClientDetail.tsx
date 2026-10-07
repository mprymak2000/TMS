import { useState, useEffect } from 'react'
import { TextInput, NumberInput, Select, Button, Menu } from '@mantine/core'
import { IconDotsVertical, IconTrash, IconPlus, IconReceipt } from '@tabler/icons-react'
import type { Contact, ContactListRow, ContactRelationships, Enrollment, InvoiceItem } from './types'
import { extractError } from './utils'
import ContactPicker from './ContactPicker'
import AppModal, { ModalFooter } from './AppModal'

const API = import.meta.env.VITE_API_URL

const RATE_LABEL = { per_session: ' per session', per_hour: '/hr', per_month: '/mo' } as const

interface Props {
    client: ContactListRow
    onSaved: (updated: ContactListRow) => void
    // Hop to a related person (a payer's dependent, a dependent's payer).
    onSelect: (id: number) => void
    // Both destructive, so the parent confirms and performs them.
    onDelete: () => void
    onRemoveEnrollment: () => void
}

const PersonLink = ({ c, onClick }: { c: Contact; onClick: () => void }) => (
    <button onClick={onClick} className="block text-sm text-indigo-600 hover:underline py-0.5">
        {c.first_name} {c.last_name}
    </button>
)

const Row = ({ label, children }: { label: string; children: React.ReactNode }) => (
    <div className="flex items-baseline gap-3 text-sm py-1.5">
        <span className="w-24 shrink-0 text-gray-400">{label}</span>
        <span className="text-gray-800 min-w-0">{children}</span>
    </div>
)

const Section = ({ title, children }: { title: string; children: React.ReactNode }) => (
    <div className="mb-6">
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">{title}</h3>
        {children}
    </div>
)

// Read-only until Edit, then one form covering identity and enrollment, one Save. Enrollment is a
// child of the client, so it's edited here rather than on a page of its own.
const ClientDetail = ({ client, onSaved, onSelect, onDelete, onRemoveEnrollment }: Props) => {
    const [editing, setEditing] = useState(false)
    const [saving, setSaving] = useState(false)
    const [formError, setFormError] = useState<string | null>(null)

    // Fetched here, not on the roster row: only one panel is open at a time.
    const [relationships, setRelationships] = useState<ContactRelationships | null>(null)
    useEffect(() => {
        fetch(`${API}/contacts/${client.id}/relationships`)
            .then(res => (res.ok ? res.json() : null))
            .then(setRelationships)
            .catch(() => setRelationships(null))
    }, [client.id])

    // Every stint, newest first. The roster and the section above carry only the open one, so this
    // is the only place a closed stint's old rate is visible.
    const [stints, setStints] = useState<Enrollment[]>([])
    useEffect(() => {
        fetch(`${API}/contacts/${client.id}/enrollments`)
            .then(res => (res.ok ? res.json() : []))
            .then(setStints)
            .catch(() => setStints([]))
    }, [client.id])

    const [first, setFirst] = useState(client.first_name)
    const [last, setLast] = useState(client.last_name)
    const [email, setEmail] = useState(client.email ?? '')
    const [phone, setPhone] = useState(client.phone ?? '')

    // Whether the form carries an enrollment block. Only ever turned on — by the row already having
    // one, or by Enroll in the dots menu. Leaving the block out means "untouched", not "remove", so
    // there's no switch that could pretend otherwise; removal is its own guarded action.
    const [enrolling, setEnrolling] = useState(client.enrollment !== null)
    const [rate, setRate] = useState<number | string>(client.enrollment?.rate ?? '')
    const [rateUnit, setRateUnit] = useState<string | null>(client.enrollment?.rate_unit ?? null)
    const [startedOn, setStartedOn] = useState(client.enrollment?.started_on ?? '')
    // Setting this closes the stint. The next save then opens a new one, which is what re-enrolling is.
    const [endedOn, setEndedOn] = useState(client.enrollment?.ended_on ?? '')
    // Who gets the invoice. Null means they pay for themselves.
    const [payerId, setPayerId] = useState<number | null>(client.enrollment?.payer_id ?? null)
    const [charging, setCharging] = useState(false)
    const [grade, setGrade] = useState<number | string>(client.enrollment?.grade ?? '')
    const [birthday, setBirthday] = useState(client.enrollment?.birthday ?? '')

    // withEnrollment: blank the enrollment fields and show them, for Enroll off the dots menu.
    const startEdit = (withEnrollment = false) => {
        setFirst(client.first_name); setLast(client.last_name)
        setEmail(client.email ?? ''); setPhone(client.phone ?? '')
        setEnrolling(withEnrollment || client.enrollment !== null)
        setRate(client.enrollment?.rate ?? ''); setRateUnit(client.enrollment?.rate_unit ?? null)
        setStartedOn(client.enrollment?.started_on ?? ''); setEndedOn(client.enrollment?.ended_on ?? '')
        setPayerId(client.enrollment?.payer_id ?? null)
        setGrade(client.enrollment?.grade ?? ''); setBirthday(client.enrollment?.birthday ?? '')
        setFormError(null)
        setEditing(true)
    }

    // Both sides of the dirty check go through here, so the field list and key order match and
    // JSON.stringify can compare them. Only the fields EnrollmentInput accepts — it forbids extras,
    // so `id` and `contact_id` stay out.
    const buildPayload = (
        c: Pick<ContactListRow, 'first_name' | 'last_name' | 'email' | 'phone'>,
        e: Enrollment | null,
    ) => ({
        first_name: c.first_name,
        last_name: c.last_name,
        email: c.email || null,
        phone: c.phone || null,
        ...(e && {
            enrollment: {
                started_on: e.started_on,
                ended_on: e.ended_on,
                rate_unit: e.rate_unit,
                rate: e.rate,
                payer_id: e.payer_id,
                grade: e.grade,
                birthday: e.birthday,
            },
        }),
    })

    const handleSave = async () => {
        if (!first.trim() || !last.trim()) { setFormError('First and last name are required.'); return }
        if (enrolling && !startedOn) { setFormError('Start date is required.'); return }
        // A rate needs a unit to mean anything. A unit with no rate is fine: plan picked, not priced.
        if (enrolling && rate !== '' && !rateUnit) { setFormError('Pick how they are charged.'); return }

        const payload = buildPayload(
            { first_name: first.trim(), last_name: last.trim(), email: email.trim(), phone: phone.trim() },
            enrolling ? {
                id: 0, contact_id: client.id, rate_id: null,   // not sent; buildPayload drops them
                payer_id: payerId,
                started_on: startedOn,
                ended_on: endedOn || null,
                rate_unit: (rateUnit as Enrollment['rate_unit']) ?? null,
                rate: rate === '' ? null : Number(rate),
                grade: grade === '' ? null : Number(grade),
                birthday: birthday || null,
            } : null,
        )
        // Nothing changed, so Save is just Close — no request, no toast.
        if (JSON.stringify(payload) === JSON.stringify(buildPayload(client, client.enrollment))) {
            setEditing(false)
            return
        }

        setSaving(true)
        try {
            const res = await fetch(`${API}/contacts/${client.id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            })
            if (!res.ok) { setFormError(extractError(await res.json(), 'Failed to save')); return }
            onSaved(await res.json())
            setEditing(false)
        } catch {
            setFormError('An unknown error occurred while saving.')
        } finally {
            setSaving(false)
        }
    }

    if (!editing) {
        const e = client.enrollment
        return (
            <>
                <Section title="Contact">
                    <Row label="Email">{client.email ?? <span className="text-gray-300">—</span>}</Row>
                    <Row label="Phone">{client.phone ?? <span className="text-gray-300">—</span>}</Row>
                    <Row label="Added">{new Date(client.created).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}</Row>
                    {client.verified_at && <Row label="Verified">✓</Row>}
                </Section>
                <Section title="Enrollment">
                    {e ? (
                        <>
                            <Row label="Rate">
                                {e.rate === null
                                    ? <span className="text-gray-400">No rate set</span>
                                    : `$${e.rate}${e.rate_unit ? RATE_LABEL[e.rate_unit] : ''}`}
                            </Row>
                            <Row label="Started">{e.started_on}</Row>
                            {e.ended_on && <Row label="Ended">{e.ended_on}</Row>}
                            {e.payer_id !== null && (
                                <Row label="Billed to">
                                    <button onClick={() => onSelect(e.payer_id!)} className="text-indigo-600 hover:underline">
                                        View payer
                                    </button>
                                </Row>
                            )}
                            {e.grade !== null && <Row label="Grade">{e.grade}</Row>}
                            {e.birthday && <Row label="Birthday">{e.birthday}</Row>}
                        </>
                    ) : (
                        <p className="text-sm text-gray-400">Not enrolled — no negotiated rate, bookings use the link's price.</p>
                    )}
                </Section>
                {/* Only worth a section once there's more than the open stint showing above. */}
                {stints.length > 1 && (
                    <Section title="History">
                        {stints.map(st => (
                            <div key={st.id} className="flex items-baseline justify-between text-sm py-0.5">
                                <span className={st.ended_on === null ? 'text-gray-700' : 'text-gray-400'}>
                                    {st.started_on} → {st.ended_on ?? 'now'}
                                </span>
                                <span className={st.ended_on === null ? 'text-gray-700' : 'text-gray-400'}>
                                    {st.rate === null
                                        ? 'no rate'
                                        : `$${st.rate}${st.rate_unit ? RATE_LABEL[st.rate_unit] : ''}`}
                                </span>
                            </div>
                        ))}
                    </Section>
                )}
                {/* Written by bookings, so shown only when there's something to show. */}
                {relationships && relationships.manages.length > 0 && (
                    <Section title="Pays for">
                        {relationships.manages.map(c => <PersonLink key={c.id} c={c} onClick={() => onSelect(c.id)} />)}
                    </Section>
                )}
                {relationships && relationships.managed_by.length > 0 && (
                    <Section title="Payer">
                        {relationships.managed_by.map(c => <PersonLink key={c.id} c={c} onClick={() => onSelect(c.id)} />)}
                    </Section>
                )}
                <div className="flex items-center gap-2 mt-8">
                    <Button onClick={() => startEdit()}>Edit</Button>
                    <Menu shadow="md" width={220} position="bottom-start">
                        <Menu.Target>
                            <button className="p-2 rounded-md text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors">
                                <IconDotsVertical size={16} />
                            </button>
                        </Menu.Target>
                        <Menu.Dropdown>
                            {/* Enrolling and unenrolling are deliberate acts, so they live here rather
                                than as a switch in the form. Removing is for mistakes — someone who
                                stopped coming gets Active off, which keeps their rate and dates. */}
                            {e ? (
                                <Menu.Item leftSection={<IconTrash size={14} />} onClick={onRemoveEnrollment}>
                                    Remove enrollment
                                </Menu.Item>
                            ) : (
                                <Menu.Item leftSection={<IconPlus size={14} />} onClick={() => startEdit(true)}>
                                    Enroll
                                </Menu.Item>
                            )}
                            <Menu.Item leftSection={<IconReceipt size={14} />} onClick={() => setCharging(true)}>
                                Add a charge
                            </Menu.Item>
                            <Menu.Item leftSection={<IconTrash size={14} />} color="red" onClick={onDelete}>
                                Delete client
                            </Menu.Item>
                        </Menu.Dropdown>
                    </Menu>
                </div>
                {charging && <ChargeModal payerId={client.id} onClose={() => setCharging(false)} />}
            </>
        )
    }

    return (
        <>
            <Section title="Contact">
                <div className="grid grid-cols-2 gap-3">
                    <TextInput label="First name" value={first} onChange={e => setFirst(e.target.value)} />
                    <TextInput label="Last name" value={last} onChange={e => setLast(e.target.value)} />
                    <TextInput label="Email" value={email} onChange={e => setEmail(e.target.value)} />
                    <TextInput label="Phone" value={phone} onChange={e => setPhone(e.target.value)} />
                </div>
            </Section>
            {enrolling && (
                <Section title="Enrollment">
                    <div className="grid grid-cols-2 gap-3">
                        <NumberInput label="Rate" value={rate} onChange={setRate} min={0} />
                        <Select
                            label="Charged"
                            data={[
                                { value: 'per_session', label: 'Per session' },
                                { value: 'per_hour', label: 'Per hour' },
                                { value: 'per_month', label: 'Per month' },
                            ]}
                            value={rateUnit}
                            onChange={setRateUnit}
                            clearable
                        />
                        <TextInput label="Started" type="date" value={startedOn} onChange={e => setStartedOn(e.target.value)} />
                        {/* Setting this closes the stint. Saving again afterwards opens a new one. */}
                        <TextInput label="Ended" type="date" value={endedOn} onChange={e => setEndedOn(e.target.value)} />
                        <div className="col-span-2">
                            <ContactPicker
                                value={payerId}
                                onChange={setPayerId}
                                label="Billed to"
                                placeholder="They pay for themselves"
                            />
                        </div>
                        <NumberInput label="Grade" value={grade} onChange={setGrade} min={0} />
                        <TextInput label="Birthday" type="date" value={birthday} onChange={e => setBirthday(e.target.value)} />
                    </div>
                </Section>
            )}
            {formError && <p className="text-sm text-red-500 mb-3">{formError}</p>}
            <div className="flex justify-end gap-2 mt-6">
                <Button variant="subtle" color="gray" onClick={() => setEditing(false)}>Cancel</Button>
                <Button loading={saving} onClick={handleSave}>Save</Button>
            </div>
            {charging && <ChargeModal payerId={client.id} onClose={() => setCharging(false)} />}
        </>
    )
}

// A charge with no booking behind it: materials, a goodwill credit, a late fee. It sits pending
// until the next invoice for this payer sweeps it, so there's nothing to pick a date for.
const ChargeModal = ({ payerId, onClose }: { payerId: number; onClose: () => void }) => {
    const [description, setDescription] = useState('')
    const [amount, setAmount] = useState<number | string>('')
    const [error, setError] = useState<string | null>(null)
    const [saving, setSaving] = useState(false)
    const [saved, setSaved] = useState<InvoiceItem | null>(null)

    const submit = async () => {
        if (!description.trim()) { setError('Say what it is for.'); return }
        if (amount === '') { setError('An amount is required.'); return }
        setSaving(true)
        setError(null)
        try {
            const res = await fetch(`${API}/invoice-items/`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ payer_id: payerId, description: description.trim(), amount: Number(amount) }),
            })
            if (!res.ok) throw new Error(extractError(await res.json(), 'Failed to add charge'))
            setSaved(await res.json())
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Failed to add charge')
        } finally {
            setSaving(false)
        }
    }

    return (
        <AppModal opened onClose={onClose} title="Add a charge"
            caption="Lands on the next invoice drawn up for this client. Negative for a credit.">
            {saved ? (
                <>
                    <p className="text-sm text-gray-600">Added — it'll appear on their next invoice.</p>
                    <ModalFooter>
                        <Button onClick={onClose}>Done</Button>
                    </ModalFooter>
                </>
            ) : (
                <>
                    <TextInput label="What for" placeholder="Workbook" value={description}
                        onChange={e => setDescription(e.target.value)} mb="sm" />
                    <NumberInput label="Amount ($)" value={amount} onChange={setAmount} decimalScale={2} />
                    {error && <p className="text-sm text-red-500 mt-3">{error}</p>}
                    <ModalFooter>
                        <Button variant="default" onClick={onClose}>Cancel</Button>
                        <Button onClick={submit} loading={saving}>Add</Button>
                    </ModalFooter>
                </>
            )}
        </AppModal>
    )
}

export default ClientDetail
