import { useState } from 'react'
import { SegmentedControl, Select, NumberInput } from '@mantine/core'

// The six stored modes are really two outcomes split by a time threshold. Three of them have the
// same outcome on both sides (no threshold), which is what the first three radios are; the fourth
// branch is where the two sides differ.
//
//   auto                 allowed        / allowed
//   request              needs approval / needs approval
//   blocked              not allowed    / not allowed
//   auto_window_block    allowed        / not allowed
//   auto_window_request  allowed        / needs approval
//   request_window       needs approval / not allowed
//
// Composing from the two sides means the invalid pairs are simply unreachable, rather than caught
// by a validator after the fact.

// The modes that carry a notice window. Mirrors _WINDOW_MODES in schemas.py.
export const WINDOW_MODES = ['auto_window_block', 'auto_window_request', 'request_window']

type Choice = 'always' | 'always_request' | 'never' | 'depends'

const BEFORE_OPTIONS = [
    { value: 'auto', label: 'Allow' },
    { value: 'request', label: 'By request' },
]

// "By request" on both sides is just `request`, so once the far side already asks for approval the
// only thing the near side can add is a hard stop.
const afterOptions = (before: string) =>
    before === 'request'
        ? [{ value: 'blocked', label: "Don't allow" }]
        : [
            { value: 'request', label: 'By request' },
            { value: 'blocked', label: "Don't allow" },
        ]

const NOTICE_UNITS = [
    { value: '1', label: 'minutes' },
    { value: '60', label: 'hours' },
    { value: '1440', label: 'days' },
]

// Show 1440 as "1 day" rather than "1440 minutes" — largest unit that divides evenly.
const splitNotice = (minutes: number | null): [number, string] => {
    if (!minutes) return [24, '60']
    for (const u of [1440, 60]) if (minutes % u === 0) return [minutes / u, String(u)]
    return [minutes, '1']
}

// `simple` fields have no window modes, so anything unrecognised there is a missing value rather
// than a custom window — fall back to the default instead of selecting nothing.
const modeToChoice = (mode: string, simple: boolean): Choice =>
    mode === 'auto' ? 'always'
    : mode === 'request' ? 'always_request'
    : mode === 'blocked' ? 'never'
    : simple || !WINDOW_MODES.includes(mode) ? 'always'
    : 'depends'

const splitMode = (mode: string): [string, string] => {
    if (mode === 'auto_window_block') return ['auto', 'blocked']
    if (mode === 'auto_window_request') return ['auto', 'request']
    if (mode === 'request_window') return ['request', 'blocked']
    return ['auto', 'blocked']
}

const joinMode = (before: string, after: string) =>
    before === 'request' ? 'request_window'
    : after === 'request' ? 'auto_window_request'
    : 'auto_window_block'

interface Props {
    label: string
    mode: string
    noticeMinutes: number | null
    onChange: (mode: string, noticeMinutes: number | null) => void
    // Series-level actions have no notice window, so they get the three plain answers only.
    simple?: boolean
}

const PolicyModeField = ({ label, mode, noticeMinutes, onChange, simple = false }: Props) => {
    const choice = modeToChoice(mode, simple)
    const [before, after] = splitMode(mode)
    const [amount, unit] = splitNotice(noticeMinutes)

    // Remembered so toggling away from "depends" and back doesn't wipe what was configured.
    const [lastWindow, setLastWindow] = useState({ before, after, amount, unit })

    const emitWindow = (next: Partial<typeof lastWindow>) => {
        const w = { ...lastWindow, before, after, amount, unit, ...next }
        // Switching the far side to "needs approval" leaves only one legal near side.
        if (w.before === 'request') w.after = 'blocked'
        setLastWindow(w)
        onChange(joinMode(w.before, w.after), w.amount * Number(w.unit))
    }

    const pick = (val: string) => {
        if (val === 'always') onChange('auto', null)
        else if (val === 'always_request') onChange('request', null)
        else if (val === 'never') onChange('blocked', null)
        else onChange(joinMode(lastWindow.before, lastWindow.after), lastWindow.amount * Number(lastWindow.unit))
    }

    const choices = [
        { value: 'always', label: 'Allow' },
        { value: 'always_request', label: 'Allow by request' },
        { value: 'never', label: "Don't allow" },
        ...(simple ? [] : [{ value: 'depends', label: 'Custom' }]),
    ]

    return (
        <div>
            <p className="text-sm font-medium text-gray-700 mb-2">{label}</p>
            <SegmentedControl
                fullWidth size="sm" radius="md"
                data={choices}
                value={choice}
                onChange={pick}
            />
            {!simple && choice === 'depends' && (
                <div className="flex items-center gap-2 mt-3 text-xs text-gray-500">
                    <Select
                        size="xs" className="w-32"
                        data={BEFORE_OPTIONS}
                        value={before}
                        onChange={val => val && emitWindow({ before: val })}
                    />
                    <span className="shrink-0">until</span>
                    <NumberInput
                        size="xs" className="w-14" min={1} hideControls
                        value={amount}
                        onChange={val => emitWindow({ amount: Number(val) || 1 })}
                    />
                    <Select
                        size="xs" className="w-24"
                        data={NOTICE_UNITS}
                        value={unit}
                        onChange={val => val && emitWindow({ unit: val })}
                    />
                    <span className="shrink-0">before, then</span>
                    <Select
                        size="xs" className="w-32"
                        data={afterOptions(before)}
                        value={after}
                        onChange={val => val && emitWindow({ after: val })}
                    />
                </div>
            )}
        </div>
    )
}

export default PolicyModeField
