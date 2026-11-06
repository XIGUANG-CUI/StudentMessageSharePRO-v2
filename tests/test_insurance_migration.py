# -*- coding: utf-8 -*-
"""保险模块 P0：建表与历史回填迁移的单元测试。

覆盖：
  - 日期规范化（斜杠 / 点 / 中文 / YYYYMMDD / Excel 序列号 / 非法日期）
  - 学生分类与批次计算（普通 / 转学生 / 推迟注册）
  - 三张新表与索引创建、schema_version 推进
  - 现有 supervisor_flag 六种状态的回填映射
  - 软删除学生跳过、幂等执行
"""
import os
import sqlite3
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import models.insurance as ins


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
    insurance_premium TEXT DEFAULT '静默',
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


def _make_db(students=()):
    """创建只含 students + schema_version 的内存库，并插入待迁移学生。"""
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript(STUDENT_SCHEMA)
    for s in students:
        conn.execute(
            '''
            INSERT INTO students
                (id_card, name, grade, enrollment_year, is_deleted,
                 student_insurance_no, student_insurance_pay_date,
                 student_insurance_expiry, insurance_premium,
                 internship_insurance_pay_date, supervisor_flag)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                s.get('id_card', ''),
                s.get('name', s.get('id_card', '')),
                s.get('grade', '2026'),
                s.get('enrollment_year', 2026),
                s.get('is_deleted', 0),
                s.get('no', ''),
                s.get('pay', ''),
                s.get('expiry', ''),
                s.get('premium', '静默'),
                s.get('intern_pay', ''),
                s.get('flag', ''),
            )
        )
    conn.commit()
    return conn


def _records(conn, insurance_type=None):
    sql = 'SELECT * FROM insurance_records'
    params = ()
    if insurance_type:
        sql += ' WHERE insurance_type = ?'
        params = (insurance_type,)
    sql += ' ORDER BY student_id, insurance_type'
    return [dict(r) for r in conn.execute(sql, params)]


def _record(conn, student_id, insurance_type):
    row = conn.execute(
        'SELECT * FROM insurance_records WHERE student_id = ? AND insurance_type = ?',
        (student_id, insurance_type)
    ).fetchone()
    return dict(row) if row else None


# ============================================================
# 日期规范化
# ============================================================
class TestNormalizeDate:
    @pytest.mark.parametrize('raw, expected', [
        ('2026-03-01', '2026-03-01'),
        ('2026/3/1', '2026-03-01'),
        ('2026.3.1', '2026-03-01'),
        ('2026年3月1日', '2026-03-01'),
        ('20260301', '2026-03-01'),
        (date(2026, 3, 1), '2026-03-01'),
        (datetime(2026, 3, 1, 8, 30), '2026-03-01'),
    ])
    def test_common_formats(self, raw, expected):
        assert ins.normalize_insurance_date(raw) == expected

    def test_excel_serial(self):
        """Excel 序列号（int / 纯数字字符串）应还原为日期。"""
        assert ins.normalize_insurance_date(45893) == '2025-08-24'
        assert ins.normalize_insurance_date('45893') == '2025-08-24'

    @pytest.mark.parametrize('raw', ['', None, '新生默认首保', '静默', '无', '-'])
    def test_placeholder_and_empty(self, raw):
        assert ins.normalize_insurance_date(raw) == ''

    def test_invalid_date_rejected(self):
        """溢出日期（2 月 30 日）与非日期文本应返回空串。"""
        assert ins.normalize_insurance_date('2026-02-30') == ''
        assert ins.normalize_insurance_date('待定') == ''


# ============================================================
# 学生分类与批次
# ============================================================
class TestStudentPlan:
    def test_offset_kind(self):
        assert ins.student_offset(2026, '2026') == 0          # 普通
        assert ins.student_offset(2026, '2025') == 1          # 转学生
        assert ins.student_offset(2026, '2027') == -1         # 推迟注册

    def test_offset_with_missing_values(self):
        assert ins.student_offset(0, '2026') == 0
        assert ins.student_offset(2026, '') == 0
        assert ins.student_offset(None, None) == 0

    def test_start_year_defers_to_enrollment(self):
        """推迟注册不能用年级当起始年，否则 batch 会变 0 或负数。"""
        assert ins.start_year(2026, '2026') == 2026
        assert ins.start_year(2026, '2025') == 2025
        assert ins.start_year(2026, '2027') == 2026

    def test_batch_now(self):
        assert ins.batch_now(2026, '2026', current_year=2026) == 1
        assert ins.batch_now(2025, '2025', current_year=2026) == 2
        assert ins.batch_now(2024, '2024', current_year=2026) == 3
        assert ins.batch_now(2026, '2027', current_year=2026) == 1   # 推迟注册
        assert ins.batch_now(2026, '2025', current_year=2026) == 2   # 转学生

    def test_xueping_batch(self):
        assert ins.xueping_batch(0, 1) == 1
        assert ins.xueping_batch(0, 3) == 2          # 普通生最多两年学平险
        assert ins.xueping_batch(1, 2) == 2          # 转学生首保即第 N 年
        assert ins.xueping_batch(-1, 3) == 3         # 推迟注册三年学平险

    def test_intern_batch(self):
        assert ins.intern_batch(0, 1) == 3           # 普通第 3 年
        assert ins.intern_batch(1, 2) == 3           # 转学生顺延一年
        assert ins.intern_batch(-1, 1) == 4          # 推迟注册第 4 年
        assert ins.intern_batch(-2, 1) == 5


# ============================================================
# 建表与版本
# ============================================================
class TestSchema:
    def test_creates_tables_and_indexes(self):
        conn = _make_db()
        stats = ins.migrate_insurance_v3(conn)
        conn.commit()

        assert stats['students'] == 0
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'insurance%'"
        )}
        assert {'insurance_records', 'insurance_export_batches', 'insurance_operation_logs'} <= tables

        indexes = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_ir%'"
        )}
        assert {'idx_ir_status', 'idx_ir_expiry', 'idx_ir_student', 'idx_ir_type'} <= indexes

        version = conn.execute('SELECT version FROM schema_version WHERE id = 1').fetchone()[0]
        assert version == ins.SCHEMA_VERSION == 3

    def test_version_bumped_from_2(self):
        conn = _make_db()
        conn.execute('UPDATE schema_version SET version = 2 WHERE id = 1')
        conn.commit()
        ins.migrate_insurance_v3(conn)
        assert conn.execute('SELECT version FROM schema_version WHERE id = 1').fetchone()[0] == 3


# ============================================================
# 历史回填映射
# ============================================================
class TestBackfill:
    def test_normal_new_student(self):
        conn = _make_db([{'id_card': 'A', 'grade': '2026', 'enrollment_year': 2026}])
        ins.migrate_insurance_v3(conn)

        rec = _record(conn, 1, 'xueping')
        assert rec['status'] == ins.InsuranceStatus.PENDING_APPROVE
        assert rec['batch_no'] == 1
        assert rec['school_year'] == '2026-2027'

    def test_xueping_done_with_mixed_dates(self):
        """学平险办结：斜杠日期与 Excel 序列号都应被规范化。"""
        conn = _make_db([{
            'id_card': 'B', 'grade': '2025', 'enrollment_year': 2025,
            'no': 'P-001', 'pay': '2025/9/1', 'expiry': 45893,
            'premium': '100', 'flag': '2025年度学平险办结',
        }])
        ins.migrate_insurance_v3(conn)

        rec = _record(conn, 1, 'xueping')
        assert rec['status'] == ins.InsuranceStatus.DONE
        assert rec['pay_date'] == '2025-09-01'
        assert rec['expiry_date'] == '2025-08-24'
        assert rec['policy_no'] == 'P-001'
        assert rec['premium'] == '100'
        assert rec['batch_no'] == 1

    def test_done_without_expiry_maps_to_exported(self):
        conn = _make_db([{
            'id_card': 'C', 'grade': '2026', 'enrollment_year': 2026,
            'no': 'P-002', 'pay': '2026-09-01', 'flag': '2026年度学平险办结',
        }])
        ins.migrate_insurance_v3(conn)

        assert _record(conn, 1, 'xueping')['status'] == ins.InsuranceStatus.EXPORTED

    def test_intern_done_implies_xueping_done(self):
        conn = _make_db([{
            'id_card': 'D', 'grade': '2024', 'enrollment_year': 2024,
            'no': 'P-003', 'pay': '2024-09-01', 'expiry': '2025-08-31',
            'intern_pay': '2025-09-01', 'flag': '2025年度实习就业险办结',
        }])
        ins.migrate_insurance_v3(conn)

        xp = _record(conn, 1, 'xueping')
        it = _record(conn, 1, 'intern')
        assert xp['status'] == ins.InsuranceStatus.DONE
        assert xp['batch_no'] == 2          # 普通生最后一年学平险是第 2 年
        assert it['status'] == ins.InsuranceStatus.DONE
        assert it['batch_no'] == 3
        assert it['policy_no'] == ''        # 实习险不存保单号
        assert it['expiry_date'] == ''

    def test_transfer_first_insurance_is_batch_2(self):
        """转学生（入学年 2026 / 年级 2025）首保即本校第 2 年学平险。"""
        conn = _make_db([{'id_card': 'E', 'grade': '2025', 'enrollment_year': 2026}])
        ins.migrate_insurance_v3(conn)

        rec = _record(conn, 1, 'xueping')
        assert rec['batch_no'] == 2
        assert rec['school_year'] == '2026-2027'

    def test_deferred_student_batch_1(self):
        """推迟注册（入学年 2026 / 年级 2027）必须落在第 1 年，不能出现 batch=0。"""
        conn = _make_db([{'id_card': 'F', 'grade': '2027', 'enrollment_year': 2026}])
        ins.migrate_insurance_v3(conn)

        rec = _record(conn, 1, 'xueping')
        assert rec['batch_no'] == 1
        assert rec['school_year'] == '2026-2027'

    def test_sentinel_pay_date_cleared(self):
        conn = _make_db([{
            'id_card': 'G', 'grade': '2026', 'enrollment_year': 2026,
            'pay': '新生默认首保', 'flag': '按计划办理学平险',
        }])
        ins.migrate_insurance_v3(conn)

        rec = _record(conn, 1, 'xueping')
        assert rec['status'] == ins.InsuranceStatus.PENDING_APPROVE
        assert rec['pay_date'] == ''

    def test_exempt(self):
        conn = _make_db([{
            'id_card': 'H', 'grade': '2026', 'enrollment_year': 2026,
            'flag': '不参保',
        }])
        ins.migrate_insurance_v3(conn)

        assert _record(conn, 1, 'xueping')['status'] == ins.InsuranceStatus.EXEMPT

    def test_pending_intern(self):
        conn = _make_db([{
            'id_card': 'I', 'grade': '2024', 'enrollment_year': 2024,
            'no': 'P-004', 'pay': '2024-09-01', 'expiry': '2026-08-31',
            'flag': '按计划办理实习就业险',
        }])
        ins.migrate_insurance_v3(conn)

        assert _record(conn, 1, 'xueping')['status'] == ins.InsuranceStatus.DONE
        it = _record(conn, 1, 'intern')
        assert it['status'] == ins.InsuranceStatus.APPROVED
        assert it['batch_no'] == 3

    def test_premium_sentinel_cleared(self):
        conn = _make_db([{'id_card': 'J', 'grade': '2026', 'enrollment_year': 2026}])
        ins.migrate_insurance_v3(conn)

        assert _record(conn, 1, 'xueping')['premium'] == ''

    def test_soft_deleted_student_skipped(self):
        conn = _make_db([
            {'id_card': 'K', 'grade': '2026', 'enrollment_year': 2026, 'is_deleted': 1},
            {'id_card': 'L', 'grade': '2026', 'enrollment_year': 2026},
        ])
        stats = ins.migrate_insurance_v3(conn)

        assert stats['students'] == 1
        assert conn.execute('SELECT COUNT(1) FROM insurance_records').fetchone()[0] == 1
        assert _record(conn, 1, 'xueping') is None
        assert _record(conn, 2, 'xueping') is not None

    def test_idempotent(self):
        conn = _make_db([{'id_card': 'M', 'grade': '2026', 'enrollment_year': 2026}])
        ins.migrate_insurance_v3(conn)
        total = conn.execute('SELECT COUNT(1) FROM insurance_records').fetchone()[0]

        second = ins.migrate_insurance_v3(conn)
        assert second is None          # 表非空时跳过回填
        assert conn.execute('SELECT COUNT(1) FROM insurance_records').fetchone()[0] == total

    def test_empty_students(self):
        conn = _make_db()
        stats = ins.migrate_insurance_v3(conn)
        assert stats == {
            'students': 0, 'xueping': 0, 'intern': 0,
            'skipped': 0, 'unparsed_date': 0,
        }
