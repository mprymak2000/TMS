// TODO: Summary card — make Total Payout tile clickable to expand payout breakdown per tutor
// TODO: Google Calendar integration — fetch events, match title to student name, derive hrs from duration, pre-populate bulk add form for review before saving. Tutor defaults to owner or manual pick.

import { Select, Input, Modal, Button, Group } from '@mantine/core'
import { IconCircleX, IconTrash, IconSquareCheck, IconPlus, IconStack2, IconFileDownload } from '@tabler/icons-react'
import LessonAddModal from './LessonAddModal'
import BulkAddCard from './BulkAddCard'
import LessonRow from './LessonRow'
import { useLessons } from './useLessons'
import { useState } from 'react'
import dayjs from 'dayjs'

export default function LessonsTable() {
  const {
    students, tutors,
    ungrouped, months, byMonth, weeks, byWeek, selectionSummary,
    expandedId, editingId, editedLesson, editErrors, editSubmitError,
    modalOpen, setModalOpen,
    bulkAddMode, setBulkAddMode,
    selectionMode, setSelectionMode,
    selectedIds,
    confirmingDelete, setConfirmingDelete,
    confirmingBulkDelete, setConfirmingBulkDelete,
    deleteError, setDeleteError,
    setFilterStudent, setFilterTutor, setFilterDateFrom, setFilterDateTo,
    handleRowClick, handleCancelSelection, handleSelectAll, handleExport,
    handleDelete, handleBulkDelete,
    handleEdit, handleCancelEdit, handleEditSave, handleEditFieldChange,
    triggerReload,
  } = useLessons()

  const [view, setView] = useState<'All' | 'Week' | 'Month'>('All')
  const [periodIndex, setPeriodIndex] = useState(0)
  
  const lessonsToDisplay = (() => {
    if (view === 'Week') return [...(byWeek[weeks[periodIndex]] ?? [])].reverse()
    if (view === 'Month') return [...(byMonth[months[periodIndex]] ?? [])].reverse()
    return ungrouped
  })()

  let dayIdx = -1
  let lastDate = ''
  const tableRows = lessonsToDisplay.flatMap(lesson => {
    const isNewDay = lesson.date !== lastDate
    if (isNewDay) { dayIdx++; lastDate = lesson.date }
    const tabColor = dayIdx % 2 === 0 ? 'bg-amber-300' : 'bg-indigo-300'
    return [
      isNewDay && (
        <tr key={`day-${lesson.date}`} className="border-t border-gray-200 shadow">
          <td colSpan={6} className={`relative before:absolute before:left-0 before:top-0 before:bottom-0 before:w-[5px] ${tabColor.replace('bg-', 'before:bg-')}`}>
            <span className={`inline-flex items-center gap-6 pl-10 pr-10 py-1.5 rounded-tr-lg ${tabColor}`}>
              <span className={`text-xs font-semibold uppercase tracking-wider ${dayIdx % 2 === 0 ? 'text-amber-900' : 'text-indigo-900'}`}>
                {dayjs(lesson.date).format('dddd, MMM D')}
              </span>
              <span className={`text-xs ${dayIdx % 2 === 0 ? 'text-amber-700' : 'text-indigo-700'}`}>
                {lessonsToDisplay.filter(l => l.date === lesson.date).length} lessons
              </span>
            </span>
          </td>
        </tr>
      ),
      <LessonRow
        key={lesson.id}
        lesson={lesson}
        student={students[lesson.student_id]}
        tutor={tutors[lesson.tutor_id]}
        isExpanded={expandedId === lesson.id}
        isEditing={editingId === lesson.id}
        editedLesson={editedLesson}
        editErrors={editErrors}
        editSubmitError={editSubmitError}
        confirmingDelete={confirmingDelete}
        deleteError={deleteError}
        selectionMode={selectionMode}
        isSelected={selectedIds.has(lesson.id)}
        dayIndex={dayIdx}
        onRowClick={() => handleRowClick(lesson.id)}
        onEdit={() => handleEdit(lesson)}
        onCancelEdit={handleCancelEdit}
        onEditFieldChange={handleEditFieldChange}
        onEditSave={handleEditSave}
        onConfirmDelete={() => setConfirmingDelete(true)}
        onCancelDelete={() => { setConfirmingDelete(false); setDeleteError(null) }}
        onDelete={() => handleDelete(lesson.id)}
      />,
    ].filter(Boolean)
  })


  return (
    <div>
      {/* View toggle */}
      <div className="flex justify-center mb-4">
        <div className="inline-flex bg-gray-100 rounded-full p-1 gap-1">
          {(['Week', 'Month', 'All'] as const).map(field => (
            <button
              key={field}
              className={`px-5 py-1.5 rounded-full text-sm font-medium transition-all duration-200 ${field === view ? 'bg-white text-indigo-700 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
              onClick={() => { setView(field); handleCancelSelection() }}
            >
              {field}
            </button>
          ))}
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex items-center gap-3 mb-4 flex-wrap">
        <Input size="sm" type="date" onChange={e => setFilterDateFrom(e.target.value || null)} styles={{ input: { borderRadius: '8px', fontFamily: 'inherit', borderColor: '#e5e7eb' } }} />
        <span className="text-gray-400 text-sm">to</span>
        <Input size="sm" type="date" onChange={e => setFilterDateTo(e.target.value || null)} styles={{ input: { borderRadius: '8px', fontFamily: 'inherit', borderColor: '#e5e7eb' } }} />

        <button
          className="ml-auto flex items-center gap-1.5 bg-emerald-500 hover:bg-emerald-600 text-white text-sm font-medium px-4 py-1.5 rounded-full shadow-sm transition-all hover:shadow-md"
          onClick={() => setModalOpen(true)}
        >
          <IconPlus size={15} strokeWidth={2.5} />
          Add Lesson
        </button>
        <button
          className="flex items-center gap-1.5 bg-indigo-500 hover:bg-indigo-600 text-white text-sm font-medium px-4 py-1.5 rounded-full shadow-sm transition-all hover:shadow-md"
          onClick={() => setBulkAddMode(true)}
        >
          <IconStack2 size={15} strokeWidth={2} />
          Bulk Add
        </button>
        <button
          className="flex items-center gap-1.5 bg-gray-500 hover:bg-gray-600 text-white text-sm font-medium px-4 py-1.5 rounded-full shadow-sm transition-all hover:shadow-md"
          onClick={handleExport}
        >
          <IconFileDownload size={15} strokeWidth={2} />
          Export
        </button>

      </div>

      <>

        {bulkAddMode && (
          <div className="mb-4">
            <BulkAddCard
              students={students}
              tutors={tutors}
              onCancel={() => setBulkAddMode(false)}
              onSave={() => { triggerReload(); setBulkAddMode(false) }}
            />
          </div>
        )}

        <LessonAddModal
          opened={modalOpen}
          students={students}
          tutors={tutors}
          onSave={triggerReload}
          onClose={() => setModalOpen(false)}
        />

        <Modal
          opened={confirmingBulkDelete}
          onClose={() => { setConfirmingBulkDelete(false); setDeleteError(null) }}
          title={<span className="flex items-center gap-2"><IconCircleX size={24} className="text-red-500" /> Delete lessons</span>}
          centered
          size="sm"
        >
          <div className="flex flex-col gap-4">
            <p className="text-sm text-gray-700">
              <span className="font-semibold text-gray-900">{selectedIds.size} lesson{selectedIds.size !== 1 ? 's' : ''}</span> will be permanently deleted.
            </p>
            {deleteError && <p className="text-sm text-red-500">{deleteError}</p>}
            <Group justify="flex-end">
              <Button variant="default" onClick={() => { setConfirmingBulkDelete(false); setDeleteError(null) }}>Cancel</Button>
              <Button color="red" onClick={handleBulkDelete}>Delete</Button>
            </Group>
          </div>
        </Modal>

        {(view === 'Week' || view === 'Month') && (() => {
          const periods = view === 'Week' ? weeks : months // get ordered list of week/month start dates (keys of our Record<date bucket string, lesson list>) 
          const byPeriod = view === 'Week' ? byWeek : byMonth // get the actual Records with date buckets as keys and lessons in that time period as values 
          const period = periods[periodIndex] // get the selected period start date
          const lessons = byPeriod[period] || [] // get lessons for that period (empty array if no lessons)
          const revenue = lessons.reduce((sum, l) => sum + l.fee, 0)
          const payout = lessons.reduce((sum, l) => sum + l.tutor_payout, 0)
          const hours = lessons.reduce((sum, l) => sum + (l.hrs ?? 0), 0)
    
          return (
            <div className="rounded-lg border border-gray-200 bg-white shadow-sm overflow-hidden mb-4">
              {/* Navigator header */}
              <div className="flex items-center justify-between px-5 py-3 bg-gray-50 border-b border-gray-200">
                <div className="flex items-center gap-2">
                  <button
                    className="px-2 py-0.5 rounded border border-gray-200 text-gray-500 hover:bg-white disabled:opacity-30 disabled:cursor-not-allowed transition-colors text-sm"
                    onClick={() => setPeriodIndex(i => i + 1)}
                    disabled={periodIndex === periods.length - 1}
                  >←</button>
                  <span className="text-sm font-semibold text-gray-700 w-40 text-center">
                    {view === 'Week'
                      ? `${dayjs(period).format('MMM D')} – ${dayjs(period).add(6, 'day').format('MMM D, YYYY')}`
                      : dayjs(period).format('MMMM YYYY')}
                  </span>
                  <button
                    className="px-2 py-0.5 rounded border border-gray-200 text-gray-500 hover:bg-white disabled:opacity-30 disabled:cursor-not-allowed transition-colors text-sm"
                    onClick={() => setPeriodIndex(i => i - 1)}
                    disabled={periodIndex === 0}
                  >→</button>
                </div>
                <span className="text-xs text-gray-400">{lessons.length} lesson{lessons.length !== 1 ? 's' : ''}</span>
              </div>

              {/* KPI tiles */}
              <div className="grid grid-cols-4 divide-x divide-gray-100">
                <div className="px-5 py-4">
                  <p className="text-xs font-medium text-gray-400 uppercase tracking-wider mb-1">Hours</p>
                  <p className="text-2xl font-semibold text-gray-900">{hours}</p>
                </div>
                <div className="px-5 py-4">
                  <p className="text-xs font-medium text-gray-400 uppercase tracking-wider mb-1">Revenue</p>
                  <p className="text-2xl font-semibold text-gray-900">${revenue.toFixed(2)}</p>
                </div>
                <div className="px-5 py-4">
                  <p className="text-xs font-medium text-gray-400 uppercase tracking-wider mb-1">Payout</p>
                  <p className="text-2xl font-semibold text-gray-900">${payout.toFixed(2)}</p>
                </div>
                <div className="px-5 py-4">
                  <p className="text-xs font-medium text-gray-400 uppercase tracking-wider mb-1">Profit</p>
                  <p className="text-2xl font-semibold text-emerald-600">${(revenue - payout).toFixed(2)}</p>
                </div>
              </div>
            </div>
          )
      })()}

        {/* Table */}
        <div className="overflow-x-auto rounded-lg border border-gray-200 shadow-sm">
          <table className="w-full text-sm">
            <thead>
              {/* Table toolbar */}
              <tr className="bg-gray-50 border-b border-gray-100">
                <th colSpan={6} className="px-4 h-14">
                  <div className="flex items-center gap-3">
                    {/* Left: checkbox/cancel + selection actions */}
                    {selectionMode ? (
                      <button
                        className="w-5 h-5 flex items-center justify-center text-gray-400 hover:text-gray-700 border border-gray-300 hover:border-gray-400 rounded transition-colors"
                        onClick={handleCancelSelection}
                        title="Cancel selection"
                      >✕</button>
                    ) : (
                      <input
                        type="checkbox"
                        className="w-4 h-4 rounded border-gray-300 cursor-pointer"
                        onChange={() => setSelectionMode(true)}
                        checked={false}
                      />
                    )}
                    {selectionMode && (
                      <>
                        <span className="text-xs text-gray-400 min-w-[70px]">{selectedIds.size} selected</span>
                        <button
                          className="flex items-center gap-1 text-sm font-medium text-indigo-600 hover:text-indigo-800 transition-colors"
                          onClick={() => handleSelectAll(lessonsToDisplay)}
                        >
                          <IconSquareCheck size={16} />
                          {lessonsToDisplay.every(l => selectedIds.has(l.id)) ? 'Unselect All' : 'Select All'}
                        </button>
                        <button
                          className=" ml-4 flex items-center justify-center text-red-400 hover:text-red-600 transition-colors"
                          onClick={() => setConfirmingBulkDelete(true)}
                          title="Delete selected"
                        >
                          <IconTrash size={23} />
                        </button>
                      </>
                    )}
                    {/* Right: filters always visible */}
                    <div className="ml-auto flex items-center gap-2">
                      <Select
                        size="xs"
                        placeholder="All Students"
                        data={Object.values(students).map(s => ({ value: s.id.toString(), label: `${s.first_name} ${s.last_name}` }))}
                        onChange={v => setFilterStudent(v ? Number(v) : null)}
                        clearable
                        styles={{ input: { borderColor: '#e5e7eb', height: '40px', borderRadius: '8px', fontFamily: 'inherit', fontWeight: 'normal' } }}
                      />
                      <Select
                        size="xs"
                        placeholder="All Tutors"
                        data={Object.values(tutors).map(t => ({ value: t.id.toString(), label: `${t.first_name} ${t.last_name}` }))}
                        onChange={v => setFilterTutor(v ? Number(v) : null)}
                        clearable
                        styles={{ input: { borderColor: '#e5e7eb', height: '40px', borderRadius: '8px', fontFamily: 'inherit', fontWeight: 'normal' } }}
                      />
                    </div>
                  </div>
                </th>
              </tr>
              {/* Selection summary */}
              {selectionSummary && (
                <tr className="border-b border-indigo-100 bg-indigo-50">
                  <th colSpan={6} className="px-0">
                    <div className="grid grid-cols-4 divide-x divide-indigo-100">
                      <div className="px-5 py-3 text-left">
                        <p className="text-xs font-medium text-indigo-400 uppercase tracking-wider mb-0.5">Total Hours</p>
                        <p className="text-xl font-semibold text-indigo-900">{selectionSummary.totalHours}</p>
                      </div>
                      <div className="px-5 py-3 text-left">
                        <p className="text-xs font-medium text-indigo-400 uppercase tracking-wider mb-0.5">Revenue</p>
                        <p className="text-xl font-semibold text-indigo-900">${selectionSummary.totalRevenue.toFixed(2)}</p>
                      </div>
                      <div className="px-5 py-3 text-left">
                        <p className="text-xs font-medium text-indigo-400 uppercase tracking-wider mb-0.5">Payout</p>
                        <p className="text-xl font-semibold text-indigo-900">${selectionSummary.totalPayout.toFixed(2)}</p>
                      </div>
                      <div className="px-5 py-3 text-left">
                        <p className="text-xs font-medium text-indigo-400 uppercase tracking-wider mb-0.5">Net Profit</p>
                        <p className="text-xl font-semibold text-emerald-600">${(selectionSummary.totalRevenue - selectionSummary.totalPayout).toFixed(2)}</p>
                      </div>
                    </div>
                  </th>
                </tr>
              )}
              {/* Column headers */}
              <tr className="bg-indigo-50 border-b border-indigo-200 text-indigo-700 uppercase text-xs tracking-wide">
                <th className="pl-4 pr-2 py-3 w-8" />
                <th className="px-4 py-3 text-left font-semibold">Student</th>
                <th className="px-4 py-3 text-left font-semibold">Tutor</th>
                <th className="px-4 py-3 text-right font-semibold">Hrs</th>
                <th className="px-4 py-3 text-right font-semibold">Fee</th>
                <th className="px-4 py-3 text-center font-semibold">Paid</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {tableRows}
            </tbody>
          </table>
        </div>
      </>
    </div>
  )
}