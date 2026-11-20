# -*- coding: utf-8 -*-
"""保险模块：提前缴费（预缴）——目标定位、写入、候选、撤销与流程衔接。"""
import os
import sqlite3
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import models.insurance as ins
from models.insurance import (
    InsuranceStatus,
    InsuranceTransitionError,
    cancel_prepay,
    prepay,
    prepay_targets,
    query_collection_stage,
    query_prepay_candidates,
    query_renewal_pool,
    start_renewal,
)

S = InsuranceStatus
TODAY = date(2026, 11, 20)


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
    attendance_status TEXT DEFAULT '',
    is_deleted INTEGER DEFAULT 0,
    student_insurance_no TEXT DEFAULT '',
    student_insurance_pay_date TEXT DEFAULT '',
    student_insurance_expiry TEXT DEFAULT '',
    insurance_premium TEXT DEFAULT '',
    internship_insurance_pay_date TEXT DEFAULT '',
    supervisor_flag TEXT DEFAULT '',
    admission_type TEXT DEFAULT ''
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


def _add_student(conn, name='张三', grade='2026', enrollment_year=2026,
                 admin_class='1班', attendance_status='在校'):
    cur = conn.execute(
        "INSERT INTO students (id_card, name, student_no, grade, enrollment_year, "
        "school, admin_class, attendance_status) "
        "VALUES (?, ?, ?, ?, ?, '1', ?, ?)",
        (f'ID-{name}', name, f'NO-{name}', grade, enrollment_year,
         admin_class, attendance_status)
    )
    conn.commit()
    return cur.lastrowid


def _add_record(conn, student_id, status, batch_no=1, insurance_type='xueping',
                expiry_date='', pay_date='', remark=''):
    cur = conn.execute(
        '''INSERT INTO insurance_records
           (student_id, id_card, school_year, batch_no, insurance_type, status,
            expiry_date, pay_date, remark)
           VALUES (?, 'ID', '2026-2027', ?, ?, ?, ?, ?, ?)''',
        (student_id, batch_no, insurance_type, status, expiry_date, pay_date, remark)
    )
    conn.commit()
    return cur.lastrowid


def _student_row(conn, student_id):
    return conn.execute('SELECT * FROM students WHERE id = ?', (student_id,)).fetchone()


def _student_dict(conn, student_id):
    row = _student_row(conn, student_id)
    return dict(row) if row else None


def _record_for(conn, student_id, batch_no, insurance_type='xueping'):
    return conn.execute(
        'SELECT * FROM insurance_records WHERE student_id = ? AND batch_no = ? '
        'AND insurance_type = ?', (student_id, batch_no, insurance_type)).fetchone()


# ============================================================
# 目标定位
# ============================================================
class TestPrepayTargets:
    def test_xueping_next_renewal_after_terminal_batch(self, conn):
        sid = _add_student(conn, '续保生', grade='2026', enrollment_year=2026)
        _add_record(conn, sid, S.DONE, batch_no=1, expiry_date='2026-11-20')
        targets = prepay_targets(_student_dict(conn, sid),
                                 ins.get_student_records(conn, sid), 2026)
        assert [(t['batch_no'], t['insurance_type'], t['sleep']) for t in targets] == \
            [(2, 'xueping', False)]

    def test_intern_allowed_one_year_early(self, conn):
        sid = _add_student(conn, '次年实习', grade='2025', enrollment_year=2025)
        _add_record(conn, sid, S.DONE, batch_no=1, expiry_date='2026-11-20')
        # 第 2 学年：允许预缴第 3 年实习就业险，同时学平险第 2 年也可预缴
        targets = prepay_targets(_student_dict(conn, sid),
                                 ins.get_student_records(conn, sid), 2026)
        pairs = {(t['batch_no'], t['insurance_type']) for t in targets}
        assert pairs == {(2, 'xueping'), (3, 'intern')}

    def test_first_batch_is_not_prepay_target(self, conn):
        sid = _add_student(conn, '新生')
        assert prepay_targets(_student_dict(conn, sid),
                              ins.get_student_records(conn, sid), 2026) == []

    def test_no_xueping_history_has_no_intern_target(self, conn):
        """无任何学平险记录的学生不应直接预缴实习险（先走首保/睡眠激活）。"""
        sid = _add_student(conn, '无记录', grade='2025', enrollment_year=2025)
        assert prepay_targets(_student_dict(conn, sid),
                              ins.get_student_records(conn, sid), 2026) == []

    def test_dropout_student_excluded(self, conn):
        sid = _add_student(conn, '退学生', attendance_status='退学办结')
        _add_record(conn, sid, S.DONE, batch_no=1, expiry_date='2026-11-20')
        assert prepay_targets(_student_dict(conn, sid),
                              ins.get_student_records(conn, sid), 2026) == []


# ============================================================
# 学平险预缴
# ============================================================
class TestXuepingPrepay:
    def test_creates_pending_renewal_without_snapshot_pollution(self, conn):
        sid = _add_student(conn, '预缴生')
        _add_record(conn, sid, S.DONE, batch_no=1, expiry_date='2026-11-20')

        result = prepay(conn, sid, 2, 'xueping', '2026-11-10', '100',
                        operator='staff', current_year=2026, today=TODAY)
        assert result['created'] is True
        assert result['sleep'] is False
        rec = result['record']
        assert rec['status'] == S.PENDING_RENEWAL
        assert rec['pay_date'] == '2026-11-10'
        assert rec['premium'] == '100'
        assert rec['remark'] == ins.PREPAY_REMARK

        # 待续保的预缴日期不投影到学生快照
        assert _student_row(conn, sid)['student_insurance_pay_date'] == ''

        # 出现在催缴「已缴费」页签，并带预缴标记
        paid = query_collection_stage(conn, paid=True)
        assert len(paid) == 1
        assert paid[0]['is_prepaid'] is True
        assert query_collection_stage(conn) == []

        # 续保进入参保流程时保留已登记的缴费日期
        started = start_renewal(conn, sid, current_year=2026)
        assert started['status'] == S.APPROVED
        assert started['pay_date'] == '2026-11-10'

    def test_idempotent_and_conflict(self, conn):
        sid = _add_student(conn, '幂等生')
        _add_record(conn, sid, S.DONE, batch_no=1, expiry_date='2026-11-20')
        first = prepay(conn, sid, 2, 'xueping', '2026-11-10',
                       current_year=2026, today=TODAY)
        second = prepay(conn, sid, 2, 'xueping', '2026-11-10',
                        current_year=2026, today=TODAY)
        assert second['created'] is False
        assert second['record']['id'] == first['record']['id']

        with pytest.raises(InsuranceTransitionError):
            prepay(conn, sid, 2, 'xueping', '2026-11-12',
                   current_year=2026, today=TODAY)

    def test_previous_batch_must_be_terminal(self, conn):
        sid = _add_student(conn, '未办结')
        _add_record(conn, sid, S.APPROVED, batch_no=1)
        with pytest.raises(InsuranceTransitionError, match='上一批学平险'):
            prepay(conn, sid, 2, 'xueping', '2026-11-10',
                   current_year=2026, today=TODAY)

    def test_first_batch_rejected(self, conn):
        sid = _add_student(conn, '首保生')
        with pytest.raises(InsuranceTransitionError, match='首保'):
            prepay(conn, sid, 1, 'xueping', '2026-11-10',
                   current_year=2026, today=TODAY)

    def test_pay_date_validation(self, conn):
        sid = _add_student(conn, '日期校验')
        _add_record(conn, sid, S.DONE, batch_no=1, expiry_date='2026-11-20')
        with pytest.raises(InsuranceTransitionError, match='缴费日期'):
            prepay(conn, sid, 2, 'xueping', '', current_year=2026, today=TODAY)
        with pytest.raises(InsuranceTransitionError, match='不能晚于今天'):
            prepay(conn, sid, 2, 'xueping', '2026-12-01',
                   current_year=2026, today=TODAY)


# ============================================================
# 实习就业险预缴
# ============================================================
class TestInternPrepay:
    def test_creates_pending_approve_and_projects_snapshot(self, conn):
        sid = _add_student(conn, '实习预缴', grade='2025', enrollment_year=2025)
        _add_record(conn, sid, S.DONE, batch_no=1, expiry_date='2026-11-20')

        result = prepay(conn, sid, 3, 'intern', '2026-11-01',
                        operator='staff', current_year=2026, today=TODAY)
        rec = result['record']
        assert rec['status'] == S.PENDING_APPROVE
        assert rec['pay_date'] == '2026-11-01'
        assert rec['remark'] == ins.PREPAY_REMARK
        # 实习险预缴可投影到学生快照
        assert _student_row(conn, sid)['internship_insurance_pay_date'] == '2026-11-01'

        # 预缴实习险占位不应把学生从续保池排除（学平险第 2 年仍未办）
        pool = query_renewal_pool(conn, today=date(2026, 11, 1), current_year=2026)
        assert [p['batch_no'] for p in pool] == [2]

    def test_too_early_rejected(self, conn):
        sid = _add_student(conn, '太早')
        _add_record(conn, sid, S.DONE, batch_no=1, expiry_date='2026-11-20')
        with pytest.raises(InsuranceTransitionError, match='实习就业险'):
            prepay(conn, sid, 3, 'intern', '2026-11-01',
                   current_year=2026, today=TODAY)

    def test_cancel_deletes_created_record(self, conn):
        sid = _add_student(conn, '撤销实习', grade='2025', enrollment_year=2025)
        _add_record(conn, sid, S.DONE, batch_no=1, expiry_date='2026-11-20')
        rec = prepay(conn, sid, 3, 'intern', '2026-11-01',
                     operator='staff', current_year=2026, today=TODAY)['record']

        result = cancel_prepay(conn, rec['id'], operator='staff')
        assert result['deleted'] is True
        assert ins.get_record(conn, rec['id']) is None
        assert _student_row(conn, sid)['internship_insurance_pay_date'] == ''


# ============================================================
# 撤销预缴
# ============================================================
class TestCancelPrepay:
    def test_cancel_created_xueping_record(self, conn):
        sid = _add_student(conn, '撤销学平')
        _add_record(conn, sid, S.DONE, batch_no=1, expiry_date='2026-11-20')
        rec = prepay(conn, sid, 2, 'xueping', '2026-11-10',
                     operator='staff', current_year=2026, today=TODAY)['record']

        assert cancel_prepay(conn, rec['id'], operator='staff')['deleted'] is True
        assert _record_for(conn, sid, 2) is None

    def test_cancel_existing_pending_renewal_restores_remark(self, conn):
        sid = _add_student(conn, '复用记录')
        _add_record(conn, sid, S.DONE, batch_no=1, expiry_date='2026-11-20')
        _add_record(conn, sid, S.PENDING_RENEWAL, batch_no=2, remark='renewal')

        rec = prepay(conn, sid, 2, 'xueping', '2026-11-10', '100',
                     operator='staff', current_year=2026, today=TODAY)['record']
        assert rec['remark'] == 'renewal'

        result = cancel_prepay(conn, rec['id'], operator='staff')
        assert result['deleted'] is False
        rec = _record_for(conn, sid, 2)
        assert rec['status'] == S.PENDING_RENEWAL
        assert rec['pay_date'] == ''
        assert rec['premium'] == ''
        assert rec['remark'] == 'renewal'

    def test_staff_cannot_cancel_others(self, conn):
        sid = _add_student(conn, '越权撤销')
        _add_record(conn, sid, S.DONE, batch_no=1, expiry_date='2026-11-20')
        rec = prepay(conn, sid, 2, 'xueping', '2026-11-10',
                     operator='alice', current_year=2026, today=TODAY)['record']
        with pytest.raises(InsuranceTransitionError, match='本人'):
            cancel_prepay(conn, rec['id'], operator='bob', is_supervisor=False)

    def test_supervisor_cancel_advanced_keeps_record(self, conn):
        """主管撤销已推进到「已审批待导出」的预缴：只清缴费，不删流程记录。"""
        sid = _add_student(conn, '已推进')
        _add_record(conn, sid, S.DONE, batch_no=1, expiry_date='2026-11-20')
        rec = prepay(conn, sid, 2, 'xueping', '2026-11-10',
                     operator='staff', current_year=2026, today=TODAY)['record']
        start_renewal(conn, sid, current_year=2026)

        result = cancel_prepay(conn, rec['id'], operator='admin', is_supervisor=True)
        assert result['deleted'] is False
        rec = _record_for(conn, sid, 2)
        assert rec['status'] == S.APPROVED
        assert rec['pay_date'] == ''

    def test_sleep_cancel_reverts_activation(self, conn):
        sid = _add_student(conn, '睡眠激活撤销', grade='2026', enrollment_year=2024)
        for batch in (1, 2, 3):
            _add_record(conn, sid, S.EXEMPT, batch_no=batch,
                        remark=ins.REMARK_AUTO_UNINSURED)

        rec = prepay(conn, sid, 3, 'xueping', '2026-10-01',
                     operator='staff', current_year=2026, today=TODAY)['record']
        assert rec['status'] == S.PENDING_APPROVE
        assert rec['remark'] == ins.REMARK_SLEEP_ACTIVATED

        assert cancel_prepay(conn, rec['id'], operator='staff')['sleep'] is True
        rec = ins.get_record(conn, rec['id'])
        assert rec['status'] == S.EXEMPT
        assert rec['remark'] == ins.REMARK_AUTO_UNINSURED
        assert rec['pay_date'] == ''


# ============================================================
# 睡眠学生预缴激活
# ============================================================
class TestSleepPrepay:
    def test_prepay_activates_sleeping_student(self, conn):
        sid = _add_student(conn, '睡眠生', grade='2026', enrollment_year=2024)
        for batch in (1, 2, 3):
            _add_record(conn, sid, S.EXEMPT, batch_no=batch,
                        remark=ins.REMARK_AUTO_UNINSURED)

        targets = prepay_targets(_student_dict(conn, sid),
                                 ins.get_student_records(conn, sid), 2026)
        assert len(targets) == 1
        assert targets[0]['sleep'] is True
        assert targets[0]['batch_no'] == 3

        result = prepay(conn, sid, 3, 'xueping', '2026-10-01', '120',
                        operator='staff', current_year=2026, today=TODAY)
        assert result['sleep'] is True
        rec = result['record']
        assert rec['status'] == S.PENDING_APPROVE
        assert rec['remark'] == ins.REMARK_SLEEP_ACTIVATED
        assert ins.query_sleep_pool(conn, current_year=2026) == []
        assert [i['record_id'] for i in ins.query_sleep_activated(conn)] == [rec['id']]


# ============================================================
# 候选查询
# ============================================================
class TestPrepayCandidates:
    def test_search_returns_rows_per_target(self, conn):
        sid = _add_student(conn, '候选生', grade='2025', enrollment_year=2025)
        _add_record(conn, sid, S.DONE, batch_no=1, expiry_date='2026-11-20')
        data = query_prepay_candidates(conn, grade='2025', current_year=2026)
        assert data['total'] == 2
        assert {(i['batch_no'], i['insurance_type']) for i in data['items']} == \
            {(2, 'xueping'), (3, 'intern')}

    def test_dropout_and_graduated_excluded(self, conn):
        dropout = _add_student(conn, '退学候选', grade='2025', enrollment_year=2025,
                               attendance_status='退学')
        _add_record(conn, dropout, S.DONE, batch_no=1, expiry_date='2026-11-20')
        graduated = _add_student(conn, '毕业候选', grade='2025', enrollment_year=2025)
        _add_record(conn, graduated, S.DONE, batch_no=1, expiry_date='2026-11-20')
        conn.execute("UPDATE students SET registration_status = '毕业' WHERE id = ?",
                     (graduated,))
        conn.commit()
        assert query_prepay_candidates(conn, grade='2025', current_year=2026)['total'] == 0

    def test_pagination(self, conn):
        for i in range(3):
            sid = _add_student(conn, f'分页{i}', grade='2026', enrollment_year=2026)
            _add_record(conn, sid, S.DONE, batch_no=1, expiry_date='2026-11-20')
        data = query_prepay_candidates(conn, grade='2026', page=1, page_size=2,
                                       current_year=2026)
        assert data['total'] == 3
        assert data['pages'] == 2
        assert len(data['items']) == 2
        assert query_prepay_candidates(conn, grade='2026', page=2, page_size=2,
                                       current_year=2026)['items'][0]['batch_no'] == 2


# ============================================================
# 路由
# ============================================================
class TestPrepayRoutes:
    CSRF = 'test-csrf-token'

    @staticmethod
    def _login(client, role='finance_staff'):
        with client.session_transaction() as sess:
            sess.clear()
            sess['user_id'] = 1
            sess['username'] = 'tester'
            sess['role'] = role
            sess['csrf_token'] = TestPrepayRoutes.CSRF

    @staticmethod
    def _post(client, url, payload):
        return client.post(url, json=payload,
                           headers={'X-CSRF-Token': TestPrepayRoutes.CSRF})

    @staticmethod
    def _clean(db_conn):
        for table in ('insurance_operation_logs', 'insurance_export_batches',
                      'insurance_records', 'students'):
            db_conn.execute(f'DELETE FROM {table}')
        db_conn.commit()

    def test_candidates_require_filter(self, client, db_conn):
        self._clean(db_conn)
        self._login(client)
        try:
            resp = client.get('/api/insurance/prepay/candidates')
            assert resp.status_code == 400
            assert '年级' in resp.get_json()['message']
        finally:
            with client.session_transaction() as sess:
                sess.clear()
            self._clean(db_conn)

    def test_candidates_and_prepay_flow(self, client, db_conn):
        self._clean(db_conn)
        cur = db_conn.execute(
            "INSERT INTO students (id_card, name, student_no, grade, enrollment_year, "
            "school, admin_class) VALUES ('P-1', '路由预缴', 'NO-1', '2026', 2026, '1', '1班')")
        sid = cur.lastrowid
        db_conn.execute(
            "INSERT INTO insurance_records (student_id, id_card, school_year, "
            "batch_no, insurance_type, status, expiry_date) "
            "VALUES (?, 'P-1', '2026-2027', 1, 'xueping', 'done', '2026-11-20')",
            (sid,))
        db_conn.commit()

        self._login(client)
        try:
            data = client.get(
                '/api/insurance/prepay/candidates?search=NO-1').get_json()
            assert data['success'] is True
            assert data['total'] == 1
            assert data['items'][0]['batch_no'] == 2

            payload = {'items': [{'student_id': sid, 'batch_no': 2,
                                  'insurance_type': 'xueping',
                                  'pay_date': '2026-11-10', 'premium': ''}]}
            resp = self._post(client, '/api/insurance/prepay/batch', payload)
            data = resp.get_json()
            assert data['success'] is True
            assert data['count'] == 1

            rec = db_conn.execute(
                'SELECT * FROM insurance_records WHERE student_id = ? AND batch_no = 2',
                (sid,)).fetchone()
            assert rec['status'] == S.PENDING_RENEWAL
            assert rec['pay_date'] == '2026-11-10'

            # 已预缴的记录出现在「已缴费」页签
            paid = client.get(
                '/api/insurance/collection/list?status=paid').get_json()
            assert paid['total'] == 1
            assert paid['items'][0]['is_prepaid'] is True

            # 撤销预缴
            resp = self._post(client, '/api/insurance/prepay/cancel',
                              {'record_id': rec['id']})
            assert resp.get_json()['success'] is True
            assert db_conn.execute(
                'SELECT COUNT(*) FROM insurance_records WHERE student_id = ? '
                'AND batch_no = 2', (sid,)).fetchone()[0] == 0
        finally:
            with client.session_transaction() as sess:
                sess.clear()
            self._clean(db_conn)

    def test_insurance_officer_forbidden(self, client):
        self._login(client, 'insurance_officer')
        try:
            resp = client.get('/api/insurance/prepay/candidates?grade=2026')
            assert resp.status_code == 403
        finally:
            with client.session_transaction() as sess:
                sess.clear()

