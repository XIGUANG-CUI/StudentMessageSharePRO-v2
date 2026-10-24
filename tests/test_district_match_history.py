# -*- coding: utf-8 -*-
"""历史映射维护（/admin/district-match 页面）测试。

覆盖：CRUD 权限、成对校验、写后缓存失效、软删除、批量导入 upsert。
"""
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CSRF_TOKEN = 'history_test_csrf'
HEADERS = {'X-CSRF-Token': CSRF_TOKEN}


@pytest.fixture(autouse=True)
def _clear_session(client):
    """用例结束后清空 session，避免登录态污染其他测试。"""
    yield
    with client.session_transaction() as sess:
        sess.clear()


@pytest.fixture
def history_isolation(db_conn):
    """只清理本用例新增的映射，保留内置种子。"""
    from models import district as dm

    max_id = db_conn.execute(
        "SELECT COALESCE(MAX(id), 0) FROM dm_history_mapping").fetchone()[0]
    yield
    db_conn.execute("DELETE FROM dm_history_mapping WHERE id > ?", (max_id,))
    db_conn.commit()
    dm.invalidate_cache()


def _login(client, role='admin'):
    with client.session_transaction() as sess:
        sess.clear()
        sess['user_id'] = 1
        sess['username'] = 'history_tester'
        sess['role'] = role
        sess['csrf_token'] = CSRF_TOKEN


def _alias_map(app):
    from models import district as dm
    with app.app_context():
        return dm.DistrictRepository().get_history_alias_map()


def test_page_renders_history_controls(client):
    _login(client)
    resp = client.get('/admin/district-match')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'dm-history-add-btn' in html
    assert 'data-history-import-url' in html
    assert '/api/district-match/history/0/update' in html
    assert '历史映射维护' in html


def test_history_create_update_delete(client, app, history_isolation):
    _login(client)

    total_seed = len(client.get('/api/district-match/history').get_json()['data']['items'])

    resp = client.post('/api/district-match/history', headers=HEADERS, json={
        'old_code': '999999', 'new_code': '230104',
        'old_name': '测试旧区', 'new_name': '道外区',
        'change_type': '更名', 'change_date': '2020-01-01', 'remark': '用例',
    })
    body = resp.get_json()
    assert resp.status_code == 200 and body['success'] is True
    history_id = body['data']['id']
    assert history_id

    # 写后缓存立刻失效：别名/代码映射均生效
    assert _alias_map(app).get('测试旧区') == '道外区'

    items = client.get('/api/district-match/history?search=999999').get_json()['data']['items']
    assert len(items) == 1 and items[0]['old_name'] == '测试旧区'

    resp = client.post('/api/district-match/history/%d/update' % history_id,
                       headers=HEADERS, json={
                           'old_code': '999999', 'new_code': '230104',
                           'old_name': '测试旧区', 'new_name': '道外新区',
                       })
    assert resp.status_code == 200 and resp.get_json()['success'] is True
    assert _alias_map(app).get('测试旧区') == '道外新区'

    resp = client.post('/api/district-match/history/%d/delete' % history_id,
                       headers=HEADERS)
    assert resp.status_code == 200 and resp.get_json()['success'] is True
    assert client.get('/api/district-match/history?search=999999').get_json()['data']['items'] == []
    assert '测试旧区' not in _alias_map(app)

    # 软删除：行仍在表里（is_deleted=1），不参与列表与匹配
    from models import district as dm
    with app.app_context():
        assert dm.DistrictRepository().get_history(history_id)['is_deleted'] == 1
    assert len(client.get('/api/district-match/history').get_json()['data']['items']) == total_seed


def test_history_rejects_unpaired_fields(client, history_isolation):
    _login(client)
    resp = client.post('/api/district-match/history', headers=HEADERS,
                       json={'old_code': '888888'})
    assert resp.status_code == 400
    assert '成对' in resp.get_json()['message']


def test_history_write_requires_admin(client, history_isolation):
    _login(client, role='user')
    resp = client.post('/api/district-match/history', headers=HEADERS,
                       json={'old_code': '777777', 'new_code': '230104'})
    assert resp.status_code == 403
    assert resp.get_json()['success'] is False
    # 读接口对登录用户开放
    assert client.get('/api/district-match/history').status_code == 200


def test_history_import_upsert(client, app, history_isolation):
    _login(client)
    csv_text = (
        "旧码,新码,旧名,新名,类型,日期,备注\n"
        "888801,230104,导入旧区A,道外区,更名,2020-01-01,备注A\n"
        "888802,230105,导入旧区B,道外区,撤并,,\n"
        ",,导入旧名C,新名C,更名,,\n"
        "999999,,,,,,\n"
    )

    def _upload():
        return client.post(
            '/api/district-match/history/import',
            headers=HEADERS,
            data={'file': (io.BytesIO(csv_text.encode('utf-8')), 'history.csv')},
            content_type='multipart/form-data')

    first = _upload()
    assert first.status_code == 200, first.get_data(as_text=True)
    data = first.get_json()['data']
    assert data['created'] == 3 and data['updated'] == 0 and data['skipped'] == 1
    assert data['errors'] and '成对' in data['errors'][0]['message']

    alias = _alias_map(app)
    assert alias.get('导入旧区A') == '道外区'
    assert alias.get('导入旧名C') == '新名C'
    from models import district as dm
    with app.app_context():
        code_map = dm.DistrictRepository().get_history_code_map()
    assert code_map.get('888801') == '230104'

    second = _upload()
    data = second.get_json()['data']
    assert data['created'] == 0 and data['updated'] == 3 and data['skipped'] == 1
