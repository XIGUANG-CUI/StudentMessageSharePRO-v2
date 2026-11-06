# -*- coding: utf-8 -*-
"""保险模块 P3：新生首保（建记录 / 全状态视图 / 审批 / 导出 / 补保单）。"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import models.insurance as ins
from models.insurance import (
    InsuranceStatus,
    InsuranceTransitionError,
    ensure_record,
    first_stage_counts,
    query_first_stage,
)

S = InsuranceStatus


STUDENT_SCHEMA = """
CREATE TABLE students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_card TEXT,
    name TEXT,
    student_no TEXT DEFAULT '',
    name_pinyin TEXT DEFAULT '',
    school TEXT DEFAULT '',
    grade TEXT,
    enrollment_year INTEGER,
    admin_class TEXT DEFAULT '',
    major TEXT DEFAULT '',
    student_phone TEXT DEFAULT '',
    registration_status TEXT DEFAULT '',
    is_deleted INTEGER DEFAULT 0,
    student_insurance_no TEXT DEFAULT '',
    student_insurance_pay_date TEXT DEFAULT '',
    student_insurance_expiry TEXT DEFAULT '',
    insurance_premium TEXT DEFAULT '',
    internship_insurance_pay_date TEXT DEFAULT '',
    supervisor_flag TEXT DEFAULT ''
);
CREATE TABLE schema_version (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    version INTEGER NOT NULL DEFAULT 0,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO schema_version (id, version) VALUES (1, 2);
"""


@pytest.fixture
def conn():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript(STUDENT_SCHEMA)
    ins.migrate_insurance_v3(c)
    c.commit()
    yield c
    c.close()


def _add_student(conn, name='张三', grade='2026', enrollment_year=2026):
    cur = conn.execute(
        '''INSERT INTO students (id_card, name, grade, enrollment_year, school)
           VALUES (?, ?, ?, ?, '1')''',
        (f'ID-{name}', name, grade, enrollment_year)
    )
    conn.commit()
    return cur.lastrowid


def _add_record(conn, student_id, status, batch_no=1, insurance_type='xueping'):
    cur = conn.execute(
        '''INSERT INTO insurance_records
           (student_id, id_card, school_year, batch_no, insurance_type, status)
           VALUES (?, 'ID', '2026-2027', ?, ?, ?)''',
        (student_id, batch_no, insurance_type, status)
    )
    conn.commit()
    return cur.lastrowid


# ============================================================
# ensure_record
# ============================================================
class TestEnsureRecord:
    def test_creates_pending_approve(self, conn):
        sid = _add_student(conn, '新生')
        rec = ensure_record(conn, sid, 1)
        assert rec['status'] == S.PENDING_APPROVE
        assert rec['batch_no'] == 1
        assert rec['insurance_type'] == 'xueping'
        assert rec['school_year'] == '2026-2027'
        assert rec['remark'] == 'first'

    def test_idempotent(self, conn):
        sid = _add_student(conn, '新生')
        first = ensure_record(conn, sid, 1)
        second = ensure_record(conn, sid, 1)
        assert first['id'] == second['id']
        count = conn.execute(
            'SELECT COUNT(*) FROM insurance_records WHERE student_id = ?', (sid,)
        ).fetchone()[0]
        assert count == 1

    def test_missing_student_raises(self, conn):
        with pytest.raises(InsuranceTransitionError):
            ensure_record(conn, 999, 1)

    def test_transfer_school_year(self, conn):
        sid = _add_student(conn, '转学生', grade='2025', enrollment_year=2026)
        rec = ensure_record(conn, sid, 2)
        # start_year = min(2025, 2026) = 2025；第 2 年 → 2026-2027
        assert rec['school_year'] == '2026-2027'


# ============================================================
# query_first_stage / first_stage_counts
# ============================================================
class TestFirstStage:
    def test_includes_all_statuses(self, conn):
        _add_student(conn, '未建档')
        statuses = (S.PENDING_APPROVE, S.APPROVED, S.EXPORTED, S.DONE, S.EXEMPT)
        for index, status in enumerate(statuses):
            sid = _add_student(conn, f'状态{index}')
            _add_record(conn, sid, status)
        items = query_first_stage(conn, current_year=2026)
        assert len(items) == 6
        got = sorted(i['source_status'] or '' for i in items)
        assert got == sorted([''] + list(statuses))

    def test_filter_by_statuses(self, conn):
        a = _add_student(conn, '待审批')
        _add_record(conn, a, S.PENDING_APPROVE)
        b = _add_student(conn, '已导出')
        _add_record(conn, b, S.EXPORTED)
        items = query_first_stage(conn, current_year=2026, statuses=[S.EXPORTED])
        assert [i['id'] for i in items] == [b]

    def test_unbuilt_filter(self, conn):
        a = _add_student(conn, '未建档')
        b = _add_student(conn, '已审批')
        _add_record(conn, b, S.APPROVED)
        items = query_first_stage(conn, current_year=2026, statuses=[''])
        assert [i['id'] for i in items] == [a]

    def test_transfer_in_first_stage(self, conn):
        _add_student(conn, '转学生', grade='2025', enrollment_year=2026)
        items = query_first_stage(conn, current_year=2026)
        assert len(items) == 1
        assert items[0]['kind'] == 'transfer'
        assert items[0]['batch_no'] == 2

    def test_counts(self, conn):
        _add_student(conn, '未建档')
        _add_record(conn, _add_student(conn, '待审批'), S.PENDING_APPROVE)
        _add_record(conn, _add_student(conn, '已导出'), S.EXPORTED)
        _add_record(conn, _add_student(conn, '已完成'), S.DONE)
        counts = first_stage_counts(conn)
        assert counts['unbuilt'] == 1
        assert counts['pending_approve'] == 1
        assert counts['exported'] == 1
        assert counts['done'] == 1
        assert counts['approved'] == 0
        assert counts['exempt'] == 0
        assert counts['total'] == 4


# ============================================================
# 首保页路由（列表 / 办理 / 不办理 / 导出 / 补录保单）
# ============================================================
class TestFirstRoutes:
    CSRF = 'test-csrf-token'

    @staticmethod
    def _login(client):
        with client.session_transaction() as sess:
            sess.clear()
            sess['user_id'] = 1
            sess['username'] = 'tester'
            sess['role'] = 'admin'
            sess['csrf_token'] = TestFirstRoutes.CSRF

    @staticmethod
    def _logout(client):
        with client.session_transaction() as sess:
            sess.clear()

    @staticmethod
    def _post(client, url, payload):
        return client.post(url, json=payload,
                           headers={'X-CSRF-Token': TestFirstRoutes.CSRF})

    @staticmethod
    def _clean(db_conn):
        for table in ('insurance_operation_logs', 'insurance_export_batches',
                      'insurance_records', 'students'):
            db_conn.execute(f'DELETE FROM {table}')
        db_conn.commit()

    @staticmethod
    def _student(db_conn, name='首保生'):
        cur = db_conn.execute(
            "INSERT INTO students (id_card, name, grade, enrollment_year, school, "
            "student_no, admin_class, student_phone) "
            "VALUES (?, ?, '2026', 2026, 1, ?, '1班', '13800000000')",
            (f'FIRST-{name}', name, f'NO-{name}')
        )
        db_conn.commit()
        return cur.lastrowid

    def test_list_approve_export_policy(self, client, db_conn):
        self._clean(db_conn)
        sid = self._student(db_conn, '全流程')
        self._login(client)
        try:
            page = client.get('/insurance/first')
            assert page.status_code == 200
            assert '新生首保' in page.get_data(as_text=True)

            data = client.get('/api/insurance/first/list').get_json()
            assert data['success'] is True
            assert data['total'] == 1
            assert data['items'][0]['id'] == sid
            assert data['items'][0]['source_status'] == ''
            assert data['summary']['unbuilt'] == 1

            resp = self._post(client, '/api/insurance/first/approve',
                              {'student_ids': [sid]})
            assert resp.get_json() == {'success': True, 'count': 1, 'skipped': 0}

            data = client.get('/api/insurance/first/list?status=approved').get_json()
            assert data['total'] == 1
            rid = data['items'][0]['record_id']
            assert data['items'][0]['source_status'] == S.APPROVED

            resp = client.get(f'/api/insurance/first/export?record_ids={rid}')
            assert resp.status_code == 200
            assert resp.mimetype.endswith('spreadsheetml.sheet')

            data = client.get('/api/insurance/first/list?status=exported').get_json()
            assert data['total'] == 1

            resp = self._post(client, '/api/insurance/first/policy', {
                'record_ids': [rid],
                'policy_no': 'P-FIRST-1',
                'expiry_date': '2027-09-01',
            })
            assert resp.get_json() == {'success': True, 'count': 1, 'skipped': 0}

            row = db_conn.execute(
                'SELECT * FROM insurance_records WHERE id = ?', (rid,)).fetchone()
            assert row['status'] == S.DONE
            assert row['policy_no'] == 'P-FIRST-1'
            assert row['expiry_date'] == '2027-09-01'
        finally:
            self._logout(client)
            self._clean(db_conn)

    def test_exempt(self, client, db_conn):
        self._clean(db_conn)
        sid = self._student(db_conn, '不办理')
        self._login(client)
        try:
            resp = self._post(client, '/api/insurance/first/exempt',
                              {'student_ids': [sid]})
            assert resp.get_json() == {'success': True, 'count': 1, 'skipped': 0}

            data = client.get('/api/insurance/first/list?status=exempt').get_json()
            assert data['total'] == 1
            assert data['items'][0]['source_status'] == S.EXEMPT
        finally:
            self._logout(client)
            self._clean(db_conn)

    def test_requires_login(self, client):
        self._logout(client)
        assert client.get('/api/insurance/first/list').status_code == 401
        assert client.get('/api/insurance/first/list?status=approved').status_code == 401


