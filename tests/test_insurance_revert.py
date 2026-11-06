# -*- coding: utf-8 -*-
"""保险模块 P6：记录查询页 + 撤销中心。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import models.insurance as ins
from models.insurance import (
    InsuranceStatus,
    export_records,
    list_export_batches,
    list_operation_logs,
    query_insurance_records,
    revert_export,
)

S = InsuranceStatus


def _clean(db_conn):
    for table in ('insurance_operation_logs', 'insurance_export_batches',
                  'insurance_records', 'students'):
        db_conn.execute(f'DELETE FROM {table}')
    db_conn.commit()


def _student(db_conn, name='学生', grade='2026', enrollment_year=2026):
    cur = db_conn.execute(
        "INSERT INTO students (id_card, name, grade, enrollment_year, school, "
        "student_no, admin_class, student_phone) "
        "VALUES (?, ?, ?, ?, 1, ?, '1班', '13800000000')",
        (f'REV-{name}', name, grade, enrollment_year, f'NO-{name}')
    )
    db_conn.commit()
    return cur.lastrowid


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


# ============================================================
# 领域层
# ============================================================
class TestQueryRecords:
    def test_type_and_status_filter(self, db_conn):
        _clean(db_conn)
        sid = _student(db_conn, '筛选')
        _record(db_conn, sid, S.PENDING_APPROVE, batch_no=1)
        _record(db_conn, sid, S.PENDING_PAY, batch_no=3, insurance_type='intern')
        try:
            assert len(query_insurance_records(db_conn)) == 2
            intern = query_insurance_records(
                db_conn, insurance_type='intern')
            assert len(intern) == 1
            assert intern[0]['insurance_type'] == 'intern'
            assert intern[0]['source_status'] == S.PENDING_PAY

            approved = query_insurance_records(
                db_conn, status=S.APPROVED)
            assert approved == []
        finally:
            _clean(db_conn)

    def test_search_filter(self, db_conn):
        _clean(db_conn)
        a = _student(db_conn, '甲一')
        b = _student(db_conn, '乙二')
        _record(db_conn, a, S.DONE)
        _record(db_conn, b, S.DONE)
        try:
            items = query_insurance_records(db_conn, search='甲一')
            assert [i['id'] for i in items] == [a]
        finally:
            _clean(db_conn)


class TestExportBatches:
    def test_revert_export(self, db_conn):
        _clean(db_conn)
        sid = _student(db_conn, '撤销')
        rid = _record(db_conn, sid, S.APPROVED)
        try:
            batch_id = export_records(db_conn, [rid], 'first', operator='tester')
            db_conn.commit()

            batches = list_export_batches(db_conn)
            assert len(batches) == 1
            assert batches[0]['id'] == batch_id
            assert batches[0]['reversed'] is False
            assert batches[0]['action_label'] == '新生首保'
            assert batches[0]['insurance_type_label'] == '学平险'

            count = revert_export(db_conn, batch_id, operator='tester')
            db_conn.commit()
            assert count == 1

            rec = db_conn.execute(
                'SELECT * FROM insurance_records WHERE id = ?', (rid,)
            ).fetchone()
            assert rec['status'] == S.APPROVED
            assert rec['export_batch_id'] == ''
            assert list_export_batches(db_conn)[0]['reversed'] is True

            with pytest.raises(ins.InsuranceTransitionError):
                revert_export(db_conn, batch_id)
        finally:
            _clean(db_conn)

    def test_operation_logs(self, db_conn):
        _clean(db_conn)
        sid = _student(db_conn, '日志')
        rid = _record(db_conn, sid, S.APPROVED)
        try:
            batch_id = export_records(db_conn, [rid], 'first', operator='tester')
            revert_export(db_conn, batch_id, operator='tester')
            db_conn.commit()

            all_logs = list_operation_logs(db_conn)
            actions = [l['action'] for l in all_logs]
            assert 'export' in actions and 'revert' in actions

            reverted = list_operation_logs(db_conn, action='revert')
            assert len(reverted) == 1
            assert reverted[0]['student_name'] == '日志'

            assert list_operation_logs(db_conn, search='日志')[0]['student_id'] == sid
            assert list_operation_logs(db_conn, search='不存在') == []
        finally:
            _clean(db_conn)


# ============================================================
# 按学生撤销
# ============================================================
class TestRevertStudent:
    def test_deletes_last_record_and_restores_snapshot(self, db_conn):
        _clean(db_conn)
        sid = _student(db_conn, '按生撤销')
        rid1 = _record(db_conn, sid, S.DONE, batch_no=1)
        db_conn.execute(
            "UPDATE insurance_records SET policy_no='P1', expiry_date='2026-11-20', "
            "pay_date='2025-09-01' WHERE id=?", (rid1,))
        rid2 = _record(db_conn, sid, S.EXPORTED, batch_no=2)
        db_conn.execute(
            "UPDATE insurance_records SET policy_no='P2', expiry_date='2027-11-20', "
            "pay_date='2026-11-01' WHERE id=?", (rid2,))
        db_conn.commit()
        ins.sync_student_snapshot(db_conn, sid)
        db_conn.commit()
        try:
            rec = ins.revert_student(db_conn, sid, operator='tester')
            db_conn.commit()
            assert rec['batch_no'] == 2

            left = db_conn.execute(
                'SELECT * FROM insurance_records WHERE student_id = ?', (sid,)
            ).fetchall()
            assert [r['batch_no'] for r in left] == [1]

            # 学生旧字段回退到上一批（第1年）的保单/缴费
            s = db_conn.execute(
                'SELECT student_insurance_no, student_insurance_expiry, '
                'student_insurance_pay_date FROM students WHERE id = ?', (sid,)
            ).fetchone()
            assert s['student_insurance_no'] == 'P1'
            assert s['student_insurance_expiry'] == '2026-11-20'
            assert s['student_insurance_pay_date'] == '2025-09-01'

            logs = list_operation_logs(db_conn, action='revert')
            assert len(logs) == 1
            assert logs[0]['to_status'] == 'deleted'
        finally:
            _clean(db_conn)

    def test_detaches_from_export_batch(self, db_conn):
        _clean(db_conn)
        sid = _student(db_conn, '批次撤销')
        rid = _record(db_conn, sid, S.APPROVED, batch_no=2)
        try:
            batch_id = export_records(db_conn, [rid], 'renewal', operator='tester')
            db_conn.commit()

            ins.revert_student(db_conn, sid, operator='tester')
            db_conn.commit()

            assert db_conn.execute(
                'SELECT COUNT(*) FROM insurance_records WHERE student_id = ?', (sid,)
            ).fetchone()[0] == 0
            batch = db_conn.execute(
                'SELECT * FROM insurance_export_batches WHERE id = ?', (batch_id,)
            ).fetchone()
            assert batch['student_count'] == 0
            assert batch['record_ids'] == '[]'
            assert batch['reversed_at']
        finally:
            _clean(db_conn)

    def test_no_record_raises(self, db_conn):
        _clean(db_conn)
        sid = _student(db_conn, '无记录')
        try:
            with pytest.raises(ins.InsuranceTransitionError):
                ins.revert_student(db_conn, sid)
        finally:
            _clean(db_conn)


# ============================================================
# 路由
# ============================================================
class TestRevertRoutes:
    CSRF = 'test-csrf-token'

    @staticmethod
    def _login(client, role='admin'):
        with client.session_transaction() as sess:
            sess.clear()
            sess['user_id'] = 1
            sess['username'] = 'tester'
            sess['role'] = role
            sess['csrf_token'] = TestRevertRoutes.CSRF

    @staticmethod
    def _logout(client):
        with client.session_transaction() as sess:
            sess.clear()

    @staticmethod
    def _post(client, url, payload):
        return client.post(url, json=payload,
                           headers={'X-CSRF-Token': TestRevertRoutes.CSRF})

    def test_pages_render(self, client):
        self._login(client)
        try:
            resp = client.get('/insurance/records')
            assert resp.status_code == 200
            assert '保险记录查询' in resp.get_data(as_text=True)
            resp = client.get('/insurance/revert')
            assert resp.status_code == 200
            assert '撤销中心' in resp.get_data(as_text=True)
        finally:
            self._logout(client)

    def test_batches_and_revert(self, client, db_conn):
        _clean(db_conn)
        sid = _student(db_conn, '路由撤销')
        rid = _record(db_conn, sid, S.APPROVED)
        batch_id = export_records(db_conn, [rid], 'first', operator='tester')
        db_conn.commit()
        self._login(client)
        try:
            data = client.get('/api/insurance/revert/batches').get_json()
            assert data['success'] is True
            assert data['total'] == 1
            assert data['items'][0]['reversed'] is False

            resp = self._post(client, '/api/insurance/revert/batch',
                              {'export_batch_id': batch_id})
            data = resp.get_json()
            assert data['success'] is True
            assert data['count'] == 1

            resp = self._post(client, '/api/insurance/revert/batch',
                              {'export_batch_id': batch_id})
            assert resp.status_code == 400
            assert resp.get_json()['success'] is False

            data = client.get('/api/insurance/revert/logs?action=revert').get_json()
            assert data['total'] == 1
        finally:
            self._logout(client)
            _clean(db_conn)

    def test_records_list(self, client, db_conn):
        _clean(db_conn)
        sid = _student(db_conn, '记录查询')
        _record(db_conn, sid, S.DONE, batch_no=1)
        _record(db_conn, sid, S.PAID, batch_no=3, insurance_type='intern')
        self._login(client)
        try:
            data = client.get('/api/insurance/records/list').get_json()
            assert data['success'] is True
            assert data['total'] == 2

            data = client.get(
                '/api/insurance/records/list?insurance_type=intern').get_json()
            assert data['total'] == 1
            assert data['items'][0]['batch_no'] == 3

            data = client.get(
                '/api/insurance/records/list?search=记录查询').get_json()
            assert data['total'] == 2
        finally:
            self._logout(client)
            _clean(db_conn)

    def test_revert_student_routes(self, client, db_conn):
        _clean(db_conn)
        sid = _student(db_conn, '路由按生')
        _record(db_conn, sid, S.DONE, batch_no=1)
        _record(db_conn, sid, S.EXPORTED, batch_no=2)
        self._login(client)
        try:
            data = client.get('/api/insurance/revert/students?search=路由按生').get_json()
            assert data['success'] is True
            assert data['total'] == 1
            assert data['items'][0]['student_id'] == sid
            assert data['items'][0]['batch_no'] == 2

            resp = self._post(client, '/api/insurance/revert/student',
                              {'student_id': sid})
            data = resp.get_json()
            assert data['success'] is True
            assert data['record']['batch_no'] == 2

            left = db_conn.execute(
                'SELECT COUNT(*) FROM insurance_records WHERE student_id = ?', (sid,)
            ).fetchone()[0]
            assert left == 1

            resp = self._post(client, '/api/insurance/revert/student',
                              {'student_id': 99999})
            assert resp.status_code == 400
            assert resp.get_json()['success'] is False
        finally:
            self._logout(client)
            _clean(db_conn)

    def test_requires_login(self, client):
        self._logout(client)
        assert client.get('/api/insurance/revert/batches').status_code == 401
        assert client.get('/api/insurance/records/list').status_code == 401

    def test_requires_admin(self, client):
        self._login(client, role='insurance_officer')
        try:
            assert client.get('/api/insurance/revert/batches').status_code == 403
            data = client.get('/api/insurance/records/list').get_json()
            assert data['success'] is True
        finally:
            self._logout(client)
