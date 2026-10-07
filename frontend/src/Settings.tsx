import { useState, useEffect } from 'react'
import { Button, Select, Switch, Loader } from '@mantine/core'
import type { Settings as SettingsType } from './types'
import { extractError } from './utils'
import Toast from './Toast'
import { useToast } from './useToast'

const API = import.meta.env.VITE_API_URL

// The zones a single-location practice plausibly runs in. Settings.business_timezone is one global
// value; a multi-location business isn't representable yet (see CLAUDE.md).
const TIMEZONES = [
    'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles',
    'America/Phoenix', 'America/Anchorage', 'Pacific/Honolulu',
    'Europe/London', 'Europe/Warsaw', 'Europe/Berlin', 'UTC',
]

const Row = ({ label, description, children }: {
    label: string
    description?: string
    children: React.ReactNode
}) => (
    <div className="flex items-start justify-between gap-6 px-5 py-4 border-b border-gray-50 last:border-0">
        <div>
            <p className="text-sm font-medium text-gray-700">{label}</p>
            {description && <p className="text-xs text-gray-400 mt-0.5 max-w-md">{description}</p>}
        </div>
        <div className="shrink-0">{children}</div>
    </div>
)

const Settings = () => {
    const [settings, setSettings] = useState<SettingsType | null>(null)
    const [timezone, setTimezone] = useState<string | null>(null)
    const [automation, setAutomation] = useState(false)
    const [loadError, setLoadError] = useState<string | null>(null)
    const [saving, setSaving] = useState(false)
    const { toast, showToast } = useToast()

    useEffect(() => {
        (async () => {
            try {
                const res = await fetch(`${API}/settings/`)
                if (!res.ok) throw new Error(extractError(await res.json(), 'Failed to load settings'))
                const data: SettingsType = await res.json()
                setSettings(data)
                setTimezone(data.business_timezone)
                setAutomation(data.billing_automation_enabled)
            } catch (e) {
                setLoadError(e instanceof Error ? e.message : 'Failed to load settings')
            }
        })()
    }, [])

    // The zone is locked while a series anchors to it, so it can't count toward dirty either —
    // otherwise Save would light up on a field the user can't actually change.
    const dirty = settings !== null && (
        (!settings.timezone_locked && timezone !== settings.business_timezone) ||
        automation !== settings.billing_automation_enabled
    )

    const save = async () => {
        setSaving(true)
        try {
            // Full replacement, like the other PUTs here: send both fields, not just the changed one.
            const res = await fetch(`${API}/settings/`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ business_timezone: timezone, billing_automation_enabled: automation }),
            })
            if (!res.ok) throw new Error(extractError(await res.json(), 'Failed to save'))
            setSettings(await res.json())
            showToast('Settings saved.')
        } catch (e) {
            showToast(e instanceof Error ? e.message : 'Failed to save', 'error')
        } finally {
            setSaving(false)
        }
    }

    if (loadError) return <div className="p-6 text-sm text-red-500">{loadError}</div>
    if (!settings) return <div className="p-6 flex justify-center"><Loader size="sm" /></div>

    return (
        <div className="p-6 max-w-2xl">
            <div className="mb-5">
                <h1 className="text-xl font-semibold text-gray-800">Settings</h1>
                <p className="text-sm text-gray-400 mt-0.5">Business-wide, one set of values.</p>
            </div>

            <div className="bg-white rounded-lg border border-gray-100 mb-4">
                <Row
                    label="Business timezone"
                    description={settings.timezone_locked
                        ? "Locked: recurring series store their times as wall-clock in this zone, so changing it would move sessions that are already booked. Cancel them first."
                        : "Every schedule and availability window is read in this zone. Set it before taking recurring bookings — it can't be changed afterwards."}
                >
                    {/* The stored zone has to be in the list, or Mantine renders the field blank
                        and saving would silently change it. */}
                    <Select
                        size="sm" className="w-56"
                        data={TIMEZONES.includes(settings.business_timezone)
                            ? TIMEZONES
                            : [settings.business_timezone, ...TIMEZONES]}
                        value={timezone} onChange={setTimezone} searchable
                        disabled={settings.timezone_locked}
                    />
                </Row>
                <Row
                    label="Draft invoices automatically"
                    description="On the 1st of each month, drafts last month's invoices for everyone with activity. Nothing is ever sent — finalizing stays a deliberate step."
                >
                    <Switch
                        checked={automation}
                        onChange={e => setAutomation(e.currentTarget.checked)}
                    />
                </Row>
            </div>

            <div className="flex justify-end">
                <Button onClick={save} loading={saving} disabled={!dirty}>Save</Button>
            </div>
            <Toast toast={toast} />
        </div>
    )
}

export default Settings
