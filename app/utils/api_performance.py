"""Bounded per-request timing; never log payloads, tokens or customer identifiers."""
from time import perf_counter
from uuid import uuid4

from flask import g, has_request_context, request
from sqlalchemy import event


def _before_sql(conn, cursor, statement, parameters, context, executemany):
    if has_request_context() and g.get('_api_perf') is not None:
        context._api_perf_started = perf_counter()


def _after_sql(conn, cursor, statement, parameters, context, executemany):
    started = getattr(context, '_api_perf_started', None)
    state = g.get('_api_perf') if has_request_context() else None
    if started is not None and state is not None:
        state['queries'] += 1
        state['sql_ms'] += (perf_counter() - started) * 1000


def init_api_performance(app, engine):
    if not event.contains(engine, 'before_cursor_execute', _before_sql):
        event.listen(engine, 'before_cursor_execute', _before_sql)
        event.listen(engine, 'after_cursor_execute', _after_sql)

    @app.before_request
    def start_api_timing():
        if request.path.startswith('/api/'):
            g._api_perf = {'started': perf_counter(), 'id': uuid4().hex, 'queries': 0, 'sql_ms': 0.0}

    @app.after_request
    def finish_api_timing(response):
        state = g.pop('_api_perf', None)
        if state is None:
            return response
        elapsed = (perf_counter() - state['started']) * 1000
        # Do not expose precise authentication/SQL timing to unauthenticated callers.
        if state.get('authenticated'):
            response.headers['Server-Timing'] = f"app;dur={elapsed:.1f}, db;dur={state['sql_ms']:.1f}"
        response.headers['X-Request-ID'] = state['id']
        if elapsed >= app.config.get('API_SLOW_REQUEST_MS', 1000):
            app.logger.warning('slow_api request_id=%s method=%s endpoint=%s status=%s ms=%.1f queries=%s sql_ms=%.1f',
                               state['id'], request.method, request.endpoint or 'unmatched', response.status_code,
                               elapsed, state['queries'], state['sql_ms'])
        return response
