import { useState, useEffect } from 'react'
import { Button, Loader } from '@mantine/core'
import { IconPlus, IconPencil, IconTrash } from '@tabler/icons-react'
import type { Tutor, Schedule } from './types'
import ScheduleForm from './ScheduleForm'
import { useToast } from './useToast'
import { extractError } from './utils'
import Toast from './Toast'

const DAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

const Availability = () => {
  const [tutors, setTutors] = useState<Tutor[]>([])
  const [schedules, setSchedules] = useState<Schedule[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [newScheduleOpen, setNewScheduleOpen] = useState(false)
  const [editingSchedule, setEditingSchedule] = useState<Schedule | null>(null)
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<number | null>(null)
  const { toast, showToast } = useToast()

  const handleDelete = async (id: number) => {
    try {
      const res = await fetch(`${import.meta.env.VITE_API_URL}/schedules/${id}`, { method: 'DELETE' })
      if (!res.ok) {
        showToast(extractError(await res.json(), 'Failed to delete schedule'), 'error')
        setConfirmingDeleteId(null)
        return
      }
      setConfirmingDeleteId(null)
      loadData()
      showToast('Schedule deleted')
    } catch {
      showToast('An unknown error occurred while deleting, please try again', 'error')
      setConfirmingDeleteId(null)
    }
  }

  const loadData = async () => {
    setLoading(true)
    try {
      const [tutorsRes, schedulesRes] = await Promise.all([
        fetch(`${import.meta.env.VITE_API_URL}/tutors`),
        fetch(`${import.meta.env.VITE_API_URL}/schedules`),
      ])
      if (!tutorsRes.ok && !schedulesRes.ok) {
        setLoadError('Failed to load tutors and schedules')
        return
      } else if (!tutorsRes.ok) {
        setLoadError('Failed to load tutors')
        return
      } else if (!schedulesRes.ok) {
        setLoadError('Failed to load schedules')
        return
      }
      setTutors(await tutorsRes.json())
      setSchedules(await schedulesRes.json())
    } catch (err) {
      console.error('Failed to load data:', err)
      setLoadError('An unknown error occurred while loading data, please try again')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadData() }, [])

  return (
    <div>
      {loading && <div className="flex justify-center py-12"><Loader size="sm" /></div>}
      {loadError && <p className="text-sm text-red-500 mb-4">{loadError}</p>}

      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-semibold text-gray-800">Availability</h1>
        {!newScheduleOpen && !editingSchedule && (
          <Button leftSection={<IconPlus size={16} />} size="sm" onClick={() => setNewScheduleOpen(true)}>
            New Schedule
          </Button>
        )}
      </div>

      {newScheduleOpen && (
        <div className="mb-4">
          <ScheduleForm
            tutors={tutors}
            editingSchedule={null}
            onClose={() => setNewScheduleOpen(false)}
            onSuccess={() => { setNewScheduleOpen(false); loadData(); showToast('Schedule created') }}
          />
        </div>
      )}

      <div className="flex flex-col gap-4">
        {schedules.map(s => editingSchedule?.id === s.id ? (
          <ScheduleForm
            key={s.id}
            tutors={tutors}
            editingSchedule={s}
            onClose={() => setEditingSchedule(null)}
            onSuccess={() => { setEditingSchedule(null); loadData(); showToast('Schedule saved') }}
          />
        ) : (
          <div key={s.id} className="bg-white border border-gray-200 rounded-xl p-5">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <span className="font-medium text-gray-800">{s.name}</span>
                <span className="text-xs bg-indigo-50 text-indigo-600 px-2 py-0.5 rounded-full font-medium">
                  {tutors.find(t => t.id === s.tutor_id)?.first_name} {tutors.find(t => t.id === s.tutor_id)?.last_name}
                </span>
                {s.is_default && <span className="text-xs bg-indigo-50 text-indigo-600 px-2 py-0.5 rounded-full">Default</span>}
              </div>
              <div className="flex items-center gap-2">
                {confirmingDeleteId === s.id ? (
                  <div className="flex items-center gap-2">
                    <span className="text-sm text-gray-500">Delete?</span>
                    <Button variant="default" size="xs" onClick={() => setConfirmingDeleteId(null)}>Cancel</Button>
                    <Button color="red" size="xs" onClick={() => handleDelete(s.id)}>Delete</Button>
                  </div>
                ) : (
                  <>
                    <button
                      className="flex items-center justify-center w-7 h-7 rounded-md text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors"
                      onClick={() => { setNewScheduleOpen(false); setEditingSchedule(s) }}
                    >
                      <IconPencil size={16} />
                    </button>
                    <button
                      className="flex items-center justify-center w-7 h-7 rounded-md text-gray-400 hover:text-red-500 hover:bg-red-50 transition-colors"
                      onClick={() => setConfirmingDeleteId(s.id)}
                    >
                      <IconTrash size={16} />
                    </button>
                  </>
                )}
              </div>
            </div>
            <div className="flex flex-col gap-1">
              {DAY_LABELS.map((label, dow) => {
                const periods = s.days.filter(d => d.day_of_week === dow)
                if (periods.length === 0) return null
                return (
                  <div key={dow} className="flex gap-3 text-sm text-gray-500">
                    <span className="w-8 font-medium text-gray-600">{label}</span>
                    <div className="flex flex-col gap-0.5">
                      {periods.map(p => (
                        <span key={p.id}>{p.start_time.slice(0, 5)} – {p.end_time.slice(0, 5)}</span>
                      ))}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        ))}
      </div>

      <Toast toast={toast} />
    </div>
  )
}

export default Availability
