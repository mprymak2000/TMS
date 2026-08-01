import { useState, useRef } from 'react'
import { Modal, Select, NumberInput, Checkbox, Textarea, Button, Group, Stack } from '@mantine/core'
import type { Student, Tutor } from './types'

interface Props {
  opened: boolean  // added: Mantine Modal needs this to know when to show
  students: Record<number, Student>
  tutors: Record<number, Tutor>
  onClose: () => void
  onSave: () => void
}



const LessonAddModal = ({ opened, students, tutors, onClose, onSave }: Props) => {
  // note: Mantine Select requires string | null — convert to Number when building payload
  const [studentId, setStudentId] = useState<string | null>(null)
  const [tutorId, setTutorId] = useState<string | null>(null)
  // note: Mantine DatePickerInput uses Date | null — convert to YYYY-MM-DD string for API
  const [date, setDate] = useState<string | null>(null)
  // note: Mantine NumberInput requires number | string — check for '' when building payload
  const [hrs, setHrs] = useState<number | string>('')
  const [feeOverride, setFeeOverride] = useState<number | string>('')
  const [tutorPayOverride, setTutorPayOverride] = useState<number | string>('')
  const [payStatus, setPayStatus] = useState<boolean>(false)
  const [notes, setNotes] = useState<string>('')
  const [errors, setErrors] = useState<string[]>([])
  const [confirming, setConfirming] = useState(false)
  const dateInputRef = useRef<HTMLInputElement>(null)

  const resetForm = () => {
    setStudentId(null)
    setTutorId(null)
    setDate(null)
    setHrs('')
    setFeeOverride('')
    setTutorPayOverride('')
    setPayStatus(false)
    setNotes('')
    setErrors([])
    if (dateInputRef.current) dateInputRef.current.value = ''
  }

    const studentOptions = Object.values(students).map(s => (
        { value: s.id.toString(), label: `${s.first_name} ${s.last_name}`}
    ))
    
    const tutorOptions = Object.values(tutors).map(t => (
        { value: t.id.toString(), label: `${t.first_name} ${t.last_name}`}
    ))


    const handleSubmit = async () => {
        const validationErrors: string[] = []
        if (!studentId) validationErrors.push('Student is required.')
        if (!tutorId) validationErrors.push('Tutor is required.')
        if (!date) validationErrors.push('Date is required.')
        if (validationErrors.length > 0) {
            setErrors(validationErrors)
            return
        }
        setErrors([])

        const payload = {
            student_id: Number(studentId),
            tutor_id: Number(tutorId),
            date: date, // convert to YYYY-MM-DD
            hrs: hrs === '' ? null : Number(hrs),
            fee_override: feeOverride === '' ? null : Number(feeOverride),
            tutor_pay_override: tutorPayOverride === '' ? null : Number(tutorPayOverride),
            pay_status: payStatus,
            notes: notes || null,
        }

        try {
            const response = await fetch(`${import.meta.env.VITE_API_URL}/lessons/`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            })
            const result = await response.json()
            if (!response.ok) {
                setErrors([result.detail || 'Failed to create lesson'])
                return
            }
            onSave()
            resetForm()
            onClose()
        } catch {
            setErrors(['Unknown error. Please try again.'])
        }

    }

  const isDirty = studentId !== null || tutorId !== null || date !== null || hrs !== '' || feeOverride !== '' || tutorPayOverride !== '' || payStatus || notes !== ''

  const handleClose = () => {
    if (isDirty) { setConfirming(true); return }
    resetForm()
    onClose()
  }

  const handleConfirmDiscard = () => {
    setConfirming(false)
    resetForm()
    onClose()
  }

  const canSubmit = !!studentId && !!tutorId && !!date

  return (
    <Modal opened={opened} onClose={handleClose} title="Add Lesson" size="lg" centered>
      <Stack>
        {/* Section 1 - Core */}
        <Group grow>
          <Select label="Student" placeholder="Select student" data={studentOptions} value={studentId} onChange={setStudentId} searchable />
          <Select label="Tutor" placeholder="Select tutor" data={tutorOptions} value={tutorId} onChange={setTutorId} searchable />
        </Group>

        <Group grow>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Date</label>
            <input
              ref={dateInputRef}
              type="date"
              className={`w-full rounded-sm border border-gray-300 bg-white px-3 py-[7px] text-sm outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 ${date ? 'text-gray-900' : 'text-gray-400'}`}
              onChange={e => setDate(e.target.value || null)}
            />
          </div>
          <NumberInput label="Hours" placeholder="e.g. 1.5" min={0} step={0.5} value={hrs} onChange={setHrs} />
        </Group>

        {/* Section 2 - Financials */}
        <Group grow>
          <NumberInput label="Fee Override (optional)" placeholder="$" min={0} step={0.01} value={feeOverride} onChange={setFeeOverride} />
          <NumberInput label="Tutor Pay Override (optional)" placeholder="$" min={0} step={0.01} value={tutorPayOverride} onChange={setTutorPayOverride} />
        </Group>

        <Checkbox label="Fee paid" checked={payStatus} onChange={e => setPayStatus(e.target.checked)} />

        <Textarea label="Notes (optional)" placeholder="Any notes..." rows={3} value={notes} onChange={e => setNotes(e.target.value)} />

        {errors.length > 0 && (
          <ul className="text-red-500 text-sm list-disc pl-4">
            {errors.map((e, i) => <li key={i}>{e}</li>)}
          </ul>
        )}

        {/* Buttons */}
        {confirming ? (
          <div className="flex items-center justify-between pt-2 border-t border-gray-200">
            <span className="text-sm text-gray-500">Discard unsaved changes?</span>
            <Group gap="xs">
              <Button variant="subtle" onClick={() => setConfirming(false)}>Keep editing</Button>
              <Button color="red" variant="light" onClick={handleConfirmDiscard}>Discard</Button>
            </Group>
          </div>
        ) : (
          <Group justify="flex-end">
            <Button variant="default" onClick={handleClose}>Cancel</Button>
            <Button onClick={handleSubmit} color="teal" disabled={!canSubmit}>Save Lesson</Button>
          </Group>
        )}
      </Stack>
    </Modal>
  )
}

export default LessonAddModal
