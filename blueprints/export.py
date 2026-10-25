import os
import re
import secrets
import time
from datetime import datetime

from flask import Blueprint, request, session, jsonify, render_template, send_file, flash
from werkzeug.utils import secure_filename
from openpyxl import load_workbook

import config
import models
import auth
from auth import _require_admin_json

bp = Blueprint('export', __name__)

# 异步任务进度存储（内存）
_export_tasks = {}

EXPORT_TEMPLATE_FOLDER = config.TEMPLATE_FOLDER
EXPORT_OUTPUT_FOLDER = config.EXPORT_OUTPUT_FOLDER

os.makedirs(EXPORT_TEMPLATE_FOLDER, exist_ok=True)
os.makedirs(EXPORT_OUTPUT_FOLDER, exist_ok=True)

# 白名单：允许导出的数据源字段（DB 列名）
EXPORT_ALLOWED_FIELDS = list(config.FIELD_MAPPING.values())
# 添加特殊计算字段
EXPORT_ALLOWED_FIELDS.extend(['age'])


def _resolve_template_path(file_path):
    """解析模板文件路径：兼容绝对路径（旧记录）和相对文件名（新记录）。
    旧记录存的是绝对路径（换环境后可能失效），新记录只存文件名。
    解析顺序：1) 原路径若存在直接用 2) 拼接 EXPORT_TEMPLATE_FOLDER 后检查 3) 都找不到返回拼接路径"""
    if not file_path:
        return file_path
    if os.path.exists(file_path):
        return file_path
    relative_path = os.path.join(EXPORT_TEMPLATE_FOLDER, os.path.basename(file_path))
    if os.path.exists(relative_path):
        return relative_path
    return relative_path  # 返回拼接路径，让调用方 os.path.exists 判 False 并报错


def _iter_element_paragraphs(element):
    """遍历 XML 子树中的所有段落。

    直接按 w:p 递归遍历，天然覆盖正文段落、表格单元格、嵌套表格与文本框
    （w:txbxContent），每个段落只访问一次。
    """
    from docx.oxml.ns import qn
    from docx.text.paragraph import Paragraph

    for p in element.iter(qn('w:p')):
        yield Paragraph(p, None)


def _iter_header_footer_paragraphs(doc):
    """遍历所有节（含首页/奇偶页）的页眉页脚段落。"""
    for section in doc.sections:
        headers_footers = [
            section.header, section.footer,
            section.first_page_header, section.first_page_footer,
            section.even_page_header, section.even_page_footer,
        ]
        for hf in headers_footers:
            yield from _iter_element_paragraphs(hf._element)


def _iter_docx_paragraphs(doc):
    """遍历 docx 中所有可能包含 ${占位符} 的段落：正文（含表格/文本框）+ 页眉/页脚。

    注意：headers/footers 属于"节"级别，不属于 body 子树，需单独遍历。
    """
    yield from _iter_element_paragraphs(doc.element.body)
    yield from _iter_header_footer_paragraphs(doc)


def _parse_template_placeholders(file_path):
    """解析模板文件中的 ${xxx} 占位符，返回去重后的占位符列表"""
    import re
    placeholders = set()
    pattern = re.compile(r'\$\{([^}]+)\}')
    ext = os.path.splitext(file_path)[1].lower()

    if ext in ('.xlsx', '.xls'):
        from openpyxl import load_workbook
        wb = load_workbook(file_path, data_only=True)
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    if cell.value and isinstance(cell.value, str):
                        found = pattern.findall(cell.value)
                        placeholders.update(found)
        wb.close()
    elif ext == '.docx':
        from docx import Document
        doc = Document(file_path)
        for para in _iter_docx_paragraphs(doc):
            found = pattern.findall(para.text)
            placeholders.update(found)

    return sorted(list(placeholders))


def _get_export_filter_from_session():
    """从 session 恢复筛选条件"""
    filter_data = session.get('export_filter', {})
    return filter_data if isinstance(filter_data, dict) else {}


def _apply_filter_to_query(filters):
    """应用筛选条件获取学生数据（白名单安全）"""
    conn = models.get_db()
    cursor = conn.cursor()

    where = ['s.is_deleted = 0', models._exclude_graduated_clause('s')]
    params = []

    allowed_filter_fields = [
        'school', 'grade', 'major', 'admin_class', 'virtual_class',
        'registration_status', 'attendance_status', 'gender', 'ethnicity',
        'enrollment_year', 'major_direction', 'admission_type'
    ]

    for field in allowed_filter_fields:
        val = filters.get(field)
        if val is not None and val != '':
            if isinstance(val, list):
                placeholders = ','.join(['?' for _ in val])
                where.append(f's.{field} IN ({placeholders})')
                params.extend(val)
            else:
                where.append(f's.{field} = ?')
                params.append(val)

    search = filters.get('search', '').strip()
    if search:
        where.append('(s.name LIKE ? OR s.student_no LIKE ? OR s.name_pinyin LIKE ? OR s.id_card LIKE ?)')
        params.extend([f'%{search}%', f'%{search}%', f'%{search}%', f'%{search}%'])

    # 姓名筛选：支持中文姓名与拼音（name_pinyin 形如 "ZhangSan"，SQLite LIKE 对 ASCII 不区分大小写）
    name = filters.get('name', '').strip()
    if name:
        where.append('(s.name LIKE ? OR s.name_pinyin LIKE ?)')
        params.extend([f'%{name}%', f'%{name}%'])

    where_clause = ' AND '.join(where)
    sql = f'SELECT * FROM students s WHERE {where_clause} ORDER BY s.grade, s.admin_class, s.name'
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


@bp.route('/admin/export-engine')
@auth.admin_required
def admin_export_engine():
    """模板导出引擎页面"""
    # 从 students 页面跳转过来时保存筛选条件到 session
    export_filter = request.args.get('save_filter', '')
    if export_filter == '1':
        filter_data = {}
        for key in ['school', 'grade', 'major', 'admin_class', 'virtual_class',
                     'registration_status', 'attendance_status', 'gender',
                     'ethnicity', 'enrollment_year', 'major_direction',
                     'admission_type', 'name', 'search']:
            val = request.args.get(key, '').strip()
            if val:
                filter_data[key] = val
        if filter_data:
            session['export_filter'] = filter_data
            session.permanent = True
            flash('已捕获当前筛选条件，请在导出页面确认', 'info')

    return render_template('admin/export_engine.html')


@bp.route('/api/export/upload-template', methods=['POST'])
@auth.admin_required
def api_export_upload_template():
    """上传模板文件，解析占位符"""
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error

    if 'file' not in request.files:
        return jsonify({'success': False, 'message': '请选择文件'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'message': '请选择文件'}), 400

    ext = os.path.splitext(file.filename)[1].lower().lstrip('.')
    if ext not in config.TEMPLATE_ALLOWED_EXTENSIONS:
        return jsonify({'success': False, 'message': f'不支持的文件类型: .{ext}'}), 400

    # 显示名用原始文件名（保留中文），落盘名用 ASCII 安全名 + 原始扩展名。
    # 注意：secure_filename 会剥离非 ASCII 字符，纯中文名（如"模板.docx"）会被剥成
    # "docx" 并丢掉扩展名，导致后续 os.path.splitext 取不到 .docx/.xlsx 而无法解析。
    display_name = os.path.basename(file.filename.replace('\\', '/'))
    safe_stem = os.path.splitext(secure_filename(file.filename))[0]
    if not safe_stem or safe_stem == ext:
        safe_stem = 'template'
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    unique_name = f'{timestamp}_{safe_stem}.{ext}'
    save_path = os.path.join(EXPORT_TEMPLATE_FOLDER, unique_name)
    file.save(save_path)

    try:
        placeholders = _parse_template_placeholders(save_path)
    except Exception as e:
        try:
            os.remove(save_path)
        except OSError:
            pass
        return jsonify({'success': False, 'message': f'模板解析失败: {str(e)}'}), 400

    if not placeholders:
        try:
            os.remove(save_path)
        except OSError:
            pass
        return jsonify({'success': False, 'message': '模板中未找到 ${占位符}，请检查模板格式'}), 400

    file_type = 'xlsx' if ext in ('xlsx', 'xls') else 'docx'
    # 存相对文件名而非绝对路径，避免换环境后路径失效
    template_id = models.create_template(display_name, unique_name, file_type, placeholders)

    return jsonify({
        'success': True,
        'template': {
            'id': template_id,
            'name': display_name,
            'file_type': file_type,
            'placeholders': placeholders
        }
    })


@bp.route('/api/export/templates')
@auth.admin_required
def api_export_templates():
    """获取所有已上传的模板列表"""
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error

    templates = models.get_all_templates()
    return jsonify({'success': True, 'templates': templates})


@bp.route('/api/export/template/<int:template_id>', methods=['DELETE'])
@auth.admin_required
def api_export_delete_template(template_id):
    """删除模板及其关联文件"""
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error

    t = models.get_template(template_id)
    if not t:
        return jsonify({'success': False, 'message': '模板不存在'}), 404

    try:
        if os.path.exists(_resolve_template_path(t['file_path'])):
            os.remove(_resolve_template_path(t['file_path']))
    except OSError:
        pass

    models.delete_template(template_id)
    return jsonify({'success': True})


@bp.route('/api/export/available-fields')
@auth.admin_required
def api_export_available_fields():
    """获取可用的数据源字段白名单"""
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error

    fields = []
    for cn_name, db_field in config.FIELD_MAPPING.items():
        fields.append({
            'chinese_name': cn_name,
            'db_field': db_field,
            'table': 'students'
        })

    # 添加计算字段
    fields.append({
        'chinese_name': '年龄',
        'db_field': 'age',
        'table': 'students'
    })

    # 获取当前筛选条件下的记录总数
    filter_data = _get_export_filter_from_session()
    filtered_students = _apply_filter_to_query(filter_data)
    total = len(filtered_students)

    # 取第一条数据作为预览样本
    sample = filtered_students[0] if filtered_students else None
    if sample:
        # 计算 age
        if sample.get('birth_date'):
            try:
                bd = datetime.strptime(sample['birth_date'], '%Y-%m-%d')
                today = datetime.today()
                sample['age'] = today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
            except (ValueError, TypeError):
                sample['age'] = ''
        else:
            sample['age'] = ''
        # 学校类型转换（以 school_types 字典为准）
        sample['_school_name'] = models.get_school_type_name(sample.get('school'))

    return jsonify({
        'success': True,
        'fields': fields,
        'total_count': total,
        'sample': sample,
        'filter': filter_data,
        'filter_values': models.get_filter_values()
    })


@bp.route('/api/export/save-mapping', methods=['POST'])
@auth.admin_required
def api_export_save_mapping():
    """保存字段映射方案"""
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error

    data = request.get_json()
    template_id = data.get('template_id')
    scheme_name = data.get('scheme_name', '').strip()
    mappings = data.get('mappings', [])

    if not template_id:
        return jsonify({'success': False, 'message': '缺少模板ID'}), 400
    if not scheme_name:
        return jsonify({'success': False, 'message': '请输入方案名称'}), 400
    if not mappings:
        return jsonify({'success': False, 'message': '请配置至少一个字段映射'}), 400

    # 白名单校验 source_field
    for m in mappings:
        sf = m.get('source_field', '')
        if sf and sf not in EXPORT_ALLOWED_FIELDS:
            return jsonify({'success': False, 'message': f'字段 "{sf}" 不在允许导出的白名单中'}), 403

    mapping_id = models.save_field_mapping(template_id, scheme_name, mappings)
    return jsonify({'success': True, 'mapping_id': mapping_id})


@bp.route('/api/export/mappings/<int:template_id>')
@auth.admin_required
def api_export_get_mappings(template_id):
    """获取模板的所有映射方案"""
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error

    mappings = models.get_field_mappings_by_template(template_id)
    return jsonify({'success': True, 'mappings': mappings})


@bp.route('/api/export/mapping/<int:mapping_id>', methods=['DELETE'])
@auth.admin_required
def api_export_delete_mapping(mapping_id):
    """删除映射方案"""
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error

    models.delete_field_mapping(mapping_id)
    return jsonify({'success': True})


@bp.route('/api/export/execute', methods=['POST'])
@auth.admin_required
def api_export_execute():
    """执行同步导出"""
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error

    data = request.get_json()
    template_id = data.get('template_id')
    mappings = data.get('mappings', [])

    if not template_id:
        return jsonify({'success': False, 'message': '缺少模板ID'}), 400
    if not mappings:
        return jsonify({'success': False, 'message': '请配置字段映射'}), 400

    # 白名单校验
    for m in mappings:
        sf = m.get('source_field', '')
        if sf and sf not in EXPORT_ALLOWED_FIELDS:
            return jsonify({'success': False, 'message': f'字段 "{sf}" 不在允许导出的白名单中'}), 403

    tpl = models.get_template(template_id)
    if not tpl:
        return jsonify({'success': False, 'message': '模板不存在'}), 404
    template_file = _resolve_template_path(tpl['file_path'])
    if not os.path.exists(template_file):
        return jsonify({'success': False, 'message': '模板文件已丢失，请重新上传'}), 404

    # 获取筛选后的数据
    filter_data = _get_export_filter_from_session()
    students = _apply_filter_to_query(filter_data)

    total = len(students)
    if total == 0:
        return jsonify({'success': False, 'message': '筛选条件下无数据可导出'}), 400
    if total > config.EXPORT_MAX_ROWS:
        return jsonify({'success': False, 'message': f'导出行数({total})超过上限({config.EXPORT_MAX_ROWS})，请缩小筛选范围'}), 400

    try:
        file_type = tpl['file_type']

        if file_type == 'xlsx':
            output_path = _export_xlsx(template_file, students, mappings)
        elif file_type == 'docx':
            output_path = _export_docx(template_file, students, mappings)
        else:
            return jsonify({'success': False, 'message': f'不支持的文件类型: {file_type}'}), 400

        # 记录导出历史
        scheme_name = data.get('scheme_name', tpl.get('name', ''))
        created_by = session.get('display_name', session.get('username', ''))
        models.add_export_history(template_id, tpl['name'], scheme_name, total, output_path, created_by)

        # 返回下载
        download_name = f'导出结果_{datetime.now().strftime("%Y%m%d_%H%M%S")}.{file_type}'
        return send_file(
            output_path,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' if file_type == 'xlsx' else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            as_attachment=True,
            download_name=download_name
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': f'导出失败: {str(e)}'}), 500


@bp.route('/api/export/execute-async', methods=['POST'])
@auth.admin_required
def api_export_execute_async():
    """异步导出（阶段三实现，当前返回提示）"""
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error
    return jsonify({'success': False, 'message': '异步导出功能将在后续版本中实现，请使用同步导出'}), 501


@bp.route('/api/export/task-status/<task_id>')
@auth.admin_required
def api_export_task_status(task_id):
    """查询异步任务进度（阶段三实现）"""
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error
    if task_id in _export_tasks:
        return jsonify({'success': True, 'task': _export_tasks[task_id]})
    return jsonify({'success': False, 'message': '任务不存在'}), 404


@bp.route('/api/export/update-filter', methods=['POST'])
@auth.admin_required
def api_export_update_filter():
    """更新导出的筛选条件"""
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error

    data = request.get_json()
    filter_data = {}
    for key in ['school', 'grade', 'major', 'admin_class', 'virtual_class',
                 'registration_status', 'attendance_status', 'gender',
                 'ethnicity', 'enrollment_year', 'major_direction',
                 'admission_type', 'name', 'search']:
        val = data.get(key, '').strip()
        if val:
            filter_data[key] = val
    session['export_filter'] = filter_data
    session.permanent = True

    students = _apply_filter_to_query(filter_data)
    return jsonify({'success': True, 'total_count': len(students), 'filter': filter_data})


@bp.route('/api/export/preview', methods=['POST'])
@auth.admin_required
def api_export_preview():
    """预览导出效果（前 5 条数据）"""
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error

    data = request.get_json()
    mappings = data.get('mappings', [])

    filter_data = _get_export_filter_from_session()
    students = _apply_filter_to_query(filter_data)

    return jsonify({
        'success': True,
        'preview_rows': _build_preview_rows(students, mappings, 5),
        'total_count': len(students)
    })


@bp.route('/api/export/history', methods=['GET', 'DELETE'])
@auth.admin_required
def api_export_history():
    """获取或清空导出历史"""
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error

    if request.method == 'DELETE':
        models.clear_export_history()
        return jsonify({'success': True, 'message': '导出历史已清空'})

    history = models.get_export_history(50)
    return jsonify({'success': True, 'history': history})


def _export_xlsx(template_path, students, mappings, empty_cells=None):
    """执行 Excel 模板填充导出。

    empty_cells: 预览用可选集合，收集"占位符替换后为空"的单元格
    (工作表名, 行号, 列号)，用于在预览中标注空值位置。
    """
    from copy import copy
    wb = load_workbook(template_path)
    pattern = re.compile(r'\$\{([^}]+)\}')
    school_names = models.get_school_type_map()

    for ws in wb.worksheets:
        # 收集需要替换的单元格
        replacements = []
        for row in ws.iter_rows():
            for cell in row:
                if cell.value and isinstance(cell.value, str):
                    found = pattern.findall(cell.value)
                    if found:
                        replacements.append(cell)

        if not replacements:
            continue

        # 确定数据起始行（取第一个占位符所在行作为模板行）
        if replacements:
            template_row = replacements[0].row

        # 保存模板行的原始值（含占位符），供后续行复制
        original_values = {}
        for col_idx in range(1, ws.max_column + 1):
            original_values[col_idx] = ws.cell(row=template_row, column=col_idx).value

        # 在模板行之后插入数据行（而非之前，避免模板行被推走导致空行）
        if len(students) > 1:
            ws.insert_rows(template_row + 1, len(students) - 1)

        # 为每个学生填充一行
        for idx, student in enumerate(students):
            # 计算 age
            if student.get('birth_date'):
                try:
                    bd = datetime.strptime(student['birth_date'], '%Y-%m-%d')
                    today = datetime.today()
                    student['age'] = today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
                except (ValueError, TypeError):
                    student['age'] = ''
            else:
                student['age'] = ''
            student['_school_name'] = school_names.get(student.get('school'), '')

            current_row = template_row + idx

            # 非首行：复制模板行的原始值（含占位符）和样式到当前行
            if idx > 0:
                for col_idx in range(1, ws.max_column + 1):
                    src = ws.cell(row=template_row, column=col_idx)
                    tgt = ws.cell(row=current_row, column=col_idx)
                    tgt.value = original_values.get(col_idx)
                    if src.has_style:
                        tgt.font = copy(src.font)
                        tgt.border = copy(src.border)
                        tgt.fill = copy(src.fill)
                        tgt.number_format = src.number_format
                        tgt.protection = copy(src.protection)
                        tgt.alignment = copy(src.alignment)
            else:
                # 首行：模板行本身，只需复制样式（值已存在）
                for col_idx in range(1, ws.max_column + 1):
                    src = ws.cell(row=template_row, column=col_idx)
                    tgt = ws.cell(row=current_row, column=col_idx)
                    if src.has_style:
                        tgt.font = copy(src.font)
                        tgt.border = copy(src.border)
                        tgt.fill = copy(src.fill)
                        tgt.number_format = src.number_format
                        tgt.protection = copy(src.protection)
                        tgt.alignment = copy(src.alignment)

            # 替换占位符
            for col_idx in range(1, ws.max_column + 1):
                cell = ws.cell(row=current_row, column=col_idx)
                if cell.value and isinstance(cell.value, str):
                    original = cell.value
                    modified = original
                    for m in mappings:
                        ph_key = m.get('placeholder', '')  # 如 "${name}"
                        sf = m.get('source_field', '')
                        dv = m.get('default_value', '')

                        # 如果 placeholder 带 ${} 格式，提取纯名
                        ph_match = pattern.match(ph_key)
                        if ph_match:
                            pure_key = ph_match.group(1)
                        else:
                            pure_key = ph_key

                        if sf and sf in student:
                            val = student[sf]
                            if val is None:
                                val = dv
                            else:
                                val = str(val)
                        elif sf == '_school_name':
                            val = student.get('_school_name', dv)
                        else:
                            val = dv

                        placeholder_pattern = '${' + pure_key + '}'
                        if placeholder_pattern in modified:
                            modified = modified.replace(placeholder_pattern, val)
                            # 对于纯占位符单元格（单元格内容只有一个占位符），保留替换值的类型
                            if original.strip() == placeholder_pattern:
                                cell.value = val
                                if empty_cells is not None and val == '':
                                    empty_cells.add((ws.title, current_row, col_idx))
                            else:
                                cell.value = modified

    output_path = os.path.join(EXPORT_OUTPUT_FOLDER, f'export_{datetime.now().strftime("%Y%m%d_%H%M%S")}_{secrets.token_hex(4)}.xlsx')
    wb.save(output_path)
    wb.close()
    return output_path


def _page_break_paragraph():
    """构造仅含分页符的段落（w:br type="page"），用于学生之间分页。"""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    p = OxmlElement('w:p')
    r = OxmlElement('w:r')
    br = OxmlElement('w:br')
    br.set(qn('w:type'), 'page')
    r.append(br)
    p.append(r)
    return p


def _paragraph_runs(para):
    """按文档顺序返回段落内的文本 run。

    含超链接（w:hyperlink）内的 run（para.text 会把它们算进段落文字，但
    para.runs 不返回）；不含文本框内嵌段落的 run（那些段落会单独遍历）。
    """
    from docx.oxml.ns import qn
    from docx.text.run import Run

    runs = []
    for child in para._p.iterchildren():
        if child.tag == qn('w:r'):
            runs.append(Run(child, para))
        elif child.tag == qn('w:hyperlink'):
            for r in child.iterchildren(qn('w:r')):
                runs.append(Run(r, para))
    return runs


def _replace_across_runs(runs, old, new):
    """在段落内把 old 替换为 new，并保留各 run 原有的字体、字号等格式。

    Word 常把一段连续文字拆成多个 run（如 ${学生 姓名}），占位符可能跨 run。
    做法：新值写入占位符起始所在的 run（沿用该 run 的字符格式），其余被覆盖的
    run 只删除占位符字符，不改动其格式属性。
    返回 True 表示发生了替换。
    """
    if not runs:
        return False

    changed = False
    search_from = 0
    while True:
        texts = [r.text for r in runs]
        full = ''.join(texts)
        start = full.find(old, search_from)
        if start == -1:
            break
        end = start + len(old)

        # 字符位置 → run 索引；各 run 在 full 中的起始偏移
        owner = []
        for i, t in enumerate(texts):
            owner.extend([i] * len(t))
        first, last = owner[start], owner[end - 1]
        offsets, acc = [], 0
        for t in texts:
            offsets.append(acc)
            acc += len(t)

        for i in range(first, last + 1):
            rs = offsets[i]
            head = texts[i][:max(0, start - rs)]
            tail = texts[i][max(0, end - rs):]
            texts[i] = (head + new + tail) if i == first else (head + tail)

        for r, t in zip(runs, texts):
            r.text = t

        changed = True
        search_from = start + len(new)

    return changed


def _fill_paragraph(para, student, mappings, pattern):
    """将段落中的 ${占位符} 替换为学生数据，保留模板原有字体与字号。"""
    runs = _paragraph_runs(para)
    if not runs:
        return
    # 段落内没有占位符时直接跳过，避免大批量导出时做无谓遍历
    if '$' not in ''.join(r.text for r in runs):
        return

    for m in mappings:
        ph_key = m.get('placeholder', '')
        sf = m.get('source_field', '')
        dv = m.get('default_value', '')
        ph_match = pattern.match(ph_key)
        pure_key = ph_match.group(1) if ph_match else ph_key
        if sf and sf in student:
            val = student[sf]
            val = str(val) if val is not None else dv
        elif sf == '_school_name':
            val = student.get('_school_name', dv)
        else:
            val = dv
        _replace_across_runs(runs, '${' + pure_key + '}', val)


def _export_docx(template_path, students, mappings):
    """执行 Word 模板填充导出：每个学生一页，输出单个 .docx。

    模板正文（body 中除末尾 sectPr 外的全部元素）视为"一页"版式，逐份 deepcopy
    后分别填充，份与份之间插入分页符；sectPr（页面设置）在文末只保留一份。
    页眉/页脚属于"节"级别，整篇共用，用第一条数据填充。
    """
    import copy
    from docx import Document
    from docx.oxml.ns import qn

    pattern = re.compile(r'\$\{([^}]+)\}')
    school_names = models.get_school_type_map()

    doc = Document(template_path)
    body = doc.element.body
    sect_pr = body.find(qn('w:sectPr'))

    def _insert(el):
        """插入到文末 sectPr 之前，保证页面设置始终位于文档最后。"""
        if sect_pr is not None:
            sect_pr.addprevious(el)
        else:
            body.append(el)

    # 取出模板"一页"区块后清空正文
    block = [el for el in body.iterchildren() if el is not sect_pr]
    for el in block:
        body.remove(el)

    for idx, student in enumerate(students):
        if student.get('birth_date'):
            try:
                bd = datetime.strptime(student['birth_date'], '%Y-%m-%d')
                today = datetime.today()
                student['age'] = today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
            except (ValueError, TypeError):
                student['age'] = ''
        else:
            student['age'] = ''
        student['_school_name'] = school_names.get(student.get('school'), '')

        if idx > 0:
            _insert(_page_break_paragraph())

        for el in block:
            new_el = copy.deepcopy(el)
            _insert(new_el)
            for para in _iter_element_paragraphs(new_el):
                _fill_paragraph(para, student, mappings, pattern)

    # 页眉/页脚为整篇共用，用第一条数据填充（若模板中无占位符则为空操作）
    header_student = students[0] if students else {}
    for para in _iter_header_footer_paragraphs(doc):
        _fill_paragraph(para, header_student, mappings, pattern)

    output_path = os.path.join(EXPORT_OUTPUT_FOLDER, f'export_{datetime.now().strftime("%Y%m%d_%H%M%S")}_{secrets.token_hex(4)}.docx')
    doc.save(output_path)
    return output_path


# ========== 导出预览：生成真实文件 + Excel 网格化渲染 ==========

_PREVIEW_FILES = {}  # token -> {'path': 文件路径, 'expires_at': 过期时间戳}


def _build_preview_rows(students, mappings, limit=5):
    """按映射把学生数据填充成预览行（placeholder -> 展示值）。"""
    school_names = models.get_school_type_map()
    preview_rows = []

    for student in students[:limit]:
        if student.get('birth_date'):
            try:
                bd = datetime.strptime(student['birth_date'], '%Y-%m-%d')
                today = datetime.today()
                student['age'] = today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
            except (ValueError, TypeError):
                student['age'] = ''
        else:
            student['age'] = ''
        student['_school_name'] = school_names.get(student.get('school'), '')

        row = {}
        for m in mappings:
            ph = m.get('placeholder', '')
            sf = m.get('source_field', '')
            dv = m.get('default_value', '')
            if sf and sf in student:
                val = student[sf]
                row[ph] = str(val) if val is not None else dv
            elif sf == '_school_name':
                row[ph] = student.get('_school_name', dv)
            else:
                row[ph] = dv
        preview_rows.append(row)

    return preview_rows


def _cleanup_expired_previews():
    """清理超过 TTL 的预览文件。"""
    now = time.time()
    for token in [t for t, info in _PREVIEW_FILES.items() if info['expires_at'] <= now]:
        info = _PREVIEW_FILES.pop(token)
        try:
            os.remove(info['path'])
        except OSError:
            pass


def _register_preview_file(path):
    """登记预览文件并返回下载 token。"""
    _cleanup_expired_previews()
    token = secrets.token_urlsafe(16)
    _PREVIEW_FILES[token] = {
        'path': path,
        'expires_at': time.time() + config.EXPORT_TEMP_FILE_TTL_SECONDS,
    }
    return token


def _argb_to_hex(color):
    """ARGB → #RRGGBB；主题色/索引色/全透明返回 None（交由前端继承默认色）。"""
    if color is None:
        return None
    rgb = getattr(color, 'rgb', None)
    if not isinstance(rgb, str):
        return None
    if len(rgb) == 8:
        if rgb[:2] == '00':
            return None
        rgb = rgb[2:]
    if len(rgb) != 6:
        return None
    return '#' + rgb.upper()


def _cell_font(cell):
    """提取字体样式（只输出与默认值不同的项）。"""
    f = cell.font
    style = {}
    if f.bold:
        style['b'] = 1
    if f.italic:
        style['i'] = 1
    if f.underline and f.underline != 'none':
        style['u'] = 1
    if f.strike:
        style['s'] = 1
    if f.sz and round(float(f.sz), 1) != 11:
        style['sz'] = round(float(f.sz), 1)
    color = _argb_to_hex(f.color)
    if color:
        style['c'] = color
    if f.name:
        style['n'] = f.name
    return style


def _cell_fill(cell):
    """提取纯色填充，非纯色返回 None。"""
    fill = cell.fill
    if fill is None or fill.patternType != 'solid':
        return None
    return _argb_to_hex(fill.fgColor)


_BORDER_CSS = {
    'hair': 'solid', 'thin': 'solid', 'medium': 'solid', 'thick': 'solid',
    'double': 'double', 'dotted': 'dotted', 'dashed': 'dashed',
    'dashDot': 'dashed', 'dashDotDot': 'dashed', 'mediumDashed': 'dashed',
    'mediumDashDot': 'dashed', 'mediumDashDotDot': 'dashed', 'slantDashDot': 'dashed',
}


def _cell_borders(cell):
    """提取四边边框（只输出实际存在的边）。"""
    sides = {}
    for key, side in (('t', cell.border.top), ('r', cell.border.right),
                      ('b', cell.border.bottom), ('l', cell.border.left)):
        if side is not None and side.style:
            sides[key] = {
                's': _BORDER_CSS.get(side.style, 'solid'),
                'c': _argb_to_hex(side.color) or '#9CA3AF',
            }
    return sides


def _cell_align(cell):
    """提取对齐方式。"""
    a = cell.alignment
    style = {}
    if a.horizontal:
        style['h'] = a.horizontal
    if a.vertical:
        style['v'] = a.vertical
    if a.wrap_text:
        style['wrap'] = 1
    if a.indent:
        style['ind'] = int(a.indent)
    return style


def _cell_text(value, number_format):
    """按数字格式生成展示文本（日期 / 百分比 / 小数位）。"""
    from datetime import date as _date, time as _time

    if value is None:
        return ''
    if isinstance(value, datetime):
        if value.hour == 0 and value.minute == 0 and value.second == 0:
            return value.strftime('%Y-%m-%d')
        return value.strftime('%Y-%m-%d %H:%M')
    if isinstance(value, _date):
        return value.strftime('%Y-%m-%d')
    if isinstance(value, _time):
        return value.strftime('%H:%M')
    if isinstance(value, bool):
        return 'TRUE' if value else 'FALSE'

    fmt = number_format or ''
    if isinstance(value, (int, float)):
        if '%' in fmt:
            decimals = len(fmt.split('.', 1)[1].split('%')[0]) if '.' in fmt else 0
            return f'{value * 100:.{decimals}f}%'
        if '.' in fmt:
            decimals = sum(1 for ch in fmt.split('.', 1)[1] if ch in '0#')
            return f'{value:.{decimals}f}'
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
    return str(value)


def _col_letter(idx):
    """列号 → 列字母（1 → A）。"""
    letters = ''
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _col_width_px(ws, col_idx):
    """列宽（字符单位）→ 像素，限制在 24~320px。"""
    letter = _col_letter(col_idx)
    dim = ws.column_dimensions.get(letter)
    if dim is None or dim.width is None:
        for candidate in ws.column_dimensions.values():
            if candidate.min and candidate.max and candidate.min <= col_idx <= candidate.max and candidate.width:
                dim = candidate
                break
    if dim is not None and dim.width:
        return max(24, min(int(dim.width * 7 + 5), 320))
    return 64


def _build_xlsx_preview(file_path, empty_cells, max_rows=300, max_cols=60):
    """把生成好的 xlsx 解析成可渲染的网格数据（值 + 样式 + 合并 + 宽高）。"""
    wb = load_workbook(file_path, data_only=False)
    sheets = []
    truncated = False

    for ws in wb.worksheets:
        if ws.sheet_state != 'visible':
            continue

        total_rows = ws.max_row or 1
        total_cols = ws.max_column or 1
        if total_rows > max_rows or total_cols > max_cols:
            truncated = True
        max_row = min(total_rows, max_rows)
        max_col = min(total_cols, max_cols)

        cells = []
        for row in ws.iter_rows(min_row=1, max_row=max_row, max_col=max_col):
            for cell in row:
                text = _cell_text(cell.value, cell.number_format)
                font = _cell_font(cell)
                fill = _cell_fill(cell)
                borders = _cell_borders(cell)
                align = _cell_align(cell)
                is_empty_slot = (ws.title, cell.row, cell.column) in empty_cells
                if not text and not font and not fill and not borders and not align and not is_empty_slot:
                    continue

                item = {'r': cell.row, 'c': cell.column, 'v': text}
                if font:
                    item['f'] = font
                if fill:
                    item['bg'] = fill
                if borders:
                    item['bd'] = borders
                if align:
                    item['al'] = align
                if is_empty_slot:
                    item['empty'] = 1
                if isinstance(cell.value, str) and '${' in cell.value:
                    item['ph'] = 1
                cells.append(item)

        merges = [str(rng) for rng in ws.merged_cells.ranges
                  if rng.min_row <= max_row and rng.min_col <= max_col]

        col_widths = {str(c): _col_width_px(ws, c) for c in range(1, max_col + 1)}
        row_heights = {}
        for r in range(1, max_row + 1):
            dim = ws.row_dimensions.get(r)
            if dim is not None and dim.height:
                row_heights[str(r)] = max(16, min(int(dim.height * 96 / 72), 300))
            else:
                row_heights[str(r)] = 20

        sheets.append({
            'name': ws.title,
            'max_row': max_row,
            'max_col': max_col,
            'cells': cells,
            'merges': merges,
            'col_widths': col_widths,
            'row_heights': row_heights,
            'freeze': ws.freeze_panes,
        })

    wb.close()
    return sheets, truncated


@bp.route('/api/export/preview-render', methods=['POST'])
@auth.admin_required
def api_export_preview_render():
    """生成真实导出文件的预览。

    取当前筛选条件下的样本学生（默认前 3 条，可指定搜索学生），调用与正式导出
    完全相同的填充函数生成文件：xlsx 解析成网格数据返回，docx 返回内联地址由前端
    docx-preview 渲染版式。
    """
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error

    data = request.get_json() or {}
    template_id = data.get('template_id')
    mappings = data.get('mappings', [])
    search = (data.get('search') or '').strip()
    try:
        limit = int(data.get('limit', 3))
    except (TypeError, ValueError):
        limit = 3
    limit = max(1, min(limit, 5))

    if not template_id:
        return jsonify({'success': False, 'message': '缺少模板ID'}), 400
    if not mappings:
        return jsonify({'success': False, 'message': '请先配置字段映射'}), 400

    tpl = models.get_template(template_id)
    if not tpl:
        return jsonify({'success': False, 'message': '模板不存在'}), 404
    template_file = _resolve_template_path(tpl['file_path'])
    if not os.path.exists(template_file):
        return jsonify({'success': False, 'message': '模板文件已丢失，请重新上传'}), 404

    filter_data = dict(_get_export_filter_from_session())
    if search:
        filter_data['search'] = search
    students = _apply_filter_to_query(filter_data)
    if not students:
        message = f'未找到匹配「{search}」的学生' if search else '当前筛选条件下无数据可预览'
        return jsonify({'success': False, 'message': message}), 400

    preview_rows = _build_preview_rows(students, mappings, limit)
    sample = students[:limit]

    # Word 模板：生成真实 docx，返回内联地址供前端 docx-preview 渲染版式
    if tpl['file_type'] != 'xlsx':
        try:
            docx_path = _export_docx(template_file, sample, mappings)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return jsonify({'success': False, 'message': f'预览生成失败: {str(e)}'}), 500

        token = _register_preview_file(docx_path)
        return jsonify({
            'success': True,
            'grid_supported': False,
            'docx_supported': True,
            'token': token,
            'inline_url': f'/api/export/preview-file/{token}?inline=1',
            'download_url': f'/api/export/preview-file/{token}',
            'sample_count': len(sample),
            'total_count': len(students),
            'preview_rows': preview_rows,
        })

    empty_cells = set()
    try:
        output_path = _export_xlsx(template_file, sample, mappings, empty_cells=empty_cells)
        sheets, truncated = _build_xlsx_preview(output_path, empty_cells)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': f'预览生成失败: {str(e)}'}), 500

    token = _register_preview_file(output_path)
    return jsonify({
        'success': True,
        'grid_supported': True,
        'token': token,
        'download_url': f'/api/export/preview-file/{token}',
        'sheets': sheets,
        'truncated': truncated,
        'sample_count': len(sample),
        'total_count': len(students),
        'preview_rows': preview_rows,
    })


@bp.route('/api/export/preview-file/<token>')
@auth.admin_required
def api_export_preview_file(token):
    """预览件：?inline=1 时内联输出（供 docx-preview 拉取字节），否则作为附件下载。"""
    info = _PREVIEW_FILES.get(token)
    if not info:
        return '预览文件已过期，请重新刷新预览', 404
    if info['expires_at'] <= time.time() or not os.path.exists(info['path']):
        _PREVIEW_FILES.pop(token, None)
        return '预览文件已过期，请重新刷新预览', 404

    if request.args.get('inline') == '1':
        return send_file(info['path'])

    ext = os.path.splitext(info['path'])[1] or '.xlsx'
    download_name = f'预览_{datetime.now().strftime("%Y%m%d_%H%M%S")}{ext}'
    return send_file(info['path'], as_attachment=True, download_name=download_name)
