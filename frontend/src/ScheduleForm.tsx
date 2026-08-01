import { useState } from 'react'
import { Select, Button, TextInput, Switch } from '@mantine/core'
import { IconTrash, IconX } from '@tabler/icons-react'
import type { Tutor, Schedule } from './types'
import { extractError } from './utils'

const TIME_OPTIONS = [
  ...Array.from({ length: 96 }, (_, i) => {
    const h = Math.floor(i / 4).toString().padStart(2, '0')
    const m = ((i % 4) * 15).toString().padStart(2, '0')
    return `${h}:${m}`
  }),
  '23:59',
]

const DAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

const DEFAULT_DAYS = DAY_LABELS.map((label, i) => ({
  label,
  enabled: i < 5,
  periods: [{ from: '10:30', to: '22:00' }],
}))

interface FormErrors { tutor?: string; name?: string }
interface FormTouched { tutor?: boolean; name?: boolean }

const validate = (tutorId: string | null, name: string): FormErrors => {
  const e: FormErrors = {}
  if (!tutorId) e.tutor = 'Required'
  if (!name.trim()) e.name = 'Required'
  return e
}

interface Props {
  tutors: Tutor[]
  editingSchedule: Schedule | null
  onClose: () => void
  onSuccess: () => void
}

const buildDays = (s: Schedule) =>
  DAY_LABELS.map((label, i) => {
    const dayPeriods = s.days
      .filter(d => d.day_of_week === i)
      .map(d => ({ from: d.start_time.slice(0, 5), to: d.end_time.slice(0, 5) }))
    return {
      label,
      enabled: dayPeriods.length > 0,
      periods: dayPeriods.length > 0 ? dayPeriods : [{ from: '10:30', to: '22:00' }],
    }
  })

const ScheduleForm = ({ tutors, editingSchedule, onClose, onSuccess }: Props) => {
  const [selectedTutorId, setSelectedTutorId] = useState<string | null>(
    editingSchedule ? String(editingSchedule.tutor_id) : null
  )
  const [scheduleName, setScheduleName] = useState(editingSchedule?.name ?? '')
  const [days, setDays] = useState(editingSchedule ? buildDays(editingSchedule) : DEFAULT_DAYS)
  const [defaultSchedule, setDefaultSchedule] = useState(editingSchedule?.is_default ?? false)
  const [timezone, setTimezone] = useState(editingSchedule?.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone)
  const [errors, setErrors] = useState<FormErrors>({})
  const [touched, setTouched] = useState<FormTouched>({})
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [confirmingDiscard, setConfirmingDiscard] = useState(false)

  // to compare current form state with initial values for dirty check
  const initial = {
    tutorId: editingSchedule ? String(editingSchedule.tutor_id) : null,
    name: editingSchedule?.name ?? '',
    isDefault: editingSchedule?.is_default ?? false,
    timezone: editingSchedule?.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone,
    days: editingSchedule ? buildDays(editingSchedule) : DEFAULT_DAYS,
  }
  const isDirty = selectedTutorId !== initial.tutorId
    || scheduleName !== initial.name
    || defaultSchedule !== initial.isDefault
    || timezone !== initial.timezone
    || JSON.stringify(days) !== JSON.stringify(initial.days)

  const allValid = Object.keys(validate(selectedTutorId, scheduleName)).length === 0


  const reset = () => {
    setSelectedTutorId(null)
    setScheduleName('')
    setDays(DEFAULT_DAYS)
    setDefaultSchedule(false)
    setTimezone(Intl.DateTimeFormat().resolvedOptions().timeZone)
    setErrors({})
    setTouched({})
    setSubmitError(null)
    setConfirmingDiscard(false)
  }

  const handleClose = () => {
    if (isDirty) {
      setConfirmingDiscard(true)
    } else {
      reset()
      onClose()
    }
  }

  const buildPayload = () => ({
    tutor_id: Number(selectedTutorId),
    name: scheduleName,
    is_default: defaultSchedule,
    timezone: timezone,
    days: days.flatMap((d, i) =>
      d.enabled ? d.periods.map(p => ({
        day_of_week: i,
        start_time: p.from + ':00',
        end_time: p.to + ':00',
      })) : []
    ),
  })

  const handleCreate = async () => {
    const errs = validate(selectedTutorId, scheduleName)
    if (Object.keys(errs).length > 0) { setErrors(errs); setTouched({ tutor: true, name: true }); return }
    try {
      const res = await fetch(`${import.meta.env.VITE_API_URL}/schedules`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildPayload()),
      })
      if (!res.ok) {
        const err = await res.json();
        setSubmitError(extractError(err, 'Failed to create schedule'));
        return
      }
      reset()
      onSuccess()
    } catch (err) {
      console.error(err)
      setSubmitError('An unknown error occurred, please try again')
    }
  }

  const handleSave = async () => {
    const errs = validate(selectedTutorId, scheduleName)
    if (Object.keys(errs).length > 0) { setErrors(errs); setTouched({ tutor: true, name: true }); return }
    try {
      const res = await fetch(`${import.meta.env.VITE_API_URL}/schedules/${editingSchedule!.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildPayload()),
      })
      if (!res.ok) { 
        const err = await res.json(); 
        setSubmitError(extractError(err, 'Failed to save schedule'));
        return 
      }
      reset()
      onSuccess()
    } 
    catch (err) {
      console.error(err)
      setSubmitError('An unknown error occurred, please try again')
    }
  }

  const handleAddPeriod = (dayIndex: number) => {
    setDays(prev => {
      const days = [...prev]
      const day = days[dayIndex]
      const LAST_TIME = TIME_OPTIONS[TIME_OPTIONS.length - 1]
      const lastTo = day.periods[day.periods.length - 1].to
      const firstFrom = day.periods[0].from
      let periods: typeof day['periods']
      if (lastTo === LAST_TIME) {
        // no room at the end — prepend before the first period instead
        const firstFromIndex = TIME_OPTIONS.indexOf(firstFrom)
        const newFrom = TIME_OPTIONS[0]
        const newTo = TIME_OPTIONS[firstFromIndex - 1] // end 15 min before first period starts
        periods = [{ from: newFrom, to: newTo }, ...day.periods]
      } else {
        const lastToIndex = TIME_OPTIONS.indexOf(lastTo)
        const nextFrom = TIME_OPTIONS[lastToIndex + 1]
        const newTo = TIME_OPTIONS[lastToIndex + 4] ?? LAST_TIME
        periods = [...day.periods, { from: nextFrom, to: newTo }]
      }
      days[dayIndex] = { ...day, periods }
      return days
    })
  }

  const handleRemovePeriod = (dayIndex: number, periodIndex: number) => {
    setDays(prev => {
      const days = [...prev]
      const periods = days[dayIndex].periods.filter((_, i) => i !== periodIndex)
      days[dayIndex] = { ...days[dayIndex], periods }
      return days
    })
  }

  const handleTimeChange = (dayIndex: number, periodIndex: number, field: 'from' | 'to') => (value: string | null) => {
    if (!value) return
    setDays(prev => {
      const days = [...prev]
      const periods = [...days[dayIndex].periods]
      periods[periodIndex] = { ...periods[periodIndex], [field]: value }
      
      // when 'to' changes, cascade forward: push each subsequent period's 'from' past the previous period's 'to' if needed
      // then if that period's 'from' >= its own 'to', push its 'to' forward too and continue
      if (field === 'to') {
        let i = periodIndex + 1
        while (i < periods.length) {
          const { from, to } = periods[i]
          const prevTo = periods[i - 1].to
          if (from <= prevTo) { // overlap: this period's from is at or before the previous period's to
            const newFrom = TIME_OPTIONS[TIME_OPTIONS.indexOf(prevTo) + 1] // push from 15 min past previous to
            if (!newFrom) { periods.splice(i) ; break } // no room — delete this and all subsequent periods
            periods[i] = { ...periods[i], from: newFrom }
            if (newFrom >= to) { // time-from passed the period's own time-to — push to forward by 15 min
              const newTo = TIME_OPTIONS[TIME_OPTIONS.indexOf(newFrom) + 1]
              if (!newTo) { periods.splice(i); break } // no room for even a 15 min period — delete from here
              periods[i] = { ...periods[i], to: newTo }
            }
            i++
          } else break // no overlap, rest of periods are fine
        }
      }
      days[dayIndex] = { ...days[dayIndex], periods }
      return days
    })
  }

  const btnAdd = "flex items-center justify-center w-7 h-7 rounded-md text-gray-400 hover:text-gray-700 hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed transition-colors text-base font-light"
  const btnTrash = "flex items-center justify-center w-7 h-7 rounded-md text-gray-400 hover:text-red-500 hover:bg-red-50 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"

  // false when no time can be added at either end: first period starts at 00:00 (TIME_OPTIONS[0]) and last period ends at 23:59 (TIME_OPTIONS[last]). gaps in the middle are not considered
  const canAddPeriod = (d: typeof days[0]) =>
    !(d.periods[d.periods.length - 1].to === TIME_OPTIONS[TIME_OPTIONS.length - 1] && d.periods[0].from === TIME_OPTIONS[0])

  return (
    <div className="bg-white border border-gray-200 rounded-xl shadow-sm">

      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100">
        <span className="text-sm font-semibold text-gray-700 tracking-wide uppercase">
          {editingSchedule ? 'Edit Schedule' : 'New Schedule'}
        </span>
        <button onClick={handleClose} className="text-gray-400 hover:text-gray-600 transition-colors p-1 rounded-md hover:bg-gray-100">
          <IconX size={16} />
        </button>
      </div>

      {/* Settings row */}
      <div className="px-6 pt-5 pb-4 flex items-end gap-4 border-b border-gray-100">
        <Select
          label="Tutor"
          placeholder="Select tutor"
          data={tutors.map(t => ({ value: String(t.id), label: `${t.first_name} ${t.last_name}` }))}
          value={selectedTutorId}
          onChange={val => { setSelectedTutorId(val); if (touched.tutor) setErrors(validate(val, scheduleName)) }}
          onBlur={() => setTouched(prev => ({ ...prev, tutor: true }))}
          error={touched.tutor ? errors.tutor : undefined}
          className="w-52"
        />
        <TextInput
          label="Name"
          placeholder="e.g. Regular Hours"
          className="flex-1"
          value={scheduleName}
          onChange={e => { setScheduleName(e.target.value); if (touched.name) setErrors(validate(selectedTutorId, e.target.value)) }}
          onBlur={() => setTouched(prev => ({ ...prev, name: true }))}
          error={touched.name ? errors.name : undefined}
        />
        <Select
          label="Timezone"
          data={Intl.supportedValuesOf('timeZone')}
          value={timezone}
          onChange={val => val && setTimezone(val)}
          searchable
          allowDeselect={false}
          className="w-52"
        />
        <div className="pb-1">
          <Switch label="Default" checked={defaultSchedule} onChange={() => setDefaultSchedule(prev => !prev)} />
        </div>
      </div>

      {/* Day grid */}
      <div className="px-6 py-4 flex flex-col">
        {days.map((day, dayIndex) => (
          <div
            key={day.label}
            className="flex items-start gap-4 py-2.5 border-b border-gray-50 last:border-0"
          >
            <div className="flex items-center gap-3 w-24 shrink-0 self-center">
              <Switch
                checked={day.enabled}
                onChange={() => setDays(prev => prev.map((d, di) =>
                  di === dayIndex ? { ...d, enabled: !d.enabled } : d
                ))}
              />
              <span className="text-sm font-medium text-gray-600">{day.label}</span>
            </div>

            {day.enabled ? (
              <div className="flex flex-col gap-1.5 flex-1">
                {day.periods.map((period, periodIndex) => (
                  <div key={periodIndex} className="flex items-center gap-2">
                    <Select
                      data={TIME_OPTIONS.filter(t => (periodIndex === 0 || t > day.periods[periodIndex - 1].to) && t < period.to)}
                      value={period.from}
                      onChange={handleTimeChange(dayIndex, periodIndex, 'from')}
                      searchable selectFirstOptionOnChange allowDeselect={false} withCheckIcon={false}
                      className="w-28"
                      comboboxProps={{ withinPortal: false }}
                    />
                    <span className="text-gray-400 text-sm select-none">–</span>
                    <Select
                      data={TIME_OPTIONS.filter(t => t > period.from)}
                      value={period.to}
                      onChange={handleTimeChange(dayIndex, periodIndex, 'to')}
                      searchable selectFirstOptionOnChange allowDeselect={false} withCheckIcon={false}
                      className="w-28"
                      comboboxProps={{ withinPortal: false }}
                    />
                    <div className="flex items-center gap-0.5 ml-1 shrink-0">
                      {periodIndex === 0 ? (
                        <button type="button" onClick={() => handleAddPeriod(dayIndex)} disabled={!canAddPeriod(day)} className={btnAdd}>+</button>
                      ) : (
                        <button type="button" onClick={() => handleRemovePeriod(dayIndex, periodIndex)} className={btnTrash}>
                          <IconTrash size={13} />
                        </button>
                      )}
                      {periodIndex === 0 && day.periods.length > 1 ? (
                        <button type="button" onClick={() => handleRemovePeriod(dayIndex, 0)} className={btnTrash}>
                          <IconTrash size={13} />
                        </button>
                      ) : (
                        <div className="w-7 h-7" />
                      )}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <span className="text-sm text-gray-300 pt-1.5">—</span>
            )}
          </div>
        ))}
      </div>

      {/* Footer */}
      <div className="px-6 py-4 border-t border-gray-100">
        {submitError && <p className="text-sm text-red-500 mb-3">{submitError}</p>}
        {confirmingDiscard ? (
          <div className="flex items-center justify-between">
            <span className="text-sm text-gray-500">Discard changes?</span>
            <div className="flex gap-2">
              <Button variant="default" size="xs" onClick={() => setConfirmingDiscard(false)}>Keep editing</Button>
              <Button color="red" size="xs" onClick={() => { reset(); onClose() }}>Discard</Button>
            </div>
          </div>
        ) : (
          <div className="flex justify-end gap-2">
            <Button variant="default" onClick={handleClose}>Cancel</Button>
            <Button disabled={!allValid} onClick={editingSchedule ? handleSave : handleCreate}>
              {editingSchedule ? 'Save' : 'Create'}
            </Button>
          </div>
        )}
      </div>

    </div>
  )
}

export default ScheduleForm
