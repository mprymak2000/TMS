import { MantineProvider, createTheme } from '@mantine/core'
import { Routes, Route, NavLink, useLocation } from 'react-router-dom'
import LessonsTable from './LessonsTable'
import Availability from './Availability'
import EventTypes from './EventTypes'
import EventTypePage from './EventTypePage'
import BookingPage from './BookingPage'
import Bookings from './Bookings'
import Tutors from './Tutors'
import ManageOccurrence from './ManageOccurrence'
import ManageSeries from './ManageSeries'
import { useState } from 'react'

const theme = createTheme({
  primaryColor: 'indigo',
  fontFamily: 'Inter, sans-serif',
})

const navItems = [
  { to: '/', label: 'Lessons' },
  { to: '/students', label: 'Students' },
  { to: '/tutors', label: 'Tutors' },
  { to: '/availability', label: 'Availability' },
  { to: '/event-types', label: 'Event Types' },
  { to: '/bookings', label: 'Bookings' },
]

function App() {

  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const location = useLocation()
  const isBookings = location.pathname === '/bookings'

  return (
    <MantineProvider theme={theme}>
      <Routes>
        <Route path="/book/:eventTypeId" element={<BookingPage />} />
        <Route path="/manage-occurrence/:ref" element={<ManageOccurrence />} />
        <Route path="/manage-series/:ref" element={<ManageSeries />} />
        <Route path="/my-bookings" element={<Bookings isCustomer={true} />} />
        <Route path="/*" element={
      <div className="flex h-screen bg-gray-800 p-3 gap-3">

        {/* Sidebar */}
        {/* line below this one does logic and makes w=0 for sidebar */}
        <aside className={`shrink-0 flex flex-col overflow-hidden transition-all duration-300 ease-in-out ${sidebarCollapsed ? 'w-0' : 'w-52'}`}>
          <div className="px-5 py-5 text-lg font-bold text-white tracking-tight whitespace-nowrap">TMS</div>
          <nav className="flex flex-col gap-0.5 px-3">
            {navItems.map(item => (
              <NavLink
                key={item.to}
                to={item.to}
                end
                className={({ isActive }) =>
                  `px-3 py-2 rounded text-sm transition-colors ${isActive ? 'bg-indigo-600 text-white font-medium' : 'text-gray-400 hover:bg-gray-800 hover:text-white'}`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
        </aside>

        {/* Main area — a rounded card floating on the dark shell, instead of meeting the
            sidebar/header at a hard right angle. */}
        <div className="flex-1 flex flex-col overflow-hidden bg-white rounded-2xl shadow-xl">

          {/* Header */}
          <header className="h-14 shrink-0 border-b border-gray-200 flex items-center justify-between px-5">
            {/* Hamburger — wire onClick to toggle sidebar */}
            <button
              className="text-gray-500 hover:text-gray-800 transition-colors p-1 rounded hover:bg-gray-100"
              onClick={() => setSidebarCollapsed(prev => !prev)}
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h16" />
              </svg>
            </button>

            {/* Right side — wire name/avatar to real user data when auth is added */}
            <div className="flex items-center gap-3">
              {/* Settings */}
              <button className="text-gray-400 hover:text-gray-600 transition-colors p-1 rounded hover:bg-gray-100">
                <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                  <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                </svg>
              </button>
              {/* User — hardcoded for now, replace with auth user */}
              <div className="flex items-center gap-2 pl-2 border-l border-gray-200">
                <div className="w-7 h-7 rounded-full bg-indigo-600 flex items-center justify-center text-white text-xs font-bold">M</div>
                <span className="text-sm font-medium text-gray-700">Michal</span>
              </div>
            </div>
          </header>

          <main className={`flex-1 overflow-y-auto ${isBookings ? 'py-4 px-6' : 'py-8 px-16'}`}>
            <div className={`h-full ${isBookings ? '' : 'max-w-7xl mx-auto'}`}>
              <Routes>
                <Route path="/" element={<LessonsTable />} />
                <Route path="/students" element={<div className="text-gray-400">Students — coming soon</div>} />
                <Route path="/tutors" element={<Tutors />} />
                <Route path="/availability" element={<Availability />} />
                <Route path="/event-types" element={<EventTypes />} />
                <Route path="/event-types/:id" element={<EventTypePage />} />
                <Route path="/bookings" element={<Bookings />} />
              </Routes>
            </div>
          </main>
        </div>

      </div>
        } />
      </Routes>
    </MantineProvider>
  )
}

export default App
