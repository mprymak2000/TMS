from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from database import Base, engine
from routers import contacts, tutors, lessons, schedules, booking_links, booking_types, booking_link_availability, available_slots, bookings, invoices, settings  # cancellation_policies unregistered — policy fields moved onto BookingLink directly
import models

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://192.168.1.0/24"],
    allow_origin_regex=r"http://192\.168\.\d+\.\d+:\d+",
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(settings.router)
app.include_router(contacts.router)
app.include_router(tutors.router)
app.include_router(lessons.router)
app.include_router(schedules.router)
app.include_router(booking_link_availability.router)
app.include_router(booking_links.router)
app.include_router(booking_types.router)
app.include_router(available_slots.router)
app.include_router(bookings.router)
app.include_router(invoices.router)
# app.include_router(cancellation_policies.router)

@app.get("/health")
def health():
    return {"status": "ok"}