"""HTTP adapter: run WORLDLINE's checker, explorer, minimizer and genome against a real ASGI service.

See docs/REAL_SERVICE.md. ``HttpWorld`` drives the service with truth events delivered under a
FaultSchedule and records an ``HttpTrace`` (a ``worldline.worlds.Trace``) from the service's own
receive log and decision endpoint.
"""
from .driver import HttpTrace, HttpWorld, ServiceUnderTest, demo_service, project_scenario
from .recorder import DEFAULT_ROUTES, record_to_event, to_request, view_from_log
from .search import challenge_http, explore_http, failure_oracle_http, minimize_http, verdicts_http

__all__ = [
    "HttpTrace", "HttpWorld", "ServiceUnderTest", "demo_service", "project_scenario",
    "DEFAULT_ROUTES", "record_to_event", "to_request", "view_from_log",
    "challenge_http", "explore_http", "failure_oracle_http", "minimize_http", "verdicts_http",
]
