import { useState, useEffect } from 'react'
import { Outlet, NavLink } from 'react-router-dom'
import { IconRepeat } from '@tabler/icons-react'
import type { Tutor, BookingLink, BookingType } from './types'
import { extractError } from './utils'
import { useToast } from './useToast'
import Toast from './Toast'

// SHAPE OF THE OUTLET CONTEXT — what every tab reads via useOutletContext().
export interface BookingsOutletContext {
    tutors: Tutor[]
    bookingLinks: BookingLink[]
    bookingTypes: BookingType[]
    reloadBookingTypes: () => void   // the picker is the CRUD, so rows can add/rename/delete types
    isLoadingRoster: boolean
    showToast: (msg: string, type?: 'success' | 'error') => void
}

// TAB BAR DATA
const TABS = [
    { to: '.', label: 'Schedule' },
    { to: 'recurring', label: 'Recurring' },
    { to: 'requests', label: 'Requests' },
] as const

const BookingsLayout = () => {
    // STATE
    const [tutors, setTutors] = useState<Tutor[]>([])
    const [bookingLinks, setLinks] = useState<BookingLink[]>([])
    const [bookingTypes, setBookingTypes] = useState<BookingType[]>([])
    const [isLoadingRoster, setIsLoadingRoster] = useState(false)
    const [rosterError, setRosterError] = useState<string | null>(null)
    const { toast, showToast } = useToast()

    // DATA FETCH (roster, once on mount)
    useEffect(() => {
        const loadRoster = async () => {
            setIsLoadingRoster(true)
            try {
                const [tutorResponse, bookingLinkResponse, bookingTypeResponse] = await Promise.all([
                    fetch(`${import.meta.env.VITE_API_URL}/tutors`),
                    // include_archived — bookings keep pointing at their link forever, so the roster
                    // has to resolve retired ones or every row that came from one renders blank.
                    fetch(`${import.meta.env.VITE_API_URL}/booking_links/?include_archived=true`),
                    fetch(`${import.meta.env.VITE_API_URL}/booking_types/`),
                ])
                if (!tutorResponse.ok) { setRosterError(extractError(await tutorResponse.json(), 'Failed to load tutors.')); return }
                if (!bookingLinkResponse.ok) { setRosterError(extractError(await bookingLinkResponse.json(), 'Failed to load booking links.')); return }
                setTutors(await tutorResponse.json())
                setLinks(await bookingLinkResponse.json())
                // Non-fatal: rows fall back to showing no type rather than the list failing to render.
                if (bookingTypeResponse.ok) setBookingTypes(await bookingTypeResponse.json())
            } catch (error) {
                console.error(error)
                setRosterError('An unknown error occurred while loading tutors/booking links.')
            } finally {
                setIsLoadingRoster(false)
            }
        }
        loadRoster()
    }, [])

    // Types are editable from the picker wherever it appears, so any tab can invalidate this roster.
    const reloadBookingTypes = async () => {
        try {
            const res = await fetch(`${import.meta.env.VITE_API_URL}/booking_types/`)
            if (!res.ok) {
                showToast(extractError(await res.json(), 'Failed to reload booking types'), 'error')
                return
            }
            setBookingTypes(await res.json())
        } catch (error) {
            console.error(error)
            showToast('An unknown error occurred while reloading booking types', 'error')
        }
    }

    // regardless of path (/booking/*) the below is rendered. In the Outlet section, the correct subpath gets rendered
    // Context (BookingOutletContext, which is the loaded data on mount with useEffect) is passed to the Outlet so that 
    // the tabs can read it via useOutletContext().
    return (
        // OUTER SHELL
        <div className="h-full flex flex-col">
            <div className="shrink-0">
                {/* ERROR PLACE */}
                {rosterError && <p className="text-sm text-red-500 mb-2">{rosterError}</p>}

                {/* TAB BAR */}
                <div className="flex gap-1 bg-gray-100 rounded-lg p-1 w-fit mb-5">
                    {TABS.map(tab => (
                        <NavLink
                            key={tab.to}
                            to={tab.to}
                            end={tab.to === '.'}
                            className={({ isActive }) =>
                                `group flex items-center gap-1 px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
                                    isActive ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-700'
                                }`
                            }
                        >
                            {tab.label}
                            {tab.label === 'Recurring' && <IconRepeat size={13} className="transition-transform duration-500 group-hover:rotate-180" />}
                        </NavLink>
                    ))}
                </div>
            </div>
            {/* OUTLET — whichever tab matched the URL renders here */}
            <div className="flex-1 min-h-0">
                <Outlet context={{ tutors, bookingLinks, bookingTypes, reloadBookingTypes, isLoadingRoster, showToast } satisfies BookingsOutletContext} />
            </div>
            {/* TOAST */}
            <Toast toast={toast} />
        </div>
    )
}

export default BookingsLayout
