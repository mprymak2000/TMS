import { useState } from 'react'
import { Select } from '@mantine/core'
import type { Student, Tutor } from './types'

interface BulkRow {
  studentId: string | null
  tutorId: string | null
  date: string
  hrs: string
  feeOverride: string
  tutorPayOverride: string
  payStatus: boolean
  notes: string
}

interface BulkRowErrors {
  studentId?: string
  tutorId?: string
  date?: string
  hrs?: string
  feeOverride?: string
  tutorPayOverride?: string
}

interface BulkRowTouched {
  studentId?: boolean
  tutorId?: boolean
  date?: boolean
  hrs?: boolean
  feeOverride?: boolean
  tutorPayOverride?: boolean
}

const emptyRow = (): BulkRow => ({
  studentId: null,
  tutorId: null,
  date: '',
  hrs: '',
  feeOverride: '',
  tutorPayOverride: '',
  payStatus: false,
  notes: '',
})

const validateRow = (row: BulkRow): BulkRowErrors => {
  const e: BulkRowErrors = {}
  if (!row.studentId) e.studentId = 'Required'
  if (!row.tutorId) e.tutorId = 'Required'
  if (!row.date) e.date = 'Required'
  if (row.hrs !== '' && Number(row.hrs) < 0) e.hrs = 'Cannot be negative'
  if (row.feeOverride !== '' && Number(row.feeOverride) < 0) e.feeOverride = 'Cannot be negative'
  if (row.tutorPayOverride !== '' && Number(row.tutorPayOverride) < 0) e.tutorPayOverride = 'Cannot be negative'
  return e
}

interface Props {
  students: Record<number, Student>
  tutors: Record<number, Tutor>
  onCancel: () => void
  onSave: () => void
}

export default function BulkAddCard({ students, tutors, onCancel, onSave }: Props) {
  const [rows, setRows] = useState<BulkRow[]>([emptyRow()])
  const [errors, setErrors] = useState<BulkRowErrors[]>([{}])
  const [touched, setTouched] = useState<BulkRowTouched[]>([{}])
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [confirmingCancel, setConfirmingCancel] = useState(false)

  const studentOptions = Object.values(students).map(s => ({
    value: s.id.toString(),
    label: `${s.first_name} ${s.last_name}`,
  }))

  const tutorOptions = Object.values(tutors).map(t => ({
    value: t.id.toString(),
    label: `${t.first_name} ${t.last_name}`,
  }))

  const updateRow = (index: number, field: keyof BulkRow, value: string | boolean | null) => {
    setRows(prev => prev.map((r, i) => i === index ? { ...r, [field]: value } : r))
  }

  const touchField = (index: number, field: keyof BulkRowTouched) => {
    setTouched(prev => prev.map((t, i) => i === index ? { ...t, [field]: true } : t))
    setErrors(prev => prev.map((e, i) => i === index ? validateRow(rows[index]) : e))
  }

  const addRow = () => {
    setRows(prev => [...prev, emptyRow()])
    setErrors(prev => [...prev, {}])
    setTouched(prev => [...prev, {}])
  }

  const removeRow = (index: number) => {
    if (rows.length === 1) return
    setRows(prev => prev.filter((_, i) => i !== index))
    setErrors(prev => prev.filter((_, i) => i !== index))
    setTouched(prev => prev.filter((_, i) => i !== index))
  }

  const allValid = rows.every(row => Object.keys(validateRow(row)).length === 0)

  const handleSubmit = async () => {
    const allErrors = rows.map(validateRow)
    setErrors(allErrors)
    setTouched(rows.map(() => ({
      studentId: true, tutorId: true, date: true, hrs: true, feeOverride: true, tutorPayOverride: true,
    })))
    if (allErrors.some(e => Object.keys(e).length > 0)) return

    const payload = rows.map(row => ({
      student_id: Number(row.studentId),
      tutor_id: Number(row.tutorId),
      date: row.date,
      hrs: row.hrs === '' ? null : Number(row.hrs),
      fee_override: row.feeOverride === '' ? null : Number(row.feeOverride),
      tutor_pay_override: row.tutorPayOverride === '' ? null : Number(row.tutorPayOverride),
      pay_status: row.payStatus,
      notes: row.notes || null,
    }))

    try {
      const response = await fetch(`${import.meta.env.VITE_API_URL}/lessons/bulk_create`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      const result = await response.json()
      if (!response.ok) {
        setSubmitError(typeof result.detail === 'string' ? result.detail : 'Failed to create lessons')
        return
      }
      onSave()
      onCancel()
    } catch {
      setSubmitError('Network error. Please try again.')
    }
  }

  // Returns tailwind border classes for a native input field based on touched/error state
  const inputBorder = (index: number, field: keyof BulkRowErrors) => {
    if (!touched[index]?.[field]) return 'border-gray-300 focus:border-indigo-500 focus:ring-indigo-500'
    if (errors[index]?.[field]) return 'border-red-400 focus:border-red-500 focus:ring-red-500'
    return 'border-green-400 focus:border-green-500 focus:ring-green-500'
  }

  // Returns Mantine Select classNames for the input element
  const selectInputClass = (index: number, field: keyof BulkRowErrors) => {
    if (!touched[index]?.[field]) return ''
    if (errors[index]?.[field]) return '!border-red-400'
    return '!border-green-400'
  }

  const isDirty = rows.some(r => r.studentId || r.tutorId || r.date || r.hrs || r.feeOverride || r.tutorPayOverride || r.payStatus || r.notes)

  const handleCancel = () => {
    if (isDirty) setConfirmingCancel(true)
    else onCancel()
  }

  return (
    <div className="rounded-lg border border-gray-200 shadow-sm">
      {/* Card header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 bg-gray-50 rounded-t-lg">
        <span className="text-sm font-semibold text-gray-700">
          Bulk Add Lessons
          <span className="ml-2 text-gray-400 font-normal">{rows.length} row{rows.length !== 1 ? 's' : ''}</span>
        </span>
        {confirmingCancel ? (
          <div className="flex items-center gap-3">
            <span className="text-sm text-gray-500">Discard all rows?</span>
            <button
              className="text-sm px-3 py-1.5 rounded border border-gray-300 text-gray-600 hover:bg-white transition-colors"
              onClick={() => setConfirmingCancel(false)}
            >
              Keep editing
            </button>
            <button
              className="text-sm px-3 py-1.5 rounded bg-red-600 hover:bg-red-700 text-white transition-colors"
              onClick={onCancel}
            >
              Discard
            </button>
          </div>
        ) : (
          <div className="flex items-center gap-2">
            {submitError && <span className="text-xs text-red-500">{submitError}</span>}
            <button
              className="text-sm px-3 py-1.5 rounded border border-gray-300 text-gray-600 hover:bg-white transition-colors"
              onClick={handleCancel}
            >
              Cancel
            </button>
            <button
              className="text-sm px-4 py-1.5 rounded bg-emerald-600 hover:bg-emerald-700 text-white font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              onClick={handleSubmit}
              disabled={!allValid}
            >
              Save {rows.length} Lesson{rows.length !== 1 ? 's' : ''}
            </button>
          </div>
        )}
      </div>

      {/* Column headers */}
      <div className="flex items-center gap-2 px-4 py-2 bg-gray-50 border-b border-gray-100 text-xs font-medium text-gray-500 uppercase tracking-wide">
        <span className="w-6 shrink-0" />
        <span className="w-36 shrink-0">Student <span className="text-red-400">*</span></span>
        <span className="w-32 shrink-0">Tutor <span className="text-red-400">*</span></span>
        <span className="w-32 shrink-0">Date <span className="text-red-400">*</span></span>
        <span className="w-16 shrink-0">Hrs</span>
        <span className="w-24 shrink-0">Fee Override</span>
        <span className="w-24 shrink-0">Pay Override</span>
        <span className="w-10 shrink-0 text-center">Paid</span>
        <span className="flex-1">Notes</span>
        <span className="w-6 shrink-0" />
      </div>

      {/* Rows */}
      <div className="divide-y divide-gray-100">
        {rows.map((row, i) => {
          const rowErrors = errors[i] ?? {}
          const hasAnyError = Object.keys(rowErrors).length > 0 && Object.values(touched[i] ?? {}).some(Boolean)
          const fieldLabels: Record<string, string> = {
            studentId: 'Student', tutorId: 'Tutor', date: 'Date',
            hrs: 'Hours', feeOverride: 'Fee Override', tutorPayOverride: 'Tutor Pay Override',
          }
          const errorMessages = Object.entries(rowErrors)
            .filter(([field]) => touched[i]?.[field as keyof BulkRowTouched])
            .map(([field, msg]) => `${fieldLabels[field] ?? field}: ${msg}`)

          return (
            <div key={i}>
              <div className="flex items-center gap-2 px-4 py-2">
                {/* Row number */}
                <span className="w-6 shrink-0 text-xs text-gray-400 text-center">{i + 1}</span>

                {/* Student */}
                <div className="w-36 shrink-0">
                  <Select
                    size="xs"
                    placeholder="Student"
                    data={studentOptions}
                    value={row.studentId}
                    onChange={v => updateRow(i, 'studentId', v)}
                    onBlur={() => touchField(i, 'studentId')}
                    searchable
                    classNames={{ input: selectInputClass(i, 'studentId') }}
                  />
                </div>

                {/* Tutor */}
                <div className="w-32 shrink-0">
                  <Select
                    size="xs"
                    placeholder="Tutor"
                    data={tutorOptions}
                    value={row.tutorId}
                    onChange={v => updateRow(i, 'tutorId', v)}
                    onBlur={() => touchField(i, 'tutorId')}
                    searchable
                    classNames={{ input: selectInputClass(i, 'tutorId') }}
                  />
                </div>

                {/* Date */}
                <input
                  type="date"
                  value={row.date}
                  onChange={e => updateRow(i, 'date', e.target.value)}
                  onBlur={() => touchField(i, 'date')}
                  className={`w-32 shrink-0 border rounded px-2 py-1 text-sm focus:outline-none focus:ring-1 ${inputBorder(i, 'date')} ${!row.date ? 'text-gray-400' : 'text-gray-900'}`}
                />

                {/* Hrs */}
                <input
                  type="number"
                  min={0}
                  step={0.5}
                  placeholder="e.g. 1.5"
                  value={row.hrs}
                  onChange={e => updateRow(i, 'hrs', e.target.value)}
                  onBlur={() => touchField(i, 'hrs')}
                  className={`w-16 shrink-0 border rounded px-2 py-1 text-sm focus:outline-none focus:ring-1 ${inputBorder(i, 'hrs')}`}
                />

                {/* Fee Override */}
                <input
                  type="number"
                  min={0}
                  step={0.01}
                  placeholder="$"
                  value={row.feeOverride}
                  onChange={e => updateRow(i, 'feeOverride', e.target.value)}
                  onBlur={() => touchField(i, 'feeOverride')}
                  className={`w-24 shrink-0 border rounded px-2 py-1 text-sm focus:outline-none focus:ring-1 ${inputBorder(i, 'feeOverride')}`}
                />

                {/* Tutor Pay Override */}
                <input
                  type="number"
                  min={0}
                  step={0.01}
                  placeholder="$"
                  value={row.tutorPayOverride}
                  onChange={e => updateRow(i, 'tutorPayOverride', e.target.value)}
                  onBlur={() => touchField(i, 'tutorPayOverride')}
                  className={`w-24 shrink-0 border rounded px-2 py-1 text-sm focus:outline-none focus:ring-1 ${inputBorder(i, 'tutorPayOverride')}`}
                />

                {/* Pay Status */}
                <div className="w-10 shrink-0 flex justify-center">
                  <input
                    type="checkbox"
                    checked={row.payStatus}
                    onChange={e => updateRow(i, 'payStatus', e.target.checked)}
                    className="h-4 w-4 rounded border-gray-300 accent-indigo-600 cursor-pointer"
                  />
                </div>

                {/* Notes */}
                <input
                  type="text"
                  placeholder="Notes..."
                  value={row.notes}
                  onChange={e => updateRow(i, 'notes', e.target.value)}
                  className="flex-1 border border-gray-300 rounded px-2 py-1 text-sm focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
                />

                {/* Remove row */}
                <button
                  className="w-6 shrink-0 text-gray-300 hover:text-red-400 transition-colors text-lg leading-none disabled:opacity-30 disabled:cursor-not-allowed"
                  onClick={() => removeRow(i)}
                  disabled={rows.length === 1}
                  title="Remove row"
                >
                  ×
                </button>
              </div>

              {/* Per-row error */}
              {hasAnyError && (
                <div className="px-4 pb-2 -mt-1">
                  <p className="text-xs text-red-500">{errorMessages.join(' · ')}</p>
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* Add row footer */}
      <div className="px-4 py-3 border-t border-gray-100">
        <button
          className="text-sm text-indigo-600 hover:text-indigo-800 font-medium transition-colors"
          onClick={addRow}
        >
          + Add Row
        </button>
      </div>
    </div>
  )
}
