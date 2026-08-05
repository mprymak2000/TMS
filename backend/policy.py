"""Cancel/reschedule policy logic — pure functions, no DB/model/schema dependencies.

Kept dependency-free on purpose: both models.py (computed cancel_action/reschedule_action
properties on Booking/BookingSeries, so the server-computed verdict can be exposed directly
in API responses) and routers/bookings.py (actual enforcement at action time) import from
here. If this file depended on models.py, models.py importing back from it would be circular.
"""


def get_cancel_action(event_type, minutes_until: float) -> str:
    """Returns 'auto', 'request', or 'blocked' based on event type cancel policy and minutes until booking."""
    if minutes_until <= 0:
        return 'blocked'
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
    if minutes_until <= 0:
        return 'blocked'
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
