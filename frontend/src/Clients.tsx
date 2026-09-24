import { useState, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { TextInput, Button, Checkbox } from '@mantine/core'
import AppModal, { ModalFooter } from './AppModal'
import { IconSearch, IconChevronLeft, IconChevronRight, IconArrowUp, IconArrowDown } from '@tabler/icons-react'
import type { ContactListRow, ContactPagedResponse } from './types'
import { extractError } from './utils'
import Toast from './Toast'
import { useToast } from './useToast'
import SidePanel from './SidePanel'
import ClientDetail from './ClientDetail'

const API = import.meta.env.VITE_API_URL
const PAGE_SIZE = 50

type Sort = 'name' | 'email' | 'created'

// Enrollment is what the roster shows about a person beyond identity: the rate, and whether they're
// still active. Roles (payer / attendee) live on bookings, not here — the filters answer those.
const EnrollmentCell = ({ e }: { e: ContactListRow['enrollment'] }) => {
    if (!e) return <span className="text-xs text-gray-300">—</span>
    return (
        <span className={`text-xs ${e.is_active ? 'text-gray-700' : 'text-gray-400'}`}>
            ${e.rate}/hr{!e.is_active && ' · Inactive'}
        </span>
    )
}

const SortableHeader = ({ label, column, sort, direction, onSort }: {
    label: string
    column: Sort
    sort: Sort
    direction: 'asc' | 'desc'
    onSort: (column: Sort) => void
}) => (
    <th className="px-5 py-3 font-medium">
        <button onClick={() => onSort(column)} className="flex items-center gap-1 hover:text-gray-600 transition-colors">
            {label}
            {sort === column && (direction === 'asc' ? <IconArrowUp size={12} /> : <IconArrowDown size={12} />)}
        </button>
    </th>
)

interface ContactForm {
    first_name: string
    last_name: string
    email: string
    phone: string
}

const EMPTY_FORM: ContactForm = { first_name: '', last_name: '', email: '', phone: '' }

const Clients = () => {
    const [contacts, setContacts] = useState<ContactListRow[]>([])
    const [total, setTotal] = useState(0)
    const [loading, setLoading] = useState(true)
    const [loadError, setLoadError] = useState<string | null>(null)
    const { toast, showToast } = useToast()

    // The query string is the source of truth for what's being viewed, so a filtered list can be
    // linked, bookmarked and reached with the back button. Everything comes back as a string.
    const [params, setParams] = useSearchParams()
    const search = params.get('search') ?? ''
    const sort = (params.get('sort') ?? 'name') as Sort
    const direction = (params.get('direction') ?? 'asc') as 'asc' | 'desc'
    const page = Number(params.get('page') ?? 1)
    // Two independent booleans, not one mode: a payer can also be enrolled, and ticking both finds them.
    const enrolled = params.get('enrolled') === 'true'
    const manages = params.get('manages') === 'true'

    // setParams replaces the whole query string, so merge onto the previous one. An empty value is
    // deleted rather than written, otherwise clearing the search box leaves a trailing "?search=".
    const updateParams = (changes: Record<string, string | null>, replace = false) => {
        setParams(prev => {
            const next = new URLSearchParams(prev)
            for (const [key, value] of Object.entries(changes)) {
                if (!value) next.delete(key)
                else next.set(key, value)
            }
            return next
        }, { replace })
    }

    // The box is local so typing stays instant; the URL catches up once you pause. Without the
    // debounce every keystroke would be a request and a history entry, and out-of-order responses
    // could leave the list showing results for a prefix of what was typed.
    const [searchInput, setSearchInput] = useState(search)
    useEffect(() => {
        if (searchInput === search) return
        const timer = setTimeout(() => {
            // Push, so back undoes a search. The debounce is what makes that safe: keystrokes
            // collapse into one write per pause in typing, not one per character.
            updateParams({ search: searchInput, page: null })
        }, 250)
        return () => clearTimeout(timer)
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [searchInput])

    // The other direction: back/forward, or opening a link with ?search= already set, moves the URL
    // without touching the box. No loop — the debounce above bails when the two already agree.
    useEffect(() => {
        setSearchInput(search)
    }, [search])

    // Create is a modal; everything about an existing client happens in the side panel.
    const [creating, setCreating] = useState(false)
    const [form, setForm] = useState<ContactForm>(EMPTY_FORM)
    const [formError, setFormError] = useState<string | null>(null)
    const [saving, setSaving] = useState(false)

    // The panel holds its own row rather than looking one up in the list: a hop to a related
    // person can land on someone who isn't on the current page.
    const [selected, setSelected] = useState<ContactListRow | null>(null)
    const [confirming, setConfirming] = useState<'delete' | 'unenroll' | null>(null)
    const [confirmError, setConfirmError] = useState<string | null>(null)

    const selectById = async (id: number) => {
        const onPage = contacts.find(c => c.id === id)
        if (onPage) { setSelected(onPage); return }
        const res = await fetch(`${API}/contacts/${id}`)
        if (res.ok) setSelected(await res.json())
    }

    // Our own writes are the only thing that changes a row, so update both places we hold it.
    const patchRow = (updated: ContactListRow) => {
        setContacts(prev => prev.map(c => (c.id === updated.id ? updated : c)))
        setSelected(updated)
    }

    const loadContacts = async () => {
        setLoading(true)
        try {
            const query = new URLSearchParams({
                sort,
                direction,
                page: String(page),
                page_size: String(PAGE_SIZE),
            })
            if (search.trim()) query.set('search', search.trim())
            if (enrolled) query.set('enrolled', 'true')
            if (manages) query.set('manages', 'true')
            const res = await fetch(`${API}/contacts/?${query}`)
            if (!res.ok) {
                setLoadError(extractError(await res.json(), 'Failed to load clients'))
                return
            }
            const body: ContactPagedResponse = await res.json()
            setContacts(body.items)
            setTotal(body.total)
            setLoadError(null)
        } catch {
            setLoadError('An unknown error occurred while loading clients.')
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => {
        loadContacts()
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [search, sort, direction, page, enrolled, manages])

    // Flipping a filter drops the page: page 3 of one set means nothing in another.
    const toggleFilter = (key: 'enrolled' | 'manages', on: boolean) =>
        updateParams({ [key]: on ? 'true' : null, page: null })

    // Clicking the active column flips direction; a new column starts ascending. Either way the
    // page number is dropped, since page 3 of the old ordering means nothing in the new one.
    const handleSort = (column: Sort) => {
        updateParams({
            sort: column,
            direction: sort === column && direction === 'asc' ? 'desc' : 'asc',
            page: null,
        })
    }

    const openCreate = () => {
        setForm(EMPTY_FORM)
        setFormError(null)
        setCreating(true)
    }

    const handleCreate = async () => {
        if (!form.first_name.trim() || !form.last_name.trim()) {
            setFormError('First and last name are required.')
            return
        }
        setSaving(true)
        try {
            const res = await fetch(`${API}/contacts/`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    first_name: form.first_name.trim(),
                    last_name: form.last_name.trim(),
                    email: form.email.trim() || null,
                    phone: form.phone.trim() || null,
                }),
            })
            if (!res.ok) {
                setFormError(extractError(await res.json(), 'Failed to save client'))
                return
            }
            setCreating(false)
            showToast('Client added')
            loadContacts()
        } catch {
            setFormError('An unknown error occurred while saving.')
        } finally {
            setSaving(false)
        }
    }

    // Both restricted server-side (bookings, lessons, dependents), so a 409 is the expected answer
    // rather than an edge case — surface its message instead of a generic failure.
    const handleConfirm = async () => {
        if (!selected || !confirming) return
        setSaving(true)
        try {
            const url = confirming === 'delete'
                ? `${API}/contacts/${selected.id}`
                : `${API}/contacts/${selected.id}/enrollment`
            const res = await fetch(url, { method: 'DELETE' })
            if (!res.ok) {
                setConfirmError(extractError(await res.json(), 'Failed to delete'))
                return
            }
            setConfirming(null)
            if (confirming === 'delete') {
                setSelected(null)
                showToast('Client deleted')
                loadContacts()
            } else {
                patchRow({ ...selected, enrollment: null })
                showToast('Enrollment removed')
            }
        } catch {
            setConfirmError('An unknown error occurred while deleting.')
        } finally {
            setSaving(false)
        }
    }

    const lastPage = Math.max(1, Math.ceil(total / PAGE_SIZE))

    return (
        <div>
            <div className="flex items-start justify-between mb-6">
                <div>
                    <h1 className="text-2xl font-bold text-gray-900">Clients</h1>
                    <p className="text-sm text-gray-400 mt-1">
                        Everyone who books or is booked for. Rows are created by bookings — add one by hand
                        only for someone who hasn't booked yet.
                    </p>
                </div>
                <Button onClick={openCreate}>Add client</Button>
            </div>

            <div className="flex items-center justify-between mb-4 gap-4">
                <div className="flex items-center gap-4">
                    <TextInput
                        placeholder="Search by name, email or phone..."
                        leftSection={<IconSearch size={14} />}
                        value={searchInput}
                        onChange={e => setSearchInput(e.target.value)}
                        className="w-80"
                    />
                    <Checkbox label="Enrolled" checked={enrolled} onChange={e => toggleFilter('enrolled', e.currentTarget.checked)} />
                    {/* "Payers" in the UI, `manages` on the wire: the column is booking authority,
                        but the person it describes is the one an invoice goes to. */}
                    <Checkbox label="Payers" checked={manages} onChange={e => toggleFilter('manages', e.currentTarget.checked)} />
                </div>
                <span className="text-sm text-gray-400 shrink-0">
                    {total} {total === 1 ? 'client' : 'clients'}
                </span>
            </div>

            {loadError && <p className="text-sm text-red-500 mb-3">{loadError}</p>}

            <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
                <table className="w-full text-sm">
                    <thead>
                        <tr className="border-b border-gray-100 text-left text-xs text-gray-400 uppercase tracking-wide">
                            <SortableHeader label="Name" column="name" sort={sort} direction={direction} onSort={handleSort} />
                            <SortableHeader label="Email" column="email" sort={sort} direction={direction} onSort={handleSort} />
                            <th className="px-5 py-3 font-medium">Phone</th>
                            <th className="px-5 py-3 font-medium">Enrollment</th>
                            <SortableHeader label="Added" column="created" sort={sort} direction={direction} onSort={handleSort} />
                        </tr>
                    </thead>
                    <tbody>
                        {contacts.map(c => (
                            <tr
                                key={c.id}
                                onClick={() => setSelected(c)}
                                className={`border-b border-gray-50 last:border-0 cursor-pointer transition-colors ${c.id === selected?.id ? 'bg-indigo-50/60' : 'hover:bg-gray-50/60'}`}
                            >
                                <td className="px-5 py-3 font-medium text-gray-800">
                                    {c.first_name} {c.last_name}
                                    {c.verified_at && (
                                        <span className="ml-2 text-xs text-emerald-600" title="Email verified">✓</span>
                                    )}
                                </td>
                                <td className="px-5 py-3 text-gray-600">{c.email ?? <span className="text-gray-300">—</span>}</td>
                                <td className="px-5 py-3 text-gray-600">{c.phone ?? <span className="text-gray-300">—</span>}</td>
                                <td className="px-5 py-3"><EnrollmentCell e={c.enrollment} /></td>
                                <td className="px-5 py-3 text-gray-400 text-xs">
                                    {new Date(c.created).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>

                {loading && <p className="text-sm text-gray-400 text-center py-12">Loading...</p>}
                {!loading && contacts.length === 0 && (
                    <p className="text-sm text-gray-400 text-center py-12">
                        {search ? 'No clients match that search.' : 'No clients yet.'}
                    </p>
                )}

                {lastPage > 1 && (
                    <div className="flex items-center justify-between border-t border-gray-100 px-5 py-3">
                        <span className="text-xs text-gray-400">Page {page} of {lastPage}</span>
                        <div className="flex gap-1">
                            <button
                                onClick={() => updateParams({ page: String(page - 1) })}
                                disabled={page === 1}
                                className="p-1.5 rounded-md text-gray-400 hover:text-gray-700 hover:bg-gray-100 disabled:opacity-30 disabled:hover:bg-transparent transition-colors"
                            >
                                <IconChevronLeft size={16} />
                            </button>
                            <button
                                onClick={() => updateParams({ page: String(page + 1) })}
                                disabled={page >= lastPage}
                                className="p-1.5 rounded-md text-gray-400 hover:text-gray-700 hover:bg-gray-100 disabled:opacity-30 disabled:hover:bg-transparent transition-colors"
                            >
                                <IconChevronRight size={16} />
                            </button>
                        </div>
                    </div>
                )}
            </div>

            <SidePanel
                open={selected !== null}
                onClose={() => setSelected(null)}
                title={selected && (
                    <h2 className="text-lg font-semibold text-gray-900 truncate">
                        {selected.first_name} {selected.last_name}
                    </h2>
                )}
            >
                {/* Keyed so switching rows remounts the form instead of carrying a draft over. */}
                {selected && (
                    <ClientDetail
                        key={selected.id}
                        client={selected}
                        onSaved={updated => { patchRow(updated); showToast('Client updated') }}
                        onSelect={selectById}
                        onDelete={() => { setConfirmError(null); setConfirming('delete') }}
                        onRemoveEnrollment={() => { setConfirmError(null); setConfirming('unenroll') }}
                    />
                )}
            </SidePanel>

            <AppModal
                opened={creating}
                onClose={() => setCreating(false)}
                title="Add client"
                caption="Only needed for someone who hasn't booked — a booking creates its own client."
            >
                {/* Paired two-up: a name or a phone number is short, and a full-bleed input across
                    the whole dialog reads as a text area rather than a field. */}
                <div className="grid grid-cols-2 gap-x-5 gap-y-5">
                    <TextInput
                        label="First name"
                        value={form.first_name}
                        onChange={e => { setForm(f => ({ ...f, first_name: e.target.value })); setFormError(null) }}
                    />
                    <TextInput
                        label="Last name"
                        value={form.last_name}
                        onChange={e => { setForm(f => ({ ...f, last_name: e.target.value })); setFormError(null) }}
                    />
                    {/* Optional because a dependent often has no address of their own. It's the key
                        contacts are matched on, so a collision comes back as a 409. */}
                    <TextInput
                        label="Email"
                        value={form.email}
                        onChange={e => { setForm(f => ({ ...f, email: e.target.value })); setFormError(null) }}
                    />
                    <TextInput
                        label="Phone"
                        value={form.phone}
                        onChange={e => { setForm(f => ({ ...f, phone: e.target.value })); setFormError(null) }}
                    />
                </div>
                {formError && <p className="text-sm text-red-500 mt-5">{formError}</p>}
                <ModalFooter>
                    <Button variant="subtle" color="gray" onClick={() => setCreating(false)}>Cancel</Button>
                    <Button loading={saving} onClick={handleCreate}>Save</Button>
                </ModalFooter>
            </AppModal>

            <AppModal
                opened={confirming !== null}
                onClose={() => setConfirming(null)}
                title={!selected ? '' : confirming === 'delete'
                    ? `Delete ${selected.first_name} ${selected.last_name}?`
                    : `Remove ${selected.first_name}'s enrollment?`}
                caption={confirming === 'delete'
                    ? 'This can\'t be undone. A client with bookings, lessons or dependents can\'t be deleted — repoint those first, or leave the row in place.'
                    : 'This throws away their rate, start date and grade. If they just stopped coming, mark the enrollment inactive instead.'}
            >
                {confirmError && <p className="text-sm text-red-500 mb-3">{confirmError}</p>}
                <ModalFooter>
                    <Button variant="subtle" color="gray" onClick={() => setConfirming(null)}>Keep</Button>
                    <Button color="red" loading={saving} onClick={handleConfirm}>
                        {confirming === 'delete' ? 'Delete' : 'Remove'}
                    </Button>
                </ModalFooter>
            </AppModal>

            <Toast toast={toast} />
        </div>
    )
}

export default Clients
