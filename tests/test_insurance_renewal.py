# -*- coding: utf-8 -*-
"""保险模块 P4：续保页 + 保费催缴页。"""
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
    query_collection_stage,
    query_renewal_pool,
    query_renewal_stage,
    start_renewal,
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
    attendance_status TEXT DEFAULT '',
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


def _add_record(conn, student_id, status, batch_no=1, insurance_type='xueping',
                expiry_date='', pay_date=''):
    cur = conn.execute(
        '''INSERT INTO insurance_records
           (student_id, id_card, school_year, batch_no, insurance_type, status,
            expiry_date, pay_date)
           VALUES (?, 'ID', '2026-2027', ?, ?, ?, ?, ?)''',
        (student_id, batch_no, insurance_type, status, expiry_date, pay_date)
    )
    conn.commit()
    return cur.lastrowid


# ============================================================
# start_renewal
# ============================================================
class TestStartRenewal:
    def test_creates_approved_next_batch(self, conn):
        """续保进入参保流程后先待导出，报送保险公司后才补录保单。"""
        sid = _add_student(conn, '续保生')
        _add_record(conn, sid, S.DONE, batch_no=1, expiry_date='2026-11-20')
        rec = start_renewal(conn, sid, current_year=2026)
        assert rec['batch_no'] == 2
        assert rec['status'] == S.APPROVED
        assert rec['insurance_type'] == 'xueping'
        assert rec['school_year'] == '2027-2028'

    def test_idempotent(self, conn):
        sid = _add_student(conn, '续保生')
        _add_record(conn, sid, S.DONE, batch_no=1, expiry_date='2026-11-20')
        first = start_renewal(conn, sid, current_year=2026)
        second = start_renewal(conn, sid, current_year=2026)
        assert first['id'] == second['id']

    def test_transfer_has_no_renewal(self, conn):
        sid = _add_student(conn, '转学生', grade='2025', enrollment_year=2026)
        _add_record(conn, sid, S.DONE, batch_no=2, expiry_date='2026-11-20')
        with pytest.raises(InsuranceTransitionError):
            start_renewal(conn, sid, current_year=2026)

    def test_missing_student(self, conn):
        with pytest.raises(InsuranceTransitionError):
            start_renewal(conn, 999, current_year=2026)

    def test_deferred_third_year(self, conn):
        sid = _add_student(conn, '推迟注册', grade='2027', enrollment_year=2026)
        _add_record(conn, sid, S.DONE, batch_no=1, expiry_date='2026-11-20')
        _add_record(conn, sid, S.DONE, batch_no=2, expiry_date='2027-11-20')
        rec = start_renewal(conn, sid, current_year=2026)
        assert rec['batch_no'] == 3
        assert rec['status'] == S.APPROVED

    def test_deferred_push_to_renewal_appears_when_expiring(self, conn):
        """推送至续保的批次按保单到期时间进入「待续保」，而非立即进入参保环节。"""
        today = date(2026, 10, 25)
        sid = _add_student(conn, '推迟注册', grade='2027', enrollment_year=2026)
        for batch in (1, 2, 3):
            _add_record(conn, sid, S.DONE, batch_no=batch,
                        expiry_date='2026-11-20')

        # 计划内学平险已全部办结，没有可续保的计划批次
        assert query_renewal_pool(conn, today=today, current_year=2026) == []

        result = ins.push_to_renewal(conn, [sid], current_year=2026)
        assert result['created'] == 1

        # 进入到期窗口后出现在「待续保」，此时尚未进入参保环节
        pool = query_renewal_pool(conn, today=today, current_year=2026)
        assert len(pool) == 1
        assert pool[0]['status'] == S.PENDING_RENEWAL
        assert pool[0]['batch_no'] == 4
        assert query_renewal_stage(conn, current_year=2026) == []

        # 点击「进入参保流程」后才转为待导出
        started = start_renewal(conn, sid, current_year=2026)
        assert started['batch_no'] == 4
        assert started['status'] == S.APPROVED
        stage = query_renewal_stage(conn, current_year=2026)
        assert len(stage) == 1
        assert stage[0]['batch_no'] == 4
        assert stage[0]['source_status'] == S.APPROVED


# ============================================================
# query_collection_stage
# ============================================================
class TestCollectionStage:
    def test_pending_all_types(self, conn):
        """催缴对象：学平险续保记录 + 已确认办理的实习就业险记录。"""
        sid = _add_student(conn, '待缴')
        _add_record(conn, sid, S.APPROVED, batch_no=2)
        _add_record(conn, sid, S.APPROVED, batch_no=3, insurance_type='intern')
        items = query_collection_stage(conn)
        assert sorted(i['insurance_type'] for i in items) == ['intern', 'xueping']

    def test_first_batch_not_collected(self, conn):
        """首保缴费在审批环节确定，不进入催缴。"""
        sid = _add_student(conn, '首保')
        _add_record(conn, sid, S.DONE, batch_no=1)
        assert query_collection_stage(conn) == []

    def test_unconfirmed_intern_excluded(self, conn):
        sid = _add_student(conn, '实习未确认')
        _add_record(conn, sid, S.PENDING_APPROVE, batch_no=3,
                    insurance_type='intern')
        assert query_collection_stage(conn) == []

    def test_paid_only_xueping(self, conn):
        sid = _add_student(conn, '已缴')
        _add_record(conn, sid, S.APPROVED, batch_no=2, pay_date='2026-10-01')
        _add_record(conn, sid, S.APPROVED, batch_no=3, insurance_type='intern',
                    pay_date='2026-10-01')
        items = query_collection_stage(
            conn, paid=True, insurance_types=('xueping',))
        assert len(items) == 1
        assert items[0]['insurance_type'] == 'xueping'


# ============================================================
# 路由
# ============================================================
class TestRenewalCollectionRoutes:
    CSRF = 'test-csrf-token'

    @staticmethod
    def _login(client, role='insurance_officer'):
        with client.session_transaction() as sess:
            sess.clear()
            sess['user_id'] = 1
            sess['username'] = 'tester'
            sess['role'] = role
            sess['csrf_token'] = TestRenewalCollectionRoutes.CSRF

    @staticmethod
    def _logout(client):
        with client.session_transaction() as sess:
            sess.clear()

    @staticmethod
    def _post(client, url, payload):
        return client.post(url, json=payload,
                           headers={'X-CSRF-Token': TestRenewalCollectionRoutes.CSRF})

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
            "student_no, admin_class) VALUES (?, ?, ?, ?, 1, ?, '1班')",
            (f'ROUTE-{name}', name, grade, enrollment_year, f'NO-{name}')
        )
        db_conn.commit()
        return cur.lastrowid

    @staticmethod
    def _record(db_conn, student_id, status, batch_no=1,
                insurance_type='xueping', expiry_date=''):
        cur = db_conn.execute(
            "INSERT INTO insurance_records (student_id, id_card, school_year, "
            "batch_no, insurance_type, status, expiry_date) "
            "VALUES (?, 'X', '2026-2027', ?, ?, ?, ?)",
            (student_id, batch_no, insurance_type, status, expiry_date)
        )
        db_conn.commit()
        return cur.lastrowid

    def test_pages_render(self, client):
        self._login(client)
        try:
            resp = client.get('/insurance/renewal')
            assert resp.status_code == 200
            html = resp.get_data(as_text=True)
            assert '学平险续保' in html
            assert '已过期' in html
            assert 'id="irnExemptBtn"' in html
            # 保险员不能「放弃本期」
            assert 'var canExempt = false' in html
            # 保费催缴页仅财务角色可见，保险员不可访问
            assert client.get('/insurance/collection').status_code == 403
            # 财务主管同样可进入续保页，并可「放弃本期」
            self._login(client, 'finance_chief')
            resp = client.get('/insurance/renewal')
            assert resp.status_code == 200
            assert 'var canExempt = true' in resp.get_data(as_text=True)
            self._login(client, 'finance_staff')
            resp = client.get('/insurance/collection')
            assert resp.status_code == 200
            assert '保费催缴' in resp.get_data(as_text=True)
        finally:
            self._logout(client)

    def test_renewal_list_and_start(self, client, db_conn):
        self._clean(db_conn)
        sid = self._student(db_conn, '续保生')
        _rid = self._record(db_conn, sid, S.DONE, batch_no=1,
                            expiry_date='2026-11-20')
        self._login(client)
        try:
            resp = client.get('/api/insurance/renewal/list?today=2026-11-01')
            data = resp.get_json()
            assert data['success'] is True
            assert data['total'] == 1
            assert data['items'][0]['id'] == sid
            assert data['items'][0]['previous_batch_no'] == 1
            assert data['items'][0]['urgency'] == 'due'
            assert data['items'][0]['target_school_year'] == '2027-2028'
            assert data['summary'] == {'overdue': 0, 'due': 1,
                                       'missing_expiry': 0, 'total': 1}

            resp = self._post(client, '/api/insurance/renewal/start',
                              {'student_ids': [sid]})
            data = resp.get_json()
            assert data['success'] is True
            assert data['count'] == 1

            rec = db_conn.execute(
                'SELECT * FROM insurance_records WHERE student_id = ? AND batch_no = 2',
                (sid,)
            ).fetchone()
            assert rec['status'] == S.APPROVED

            # 已生成下一批记录后，续保池不再重复出现
            resp = client.get('/api/insurance/renewal/list?today=2026-11-01')
            assert resp.get_json()['total'] == 0
        finally:
            self._logout(client)
            self._clean(db_conn)

    def test_renewal_exempt_batch(self, client, db_conn):
        """放弃本期：仅 admin/财务主管可操作，且只终结该批次。"""
        self._clean(db_conn)
        sid = self._student(db_conn, '放弃生')
        self._record(db_conn, sid, S.DONE, batch_no=1,
                     expiry_date='2025-01-01')

        # 保险员无此权限
        self._login(client)
        resp = self._post(client, '/api/insurance/renewal/exempt',
                          {'student_ids': [sid]})
        assert resp.status_code == 403

        self._login(client, 'finance_chief')
        try:
            data = self._post(client, '/api/insurance/renewal/exempt',
                              {'student_ids': [sid]}).get_json()
            assert data['success'] is True
            assert data['count'] == 1

            rec = db_conn.execute(
                'SELECT * FROM insurance_records WHERE student_id = ? '
                'AND batch_no = 2', (sid,)).fetchone()
            assert rec['status'] == S.EXEMPT
            assert rec['completed_at']
            assert rec['remark'] == 'exempt_renewal'
            log = db_conn.execute(
                'SELECT action FROM insurance_operation_logs '
                'WHERE record_id = ? ORDER BY id DESC LIMIT 1',
                (rec['id'],)).fetchone()
            assert log['action'] == 'exempt_renewal'

            # 放弃后不再停留在待续保池
            pool = client.get(
                '/api/insurance/renewal/list?today=2025-06-01').get_json()
            assert pool['total'] == 0
        finally:
            self._logout(client)
            self._clean(db_conn)

    def test_overdue_pool_and_intern_exclusion(self, client, db_conn):
        """过期学生进入池；到期日缺失单列；已进入实习险的学生业务终了须排除。"""
        self._clean(db_conn)
        overdue = self._student(db_conn, '过期生')
        self._record(db_conn, overdue, S.DONE, batch_no=1,
                     expiry_date='2026-10-20')
        internship = self._student(db_conn, '已实习')
        self._record(db_conn, internship, S.DONE, batch_no=1,
                     expiry_date='2026-10-20')
        self._record(db_conn, internship, S.DONE, batch_no=3,
                     insurance_type='intern')
        missing = self._student(db_conn, '缺到期')
        self._record(db_conn, missing, S.DONE, batch_no=1)
        self._login(client)
        try:
            data = client.get(
                '/api/insurance/renewal/list?today=2026-11-07').get_json()
            items = {item['name']: item for item in data['items']}
            assert set(items) == {'过期生', '缺到期'}
            assert items['过期生']['urgency'] == 'overdue'
            assert items['过期生']['days_left'] < 0
            assert items['过期生']['status_label'] == '已过期'
            assert items['缺到期']['urgency'] == 'missing_expiry'
            assert items['缺到期']['days_left'] is None
            assert data['summary'] == {'overdue': 1, 'due': 0,
                                       'missing_expiry': 1, 'total': 2}
        finally:
            self._logout(client)
            self._clean(db_conn)

    def test_collection_pay_then_renewal_export_policy(self, client, db_conn):
        """催缴只登记收费；续保导出 → 补录保单 → 办结走续保接口。"""
        self._clean(db_conn)
        sid = self._student(db_conn, '催缴生')
        rid = self._record(db_conn, sid, S.APPROVED, batch_no=2)
        isid = self._student(db_conn, '实习生')
        irid = self._record(db_conn, isid, S.APPROVED, batch_no=3,
                            insurance_type='intern')
        self._login(client, 'finance_staff')
        try:
            resp = client.get('/api/insurance/collection/list?status=pending')
            data = resp.get_json()
            assert data['success'] is True
            assert data['total'] == 2
            assert data['summary']['pending'] == 2

            resp = self._post(client, '/api/insurance/collection/pay', {
                'record_ids': [rid, irid], 'pay_date': '2026-11-06',
            })
            data = resp.get_json()
            assert data['success'] is True
            assert data['count'] == 2

            # 缴费不改变保险状态，只写缴费日期
            for record_id in (rid, irid):
                rec = db_conn.execute(
                    'SELECT status, pay_date FROM insurance_records WHERE id = ?',
                    (record_id,)).fetchone()
                assert rec['status'] == S.APPROVED
                assert rec['pay_date'] == '2026-11-06'

            # 已缴费只读页签可见
            resp = client.get('/api/insurance/collection/list?status=paid')
            assert resp.get_json()['total'] == 2

            # 续保导出/补录保单仅保险员可操作
            self._login(client)
            # 续保环节：待导出
            resp = client.get('/api/insurance/renewal/stage?status=approved')
            data = resp.get_json()
            assert data['success'] is True
            assert data['total'] == 1
            assert data['items'][0]['record_id'] == rid

            # 导出续保参保数据 → exported
            resp = client.get(f'/api/insurance/renewal/export?record_ids={rid}')
            assert resp.status_code == 200
            assert db_conn.execute(
                'SELECT status FROM insurance_records WHERE id = ?', (rid,)
            ).fetchone()['status'] == S.EXPORTED

            # 报送后补录保单 → done
            resp = self._post(client, '/api/insurance/renewal/policy', {
                'record_ids': [rid],
                'policy_no': 'RENEW-001',
                'expiry_date': '2027-11-05',
            })
            data = resp.get_json()
            assert data['success'] is True
            assert data['count'] == 1

            rec = db_conn.execute(
                'SELECT * FROM insurance_records WHERE id = ?', (rid,)
            ).fetchone()
            assert rec['status'] == S.DONE
            assert rec['policy_no'] == 'RENEW-001'
            assert rec['expiry_date'] == '2027-11-05'

            # 已办结记录不再堆积在「已进入参保」页签
            resp = client.get('/api/insurance/renewal/stage')
            assert resp.get_json()['total'] == 0
        finally:
            self._logout(client)
            self._clean(db_conn)

    def test_requires_login(self, client):
        self._logout(client)
        resp = client.get('/api/insurance/collection/list')
        assert resp.status_code == 401
        assert resp.get_json()['success'] is False
