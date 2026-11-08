# -*- coding: utf-8 -*-
"""保险初始化导入 V2：按学年/批次精确 upsert、仅补空、预览 diff、整批撤销。"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from openpyxl import Workbook

import config
import models
from models import insurance as ins
from services import insurance_service as svc


def _xlsx(headers, rows):
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _add_student(conn, id_card, grade, enrollment_year, name='测试'):
    cur = conn.execute(
        'INSERT INTO students (student_no, id_card, name, grade, enrollment_year, school) '
        'VALUES (?, ?, ?, ?, ?, 1)',
        (id_card, id_card, name, grade, enrollment_year))
    conn.commit()
    return cur.lastrowid


def _records(conn, student_id):
    return [dict(r) for r in conn.execute(
        'SELECT * FROM insurance_records WHERE student_id = ? ORDER BY batch_no',
        (student_id,))]


# ============================================================
# 目标批次解析
# ============================================================
class TestTargetResolve:
    def test_school_year_maps_to_batch(self):
        assert ins.batch_from_school_year('2024-2025', 2024, '2024') == 1
        assert ins.batch_from_school_year('2025-2026', 2024, '2024') == 2
        assert ins.batch_from_school_year('2025/2026', 2024, '2024') == 2
        assert ins.batch_from_school_year('乱写', 2024, '2024') is None

    def test_resolve_supports_history_batch(self):
        """第 3 年学生也能定位到第 1 批，用于补录历史保单。"""
        student = {'enrollment_year': 2024, 'grade': '2024'}
        batch, step, error = svc._resolve_target({'保险学年': '2024-2025'}, student)
        assert (batch, step['insurance_type'], error) == (1, 'xueping', '')

    def test_resolve_rejects_out_of_plan(self):
        student = {'enrollment_year': 2024, 'grade': '2024'}
        batch, step, error = svc._resolve_target({'保险学年': '2030-2031'}, student)
        assert batch is None and '不在该生保险计划内' in error

    def test_resolve_requires_target(self):
        student = {'enrollment_year': 2024, 'grade': '2024'}
        _, _, error = svc._resolve_target({}, student)
        assert '缺少' in error

    def test_type_mismatch_rejected(self):
        student = {'enrollment_year': 2024, 'grade': '2024'}
        batch, step, error = svc._resolve_target(
            {'批次号': '3', '险种': '学平险'}, student)
        assert error == '' and step['insurance_type'] == 'intern'
        _, _, msg = svc._prepare_values(
            {'险种': '学平险', '实习险缴费日期': ''}, student, batch, step)
        assert '不一致' in msg

    def test_inter_year_column_conflict_rejected(self):
        """实习就业险批次不允许填写学平险列。"""
        student = {'enrollment_year': 2024, 'grade': '2024'}
        _, step, _ = svc._resolve_target({'批次号': '3'}, student)
        _, _, error = svc._prepare_values(
            {'学平险保单号': 'P1'}, student, 3, step)
        assert '不能填写' in error


# ============================================================
# 仅补空写入语义
# ============================================================
class TestPartialApply:
    def test_blank_keeps_existing(self):
        conn = sqlite3_conn()
        sid = _add_student(conn, 'A1', '2024', 2024)
        ins.apply_import_record(conn, sid, 'A1', 1, 'xueping', flag='2024年度学平险办结',
                                policy_no='P-1', expiry_date='2025-08-31')
        ins.apply_import_record(conn, sid, 'A1', 1, 'xueping',
                                pay_date='2026-03-01', partial=True)
        rec = ins._record_for_batch(conn, sid, 1, 'xueping')
        assert rec['pay_date'] == '2026-03-01'
        assert rec['policy_no'] == 'P-1'          # 未提供 → 保留
        assert rec['status'] == 'done'            # 无新证据 → 状态不降级

    def test_empty_string_clears(self):
        conn = sqlite3_conn()
        sid = _add_student(conn, 'A2', '2024', 2024)
        ins.apply_import_record(conn, sid, 'A2', 1, 'xueping', flag='2024年度学平险办结',
                                policy_no='P-1', expiry_date='2025-08-31')
        ins.apply_import_record(conn, sid, 'A2', 1, 'xueping', policy_no='', partial=True)
        rec = ins._record_for_batch(conn, sid, 1, 'xueping')
        assert rec['policy_no'] == ''
        assert rec['expiry_date'] == '2025-08-31'

    def test_no_data_creates_nothing(self):
        conn = sqlite3_conn()
        sid = _add_student(conn, 'A3', '2024', 2024)
        assert ins.apply_import_record(conn, sid, 'A3', 1, 'xueping', partial=True) is None
        assert _records(conn, sid) == []

    def test_identical_reimport_is_noop(self):
        """原样回导（值完全一致、无主管标记）不应重算状态或写库。"""
        conn = sqlite3_conn()
        sid = _add_student(conn, 'A4', '2024', 2024)
        ins.apply_import_record(conn, sid, 'A4', 1, 'xueping', policy_no='P-1',
                                pay_date='2024-09-01', expiry_date='2025-08-31')
        conn.execute("UPDATE insurance_records SET status = 'pending_approve' "
                     "WHERE student_id = ?", (sid,))
        conn.commit()
        assert ins.apply_import_record(
            conn, sid, 'A4', 1, 'xueping', policy_no='P-1',
            pay_date='2024-09-01', expiry_date='2025-08-31', partial=True) is None
        assert _records(conn, sid)[0]['status'] == 'pending_approve'


# ============================================================
# 模板导出
# ============================================================
class TestTemplateExport:
    def test_intern_row_keeps_xueping_columns_blank(self, client, db_conn):
        """实习就业险批次只填「实习险缴费日期」，学平险三列必须留空（否则原样回导被拒）。"""
        from openpyxl import load_workbook

        sid = _add_student(db_conn, '440101200601010055', '2023', 2023, '实习生')
        db_conn.execute(
            "INSERT INTO insurance_records (student_id, id_card, school_year, batch_no, "
            "insurance_type, status, pay_date) VALUES (?, ?, '2025-2026', 3, 'intern', 'done', "
            "'2025-10-01')", (sid, '440101200601010055'))
        db_conn.commit()

        with client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'tester'
            sess['role'] = 'admin'
            sess['csrf_token'] = 'x'
        try:
            resp = client.get('/insurance/init/template')
            assert resp.status_code == 200
            ws = load_workbook(io.BytesIO(resp.data))['保险初始化导入']
            header = [c.value for c in ws[1]]
            target = None
            for row in ws.iter_rows(min_row=2, values_only=True):
                data = dict(zip(header, row))
                if data['身份证号'] == '440101200601010055' and str(data['批次号']) == '3':
                    target = data
                    break
            assert target is not None
            assert target['险种'] == '实习就业险'
            assert not target['学平险保单号'] and not target['学平险缴费日期'] \
                and not target['学平险到期时间']
            assert target['实习险缴费日期'] == '2025-10-01'
        finally:
            with client.session_transaction() as sess:
                sess.clear()


# ============================================================
# 解析 / 预览 / 导入 / 撤销（走真实 DB 与 Flask 上下文）
# ============================================================
class TestImportPipeline:
    def test_old_template_rejected(self, app):
        with app.app_context():
            buf = _xlsx(['身份证号', '学平险保单号'], [['X', 'P1']])
            rows, errors = svc.parse_insurance_excel(buf)
            assert rows == []
            assert '旧 6 列模板已停用' in errors[0]

    def test_preview_diff_without_writing(self, app, db_conn):
        sid = _add_student(db_conn, '440101200601010011', '2024', 2024, '张三')
        with app.app_context():
            buf = _xlsx(['身份证号', '保险学年', '学平险保单号', '学平险到期时间'],
                        [['440101200601010011', '2024-2025', 'P-HIST', '2025-08-31']])
            rows, errors = svc.parse_insurance_excel(buf)
            assert errors == []
            result = svc.preview_insurance_import(rows)
            assert result['summary']['created'] == 1
            item = result['preview'][0]
            assert item['batch'] == 1 and item['action'] == 'created'
            assert {c['field'] for c in item['changes']} >= {'保单号', '到期日', '状态'}
        assert _records(db_conn, sid) == []          # 预览已回滚

    def test_import_history_batch_and_revert(self, app, db_conn):
        sid = _add_student(db_conn, '440101200601010022', '2024', 2024, '李四')
        with app.app_context():
            buf = _xlsx(['身份证号', '保险学年', '学平险保单号', '学平险到期时间'],
                        [['440101200601010022', '2024-2025', 'P-HIST', '2025-08-31']])
            rows, _ = svc.parse_insurance_excel(buf)
            progress = {'p1': svc._new_progress(len(rows))}
            svc._run_import(rows, progress, 'p1')
            state = progress['p1']
            assert state['status'] == 'completed' and state['success'] == 1
            batch_id = state['import_batch_id']
            assert batch_id

        recs = _records(db_conn, sid)
        assert len(recs) == 1
        assert (recs[0]['batch_no'], recs[0]['status']) == (1, 'done')
        assert recs[0]['policy_no'] == 'P-HIST'
        student = db_conn.execute(
            'SELECT student_insurance_no, supervisor_flag FROM students WHERE id = ?',
            (sid,)).fetchone()
        assert student['student_insurance_no'] == 'P-HIST'
        assert student['supervisor_flag'] == '2024年度学平险办结'

        with app.app_context():
            conn = models.get_db()
            reverted = ins.revert_import_batch(conn, batch_id)
            conn.commit()
        assert reverted == 1
        assert _records(db_conn, sid) == []
        student = db_conn.execute(
            'SELECT student_insurance_no FROM students WHERE id = ?', (sid,)).fetchone()
        assert (student['student_insurance_no'] or '') == ''

    def test_import_rejects_type_conflict(self, app, db_conn):
        _add_student(db_conn, '440101200601010033', '2024', 2024, '王五')
        with app.app_context():
            buf = _xlsx(['身份证号', '批次号', '学平险保单号'],
                        [['440101200601010033', '3', 'P-SHOULD-NOT']])
            rows, _ = svc.parse_insurance_excel(buf)
            progress = {'p2': svc._new_progress(len(rows))}
            svc._run_import(rows, progress, 'p2')
        assert progress['p2']['success'] == 0
        assert progress['p2']['error'] == 1

    def test_import_only_pay_date_keeps_policy(self, app, db_conn):
        sid = _add_student(db_conn, '440101200601010044', '2024', 2024, '赵六')
        with app.app_context():
            first = _xlsx(['身份证号', '保险学年', '学平险保单号', '学平险到期时间'],
                          [['440101200601010044', '2024-2025', 'P-KEEP', '2025-08-31']])
            rows, _ = svc.parse_insurance_excel(first)
            svc._run_import(rows, {'p3': svc._new_progress(len(rows))}, 'p3')

            second = _xlsx(['身份证号', '保险学年', '学平险缴费日期'],
                           [['440101200601010044', '2024-2025', '2025-03-01']])
            rows, _ = svc.parse_insurance_excel(second)
            svc._run_import(rows, {'p4': svc._new_progress(len(rows))}, 'p4')

        rec = _records(db_conn, sid)[0]
        assert rec['policy_no'] == 'P-KEEP'
        assert rec['expiry_date'] == '2025-08-31'
        assert rec['pay_date'] == '2025-03-01'
        assert rec['status'] == 'done'


# ------------------------------------------------------------
# 内存库（域层用例用，轻量 schema）
# ------------------------------------------------------------
_DOMAIN_SCHEMA = """
CREATE TABLE students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_no TEXT,
    id_card TEXT, name TEXT, grade TEXT, enrollment_year INTEGER, school INTEGER DEFAULT 1,
    is_deleted INTEGER DEFAULT 0,
    registration_status TEXT DEFAULT '正式学籍',
    attendance_status TEXT DEFAULT '在校',
    student_insurance_no TEXT DEFAULT '', student_insurance_pay_date TEXT DEFAULT '',
    student_insurance_expiry TEXT DEFAULT '', insurance_premium TEXT DEFAULT '',
    internship_insurance_pay_date TEXT DEFAULT '', supervisor_flag TEXT DEFAULT ''
);
CREATE TABLE schema_version (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    version INTEGER NOT NULL DEFAULT 0,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO schema_version (id, version) VALUES (1, 2);
"""


def sqlite3_conn():
    import sqlite3
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript(_DOMAIN_SCHEMA)
    ins.migrate_insurance_v3(conn)
    conn.commit()
    return conn
