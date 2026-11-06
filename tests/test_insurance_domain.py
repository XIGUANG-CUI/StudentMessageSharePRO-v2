# -*- coding: utf-8 -*-
"""保险模块 P1：计划引擎、状态流转、日志与快照投影的单元测试。"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import config
import models.insurance as ins
from models.insurance import (
    InsuranceStatus,
    InsuranceTransitionError,
    build_plan,
    current_insurance_batch,
    student_kind,
)

S = InsuranceStatus


STUDENT_SCHEMA = """
CREATE TABLE students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_card TEXT,
    name TEXT,
    grade TEXT,
    enrollment_year INTEGER,
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


def _db():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript(STUDENT_SCHEMA)
    ins.migrate_insurance_v3(conn)      # 建三张保险表（此时 0 学生）
    conn.commit()
    return conn


def _add_student(conn, grade='2026', enrollment_year=2026, id_card='X'):
    cur = conn.execute(
        'INSERT INTO students (id_card, name, grade, enrollment_year) VALUES (?, ?, ?, ?)',
        (id_card, id_card, grade, enrollment_year)
    )
    conn.commit()
    return cur.lastrowid


def _add_record(conn, student_id, insurance_type='xueping', status=None,
                batch_no=1, school_year='2026-2027',
                pay_date='', policy_no='', expiry_date='', premium=''):
    cur = conn.execute(
        '''INSERT INTO insurance_records
           (student_id, id_card, school_year, batch_no, insurance_type, status,
            pay_date, premium, policy_no, expiry_date)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (student_id, 'X', school_year, batch_no, insurance_type,
         status or S.PENDING_APPROVE, pay_date, premium, policy_no, expiry_date)
    )
    conn.commit()
    return cur.lastrowid


def _student(conn, student_id):
    return dict(conn.execute('SELECT * FROM students WHERE id = ?', (student_id,)).fetchone())


# ============================================================
# 计划引擎
# ============================================================
class TestPlan:
    def test_student_kind(self):
        assert student_kind(2026, '2026') == 'normal'
        assert student_kind(2026, '2025') == 'transfer'
        assert student_kind(2026, '2027') == 'deferred'

    def test_normal_plan(self):
        assert build_plan(2026, '2026', current_year=2026) == [
            {'batch_no': 1, 'insurance_type': 'xueping', 'stage': 'first'},
            {'batch_no': 2, 'insurance_type': 'xueping', 'stage': 'renewal'},
            {'batch_no': 3, 'insurance_type': 'intern', 'stage': 'push'},
        ]

    def test_transfer_plan_skips_renewal(self):
        """转学生首保即本校第 2 年学平险，下一步直接进实习险。"""
        assert build_plan(2026, '2025', current_year=2026) == [
            {'batch_no': 2, 'insurance_type': 'xueping', 'stage': 'first'},
            {'batch_no': 3, 'insurance_type': 'intern', 'stage': 'push'},
        ]

    def test_transfer_plan_batch_not_sliding(self):
        """转学生首保批次按入学年序固定，不随当前年份漂移。"""
        for year in (2025, 2026, 2027):
            plan = build_plan(2025, '2024', current_year=year)
            assert [p['batch_no'] for p in plan] == [2, 3]

    def test_deferred_minus1_plan(self):
        """推迟注册：三年学平险，第 4 年实习险。"""
        plan = build_plan(2026, '2027', current_year=2026)
        assert [(p['batch_no'], p['insurance_type'], p['stage']) for p in plan] == [
            (1, 'xueping', 'first'),
            (2, 'xueping', 'renewal'),
            (3, 'xueping', 'renewal'),
            (4, 'intern', 'push'),
        ]

    def test_deferred_minus2_plan(self):
        plan = build_plan(2026, '2028', current_year=2026)
        assert [(p['batch_no'], p['insurance_type']) for p in plan] == [
            (1, 'xueping'), (2, 'xueping'), (3, 'xueping'), (4, 'xueping'), (5, 'intern'),
        ]

    def test_current_insurance_batch_legacy_compatible(self):
        assert current_insurance_batch('2026', 2026, 2026) == 1
        assert current_insurance_batch('2025', 2025, 2026) == 2
        assert current_insurance_batch('2024', 2024, 2026) == 3
        assert current_insurance_batch('2026', 2026, 2029) is None   # 超出三年制
        assert current_insurance_batch('', 2026, 2026) is None
        assert current_insurance_batch(None, None, 2026) is None


# ============================================================
# 状态流转
# ============================================================
class TestTransitions:
    def test_approve(self):
        conn = _db()
        sid = _add_student(conn)
        rid = _add_record(conn, sid, status=S.PENDING_APPROVE)

        rec = ins.approve(conn, rid, operator='admin')
        assert rec['status'] == S.APPROVED
        assert rec['approved_by'] == 'admin'
        assert rec['approved_at']

    def test_approve_invalid_status_raises(self):
        conn = _db()
        sid = _add_student(conn)
        rid = _add_record(conn, sid, status=S.DONE)
        with pytest.raises(InsuranceTransitionError):
            ins.approve(conn, rid)

    def test_reject(self):
        conn = _db()
        sid = _add_student(conn)
        rid = _add_record(conn, sid, status=S.PENDING_APPROVE)

        assert ins.reject(conn, rid)['status'] == S.EXEMPT

    def test_record_payment_normalizes_date(self):
        """缴费只写缴费日期，不改变保险状态。"""
        conn = _db()
        sid = _add_student(conn)
        rid = _add_record(conn, sid, status=S.APPROVED, batch_no=2,
                          school_year='2027-2028')

        rec = ins.record_payment(conn, rid, '2026/9/1')
        assert rec['status'] == S.APPROVED
        assert rec['pay_date'] == '2026-09-01'

    def test_record_payment_invalid_date_raises(self):
        conn = _db()
        sid = _add_student(conn)
        rid = _add_record(conn, sid, status=S.APPROVED, batch_no=2)
        with pytest.raises(InsuranceTransitionError):
            ins.record_payment(conn, rid, '2026-02-30')

    def test_fill_policy(self):
        conn = _db()
        sid = _add_student(conn)
        rid = _add_record(conn, sid, status=S.EXPORTED)

        rec = ins.fill_policy(conn, rid, 'P-001', '2027/8/31')
        assert rec['status'] == S.DONE
        assert rec['policy_no'] == 'P-001'
        assert rec['expiry_date'] == '2027-08-31'

    def test_fill_policy_from_paid(self):
        """第二年续保：缴费后补录保单与到期日 → done。"""
        conn = _db()
        sid = _add_student(conn)
        rid = _add_record(conn, sid, status=S.PAID, batch_no=2, school_year='2027-2028')

        assert ins.fill_policy(conn, rid, 'P-002', '2028-08-31')['status'] == S.DONE

    def test_fill_policy_requires_policy_no(self):
        conn = _db()
        sid = _add_student(conn)
        rid = _add_record(conn, sid, status=S.EXPORTED)
        with pytest.raises(InsuranceTransitionError):
            ins.fill_policy(conn, rid, '', '2027-08-31')

    def test_fill_policy_rejects_intern(self):
        conn = _db()
        sid = _add_student(conn)
        rid = _add_record(conn, sid, insurance_type='intern', status=S.PAID, batch_no=3)
        with pytest.raises(InsuranceTransitionError):
            ins.fill_policy(conn, rid, 'P-003', '2028-08-31')

    def test_export_first(self):
        conn = _db()
        sid = _add_student(conn)
        rid = _add_record(conn, sid, status=S.APPROVED)

        batch_id = ins.export_records(conn, [rid], action='first', operator='clerk')
        rec = ins.get_record(conn, rid)
        assert rec['status'] == S.EXPORTED
        assert rec['export_batch_id'] == batch_id

        batch = dict(conn.execute(
            'SELECT * FROM insurance_export_batches WHERE id = ?', (batch_id,)
        ).fetchone())
        assert batch['action'] == 'first'
        assert batch['student_count'] == 1
        assert batch['created_by'] == 'clerk'

    def test_export_wrong_type_raises(self):
        conn = _db()
        sid = _add_student(conn)
        rid = _add_record(conn, sid, insurance_type='intern', status=S.APPROVED, batch_no=3)
        with pytest.raises(InsuranceTransitionError):
            ins.export_records(conn, [rid], action='first')

    def test_export_requires_approved(self):
        conn = _db()
        sid = _add_student(conn)
        rid = _add_record(conn, sid, status=S.PENDING_APPROVE)
        with pytest.raises(InsuranceTransitionError):
            ins.export_records(conn, [rid], action='first')

    def test_complete_intern_sets_done_directly(self):
        """实习险无保单信息，导出即办结，不经过 exported 状态。"""
        conn = _db()
        sid = _add_student(conn, grade='2024', enrollment_year=2024)
        rid = _add_record(conn, sid, insurance_type='intern', status=S.APPROVED,
                          batch_no=3, school_year='2028-2029')

        ins.complete_intern(conn, [rid], operator='clerk')
        rec = ins.get_record(conn, rid)
        assert rec['status'] == S.DONE
        assert rec['policy_no'] == ''
        assert rec['expiry_date'] == ''

    def test_revert_export(self):
        conn = _db()
        sid = _add_student(conn)
        rid = _add_record(conn, sid, status=S.APPROVED)
        batch_id = ins.export_records(conn, [rid], action='first')

        assert ins.revert_export(conn, batch_id) == 1
        rec = ins.get_record(conn, rid)
        assert rec['status'] == S.APPROVED
        assert rec['export_batch_id'] == ''

        with pytest.raises(InsuranceTransitionError):
            ins.revert_export(conn, batch_id)

    def test_logs_written(self):
        conn = _db()
        sid = _add_student(conn)
        rid = _add_record(conn, sid, status=S.PENDING_APPROVE)

        ins.approve(conn, rid, operator='boss')
        logs = [dict(r) for r in conn.execute(
            'SELECT * FROM insurance_operation_logs WHERE record_id = ? ORDER BY id', (rid,)
        )]
        assert len(logs) == 1
        assert logs[0]['action'] == 'approve'
        assert logs[0]['from_status'] == S.PENDING_APPROVE
        assert logs[0]['to_status'] == S.APPROVED
        assert logs[0]['operator'] == 'boss'


# ============================================================
# 快照投影（旧字段兼容）
# ============================================================
class TestSnapshot:
    def test_approve_updates_legacy_flag(self):
        conn = _db()
        sid = _add_student(conn)
        rid = _add_record(conn, sid, status=S.PENDING_APPROVE)

        ins.approve(conn, rid)
        assert _student(conn, sid)['supervisor_flag'] == config.InsuranceStatus.PENDING_XUEPING

    def test_fill_policy_writes_legacy_fields(self):
        conn = _db()
        sid = _add_student(conn)
        rid = _add_record(conn, sid, status=S.EXPORTED, pay_date='2026-09-01')

        ins.fill_policy(conn, rid, 'P-100', '2027-08-31', premium='120')
        s = _student(conn, sid)
        assert s['student_insurance_no'] == 'P-100'
        assert s['student_insurance_pay_date'] == '2026-09-01'
        assert s['student_insurance_expiry'] == '2027-08-31'
        assert s['insurance_premium'] == '120'
        assert s['supervisor_flag'] == config.InsuranceStatus.done_xueping(2026)

    def test_intern_done_updates_legacy_flag(self):
        conn = _db()
        sid = _add_student(conn, grade='2024', enrollment_year=2024)
        rid = _add_record(conn, sid, insurance_type='intern', status=S.APPROVED,
                          batch_no=3, school_year='2028-2029', pay_date='2028-09-01')

        ins.complete_intern(conn, [rid])
        s = _student(conn, sid)
        assert s['internship_insurance_pay_date'] == '2028-09-01'
        assert s['supervisor_flag'] == config.InsuranceStatus.done_intern(2028)

    def test_new_blank_batch_keeps_legacy_fields(self):
        """续保新建空白批次记录时，不清空已有保单/缴费/到期字段。"""
        conn = _db()
        sid = _add_student(conn)
        _add_record(conn, sid, status=S.DONE, batch_no=1,
                    pay_date='2025-09-01', policy_no='P-OLD',
                    expiry_date='2026-10-01')
        ins.sync_student_snapshot(conn, sid)

        ins.start_renewal(conn, sid, current_year=2026)
        s = _student(conn, sid)
        assert s['student_insurance_no'] == 'P-OLD'
        assert s['student_insurance_pay_date'] == '2025-09-01'
        assert s['student_insurance_expiry'] == '2026-10-01'


# ============================================================
# 初始化导入同步写记录
# ============================================================
class TestApplyImportRecord:
    @staticmethod
    def _record(conn, student_id, batch_no, insurance_type='xueping'):
        row = conn.execute(
            '''SELECT * FROM insurance_records
               WHERE student_id = ? AND batch_no = ? AND insurance_type = ?''',
            (student_id, batch_no, insurance_type)).fetchone()
        return dict(row) if row else None

    def test_creates_xueping_done(self):
        conn = _db()
        sid = _add_student(conn)
        action = ins.apply_import_record(
            conn, sid, 'X', 1, 'xueping', flag='2026年度学平险办结',
            pay_date='2026/9/1', policy_no='P-1', expiry_date='2027-08-31')
        assert action == 'created'
        rec = self._record(conn, sid, 1)
        assert rec['status'] == S.DONE
        assert rec['pay_date'] == '2026-09-01'
        assert rec['policy_no'] == 'P-1'

    def test_renewal_pending_goes_approved(self):
        """续保批次无独立审批：待办结标记直接落 approved（待导出）。"""
        conn = _db()
        sid = _add_student(conn)
        action = ins.apply_import_record(
            conn, sid, 'X', 2, 'xueping', flag='按计划办理学平险')
        assert action == 'created'
        assert self._record(conn, sid, 2)['status'] == S.APPROVED

    def test_first_batch_pending_goes_pending_approve(self):
        conn = _db()
        sid = _add_student(conn)
        ins.apply_import_record(conn, sid, 'X', 1, 'xueping',
                                flag='按计划办理学平险')
        assert self._record(conn, sid, 1)['status'] == S.PENDING_APPROVE

    def test_intern_done(self):
        conn = _db()
        sid = _add_student(conn, grade='2024', enrollment_year=2024)
        action = ins.apply_import_record(
            conn, sid, 'X', 3, 'intern', flag='2025年度实习就业险办结',
            pay_date='2025-09-01')
        assert action == 'created'
        rec = self._record(conn, sid, 3, 'intern')
        assert rec['status'] == S.DONE
        assert rec['pay_date'] == '2025-09-01'

    def test_blank_row_skipped(self):
        conn = _db()
        sid = _add_student(conn)
        assert ins.apply_import_record(conn, sid, 'X', 1, 'xueping') is None
        assert self._record(conn, sid, 1) is None

    def test_updates_existing_batch(self):
        conn = _db()
        sid = _add_student(conn)
        _add_record(conn, sid, status=S.PENDING_APPROVE, batch_no=1)
        action = ins.apply_import_record(
            conn, sid, 'X', 1, 'xueping', flag='2026年度学平险办结',
            pay_date='2026-09-01', policy_no='P-9', expiry_date='2027-08-31')
        assert action == 'updated'
        rec = self._record(conn, sid, 1)
        assert rec['status'] == S.DONE
        assert rec['policy_no'] == 'P-9'


# ============================================================
# 手动推送至实习办结池
# ============================================================
class TestPushToIntern:
    def test_push_normal_creates_batch3(self):
        conn = _db()
        sid = _add_student(conn, grade='2024', enrollment_year=2024)

        result = ins.push_to_intern(conn, [sid], current_year=2026)
        assert result == {'created': 1, 'skipped': 0}

        rec = dict(conn.execute(
            "SELECT * FROM insurance_records WHERE student_id = ? AND insurance_type = 'intern'",
            (sid,)
        ).fetchone())
        assert rec['status'] == S.PENDING_APPROVE
        assert rec['batch_no'] == 3
        assert rec['school_year'] == '2026-2027'

    def test_push_transfer_creates_batch3(self):
        """转学生（2026 入学 / 2025 级）首保为第 2 年，实习险顺延到第 3 年。"""
        conn = _db()
        sid = _add_student(conn, grade='2025', enrollment_year=2026)

        ins.push_to_intern(conn, [sid], current_year=2026)
        rec = dict(conn.execute(
            "SELECT * FROM insurance_records WHERE student_id = ? AND insurance_type = 'intern'",
            (sid,)
        ).fetchone())
        assert rec['batch_no'] == 3

    def test_push_deferred_creates_batch4(self):
        conn = _db()
        sid = _add_student(conn, grade='2027', enrollment_year=2026)

        ins.push_to_intern(conn, [sid], current_year=2026)
        rec = dict(conn.execute(
            "SELECT * FROM insurance_records WHERE student_id = ? AND insurance_type = 'intern'",
            (sid,)
        ).fetchone())
        assert rec['batch_no'] == 4

    def test_push_duplicate_skipped(self):
        conn = _db()
        sid = _add_student(conn, grade='2024', enrollment_year=2024)
        ins.push_to_intern(conn, [sid], current_year=2026)

        assert ins.push_to_intern(conn, [sid], current_year=2026) == {'created': 0, 'skipped': 1}

    def test_push_missing_student_skipped(self):
        conn = _db()
        assert ins.push_to_intern(conn, [9999], current_year=2026) == {'created': 0, 'skipped': 1}
