import { useEffect, useState } from 'react'
import type { Lesson, Student, Tutor, LessonEdit } from './types'
import dayjs from 'dayjs'

export interface LessonEditErrors {
  date?: string
  hrs?: string
  feeOverride?: string
  tutorPayOverride?: string
}

export const validateEditField = (field: keyof LessonEditErrors, value: number | string): string | undefined => {
  switch (field) {
    case 'date': return !value ? 'Date is required' : undefined
    case 'hrs': return value !== '' && Number(value) < 0 ? 'hours cannot be less than 0' : undefined
    case 'feeOverride': return value !== '' && Number(value) < 0 ? 'fee cannot be less than 0' : undefined
    case 'tutorPayOverride': return value !== '' && Number(value) < 0 ? 'tutor payout cannot be less than 0' : undefined
  }
}

const filterLessons = (
  lessons: Lesson[],
  studentId: number | null,
  tutorId: number | null,
  dateFrom: string | null,
  dateTo: string | null
): Lesson[] =>
  lessons.filter(lesson => {
    if (studentId && lesson.student_id !== studentId) return false
    if (tutorId && lesson.tutor_id !== tutorId) return false
    if (dateFrom && lesson.date < dateFrom) return false
    if (dateTo && lesson.date > dateTo) return false
    return true
  })

const sortLessons = (lessons: Lesson[]): Lesson[] =>
  [...lessons].sort((a, b) => a.date < b.date ? 1 : -1)

export function useLessons() {
  const [lessons, setLessons] = useState<Lesson[]>([])
  const [students, setStudents] = useState<Record<number, Student>>({})
  const [tutors, setTutors] = useState<Record<number, Tutor>>({})
  const [reload, setReload] = useState(false)
  const [loading, setLoading] = useState(false)

  const [filterStudent, setFilterStudent] = useState<number | null>(null)
  const [filterTutor, setFilterTutor] = useState<number | null>(null)
  const [filterDateFrom, setFilterDateFrom] = useState<string | null>(null)
  const [filterDateTo, setFilterDateTo] = useState<string | null>(null)

  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editedLesson, setEditedLesson] = useState<LessonEdit | null>(null)
  const [editErrors, setEditErrors] = useState<LessonEditErrors>({})
  const [editSubmitError, setEditSubmitError] = useState<string | null>(null)

  const [modalOpen, setModalOpen] = useState(false)
  const [bulkAddMode, setBulkAddMode] = useState(false)

  const [selectionMode, setSelectionMode] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const [confirmingBulkDelete, setConfirmingBulkDelete] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  useEffect(() => {
    const load = async () => {
      setLoading(true)
      try {
        const [lessonsRes, studentsRes, tutorsRes] = await Promise.all([
          fetch(`${import.meta.env.VITE_API_URL}/lessons/`),
          fetch(`${import.meta.env.VITE_API_URL}/students/`),
          fetch(`${import.meta.env.VITE_API_URL}/tutors/`),
        ])
        setLessons(await lessonsRes.json())
        const studentsData: Student[] = await studentsRes.json()
        const tutorsData: Tutor[] = await tutorsRes.json()
        setStudents(Object.fromEntries(studentsData.map(s => [s.id, s])))
        setTutors(Object.fromEntries(tutorsData.map(t => [t.id, t])))
      } catch (err) {
        console.error('An unknown error occurred while loading data, please try again', err)
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [reload])

  const triggerReload = () => setReload(prev => !prev)

  // Derived
  const ungrouped = sortLessons(filterLessons(lessons, filterStudent, filterTutor, filterDateFrom, filterDateTo))

  const byMonth = ungrouped.reduce((acc, lesson) => {
    const key = lesson.date.slice(0, 7)
    if (!acc[key]) acc[key] = []
    acc[key].push(lesson)
    return acc
  }, {} as Record<string, Lesson[]>)
  const months = Object.keys(byMonth).sort().reverse()

  const getWeekStart = (dateStr: string): string => {
    const date = dayjs(dateStr)
    const dayDiff = date.day() === 0 ? 6 : date.day()-1 // convert Sunday=0 to 6, Mon=1 to 0, Tue=2 to 1, etc.
    return date.subtract(dayDiff, 'day').format('YYYY-MM-DD')
  }

  const byWeek = ungrouped.reduce((acc, lesson) => {
    const key = getWeekStart(lesson.date)
    if (!acc[key]) acc[key] = []
    acc[key].push(lesson)
    return acc
  }, {} as Record<string, Lesson[]>)
  const weeks = Object.keys(byWeek).sort().reverse()
  

  const selectedLessons = lessons.filter(l => selectedIds.has(l.id))
  const selectionSummary = selectedIds.size > 0 ? {
    totalLessons: selectedLessons.length,
    totalHours: selectedLessons.reduce((sum, l) => sum + (l.hrs ?? 0), 0),
    totalRevenue: selectedLessons.reduce((sum, l) => sum + l.fee, 0),
    totalPayout: selectedLessons.reduce((sum, l) => sum + l.tutor_payout, 0),
  } : null

  // Handlers
  const handleToggleSelect = (lessonId: number) => {
    setSelectedIds(prev => {
      const newSet = new Set(prev)
      if (newSet.has(lessonId)) newSet.delete(lessonId)
      else newSet.add(lessonId)
      return newSet
    })
  }

  const handleCancelSelection = () => {
    setSelectionMode(false)
    setSelectedIds(new Set())
  }

  const handleSelectAll = (displayed: Lesson[]) => {
    if (displayed.every(l => selectedIds.has(l.id))) setSelectedIds(new Set())
    else {
      setSelectedIds(new Set(displayed.map(l => l.id)))
      setSelectionMode(true)
    }
  }

  const handleRowClick = (lessonId: number) => {
    if (selectionMode) handleToggleSelect(lessonId)
    else {
      setExpandedId(expandedId === lessonId ? null : lessonId)
      setConfirmingDelete(false)
      setDeleteError(null)
    }
  }

  const handleDelete = async (lessonId: number) => {
    try {
      const response = await fetch(`${import.meta.env.VITE_API_URL}/lessons/${lessonId}/`, { method: 'DELETE' })
      if (!response.ok) {
        const result = await response.json()
        setDeleteError(result.detail || 'Failed to delete lesson')
        return
      }
      setExpandedId(null)
      triggerReload()
    } catch {
      setDeleteError('Network error. Please try again.')
    }
  }

  const handleBulkDelete = async () => {
    try {
      const response = await fetch(`${import.meta.env.VITE_API_URL}/lessons/bulk_delete/`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lesson_ids: [...selectedIds] }),
      })
      if (!response.ok) {
        const result = await response.json()
        setDeleteError(typeof result.detail === 'string' ? result.detail : 'Failed to delete selected lessons')
        return
      }
      setConfirmingBulkDelete(false)
      setSelectedIds(new Set())
      setSelectionMode(false)
      triggerReload()
    } catch {
      setDeleteError('Network error. Please try again.')
    }
  }

  const handleEdit = (lesson: Lesson) => {
    setEditingId(lesson.id)
    setEditedLesson({
      date: lesson.date,
      hrs: lesson.hrs ?? '',
      feeOverride: lesson.is_fee_overridden ? lesson.fee : '',
      tutorPayOverride: lesson.is_tutor_payout_overridden ? lesson.tutor_payout : '',
      payStatus: lesson.pay_status,
      notes: lesson.notes,
    })
    setConfirmingDelete(false)
    setEditErrors({})
    setEditSubmitError(null)
  }

  const handleCancelEdit = () => {
    setEditingId(null)
    setEditedLesson(null)
    setEditErrors({})
    setEditSubmitError(null)
  }

  const handleEditSave = async () => {
    if (!editedLesson || editingId === null) return
    if (Object.values(editErrors).some(e => e !== undefined)) return
    setEditSubmitError(null)

    const payload = {
      date: editedLesson.date,
      hrs: editedLesson.hrs === '' ? null : Number(editedLesson.hrs),
      fee_override: editedLesson.feeOverride === '' ? null : Number(editedLesson.feeOverride),
      tutor_pay_override: editedLesson.tutorPayOverride === '' ? null : Number(editedLesson.tutorPayOverride),
      pay_status: editedLesson.payStatus,
      notes: editedLesson.notes,
    }

    try {
      const response = await fetch(`${import.meta.env.VITE_API_URL}/lessons/${editingId}/`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      const result = await response.json()
      if (!response.ok) {
        setEditSubmitError(result.detail || 'Failed to update lesson')
        return
      }
      setEditingId(null)
      setEditedLesson(null)
      triggerReload()
    } catch {
      setEditSubmitError('Network error. Please try again.')
    }
  }

  const handleExport = () => {
    const params = new URLSearchParams()
    if (filterStudent) params.set('student_id', filterStudent.toString())
    if (filterTutor) params.set('tutor_id', filterTutor.toString())
    if (filterDateFrom) params.set('date_from', filterDateFrom)
    if (filterDateTo) params.set('date_to', filterDateTo)
    window.open(`${import.meta.env.VITE_API_URL}/lessons/export?${params}`, '_blank')
  }

  const handleEditFieldChange = (field: keyof LessonEdit, value: string | boolean) => {
    setEditedLesson(prev => prev ? { ...prev, [field]: value } : prev)
    if (field !== 'payStatus' && field !== 'notes')
      setEditErrors(prev => ({ ...prev, [field]: validateEditField(field as keyof LessonEditErrors, value as string) }))
  }

  return {
    // data
    lessons, students, tutors,
    ungrouped, byMonth, months, byWeek, weeks, selectionSummary,
    // ui state
    loading,
    expandedId, editingId, editedLesson, editErrors, editSubmitError,
    modalOpen, setModalOpen,
    bulkAddMode, setBulkAddMode,
    selectionMode, setSelectionMode,
    selectedIds,
    confirmingDelete, setConfirmingDelete,
    confirmingBulkDelete, setConfirmingBulkDelete,
    deleteError, setDeleteError,
    // filter setters
    setFilterStudent, setFilterTutor, setFilterDateFrom, setFilterDateTo,
    handleExport,
    // handlers
    handleRowClick, handleToggleSelect, handleCancelSelection, handleSelectAll,
    handleDelete, handleBulkDelete,
    handleEdit, handleCancelEdit, handleEditSave, handleEditFieldChange,
    triggerReload,
  }
}