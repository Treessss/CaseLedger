from app.utils.api_performance import _before_sql, _after_sql


def test_api_timing_covers_authenticated_and_rejected_requests(client, admin_headers):
    response = client.get('/api/permissions', headers=admin_headers)
    assert response.status_code == 200
    assert 'app;dur=' in response.headers['Server-Timing']
    assert 'db;dur=' in response.headers['Server-Timing']
    first_id = response.headers['X-Request-ID']
    assert len(first_id) == 32
    rejected = client.get('/api/permissions')
    assert rejected.status_code == 401
    assert rejected.headers['X-Request-ID'] != first_id
    assert 'Server-Timing' not in rejected.headers


def test_sql_hooks_ignore_work_outside_requests():
    class Context:
        pass
    context = Context()
    _before_sql(None, None, None, None, context, False)
    _after_sql(None, None, None, None, context, False)
    assert not hasattr(context, '_api_perf_started')
