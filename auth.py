from functools import wraps
from flask import session, redirect, url_for, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
import models

def login_required(f):
    """权限装饰器：校验用户登录状态，未登录则重定向到登录页。"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('请先登录', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    """权限装饰器：校验管理员角色，非管理员重定向到普通用户页。"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('请先登录', 'warning')
            return redirect(url_for('login'))
        if session.get('role') != 'admin':
            flash('需要管理员权限', 'danger')
            return redirect(url_for('user.user_query'))
        return f(*args, **kwargs)
    return decorated_function

def attendance_required(f):
    """权限装饰器：校验出勤管理权限（admin 或 attendance 角色）。"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('请先登录', 'warning')
            return redirect(url_for('login'))
        if session.get('role') not in ('admin', 'attendance'):
            flash('需要出勤管理权限', 'danger')
            return redirect(url_for('user.user_query'))
        return f(*args, **kwargs)
    return decorated_function

def finance_required(f):
    """权限装饰器：校验财务权限（admin/finance_chief/finance_staff 角色）。"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('请先登录', 'warning')
            return redirect(url_for('login'))
        if session.get('role') not in ('admin', 'finance_chief', 'finance_staff'):
            flash('需要财务权限', 'danger')
            return redirect(url_for('user.user_query'))
        return f(*args, **kwargs)
    return decorated_function

def finance_chief_required(f):
    """权限装饰器：校验财务主管权限（admin/finance_chief 角色）。"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('请先登录', 'warning')
            return redirect(url_for('login'))
        if session.get('role') not in ('admin', 'finance_chief'):
            flash('需要财务主管权限', 'danger')
            return redirect(url_for('user.user_query'))
        return f(*args, **kwargs)
    return decorated_function

def insurance_officer_required(f):
    """权限装饰器：校验保险员权限（admin/insurance_officer 角色）。"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('请先登录', 'warning')
            return redirect(url_for('login'))
        if session.get('role') not in ('admin', 'insurance_officer'):
            flash('需要保险员权限', 'danger')
            return redirect(url_for('user.user_query'))
        return f(*args, **kwargs)
    return decorated_function

def _require_admin_json():
    """校验管理员登录态，返回 JSON 错误响应或 None（通过）。
    从 app.py 公共化至此，供 Blueprint 与 app 共用。"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': '请先登录'}), 401
    if session.get('role') != 'admin':
        return jsonify({'success': False, 'message': '需要管理员权限'}), 403
    return None

def _require_login_json():
    """校验登录态，返回 JSON 错误响应或 None（通过）。"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': '请先登录'}), 401
    return None

def _get_current_enrollment_year():
    """从 session 获取当前选中的入学年份（None 表示全部年份）。"""
    year = session.get('enrollment_year')
    if year is not None:
        return int(year)
    return None

def authenticate_user(username, password):
    """验证用户名密码，成功返回用户记录 dict，失败返回 None。"""
    user = models.get_user_by_username(username)
    if user and user['is_active'] and check_password_hash(user['password_hash'], password):
        return user
    return None

def hash_password(password):
    """对密码进行 Werkzeug 安全哈希处理。"""
    return generate_password_hash(password)

def verify_password(password_hash, password):
    """校验明文密码与哈希值是否匹配。"""
    return check_password_hash(password_hash, password)

def create_session(user):
    """用户登录成功后创建 session（写入 user_id/role/username/权限配置等）。"""
    session.permanent = True
    session['user_id'] = user['id']
    session['username'] = user['username']
    session['role'] = user['role']
    session['display_name'] = user.get('display_name', user['username'])
    session['school_visible'] = user.get('school_visible', '1,2')
    session['fields_visible'] = user.get('fields_visible', '[]')
    session['grade_visible'] = user.get('grade_visible', '*')
    if user['role'] == 'admin':
        from datetime import datetime
        session['enrollment_year'] = datetime.now().year

def clear_session():
    """清除 session（用户登出）。"""
    session.clear()

def get_current_user():
    """从 session 获取当前登录用户的完整记录。"""
    if 'user_id' in session:
        return models.get_user_by_id(session['user_id'])
    return None

def check_field_permission(field_name):
    """检查当前用户是否有权访问指定字段（基于 fields_visible 配置）。"""
    if session.get('role') == 'admin':
        return True

    fields_visible = session.get('fields_visible', '[]')
    if fields_visible == '["*"]' or fields_visible == '*':
        return True

    import json
    try:
        visible_fields = json.loads(fields_visible)
        return field_name in visible_fields
    except (ValueError, TypeError):
        return False

def _is_insurance_role():
    """判断当前用户角色是否为保险相关角色（finance_chief/finance_staff/insurance_officer）。"""
    return session.get('role') in ('finance_chief', 'finance_staff', 'insurance_officer')

def check_school_permission(school_type):
    """检查当前用户是否有权访问指定学校类别的数据。"""
    if session.get('role') == 'admin':
        return True
    if _is_insurance_role():
        return True

    school_visible = session.get('school_visible', '1,2')
    visible_schools = [s.strip() for s in school_visible.split(',')]
    return str(school_type) in visible_schools

def check_grade_permission(grade):
    """检查当前用户是否有权访问指定年级的数据。"""
    if session.get('role') == 'admin':
        return True
    if _is_insurance_role():
        return True

    grade_visible = session.get('grade_visible', '*')
    if grade_visible == '*':
        return True

    visible_grades = [g.strip() for g in grade_visible.split(',')]
    return grade in visible_grades

def apply_permission_filters(filters):
    """根据用户权限自动追加数据过滤条件（学校/年级可见性限制）。"""
    if session.get('role') == 'admin':
        return filters

    if filters is None:
        filters = {}

    school_visible = session.get('school_visible', '1,2')
    if 'school' not in filters:
        if school_visible and school_visible != '*':
            school_list = [int(s.strip()) for s in school_visible.split(',') if s.strip()]
            if len(school_list) == 1:
                filters['school'] = school_list[0]
            elif len(school_list) > 1:
                filters['school'] = school_list

    grade_visible = session.get('grade_visible', '*')
    if grade_visible and grade_visible != '*' and 'grade' not in filters:
        grade_list = [g.strip() for g in grade_visible.split(',') if g.strip()]
        if len(grade_list) == 1:
            filters['grade'] = grade_list[0]
        elif len(grade_list) > 1:
            filters['grade'] = grade_list

    return filters

def get_visible_fields():
    """获取当前用户可见的字段列表（admin 返回 ['*'] 表示全部可见）。"""
    if session.get('role') == 'admin':
        return ['*']

    fields_visible = session.get('fields_visible', '[]')
    if fields_visible == '["*"]' or fields_visible == '*':
        return ['*']

    import json
    try:
        fields = json.loads(fields_visible)
        if not fields:
            return ['学号', '学生姓名', '性别', '年龄', '专业', '行政班级', '年级', '学籍状态', '在校状态']
        return fields
    except (ValueError, TypeError):
        return ['学号', '学生姓名', '性别', '年龄', '专业', '行政班级', '年级', '学籍状态', '在校状态']

def filter_student_data(student_data):
    """根据字段级权限过滤学生数据（移除不可见字段，保留 id/age 等基础字段）。"""
    if session.get('role') == 'admin':
        return student_data

    visible_fields = get_visible_fields()
    if visible_fields == ['*']:
        return student_data

    from config import REVERSE_FIELD_MAPPING
    filtered_data = {}

    for key, value in student_data.items():
        if key in ['id', 'age']:
            filtered_data[key] = value
        elif key in REVERSE_FIELD_MAPPING:
            field_name = REVERSE_FIELD_MAPPING[key]
            if field_name in visible_fields:
                filtered_data[key] = value

    return filtered_data

def mask_sensitive_data(value, field_name):
    """对敏感数据（身份证号/电话号码）进行脱敏处理，admin 角色不脱敏。"""
    if session.get('role') == 'admin':
        return value

    if field_name in ['id_card', 'parent_id_card']:
        if value and len(value) >= 6:
            return value[:3] + '********' + value[-4:]

    if field_name in ['parent_phone', 'student_phone']:
        if value and len(value) >= 7:
            return value[:3] + '****' + value[-4:]

    return value
