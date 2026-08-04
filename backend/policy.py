"""Cancel/reschedule policy logic — pure functions, no DB/model/schema dependencies.

Kept dependency-free on purpose: both models.py (computed cancel_action/reschedule_action
properties on Booking/BookingSeries, so the server-computed verdict can be exposed directly
in API responses) and routers/bookings.py (actual enforcement at action time) import from
here. If this file depended on models.py, models.py importing back from it would be circular.
"""


def format_notice(minutes: int) -> str:
    hours = minutes // 60
    if hours <= 48:
        return f"{hours} hours" if minutes % 60 == 0 else f"{minutes} minutes"
    days, rem_hours = divmod(hours, 24)
    return f"{days} days {rem_hours} hours" if rem_hours else f"{days} days"


def get_cancel_action(event_type, minutes_until: float) -> str:
    """Returns 'auto', 'request', or 'blocked' based on event type cancel policy and minutes until booking."""
    mode = event_type.cancel_mode if event_type else None
    if mode is None or mode == 'auto':
        return 'auto'
    if mode == 'not_allowed':
        return 'blocked'
    if mode == 'request':
        return 'request'
    notice = event_type.cancel_notice_minutes or 0
    outside_window = minutes_until >= notice
    if mode == 'auto_window_block':
        return 'auto' if outside_window else 'blocked'
    if mode == 'auto_window_request':
        return 'auto' if outside_window else 'request'
    if mode == 'request_window':
        return 'request' if outside_window else 'blocked'
    return 'blocked'


def get_reschedule_action(event_type, minutes_until: float) -> str:
    """Returns 'auto', 'request', or 'blocked' based on event type reschedule policy and minutes until booking."""
    mode = event_type.reschedule_mode if event_type else None
    if mode is None or mode == 'auto':
        return 'auto'
    if mode == 'not_allowed':
        return 'blocked'
    if mode == 'request':
        return 'request'
    notice = event_type.reschedule_notice_minutes or 0
    outside_window = minutes_until >= notice
    if mode == 'auto_window_block':
        return 'auto' if outside_window else 'blocked'
    if mode == 'auto_window_request':
        return 'auto' if outside_window else 'request'
    if mode == 'request_window':
        return 'request' if outside_window else 'blocked'
    return 'blocked'


def cancel_blocked_detail(event_type) -> str:
    if event_type is None or event_type.cancel_mode == 'not_allowed':
        return "Cancellation is not allowed for this event type"
    return f"Cancellation requires at least {format_notice(event_type.cancel_notice_minutes or 0)} notice"


def reschedule_blocked_detail(event_type) -> str:
    if event_type is None or event_type.reschedule_mode == 'not_allowed':
        return "Rescheduling is not allowed for this event type"
    return f"Rescheduling requires at least {format_notice(event_type.reschedule_notice_minutes or 0)} notice"
