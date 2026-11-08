"""保险业务服务层（P1-5：从 blueprints/insurance.py 抽取的业务逻辑）。

将保险数据初始化导入的后台处理逻辑从路由层分离：
- 路由层（insurance.py）：解析 request、返回 jsonify 响应
- 服务层（本模块）：进度管理、后台 worker 线程、DB 操作、Excel 解析

insurance_init_progress 字典由调用方（blueprints/insurance.py）持有并传入，
全局状态结构不变，仅业务逻辑位置迁移。
"""
import re
import secrets
import threading
import time
import datetime
import logging

import models
import config
from models.insurance import (
    INSURANCE_TYPE_INTERN,
    INSURANCE_TYPE_XUEPING,
    apply_import_record,
    batch_from_school_year,
    build_plan,
    create_import_batch,
    current_insurance_batch,
    finish_import_batch,
    normalize_insurance_date,
    record_import_entry,
    snapshot_record,
    start_year,
    sync_student_snapshot,
)

logger = logging.getLogger(__name__)


# 导入模板列（中文表头）与 DB 字段映射
# 目标定位列（保险学年 / 批次号）为新增必填项：旧 6 列模板不再支持，避免"只能写当前批次"
IMPORT_COLUMNS = ['身份证号', '保险学年', '批次号', '险种',
                  '学平险保单号', '学平险缴费日期', '学平险到期时间',
                  '实习险缴费日期', '主管标记']
FIELD_MAPPING = {
    '学平险保单号': 'student_insurance_no',
    '学平险缴费日期': 'student_insurance_pay_date',
    '学平险到期时间': 'student_insurance_expiry',
    '实习险缴费日期': 'internship_insurance_pay_date',
    '主管标记': 'supervisor_flag',
}
# 学平险相关列：与实习就业险批次互斥，填错即拒绝该行
XUEPING_COLUMNS = ['学平险保单号', '学平险缴费日期', '学平险到期时间']
# 日期型列：导入时 Excel 常以 int/float 序列号形式读入，需转成 YYYY-MM-DD 字符串
DATE_COLUMNS = {'学平险缴费日期', '学平险到期时间', '实习险缴费日期'}

TYPE_XUEPING = '学平险'
TYPE_INTERN = '实习就业险'
# 显式清空标记：默认"仅补空"，只有填这些值才清空库内字段
CLEAR_TOKENS = {'-', '—', '－', '空', '清空', 'null', 'none', '无'}

# 进度字典淘汰配置：防止 insurance_init_progress 内存单调增长
PROGRESS_TTL_SECONDS = 600      # 已完成进度保留时长（秒），超时惰性删除
PROGRESS_MAX_ENTRIES = 100      # 字典上限，超出后按最旧优先淘汰已完成条目


def _cleanup_stale_progress(progress_dict):
    """惰性清理进度字典：删除已完成且超过 TTL 的条目；仍超上限时淘汰最旧的已完成条目。

    在每次启动新导入前调用。进行中（processing）的条目永不删除，
    避免正在轮询的前端拿到 404。返回本次清理的条目数。
    """
    now = time.time()
    removed = 0

    stale = [
        pid for pid, p in progress_dict.items()
        if p.get('status') == 'completed' and p.get('completed_at')
        and now - p['completed_at'] > PROGRESS_TTL_SECONDS
    ]
    for pid in stale:
        progress_dict.pop(pid, None)
    removed += len(stale)

    overflow = len(progress_dict) - PROGRESS_MAX_ENTRIES
    if overflow > 0:
        completed = [
            (p.get('completed_at') or 0, pid)
            for pid, p in progress_dict.items() if p.get('status') == 'completed'
        ]
        completed.sort(key=lambda x: x[0])  # 最旧优先
        # 只淘汰已完成条目（进行中的保留）；completed 数量不足时删到能删为止
        for _, pid in completed[:overflow]:
            progress_dict.pop(pid, None)
        removed += min(overflow, len(completed))

    return removed


def compute_insurance_batch(grade, enrollment_year, current_year=None):
    """计算学生保险批次（第几年），委托 models.insurance 统一实现。

    保持原签名与返回值（1~3 或 None），避免导入/登记逻辑各自维护一份公式。
    """
    return current_insurance_batch(grade, enrollment_year, current_year)


def normalize_supervisor_flag(raw):
    """主管标记轻量归一化（基于真实样本）。

    直接接受规范值：
      - `YYYY年度学平险办结` / `YYYY年度实习就业险办结`（4 位年）
      - `按计划办理学平险` / `按计划办理实习就业险` / `不参保`
    并容忍常见变体：
      - 2 位年 `26年度…` → `2026年度…`
      - `2026年…`（非"年度"）→ `2026年度…`
      - 同义 `不参保/未参保/放弃/不参加` → `不参保`
    留空 → 默认 `按计划办理学平险`（流入待办由保险员办结），不阻断。
    """
    if not raw:
        return config.InsuranceStatus.PENDING_XUEPING
    s = re.sub(r'\s+', '', str(raw)).strip()

    # 同义归一
    if s in ('不参保', '未参保', '放弃参保', '放弃', '不参加', '免参保', '不保'):
        return config.InsuranceStatus.EXEMPT

    # 2 位年或 4 位年 + "年"(且后非"度") → "年度"
    def fix_year(m):
        y = m.group(1)
        if len(y) == 2:
            y = '20' + y
        return y + '年度'

    s = re.sub(r'(\d{2,4})年(?!度)', fix_year, s)

    # 已是 "XX年度" 但年份为 2 位：'26年度' → '2026年度'
    def fix_year2(m):
        return '20' + m.group(1) + '年度'

    s = re.sub(r'(?<!\d)(\d{2})年度', fix_year2, s)
    return s


def _is_canonical_flag(flag):
    """归一化后是否仍为已识别的规范枚举（含办结态）。"""
    if not flag:
        return False
    if flag in (config.InsuranceStatus.PENDING_XUEPING,
                config.InsuranceStatus.PENDING_INTERN,
                config.InsuranceStatus.EXEMPT):
        return True
    return config.InsuranceStatus.is_done(flag)


def _is_blank(v):
    """判断缴费日期等字段是否为空（None / 空串 / 纯空白）。"""
    return v is None or (isinstance(v, str) and v.strip() == '')


def parse_insurance_excel(file_storage):
    """用 openpyxl 在后端解析导入 Excel（去 CDN，离线可靠）。

    Args:
        file_storage: Flask 的 request.files['file'] 对象（类文件）。
    Returns:
        (rows, errors)
        rows: list[dict]，每行为 {中文表头: 值}；空单元格为 ''。
        errors: list[str]，解析级错误（如缺列）。
    """
    import openpyxl

    rows = []
    errors = []
    try:
        wb = openpyxl.load_workbook(file_storage, data_only=True, read_only=True)
        ws = wb.active
        if ws is None:
            return rows, ['Excel 文件无工作表']
        iter_rows = ws.iter_rows(values_only=True)
        try:
            header = next(iter_rows)
        except StopIteration:
            return rows, ['Excel 文件为空']
        header = [('' if h is None else str(h).strip()) for h in header]

        idx_map = {}
        for i, h in enumerate(header):
            if h in IMPORT_COLUMNS:
                idx_map[h] = i
        if '身份证号' not in idx_map:
            return rows, ['缺少"身份证号"列，无法匹配学生']
        if '保险学年' not in idx_map and '批次号' not in idx_map:
            return rows, ['缺少"保险学年"或"批次号"列：旧 6 列模板已停用，请重新下载新模板']

        for r in iter_rows:
            if r is None:
                continue
            id_card = r[idx_map['身份证号']]
            if id_card is None:
                continue
            id_card = str(id_card).strip()
            if not id_card:
                continue
            row = {'身份证号': id_card}
            for cn in IMPORT_COLUMNS:
                if cn == '身份证号':
                    continue
                if cn not in idx_map:
                    row[cn] = ''
                    continue
                v = r[idx_map[cn]]
                if v is None:
                    row[cn] = ''
                elif isinstance(v, datetime.datetime):
                    row[cn] = v.strftime('%Y-%m-%d')
                elif isinstance(v, datetime.date):
                    row[cn] = v.strftime('%Y-%m-%d')
                elif isinstance(v, (int, float)) and not isinstance(v, bool):
                    if cn in DATE_COLUMNS:
                        # Excel 日期序列号 -> YYYY-MM-DD（与前端 formatDate 的 (num-25569)*86400000 等价）
                        try:
                            serial = float(v)
                            d = datetime.datetime(1899, 12, 30) + datetime.timedelta(days=serial)
                            row[cn] = d.strftime('%Y-%m-%d')
                        except (ValueError, OverflowError):
                            row[cn] = str(v).strip()
                    else:
                        row[cn] = str(v).strip()
                else:
                    row[cn] = str(v).strip()
            rows.append(row)
    except Exception as e:
        errors.append('Excel 解析失败: ' + str(e))
    return rows, errors


def _new_progress(total_count, file_name=''):
    """导入进度初值（start_... 与测试共用，避免两处定义漂移）。"""
    return {
        'status': 'processing',
        'total': total_count,
        'current': 0,
        'success': 0,
        'skipped': 0,
        'error': 0,
        'message': '正在初始化...',
        'errors': [],
        'errors_full': [],
        'batch_distribution': {},
        'normalized': 0,
        'unrecognized': 0,
        'records_created': 0,
        'records_updated': 0,
        'unchanged': 0,
        'warnings': [],
        'import_batch_id': '',
        'file_name': file_name or '',
    }


def start_insurance_init_import(rows, progress_dict, file_name='', operator=''):
    """启动保险数据初始化导入的后台处理。

    语义（按批次精确 upsert）：
    - **目标批次**：按行解析「保险学年」（2024-2025，优先）或「批次号」，
      支持补录历史批次（不再限制"只能写当前批次/1~3"）。
    - **仅补空**：空单元格跳过、不清字段；填 `-` 才显式清空。
    - **快照统一投影**：写完记录后调用 sync_student_snapshot，杜绝两套数据分叉。
    - **校验**：批次越界、险种与批次不符、学平险/实习险列混填 → 拒绝该行。
    - 未匹配（身份证号无对应学生）计入失败明细，不动该生记录。
    - 每次导入生成 import_batch_id，可整批撤销。

    Args:
        rows: 待导入的行数据列表（每行含 身份证号 + 保险字段中文键）
        progress_dict: 进度存储字典（调用方持有，如 insurance_init_progress）
        file_name: 源文件名（留痕用）
        operator: 操作人（留痕用）

    Returns:
        (progress_id, total_count)
    """
    progress_id = secrets.token_hex(16)
    total_count = len(rows)

    # 惰性清理旧进度，防止字典内存单调增长（进行中的条目不受影响）
    _cleanup_stale_progress(progress_dict)

    progress_dict[progress_id] = _new_progress(total_count, file_name)

    def worker():
        """保险初始化导入的后台 worker 线程函数。"""
        # 后台线程无请求上下文，必须显式推送 app context，
        # 否则 models.get_db() 会走到非上下文分支、连接到错误的数据库。
        from app import app as _app
        with _app.app_context():
            try:
                _run_import(rows, progress_dict, progress_id, file_name, operator)
            except Exception as exc:          # 兜底：避免后台线程静默失败
                logger.exception('保险初始化导入失败')
                p = progress_dict.get(progress_id) or {}
                p['status'] = 'failed'
                p['message'] = f'导入失败：{exc}'
                p['completed_at'] = time.time()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    return progress_id, total_count


def _tick(progress_dict, progress_id, i, success_count, errors):
    """更新进度字典（单点维护，避免重复代码）。"""
    p = progress_dict[progress_id]
    p['current'] = i
    p['success'] = success_count
    p['error'] = len(errors)
    p['errors'] = errors[-10:]     # 轮询展示用：仅最近 10 条，保持响应轻量
    p['errors_full'] = errors      # 全量错误（CSV 导出用），存引用无拷贝开销
    p['message'] = f'正在处理第 {i}/{p["total"]} 行...'


def _has_value(row, column):
    """该单元格是否"有内容"（显式清空标记也算填写）。"""
    return bool((row.get(column) or '').strip())


def _cell(row, column):
    """读取单元格：None=未提供（仅补空时跳过），''=显式清空，其余为原值。"""
    raw = (row.get(column) or '').strip()
    if not raw:
        return None
    if raw.lower() in CLEAR_TOKENS:
        return ''
    return raw


def _resolve_target(row, student):
    """解析本行目标批次：保险学年优先，批次号兜底。返回 (batch_no, step, error)。"""
    plan = build_plan(student['enrollment_year'], student['grade'])
    steps = {s['batch_no']: s for s in plan}
    if not steps:
        return None, None, '该生缺少年级/入学年份，无法推算保险计划'

    school_year = (row.get('保险学年') or '').strip()
    batch_raw = (row.get('批次号') or '').strip()
    batch = None
    if school_year and school_year.lower() not in CLEAR_TOKENS:
        batch = batch_from_school_year(school_year, student['enrollment_year'], student['grade'])
        if batch is None and re.fullmatch(r'\d{4}', school_year):
            start = start_year(student['enrollment_year'], student['grade'])
            batch = int(school_year) - start + 1 if start else None
        if batch is None:
            return None, None, f'保险学年格式应为 2024-2025（收到：{school_year}）'
    elif batch_raw and batch_raw.lower() not in CLEAR_TOKENS:
        try:
            batch = int(float(batch_raw))
        except (TypeError, ValueError):
            return None, None, f'批次号应为数字（收到：{batch_raw}）'
    else:
        return None, None, '缺少"保险学年"或"批次号"'

    if batch not in steps:
        allowed = f'{min(steps)}~{max(steps)}' if steps else '无'
        return None, None, f'批次 {batch} 不在该生保险计划内（有效批次：{allowed}）'
    return batch, steps[batch], ''


def _prepare_values(row, student, batch, step):
    """把一行 Excel 转换为写入参数。返回 (values, warnings, error)。"""
    is_intern = step['insurance_type'] == INSURANCE_TYPE_INTERN

    # 险种与列互斥校验：填错列直接拒绝，避免把学平险数据写进实习险批次
    if is_intern:
        for column in XUEPING_COLUMNS:
            if _has_value(row, column):
                return None, None, f'批次 {batch} 为实习就业险，不能填写"{column}"'
    elif _has_value(row, '实习险缴费日期'):
        return None, None, f'批次 {batch} 为学平险，不能填写"实习险缴费日期"'

    want = (row.get('险种') or '').strip()
    if want and want.lower() not in CLEAR_TOKENS:
        if want not in (TYPE_XUEPING, TYPE_INTERN):
            return None, None, f'险种应为"{TYPE_XUEPING}"或"{TYPE_INTERN}"（收到：{want}）'
        expect = TYPE_INTERN if is_intern else TYPE_XUEPING
        if want != expect:
            return None, None, f'批次 {batch} 为{expect}，与"险种={want}"不一致'

    warnings = []

    def _val(column):
        value = _cell(row, column)
        if value is None or value == '':
            return value
        if column in DATE_COLUMNS:
            normalized = normalize_insurance_date(value)
            if not normalized:
                raise ValueError(f'"{column}"日期无法识别：{value}')
            return normalized
        return value

    try:
        if is_intern:
            pay_date = _val('实习险缴费日期')
            policy_no, expiry_date = None, None
        else:
            policy_no = _val('学平险保单号')
            pay_date = _val('学平险缴费日期')
            expiry_date = _val('学平险到期时间')
    except ValueError as exc:
        return None, None, str(exc)

    if expiry_date and not is_intern:
        start = start_year(student['enrollment_year'], student['grade'])
        if start:
            year = start + batch - 1
            try:
                if int(expiry_date[:4]) not in (year, year + 1):
                    warnings.append(
                        f'到期日 {expiry_date} 不在学年 {year}-{year + 1} 内')
            except ValueError:
                pass

    flag_raw = (row.get('主管标记') or '').strip()
    flag = None
    if flag_raw and flag_raw.lower() not in CLEAR_TOKENS:
        flag = normalize_supervisor_flag(flag_raw)

    return {'flag': flag, 'pay_date': pay_date,
            'policy_no': policy_no, 'expiry_date': expiry_date}, warnings, ''


def _record_for_batch(conn, student_id, batch_no, insurance_type):
    cur = conn.execute(
        '''SELECT * FROM insurance_records
           WHERE student_id = ? AND batch_no = ? AND insurance_type = ?
           ORDER BY id DESC LIMIT 1''',
        (student_id, batch_no, insurance_type))
    row = cur.fetchone()
    return dict(row) if row else None


def _apply_row(conn, row, student, batch, step):
    """执行一行导入（调用方负责提交/回滚）。返回结果字典。"""
    values, warnings, error = _prepare_values(row, student, batch, step)
    if error:
        return {'error': error}

    old = _record_for_batch(conn, student['id'], batch, step['insurance_type'])
    action = apply_import_record(
        conn, student['id'], student['id_card'], batch, step['insurance_type'],
        flag=values['flag'], pay_date=values['pay_date'],
        policy_no=values['policy_no'], expiry_date=values['expiry_date'],
        operator='import', partial=True)
    if action:
        sync_student_snapshot(conn, student['id'])
    new = _record_for_batch(conn, student['id'], batch, step['insurance_type'])
    return {'action': action, 'old': old, 'new': new, 'warnings': warnings,
            'batch': batch, 'insurance_type': step['insurance_type']}


_DIFF_LABELS = [('status', '状态'), ('policy_no', '保单号'),
                ('pay_date', '缴费日期'), ('expiry_date', '到期日')]


def _diff_records(old, new):
    changes = []
    for key, label in _DIFF_LABELS:
        before = (old or {}).get(key, '') or ''
        after = (new or {}).get(key, '') or ''
        if before != after:
            changes.append({'field': label, 'old': before, 'new': after})
    return changes


def preview_insurance_import(rows, limit=20):
    """预览导入：真实执行后回滚（SAVEPOINT），返回逐行 diff 与校验结果。"""
    conn = models.get_db()
    items = []
    summary = {'total': len(rows), 'ok': 0, 'reject': 0, 'warn': 0,
               'created': 0, 'updated': 0, 'unchanged': 0}

    for i, row in enumerate(rows, 1):
        id_card = (row.get('身份证号') or '').strip()
        item = {'row': i, 'id_card': id_card, 'name': '', 'batch': '',
                'school_year': (row.get('保险学年') or '').strip(),
                'type': '', 'action': '', 'changes': [], 'warnings': [],
                'error': ''}
        student = conn.execute(
            'SELECT id, id_card, name, grade, enrollment_year, is_deleted '
            'FROM students WHERE id_card = ?', (id_card,)).fetchone()
        if not student:
            item['error'] = f'身份证号 {id_card} 对应的学生不存在'
        else:
            item['name'] = student['name'] or ''
            if student['is_deleted']:
                item['error'] = '该学生已删除，将跳过'
            else:
                batch, step, error = _resolve_target(row, student)
                if error:
                    item['error'] = error
                else:
                    item['batch'] = batch
                    item['type'] = (TYPE_XUEPING if step['insurance_type']
                                    == INSURANCE_TYPE_XUEPING else TYPE_INTERN)
                    conn.execute('SAVEPOINT preview_import')
                    try:
                        result = _apply_row(conn, row, student, batch, step)
                    except Exception as exc:      # 预览失败不阻断其它行
                        result = {'error': str(exc)}
                    finally:
                        conn.execute('ROLLBACK TO preview_import')
                        conn.execute('RELEASE preview_import')
                    if result.get('error'):
                        item['error'] = result['error']
                    else:
                        item['action'] = result['action'] or ''
                        item['warnings'] = result['warnings']
                        item['changes'] = _diff_records(result['old'], result['new'])

        if item['error']:
            item['status'] = 'reject'
            summary['reject'] += 1
        elif item['warnings']:
            item['status'] = 'warn'
            summary['warn'] += 1
            summary['ok'] += 1
        else:
            item['status'] = 'ok'
            summary['ok'] += 1
        if item['action'] == 'created':
            summary['created'] += 1
        elif item['action'] == 'updated':
            summary['updated'] += 1
        elif not item['error']:
            summary['unchanged'] += 1

        if len(items) < limit:
            items.append(item)

    return {'success': True, 'columns': IMPORT_COLUMNS,
            'total': len(rows), 'summary': summary,
            'preview': items, 'errors': []}


def _run_import(rows, progress_dict, progress_id, file_name='', operator=''):
    """导入核心循环（在 app context 内执行）。

    每行：解析目标批次（保险学年优先）→ 仅补空 upsert 记录 → 快照重算；
    改动登记到导入批次，支持事后撤销。
    """
    db = models.get_db()
    batch_id = create_import_batch(db, file_name=file_name, operator=operator)
    progress_dict[progress_id]['import_batch_id'] = batch_id
    success_count = 0
    errors = []

    for i, row in enumerate(rows, 1):
        progress = progress_dict[progress_id]
        try:
            id_card = (row.get('身份证号') or '').strip()
            if not id_card:
                raise ValueError('缺少身份证号')

            student = db.execute(
                'SELECT id, id_card, name, grade, enrollment_year, is_deleted '
                'FROM students WHERE id_card = ?', (id_card,)).fetchone()
            if not student:
                raise ValueError(f'身份证号 {id_card} 对应的学生不存在')
            if student['is_deleted']:
                # 已删除（回收站）学生不在导入范围：计入 skipped 而非 error
                errors.append(f'第{i}行: 身份证号 {id_card} 对应的学生已删除，已跳过')
                progress['skipped'] = progress.get('skipped', 0) + 1
                _tick(progress_dict, progress_id, i, success_count, errors)
                continue

            batch, step, error = _resolve_target(row, student)
            if error:
                raise ValueError(error)

            result = _apply_row(db, row, student, batch, step)
            if result.get('error'):
                raise ValueError(result['error'])

            for warning in result.get('warnings', []):
                progress['warnings'].append(
                    f'第{i}行 {(student["name"] or id_card)}：{warning}')

            flag_raw = (row.get('主管标记') or '').strip()
            if flag_raw and flag_raw.lower() not in CLEAR_TOKENS:
                normalized = normalize_supervisor_flag(flag_raw)
                if normalized != re.sub(r'\s+', '', flag_raw):
                    progress['normalized'] = progress.get('normalized', 0) + 1
                    if not _is_canonical_flag(normalized):
                        progress['unrecognized'] = progress.get('unrecognized', 0) + 1

            if result['action']:
                record_import_entry(
                    db, batch_id, result['new'], student['id'], result['action'],
                    snapshot_record(result['old']), snapshot_record(result['new']))
                success_count += 1
                key = str(batch)
                progress['batch_distribution'][key] = \
                    progress['batch_distribution'].get(key, 0) + 1
                if result['action'] == 'created':
                    progress['records_created'] = progress.get('records_created', 0) + 1
                else:
                    progress['records_updated'] = progress.get('records_updated', 0) + 1
            else:
                progress['unchanged'] = progress.get('unchanged', 0) + 1

            db.commit()
        except Exception as e:
            errors.append(f'第{i}行: {str(e)}')

        _tick(progress_dict, progress_id, i, success_count, errors)

    finish_import_batch(db, batch_id, len(rows), success_count)
    db.commit()

    progress_dict[progress_id]['status'] = 'completed'
    progress_dict[progress_id]['message'] = '导入完成！'
    progress_dict[progress_id]['success'] = success_count
    progress_dict[progress_id]['error'] = len(errors)
    progress_dict[progress_id]['errors'] = errors[-10:]
    progress_dict[progress_id]['errors_full'] = errors
    progress_dict[progress_id]['completed_at'] = time.time()
