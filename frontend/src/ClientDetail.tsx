import { useState, useEffect } from 'react'
import { TextInput, NumberInput, Switch, Button, Menu } from '@mantine/core'
import { IconDotsVertical, IconTrash } from '@tabler/icons-react'
import type { Contact, ContactListRow, ContactRelationships } from './types'
import { extractError } from './utils'

const API = import.meta.env.VITE_API_URL

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

    const [first, setFirst] = useState(client.first_name)
    const [last, setLast] = useState(client.last_name)
    const [email, setEmail] = useState(client.email ?? '')
    const [phone, setPhone] = useState(client.phone ?? '')

    // "enrolling" toggles whether the enrollment block is sent at all. Someone who stopped coming
    // gets is_active off, not the block removed — that's what keeps their rate and dates.
    const [enrolling, setEnrolling] = useState(client.enrollment !== null)
    const [rate, setRate] = useState<number | string>(client.enrollment?.rate ?? '')
    const [startDate, setStartDate] = useState(client.enrollment?.start_date ?? '')
    const [isActive, setIsActive] = useState(client.enrollment?.is_active ?? true)
    const [grade, setGrade] = useState<number | string>(client.enrollment?.grade ?? '')
    const [birthday, setBirthday] = useState(client.enrollment?.birthday ?? '')

    const startEdit = () => {
        setFirst(client.first_name); setLast(client.last_name)
        setEmail(client.email ?? ''); setPhone(client.phone ?? '')
        setEnrolling(client.enrollment !== null)
        setRate(client.enrollment?.rate ?? ''); setStartDate(client.enrollment?.start_date ?? '')
        setIsActive(client.enrollment?.is_active ?? true)
        setGrade(client.enrollment?.grade ?? ''); setBirthday(client.enrollment?.birthday ?? '')
        setFormError(null)
        setEditing(true)
    }

    const handleSave = async () => {
        if (!first.trim() || !last.trim()) { setFormError('First and last name are required.'); return }
        if (enrolling && (rate === '' || Number(rate) <= 0)) { setFormError('Rate must be greater than 0.'); return }
        if (enrolling && !startDate) { setFormError('Start date is required.'); return }
        setSaving(true)
        try {
            const res = await fetch(`${API}/contacts/${client.id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    first_name: first.trim(),
                    last_name: last.trim(),
                    email: email.trim() || null,
                    phone: phone.trim() || null,
                    ...(enrolling && {
                        enrollment: {
                            rate: Number(rate),
                            start_date: startDate,
                            is_active: isActive,
                            grade: grade === '' ? null : Number(grade),
                            birthday: birthday || null,
                        },
                    }),
                }),
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
                            <Row label="Rate">${e.rate}/hr</Row>
                            <Row label="Started">{e.start_date}</Row>
                            <Row label="Status">{e.is_active ? 'Active' : <span className="text-gray-400">Inactive</span>}</Row>
                            {e.grade !== null && <Row label="Grade">{e.grade}</Row>}
                            {e.birthday && <Row label="Birthday">{e.birthday}</Row>}
                        </>
                    ) : (
                        <p className="text-sm text-gray-400">Not enrolled — no negotiated rate, bookings use the link's price.</p>
                    )}
                </Section>
                {/* Written by bookings, so shown only when there's something to show. */}
                {relationships && relationships.manages.length > 0 && (
                    <Section title="Books for">
                        {relationships.manages.map(c => <PersonLink key={c.id} c={c} onClick={() => onSelect(c.id)} />)}
                    </Section>
                )}
                {relationships && relationships.managed_by.length > 0 && (
                    <Section title="Booked for by">
                        {relationships.managed_by.map(c => <PersonLink key={c.id} c={c} onClick={() => onSelect(c.id)} />)}
                    </Section>
                )}
                <div className="flex items-center gap-2 mt-8">
                    <Button onClick={startEdit}>Edit</Button>
                    <Menu shadow="md" width={220} position="bottom-start">
                        <Menu.Target>
                            <button className="p-2 rounded-md text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors">
                                <IconDotsVertical size={16} />
                            </button>
                        </Menu.Target>
                        <Menu.Dropdown>
                            {/* Behind the dots on purpose: removing an enrollment is for mistakes. Someone
                                who stopped coming gets the Active switch, which keeps their rate and dates. */}
                            {e && (
                                <Menu.Item leftSection={<IconTrash size={14} />} onClick={onRemoveEnrollment}>
                                    Remove enrollment
                                </Menu.Item>
                            )}
                            <Menu.Item leftSection={<IconTrash size={14} />} color="red" onClick={onDelete}>
                                Delete client
                            </Menu.Item>
                        </Menu.Dropdown>
                    </Menu>
                </div>
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
            <Section title="Enrollment">
                <Switch label="Enrolled" checked={enrolling} onChange={e => setEnrolling(e.currentTarget.checked)} className="mb-3" />
                {enrolling && (
                    <div className="grid grid-cols-2 gap-3">
                        <NumberInput label="Rate ($/hr)" value={rate} onChange={setRate} min={0} />
                        <TextInput label="Start date" type="date" value={startDate} onChange={e => setStartDate(e.target.value)} />
                        <NumberInput label="Grade" value={grade} onChange={setGrade} min={0} />
                        <TextInput label="Birthday" type="date" value={birthday} onChange={e => setBirthday(e.target.value)} />
                        <Switch label="Active" checked={isActive} onChange={e => setIsActive(e.currentTarget.checked)} className="col-span-2" />
                    </div>
                )}
            </Section>
            {formError && <p className="text-sm text-red-500 mb-3">{formError}</p>}
            <div className="flex justify-end gap-2 mt-6">
                <Button variant="subtle" color="gray" onClick={() => setEditing(false)}>Cancel</Button>
                <Button loading={saving} onClick={handleSave}>Save</Button>
            </div>
        </>
    )
}

export default ClientDetail
