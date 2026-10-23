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

import models
import config


# 导入模板列（中文表头）与 DB 字段映射
IMPORT_COLUMNS = ['身份证号', '学平险保单号', '学平险缴费日期', '学平险到期时间', '实习险缴费日期', '主管标记']
FIELD_MAPPING = {
    '学平险保单号': 'student_insurance_no',
    '学平险缴费日期': 'student_insurance_pay_date',
    '学平险到期时间': 'student_insurance_expiry',
    '实习险缴费日期': 'internship_insurance_pay_date',
    '主管标记': 'supervisor_flag',
}
# 学平险相关列：当学生应为"第3年实习就业险"(batch=3) 时整列忽略
XUEPING_COLUMNS = ['学平险保单号', '学平险缴费日期', '学平险到期时间']
# 日期型列：导入时 Excel 常以 int/float 序列号形式读入，需转成 YYYY-MM-DD 字符串
DATE_COLUMNS = {'学平险缴费日期', '学平险到期时间', '实习险缴费日期'}

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
    """计算学生保险批次（第几年），返回 1~3 的整数或 None（无法判定 / 超出 3 年制）。

    与 blueprints/insurance.py 中的同名函数保持一致：先判定学生类别，再计算，
    保证 普通 / 转入 / 特殊 三种情况任何时刻都正确：
      - 普通：grade == enrollment_year           -> 起始年 = grade
      - 转入：grade <  enrollment_year           -> 起始年 = grade（按本校年级进度）
      - 特殊：grade >  enrollment_year           -> 年级由初中毕业时间推算，可能偏高：
            * 若 grade 超出当前学年（被算飞到未来），以 enrollment_year 为准；
            * 否则以 grade 为准（年级合法、入学年被压低）。
    batch = current_year − 起始年 + 1；仅 1~3 显示。
    """
    if current_year is None:
        current_year = config.CURRENT_SCHOOL_YEAR
    try:
        g = int(grade)
    except (TypeError, ValueError):
        return None
    try:
        en = int(enrollment_year)
    except (TypeError, ValueError):
        en = None
    if g is None:
        return None
    if en is None:
        b = current_year - g + 1
        return b if 1 <= b <= 3 else None
    if g == en:
        start = g                       # 普通
    elif g < en:
        start = g                       # 转入：按本校年级进度
    else:  # g > en -> 特殊
        start = en if g > current_year else g
    b = current_year - start + 1
    return b if 1 <= b <= 3 else None


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


def start_insurance_init_import(rows, progress_dict):
    """启动保险数据初始化导入的后台处理。

    语义（Phase 6 加固）：
    - **全量覆盖**：匹配到的学生，该行提供的保险列整体覆盖库内对应字段；
      空单元格按覆盖语义清空该字段（Excel 为该生该列完整真相）。
    - **主管标记轻量归一化**：2 位年→4 位、年→年度、同义归一、留空默认待办结。
    - **batch 感知**：按学生 grade 算 current_batch；batch=3（实习就业险年）
      忽略学平险列（系统无学平险/保单号字段）。
    - 未匹配（身份证号无对应学生）计入失败明细，不动该生记录。

    Args:
        rows: 待导入的行数据列表（每行含 身份证号 + 保险字段中文键）
        progress_dict: 进度存储字典（调用方持有，如 insurance_init_progress）

    Returns:
        (progress_id, total_count)
    """
    progress_id = secrets.token_hex(16)
    total_count = len(rows)

    # 惰性清理旧进度，防止字典内存单调增长（进行中的条目不受影响）
    _cleanup_stale_progress(progress_dict)

    progress_dict[progress_id] = {
        'status': 'processing',
        'total': total_count,
        'current': 0,
        'success': 0,
        'skipped': 0,
        'error': 0,
        'message': '正在初始化...',
        'errors': [],
        'errors_full': [],
        'batch_distribution': {1: 0, 2: 0, 3: 0},
        'normalized': 0,
        'unrecognized': 0,
        'ignored_xueping': 0,
    }

    def worker():
        """保险初始化导入的后台 worker 线程函数。"""
        # 后台线程无请求上下文，必须显式推送 app context，
        # 否则 models.get_db() 会走到非上下文分支、连接到错误的数据库。
        from app import app as _app
        with _app.app_context():
            _run_import(rows, progress_dict, progress_id)

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


def _run_import(rows, progress_dict, progress_id):
    """导入核心循环（在 app context 内执行）。"""
    success_count = 0
    errors = []

    for i, row in enumerate(rows, 1):
        try:
            id_card = (row.get('身份证号') or '').strip()
            if not id_card:
                errors.append(f'第{i}行: 缺少身份证号')
                _tick(progress_dict, progress_id, i, success_count, errors)
                continue

            existing = models.get_db().execute(
                'SELECT id, grade, enrollment_year, supervisor_flag, is_deleted FROM students WHERE id_card = ?',
                (id_card,)
            ).fetchone()

            if not existing:
                errors.append(f'第{i}行: 身份证号 {id_card} 对应的学生不存在')
                _tick(progress_dict, progress_id, i, success_count, errors)
                continue

            if existing['is_deleted']:
                # 已删除（回收站）学生不在导入范围：计入 skipped 而非 error，避免误报警
                errors.append(f'第{i}行: 身份证号 {id_card} 对应的学生已删除，已跳过')
                progress_dict[progress_id]['skipped'] = \
                    progress_dict[progress_id].get('skipped', 0) + 1
                _tick(progress_dict, progress_id, i, success_count, errors)
                continue

            # batch 感知：current_batch = CURRENT_SCHOOL_YEAR − grade + 1
            current_batch = compute_insurance_batch(existing['grade'], existing['enrollment_year'])

            fields = {}
            ignored_xp = False
            supervisor_raw_blank = False
            for cn, en in FIELD_MAPPING.items():
                if en == 'supervisor_flag':
                    raw = (row.get(cn) or '').strip()
                    if raw:
                        norm = normalize_supervisor_flag(raw)
                        # 归一化计数（原值非空且与归一化结果不同）
                        if norm != re.sub(r'\s+', '', raw):
                            progress_dict[progress_id]['normalized'] = \
                                progress_dict[progress_id].get('normalized', 0) + 1
                            if not _is_canonical_flag(norm):
                                progress_dict[progress_id]['unrecognized'] = \
                                    progress_dict[progress_id].get('unrecognized', 0) + 1
                    else:
                        # 主管标记留空：标记 raw 为空，后续按 batch 决定去向
                        #   batch=1 → 占位缴费日期 + 按计划办理学平险（直接进待办，等同人工确认）
                        #   batch=2/3 → 保持「待处理」(空串)，走 登记→审批→待办 正常流
                        supervisor_raw_blank = True
                        norm = ''
                    fields[en] = norm
                    continue

                # batch=3 忽略学平险列
                if current_batch == 3 and cn in XUEPING_COLUMNS:
                    if (row.get(cn) or '').strip():
                        ignored_xp = True
                    continue

                # 全量覆盖：空单元格也写入（清空该字段）
                val = (row.get(cn) or '').strip()
                fields[en] = val

            if ignored_xp:
                progress_dict[progress_id]['ignored_xueping'] = \
                    progress_dict[progress_id].get('ignored_xueping', 0) + 1

            # batch=1 空标记：占位标记「新生默认首保」+ 按计划办理学平险（直接进待办，等同人工确认）
            # 不写真实缴费日期——外部流转学生在系统内并未缴费，写日期会误导查账。
            if current_batch == 1 and supervisor_raw_blank:
                fields['supervisor_flag'] = config.InsuranceStatus.PENDING_XUEPING
                if _is_blank(fields.get('student_insurance_pay_date')):
                    fields['student_insurance_pay_date'] = config.InsuranceStatus.EXTERNAL_PAY_MARKER

            models.update_student_insurance(existing['id'], fields)
            success_count += 1
            if current_batch in (1, 2, 3):
                progress_dict[progress_id]['batch_distribution'][current_batch] += 1

        except Exception as e:
            errors.append(f'第{i}行: {str(e)}')

        _tick(progress_dict, progress_id, i, success_count, errors)

    progress_dict[progress_id]['status'] = 'completed'
    progress_dict[progress_id]['message'] = '导入完成！'
    progress_dict[progress_id]['success'] = success_count
    progress_dict[progress_id]['error'] = len(errors)
    progress_dict[progress_id]['errors'] = errors[-10:]
    progress_dict[progress_id]['errors_full'] = errors
    progress_dict[progress_id]['completed_at'] = time.time()
