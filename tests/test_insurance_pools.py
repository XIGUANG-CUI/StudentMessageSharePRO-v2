# -*- coding: utf-8 -*-
"""保险模块 P2：五个池只读查询的单元测试与接口冒烟。"""
import os
import sqlite3
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import models.insurance as ins
from models.insurance import (
    InsuranceStatus,
    pool_summary,
    query_collection_pool,
    query_first_pool,
    query_intern_pool,
    query_push_pool,
    query_renewal_pool,
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


def _add_student(conn, name='张三', grade='2026', enrollment_year=2026,
                 school='1', admin_class='1班', major='计算机',
                 registration_status='', is_deleted=0):
    cur = conn.execute(
        '''INSERT INTO students
           (id_card, name, grade, enrollment_year, school, admin_class, major,
            registration_status, is_deleted)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (f'ID-{name}', name, grade, enrollment_year, school, admin_class,
         major, registration_status, is_deleted)
    )
    conn.commit()
    return cur.lastrowid


def _add_record(conn, student_id, insurance_type='xueping', status=None,
                batch_no=1, expiry_date='', pay_date='', policy_no=''):
    cur = conn.execute(
        '''INSERT INTO insurance_records
           (student_id, id_card, school_year, batch_no, insurance_type, status,
            pay_date, policy_no, expiry_date)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (student_id, 'ID', '2026-2027', batch_no, insurance_type,
         status or S.PENDING_APPROVE, pay_date, policy_no, expiry_date)
    )
    conn.commit()
    return cur.lastrowid


# ============================================================
# ① 首保池
# ============================================================
class TestFirstPool:
    def test_new_normal_student_included(self, conn):
        sid = _add_student(conn, '新生')
        items = query_first_pool(conn, current_year=2026)
        assert len(items) == 1
        assert items[0]['id'] == sid
        assert items[0]['batch_no'] == 1
        assert items[0]['insurance_type'] == 'xueping'
        assert items[0]['stage'] == 'first'
        assert items[0]['status'] == ''
        assert items[0]['status_label'] == '未建档'
        assert items[0]['kind'] == 'normal'

    def test_pending_approve_included_approved_excluded(self, conn):
        a = _add_student(conn, '待审批')
        b = _add_student(conn, '已审批')
        _add_record(conn, a, status=S.PENDING_APPROVE)
        _add_record(conn, b, status=S.APPROVED)
        items = query_first_pool(conn, current_year=2026)
        assert [i['id'] for i in items] == [a]

    def test_done_excluded(self, conn):
        sid = _add_student(conn, '已办结')
        _add_record(conn, sid, status=S.DONE)
        assert query_first_pool(conn, current_year=2026) == []

    def test_transfer_uses_current_batch(self, conn):
        _add_student(conn, '转学生', grade='2025', enrollment_year=2026)
        items = query_first_pool(conn, current_year=2026)
        assert len(items) == 1
        assert items[0]['batch_no'] == 2
        assert items[0]['kind'] == 'transfer'

    def test_transfer_completed_first_not_again(self, conn):
        """转学生首保批次固定：第 2 年办结后不再回到首保池。"""
        sid = _add_student(conn, '转学生', grade='2024', enrollment_year=2025)
        _add_record(conn, sid, status=S.DONE, batch_no=2)
        assert query_first_pool(conn, current_year=2026) == []

    def test_deferred_first_batch(self, conn):
        _add_student(conn, '推迟注册', grade='2027', enrollment_year=2026)
        items = query_first_pool(conn, current_year=2026)
        assert len(items) == 1
        assert items[0]['batch_no'] == 1
        assert items[0]['kind'] == 'deferred'

    def test_soft_deleted_and_graduated_excluded(self, conn):
        _add_student(conn, '已删除', is_deleted=1)
        _add_student(conn, '已毕业', registration_status='毕业')
        assert query_first_pool(conn, current_year=2026) == []

    def test_search_and_school_filter(self, conn):
        _add_student(conn, '张三', school='1')
        _add_student(conn, '李四', school='2')
        assert len(query_first_pool(conn, current_year=2026)) == 2
        assert len(query_first_pool(conn, current_year=2026, search='张')) == 1
        assert len(query_first_pool(conn, current_year=2026, school='2')) == 1


# ============================================================
# ② 续保池（30 天窗口 + 同月整批）
# ============================================================
class TestRenewalPool:
    def test_window_activates_and_same_month_expands(self, conn):
        today = date(2026, 10, 20)
        a = _add_student(conn, 'A')
        b = _add_student(conn, 'B')
        c = _add_student(conn, 'C')
        _add_record(conn, a, status=S.DONE, batch_no=1, expiry_date='2026-11-10')
        _add_record(conn, b, status=S.DONE, batch_no=1, expiry_date='2026-11-25')
        _add_record(conn, c, status=S.DONE, batch_no=1, expiry_date='2026-12-10')

        items = query_renewal_pool(conn, today=today, current_year=2026)
        assert sorted(i['id'] for i in items) == sorted([a, b])
        for item in items:
            assert item['batch_no'] == 2
            assert item['previous_batch_no'] == 1
            assert item['stage'] == 'renewal'
            assert item['status'] == 'pending_renewal'
            assert item['status_label'] == '待续保'

    def test_no_active_month_returns_empty(self, conn):
        sid = _add_student(conn, '远端到期')
        _add_record(conn, sid, status=S.DONE, batch_no=1, expiry_date='2027-06-01')
        assert query_renewal_pool(conn, today=date(2026, 10, 20),
                                  current_year=2026) == []

    def test_transfer_not_in_renewal(self, conn):
        sid = _add_student(conn, '转学生', grade='2025', enrollment_year=2026)
        _add_record(conn, sid, status=S.DONE, batch_no=2, expiry_date='2026-11-10')
        assert query_renewal_pool(conn, today=date(2026, 10, 20),
                                  current_year=2026) == []

    def test_existing_next_batch_record_excluded(self, conn):
        sid = _add_student(conn, '已建下一批')
        _add_record(conn, sid, status=S.DONE, batch_no=1, expiry_date='2026-11-10')
        _add_record(conn, sid, status=S.PENDING_APPROVE, batch_no=2)
        assert query_renewal_pool(conn, today=date(2026, 10, 20),
                                  current_year=2026) == []


# ============================================================
# ③ 催缴池
# ============================================================
class TestCollectionPool:
    def test_pending_pay_xueping_and_intern(self, conn):
        """催缴 = 学平险续保 + 已确认办理的实习就业险，且未缴费。"""
        sid = _add_student(conn, '待缴')
        _add_record(conn, sid, status=S.APPROVED, batch_no=2)
        _add_record(conn, sid, insurance_type='intern', status=S.APPROVED,
                    batch_no=3)
        items = query_collection_pool(conn)
        assert sorted(i['insurance_type'] for i in items) == ['intern', 'xueping']
        assert all(i['status_label'] == '待缴费' for i in items)

    def test_first_batch_and_unconfirmed_intern_excluded(self, conn):
        sid = _add_student(conn, '已办')
        _add_record(conn, sid, status=S.DONE, batch_no=1)
        _add_record(conn, sid, insurance_type='intern', status=S.PENDING_APPROVE,
                    batch_no=3)
        assert query_collection_pool(conn) == []

    def test_already_paid_excluded(self, conn):
        sid = _add_student(conn, '已缴')
        _add_record(conn, sid, status=S.APPROVED, batch_no=2,
                    pay_date='2026-10-01')
        _add_record(conn, sid, insurance_type='intern', status=S.APPROVED,
                    batch_no=3, pay_date='2026-10-01')
        assert query_collection_pool(conn) == []


# ============================================================
# ④ 待推送池
# ============================================================
class TestPushPool:
    def test_all_xueping_done_enters(self, conn):
        sid = _add_student(conn, '可推送')
        _add_record(conn, sid, status=S.DONE, batch_no=1)
        _add_record(conn, sid, status=S.DONE, batch_no=2)
        items = query_push_pool(conn, current_year=2026)
        assert len(items) == 1
        assert items[0]['insurance_type'] == 'intern'
        assert items[0]['batch_no'] == 3
        assert items[0]['status'] == 'ready_to_push'
        assert items[0]['status_label'] == '待推送'
        assert items[0]['previous_batch_no'] == 2

    def test_missing_xueping_done_blocked(self, conn):
        sid = _add_student(conn, '缺第2年')
        _add_record(conn, sid, status=S.DONE, batch_no=1)
        assert query_push_pool(conn, current_year=2026) == []

    def test_existing_intern_record_blocked(self, conn):
        sid = _add_student(conn, '已有实习险')
        _add_record(conn, sid, status=S.DONE, batch_no=1)
        _add_record(conn, sid, status=S.DONE, batch_no=2)
        _add_record(conn, sid, insurance_type='intern', status=S.DONE, batch_no=3)
        assert query_push_pool(conn, current_year=2026) == []

    def test_transfer_push(self, conn):
        sid = _add_student(conn, '转学生', grade='2025', enrollment_year=2026)
        _add_record(conn, sid, status=S.DONE, batch_no=2)
        items = query_push_pool(conn, current_year=2026)
        assert len(items) == 1
        assert items[0]['batch_no'] == 3

    def test_deferred_push_to_renewal_waits_for_expiry(self, conn):
        """推送至续保后，只有当前保单到期才进入待续保并走续保流程。"""
        sid = _add_student(conn, '推迟注册', grade='2027', enrollment_year=2026)
        for batch in (1, 2, 3):
            _add_record(conn, sid, status=S.DONE, batch_no=batch,
                        expiry_date='2026-11-20')

        result = ins.push_to_renewal(conn, [sid], current_year=2026)
        assert result['created'] == 1
        assert result['skipped'] == 0

        rec = conn.execute(
            "SELECT * FROM insurance_records WHERE student_id = ? "
            "AND insurance_type = 'xueping' AND batch_no = 4", (sid,)
        ).fetchone()
        assert rec is not None
        assert rec['status'] == S.PENDING_RENEWAL
        assert rec['remark'] == 'renewal'

        # 不在推送页，且保单未到期时不在待续保
        assert query_push_pool(conn, current_year=2026) == []
        assert query_renewal_pool(conn, today=date(2026, 8, 1),
                                  current_year=2026) == []

        # 进入 30 天到期窗口：出现在待续保，展示当前批次与下一批
        items = query_renewal_pool(conn, today=date(2026, 10, 25),
                                   current_year=2026)
        assert len(items) == 1
        assert items[0]['status'] == S.PENDING_RENEWAL
        assert items[0]['previous_batch_no'] == 3
        assert items[0]['batch_no'] == 4

        # 进入参保流程：占位记录转为待导出；办结后批次增长为 4
        started = ins.start_renewal(conn, sid, current_year=2026)
        assert started['batch_no'] == 4
        assert started['status'] == S.APPROVED
        conn.execute(
            'UPDATE insurance_records SET status = ? WHERE id = ?',
            (S.DONE, started['id'])
        )
        conn.commit()
        items = query_push_pool(conn, current_year=2026)
        assert len(items) == 1
        assert items[0]['previous_batch_no'] == 4

    def test_push_to_renewal_rejects_normal(self, conn):
        sid = _add_student(conn, '普通生')
        _add_record(conn, sid, status=S.DONE, batch_no=1)
        _add_record(conn, sid, status=S.DONE, batch_no=2)
        result = ins.push_to_renewal(conn, [sid], current_year=2026)
        assert result['created'] == 0
        assert result['skipped'] == 1
        assert result['errors'][0]['message'] == '仅推迟注册学生可继续续保'


# ============================================================
# ⑤ 实习办结池
# ============================================================
class TestInternPool:
    def test_pending_approve_paid_approved_included(self, conn):
        for index, status in enumerate((S.PENDING_APPROVE, S.PAID, S.APPROVED)):
            sid = _add_student(conn, f'实习{index}')
            _add_record(conn, sid, insurance_type='intern', batch_no=3,
                        status=status)
        items = query_intern_pool(conn)
        assert len(items) == 3
        assert all(i['insurance_type'] == 'intern' for i in items)

    def test_done_and_xueping_excluded(self, conn):
        a = _add_student(conn, '实习已办结')
        b = _add_student(conn, '学平险记录')
        _add_record(conn, a, insurance_type='intern', status=S.DONE, batch_no=3)
        _add_record(conn, b, insurance_type='xueping', status=S.PENDING_APPROVE,
                    batch_no=1)
        assert query_intern_pool(conn) == []


# ============================================================
# 汇总
# ============================================================
class TestPoolSummary:
    def test_counts(self, conn):
        _add_student(conn, '首保')

        renewal = _add_student(conn, '续保')
        _add_record(conn, renewal, status=S.DONE, batch_no=1,
                    expiry_date='2026-11-10')

        collection = _add_student(conn, '催缴')
        _add_record(conn, collection, status=S.DONE, batch_no=1)
        _add_record(conn, collection, status=S.APPROVED, batch_no=2)

        push = _add_student(conn, '推送')
        _add_record(conn, push, status=S.DONE, batch_no=1)
        _add_record(conn, push, status=S.DONE, batch_no=2,
                    pay_date='2026-09-01')

        intern = _add_student(conn, '实习')
        _add_record(conn, intern, status=S.DONE, batch_no=1)
        _add_record(conn, intern, insurance_type='intern', status=S.APPROVED,
                    batch_no=3, pay_date='2026-10-01')

        summary = pool_summary(conn, today=date(2026, 10, 20), current_year=2026)
        assert summary == {
            'first': 1, 'renewal': 1, 'collection': 1, 'push': 1, 'intern': 1,
        }


# ============================================================
# 路由冒烟（只读接口）
# ============================================================
POOL_PATHS = (
    '/api/insurance/pools/first',
    '/api/insurance/pools/renewal',
    '/api/insurance/pools/collection',
    '/api/insurance/pools/push',
    '/api/insurance/pools/intern',
)


class TestPoolRoutes:
    @staticmethod
    def _login(client, role='admin'):
        with client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'tester'
            sess['role'] = role

    def test_requires_login(self, client):
        with client.session_transaction() as sess:
            sess.clear()
        resp = client.get('/api/insurance/pools/first')
        assert resp.status_code == 401
        assert resp.get_json()['success'] is False

    def test_role_forbidden(self, client):
        self._login(client, role='user')
        resp = client.get('/api/insurance/pools/first')
        assert resp.status_code == 403
        assert resp.get_json()['success'] is False
        with client.session_transaction() as sess:
            sess.clear()

    def test_pools_and_summary(self, client, db_conn):
        db_conn.execute('DELETE FROM insurance_records')
        db_conn.execute('DELETE FROM students')
        db_conn.execute(
            "INSERT INTO students (id_card, name, grade, enrollment_year, school) "
            "VALUES ('ROUTE-1', '路由测试', '2026', 2026, 1)"
        )
        db_conn.commit()

        try:
            self._login(client)
            for path in POOL_PATHS:
                resp = client.get(path)
                assert resp.status_code == 200
                data = resp.get_json()
                assert data['success'] is True
                assert isinstance(data['items'], list)
                assert data['total'] == len(data['items'])

            resp = client.get('/api/insurance/pools/summary')
            assert resp.status_code == 200
            data = resp.get_json()
            assert data['success'] is True
            assert set(data['summary']) == {
                'first', 'renewal', 'collection', 'push', 'intern',
            }
        finally:
            with client.session_transaction() as sess:
                sess.clear()
            db_conn.execute('DELETE FROM insurance_records')
            db_conn.execute('DELETE FROM students')
            db_conn.commit()


# ============================================================
# 催缴页：待缴费导出 + 已缴费按险种筛选
# ============================================================
class TestCollectionRoutes:
    @staticmethod
    def _login(client):
        with client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'tester'
            sess['role'] = 'admin'

    @staticmethod
    def _clean(db_conn):
        db_conn.execute('DELETE FROM insurance_records')
        db_conn.execute('DELETE FROM students')
        db_conn.commit()

    @staticmethod
    def _seed(db_conn):
        cur = db_conn.execute(
            "INSERT INTO students (id_card, name, grade, enrollment_year, school, "
            "student_no, admin_class) VALUES ('COL-1', '催缴生', '2026', 2026, 1, "
            "'COL-NO-1', '1班')")
        sid = cur.lastrowid
        db_conn.execute(
            "INSERT INTO insurance_records (student_id, id_card, school_year, "
            "batch_no, insurance_type, status, pay_date) "
            "VALUES (?, 'COL-1', '2026-2027', 2, 'xueping', 'approved', '')", (sid,))
        db_conn.execute(
            "INSERT INTO insurance_records (student_id, id_card, school_year, "
            "batch_no, insurance_type, status, pay_date) "
            "VALUES (?, 'COL-1', '2026-2027', 3, 'intern', 'approved', '')", (sid,))
        db_conn.commit()
        return sid

    def test_pending_export_and_paid_type_filter(self, client, db_conn):
        self._clean(db_conn)
        sid = self._seed(db_conn)
        try:
            self._login(client)

            data = client.get('/api/insurance/collection/list?status=pending').get_json()
            assert data['total'] == 2
            data = client.get(
                '/api/insurance/collection/list?status=pending&insurance_type=xueping'
            ).get_json()
            assert data['total'] == 1
            assert data['items'][0]['insurance_type'] == 'xueping'

            resp = client.get('/api/insurance/collection/export')
            assert resp.status_code == 200
            assert resp.mimetype.endswith('spreadsheetml.sheet')
            assert 'attachment' in resp.headers.get('Content-Disposition', '')

            db_conn.execute(
                "UPDATE insurance_records SET pay_date = '2026-11-01' "
                "WHERE student_id = ? AND insurance_type = 'xueping'", (sid,))
            db_conn.commit()

            data = client.get(
                '/api/insurance/collection/list?status=paid&insurance_type=xueping'
            ).get_json()
            assert data['total'] == 1
            assert data['items'][0]['status_label'] == '已缴费'
            data = client.get(
                '/api/insurance/collection/list?status=paid&insurance_type=intern'
            ).get_json()
            assert data['total'] == 0

            # 仅剩实习险未缴费，导出仍可用
            assert client.get('/api/insurance/collection/export').status_code == 200

            db_conn.execute(
                "UPDATE insurance_records SET pay_date = '2026-11-01' "
                "WHERE student_id = ? AND insurance_type = 'intern'", (sid,))
            db_conn.commit()
            resp = client.get('/api/insurance/collection/export')
            assert resp.status_code == 400
            assert resp.get_json()['success'] is False
        finally:
            with client.session_transaction() as sess:
                sess.clear()
            self._clean(db_conn)
