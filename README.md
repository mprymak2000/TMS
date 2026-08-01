# TMS

A session management system for recurring-session service businesses (tutoring, therapy, coaching, etc.) — practitioners, clients, scheduling, and bookings.

For full architecture, business logic, and conventions, see [`CLAUDE.md`](./CLAUDE.md). This file is just the quick-start.

## Project status

**This project is unfinished — a lot of functionality doesn't work yet, for two different reasons:**

1. **Not built yet.** Several features are stored/scaffolded in the data model but not implemented (see CLAUDE.md's "Known TODOs" for the full list — things like per-day/week/month booking limits, buffer times, a slug field, email notifications, etc.).
2. **Google Calendar coupling is still being worked out.** Bookings are tightly integrated with a real Google Calendar service account — creating, rescheduling, and cancelling a booking all require one to actually function (see Limitations below). That coupling hasn't been relaxed yet, so **the booking/scheduling side of this app is currently intended to be view-only** — browse students, tutors, lessons, and existing (seeded) bookings, but don't expect to create/reschedule/cancel real bookings without setting up real Google credentials first.

Treat this as a project under active development, not a finished product.

## Prerequisites

- [Poetry](https://python-poetry.org/) (backend dependency management)
- Node.js + npm (frontend)
- A Docker runtime — [Colima](https://github.com/abiosoft/colima) (macOS), Docker Desktop, or Docker Desktop's WSL2 backend (Windows)

## Setup

```bash
git clone <this repo>
cd TMS

# Backend
cd backend
cp .env.example .env   # then fill in your real GOOGLE_SERVICE_ACCOUNT_JSON — see Limitations below
poetry install

# Frontend
cd ../frontend
npm install
```

## Starting everything

From the repo root, `start.py` brings up the database, backend, and frontend together:

```bash
python3 start.py             # all three inline, in one terminal
python3 start.py --separate  # each in its own terminal window
```

It handles starting Colima/your Docker runtime if it's not already running, and stops the database container on exit (inline mode only). See the comments in `start.py` for details, or `CLAUDE.md`'s "Running the Backend/Frontend" sections to run each piece manually instead.

Once running: backend docs at `http://localhost:8000/docs`, frontend at `http://localhost:5173`.

## Seeding the database

```bash
cd backend
poetry run python initialize_database.example.py
```

This wipes and repopulates the local dev database with fake demo data: 2 students, 1 tutor, 2 event types (one recurring, one standalone), ~3 months of historical lessons, a handful of standalone bookings (including one that intentionally fully books a tutor's day, to demo the "no free slots" case), and one 8-occurrence recurring booking series.

If you have real data of your own, copy this file to `initialize_database.py` (already gitignored) and replace the fake `students`/`tutors` lists with real ones — that filename is what CLAUDE.md and this repo's conventions assume for your actual local seed script.

## Limitations

- **Bookings require a real Google Calendar service account.** `POST /bookings/` (and reschedule/cancel/approve) creates a calendar event before writing the DB row — `Booking.google_event_id` is non-nullable by design (see CLAUDE.md). Without a real `GOOGLE_SERVICE_ACCOUNT_JSON` in `backend/.env`, those specific write endpoints fail; reads, contact edits, and denying a request are unaffected. See CLAUDE.md's "Google Calendar Integration" section for real setup.
- **The seed script's demo bookings bypass this entirely** — they're inserted directly into the database with fake `google_event_id` values, so they show up in the UI, but reschedule/cancel on them will fail for the same reason above. This is expected in a demo environment without real credentials, not a bug.
- **`GET /available-slots`** degrades gracefully without calendar credentials (falls back to DB-only busy-block checking), so it works out of the box even in a fresh clone.

---
Last updated 2026-07-31.
