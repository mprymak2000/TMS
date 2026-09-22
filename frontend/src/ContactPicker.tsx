import { useState, useEffect } from 'react'
import { Select } from '@mantine/core'
import type { ContactListRow } from './types'

const API = import.meta.env.VITE_API_URL

interface Props {
    value: number | null
    onChange: (id: number | null) => void
    enrolled?: boolean       // restrict to contacts with an enrollment
    label?: string
    placeholder?: string
    error?: string
}

// Opens with the first page so a small practice sees everyone; typing asks the server instead, so
// the rest are reachable without ever shipping the whole roster.
const ContactPicker = ({ value, onChange, enrolled = false, label, placeholder = 'Select...', error }: Props) => {
    const [options, setOptions] = useState<ContactListRow[]>([])
    const [search, setSearch] = useState('')

    useEffect(() => {
        const timer = setTimeout(async () => {
            const params = new URLSearchParams({ page_size: '50' })
            if (enrolled) params.set('enrolled', 'true')
            if (search.trim()) params.set('search', search.trim())
            const res = await fetch(`${API}/contacts/?${params}`)
            if (res.ok) setOptions((await res.json()).items)
        }, search ? 250 : 0)
        return () => clearTimeout(timer)
    }, [search, enrolled])

    return (
        <Select
            label={label}
            placeholder={placeholder}
            data={options.map(c => ({ value: String(c.id), label: `${c.first_name} ${c.last_name}` }))}
            value={value === null ? null : String(value)}
            onChange={v => onChange(v === null ? null : Number(v))}
            searchable
            searchValue={search}
            onSearchChange={setSearch}
            filter={({ options }) => options}   // the server already filtered
            error={error}
            clearable
        />
    )
}

export default ContactPicker
