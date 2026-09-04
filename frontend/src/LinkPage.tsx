import { useState, useEffect } from 'react'
import { useParams, useSearchParams, useNavigate } from 'react-router-dom'
import { Textarea, Switch, NumberInput, Select, Button, Loader, Input, Modal } from '@mantine/core'
import { IconChevronLeft, IconPlus, IconTrash, IconExternalLink, IconFileDescription, IconClock, IconRepeat, IconUsers, IconBan, IconAdjustmentsHorizontal, IconCreditCard, IconCopy, IconCheck, IconLinkOff } from '@tabler/icons-react'
import type { Tutor, Schedule, BookingLink } from './types'
import { extractError } from './utils'
import { useToast } from './useToast'
import Toast from './Toast'

type NoticeUnit = 'minutes' | 'hours' | 'days'
const NOTICE_UNITS = [
    { value: 'minutes', label: 'min' },
    { value: 'hours', label: 'hrs' },
    { value: 'days', label: 'days' },
]
const unitToMinutes = (unit: NoticeUnit) => unit === 'minutes' ? 1 : unit === 'hours' ? 60 : 1440

// Trailing hyphens survive typing ("my-" en route to "my-link") and are trimmed at save.
const slugify = (v: string) => v.toLowerCase().replace(/[^a-z0-9-]+/g, '-').replace(/-{2,}/g, '-').replace(/^-/, '')

// Frozen prefix inside the URL field — only the slug after it is editable.
const BOOK_URL_PREFIX = `${window.location.host}/book/`

// An unrecognised ?tab= matches no panel and renders blank, so it falls back to details.
const TABS = ['details', 'duration', 'recurrence', 'hosts', 'cancellation', 'limits', 'booking'] as const

const WINDOW_MODES = ['auto_window_block', 'auto_window_request', 'request_window']
const CANCEL_MODE_OPTIONS = [
    { value: 'auto', label: 'Always allowed' },
    { value: 'not_allowed', label: 'Not allowed' },
    { value: 'request', label: 'Request only' },
    { value: 'auto_window_block', label: 'Window — allow or block' },
    { value: 'auto_window_request', label: 'Window — allow or request' },
    { value: 'request_window', label: 'Window — request or block' },
]

interface FormState {
    slug: string
    description: string
    recurring: boolean
    recurWeeks: number | null
    expiresOn: string | null
    bookerCanSetRecurUntil: boolean
    durationMinutes: number
    minDurationMinutes: number | null
    maxDurationMinutes: number | null
    bufferMinutes: number | null
    intervalMinutes: number | null
    price: number | null
    cancelMode: string | null
    cancelNoticeMinutes: number | null
    rescheduleMode: string | null
    rescheduleNoticeMinutes: number | null
    limitPerDay: number | null
    limitPerWeek: number | null
    limitPerMonth: number | null
    limitPerBooker: number | null
    limitFutureBookingsDays: number | null
    onlyShowFirstSlot: boolean
    tutorRows: { tutorId: string | null, scheduleId: string | null }[]
}

interface FormErrors {
    slug?: string
    durationMinutes?: string
    minDurationMinutes?: string
    maxDurationMinutes?: string
    recurWeeks?: string
    tutorRows?: string
    cancelNoticeMinutes?: string
    rescheduleNoticeMinutes?: string
}

interface FormTouched {
    slug?: boolean
    durationMinutes?: boolean
    minDurationMinutes?: boolean
    maxDurationMinutes?: boolean
    recurWeeks?: boolean
    tutorRows?: boolean
    cancelNoticeMinutes?: boolean
    rescheduleNoticeMinutes?: boolean
}

const buildInitial = (link: BookingLink | null): FormState => ({
    slug: link?.slug ?? '',
    description: link?.description ?? '',
    recurring: link?.recurring ?? false,
    recurWeeks: link?.recur_weeks ?? null,
    expiresOn: link?.expires_on ?? null,
    bookerCanSetRecurUntil: link?.booker_can_set_recur_until ?? false,
    durationMinutes: link?.duration_minutes ?? 90,
    minDurationMinutes: link?.min_duration_minutes ?? null,
    maxDurationMinutes: link?.max_duration_minutes ?? null,
    bufferMinutes: link?.buffer_minutes ?? null,
    intervalMinutes: link?.interval_minutes ?? null,
    price: link?.price ?? null,
    cancelMode: link?.cancel_mode ?? null,
    cancelNoticeMinutes: link?.cancel_notice_minutes ?? null,
    rescheduleMode: link?.reschedule_mode ?? null,
    rescheduleNoticeMinutes: link?.reschedule_notice_minutes ?? null,
    limitPerDay: link?.limit_per_day ?? null,
    limitPerWeek: link?.limit_per_week ?? null,
    limitPerMonth: link?.limit_per_month ?? null,
    limitPerBooker: link?.limit_per_booker ?? null,
    limitFutureBookingsDays: link?.limit_future_bookings_days ?? null,
    onlyShowFirstSlot: link?.only_show_first_slot ?? false,
    tutorRows: link?.availability.map(a => ({ tutorId: String(a.tutor_id), scheduleId: String(a.schedule_id) })) ?? [],
})

const validate = (f: FormState): FormErrors => {
    const errs: FormErrors = {}
    if (!f.slug.trim()) errs.slug = 'URL is required'
    if (f.minDurationMinutes !== null) {
        if (f.minDurationMinutes <= 0) errs.minDurationMinutes = 'Must be positive'
        if (f.maxDurationMinutes === null || f.maxDurationMinutes <= 0) errs.maxDurationMinutes = 'Must be positive'
        if (f.minDurationMinutes !== null && f.maxDurationMinutes !== null && f.minDurationMinutes > f.maxDurationMinutes)
            errs.maxDurationMinutes = 'Max must exceed min'
    } else {
        if (f.durationMinutes <= 0) errs.durationMinutes = 'Must be positive'
    }
    if (f.recurring && f.recurWeeks !== null && f.recurWeeks < 2) errs.recurWeeks = 'Must be at least 2 weeks'
    if (f.tutorRows.length === 0) errs.tutorRows = 'At least one host is required'
    if (f.tutorRows.some(r => !r.tutorId || !r.scheduleId)) errs.tutorRows = 'All rows must have a tutor and schedule'
    if (f.cancelMode && WINDOW_MODES.includes(f.cancelMode) && !(f.cancelNoticeMinutes && f.cancelNoticeMinutes > 0))
        errs.cancelNoticeMinutes = 'Notice period is required'
    if (f.rescheduleMode && WINDOW_MODES.includes(f.rescheduleMode) && !(f.rescheduleNoticeMinutes && f.rescheduleNoticeMinutes > 0))
        errs.rescheduleNoticeMinutes = 'Notice period is required'
    return errs
}

const SectionRow = ({ label, description, children }: { label: string, description?: string, children: React.ReactNode }) => (
    <div className="flex items-center justify-between gap-6 px-4 py-3.5">
        <div className="min-w-0">
            <p className="text-sm font-medium text-gray-700">{label}</p>
            {description && <p className="text-xs text-gray-400 mt-0.5">{description}</p>}
        </div>
        <div className="shrink-0">{children}</div>
    </div>
)

const Group = ({ title, children }: { title: string, children: React.ReactNode }) => (
    <div>
        <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2.5">{title}</p>
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden divide-y divide-gray-100">
            {children}
        </div>
    </div>
)

const LinkPage = () => {
    const { id } = useParams<{ id: string }>()
    const [searchParams, setSearchParams] = useSearchParams()
    const navigate = useNavigate()
    const isNew = id === 'new'
    const tabParam = searchParams.get('tab')
    const activeTab = TABS.includes(tabParam as typeof TABS[number]) ? tabParam! : 'details'

    const [form, setForm] = useState<FormState>(() => buildInitial(null))
    // Snapshot of the form as loaded — same dirty-check approach as ScheduleForm.
    const [initial, setInitial] = useState<FormState>(() => buildInitial(null))
    const [confirmingLeave, setConfirmingLeave] = useState(false)
    const isDirty = JSON.stringify(form) !== JSON.stringify(initial)
    const [tutors, setTutors] = useState<Tutor[]>([])
    const [schedules, setSchedules] = useState<Schedule[]>([])
    const [loading, setLoading] = useState(true)
    const [loadError, setLoadError] = useState<string | null>(null)
    const [saving, setSaving] = useState(false)
    const { toast, showToast } = useToast()
    const [errors, setErrors] = useState<FormErrors>({})
    const [touched, setTouched] = useState<FormTouched>({})
    const [copied, setCopied] = useState(false)
    const [cancelUnit, setCancelUnit] = useState<NoticeUnit>('hours')
    const [rescheduleUnit, setRescheduleUnit] = useState<NoticeUnit>('hours')

    useEffect(() => {
        const load = async () => {
            setLoading(true)
            try {
                const [tutorsRes, schedulesRes] = await Promise.all([
                    fetch(`${import.meta.env.VITE_API_URL}/tutors`),
                    fetch(`${import.meta.env.VITE_API_URL}/schedules`),
                ])
                if (!tutorsRes.ok || !schedulesRes.ok) { setLoadError('Failed to load data'); return }
                const [tutorsData, schedulesData]: [Tutor[], Schedule[]] = await Promise.all([tutorsRes.json(), schedulesRes.json()])
                setTutors(tutorsData)
                setSchedules(schedulesData)
                if (!isNew) {
                    const linkRes = await fetch(`${import.meta.env.VITE_API_URL}/booking_links/${id}`)
                    if (!linkRes.ok) { setLoadError('Booking link not found'); return }
                    const linkData: BookingLink = await linkRes.json()
                    setForm(buildInitial(linkData))
                    setInitial(buildInitial(linkData))
                }
            } catch {
                setLoadError('Failed to load data')
            } finally {
                setLoading(false)
            }
        }
        load()
    }, [id])

    const setField = <K extends keyof FormState>(key: K, value: FormState[K]) =>
        setForm(prev => ({ ...prev, [key]: value }))

    const handleNumericField = (key: keyof FormState, val: number | string) =>
        setField(key as any, val === '' ? null : Number(val))

    const touchAll = () => setTouched({
        slug: true, durationMinutes: true, minDurationMinutes: true, maxDurationMinutes: true,
        recurWeeks: true, tutorRows: true, cancelNoticeMinutes: true, rescheduleNoticeMinutes: true,
    })

    const buildPayload = () => ({
        slug: form.slug.replace(/-+$/, ''),   // trailing hyphen survives typing, not saving
        description: form.description.trim() || null,
        recurring: form.recurring,
        recur_weeks: form.recurring ? form.recurWeeks : null,
        expires_on: form.recurring ? form.expiresOn : null,
        booker_can_set_recur_until: form.recurring ? form.bookerCanSetRecurUntil : false,
        duration_minutes: form.durationMinutes,
        min_duration_minutes: form.minDurationMinutes,
        max_duration_minutes: form.maxDurationMinutes,
        buffer_minutes: form.bufferMinutes,
        interval_minutes: form.intervalMinutes,
        price: form.price,
        cancel_mode: form.cancelMode,
        cancel_notice_minutes: form.cancelNoticeMinutes,
        reschedule_mode: form.rescheduleMode,
        reschedule_notice_minutes: form.rescheduleNoticeMinutes,
        limit_per_day: form.limitPerDay,
        limit_per_week: form.limitPerWeek,
        limit_per_month: form.limitPerMonth,
        limit_per_booker: form.limitPerBooker,
        limit_future_bookings_days: form.limitFutureBookingsDays,
        only_show_first_slot: form.onlyShowFirstSlot,
        availability: form.tutorRows.map(row => ({ tutor_id: Number(row.tutorId), schedule_id: Number(row.scheduleId) })),
    })

    const handleSave = async () => {
        const errs = validate(form)
        if (Object.keys(errs).length > 0) {
            setErrors(errs)
            touchAll()
            // The per-tab dots only help if you're looking at the nav — say it out loud too, since
            // the offending field is often on a tab you aren't on.
            const count = Object.keys(errs).length
            showToast(`Can't save — ${count} ${count === 1 ? 'field needs' : 'fields need'} attention`, 'error')
            return
        }
        setSaving(true)
        try {
            const url = isNew
                ? `${import.meta.env.VITE_API_URL}/booking_links/`
                : `${import.meta.env.VITE_API_URL}/booking_links/${id}`
            const res = await fetch(url, {
                method: isNew ? 'POST' : 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(buildPayload()),
            })
            if (!res.ok) { showToast(extractError(await res.json(), 'Failed to save'), 'error'); return }
            if (isNew) {
                navigate('/links', { state: { toast: 'Booking link created' } })
            } else {
                setInitial(form)   // new baseline, so the page stops reading as dirty
                showToast('Changes saved')
            }
        } catch {
            showToast('An unknown error occurred', 'error')
        } finally {
            setSaving(false)
        }
    }

    const handleTutorRowChange = (index: number, field: 'tutorId' | 'scheduleId', value: string) => {
        const rows = [...form.tutorRows]
        if (field === 'tutorId') {
            const defaultSchedule = schedules.find(s => s.tutor_id === Number(value) && s.is_default)!
            rows[index] = { tutorId: value, scheduleId: String(defaultSchedule.id) }
        } else {
            rows[index] = { ...rows[index], [field]: value }
        }
        const updated = { ...form, tutorRows: rows }
        setForm(updated)
        if (touched.tutorRows) setErrors(validate(updated))
    }

    const tabHasError = {
        details: !!(errors.slug),
        duration: !!(errors.durationMinutes || errors.minDurationMinutes || errors.maxDurationMinutes),
        recurrence: !!(errors.recurWeeks),
        hosts: !!errors.tutorRows,
        cancellation: !!(errors.cancelNoticeMinutes || errors.rescheduleNoticeMinutes),
    }

    const NAV_ITEMS = [
        { heading: 'Setup' },
        { tab: 'details',      label: 'details',      icon: IconFileDescription,        hasError: tabHasError.details },
        { tab: 'duration',     label: 'duration',     icon: IconClock,                  hasError: tabHasError.duration },
        { tab: 'recurrence',   label: 'recurrence',   icon: IconRepeat,                 hasError: tabHasError.recurrence },
        { heading: 'Hosts' },
        { tab: 'hosts',        label: 'hosts',        icon: IconUsers,                  hasError: tabHasError.hosts },
        { heading: 'Policies' },
        { tab: 'cancellation', label: 'cancellation', icon: IconBan,                    hasError: tabHasError.cancellation },
        { tab: 'limits',       label: 'limits',       icon: IconAdjustmentsHorizontal },
        { heading: 'Booking' },
        { tab: 'booking',      label: 'booking',      icon: IconCreditCard },
    ] as const

    if (loading) return (
        <div className="flex items-center justify-center h-64"><Loader size="sm" /></div>
    )

    if (loadError) return (
        <div className="flex flex-col items-center justify-center h-96 text-center">
            <div className="w-12 h-12 rounded-full bg-gray-100 flex items-center justify-center mb-4">
                <IconLinkOff size={22} className="text-gray-400" />
            </div>
            <h2 className="text-lg font-semibold text-gray-800 mb-1.5">{loadError}</h2>
            <p className="text-sm text-gray-500 max-w-sm leading-relaxed mb-5">
                It may have been archived, or the address may be wrong. Archived links stay out of this
                list on purpose — their bookings still work, they just can't take new ones.
            </p>
            <Button variant="default" size="sm" onClick={() => navigate('/links')}>
                Back to links
            </Button>
        </div>
    )

    return (
        <div className="-m-8 flex flex-col overflow-hidden" style={{ height: 'calc(100vh - 3.5rem)' }}>

            {/* Page header */}
            <div className="shrink-0 flex items-center justify-between px-6 py-3.5 bg-white border-b border-gray-200">
                <div className="flex items-center gap-3">
                    <button
                        onClick={() => isDirty ? setConfirmingLeave(true) : navigate('/links')}
                        className="flex items-center justify-center w-7 h-7 rounded-lg text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors"
                    >
                        <IconChevronLeft size={16} />
                    </button>
                    <div className="h-4 w-px bg-gray-200" />
                    <div>
                        <p className="text-xs text-gray-400 leading-none mb-0.5">Links</p>
                        <h1 className="text-sm font-semibold text-gray-900 leading-none">
                            {isNew ? 'New booking link' : form.slug || '...'}
                        </h1>
                    </div>
                </div>
                <div className="flex items-center gap-3">
                    {!isNew && (
                        <Button
                            component="a"
                            href={`/book/${form.slug}`}
                            target="_blank"
                            variant="default"
                            size="xs"
                            leftSection={<IconExternalLink size={12} />}
                        >
                            Preview
                        </Button>
                    )}
                    <Button color="indigo" size="sm" loading={saving} onClick={handleSave}>
                        {isNew ? 'Create' : 'Save changes'}
                    </Button>
                </div>
            </div>

            {/* Body */}
            <div className="flex flex-1 overflow-hidden">

                {/* Left nav */}
                <nav className="w-48 shrink-0 bg-white border-r border-gray-200 py-3 px-2">
                    {NAV_ITEMS.map((item, i) =>
                        'heading' in item ? (
                            <p key={i} className="px-3 pt-4 pb-1 text-xs font-semibold text-gray-500 uppercase tracking-widest first:pt-1">
                                {item.heading}
                            </p>
                        ) : (
                            <button
                                key={item.tab}
                                onClick={() => setSearchParams({ tab: item.tab })}
                                className={`w-full flex items-center justify-between px-3 py-1.5 rounded-lg text-sm transition-colors mb-0.5 ${
                                    activeTab === item.tab
                                        ? 'bg-indigo-50 text-indigo-700 font-medium'
                                        : 'text-gray-500 hover:text-gray-800 hover:bg-gray-50'
                                }`}
                            >
                                    <span className="flex items-center gap-2">
                                    <item.icon size={14} />
                                    <span className="capitalize">{item.label}</span>
                                </span>
                                {'hasError' in item && item.hasError && <span className="w-1.5 h-1.5 rounded-full bg-red-500 shrink-0" />}
                            </button>
                        )
                    )}
                </nav>

                {/* Content */}
                <div className="flex-1 overflow-y-auto bg-gray-50 px-8 py-6">
                    <div className="max-w-2xl space-y-6">

                        {/* DETAILS */}
                        {activeTab === 'details' && (
                            <Group title="Basic info">
                                <div className="p-4 space-y-4">
                                    <Input.Wrapper
                                        label="URL"
                                        description="Where clients book. Freed for reuse if this link is ever archived."
                                        error={touched.slug ? errors.slug : undefined}
                                    >
                                        {/* select-none so a drag only grabs the editable slug. */}
                                        <div className={`mt-1 flex items-center gap-0 rounded-md border px-3 py-1.5 bg-white text-sm leading-5 focus-within:border-indigo-400 ${touched.slug && errors.slug ? 'border-red-400' : 'border-gray-300'}`}>
                                            <span className="text-gray-400 whitespace-nowrap select-none shrink-0">
                                                {BOOK_URL_PREFIX}
                                            </span>
                                            <input
                                                style={{ font: 'inherit' }}
                                                className="flex-1 min-w-0 bg-transparent outline-none"
                                                placeholder="trial-lesson"
                                                value={form.slug}
                                                onChange={e => {
                                                    const updated = { ...form, slug: slugify(e.target.value) }
                                                    setForm(updated)
                                                    if (touched.slug) setErrors(validate(updated))
                                                }}
                                                onBlur={() => setTouched(prev => ({ ...prev, slug: true }))}
                                            />
                                            <button
                                                type="button"
                                                title="Copy link"
                                                className="shrink-0 ml-2 text-gray-400 hover:text-gray-600 transition-colors"
                                                onClick={() => {
                                                    navigator.clipboard.writeText(`${BOOK_URL_PREFIX}${form.slug}`)
                                                    setCopied(true)
                                                    setTimeout(() => setCopied(false), 1500)
                                                }}
                                            >
                                                {copied ? <IconCheck size={15} /> : <IconCopy size={15} />}
                                            </button>
                                        </div>
                                    </Input.Wrapper>
                                    <Textarea
                                        label="Description"
                                        placeholder="What is this booking link for?"
                                        size="sm"
                                        autosize
                                        minRows={2}
                                        value={form.description}
                                        onChange={e => setField('description', e.target.value)}
                                    />
                                </div>
                            </Group>
                        )}

                        {/* DURATION */}
                        {activeTab === 'duration' && (
                            <Group title="Session length">
                                <SectionRow label="Custom duration" description="Let bookers choose their session length">
                                    <Switch
                                        checked={form.minDurationMinutes !== null}
                                        onChange={e => setForm(prev => ({
                                            ...prev,
                                            minDurationMinutes: e.target.checked ? 60 : null,
                                            maxDurationMinutes: e.target.checked ? 120 : null,
                                        }))}
                                        color="indigo" size="sm"
                                    />
                                </SectionRow>
                                <div className="p-4">
                                    {form.minDurationMinutes !== null ? (
                                        <div className="flex gap-3">
                                            <NumberInput
                                                label="Min (min)"
                                                size="sm"
                                                value={form.minDurationMinutes ?? ''}
                                                onChange={val => {
                                                    const updated = { ...form, minDurationMinutes: val === '' ? null : Number(val) }
                                                    setForm(updated)
                                                    if (touched.minDurationMinutes) setErrors(validate(updated))
                                                }}
                                                onBlur={() => setTouched(prev => ({ ...prev, minDurationMinutes: true }))}
                                                error={touched.minDurationMinutes ? errors.minDurationMinutes : undefined}
                                                min={1} className="flex-1"
                                            />
                                            <NumberInput
                                                label="Max (min)"
                                                size="sm"
                                                value={form.maxDurationMinutes ?? ''}
                                                onChange={val => {
                                                    const updated = { ...form, maxDurationMinutes: val === '' ? null : Number(val) }
                                                    setForm(updated)
                                                    if (touched.maxDurationMinutes) setErrors(validate(updated))
                                                }}
                                                onBlur={() => setTouched(prev => ({ ...prev, maxDurationMinutes: true }))}
                                                error={touched.maxDurationMinutes ? errors.maxDurationMinutes : undefined}
                                                min={1} className="flex-1"
                                            />
                                        </div>
                                    ) : (
                                        <NumberInput
                                            label="Duration (min)"
                                            size="sm"
                                            value={form.durationMinutes}
                                            onChange={val => {
                                                const updated = { ...form, durationMinutes: val as number }
                                                setForm(updated)
                                                if (touched.durationMinutes) setErrors(validate(updated))
                                            }}
                                            onBlur={() => setTouched(prev => ({ ...prev, durationMinutes: true }))}
                                            error={touched.durationMinutes ? errors.durationMinutes : undefined}
                                            min={1} className="w-44"
                                        />
                                    )}
                                </div>
                            </Group>
                        )}

                        {/* RECURRENCE */}
                        {activeTab === 'recurrence' && (
                            <Group title="Recurrence">
                                <SectionRow label="Recurring" description="Repeat weekly on the same day">
                                    <Switch
                                        checked={form.recurring}
                                        onChange={e => setForm(prev => ({
                                            ...prev,
                                            recurring: e.target.checked,
                                            recurWeeks: null,
                                            expiresOn: null,
                                            bookerCanSetRecurUntil: false,
                                        }))}
                                        color="indigo" size="sm"
                                    />
                                </SectionRow>
                                {form.recurring && <>
                                    <div className="p-4 space-y-3">
                                        <Select
                                            label="Series end"
                                            size="sm"
                                            data={[
                                                { value: 'none', label: 'Indefinite (no end)' },
                                                { value: 'date', label: 'Fixed end date' },
                                                { value: 'weeks', label: 'After N weeks' },
                                            ]}
                                            value={form.expiresOn !== null ? 'date' : form.recurWeeks !== null ? 'weeks' : 'none'}
                                            onChange={val => {
                                                if (val === 'none') setForm(prev => ({ ...prev, expiresOn: null, recurWeeks: null }))
                                                else if (val === 'date') setForm(prev => ({ ...prev, expiresOn: new Date(Date.now() + 365 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10), recurWeeks: null, bookerCanSetRecurUntil: false }))
                                                else setForm(prev => ({ ...prev, recurWeeks: 12, expiresOn: null }))
                                            }}
                                        />
                                        {form.expiresOn !== null && (
                                            <input
                                                type="date"
                                                value={form.expiresOn}
                                                min={new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString().slice(0, 10)}
                                                onChange={e => setField('expiresOn', e.target.value || null)}
                                                className="border border-gray-300 rounded-lg px-3 py-2 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-400"
                                            />
                                        )}
                                        {form.recurWeeks !== null && (
                                            <NumberInput
                                                label="Duration"
                                                rightSection={<span className="text-xs text-gray-400 pr-2">weeks</span>}
                                                size="sm"
                                                value={form.recurWeeks ?? ''}
                                                onChange={val => setField('recurWeeks', val === '' ? null : Number(val))}
                                                onBlur={() => setTouched(prev => ({ ...prev, recurWeeks: true }))}
                                                error={touched.recurWeeks ? errors.recurWeeks : undefined}
                                                min={2} className="w-44"
                                            />
                                        )}
                                    </div>
                                    <SectionRow label="Booker sets end date" description="Show an end date picker on the booking page">
                                        <Switch
                                            checked={form.bookerCanSetRecurUntil}
                                            disabled={form.expiresOn !== null}
                                            onChange={e => setField('bookerCanSetRecurUntil', e.target.checked)}
                                            color="indigo" size="sm"
                                        />
                                    </SectionRow>
                                </>}
                            </Group>
                        )}

                        {/* HOSTS */}
                        {activeTab === 'hosts' && (
                            <Group title="Tutors & schedules">
                                {form.tutorRows.length === 0 ? (
                                    <div className="flex flex-col items-center justify-center py-10 text-center">
                                        <p className="text-sm font-medium text-gray-500">No hosts yet</p>
                                        <p className="text-xs text-gray-400 mt-0.5">Add a tutor to enable bookings for this link</p>
                                    </div>
                                ) : (
                                    form.tutorRows.map((row, i) => (
                                        <div key={i} className="flex items-end gap-3 p-4">
                                            <Select
                                                label={i === 0 ? 'Tutor' : undefined}
                                                placeholder="Select tutor"
                                                size="sm"
                                                data={tutors.filter(t => !form.tutorRows.some((r, idx) => idx !== i && r.tutorId === String(t.id))).map(t => ({ value: String(t.id), label: `${t.first_name} ${t.last_name}` }))}
                                                value={row.tutorId}
                                                onChange={val => val && handleTutorRowChange(i, 'tutorId', val)}
                                                className="flex-1"
                                            />
                                            <Select
                                                label={i === 0 ? 'Schedule' : undefined}
                                                placeholder="Schedule"
                                                size="sm"
                                                disabled={!row.tutorId}
                                                data={schedules.filter(s => s.tutor_id === Number(row.tutorId)).map(s => ({ value: String(s.id), label: s.name }))}
                                                value={row.scheduleId}
                                                onChange={val => val && handleTutorRowChange(i, 'scheduleId', val)}
                                                className="flex-1"
                                            />
                                            <button
                                                onClick={() => {
                                                    const updated = { ...form, tutorRows: form.tutorRows.filter((_, idx) => idx !== i) }
                                                    setForm(updated)
                                                    if (touched.tutorRows) setErrors(validate(updated))
                                                }}
                                                className="flex items-center justify-center w-7 h-7 mb-0.5 rounded-lg text-gray-400 hover:text-red-500 hover:bg-red-50 transition-colors"
                                            >
                                                <IconTrash size={14} />
                                            </button>
                                        </div>
                                    ))
                                )}
                                {touched.tutorRows && errors.tutorRows && (
                                    <p className="text-xs text-red-500 px-4 pb-3">{errors.tutorRows}</p>
                                )}
                                <div className="px-4 py-3 border-t border-gray-100">
                                    <button
                                        onClick={() => setForm(prev => ({ ...prev, tutorRows: [...prev.tutorRows, { tutorId: null, scheduleId: null }] }))}
                                        className="flex items-center gap-1.5 text-sm font-medium text-indigo-600 hover:text-indigo-800 transition-colors"
                                    >
                                        <IconPlus size={14} />
                                        Add host
                                    </button>
                                </div>
                            </Group>
                        )}

                        {/* CANCELLATION */}
                        {activeTab === 'cancellation' && (
                            <Group title="Cancellation & rescheduling">
                                <div className="p-4 space-y-3">
                                    <Select
                                        label="Cancellation"
                                        size="sm"
                                        data={CANCEL_MODE_OPTIONS}
                                        value={form.cancelMode ?? 'auto'}
                                        onChange={val => setForm(prev => ({ ...prev, cancelMode: val, cancelNoticeMinutes: null }))}
                                    />
                                    {form.cancelMode && WINDOW_MODES.includes(form.cancelMode) && (
                                        <div className="flex gap-2 items-end">
                                            <NumberInput
                                                label="Notice required"
                                                placeholder="e.g. 24"
                                                size="sm"
                                                value={form.cancelNoticeMinutes ? Math.round(form.cancelNoticeMinutes / unitToMinutes(cancelUnit)) : ''}
                                                onChange={val => {
                                                    const updated = { ...form, cancelNoticeMinutes: val === '' ? null : Number(val) * unitToMinutes(cancelUnit) }
                                                    setForm(updated)
                                                    if (touched.cancelNoticeMinutes) setErrors(validate(updated))
                                                }}
                                                onBlur={() => setTouched(prev => ({ ...prev, cancelNoticeMinutes: true }))}
                                                error={touched.cancelNoticeMinutes ? errors.cancelNoticeMinutes : undefined}
                                                min={1} className="w-28"
                                            />
                                            <Select data={NOTICE_UNITS} value={cancelUnit} size="sm" onChange={val => val && setCancelUnit(val as NoticeUnit)} className="w-24" />
                                        </div>
                                    )}
                                </div>
                                <div className="p-4 space-y-3">
                                    <Select
                                        label="Rescheduling"
                                        size="sm"
                                        data={CANCEL_MODE_OPTIONS}
                                        value={form.rescheduleMode ?? 'auto'}
                                        onChange={val => setForm(prev => ({ ...prev, rescheduleMode: val, rescheduleNoticeMinutes: null }))}
                                    />
                                    {form.rescheduleMode && WINDOW_MODES.includes(form.rescheduleMode) && (
                                        <div className="flex gap-2 items-end">
                                            <NumberInput
                                                label="Notice required"
                                                placeholder="e.g. 24"
                                                size="sm"
                                                value={form.rescheduleNoticeMinutes ? Math.round(form.rescheduleNoticeMinutes / unitToMinutes(rescheduleUnit)) : ''}
                                                onChange={val => {
                                                    const updated = { ...form, rescheduleNoticeMinutes: val === '' ? null : Number(val) * unitToMinutes(rescheduleUnit) }
                                                    setForm(updated)
                                                    if (touched.rescheduleNoticeMinutes) setErrors(validate(updated))
                                                }}
                                                onBlur={() => setTouched(prev => ({ ...prev, rescheduleNoticeMinutes: true }))}
                                                error={touched.rescheduleNoticeMinutes ? errors.rescheduleNoticeMinutes : undefined}
                                                min={1} className="w-28"
                                            />
                                            <Select data={NOTICE_UNITS} value={rescheduleUnit} size="sm" onChange={val => val && setRescheduleUnit(val as NoticeUnit)} className="w-24" />
                                        </div>
                                    )}
                                </div>
                            </Group>
                        )}

                        {/* LIMITS */}
                        {activeTab === 'limits' && (
                            <Group title="Booking limits">
                                <div className="p-4 space-y-4">
                                    <div className="grid grid-cols-2 gap-3">
                                        <NumberInput label="Per day" placeholder="No limit" size="sm" value={form.limitPerDay ?? ''} onChange={val => handleNumericField('limitPerDay', val)} min={1} />
                                        <NumberInput label="Per week" placeholder="No limit" size="sm" value={form.limitPerWeek ?? ''} onChange={val => handleNumericField('limitPerWeek', val)} min={1} />
                                        <NumberInput label="Per month" placeholder="No limit" size="sm" value={form.limitPerMonth ?? ''} onChange={val => handleNumericField('limitPerMonth', val)} min={1} />
                                        <NumberInput label="Per booker" placeholder="No limit" size="sm" value={form.limitPerBooker ?? ''} onChange={val => handleNumericField('limitPerBooker', val)} min={1} />
                                    </div>
                                    <div className="grid grid-cols-2 gap-3">
                                        <NumberInput label="Buffer (min)" description="Gap after each booking" placeholder="None" size="sm" value={form.bufferMinutes ?? ''} onChange={val => handleNumericField('bufferMinutes', val)} min={0} />
                                        <NumberInput label="Slot interval (min)" description="Step between start times" placeholder="Same as duration" size="sm" value={form.intervalMinutes ?? ''} onChange={val => handleNumericField('intervalMinutes', val)} min={1} />
                                    </div>
                                    <NumberInput
                                        label="Future booking window (days)"
                                        description="How far in advance this can be booked"
                                        placeholder="No limit"
                                        size="sm"
                                        value={form.limitFutureBookingsDays ?? ''}
                                        onChange={val => handleNumericField('limitFutureBookingsDays', val)}
                                        min={1} className="w-60"
                                    />
                                </div>
                                <SectionRow label="First slot only" description="Only show the earliest available slot per day">
                                    <Switch checked={form.onlyShowFirstSlot} onChange={e => setField('onlyShowFirstSlot', e.target.checked)} color="indigo" size="sm" />
                                </SectionRow>
                            </Group>
                        )}

                        {/* BOOKING */}
                        {activeTab === 'booking' && <>
                            <Group title="Pricing">
                                <div className="p-4">
                                    <NumberInput
                                        label="Price ($)"
                                        description="Displayed on the booking page"
                                        placeholder="Free / contact for pricing"
                                        size="sm"
                                        value={form.price ?? ''}
                                        onChange={val => handleNumericField('price', val)}
                                        min={0} decimalScale={2} className="w-44"
                                    />
                                </div>
                            </Group>

                            <Group title="Custom fields">
                                <div className="px-4 py-8 flex flex-col items-center text-center">
                                    <p className="text-sm font-medium text-gray-500">Custom form fields</p>
                                    <p className="text-xs text-gray-400 mt-1 max-w-xs">Add extra questions to the booking form — text, phone, notes, and more.</p>
                                    <span className="mt-3 text-xs text-gray-300 font-medium uppercase tracking-wider">Coming soon</span>
                                </div>
                            </Group>
                        </>}

                    </div>
                </div>
            </div>
            <Modal
                opened={confirmingLeave}
                onClose={() => setConfirmingLeave(false)}
                title="Discard unsaved changes?"
                centered
                size="sm"
            >
                <p className="text-sm text-gray-600 mb-4">
                    {isNew
                        ? "This link hasn't been created yet — leaving now discards it."
                        : 'Your edits to this link will be lost.'}
                </p>
                <div className="flex justify-end gap-2">
                    <Button variant="default" onClick={() => setConfirmingLeave(false)}>Keep editing</Button>
                    <Button color="red" onClick={() => navigate('/links')}>Discard</Button>
                </div>
            </Modal>
            <Toast toast={toast} />
        </div>
    )
}

export default LinkPage
