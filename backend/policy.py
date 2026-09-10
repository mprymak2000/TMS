"""Cancel/reschedule policy logic — pure functions.

Two callers: routers/bookings.py enforces on the way in, schemas.py computes the field on the way out.
"""
from datetime import UTC, datetime


def minutes_until(start: datetime) -> float:
    """Defensive against naive datetimes — SQLite doesn't preserve tz-awareness."""
    start_tz = start if start.tzinfo else start.replace(tzinfo=UTC)
    return (start_tz - datetime.now(UTC)).total_seconds() / 60


def get_cancel_action(policy, minutes_until: float) -> str:
    """Verdict for cancelling. `policy` is anything carrying cancel_mode/cancel_notice_minutes —
    a Booking (frozen at creation) or a BookingLink (the settings being configured)."""
    if minutes_until <= 0:
        return 'blocked'
    mode = policy.cancel_mode if policy else None
    if mode is None or mode == 'auto':
        return 'auto'
    if mode == 'blocked':
        return 'blocked'
    if mode == 'request':
        return 'request'
    notice = policy.cancel_notice_minutes or 0
    outside_window = minutes_until >= notice
    if mode == 'auto_window_block':
        return 'auto' if outside_window else 'blocked'
    if mode == 'auto_window_request':
        return 'auto' if outside_window else 'request'
    if mode == 'request_window':
        return 'request' if outside_window else 'blocked'
    return 'blocked'


def get_reschedule_action(policy, minutes_until: float) -> str:
    """Verdict for rescheduling. Same shape as get_cancel_action above."""
    if minutes_until <= 0:
        return 'blocked'
    mode = policy.reschedule_mode if policy else None
    if mode is None or mode == 'auto':
        return 'auto'
    if mode == 'blocked':
        return 'blocked'
    if mode == 'request':
        return 'request'
    notice = policy.reschedule_notice_minutes or 0
    outside_window = minutes_until >= notice
    if mode == 'auto_window_block':
        return 'auto' if outside_window else 'blocked'
    if mode == 'auto_window_request':
        return 'auto' if outside_window else 'request'
    if mode == 'request_window':
        return 'request' if outside_window else 'blocked'
    return 'blocked'
