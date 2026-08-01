import { Fragment } from 'react'
import type { Lesson, Student, Tutor, LessonEdit } from './types'
import type { LessonEditErrors } from './useLessons'
import { tutorBubbleClass, tutorInitials } from './utils'

interface Props {
  lesson: Lesson
  student: Student | undefined
  tutor: Tutor | undefined
  isExpanded: boolean
  isEditing: boolean
  editedLesson: LessonEdit | null
  editErrors: LessonEditErrors
  editSubmitError: string | null
  confirmingDelete: boolean
  deleteError: string | null
  selectionMode: boolean
  isSelected: boolean
  dayIndex: number
  onRowClick: () => void
  onEdit: () => void
  onCancelEdit: () => void
  onEditFieldChange: (field: keyof LessonEdit, value: string | boolean) => void
  onEditSave: () => void
  onConfirmDelete: () => void
  onCancelDelete: () => void
  onDelete: () => void
}

export default function LessonRow({
  lesson, student, tutor,
  isExpanded, isEditing, editedLesson, editErrors, editSubmitError,
  confirmingDelete, deleteError,
  selectionMode, isSelected, dayIndex,
  onRowClick, onEdit, onCancelEdit, onEditFieldChange, onEditSave,
  onConfirmDelete, onCancelDelete, onDelete,
}: Props) {
  return (
    <Fragment>
      <tr
        className={`cursor-pointer transition-colors ${isExpanded ? (confirmingDelete ? 'bg-red-50' : 'bg-indigo-50/50') : isSelected ? 'bg-violet-50' : 'hover:bg-gray-50'}`}
        onClick={onRowClick}
      >
        <td className={`pl-4 pr-2 py-3 w-8 relative before:absolute before:left-0 before:top-0 before:bottom-0 before:w-[5px] ${dayIndex % 2 === 0 ? 'before:bg-amber-300' : 'before:bg-indigo-300'}`}>
          <input
            type="checkbox"
            checked={isSelected}
            onChange={onRowClick}
            onClick={e => e.stopPropagation()}
            className={`h-4 w-4 rounded border-gray-300 accent-indigo-600 cursor-pointer ${selectionMode ? 'visible' : 'invisible'}`}
          />
        </td>
        <td className="px-4 py-3 font-medium text-gray-900">{student?.first_name} {student?.last_name}</td>
        <td className="px-4 py-3">
          {tutor && (
            <span className={`inline-flex items-center justify-center w-5 h-5 rounded-full text-[13px] font-black overflow-hidden pt-[0.5px] uppercase ${tutorBubbleClass(tutor)}`}>
              {tutorInitials(tutor)}
            </span>
          )}
        </td>
        <td className="px-4 py-3 text-right text-gray-700">{lesson.hrs ?? '—'}</td>
        <td className="px-4 py-3 text-right font-medium text-gray-900">
          ${lesson.fee.toFixed(2)}
          {lesson.is_fee_overridden && <span className="ml-1 text-xs text-orange-500">✎</span>}
        </td>
        <td className="px-4 py-3 text-center">
          {lesson.pay_status
            ? <span className="text-emerald-600 font-bold">✓</span>
            : <span className="text-red-600">✗</span>}
        </td>
      </tr>

      {isExpanded && (
        <tr className={confirmingDelete ? 'bg-red-50' : 'bg-indigo-50/50'}>
          <td colSpan={7} className="pl-12 pr-6">
            {confirmingDelete ? (
              <div className="flex flex-col gap-1 border-t-2 border-red-400 py-3">
                <div className="flex items-center justify-end gap-4">
                  <span className="pr-6 text-base font-medium text-red-700">Delete this lesson?</span>
                  <div className="flex gap-1.5">
                    <button
                      className="text-sm px-3 py-1 rounded border border-gray-300 text-gray-600 hover:bg-white transition-colors"
                      onClick={onCancelDelete}
                    >Cancel</button>
                    <button
                      className="text-sm px-3 py-1 rounded bg-red-600 text-white hover:bg-red-700 transition-colors"
                      onClick={onDelete}
                    >Confirm Delete</button>
                  </div>
                </div>
                {deleteError && <p className="text-xs text-red-500 text-right">{deleteError}</p>}
              </div>
            ) : isEditing && editedLesson ? (
              <div className="border-t border-indigo-100 py-3 flex flex-col gap-3">
                <div className="flex gap-3 flex-wrap items-end">
                  <div>
                    <span className="text-xs text-gray-400 uppercase tracking-wider block mb-1">Date</span>
                    <input
                      type="date"
                      value={editedLesson.date}
                      onChange={e => onEditFieldChange('date', e.target.value)}
                      className={`border rounded px-2 py-1 text-sm focus:outline-none focus:ring-1 ${editErrors.date ? 'border-red-400 focus:border-red-500 focus:ring-red-500' : 'border-gray-300 focus:border-indigo-500 focus:ring-indigo-500'}`}
                    />
                  </div>
                  <div>
                    <span className="text-xs text-gray-400 uppercase tracking-wider block mb-1">Hours</span>
                    <input
                      type="number"
                      min={0}
                      step={0.5}
                      value={editedLesson.hrs}
                      onChange={e => onEditFieldChange('hrs', e.target.value)}
                      placeholder="e.g. 1.5"
                      className={`border rounded px-2 py-1 text-sm w-24 focus:outline-none focus:ring-1 ${editErrors.hrs ? 'border-red-400 focus:border-red-500 focus:ring-red-500' : 'border-gray-300 focus:border-indigo-500 focus:ring-indigo-500'}`}
                    />
                  </div>
                  <div>
                    <span className="text-xs text-gray-400 uppercase tracking-wider block mb-1">Fee Override</span>
                    <input
                      type="number"
                      min={0}
                      step={10}
                      value={editedLesson.feeOverride}
                      onChange={e => onEditFieldChange('feeOverride', e.target.value)}
                      placeholder="$"
                      className={`border rounded px-2 py-1 text-sm w-24 focus:outline-none focus:ring-1 ${editErrors.feeOverride ? 'border-red-400 focus:border-red-500 focus:ring-red-500' : 'border-gray-300 focus:border-indigo-500 focus:ring-indigo-500'}`}
                    />
                  </div>
                  <div>
                    <span className="text-xs text-gray-400 uppercase tracking-wider block mb-1">Tutor Pay Override</span>
                    <input
                      type="number"
                      min={0}
                      step={10}
                      value={editedLesson.tutorPayOverride}
                      onChange={e => onEditFieldChange('tutorPayOverride', e.target.value)}
                      placeholder="$"
                      className={`border rounded px-2 py-1 text-sm w-24 focus:outline-none focus:ring-1 ${editErrors.tutorPayOverride ? 'border-red-400 focus:border-red-500 focus:ring-red-500' : 'border-gray-300 focus:border-indigo-500 focus:ring-indigo-500'}`}
                    />
                  </div>
                  <div>
                    <span className="text-xs text-gray-400 uppercase tracking-wider block mb-1">Notes</span>
                    <input
                      type="text"
                      value={editedLesson.notes ?? ''}
                      onChange={e => onEditFieldChange('notes', e.target.value)}
                      placeholder="Notes..."
                      className="border border-gray-300 rounded px-2 py-1 text-sm w-48 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
                    />
                  </div>
                  <div>
                    <span className="text-xs text-gray-400 uppercase tracking-wider block mb-1">Paid</span>
                    <div className="py-1">
                      <input
                        type="checkbox"
                        checked={editedLesson.payStatus}
                        onChange={e => onEditFieldChange('payStatus', e.target.checked)}
                        className="h-4 w-4 rounded border-gray-300 accent-indigo-600"
                      />
                    </div>
                  </div>
                </div>
                <div className="flex items-center justify-between">
                  <p className="text-xs text-red-500 h-4">
                    {editSubmitError ?? Object.values(editErrors).find(e => e !== undefined)}
                  </p>
                  <div className="flex gap-1.5">
                    <button
                      className="text-sm px-3 py-1 rounded border border-gray-300 text-gray-600 hover:bg-white transition-colors"
                      onClick={onCancelEdit}
                    >Cancel</button>
                    <button
                      className="text-sm px-3 py-1 rounded bg-indigo-600 text-white hover:bg-indigo-700 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                      onClick={onEditSave}
                      disabled={Object.values(editErrors).some(e => e !== undefined)}
                    >Save</button>
                  </div>
                </div>
              </div>
            ) : (
              <div className="flex items-center gap-16 text-sm text-gray-600 border-t border-indigo-100 py-3">
                <div className="flex-1">
                  <span className="text-xs text-gray-400 uppercase tracking-wider">Tutor Payout</span>
                  <p className="font-medium text-gray-800">
                    ${lesson.tutor_payout.toFixed(2)}
                    {lesson.is_tutor_payout_overridden && <span className="ml-1 text-xs text-orange-400">✎</span>}
                  </p>
                </div>
                <div className="flex-1">
                  <span className="text-xs text-gray-400 uppercase tracking-wider">Notes</span>
                  <p className="text-gray-600">{lesson.notes ?? <span className="text-gray-300">—</span>}</p>
                </div>
                <div className="flex gap-1.5 items-center">
                  <button
                    className="text-sm px-3 py-1 rounded border border-indigo-200 text-indigo-600 hover:bg-white hover:border-indigo-400 transition-colors"
                    onClick={onEdit}
                  >Edit</button>
                  <button
                    className="text-sm px-3 py-1 rounded border border-red-300 text-red-700 hover:bg-red-50 hover:border-red-400 transition-colors"
                    onClick={onConfirmDelete}
                  >Delete</button>
                </div>
              </div>
            )}
          </td>
        </tr>
      )}
    </Fragment>
  )
}
