import { useState, useEffect } from 'react'
import { Button, Loader } from '@mantine/core'
import { IconPlus, IconPencil, IconTrash, IconExternalLink } from '@tabler/icons-react'
import { useNavigate } from 'react-router-dom'
import type { Tutor, Schedule, EventType } from './types'
import { useToast } from './useToast'
import { extractError } from './utils'
import Toast from './Toast'

/* TODO (backend) — wire EventType limit fields to available-slots logic (see CLAUDE.md Known TODOs):
  - limit_future_bookings_days: cap time_max before slot generation
  - only_show_first_slot: keep earliest slot per (tutor_id, date) after generation
  - buffer_minutes: expand busy overlap check to [slot_start - buffer, slot_end + buffer]
  - limit_per_day / per_week / per_month: count existing bookings per tutor per period
*/

const modeLabel = (mode: string | null) => {
    switch (mode) {
        case 'not_allowed': return 'Not allowed'
        case 'request': return 'Request only'
        case 'auto_window_block': return 'Window — allow or block'
        case 'auto_window_request': return 'Window — allow or request'
        case 'request_window': return 'Window — request or block'
        default: return 'Always allowed'
    }
}

interface LoadErrors {
    eventTypes?: string
    tutors?: string
    schedules?: string
    unknown?: string
}

const EventTypes = () => {
    const navigate = useNavigate()

    const [eventTypes, setEventTypes] = useState<EventType[]>([])
    const [tutors, setTutors] = useState<Tutor[]>([])
    const [schedules, setSchedules] = useState<Schedule[]>([])
    const [loadErrors, setLoadErrors] = useState<LoadErrors>({})
    const [loading, setLoading] = useState(false)

    const [confirmingDeleteId, setConfirmingDeleteId] = useState<number | null>(null)
    const { toast, showToast } = useToast()

    const loadData = async () => {
        setLoading(true)
        try {
            const [eventTypesRes, tutorsRes, schedulesRes] = await Promise.all([
                fetch(`${import.meta.env.VITE_API_URL}/event_types`),
                fetch(`${import.meta.env.VITE_API_URL}/tutors`),
                fetch(`${import.meta.env.VITE_API_URL}/schedules`)
            ])
            if (!eventTypesRes.ok) {
                const err = await eventTypesRes.json()
                setLoadErrors(prev => ({ ...prev, eventTypes: extractError(err, 'Failed to load event types') }))
                return
            }
            if (!tutorsRes.ok) {
                const err = await tutorsRes.json()
                setLoadErrors(prev => ({ ...prev, tutors: extractError(err, 'Failed to load tutors') }))
                return
            }
            if (!schedulesRes.ok) {
                const err = await schedulesRes.json()
                setLoadErrors(prev => ({ ...prev, schedules: extractError(err, 'Failed to load schedules') }))
                return
            }
            setEventTypes(await eventTypesRes.json())
            setTutors(await tutorsRes.json())
            setSchedules(await schedulesRes.json())
        } catch (error) {
            console.error('Error loading data:', error)
            setLoadErrors(prev => ({ ...prev, unknown: 'An unknown error occurred while loading data' }))
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => { loadData() }, [])

    const handleDelete = async (id: number) => {
        try {
            const res = await fetch(`${import.meta.env.VITE_API_URL}/event_types/${id}`, { method: 'DELETE' })
            if (!res.ok) {
                showToast(extractError(await res.json(), 'Failed to delete event type'), 'error')
                setConfirmingDeleteId(null)
                return
            }
            setConfirmingDeleteId(null)
            loadData()
            showToast('Event type deleted')
        } catch (error) {
            console.error(error)
            showToast('An unknown error occurred while deleting, please try again', 'error')
            setConfirmingDeleteId(null)
        }
    }

    return (
        <div>
            {loading && <div className="flex justify-center py-12"><Loader size="sm" /></div>}

            {/* load errors */}
            {loadErrors.eventTypes && <p className="text-sm text-red-500 mb-2">{loadErrors.eventTypes}</p>}
            {loadErrors.tutors && <p className="text-sm text-red-500 mb-2">{loadErrors.tutors}</p>}
            {loadErrors.schedules && <p className="text-sm text-red-500 mb-2">{loadErrors.schedules}</p>}
            {loadErrors.unknown && <p className="text-sm text-red-500 mb-4">{loadErrors.unknown}</p>}

            {/* header */}
            <div className="flex items-center justify-between mb-6">
                <h1 className="text-xl font-semibold text-gray-800">Event Types</h1>
                <Button leftSection={<IconPlus size={16} />} size="sm" onClick={() => navigate('/event-types/new')}>
                    New Event Type
                </Button>
            </div>

            {/* cards list */}
            <div className="flex flex-col gap-4">
                {eventTypes.map(e => (
                    <div key={e.id} className="bg-white border border-gray-200 rounded-xl p-5">

                        {/* card header: name, duration badge, recurring badge + edit/delete */}
                        <div className="flex items-center justify-between mb-3">
                            <div className="flex items-center gap-2">
                                <span className="font-medium text-gray-800">{e.name}</span>
                                <span className="text-xs bg-indigo-50 text-indigo-600 px-2 py-0.5 rounded-full font-medium">
                                    {e.min_duration_minutes !== null ? `${e.min_duration_minutes}–${e.max_duration_minutes} min` : `${e.duration_minutes} min`}
                                </span>
                                {e.recurring && (
                                    <span className="text-xs bg-gray-100 text-gray-500 px-2 py-0.5 rounded-full">Recurring</span>
                                )}
                            </div>

                            {/* edit / delete buttons */}
                            <div className="flex items-center gap-2">
                                {confirmingDeleteId === e.id ? (
                                    <div className="flex items-center gap-2">
                                        <span className="text-sm text-gray-500">Delete?</span>
                                        <Button variant="default" size="xs" onClick={() => setConfirmingDeleteId(null)}>Cancel</Button>
                                        <Button color="red" size="xs" onClick={() => handleDelete(e.id)}>Delete</Button>
                                    </div>
                                ) : (
                                    <>
                                        <Button
                                            component="a"
                                            href={`/book/${e.id}`}
                                            target="_blank"
                                            variant="light"
                                            color="indigo"
                                            size="xs"
                                            leftSection={<IconExternalLink size={13} />}
                                        >
                                            Book
                                        </Button>
                                        <button
                                            className="flex items-center justify-center w-7 h-7 rounded-md text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors"
                                            onClick={() => navigate(`/event-types/${e.id}`)}
                                        >
                                            <IconPencil size={16} />
                                        </button>
                                        <button
                                            className="flex items-center justify-center w-7 h-7 rounded-md text-gray-400 hover:text-red-500 hover:bg-red-50 transition-colors"
                                            onClick={() => setConfirmingDeleteId(e.id)}
                                        >
                                            <IconTrash size={16} />
                                        </button>
                                    </>
                                )}
                            </div>
                        </div>

                        {/* description */}
                        {e.description && (
                            <p className="text-sm text-gray-500 mb-3 truncate">{e.description}</p>
                        )}

                        {/* tutor rows */}
                        {e.availability.length > 0 && (
                            <div className="flex flex-col gap-1 border-t border-gray-100 pt-3">
                                {e.availability.map(a => {
                                    const tutor = tutors.find(t => t.id === a.tutor_id)
                                    const schedule = schedules.find(s => s.id === a.schedule_id)
                                    return (
                                        <div key={a.id} className="flex items-center gap-2">
                                            <span className="text-xs bg-indigo-50 text-indigo-700 px-2 py-0.5 rounded-full font-medium">{tutor?.first_name} {tutor?.last_name}</span>
                                            <span className="text-gray-300 text-xs">—</span>
                                            <span className="text-xs text-gray-500">{schedule?.name}</span>
                                        </div>
                                    )
                                })}
                            </div>
                        )}

                        {/* policy line */}
                        {(e.cancel_mode || e.reschedule_mode) && (
                            <div className="flex gap-5 border-t border-gray-100 pt-3 mt-1">
                                <span className="text-xs text-gray-400">Cancel <span className="text-gray-600 font-medium">{modeLabel(e.cancel_mode)}</span></span>
                                <span className="text-xs text-gray-400">Reschedule <span className="text-gray-600 font-medium">{modeLabel(e.reschedule_mode)}</span></span>
                            </div>
                        )}
                    </div>
                ))}
            </div>

            <Toast toast={toast} />
        </div>
    )
}
export default EventTypes
