import pytest


@pytest.mark.asyncio
async def test_create_and_list_owned_reports(client):
    response = client.post('/reports/', json={'name': 'Test Report', 'type': 'INVENTORY_VALUATION'})
    assert response.status_code == 200
    report = response.json()['report']
    assert report['tenant_id'] == 'tenant-1'
    assert report['created_by'] == client.get('/auth/me').json()['id']
    assert client.get('/reports/').json()['reports'][0]['id'] == report['id']


@pytest.mark.asyncio
async def test_execute_and_schedule_require_owned_report_and_real_jobs(client):
    assert client.post('/reports/missing/execute', json={'format': 'json'}).status_code == 404
    report = client.post('/reports/', json={'name': 'Report'}).json()['report']
    execution = client.post('/reports/' + report['id'] + '/execute', json={'format': 'json'})
    assert execution.status_code == 202
    assert execution.json()['execution']['status'] == 'QUEUED'
    assert client.post('/reports/' + report['id'] + '/schedule', json={'cronExpression': '0 0 * * *'}).status_code == 200


@pytest.mark.asyncio
async def test_unverified_share_tokens_do_not_create_file_urls(client):
    for token in ('valid_token', 'expired', 'invalid'):
        response = client.get('/reports/shared/' + token)
        assert response.status_code == 404
        assert 'fileUrl' not in response.json()


@pytest.mark.asyncio
async def test_report_requires_authentication(client):
    assert client.get('/reports/', headers={'Authorization': ''}).status_code == 401
