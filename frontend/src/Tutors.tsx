import { useState, useEffect } from 'react'
import { Switch } from '@mantine/core'
import type { Tutor } from './types'
import { extractError, tutorBubbleClass, tutorInitials } from './utils'
import Toast from './Toast'
import { useToast } from './useToast'

const API = import.meta.env.VITE_API_URL

const StatusBadge = ({ tutor }: { tutor: Tutor }) => {
    if (!tutor.calendar_id)
        return <span className="text-xs text-gray-400">No calendar connected</span>
    if (tutor.check_calendar_conflicts)
        return <span className="text-xs text-emerald-600 font-medium">✓ Conflicts checked</span>
    return <span className="text-xs text-gray-400">Calendar set — conflicts off</span>
}

interface TutorRowState {
    calendar_id: string
    check_calendar_conflicts: boolean
}

const Tutors = () => {
    const [tutors, setTutors] = useState<Tutor[]>([])
    const [rows, setRows] = useState<Record<number, TutorRowState>>({})
    const [loading, setLoading] = useState(true)
    const [loadError, setLoadError] = useState<string | null>(null)
    const [savingId, setSavingId] = useState<number | null>(null)
    const { toast, showToast } = useToast()

    const loadData = async () => {
        setLoading(true)
        try {
            const res = await fetch(`${API}/tutors/`)
            if (!res.ok) {
                const err = await res.json()
                setLoadError(extractError(err, 'Failed to load tutors'))
                return
            }
            const data: Tutor[] = await res.json()
            setTutors(data)
            const initial: Record<number, TutorRowState> = {}
            for (const t of data) {
                initial[t.id] = {
                    calendar_id: t.calendar_id ?? '',
                    check_calendar_conflicts: t.check_calendar_conflicts,
                }
            }
            setRows(initial)
        } catch (err) {
            console.error('Failed to load tutors:', err)
            setLoadError('An unknown error occurred while loading tutors, please try again')
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => { loadData() }, [])

    const update = (id: number, patch: Partial<TutorRowState>) =>
        setRows(prev => ({ ...prev, [id]: { ...prev[id], ...patch } }))

    const buildPayload = (tutor: Tutor, row: TutorRowState) => {
        const calendar_id = row.calendar_id.trim() || null
        return {
            pay_rate: tutor.pay_rate,
            is_active: tutor.is_active,
            calendar_id,
            check_calendar_conflicts: calendar_id ? row.check_calendar_conflicts : false,
        }
    }

    const save = async (tutor: Tutor) => {
        const row = rows[tutor.id]
        setSavingId(tutor.id)

        try {
            const res = await fetch(`${API}/tutors/${tutor.id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(buildPayload(tutor, row)),
            })
            if (!res.ok) {
                const err = await res.json()
                showToast(extractError(err, 'Failed to save'))
                return
            }
            const updated: Tutor = await res.json()
            setTutors(prev => prev.map(t => t.id === updated.id ? updated : t))
            update(tutor.id, {
                calendar_id: updated.calendar_id ?? '',
                check_calendar_conflicts: updated.check_calendar_conflicts,
            })
            showToast(`${tutor.first_name} ${tutor.last_name} saved`)
        } catch (err) {
            console.error('Failed to save tutor:', err)
            showToast(extractError(err, 'Network error'))
        } finally {
            setSavingId(null)
        }
    }

    if (loading) return (
        <div className="flex items-center justify-center h-48">
            <div className="w-6 h-6 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
        </div>
    )

    return (
        <div className="max-w-2xl">
            <div className="mb-6">
                <h1 className="text-xl font-bold text-gray-900">Tutors</h1>
                <p className="text-sm text-gray-500 mt-1">
                    Manage calendar integration per tutor. The tutor must share their Google Calendar with{' '}
                    <span className="font-mono text-xs bg-gray-100 px-1 py-0.5 rounded">tms-calendar@scheduling-tms.iam.gserviceaccount.com</span>{' '}
                    before enabling conflict checking.
                </p>
            </div>

            {loadError && <p className="text-sm text-red-500 mb-4">{loadError}</p>}

            <div className="flex flex-col gap-3">
                {tutors.map(tutor => {
                    const row = rows[tutor.id]
                    if (!row) return null
                    const isSaving = savingId === tutor.id
                    const hasCalendar = row.calendar_id.trim().length > 0
                    return (
                        <div key={tutor.id} className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
                            <div className="flex items-center gap-3 mb-4">
                                <div className={`w-9 h-9 rounded-full flex items-center justify-center text-sm font-bold shrink-0 ${tutorBubbleClass(tutor)}`}>
                                    {tutorInitials(tutor)}
                                </div>
                                <div>
                                    <p className="text-sm font-semibold text-gray-900">{tutor.first_name} {tutor.last_name}</p>
                                    <StatusBadge tutor={{ ...tutor, calendar_id: row.calendar_id || null, check_calendar_conflicts: row.check_calendar_conflicts }} />
                                </div>
                            </div>

                            <div className="flex flex-col gap-3">
                                <div>
                                    <label className="text-xs font-medium text-gray-500 mb-1 block">Google Calendar ID</label>
                                    <input
                                        type="text"
                                        value={row.calendar_id}
                                        onChange={e => update(tutor.id, { calendar_id: e.target.value })}
                                        placeholder="e.g. tutor@gmail.com"
                                        className="w-full text-sm border border-gray-200 rounded-lg px-3 py-2 focus:outline-none focus:border-indigo-400 focus:ring-1 focus:ring-indigo-400"
                                    />
                                </div>

                                <div className="flex items-center justify-between">
                                    <Switch
                                        label="Check calendar conflicts"
                                        size="sm"
                                        checked={row.check_calendar_conflicts}
                                        disabled={!hasCalendar}
                                        onChange={e => update(tutor.id, { check_calendar_conflicts: e.currentTarget.checked })}
                                    />
                                    <button
                                        onClick={() => save(tutor)}
                                        disabled={isSaving}
                                        className="text-sm px-4 py-1.5 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50 transition-colors"
                                    >
                                        Save
                                    </button>
                                </div>

                            </div>
                        </div>
                    )
                })}
            </div>

            <Toast toast={toast} />
        </div>
    )
}

export default Tutors