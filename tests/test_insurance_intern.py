# -*- coding: utf-8 -*-
"""保险模块 P5：独立推送页 + 实习就业险办结页。"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import models.insurance as ins
from models.insurance import (
    InsuranceStatus,
    intern_stage_counts,
    query_intern_stage,
    query_push_pool,
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
        "INSERT INTO students (id_card, name, grade, enrollment_year, school) "
        "VALUES (?, ?, ?, ?, '1')",
        (f'ID-{name}', name, grade, enrollment_year)
    )
    conn.commit()
    return cur.lastrowid


def _add_record(conn, student_id, status, batch_no=1,
                insurance_type='xueping', pay_date=''):
    cur = conn.execute(
        '''INSERT INTO insurance_records
           (student_id, id_card, school_year, batch_no, insurance_type, status, pay_date)
           VALUES (?, 'ID', '2026-2027', ?, ?, ?, ?)''',
        (student_id, batch_no, insurance_type, status, pay_date)
    )
    conn.commit()
    return cur.lastrowid


# ============================================================
# 领域层
# ============================================================
class TestInternStage:
    def test_includes_intern_only(self, conn):
        sid = _add_student(conn, '实习')
        _add_record(conn, sid, S.PENDING_APPROVE, batch_no=3, insurance_type='intern')
        _add_record(conn, sid, S.DONE, batch_no=1)
        items = query_intern_stage(conn)
        assert len(items) == 1
        assert items[0]['insurance_type'] == 'intern'
        assert items[0]['source_status'] == S.PENDING_APPROVE

    def test_status_filter(self, conn):
        a = _add_student(conn, '已缴')
        _add_record(conn, a, S.PAID, batch_no=3, insurance_type='intern')
        b = _add_student(conn, '已办')
        _add_record(conn, b, S.DONE, batch_no=3, insurance_type='intern')
        items = query_intern_stage(conn, statuses=(S.PAID,))
        assert [i['id'] for i in items] == [a]

    def test_counts(self, conn):
        for index, status in enumerate((S.PENDING_APPROVE, S.PAID, S.APPROVED, S.DONE)):
            sid = _add_student(conn, f'状态{index}')
            _add_record(conn, sid, status, batch_no=3, insurance_type='intern')
        counts = intern_stage_counts(conn)
        assert counts['pending_approve'] == 1
        assert counts['paid'] == 1
        assert counts['approved'] == 1
        assert counts['done'] == 1
        assert counts['total'] == 4


# ============================================================
# 路由
# ============================================================
class TestPushInternRoutes:
    CSRF = 'test-csrf-token'

    @staticmethod
    def _login(client, role='insurance_officer'):
        with client.session_transaction() as sess:
            sess.clear()
            sess['user_id'] = 1
            sess['username'] = 'tester'
            sess['role'] = role
            sess['csrf_token'] = TestPushInternRoutes.CSRF

    @staticmethod
    def _logout(client):
        with client.session_transaction() as sess:
            sess.clear()

    @staticmethod
    def _post(client, url, payload):
        return client.post(url, json=payload,
                           headers={'X-CSRF-Token': TestPushInternRoutes.CSRF})

    @staticmethod
    def _clean(db_conn):
        for table in ('insurance_operation_logs', 'insurance_export_batches',
                      'insurance_records', 'students'):
            db_conn.execute(f'DELETE FROM {table}')
        db_conn.commit()

    @staticmethod
    def _student(db_conn, name='学生', grade='2026', enrollment_year=2026):
        cur = db_conn.execute(
            "INSERT INTO students (id_card, name, grade, enrollment_year, school, "
            "student_no, admin_class, student_phone) "
            "VALUES (?, ?, ?, ?, 1, ?, '1班', '13800000000')",
            (f'ROUTE-{name}', name, grade, enrollment_year, f'NO-{name}')
        )
        db_conn.commit()
        return cur.lastrowid

    @staticmethod
    def _record(db_conn, student_id, status, batch_no=1,
                insurance_type='xueping'):
        cur = db_conn.execute(
            "INSERT INTO insurance_records (student_id, id_card, school_year, "
            "batch_no, insurance_type, status) "
            "VALUES (?, 'X', '2026-2027', ?, ?, ?)",
            (student_id, batch_no, insurance_type, status)
        )
        db_conn.commit()
        return cur.lastrowid

    def test_pages_render(self, client):
        self._login(client)
        try:
            resp = client.get('/insurance/push')
            assert resp.status_code == 200
            assert '实习推送' in resp.get_data(as_text=True)
            resp = client.get('/insurance/intern')
            assert resp.status_code == 200
            assert '实习就业险办结' in resp.get_data(as_text=True)
            # 财务主管同样可进入实习推送页
            self._login(client, 'finance_chief')
            assert client.get('/insurance/push').status_code == 200
        finally:
            self._logout(client)

    def test_push_list_and_start(self, client, db_conn):
        self._clean(db_conn)
        sid = self._student(db_conn, '可推送')
        self._record(db_conn, sid, S.DONE, batch_no=1)
        self._record(db_conn, sid, S.DONE, batch_no=2)
        self._login(client)
        try:
            resp = client.get('/api/insurance/push/list')
            data = resp.get_json()
            assert data['success'] is True
            assert data['total'] == 1
            assert data['items'][0]['id'] == sid

            resp = self._post(client, '/api/insurance/push/start',
                              {'student_ids': [sid]})
            data = resp.get_json()
            assert data['success'] is True
            assert data['count'] == 1

            rec = db_conn.execute(
                "SELECT * FROM insurance_records WHERE student_id = ? "
                "AND insurance_type = 'intern'", (sid,)
            ).fetchone()
            assert rec is not None
            assert rec['status'] == S.PENDING_APPROVE
            assert rec['batch_no'] == 3

            resp = client.get('/api/insurance/push/list')
            assert resp.get_json()['total'] == 0
        finally:
            self._logout(client)
            self._clean(db_conn)

    def test_push_to_renewal(self, client, db_conn):
        self._clean(db_conn)
        sid = self._student(db_conn, '推迟注册', grade='2027', enrollment_year=2026)
        for batch in (1, 2, 3):
            self._record(db_conn, sid, S.DONE, batch_no=batch)
        self._login(client)
        try:
            resp = self._post(client, '/api/insurance/push/renewal',
                              {'student_ids': [sid]})
            data = resp.get_json()
            assert data['success'] is True
            assert data['count'] == 1
            assert data['skipped'] == 0

            rec = db_conn.execute(
                "SELECT * FROM insurance_records WHERE student_id = ? "
                "AND batch_no = 4 AND insurance_type = 'xueping'", (sid,)
            ).fetchone()
            assert rec is not None
            assert rec['status'] == S.PENDING_RENEWAL
        finally:
            self._logout(client)
            self._clean(db_conn)

    def test_intern_list_approve_export(self, client, db_conn):
        self._clean(db_conn)
        sid = self._student(db_conn, '实习生')
        rid = self._record(db_conn, sid, S.PAID, batch_no=3,
                           insurance_type='intern')
        self._login(client)
        try:
            resp = client.get('/api/insurance/intern/list?status=pending')
            data = resp.get_json()
            assert data['success'] is True
            assert data['total'] == 1
            assert data['items'][0]['source_status'] == S.PAID
            assert data['summary']['paid'] == 1

            # 审批仅财务主管可操作
            assert self._post(client, '/api/insurance/intern/approve',
                              {'record_ids': [rid]}).status_code == 403
            self._login(client, 'finance_chief')
            resp = self._post(client, '/api/insurance/intern/approve',
                              {'record_ids': [rid]})
            data = resp.get_json()
            assert data['success'] is True
            assert data['count'] == 1
            assert db_conn.execute(
                'SELECT status FROM insurance_records WHERE id = ?', (rid,)
            ).fetchone()['status'] == S.APPROVED

            # 导出办结仅保险员可操作
            self._login(client)
            resp = client.get(f'/api/insurance/intern/export?record_ids={rid}')
            assert resp.status_code == 200
            assert 'spreadsheetml' in resp.mimetype
            assert len(resp.data) > 0

            rec = db_conn.execute(
                'SELECT * FROM insurance_records WHERE id = ?', (rid,)
            ).fetchone()
            assert rec['status'] == S.DONE
            assert rec['export_batch_id']

            resp = client.get('/api/insurance/intern/list?status=done')
            assert resp.get_json()['total'] == 1
        finally:
            self._logout(client)
            self._clean(db_conn)

    def test_export_rejects_unapproved(self, client, db_conn):
        self._clean(db_conn)
        sid = self._student(db_conn, '待审批')
        rid = self._record(db_conn, sid, S.PENDING_APPROVE, batch_no=3,
                           insurance_type='intern')
        self._login(client)
        try:
            resp = client.get(f'/api/insurance/intern/export?record_ids={rid}')
            assert resp.status_code == 400
            assert resp.get_json()['success'] is False
        finally:
            self._logout(client)
            self._clean(db_conn)

    def test_requires_login(self, client):
        self._logout(client)
        resp = client.get('/api/insurance/push/list')
        assert resp.status_code == 401
        assert resp.get_json()['success'] is False
