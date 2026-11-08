"""保险模块领域层：表结构、历史回填、计划引擎与状态流转。

P0：建表 + 一次性回填，不改动现有页面/接口；旧字段作为当前快照双轨运行。
P1：统一批次/计划/状态流转/日志/快照投影，收敛各页面重复的批次计算。

数据模型：
  insurance_records          每生 × 批次 × 险种一条记录
  insurance_export_batches   导出批次留痕（导出即业务动作，可撤销）
  insurance_operation_logs   状态流转日志（撤销依据）
"""
import json
import logging
import re
import sqlite3
import uuid
from datetime import date, datetime, timedelta

import config

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 3

# ========== 险种 ==========
INSURANCE_TYPE_XUEPING = 'xueping'
INSURANCE_TYPE_INTERN = 'intern'


class InsuranceStatus:
    """保险记录状态（存储值使用英文枚举，杜绝中文串拼写漂移）。"""
    PENDING_APPROVE = 'pending_approve'   # 待审批
    PENDING_RENEWAL = 'pending_renewal'   # 已推送待续保（等当前保单到期）
    APPROVED = 'approved'                 # 已审批待导出
    EXPORTED = 'exported'                 # 已办结·待补保单（导出后）
    PENDING_PAY = 'pending_pay'           # 待缴费
    PAID = 'paid'                         # 已缴费待办结
    DONE = 'done'                         # 已办结
    EXEMPT = 'exempt'                     # 本期不办理（仅终结该批次）

    ALL = (PENDING_APPROVE, PENDING_RENEWAL, APPROVED, EXPORTED, PENDING_PAY,
           PAID, DONE, EXEMPT)


# 学平险「终态」：已办结或放弃本期。
# 放弃只终结该批次，学生后续批次仍按计划自动投保，因此两者都视为该批次已了结。
XUEPING_TERMINAL_STATUSES = (InsuranceStatus.DONE, InsuranceStatus.EXEMPT)


# 推迟注册的学生：按 offset（入学年 − 年级）配置学平险年数与实习险年份。
# 未配置的 offset 走兜底：学平险 = 3 + (-offset) 年，实习险 = 4 + (-offset) 年。
DEFERRED_PLAN = {
    -1: {'xueping_years': 3, 'intern_year': 4},
    -2: {'xueping_years': 4, 'intern_year': 5},
}


INSURANCE_V3_DDL = """
CREATE TABLE IF NOT EXISTS insurance_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id      INTEGER NOT NULL,
    id_card         TEXT    NOT NULL,
    school_year     TEXT    NOT NULL,
    batch_no        INTEGER NOT NULL,
    insurance_type  TEXT    NOT NULL,
    status          TEXT    NOT NULL,
    pay_date        TEXT DEFAULT '',
    premium         TEXT DEFAULT '',
    policy_no       TEXT DEFAULT '',
    expiry_date     TEXT DEFAULT '',
    export_batch_id TEXT DEFAULT '',
    exported_at     TEXT DEFAULT '',
    approved_at     TEXT DEFAULT '',
    approved_by     TEXT DEFAULT '',
    pushed_at       TEXT DEFAULT '',
    completed_at    TEXT DEFAULT '',
    remark          TEXT DEFAULT '',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(student_id, batch_no, insurance_type)
);

CREATE INDEX IF NOT EXISTS idx_ir_status  ON insurance_records(status);
CREATE INDEX IF NOT EXISTS idx_ir_expiry  ON insurance_records(expiry_date);
CREATE INDEX IF NOT EXISTS idx_ir_student ON insurance_records(student_id);
CREATE INDEX IF NOT EXISTS idx_ir_type    ON insurance_records(insurance_type, status);

CREATE TABLE IF NOT EXISTS insurance_export_batches (
    id             TEXT PRIMARY KEY,
    action         TEXT NOT NULL,
    batch_no       INTEGER,
    insurance_type TEXT,
    school_year    TEXT,
    student_count  INTEGER DEFAULT 0,
    record_ids     TEXT DEFAULT '[]',
    file_name      TEXT DEFAULT '',
    created_by     TEXT DEFAULT '',
    reversed_at    TEXT DEFAULT '',
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS insurance_operation_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id       INTEGER NOT NULL,
    student_id      INTEGER NOT NULL,
    action          TEXT NOT NULL,
    from_status     TEXT DEFAULT '',
    to_status       TEXT DEFAULT '',
    payload         TEXT DEFAULT '',
    export_batch_id TEXT DEFAULT '',
    operator        TEXT DEFAULT '',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_iol_record
    ON insurance_operation_logs(record_id);
CREATE INDEX IF NOT EXISTS idx_iol_student
    ON insurance_operation_logs(student_id);

CREATE TABLE IF NOT EXISTS insurance_import_batches (
    id          TEXT PRIMARY KEY,
    file_name   TEXT DEFAULT '',
    operator    TEXT DEFAULT '',
    total_rows  INTEGER DEFAULT 0,
    ok_rows     INTEGER DEFAULT 0,
    reversed_at TEXT DEFAULT '',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS insurance_import_entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id    TEXT NOT NULL,
    record_id   INTEGER DEFAULT 0,
    student_id  INTEGER NOT NULL,
    action      TEXT DEFAULT '',
    before_json TEXT DEFAULT '',
    after_json  TEXT DEFAULT '',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_iie_batch
    ON insurance_import_entries(batch_id);
"""


# ============================================================
# 日期规范化
# ============================================================
_DATE_PATTERN = re.compile(r'^(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?$')


def _safe_date(year, month, day):
    """构造合法日期，非法（如 2 月 30 日）返回空串。"""
    try:
        return date(int(year), int(month), int(day)).strftime('%Y-%m-%d')
    except (TypeError, ValueError):
        return ''


def _serial_to_date(serial):
    """Excel 日期序列号 → YYYY-MM-DD（与前端 (num-25569)*86400000 等价）。"""
    try:
        serial = float(serial)
    except (TypeError, ValueError):
        return ''
    if not 10000 < serial < 100000:   # 约 1927~2137 年，超出视为非法
        return ''
    try:
        return (datetime(1899, 12, 30) + timedelta(days=serial)).strftime('%Y-%m-%d')
    except (OverflowError, ValueError):
        return ''


def normalize_insurance_date(value):
    """把历史数据中的各种日期形态统一为 YYYY-MM-DD。

    兼容：datetime/date、Excel 序列号（int/float/纯数字串）、YYYY-MM-DD、
    YYYY/M/D、YYYY.M.D、YYYY年M月D日、YYYYMMDD。
    无法识别或占位标记（如“新生默认首保”）返回空串。
    """
    if value is None:
        return ''
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d')
    if isinstance(value, date):
        return value.strftime('%Y-%m-%d')
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _serial_to_date(value)

    text = str(value).strip()
    if not text or text in ('新生默认首保', '静默', '无', '-', '/', 'None'):
        return ''

    if text.isdigit():
        if len(text) == 8:                       # YYYYMMDD
            return _safe_date(text[:4], text[4:6], text[6:8])
        return _serial_to_date(text)             # Excel 序列号

    m = _DATE_PATTERN.match(text)
    if m:
        return _safe_date(m.group(1), m.group(2), m.group(3))
    return ''


# ============================================================
# 学生分类与计划（P0 仅迁移所需的最小集合，P1 会在此基础上扩展）
# ============================================================
def _to_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def student_offset(enrollment_year, grade):
    """offset = 入学年 − 年级。0=普通，>=1 转学生，<=-1 推迟注册。"""
    en = _to_int(enrollment_year)
    g = _to_int(grade)
    if not en or not g:
        return 0
    return en - g


def start_year(enrollment_year, grade):
    """学平险第 1 年对应的学年起始年。

    普通/转学生取年级；推迟注册（年级晚于入学年）取入学年，避免 batch 出现 0 或负数。
    """
    en = _to_int(enrollment_year)
    g = _to_int(grade)
    if not g:
        return en
    if not en:
        return g
    return min(g, en)


def batch_now(enrollment_year, grade, current_year=None):
    """当前处于保险计划的第几年（从 1 开始）。"""
    if current_year is None:
        current_year = config.CURRENT_SCHOOL_YEAR
    start = start_year(enrollment_year, grade)
    if not start:
        return 1
    return max(1, current_year - start + 1)


def xueping_batch(offset, batch):
    """学平险批次号（受计划年限约束）。"""
    if offset >= 1:                 # 转学生：首保即本校第 N 年学平险
        return max(1, batch)
    if offset == 0:                 # 普通：第 1、2 年学平险
        return min(max(1, batch), 2)
    years = DEFERRED_PLAN.get(offset, {}).get('xueping_years', 3 + (-offset))
    return min(max(1, batch), years)


def intern_batch(offset, batch):
    """实习就业险批次号。"""
    if offset >= 1:
        return batch + 1
    if offset == 0:
        return 3
    return DEFERRED_PLAN.get(offset, {}).get('intern_year', 4 + (-offset))


def _school_year(start, batch_no):
    year = start + batch_no - 1
    return f'{year}-{year + 1}'


# ============================================================
# 历史回填
# ============================================================
_FLAG_DONE_XUEPING = '学平险办结'
_FLAG_DONE_INTERN = '实习就业险办结'
_FLAG_PENDING_XUEPING = '按计划办理学平险'
_FLAG_PENDING_INTERN = '按计划办理实习就业险'
_FLAG_EXEMPT = '不参保'


def _flag_year(flag):
    m = re.search(r'(\d{4})', flag or '')
    return int(m.group(1)) if m else None


def _rows_as_dicts(cursor, rows):
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row)) for row in rows]


def _map_xueping_status(flag, policy_no, pay_date, expiry,
                        batch_no=None, first_batch=1):
    """把旧主管标记翻译为新状态机状态（续保批次没有独立审批 → approved）。"""
    if flag == _FLAG_EXEMPT:
        return InsuranceStatus.EXEMPT
    if _FLAG_DONE_XUEPING in flag:
        return InsuranceStatus.DONE if expiry else InsuranceStatus.EXPORTED
    if flag == _FLAG_PENDING_XUEPING:
        if batch_no is not None and batch_no > first_batch:
            return InsuranceStatus.APPROVED
        return InsuranceStatus.PENDING_APPROVE
    if _FLAG_DONE_INTERN in flag:
        # 已办结实习险，说明学平险早已办结
        return InsuranceStatus.DONE
    if _FLAG_PENDING_INTERN in flag:
        # 已推进到实习险阶段：学平险应已办结
        if expiry:
            return InsuranceStatus.DONE
        return InsuranceStatus.EXPORTED if (policy_no or pay_date) else InsuranceStatus.PENDING_APPROVE
    return InsuranceStatus.PENDING_APPROVE


def _map_intern_status(flag, pay_date):
    if _FLAG_DONE_INTERN in flag:
        return InsuranceStatus.DONE
    if flag == _FLAG_PENDING_INTERN:
        # 确认办理实习就业险 = 已审批待导出（缴费与状态解耦）
        return InsuranceStatus.APPROVED
    return InsuranceStatus.PENDING_APPROVE


def _insert_record(cursor, student_id, id_card, school_year, batch_no,
                   insurance_type, status, pay_date='', premium='',
                   policy_no='', expiry_date='', remark=''):
    cursor.execute(
        '''
        INSERT OR IGNORE INTO insurance_records
            (student_id, id_card, school_year, batch_no, insurance_type, status,
             pay_date, premium, policy_no, expiry_date, remark)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (student_id, id_card or '', school_year, batch_no, insurance_type, status,
         pay_date or '', premium or '', policy_no or '', expiry_date or '', remark or '')
    )
    return cursor.rowcount > 0


def backfill_insurance_records(cursor, current_year=None):
    """把 students 表的 9 个保险字段翻译为 insurance_records 记录。

    仅在 insurance_records 为空时调用。返回统计字典，便于日志与验证。
    """
    if current_year is None:
        current_year = config.CURRENT_SCHOOL_YEAR

    rows = cursor.execute(
        '''
        SELECT id, id_card, grade, enrollment_year,
               student_insurance_no, student_insurance_pay_date,
               student_insurance_expiry, insurance_premium,
               internship_insurance_pay_date, supervisor_flag
        FROM students
        WHERE COALESCE(is_deleted, 0) = 0
        '''
    ).fetchall()
    students = _rows_as_dicts(cursor, rows)

    stats = {
        'students': len(students),
        'xueping': 0,
        'intern': 0,
        'skipped': 0,
        'unparsed_date': 0,
    }

    for s in students:
        flag = (s.get('supervisor_flag') or '').strip()
        policy_no = (s.get('student_insurance_no') or '').strip()
        raw_pay_xp = s.get('student_insurance_pay_date')
        raw_expiry = s.get('student_insurance_expiry')
        raw_pay_intern = s.get('internship_insurance_pay_date')

        pay_xp = normalize_insurance_date(raw_pay_xp)
        expiry = normalize_insurance_date(raw_expiry)
        pay_intern = normalize_insurance_date(raw_pay_intern)
        if any(v and not normalize_insurance_date(v) for v in
               (raw_pay_xp, raw_expiry, raw_pay_intern)):
            stats['unparsed_date'] += 1

        premium = (s.get('insurance_premium') or '').strip()
        if premium == '静默':
            premium = ''

        offset = student_offset(s.get('enrollment_year'), s.get('grade'))
        start = start_year(s.get('enrollment_year'), s.get('grade'))
        batch = batch_now(s.get('enrollment_year'), s.get('grade'), current_year)

        has_xp = (
            bool(policy_no or pay_xp or expiry)
            or flag == ''
            or flag == _FLAG_EXEMPT
            or '学平险' in flag
        )
        if has_xp:
            plan = build_plan(s.get('enrollment_year'), s.get('grade'), current_year)
            first_batch = plan[0]['batch_no'] if plan else 1
            flag_yr = _flag_year(flag) if _FLAG_DONE_XUEPING in flag else None
            if flag_yr:
                xp_batch = max(1, flag_yr - start + 1)
            else:
                xp_batch = xueping_batch(offset, batch)
            xp_status = _map_xueping_status(flag, policy_no, pay_xp, expiry,
                                            xp_batch, first_batch)
            if _insert_record(
                cursor, s['id'], s.get('id_card'),
                _school_year(start, xp_batch), xp_batch,
                INSURANCE_TYPE_XUEPING, xp_status,
                pay_date=pay_xp, premium=premium,
                policy_no=policy_no, expiry_date=expiry,
                remark='migrated'
            ):
                stats['xueping'] += 1

        has_intern = bool(pay_intern) or '实习就业险' in flag
        if has_intern:
            i_status = _map_intern_status(flag, pay_intern)
            i_batch = intern_batch(offset, batch)
            if _insert_record(
                cursor, s['id'], s.get('id_card'),
                _school_year(start, i_batch), i_batch,
                INSURANCE_TYPE_INTERN, i_status,
                pay_date=pay_intern,
                remark='migrated'
            ):
                stats['intern'] += 1

        if not has_xp and not has_intern:
            stats['skipped'] += 1

    return stats


def _record_for_batch(conn, student_id, batch_no, insurance_type):
    cur = conn.execute(
        '''SELECT * FROM insurance_records
           WHERE student_id = ? AND batch_no = ? AND insurance_type = ?
           ORDER BY id DESC LIMIT 1''',
        (student_id, batch_no, insurance_type)
    )
    return _row_to_dict(cur, cur.fetchone())


def batch_from_school_year(school_year, enrollment_year, grade):
    """把「2024-2025」学年解析为批次号；格式非法或早于学制起点返回 None。"""
    m = re.match(r'^\s*(\d{4})\s*[-~—－/]\s*(\d{4})\s*$', str(school_year or ''))
    if not m:
        return None
    start = start_year(enrollment_year, grade)
    if not start:
        return None
    return int(m.group(1)) - start + 1


# 导入时若未提供新证据，保留这些"进行中/已终结"状态，避免仅补一个字段把状态降级
_IMPORT_KEEP_STATUSES = (
    InsuranceStatus.DONE, InsuranceStatus.EXPORTED, InsuranceStatus.EXEMPT,
    InsuranceStatus.PENDING_RENEWAL, InsuranceStatus.PENDING_PAY, InsuranceStatus.PAID,
)


def apply_import_record(conn, student_id, id_card, batch_no, insurance_type,
                        flag=None, pay_date=None, policy_no=None, expiry_date=None,
                        operator='', partial=False):
    """初始化导入写 insurance_records（按指定批次 upsert，支持历史批次补录）。

    partial=False：全量覆盖（旧行为，未提供的字段视为空）。
    partial=True ：仅补空——None=未提供（保留库内值），''=显式清空。
    仅在该行确有保险数据时建/改记录，空行不生成裸记录：
      - 学平险：主管标记 / 保单号 / 缴费日期 / 到期时间任一非空
      - 实习险：主管标记 / 缴费日期任一非空
    状态推导（有标记时以标记为准，否则按数据证据）：
      - 不参保 → exempt；学平险办结 → 有到期 done，否则 exported
      - 按计划办理 → 首保批次 pending_approve，其余 approved
      - 有到期 → done；仅有保单/缴费 → approved（续保批次）或保持原终态
    返回 'created' / 'updated' / None（无数据不处理）。
    """
    existing = _record_for_batch(conn, student_id, batch_no, insurance_type)
    provided = (flag, pay_date, policy_no, expiry_date)

    if partial:
        if all(v is None for v in provided):
            return None
        cur = existing or {}
        flag = flag or ''
        pay_date = pay_date if pay_date is not None else (cur.get('pay_date') or '')
        policy_no = policy_no if policy_no is not None else (cur.get('policy_no') or '')
        expiry_date = (expiry_date if expiry_date is not None
                       else (cur.get('expiry_date') or ''))
    else:
        flag = flag or ''
        pay_date = pay_date or ''
        policy_no = policy_no or ''
        expiry_date = expiry_date or ''

    flag = flag.strip()
    policy_no = policy_no.strip()
    pay_date = normalize_insurance_date(pay_date)
    expiry_date = normalize_insurance_date(expiry_date)

    # 原样回导即"无变化"：未填主管标记且三类值字段与库内完全一致时直接返回，
    # 不重算状态、不写库（避免把历史状态差异当成导入改动）
    if partial and existing and not flag:
        if ((pay_date or '') == (existing['pay_date'] or '')
                and (policy_no or '') == (existing['policy_no'] or '')
                and (expiry_date or '') == (existing['expiry_date'] or '')):
            return None

    stu = conn.execute(
        'SELECT grade, enrollment_year FROM students WHERE id = ?', (student_id,)
    ).fetchone()
    if not stu:
        return None

    if insurance_type == INSURANCE_TYPE_INTERN:
        if flag == _FLAG_EXEMPT:
            status = InsuranceStatus.EXEMPT
        elif _FLAG_DONE_INTERN in flag:
            status = InsuranceStatus.DONE
        elif flag == _FLAG_PENDING_INTERN:
            status = InsuranceStatus.APPROVED
        elif not (flag or pay_date):
            return None
        elif existing and existing['status'] in _IMPORT_KEEP_STATUSES:
            status = existing['status']
        else:
            status = InsuranceStatus.PENDING_APPROVE
    else:
        plan = build_plan(stu['enrollment_year'], stu['grade'])
        first_batch = plan[0]['batch_no'] if plan else 1
        if flag == _FLAG_EXEMPT:
            status = InsuranceStatus.EXEMPT
        elif _FLAG_DONE_XUEPING in flag:
            status = InsuranceStatus.DONE if expiry_date else InsuranceStatus.EXPORTED
        elif flag == _FLAG_PENDING_XUEPING:
            status = (InsuranceStatus.APPROVED if batch_no > first_batch
                      else InsuranceStatus.PENDING_APPROVE)
        elif expiry_date:
            status = InsuranceStatus.DONE
        elif not (policy_no or pay_date or flag):
            if not existing:
                return None
            # 显式清空或空行：保留原状态，只清理字段
            status = (existing['status'] if existing['status'] in _IMPORT_KEEP_STATUSES
                      else InsuranceStatus.PENDING_APPROVE)
        elif existing and existing['status'] in _IMPORT_KEEP_STATUSES:
            status = existing['status']
        elif batch_no > first_batch:
            # 续保批次没有独立审批：有缴费/保单数据即视为已确认参保，等导出
            status = InsuranceStatus.APPROVED
        else:
            status = InsuranceStatus.PENDING_APPROVE

    now = _now()
    payload = {'pay_date': pay_date, 'policy_no': policy_no,
               'expiry_date': expiry_date, 'flag': flag}

    if existing:
        fields = dict(status=status, pay_date=pay_date)
        if insurance_type == INSURANCE_TYPE_XUEPING:
            fields.update(policy_no=policy_no, expiry_date=expiry_date)
        if status == InsuranceStatus.DONE and not existing['completed_at'] \
                and existing['status'] != InsuranceStatus.DONE:
            fields['completed_at'] = now
        if status == InsuranceStatus.APPROVED and not existing['approved_at'] \
                and existing['status'] != InsuranceStatus.APPROVED:
            fields['approved_at'] = now
            fields['approved_by'] = operator or ''
        # 原样回导不产生噪音：所有字段与库内一致时按"无变化"处理，不写库不记日志
        if all((existing[key] or '') == (value or '') for key, value in fields.items()):
            return None
        _update_record(conn, existing['id'], **fields)
        log_operation(conn, existing, 'import', existing['status'], status,
                      payload, operator=operator)
        return 'updated'

    start = start_year(stu['enrollment_year'], stu['grade'])
    cur = conn.execute(
        '''INSERT INTO insurance_records
           (student_id, id_card, school_year, batch_no, insurance_type, status,
            pay_date, policy_no, expiry_date, approved_at, approved_by,
            completed_at, remark)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'imported')''',
        (
            student_id, id_card or '', _school_year(start, batch_no), batch_no,
            insurance_type, status, pay_date,
            policy_no if insurance_type == INSURANCE_TYPE_XUEPING else '',
            expiry_date if insurance_type == INSURANCE_TYPE_XUEPING else '',
            now if status == InsuranceStatus.APPROVED else '',
            (operator or '') if status == InsuranceStatus.APPROVED else '',
            now if status == InsuranceStatus.DONE else '',
        )
    )
    log_operation(conn, {'id': cur.lastrowid, 'student_id': student_id},
                  'import', '', status, payload, operator=operator)
    return 'created'


# ========== 导入批次留痕与撤销 ==========
_IMPORT_RESTORE_FIELDS = (
    'status', 'pay_date', 'premium', 'policy_no', 'expiry_date',
    'export_batch_id', 'exported_at', 'approved_at', 'approved_by',
    'pushed_at', 'completed_at', 'remark',
)


def snapshot_record(record):
    """记录快照（JSON），供导入撤销时还原字段。"""
    if not record:
        return ''
    return json.dumps({k: record.get(k, '') for k in _IMPORT_RESTORE_FIELDS},
                      ensure_ascii=False)


def create_import_batch(conn, file_name='', operator=''):
    """创建一次导入批次，返回 batch_id（用于留痕与撤销）。"""
    batch_id = uuid.uuid4().hex
    conn.execute(
        'INSERT INTO insurance_import_batches (id, file_name, operator) VALUES (?, ?, ?)',
        (batch_id, file_name or '', operator or '')
    )
    return batch_id


def record_import_entry(conn, batch_id, record, student_id, action, before, after):
    """登记一条导入改动（before/after 为 snapshot_record 结果）。"""
    conn.execute(
        '''INSERT INTO insurance_import_entries
             (batch_id, record_id, student_id, action, before_json, after_json)
           VALUES (?, ?, ?, ?, ?, ?)''',
        (batch_id, (record or {}).get('id', 0), student_id, action,
         before or '', after or '')
    )


def finish_import_batch(conn, batch_id, total_rows, ok_rows):
    conn.execute(
        'UPDATE insurance_import_batches SET total_rows = ?, ok_rows = ? WHERE id = ?',
        (int(total_rows or 0), int(ok_rows or 0), batch_id)
    )


def revert_import_batch(conn, batch_id, operator=''):
    """撤销一次导入：created → 删除记录，updated → 还原 before 快照。

    返回撤销的记录条数；同一批次不可重复撤销。
    """
    batch = conn.execute(
        'SELECT * FROM insurance_import_batches WHERE id = ?', (batch_id,)
    ).fetchone()
    if not batch:
        raise InsuranceTransitionError('导入批次不存在')
    if batch['reversed_at']:
        raise InsuranceTransitionError('该导入批次已撤销')

    entries = conn.execute(
        'SELECT * FROM insurance_import_entries WHERE batch_id = ? ORDER BY id DESC',
        (batch_id,)
    ).fetchall()
    sids = set()
    reverted = 0
    for e in entries:
        sids.add(e['student_id'])
        if e['action'] == 'created':
            if e['record_id']:
                conn.execute('DELETE FROM insurance_records WHERE id = ?', (e['record_id'],))
                reverted += 1
            continue
        before = json.loads(e['before_json'] or '{}')
        if not before or not e['record_id']:
            continue
        fields = {k: before.get(k, '') for k in _IMPORT_RESTORE_FIELDS}
        sets = ', '.join(f'{k} = ?' for k in fields)
        conn.execute(
            f'UPDATE insurance_records SET {sets}, updated_at = ? WHERE id = ?',
            (*fields.values(), _now(), e['record_id'])
        )
        reverted += 1

    conn.execute(
        'UPDATE insurance_import_batches SET reversed_at = ? WHERE id = ?',
        (_now(), batch_id)
    )
    for sid in sids:
        sync_student_snapshot(conn, sid)
    logger.info('撤销保险导入批次 %s：%s 条记录（operator=%s）', batch_id, reverted, operator)
    return reverted


def migrate_insurance_v3(conn):
    """建新表 + 回填历史数据（幂等）。由 models.db.init_db 调用。

    注意：不在此处 commit，交由调用方统一提交。
    """
    cursor = conn.cursor()

    for statement in INSURANCE_V3_DDL.split(';'):
        if statement.strip():
            cursor.execute(statement)

    try:
        existing = cursor.execute('SELECT COUNT(1) FROM insurance_records').fetchone()[0]
    except sqlite3.OperationalError:
        existing = 0

    stats = None
    if existing == 0:
        stats = backfill_insurance_records(cursor)
        logger.info(
            '保险 v3 迁移完成：学生 %s，学平险记录 %s，实习险记录 %s，跳过 %s，日期无法解析 %s',
            stats['students'], stats['xueping'], stats['intern'],
            stats['skipped'], stats['unparsed_date']
        )

    repaired = repair_never_insured_xueping(conn)
    if repaired['students']:
        logger.info(
            '保险从未投保收敛：候选 %s 人，处理 %s 人，新建放弃记录 %s 条，'
            '更新为放弃 %s 条，清理误填缴费 %s 条',
            repaired['candidates'], repaired['students'],
            repaired['created'], repaired['exempted'], repaired['cleared']
        )

    try:
        cursor.execute(
            'UPDATE schema_version SET version = ?, applied_at = CURRENT_TIMESTAMP '
            'WHERE id = 1 AND version < ?',
            (SCHEMA_VERSION, SCHEMA_VERSION)
        )
    except sqlite3.OperationalError:
        pass

    return stats


# ============================================================
# 从未投保老生收敛（历史脏数据修复）
# ============================================================
REMARK_AUTO_UNINSURED = 'auto_uninsured'


def has_real_xueping_data(record):
    """该学平险记录是否有真实投保凭证（保单号 / 到期时间任一非空）。

    缴费日期（pay_date）仅是收费登记，不构成投保凭证——历史上曾出现
    「从未投保的空占位被误填统一缴费日期」的脏数据，因此不把 pay_date
    视为真实投保的依据。真实投保必须有保单号或到期日。
    """
    return any((record.get(field) or '').strip()
               for field in ('policy_no', 'expiry_date'))


def repair_never_insured_xueping(conn, current_year=None):
    """把「从未投保的老生」已到学年的学平险批次标记为「放弃本批次参保」。

    背景：历史迁移把「主管标记为空且无任何保险数据」的学生也当成已参保，
    按当时批次生成了空占位记录（pending_approve），使他们出现在催缴名单里，
    却永远无法推进到实习就业险。

    判定（全部满足才处理，谨慎对待旧数据）：
      1) 未删除、未毕业、非托管；
      2) 主管标记为空或「不参保」，且无保单号 / 到期日（缴费日期不作为投保凭证，
         因历史上出现过空占位被批量误填统一缴费日期的脏数据）；
      3) 无实习就业险记录（已有则说明走过保险流程，不做推断）；
      4) 学平险记录只可能停留在「待审批 / 放弃」，且没有保单号 / 到期日；
      5) 当前批次已越过计划首保批次（仍在首保学年的学生不处理）。
    处理：仅把「学年已到达」的计划内学平险批次置为 exempt（remark=auto_uninsured），
    并清空 pay_date / premium（这些学生从未投保，缴费日期只可能是误填）；
    不删除记录、不改写保单号/到期日等真实凭证；未到学年的批次留待下次启动继续收敛。
    幂等：重复执行不会重复写入，也不会改动已办结/已审批的记录。
    """
    if current_year is None:
        current_year = config.CURRENT_SCHOOL_YEAR

    stats = {'candidates': 0, 'students': 0, 'created': 0, 'exempted': 0,
             'cleared': 0}

    rows = conn.execute(
        '''SELECT s.id, s.id_card, s.grade, s.enrollment_year
           FROM students s
           WHERE COALESCE(s.is_deleted, 0) = 0
             AND COALESCE(s.registration_status, '') != '毕业'
             AND COALESCE(s.attendance_status, '') != '托管'
             AND COALESCE(TRIM(s.supervisor_flag), '') IN ('', ?)
             AND COALESCE(TRIM(s.student_insurance_no), '') = ''
             AND COALESCE(TRIM(s.student_insurance_expiry), '') = ''
             AND COALESCE(TRIM(s.internship_insurance_pay_date), '') = '' ''',
        (config.InsuranceStatus.EXEMPT,)
    ).fetchall()
    candidates = [dict(r) for r in rows]
    stats['candidates'] = len(candidates)
    rec_map = _records_map(conn, [c['id'] for c in candidates])
    insert_cursor = conn.cursor()

    for s in candidates:
        plan = build_plan(s.get('enrollment_year'), s.get('grade'), current_year)
        xueping_steps = [p for p in plan
                         if p['insurance_type'] == INSURANCE_TYPE_XUEPING]
        if not xueping_steps:
            continue
        now_batch = batch_now(s.get('enrollment_year'), s.get('grade'), current_year)
        if now_batch <= xueping_steps[0]['batch_no']:
            continue                    # 仍处于首保学年：属正常待办，不自动放弃

        records = rec_map.get(s['id'], [])
        if any(r['insurance_type'] == INSURANCE_TYPE_INTERN for r in records):
            continue                    # 已有实习险记录：不推断
        xueping_records = [r for r in records
                           if r['insurance_type'] == INSURANCE_TYPE_XUEPING]
        if any(has_real_xueping_data(r) for r in xueping_records):
            continue                    # 有任何真实数据：绝不改写
        if any(r['status'] not in (InsuranceStatus.PENDING_APPROVE,
                                   InsuranceStatus.EXEMPT)
               for r in xueping_records):
            continue                    # 已进入办理流程：不干预

        start = start_year(s.get('enrollment_year'), s.get('grade'))
        changed = False
        for step in xueping_steps:
            if step['batch_no'] > now_batch:
                continue                # 学年未到：留给后续批次，不提前放弃
            rec = _find_record(rec_map, s['id'], step['batch_no'], INSURANCE_TYPE_XUEPING)
            if rec is None:
                if not _insert_record(
                    insert_cursor, s['id'], s.get('id_card'),
                    _school_year(start, step['batch_no']), step['batch_no'],
                    INSURANCE_TYPE_XUEPING, InsuranceStatus.EXEMPT,
                    remark=REMARK_AUTO_UNINSURED
                ):
                    continue
                rec = _record_for_batch(conn, s['id'], step['batch_no'],
                                        INSURANCE_TYPE_XUEPING)
                _update_record(conn, rec['id'], completed_at=_now())
                log_operation(conn, rec, 'auto_uninsured', '',
                              InsuranceStatus.EXEMPT,
                              {'reason': 'never_insured'})
                stats['created'] += 1
                changed = True
                continue
            if rec['status'] == InsuranceStatus.EXEMPT:
                # 已是「放弃」但残留误填的缴费日期/保费：一并清空（幂等自愈）。
                if (rec.get('pay_date') or '').strip() or (rec.get('premium') or '').strip():
                    _update_record(conn, rec['id'], pay_date='', premium='')
                    stats['cleared'] += 1
                    changed = True
                continue
            # 清空误填的缴费日期/保费：这些学生从未投保，pay_date/premium
            # 只可能是迁移占位或人工误填（如统一填 2026-01-01）。
            _update_record(conn, rec['id'], status=InsuranceStatus.EXEMPT,
                           pay_date='', premium='', completed_at=_now(),
                           remark=REMARK_AUTO_UNINSURED)
            log_operation(conn, rec, 'auto_uninsured', rec['status'],
                          InsuranceStatus.EXEMPT, {'reason': 'never_insured'})
            stats['exempted'] += 1
            changed = True

        # 迁移可能把空占位生成在计划之外的批次（如转学生的更高批次），
        # 已到学年的空占位一并了结，避免残留「待审批」出现在记录页。
        plan_batches = {p['batch_no'] for p in xueping_steps}
        for rec in xueping_records:
            if (rec['batch_no'] in plan_batches
                    or rec['batch_no'] > now_batch
                    or rec['status'] != InsuranceStatus.PENDING_APPROVE):
                continue
            _update_record(conn, rec['id'], status=InsuranceStatus.EXEMPT,
                           pay_date='', premium='', completed_at=_now(),
                           remark=REMARK_AUTO_UNINSURED)
            log_operation(conn, rec, 'auto_uninsured', rec['status'],
                          InsuranceStatus.EXEMPT, {'reason': 'never_insured'})
            stats['exempted'] += 1
            changed = True

        if changed:
            sync_student_snapshot(conn, s['id'])
            stats['students'] += 1

    return stats


# ============================================================
# 计划引擎（P1）：三类学生 / 计划步骤 / 当前批次
# ============================================================
STUDENT_KIND_NORMAL = 'normal'
STUDENT_KIND_TRANSFER = 'transfer'
STUDENT_KIND_DEFERRED = 'deferred'

STAGE_FIRST = 'first'         # 新生首保
STAGE_RENEWAL = 'renewal'     # 第二年续保
STAGE_PUSH = 'push'           # 推送至实习办结池


def student_kind(enrollment_year, grade):
    """学生分类：normal（普通）/ transfer（转学生）/ deferred（推迟注册）。"""
    offset = student_offset(enrollment_year, grade)
    if offset >= 1:
        return STUDENT_KIND_TRANSFER
    if offset <= -1:
        return STUDENT_KIND_DEFERRED
    return STUDENT_KIND_NORMAL


def build_plan(enrollment_year, grade, current_year=None):
    """生成该生的完整保险计划（按批次升序）。

    每个步骤：{'batch_no': int, 'insurance_type': 'xueping'|'intern', 'stage': 'first'|'renewal'|'push'}
      - normal  : 第1年学平险 → 第2年学平险 → 第3年实习险
      - transfer: 本校第N年学平险（首保）→ 第N+1年实习险（跳过续保）
      - deferred: 按 offset 配置多一年学平险，实习险顺延
    """
    if current_year is None:
        current_year = config.CURRENT_SCHOOL_YEAR
    offset = student_offset(enrollment_year, grade)

    if offset >= 1:
        # 转学生首保批次固定为其入学时所处的本校年序（offset + 1），
        # 不能按当前年份推算，否则每过一年“首保批次”都会往后滑。
        n = offset + 1
        return [
            {'batch_no': n, 'insurance_type': INSURANCE_TYPE_XUEPING, 'stage': STAGE_FIRST},
            {'batch_no': n + 1, 'insurance_type': INSURANCE_TYPE_INTERN, 'stage': STAGE_PUSH},
        ]

    if offset == 0:
        return [
            {'batch_no': 1, 'insurance_type': INSURANCE_TYPE_XUEPING, 'stage': STAGE_FIRST},
            {'batch_no': 2, 'insurance_type': INSURANCE_TYPE_XUEPING, 'stage': STAGE_RENEWAL},
            {'batch_no': 3, 'insurance_type': INSURANCE_TYPE_INTERN, 'stage': STAGE_PUSH},
        ]

    cfg = DEFERRED_PLAN.get(offset, {
        'xueping_years': 3 + (-offset),
        'intern_year': 4 + (-offset),
    })
    plan = [
        {
            'batch_no': i,
            'insurance_type': INSURANCE_TYPE_XUEPING,
            'stage': STAGE_FIRST if i == 1 else STAGE_RENEWAL,
        }
        for i in range(1, cfg['xueping_years'] + 1)
    ]
    plan.append({
        'batch_no': cfg['intern_year'],
        'insurance_type': INSURANCE_TYPE_INTERN,
        'stage': STAGE_PUSH,
    })
    return plan


def current_insurance_batch(grade, enrollment_year, current_year=None):
    """兼容旧调用：返回 1~3 的批次号；无法判定或超出三年制返回 None。"""
    if not _to_int(grade):
        return None
    b = batch_now(enrollment_year, grade, current_year)
    return b if 1 <= b <= 3 else None


# ============================================================
# 记录读取
# ============================================================
def _row_to_dict(cursor, row):
    if row is None:
        return None
    return dict(zip([d[0] for d in cursor.description], row))


def get_record(conn, record_id):
    cur = conn.execute('SELECT * FROM insurance_records WHERE id = ?', (record_id,))
    return _row_to_dict(cur, cur.fetchone())


def get_student_records(conn, student_id, insurance_type=None):
    sql = 'SELECT * FROM insurance_records WHERE student_id = ?'
    params = [student_id]
    if insurance_type:
        sql += ' AND insurance_type = ?'
        params.append(insurance_type)
    sql += ' ORDER BY batch_no'
    cur = conn.execute(sql, params)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def get_latest_record(conn, student_id, insurance_type):
    cur = conn.execute(
        'SELECT * FROM insurance_records WHERE student_id = ? AND insurance_type = ? '
        'ORDER BY batch_no DESC LIMIT 1',
        (student_id, insurance_type)
    )
    return _row_to_dict(cur, cur.fetchone())


def get_latest_done_record(conn, student_id, insurance_type):
    """返回指定险种中批次最高的「已办结」记录（DONE / EXPORTED）。

    用于快照投影：当学生存在已办结批次时，优先展示该批次的标记，
    而不是被后续「放弃本期」（exempt）批次覆盖。
    """
    cur = conn.execute(
        '''SELECT * FROM insurance_records
           WHERE student_id = ? AND insurance_type = ?
             AND status IN (?, ?)
           ORDER BY batch_no DESC LIMIT 1''',
        (student_id, insurance_type,
         InsuranceStatus.DONE, InsuranceStatus.EXPORTED)
    )
    return _row_to_dict(cur, cur.fetchone())


# ============================================================
# 状态流转 + 操作日志 + 快照投影
# ============================================================
class InsuranceTransitionError(ValueError):
    """非法状态流转或参数缺失。"""


_UPDATABLE_FIELDS = {
    'status', 'pay_date', 'premium', 'policy_no', 'expiry_date',
    'export_batch_id', 'exported_at', 'approved_at', 'approved_by',
    'pushed_at', 'completed_at', 'remark',
}


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _update_record(conn, record_id, **fields):
    invalid = set(fields) - _UPDATABLE_FIELDS
    if invalid:
        raise ValueError(f'不允许更新的字段: {sorted(invalid)}')
    if not fields:
        return
    assignments = ', '.join(f'{name} = ?' for name in fields)
    conn.execute(
        f'UPDATE insurance_records SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
        list(fields.values()) + [record_id]
    )


def _require_record(record, allowed):
    if not record:
        raise InsuranceTransitionError('保险记录不存在')
    if record['status'] not in allowed:
        raise InsuranceTransitionError(
            f"记录 {record['id']} 当前状态 {record['status']} 不允许该操作"
        )


def log_operation(conn, record, action, from_status='', to_status='',
                  payload=None, export_batch_id='', operator=''):
    conn.execute(
        '''INSERT INTO insurance_operation_logs
           (record_id, student_id, action, from_status, to_status, payload, export_batch_id, operator)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
        (
            record['id'], record['student_id'], action,
            from_status or '', to_status or '',
            json.dumps(payload or {}, ensure_ascii=False, default=str),
            export_batch_id or '', operator or '',
        )
    )


def _flag_year_from_school_year(school_year):
    try:
        return int(str(school_year)[:4])
    except (TypeError, ValueError):
        return config.CURRENT_SCHOOL_YEAR


_SNAPSHOT_FIELDS = ('policy_no', 'pay_date', 'expiry_date', 'premium')


def _latest_nonempty(conn, student_id, insurance_type, field):
    """取该险种记录中该字段最近一次非空值（按批次倒序 → id 倒序）。

    新建批次记录时字段通常为空（待导出/待补保单），此时不能让旧字段被清空，
    因此快照按「每个字段取最近非空值」而非「整条最新记录」投影。
    """
    if field not in _SNAPSHOT_FIELDS:
        raise ValueError(f'不支持快照字段: {field}')
    row = conn.execute(
        f'''SELECT {field} FROM insurance_records
            WHERE student_id = ? AND insurance_type = ?
              AND COALESCE({field}, '') <> ''
            ORDER BY batch_no DESC, id DESC LIMIT 1''',
        (student_id, insurance_type)
    ).fetchone()
    return (row[0] if row else '') or ''


def sync_student_snapshot(conn, student_id):
    """把记录回写到 students 旧字段，保证旧页面/导出/导入继续可用。

    每个字段独立取「最近一次非空值」：续保刚创建的空记录不会把
    现有保单号/缴费日期/到期日期清空，历史缴费日期也能保留到被新值覆盖。
    """
    xp = get_latest_record(conn, student_id, INSURANCE_TYPE_XUEPING)
    it = get_latest_record(conn, student_id, INSURANCE_TYPE_INTERN)

    flag = ''
    if it:
        if it['status'] == InsuranceStatus.DONE:
            flag = config.InsuranceStatus.done_intern(_flag_year_from_school_year(it['school_year']))
        elif it['status'] == InsuranceStatus.EXEMPT:
            flag = config.InsuranceStatus.EXEMPT
        else:
            flag = config.InsuranceStatus.PENDING_INTERN
    elif xp:
        # 优先展示「已办结」批次的学平险标记；仅当没有任何已办结记录时，
        # 才按最高批次的状态展示（如「放弃本期」exempt → 不参保）。
        done_rec = get_latest_done_record(conn, student_id, INSURANCE_TYPE_XUEPING)
        base = done_rec if done_rec else xp
        if base['status'] in (InsuranceStatus.DONE, InsuranceStatus.EXPORTED):
            flag = config.InsuranceStatus.done_xueping(
                _flag_year_from_school_year(base['school_year']))
        elif base['status'] == InsuranceStatus.EXEMPT:
            flag = config.InsuranceStatus.EXEMPT
        elif base['status'] in (InsuranceStatus.PENDING_PAY, InsuranceStatus.PAID,
                                InsuranceStatus.APPROVED):
            flag = config.InsuranceStatus.PENDING_XUEPING
        elif base['status'] == InsuranceStatus.PENDING_RENEWAL:
            # 已推送待续保：当前保单仍在有效期内，继续显示上一批已办结标记
            flag = (config.InsuranceStatus.done_xueping(
                        _flag_year_from_school_year(done_rec['school_year']))
                    if done_rec else config.InsuranceStatus.PENDING_XUEPING)
        elif base['status'] == InsuranceStatus.PENDING_APPROVE:
            flag = ''

    conn.execute(
        '''UPDATE students SET
             student_insurance_no = ?,
             student_insurance_pay_date = ?,
             student_insurance_expiry = ?,
             insurance_premium = ?,
             internship_insurance_pay_date = ?,
             supervisor_flag = ?
           WHERE id = ?''',
        (
            _latest_nonempty(conn, student_id, INSURANCE_TYPE_XUEPING, 'policy_no'),
            _latest_nonempty(conn, student_id, INSURANCE_TYPE_XUEPING, 'pay_date'),
            _latest_nonempty(conn, student_id, INSURANCE_TYPE_XUEPING, 'expiry_date'),
            _latest_nonempty(conn, student_id, INSURANCE_TYPE_XUEPING, 'premium'),
            _latest_nonempty(conn, student_id, INSURANCE_TYPE_INTERN, 'pay_date'),
            flag,
            student_id,
        )
    )


def approve(conn, record_id, operator=''):
    """审批通过：pending_approve / paid → approved。

    paid 用于实习就业险：先由催缴池录缴费，再送财务主管审批。
    """
    rec = get_record(conn, record_id)
    _require_record(rec, (InsuranceStatus.PENDING_APPROVE, InsuranceStatus.PAID))
    _update_record(conn, record_id, status=InsuranceStatus.APPROVED,
                   approved_at=_now(), approved_by=operator or '')
    log_operation(conn, rec, 'approve', rec['status'], InsuranceStatus.APPROVED, operator=operator)
    sync_student_snapshot(conn, rec['student_id'])
    return get_record(conn, record_id)


def reject(conn, record_id, operator=''):
    """审批否决/放弃本期：pending_approve / paid / pending_renewal → exempt。

    exempt 仅表示「该批次不办理（该批次终结）」，不影响后续批次继续投保。
    """
    rec = get_record(conn, record_id)
    _require_record(rec, (InsuranceStatus.PENDING_APPROVE, InsuranceStatus.PAID,
                          InsuranceStatus.PENDING_RENEWAL))
    _update_record(conn, record_id, status=InsuranceStatus.EXEMPT, completed_at=_now())
    log_operation(conn, rec, 'reject', rec['status'], InsuranceStatus.EXEMPT, operator=operator)
    sync_student_snapshot(conn, rec['student_id'])
    return get_record(conn, record_id)


def record_payment(conn, record_id, pay_date, operator='', premium=None):
    """催缴页登记缴费：只写缴费日期/保费，不改变保险状态。

    业务口径：首保的缴费在首保审批环节确定，不进入催缴；
    只有学平险续保记录，以及已确认办理（审批通过后）的实习就业险记录在此登记。
    """
    rec = get_record(conn, record_id)
    if not rec:
        raise InsuranceTransitionError('记录不存在')
    if rec['status'] == InsuranceStatus.EXEMPT:
        raise InsuranceTransitionError('该记录为不办理，无需缴费')

    if rec['insurance_type'] == INSURANCE_TYPE_XUEPING:
        cur = conn.execute(
            'SELECT grade, enrollment_year FROM students WHERE id = ?',
            (rec['student_id'],))
        student = cur.fetchone()
        if not student:
            raise InsuranceTransitionError('学生不存在')
        plan = build_plan(student['enrollment_year'], student['grade'])
        first_batch = plan[0]['batch_no'] if plan else 1
        if rec['batch_no'] <= first_batch:
            raise InsuranceTransitionError('首保缴费在审批环节登记，不进入催缴')
    elif rec['status'] not in (InsuranceStatus.APPROVED, InsuranceStatus.EXPORTED,
                               InsuranceStatus.DONE):
        raise InsuranceTransitionError('实习就业险尚未确认办理')

    normalized = normalize_insurance_date(pay_date)
    if not normalized:
        raise InsuranceTransitionError('缴费日期无效')

    fields = dict(pay_date=normalized)
    if premium is not None:
        fields['premium'] = premium
    _update_record(conn, record_id, **fields)
    log_operation(conn, rec, 'pay', rec['status'], rec['status'],
                  {'pay_date': normalized, 'premium': premium}, operator=operator)
    sync_student_snapshot(conn, rec['student_id'])
    return get_record(conn, record_id)


def fill_policy(conn, record_id, policy_no, expiry_date, operator='', premium=None):
    """补录学平险保单号与到期日：exported/paid → done。"""
    rec = get_record(conn, record_id)
    _require_record(rec, (InsuranceStatus.EXPORTED, InsuranceStatus.PAID))
    if rec['insurance_type'] != INSURANCE_TYPE_XUEPING:
        raise InsuranceTransitionError('实习就业险不需要保单号与到期日')
    policy_no = (policy_no or '').strip()
    normalized = normalize_insurance_date(expiry_date)
    if not policy_no:
        raise InsuranceTransitionError('保单号不能为空')
    if not normalized:
        raise InsuranceTransitionError('保险到期日期无效')

    fields = dict(status=InsuranceStatus.DONE, policy_no=policy_no,
                  expiry_date=normalized, completed_at=_now())
    if premium is not None:
        fields['premium'] = premium
    _update_record(conn, record_id, **fields)
    log_operation(conn, rec, 'policy', rec['status'], InsuranceStatus.DONE,
                  {'policy_no': policy_no, 'expiry_date': normalized}, operator=operator)
    sync_student_snapshot(conn, rec['student_id'])
    return get_record(conn, record_id)


def export_records(conn, record_ids, action, operator='', file_name=''):
    """批量导出：approved → exported，并创建导出批次留痕。

    action: 'first'（学平险首保）| 'renewal'（学平险续保）| 'intern'（实习就业险办结）
    返回 export_batch_id。
    """
    if action not in ('first', 'renewal', 'intern'):
        raise InsuranceTransitionError('未知导出类型')
    expected_type = INSURANCE_TYPE_INTERN if action == 'intern' else INSURANCE_TYPE_XUEPING

    records = []
    for rid in record_ids:
        rec = get_record(conn, rid)
        _require_record(rec, (InsuranceStatus.APPROVED,))
        if rec['insurance_type'] != expected_type:
            raise InsuranceTransitionError(f'记录 {rid} 险种与导出类型不匹配')
        records.append(rec)
    if not records:
        raise InsuranceTransitionError('未选择任何记录')

    batch_id = uuid.uuid4().hex
    first = records[0]
    conn.execute(
        '''INSERT INTO insurance_export_batches
           (id, action, batch_no, insurance_type, school_year,
            student_count, record_ids, file_name, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (batch_id, action, first['batch_no'], expected_type, first['school_year'],
         len(records), json.dumps(record_ids), file_name or '', operator or '')
    )

    now = _now()
    to_status = InsuranceStatus.DONE if action == 'intern' else InsuranceStatus.EXPORTED
    for rec in records:
        fields = dict(status=to_status, export_batch_id=batch_id, exported_at=now)
        if action == 'intern':
            fields['completed_at'] = now
        _update_record(conn, rec['id'], **fields)
        log_operation(conn, rec, 'export', rec['status'], to_status,
                      export_batch_id=batch_id, operator=operator)
        sync_student_snapshot(conn, rec['student_id'])
    return batch_id


def complete_intern(conn, record_ids, operator='', file_name=''):
    """实习就业险批量办结（导出，不写保单号）。"""
    return export_records(conn, record_ids, action='intern',
                          operator=operator, file_name=file_name)


def revert_export(conn, export_batch_id, operator=''):
    """撤销导出批次：exported → approved，并标记批次已撤销。返回撤销条数。"""
    cur = conn.execute('SELECT * FROM insurance_export_batches WHERE id = ?', (export_batch_id,))
    batch = _row_to_dict(cur, cur.fetchone())
    if not batch:
        raise InsuranceTransitionError('导出批次不存在')
    if batch['reversed_at']:
        raise InsuranceTransitionError('该批次已撤销')

    record_ids = json.loads(batch['record_ids'] or '[]')
    now = _now()
    reverted = 0
    for rid in record_ids:
        rec = get_record(conn, rid)
        if not rec or rec['status'] not in (InsuranceStatus.EXPORTED, InsuranceStatus.DONE):
            continue
        if rec['export_batch_id'] != export_batch_id:
            continue
        _update_record(conn, rid, status=InsuranceStatus.APPROVED,
                       export_batch_id='', exported_at='', completed_at='')
        log_operation(conn, rec, 'revert', rec['status'], InsuranceStatus.APPROVED,
                      export_batch_id=export_batch_id, operator=operator)
        sync_student_snapshot(conn, rec['student_id'])
        reverted += 1

    conn.execute('UPDATE insurance_export_batches SET reversed_at = ? WHERE id = ?',
                 (now, export_batch_id))
    return reverted


def latest_active_record(conn, student_id):
    """该生最近一条保险记录（批次倒序 → id 倒序）。"""
    cur = conn.execute(
        '''SELECT * FROM insurance_records WHERE student_id = ?
           ORDER BY batch_no DESC, id DESC LIMIT 1''',
        (student_id,)
    )
    return _row_to_dict(cur, cur.fetchone())


def _detach_record_from_batch(conn, batch_id, record_id):
    """把记录从导出批次中剔除；批次清空时标记为已撤销。"""
    cur = conn.execute(
        'SELECT record_ids FROM insurance_export_batches WHERE id = ?', (batch_id,))
    row = cur.fetchone()
    if not row:
        return
    try:
        ids = [int(i) for i in json.loads(row[0] or '[]')]
    except (TypeError, ValueError):
        ids = []
    ids = [i for i in ids if i != int(record_id)]
    conn.execute(
        'UPDATE insurance_export_batches SET record_ids = ?, student_count = ? WHERE id = ?',
        (json.dumps(ids), len(ids), batch_id))
    if not ids:
        conn.execute(
            'UPDATE insurance_export_batches SET reversed_at = ? '
            'WHERE id = ? AND COALESCE(reversed_at, \'\') = \'\'',
            (_now(), batch_id))


def revert_student(conn, student_id, operator=''):
    """按学生撤销最后一次参保：删除最近一条保险记录，回到办理前状态。

    - 记录本身（保单号、到期日、缴费日期、办结时间）一并删除
    - 从所属导出批次中剔除；批次清空则标记已撤销
    - students 旧字段按剩余记录重新投影（历史缴费/保单回退到上一批）
    - 操作日志保留（action='revert'，to_status='deleted'）
    返回被删除的记录字典。
    """
    rec = latest_active_record(conn, student_id)
    if not rec:
        raise InsuranceTransitionError('该学生没有可撤销的保险记录')

    batch_id = rec.get('export_batch_id') or ''
    if batch_id:
        _detach_record_from_batch(conn, batch_id, rec['id'])

    conn.execute('DELETE FROM insurance_records WHERE id = ?', (rec['id'],))
    log_operation(
        conn, rec, 'revert', rec['status'], 'deleted',
        {
            'batch_no': rec['batch_no'],
            'insurance_type': rec['insurance_type'],
            'policy_no': rec.get('policy_no') or '',
            'expiry_date': rec.get('expiry_date') or '',
            'pay_date': rec.get('pay_date') or '',
        },
        export_batch_id=batch_id, operator=operator)
    sync_student_snapshot(conn, student_id)
    return rec


def search_student_last_records(conn, search, limit=50):
    """撤销中心按学生查询：匹配学生 + 其最近一条保险记录。"""
    term = (search or '').strip()
    if not term:
        return []
    like = f'%{term}%'
    rows = conn.execute(
        '''SELECT s.id AS student_id, s.name, s.student_no, s.grade,
                  s.enrollment_year, s.school, s.admin_class, s.attendance_status,
                  r.id AS record_id, r.batch_no, r.insurance_type, r.status,
                  r.school_year, r.policy_no, r.expiry_date, r.pay_date
           FROM students s
           JOIN insurance_records r ON r.id = (
                SELECT r2.id FROM insurance_records r2
                WHERE r2.student_id = s.id
                ORDER BY r2.batch_no DESC, r2.id DESC LIMIT 1)
           WHERE COALESCE(s.is_deleted, 0) = 0
             AND (s.name LIKE ? OR s.student_no LIKE ?
                  OR s.id_card LIKE ? OR s.name_pinyin LIKE ?)
           ORDER BY s.name LIMIT ?''',
        (like, like, like, like, max(1, min(int(limit), 200)))
    ).fetchall()
    return [dict(r) for r in rows]


def push_to_intern(conn, student_ids, operator='', current_year=None):
    """手动推送：为续保/首保已完成的学生创建实习就业险待审批记录。

    已存在实习险记录的学生跳过。返回 {'created': n, 'skipped': m}。
    """
    if current_year is None:
        current_year = config.CURRENT_SCHOOL_YEAR

    created = 0
    skipped = 0
    for sid in student_ids:
        cur = conn.execute(
            'SELECT id, id_card, grade, enrollment_year FROM students WHERE id = ?', (sid,)
        )
        student = _row_to_dict(cur, cur.fetchone())
        if not student:
            skipped += 1
            continue

        plan = build_plan(student.get('enrollment_year'), student.get('grade'), current_year)
        intern_step = next(
            (p for p in plan if p['insurance_type'] == INSURANCE_TYPE_INTERN), None
        )
        if not intern_step:
            skipped += 1
            continue

        exists = conn.execute(
            'SELECT 1 FROM insurance_records '
            'WHERE student_id = ? AND batch_no = ? AND insurance_type = ?',
            (sid, intern_step['batch_no'], INSURANCE_TYPE_INTERN)
        ).fetchone()
        if exists:
            skipped += 1
            continue

        start = start_year(student.get('enrollment_year'), student.get('grade'))
        school_year = _school_year(start, intern_step['batch_no'])
        conn.execute(
            '''INSERT INTO insurance_records
               (student_id, id_card, school_year, batch_no, insurance_type,
                status, pushed_at, remark)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (sid, student.get('id_card') or '', school_year, intern_step['batch_no'],
             INSURANCE_TYPE_INTERN, InsuranceStatus.PENDING_APPROVE, _now(), 'pushed')
        )
        new_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        rec = get_record(conn, new_id)
        log_operation(conn, rec, 'push', '', InsuranceStatus.PENDING_APPROVE,
                      operator=operator)
        sync_student_snapshot(conn, sid)
        created += 1

    return {'created': created, 'skipped': skipped}


def push_to_renewal(conn, student_ids, operator='', current_year=None):
    """推送至续保：为推迟注册学生追加下一批学平险记录。

    仅对「计划内学平险批次已全部办结」的推迟注册学生生效；新记录置为
    待续保（pending_renewal），等当前保单到期后自动出现在「待续保」名单，
    再走续保流程（进入参保 → 导出 → 补录保单 → 办结），
    使该生的学平险已办结批次正常增长。
    返回 {'created': n, 'skipped': m, 'errors': [...]}。
    """
    if current_year is None:
        current_year = config.CURRENT_SCHOOL_YEAR

    created = 0
    skipped = 0
    errors = []
    for sid in student_ids:
        cur = conn.execute(
            'SELECT id, id_card, grade, enrollment_year FROM students '
            'WHERE id = ? AND COALESCE(is_deleted, 0) = 0', (sid,)
        )
        student = _row_to_dict(cur, cur.fetchone())
        if not student:
            skipped += 1
            errors.append({'student_id': sid, 'message': '学生不存在'})
            continue

        if student_kind(student.get('enrollment_year'),
                        student.get('grade')) != STUDENT_KIND_DEFERRED:
            skipped += 1
            errors.append({'student_id': sid, 'message': '仅推迟注册学生可继续续保'})
            continue

        plan = build_plan(student.get('enrollment_year'), student.get('grade'), current_year)
        terminal_batches = {
            r['batch_no'] for r in get_student_records(conn, sid, INSURANCE_TYPE_XUEPING)
            if r['status'] in XUEPING_TERMINAL_STATUSES
        }
        xueping_steps = [p for p in plan if p['insurance_type'] == INSURANCE_TYPE_XUEPING]
        if any(step['batch_no'] not in terminal_batches for step in xueping_steps):
            skipped += 1
            errors.append({'student_id': sid, 'message': '计划内学平险尚未全部办结'})
            continue

        next_batch = max(terminal_batches) + 1 if terminal_batches else 1
        exists = conn.execute(
            'SELECT 1 FROM insurance_records '
            'WHERE student_id = ? AND batch_no = ? AND insurance_type = ?',
            (sid, next_batch, INSURANCE_TYPE_XUEPING)
        ).fetchone()
        if exists:
            skipped += 1
            errors.append({'student_id': sid, 'message': '下一批学平险已存在'})
            continue

        start = start_year(student.get('enrollment_year'), student.get('grade'))
        conn.execute(
            '''INSERT INTO insurance_records
               (student_id, id_card, school_year, batch_no, insurance_type,
                status, approved_at, approved_by, remark)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (sid, student.get('id_card') or '', _school_year(start, next_batch),
             next_batch, INSURANCE_TYPE_XUEPING, InsuranceStatus.PENDING_RENEWAL,
             '', operator or '', 'renewal')
        )
        new_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        rec = get_record(conn, new_id)
        log_operation(conn, rec, 'renew', '', InsuranceStatus.PENDING_RENEWAL,
                      operator=operator)
        sync_student_snapshot(conn, sid)
        created += 1

    return {'created': created, 'skipped': skipped, 'errors': errors[:20]}


# ============================================================
# 五个池的只读查询（P2）
# ============================================================
STATUS_LABELS = {
    InsuranceStatus.PENDING_APPROVE: '待审批',
    InsuranceStatus.PENDING_RENEWAL: '待续保',
    InsuranceStatus.APPROVED: '已审批待导出',
    InsuranceStatus.EXPORTED: '已办结待补保单',
    InsuranceStatus.PENDING_PAY: '待缴费',
    InsuranceStatus.PAID: '已缴费',
    InsuranceStatus.DONE: '已办结',
    InsuranceStatus.EXEMPT: '不办理',
}

_POOL_STUDENT_COLUMNS = '''
    s.id, s.name, s.student_no, s.id_card, s.school, s.grade, s.enrollment_year,
    s.admin_class, s.major, s.student_phone, s.name_pinyin, s.attendance_status
'''


def _pool_student_conditions(search=None, school=None, grade=None,
                             admin_class=None, major=None):
    """池的公共学生筛选条件（排除软删除、已毕业与托管学生）。

    托管学生不参加学平险业务，与毕业/已删除学生同等排除。
    """
    conditions = [
        'COALESCE(s.is_deleted, 0) = 0',
        "(s.registration_status IS NULL OR s.registration_status != '毕业')",
        "(s.attendance_status IS NULL OR s.attendance_status != '托管')",
    ]
    params = []
    if search:
        like = f'%{search}%'
        conditions.append(
            '(s.name LIKE ? OR s.student_no LIKE ? OR s.id_card LIKE ? '
            'OR s.name_pinyin LIKE ? OR s.student_phone LIKE ?)'
        )
        params.extend([like, like, like, like, like])
    for column, value in (('school', school), ('grade', grade),
                          ('admin_class', admin_class), ('major', major)):
        if value:
            conditions.append(f's.{column} = ?')
            params.append(value)
    return conditions, params


def _pool_students(conn, search=None, school=None, grade=None,
                   admin_class=None, major=None):
    """池的公共学生底表：排除软删除与已毕业学生。"""
    conditions, params = _pool_student_conditions(
        search, school, grade, admin_class, major)
    sql = (f'SELECT {_POOL_STUDENT_COLUMNS} FROM students s WHERE '
           + ' AND '.join(conditions)
           + ' ORDER BY s.grade, s.admin_class, s.name')
    cur = conn.execute(sql, params)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def _records_map(conn, student_ids):
    """一次取回全部记录，避免按学生逐条查询。"""
    result = {}
    ids = list(student_ids)
    for start in range(0, len(ids), 500):      # 规避 SQLite 变量上限
        chunk = ids[start:start + 500]
        placeholders = ','.join('?' * len(chunk))
        cur = conn.execute(
            f'SELECT * FROM insurance_records WHERE student_id IN ({placeholders})',
            chunk
        )
        names = [d[0] for d in cur.description]
        for row in cur.fetchall():
            rec = dict(zip(names, row))
            result.setdefault(rec['student_id'], []).append(rec)
    return result


def _find_record(rec_map, student_id, batch_no, insurance_type):
    for rec in rec_map.get(student_id, []):
        if rec['batch_no'] == batch_no and rec['insurance_type'] == insurance_type:
            return rec
    return None


def _days_left(expiry_date, today):
    try:
        return (datetime.strptime(expiry_date, '%Y-%m-%d').date() - today).days
    except (TypeError, ValueError):
        return None


def _coverage_start_date(expiry_date):
    """投保日期：记录未单独保存，按到期日期减一年推算。"""
    raw = (expiry_date or '').strip()
    if not raw:
        return ''
    normalized = raw.replace('/', '-').replace('.', '-')
    try:
        parsed = datetime.strptime(normalized[:10], '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return ''
    try:
        return parsed.replace(year=parsed.year - 1).isoformat()
    except ValueError:                     # 2 月 29 日 → 上一年 2 月 28 日
        return parsed.replace(year=parsed.year - 1, day=28).isoformat()


def _pool_item(student, step, record=None, today=None):
    record = record or {}
    item = dict(student)
    item.update({
        'record_id': record.get('id'),
        'batch_no': step['batch_no'],
        'insurance_type': step['insurance_type'],
        'stage': step['stage'],
        'status': record.get('status', ''),
        'status_label': STATUS_LABELS.get(record.get('status'), '未建档'),
        'school_year': record.get('school_year', ''),
        'pay_date': record.get('pay_date', ''),
        'premium': record.get('premium', ''),
        'policy_no': record.get('policy_no', ''),
        'expiry_date': record.get('expiry_date', ''),
        'start_date': _coverage_start_date(record.get('expiry_date')),
        'days_left': _days_left(record.get('expiry_date'), today) if today else None,
        'kind': student_kind(student.get('enrollment_year'), student.get('grade')),
    })
    return item


def _sort_pool_items(items):
    """池列表统一按 入学年 → 班级 → 姓名 排序。"""
    items.sort(key=lambda r: (
        str(r.get('enrollment_year') or ''),
        str(r.get('admin_class') or ''),
        str(r.get('name') or ''),
    ))
    return items


def query_first_pool(conn, current_year=None, **filters):
    """① 首保池：计划第一步为「首保」，且无记录或记录待审批。"""
    if current_year is None:
        current_year = config.CURRENT_SCHOOL_YEAR
    students = _pool_students(conn, **filters)
    rec_map = _records_map(conn, [s['id'] for s in students])

    items = []
    for s in students:
        plan = build_plan(s.get('enrollment_year'), s.get('grade'), current_year)
        step = plan[0]
        if step['stage'] != STAGE_FIRST:
            continue
        # 仅本年度仍处于首保批次的学生；已升入高批次的老生不再进首保池
        if batch_now(s.get('enrollment_year'), s.get('grade'), current_year) > step['batch_no']:
            continue
        rec = _find_record(rec_map, s['id'], step['batch_no'], step['insurance_type'])
        if rec and rec['status'] != InsuranceStatus.PENDING_APPROVE:
            continue
        items.append(_pool_item(s, step, rec))
    _sort_pool_items(items)
    return items


# 续保池分组：已过期 → 待到期 → 到期日待补
URGENCY_OVERDUE = 'overdue'
URGENCY_DUE = 'due'
URGENCY_MISSING_EXPIRY = 'missing_expiry'

_URGENCY_ORDER = {URGENCY_OVERDUE: 0, URGENCY_DUE: 1, URGENCY_MISSING_EXPIRY: 2}
_RENEWAL_STATUS_LABELS = {
    URGENCY_OVERDUE: '已过期',
    URGENCY_DUE: '待续保',
    URGENCY_MISSING_EXPIRY: '到期日待补',
}

_STUDENT_ALIAS_KEYS = (
    'name', 'student_no', 'student_id_card', 'school', 'student_grade',
    'enrollment_year', 'admin_class', 'major', 'student_phone', 'name_pinyin',
    'attendance_status',
)


def _renewal_candidates(conn, **filters):
    """以保险记录为驱动拉取续保候选：学生筛选与记录一次 JOIN 取回。"""
    conditions, params = _pool_student_conditions(**filters)
    sql = f'''
        SELECT r.*, s.name, s.student_no, s.id_card AS student_id_card,
               s.school, s.grade AS student_grade, s.enrollment_year,
               s.admin_class, s.major, s.student_phone, s.name_pinyin,
               s.attendance_status
        FROM insurance_records r
        JOIN students s ON s.id = r.student_id
        WHERE {' AND '.join(conditions)}
          AND r.insurance_type IN (?, ?)
    '''
    params = params + [INSURANCE_TYPE_XUEPING, INSURANCE_TYPE_INTERN]
    cur = conn.execute(sql, params)
    names = [d[0] for d in cur.description]
    students = {}
    records = {}
    for row in cur.fetchall():
        data = dict(zip(names, row))
        sid = data['student_id']
        if sid not in students:
            students[sid] = {
                'id': sid,
                'name': data['name'],
                'student_no': data['student_no'],
                'id_card': data['student_id_card'],
                'school': data['school'],
                'grade': data['student_grade'],
                'enrollment_year': data['enrollment_year'],
                'admin_class': data['admin_class'],
                'major': data['major'],
                'student_phone': data['student_phone'],
                'name_pinyin': data['name_pinyin'],
                'attendance_status': data['attendance_status'],
            }
        record = {k: v for k, v in data.items() if k not in _STUDENT_ALIAS_KEYS}
        records.setdefault(sid, []).append(record)
    return students, records


def _renewal_urgency(expiry_date, today, active_months):
    """续保分组：过期 / 待到期 / 到期日缺失；None 表示暂不进池。"""
    expiry = (expiry_date or '').strip()
    if not expiry:
        return URGENCY_MISSING_EXPIRY
    days_left = _days_left(expiry, today)
    if days_left is None:
        return URGENCY_MISSING_EXPIRY
    if days_left < 0:
        return URGENCY_OVERDUE
    if expiry[:7] in active_months:
        return URGENCY_DUE
    return None


def _renewal_item(student, step, previous, record_id, urgency, today):
    item = _pool_item(student, step, previous, today=today)
    item.update({
        'record_id': record_id,
        'status': InsuranceStatus.PENDING_RENEWAL,
        'status_label': _RENEWAL_STATUS_LABELS[urgency],
        'previous_batch_no': previous['batch_no'],
        'previous_expiry': previous.get('expiry_date', ''),
        'days_left': _days_left(previous.get('expiry_date'), today),
        'urgency': urgency,
        'target_school_year': _school_year(
            start_year(student.get('enrollment_year'), student.get('grade')),
            step['batch_no']),
    })
    return item


def _renewal_active_months(conn, today, window_days):
    """活跃到期月份：今天起 window_days 内到期的已办结学平险到期月份。"""
    rows = conn.execute(
        '''SELECT DISTINCT substr(expiry_date, 1, 7) AS ym
           FROM insurance_records
           WHERE insurance_type = ? AND status = ? AND expiry_date != ''
             AND date(expiry_date) BETWEEN date(?) AND date(?, ?)''',
        (INSURANCE_TYPE_XUEPING, InsuranceStatus.DONE,
         today.isoformat(), today.isoformat(), f'+{window_days} day')
    ).fetchall()
    return {r[0] for r in rows if r[0]}


def _xueping_terminal(records):
    """学平险终态记录（已办结 / 放弃本期），作为续保与推送的进度基线。"""
    return [r for r in records
            if r['insurance_type'] == INSURANCE_TYPE_XUEPING
            and r['status'] in XUEPING_TERMINAL_STATUSES]


def query_renewal_pool(conn, today=None, window_days=30,
                       current_year=None, **filters):
    """② 续保池：同月到期整批列出，并覆盖「已过期」与「到期日缺失」。

    判定以该生批次最大的「终态」学平险记录（已办结 / 放弃本期）为当前保单：
      - 已办结：按保单到期日分组（已过期 / 待到期 / 到期日缺失）；
      - 放弃本期：无到期日，等到下一批次的学年到来才再次进入续保（归「待到期」）。
    已存在实习就业险记录的学生视为业务终了，直接排除。
    """
    today = today or date.today()
    if current_year is None:
        current_year = config.CURRENT_SCHOOL_YEAR
    window_days = max(1, int(window_days or 30))
    active_months = _renewal_active_months(conn, today, window_days)

    students, records_by_student = _renewal_candidates(conn, **filters)

    items = []
    for sid, student in students.items():
        records = records_by_student.get(sid, [])
        # 已进入实习就业险阶段（任何状态）说明业务已终了，不再续保
        if any(r['insurance_type'] == INSURANCE_TYPE_INTERN for r in records):
            continue

        terminal = _xueping_terminal(records)
        if not terminal:
            continue
        latest = max(terminal, key=lambda r: r['batch_no'])
        now_batch = batch_now(student.get('enrollment_year'),
                              student.get('grade'), current_year)

        # 已「推送至续保」的占位记录：到期后（放弃本期则等学年）进入待续保
        pending = next(
            (r for r in records
             if r['insurance_type'] == INSURANCE_TYPE_XUEPING
             and r['status'] == InsuranceStatus.PENDING_RENEWAL
             and r['batch_no'] > latest['batch_no']),
            None
        )
        if pending is not None:
            if latest['status'] == InsuranceStatus.DONE:
                urgency = _renewal_urgency(
                    latest.get('expiry_date'), today, active_months)
            elif pending['batch_no'] <= now_batch:
                urgency = URGENCY_DUE
            else:
                urgency = None
            if urgency is None:
                continue
            step = {'batch_no': pending['batch_no'],
                    'insurance_type': INSURANCE_TYPE_XUEPING,
                    'stage': STAGE_RENEWAL}
            items.append(_renewal_item(
                student, step, latest, pending['id'], urgency, today))
            continue

        plan = build_plan(student.get('enrollment_year'),
                          student.get('grade'), current_year)
        renew_steps = [
            p for p in plan
            if p['insurance_type'] == INSURANCE_TYPE_XUEPING
            and p['stage'] == STAGE_RENEWAL
            and p['batch_no'] > latest['batch_no']
        ]
        if not renew_steps:
            continue                    # 转学生或已完成计划内全部学平险
        step = renew_steps[0]
        if any(r['insurance_type'] == INSURANCE_TYPE_XUEPING
               and r['batch_no'] == step['batch_no'] for r in records):
            continue                    # 已生成下一批记录，无需重复进入

        if latest['status'] == InsuranceStatus.DONE:
            urgency = _renewal_urgency(
                latest.get('expiry_date'), today, active_months)
        elif step['batch_no'] <= now_batch:
            # 上一批为「放弃本期」：无到期日，学年到达该批次后才再次续保，
            # 避免把已放弃批次的旧保单误标为「已过期」。
            urgency = URGENCY_DUE
        else:
            urgency = None
        if urgency is None:
            continue

        items.append(_renewal_item(
            student, step, latest, latest['id'], urgency, today))

    items.sort(key=lambda r: (
        _URGENCY_ORDER.get(r.get('urgency'), 9),
        r['days_left'] if r.get('days_left') is not None else 9999,
        str(r.get('enrollment_year') or ''),
        str(r.get('admin_class') or ''),
        str(r.get('name') or ''),
    ))
    return items


def query_collection_pool(conn, today=None, **filters):
    """③ 催缴池：已参保但未缴费的学平险续保 + 实习就业险记录。"""
    return query_collection_stage(conn, paid=False, **filters)


def query_push_pool(conn, current_year=None, **filters):
    """④ 待推送池：计划内最后一个学平险批次已了结，且尚无实习险记录。"""
    if current_year is None:
        current_year = config.CURRENT_SCHOOL_YEAR
    students = _pool_students(conn, **filters)
    rec_map = _records_map(conn, [s['id'] for s in students])

    items = []
    for s in students:
        plan = build_plan(s.get('enrollment_year'), s.get('grade'), current_year)
        xueping_steps = [p for p in plan if p['insurance_type'] == INSURANCE_TYPE_XUEPING]
        intern_step = next(
            (p for p in plan if p['insurance_type'] == INSURANCE_TYPE_INTERN), None
        )
        if not xueping_steps or not intern_step:
            continue

        records = rec_map.get(s['id'], [])
        xueping_records = [
            r for r in records if r['insurance_type'] == INSURANCE_TYPE_XUEPING
        ]
        last_terminal = max(
            (r for r in xueping_records
             if r['status'] in XUEPING_TERMINAL_STATUSES),
            key=lambda r: r['batch_no'], default=None)

        if student_kind(s.get('enrollment_year'), s.get('grade')) == STUDENT_KIND_DEFERRED:
            # 推迟注册学生历史数据可能缺档（如没有第 1 批记录），
            # 以「已了结的最高批次」覆盖计划最后一个学平险批次即可。
            plan_last = max(p['batch_no'] for p in xueping_steps)
            if last_terminal is None or last_terminal['batch_no'] < plan_last:
                continue
        else:
            ready = True
            for step in xueping_steps:
                rec = _find_record(rec_map, s['id'], step['batch_no'],
                                   INSURANCE_TYPE_XUEPING)
                if not rec or rec['status'] not in XUEPING_TERMINAL_STATUSES:
                    ready = False
                    break
            if not ready:
                continue

        # 存在仍在办理中的学平险批次（已「推送至续保」）时先等其了结，
        # 避免同一学生同时出现在推送页与续保流程中。放弃本期的批次视为已了结。
        if any(r['status'] not in XUEPING_TERMINAL_STATUSES for r in xueping_records):
            continue

        if any(r['insurance_type'] == INSURANCE_TYPE_INTERN for r in records):
            continue

        # 学年门槛对所有学生生效：严格按「入学年 − grade」推算实习险批次，
        # 只有学年推进到实习批次才进入推送。避免「放弃本期 / 从未投保」的学生
        # 在学平险批次被了结后提前一年被推入实习就业险。
        if batch_now(s.get('enrollment_year'), s.get('grade'),
                     current_year) < intern_step['batch_no']:
            continue

        last_done = last_terminal

        item = _pool_item(s, intern_step, last_done)
        item.update({
            'record_id': None,
            'status': 'ready_to_push',
            'status_label': '待推送',
            'school_year': '',
            'pay_date': '',
            'policy_no': '',
            'expiry_date': '',
            'previous_record_id': (last_done or {}).get('id'),
            'previous_batch_no': (last_done or {}).get('batch_no'),
        })
        items.append(item)
    return items


def query_intern_pool(conn, today=None, **filters):
    """⑤ 实习办结池：待审批 / 已缴费待审批 / 已审批待导出。"""
    students = _pool_students(conn, **filters)
    rec_map = _records_map(conn, [s['id'] for s in students])

    allowed = (
        InsuranceStatus.PENDING_APPROVE,
        InsuranceStatus.PAID,
        InsuranceStatus.APPROVED,
    )
    items = []
    for s in students:
        for rec in rec_map.get(s['id'], []):
            if rec['insurance_type'] != INSURANCE_TYPE_INTERN:
                continue
            if rec['status'] not in allowed:
                continue
            step = {
                'batch_no': rec['batch_no'],
                'insurance_type': INSURANCE_TYPE_INTERN,
                'stage': STAGE_PUSH,
            }
            items.append(_pool_item(s, step, rec))
    items.sort(key=lambda r: (r['grade'], r['admin_class'], r['name']))
    return items


def pool_summary(conn, today=None, current_year=None):
    """五个池的人数汇总，供页面顶部卡片使用。"""
    today = today or date.today()
    return {
        'first': len(query_first_pool(conn, current_year)),
        'renewal': len(query_renewal_pool(conn, today=today, current_year=current_year)),
        'collection': len(query_collection_pool(conn)),
        'push': len(query_push_pool(conn, current_year)),
        'intern': len(query_intern_pool(conn, today=today)),
    }


# ============================================================
# 首保页（P3）：全状态视图 + 按计划补建记录
# ============================================================
def ensure_record(conn, student_id, batch_no,
                  insurance_type=INSURANCE_TYPE_XUEPING, operator='', remark=''):
    """确保学生在该批次存在保险记录；不存在则按计划创建 pending_approve。"""
    cur = conn.execute(
        'SELECT id, id_card, grade, enrollment_year FROM students WHERE id = ?',
        (student_id,)
    )
    student = _row_to_dict(cur, cur.fetchone())
    if not student:
        raise InsuranceTransitionError('学生不存在')

    cur = conn.execute(
        'SELECT * FROM insurance_records '
        'WHERE student_id = ? AND batch_no = ? AND insurance_type = ?',
        (student_id, batch_no, insurance_type)
    )
    existing = _row_to_dict(cur, cur.fetchone())
    if existing:
        return existing

    start = start_year(student.get('enrollment_year'), student.get('grade'))
    conn.execute(
        '''INSERT INTO insurance_records
           (student_id, id_card, school_year, batch_no, insurance_type, status, remark)
           VALUES (?, ?, ?, ?, ?, ?, ?)''',
        (student_id, student.get('id_card') or '', _school_year(start, batch_no),
         batch_no, insurance_type, InsuranceStatus.PENDING_APPROVE,
         remark or 'first')
    )
    new_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
    rec = get_record(conn, new_id)
    log_operation(conn, rec, 'create', '', InsuranceStatus.PENDING_APPROVE,
                  operator=operator)
    return rec


def query_first_stage(conn, current_year=None, statuses=None, **filters):
    """首保页：计划首步为「首保」的全部学生（可按状态集合过滤）。

    statuses 为 None 表示不限制；集合中允许包含 '' 代表尚未建档。
    """
    if current_year is None:
        current_year = config.CURRENT_SCHOOL_YEAR
    allowed = set(statuses) if statuses is not None else None
    students = _pool_students(conn, **filters)
    rec_map = _records_map(conn, [s['id'] for s in students])

    items = []
    for s in students:
        plan = build_plan(s.get('enrollment_year'), s.get('grade'), current_year)
        step = plan[0]
        if step['stage'] != STAGE_FIRST:
            continue
        # 仅本年度仍处于首保批次的学生；已升入高批次的老生不再进首保池
        if batch_now(s.get('enrollment_year'), s.get('grade'), current_year) > step['batch_no']:
            continue
        rec = _find_record(rec_map, s['id'], step['batch_no'], step['insurance_type'])
        current = rec['status'] if rec else ''
        if allowed is not None and current not in allowed:
            continue
        item = _pool_item(s, step, rec)
        item['source_status'] = current
        items.append(item)
    _sort_pool_items(items)
    return items


def first_stage_counts(conn, current_year=None, **filters):
    """首保页各状态人数（未建档单独计数）。"""
    items = query_first_stage(conn, current_year=current_year, **filters)
    counts = {'unbuilt': 0, 'total': len(items)}
    for key in (InsuranceStatus.PENDING_APPROVE, InsuranceStatus.APPROVED,
                InsuranceStatus.EXPORTED, InsuranceStatus.DONE,
                InsuranceStatus.EXEMPT):
        counts[key] = 0
    for item in items:
        key = item['source_status'] or 'unbuilt'
        counts[key] = counts.get(key, 0) + 1
    return counts


# ============================================================
# 续保 / 催缴（P4）
# ============================================================
def start_renewal(conn, student_id, current_year=None, operator=''):
    """续保页「进入参保流程」：创建下一批学平险记录，置为已审批待导出。"""
    if current_year is None:
        current_year = config.CURRENT_SCHOOL_YEAR

    cur = conn.execute(
        'SELECT id, id_card, grade, enrollment_year FROM students '
        'WHERE id = ? AND COALESCE(is_deleted, 0) = 0', (student_id,)
    )
    student = _row_to_dict(cur, cur.fetchone())
    if not student:
        raise InsuranceTransitionError('学生不存在')

    # 已「推送至续保」的计划外批次：到期后进入参保流程时直接启用
    cur = conn.execute(
        'SELECT * FROM insurance_records WHERE student_id = ? AND insurance_type = ? '
        'AND status = ? ORDER BY batch_no ASC LIMIT 1',
        (student_id, INSURANCE_TYPE_XUEPING, InsuranceStatus.PENDING_RENEWAL)
    )
    pending = _row_to_dict(cur, cur.fetchone())
    if pending:
        _update_record(conn, pending['id'], status=InsuranceStatus.APPROVED,
                       approved_at=_now(), approved_by=operator or '')
        log_operation(conn, pending, 'approve', InsuranceStatus.PENDING_RENEWAL,
                      InsuranceStatus.APPROVED, operator=operator)
        sync_student_snapshot(conn, student_id)
        return get_record(conn, pending['id'])

    plan = build_plan(student.get('enrollment_year'), student.get('grade'), current_year)
    terminal_batches = [
        r['batch_no'] for r in get_student_records(conn, student_id)
        if r['insurance_type'] == INSURANCE_TYPE_XUEPING
        and r['status'] in XUEPING_TERMINAL_STATUSES
    ]
    latest_batch = max(terminal_batches) if terminal_batches else 0
    renew_steps = [
        p for p in plan
        if p['insurance_type'] == INSURANCE_TYPE_XUEPING
        and p['stage'] == STAGE_RENEWAL
        and p['batch_no'] > latest_batch
    ]
    if not renew_steps:
        raise InsuranceTransitionError('该学生没有可续保的批次')
    step = renew_steps[0]

    cur = conn.execute(
        'SELECT * FROM insurance_records '
        'WHERE student_id = ? AND batch_no = ? AND insurance_type = ?',
        (student_id, step['batch_no'], INSURANCE_TYPE_XUEPING)
    )
    existing = _row_to_dict(cur, cur.fetchone())
    if existing:
        return existing

    start = start_year(student.get('enrollment_year'), student.get('grade'))
    conn.execute(
        '''INSERT INTO insurance_records
           (student_id, id_card, school_year, batch_no, insurance_type, status,
            approved_at, approved_by, remark)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (student_id, student.get('id_card') or '',
         _school_year(start, step['batch_no']), step['batch_no'],
         INSURANCE_TYPE_XUEPING, InsuranceStatus.APPROVED,
         _now(), operator or '', 'renewal')
    )
    new_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
    rec = get_record(conn, new_id)
    log_operation(conn, rec, 'renew', '', InsuranceStatus.APPROVED,
                  operator=operator)
    sync_student_snapshot(conn, student_id)
    return rec


def exempt_renewal_batch(conn, student_id, current_year=None, operator='',
                         remark='', today=None):
    """续保页「放弃本期」：把下一个待续保批次标记为 exempt。

    语义：仅终结该批次，学生后续批次仍按计划自动投保，不会永久退出流程。
    仅允许放弃当前已进入续保窗口的批次，避免误放弃未来批次。
    """
    if current_year is None:
        current_year = config.CURRENT_SCHOOL_YEAR
    if today is None:
        today = date.today()

    cur = conn.execute(
        'SELECT id, id_card, grade, enrollment_year FROM students '
        'WHERE id = ? AND COALESCE(is_deleted, 0) = 0', (student_id,)
    )
    student = _row_to_dict(cur, cur.fetchone())
    if not student:
        raise InsuranceTransitionError('学生不存在')

    records = get_student_records(conn, student_id)
    if any(r['insurance_type'] == INSURANCE_TYPE_INTERN for r in records):
        raise InsuranceTransitionError('该学生已进入实习就业险阶段，无需放弃学平险')

    terminal = _xueping_terminal(records)
    if not terminal:
        raise InsuranceTransitionError('该学生尚无已办结的学平险批次')
    latest = max(terminal, key=lambda r: r['batch_no'])

    # 已「推送至续保」的占位批次优先（可能早于计划追加，如推迟注册学生的额外批次）
    pending = next(
        (r for r in records
         if r['insurance_type'] == INSURANCE_TYPE_XUEPING
         and r['status'] == InsuranceStatus.PENDING_RENEWAL
         and r['batch_no'] > latest['batch_no']),
        None
    )
    if pending is not None:
        step = {'batch_no': pending['batch_no'],
                'insurance_type': INSURANCE_TYPE_XUEPING,
                'stage': STAGE_RENEWAL}
        existing = pending
    else:
        plan = build_plan(student.get('enrollment_year'),
                          student.get('grade'), current_year)
        renew_steps = [
            p for p in plan
            if p['insurance_type'] == INSURANCE_TYPE_XUEPING
            and p['stage'] == STAGE_RENEWAL
            and p['batch_no'] > latest['batch_no']
        ]
        if not renew_steps:
            raise InsuranceTransitionError('该学生没有可放弃的续保批次')
        step = renew_steps[0]
        existing = next(
            (r for r in records
             if r['insurance_type'] == INSURANCE_TYPE_XUEPING
             and r['batch_no'] == step['batch_no']),
            None
        )
        if existing and existing['status'] not in (
                InsuranceStatus.PENDING_APPROVE, InsuranceStatus.PAID,
                InsuranceStatus.PENDING_RENEWAL):
            raise InsuranceTransitionError('该批次已在办理或已终结，无法放弃')

    # 与续保池同口径：已办结需进入到期窗口；放弃本期则需学年到达该批次
    if latest['status'] == InsuranceStatus.DONE:
        urgency = _renewal_urgency(
            latest.get('expiry_date'), today,
            _renewal_active_months(conn, today, 30))
        if urgency is None:
            raise InsuranceTransitionError('该学生尚未进入续保窗口')
    else:
        now_batch = batch_now(student.get('enrollment_year'),
                              student.get('grade'), current_year)
        if step['batch_no'] > now_batch:
            raise InsuranceTransitionError('该批次学年尚未到达，暂不能放弃')

    if existing is None:
        existing = ensure_record(conn, student_id, step['batch_no'],
                                 INSURANCE_TYPE_XUEPING, operator=operator,
                                 remark=remark or 'exempt_renewal')
    from_status = existing['status']
    _update_record(conn, existing['id'], status=InsuranceStatus.EXEMPT,
                   completed_at=_now(), remark=remark or 'exempt_renewal')
    log_operation(conn, existing, 'exempt_renewal', from_status,
                  InsuranceStatus.EXEMPT, operator=operator)
    sync_student_snapshot(conn, student_id)
    return get_record(conn, existing['id'])


def query_collection_stage(conn, paid=False, insurance_types=None,
                           current_year=None, **filters):
    """催缴页：已参保待收费的学平险续保记录 + 实习就业险已确认办理记录。

    缴费是滞后的收费登记，不参与保险状态流转；首保（含转学生首保批次）
    的缴费在审批环节确定，不进入催缴。
    """
    if current_year is None:
        current_year = config.CURRENT_SCHOOL_YEAR
    types = set(insurance_types) if insurance_types else None
    students = _pool_students(conn, **filters)
    rec_map = _records_map(conn, [s['id'] for s in students])

    items = []
    for s in students:
        plan = build_plan(s.get('enrollment_year'), s.get('grade'), current_year)
        first_batch = plan[0]['batch_no'] if plan else 1
        for rec in rec_map.get(s['id'], []):
            if rec['status'] == InsuranceStatus.EXEMPT:
                continue
            if rec['status'] == InsuranceStatus.PENDING_RENEWAL:
                continue    # 待续保尚未进入参保流程，不催缴
            if types is not None and rec['insurance_type'] not in types:
                continue
            if rec['insurance_type'] == INSURANCE_TYPE_XUEPING:
                if rec['batch_no'] <= first_batch:
                    continue    # 首保缴费在审批环节登记，不进催缴
                # 迁移生成的空占位记录（从未投保）没有任何可催缴的数据，
                # 无论如何都不能出现在催缴名单中。
                if (rec['status'] == InsuranceStatus.PENDING_APPROVE
                        and not has_real_xueping_data(rec)):
                    continue
            elif rec['status'] not in (InsuranceStatus.APPROVED,
                                       InsuranceStatus.EXPORTED,
                                       InsuranceStatus.DONE):
                continue        # 实习险需确认办理后才催缴
            has_paid = bool((rec.get('pay_date') or '').strip())
            if has_paid != paid:
                continue
            step = {
                'batch_no': rec['batch_no'],
                'insurance_type': rec['insurance_type'],
                'stage': STAGE_RENEWAL if rec['insurance_type'] == INSURANCE_TYPE_XUEPING
                         else STAGE_PUSH,
            }
            item = _pool_item(s, step, rec)
            item['source_status'] = rec['status']
            item['status_label'] = '待缴费' if not has_paid else '已缴费'
            items.append(item)
    _sort_pool_items(items)
    return items


def query_renewal_stage(conn, statuses=None, current_year=None, **filters):
    """续保环节：尚未走完流程的学平险续保记录（待导出 / 待补录保单）。

    已办结（done）不在续保页展示，避免历史数据堆积。
    """
    if current_year is None:
        current_year = config.CURRENT_SCHOOL_YEAR
    if statuses is None:
        statuses = (InsuranceStatus.APPROVED, InsuranceStatus.EXPORTED)
    allowed = set(statuses)
    students = _pool_students(conn, **filters)
    rec_map = _records_map(conn, [s['id'] for s in students])

    items = []
    for s in students:
        plan = build_plan(s.get('enrollment_year'), s.get('grade'), current_year)
        first_batch = plan[0]['batch_no'] if plan else 1
        for rec in rec_map.get(s['id'], []):
            if rec['insurance_type'] != INSURANCE_TYPE_XUEPING:
                continue
            if rec['batch_no'] <= first_batch:
                continue    # 首保记录不在续保页展示
            if allowed is not None and rec['status'] not in allowed:
                continue
            step = {
                'batch_no': rec['batch_no'],
                'insurance_type': rec['insurance_type'],
                'stage': STAGE_RENEWAL,
            }
            item = _pool_item(s, step, rec)
            item['source_status'] = rec['status']
            items.append(item)
    _sort_pool_items(items)
    return items


def query_intern_stage(conn, statuses=None, **filters):
    """实习办结页：实习险记录全状态视图（可按状态集合过滤）。"""
    allowed = set(statuses) if statuses is not None else None
    students = _pool_students(conn, **filters)
    rec_map = _records_map(conn, [s['id'] for s in students])

    items = []
    for s in students:
        for rec in rec_map.get(s['id'], []):
            if rec['insurance_type'] != INSURANCE_TYPE_INTERN:
                continue
            if allowed is not None and rec['status'] not in allowed:
                continue
            step = {'batch_no': rec['batch_no'],
                    'insurance_type': INSURANCE_TYPE_INTERN,
                    'stage': STAGE_PUSH}
            item = _pool_item(s, step, rec)
            item['source_status'] = rec['status']
            items.append(item)
    items.sort(key=lambda r: (r['grade'], r['admin_class'], r['name'], r['batch_no']))
    return items


def intern_stage_counts(conn, **filters):
    """实习办结页各状态人数。"""
    items = query_intern_stage(conn, **filters)
    counts = {'total': len(items)}
    for key in (InsuranceStatus.PENDING_APPROVE, InsuranceStatus.PAID,
                InsuranceStatus.APPROVED, InsuranceStatus.DONE,
                InsuranceStatus.EXEMPT):
        counts[key] = 0
    for item in items:
        key = item['source_status']
        counts[key] = counts.get(key, 0) + 1
    return counts


# ============================================================
# 记录查询 / 撤销中心 / 操作日志（P6）
# ============================================================
def query_insurance_records(conn, insurance_type=None, status=None, **filters):
    """保险记录查询：以 insurance_records 为数据源的统一查询。"""
    students = _pool_students(conn, **filters)
    rec_map = _records_map(conn, [s['id'] for s in students])

    items = []
    for s in students:
        for rec in rec_map.get(s['id'], []):
            if insurance_type and rec['insurance_type'] != insurance_type:
                continue
            if status and rec['status'] != status:
                continue
            step = {'batch_no': rec['batch_no'],
                    'insurance_type': rec['insurance_type'],
                    'stage': STAGE_PUSH if rec['insurance_type'] == INSURANCE_TYPE_INTERN
                             else STAGE_RENEWAL}
            item = _pool_item(s, step, rec)
            item['source_status'] = rec['status']
            items.append(item)
    items.sort(key=lambda r: (r['grade'], r['admin_class'], r['name'],
                              r['batch_no'], r['insurance_type']))
    return items


def list_export_batches(conn, limit=100):
    """导出批次列表（撤销中心）。"""
    rows = conn.execute(
        'SELECT * FROM insurance_export_batches ORDER BY created_at DESC LIMIT ?',
        (limit,)
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item['reversed'] = bool(item.get('reversed_at'))
        item['insurance_type_label'] = (
            '学平险' if item['insurance_type'] == INSURANCE_TYPE_XUEPING else '实习就业险')
        item['action_label'] = '新生首保' if item['action'] == 'first' else '实习办结'
        items.append(item)
    return items


def list_operation_logs(conn, limit=200, action='', search=''):
    """操作日志列表（撤销中心）。"""
    sql = '''SELECT l.*, s.name AS student_name, s.student_no
             FROM insurance_operation_logs l
             LEFT JOIN students s ON s.id = l.student_id
             WHERE 1 = 1'''
    params = []
    if action:
        sql += ' AND l.action = ?'
        params.append(action)
    if search:
        like = f'%{search}%'
        sql += ' AND (s.name LIKE ? OR s.student_no LIKE ? OR s.id_card LIKE ?)'
        params.extend([like, like, like])
    sql += ' ORDER BY l.created_at DESC, l.id DESC LIMIT ?'
    params.append(max(1, min(int(limit), 1000)))
    return [dict(row) for row in conn.execute(sql, params).fetchall()]
