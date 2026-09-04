"""Cancel/reschedule policy logic — pure functions, no DB/model/schema dependencies.

Kept dependency-free on purpose: both models.py (computed cancel_action/reschedule_action
properties on Booking/BookingSeries, so the server-computed verdict can be exposed directly
in API responses) and routers/bookings.py (actual enforcement at action time) import from
here. If this file depended on models.py, models.py importing back from it would be circular.
"""


def get_cancel_action(booking_link, minutes_until: float) -> str:
    """Returns 'auto', 'request', or 'blocked' based on the booking link's cancel policy and minutes until booking."""
    if minutes_until <= 0:
        return 'blocked'
    mode = booking_link.cancel_mode if booking_link else None
    if mode is None or mode == 'auto':
        return 'auto'
    if mode == 'not_allowed':
        return 'blocked'
    if mode == 'request':
        return 'request'
    notice = booking_link.cancel_notice_minutes or 0
    outside_window = minutes_until >= notice
    if mode == 'auto_window_block':
        return 'auto' if outside_window else 'blocked'
    if mode == 'auto_window_request':
        return 'auto' if outside_window else 'request'
    if mode == 'request_window':
        return 'request' if outside_window else 'blocked'
    return 'blocked'


def get_reschedule_action(booking_link, minutes_until: float) -> str:
    """Returns 'auto', 'request', or 'blocked' based on the booking link's reschedule policy and minutes until booking."""
    if minutes_until <= 0:
        return 'blocked'
    mode = booking_link.reschedule_mode if booking_link else None
    if mode is None or mode == 'auto':
        return 'auto'
    if mode == 'not_allowed':
        return 'blocked'
    if mode == 'request':
        return 'request'
    notice = booking_link.reschedule_notice_minutes or 0
    outside_window = minutes_until >= notice
    if mode == 'auto_window_block':
        return 'auto' if outside_window else 'blocked'
    if mode == 'auto_window_request':
        return 'auto' if outside_window else 'request'
    if mode == 'request_window':
        return 'request' if outside_window else 'blocked'
    return 'blocked'
