from flask import Blueprint, request, session, render_template
import utils
import auth
import models
import config

bp = Blueprint('user', __name__)


@bp.route('/user/query')
@auth.login_required
def user_query():
    """查询。"""
    page = int(request.args.get('page', 1))
    
    filters = utils.parse_filters_from_request(request)
    filters = auth.apply_permission_filters(filters)
    
    result = models.get_students(filters, page=page, per_page=20)
    
    school_value = filters.get('school')
    if school_value:
        school_param = ','.join(str(s) for s in school_value) if isinstance(school_value, list) else str(school_value)
    else:
        school_param = session.get('school_visible') or ''
    grade_visible = session.get('grade_visible') or '*'
    filter_values = models.get_filter_values(school_param, grade_visible)

    # 学校筛选下拉：admin 显示全部类别，普通用户仅显示已授权类别
    if session.get('role') == 'admin':
        visible_codes = [st['code'] for st in models.get_school_types()]
    else:
        visible_codes = models.parse_visible_school_codes(session.get('school_visible'))
        if visible_codes is None:
            visible_codes = [st['code'] for st in models.get_school_types()]
    school_names = models.get_school_type_map()
    allowed_schools = [{'code': str(c), 'name': school_names.get(c, str(c))} for c in visible_codes]

    current_school = filters.get('school')
    if isinstance(current_school, list):
        current_school = str(current_school[0]) if len(current_school) == 1 else ''
    current_school = str(current_school) if current_school else ''
    
    visible_fields = auth.get_visible_fields()

    SENSITIVE_FIELDS = {'学生身份证号', '国网学籍号', '学生电话', '家长电话', '家长身份证号',
                        '住址', '家长姓名', '签发机关', '有效期开始', '有效期结束'}
    BADGE_FIELDS = {'学籍状态': {'正式学籍': 'success', '未注册': 'warning'},
                    '在校状态': {'在校': 'success', '非在校': 'danger', '托管': 'info'}}
    FIELD_LABELS = {f: f for f in config.ALL_STUDENT_FIELDS + ['入学年份']}
    FIELD_LABELS.update({'学号': '学号', '学生姓名': '姓名', '性别': '性别', '年龄': '年龄',
                         '民族': '民族', '专业': '专业', '行政班级': '行政班级', '虚拟班级': '虚拟班级',
                         '年级': '年级', '学籍状态': '学籍状态', '在校状态': '在校状态'})

    if visible_fields == ['*']:
        display_fields = list(config.ALL_STUDENT_FIELDS)
    else:
        display_fields = visible_fields

    visible_columns = []
    for field in display_fields:
        col = {
            'field': field,
            'db_key': config.FIELD_MAPPING.get(field, ''),
            'label': FIELD_LABELS.get(field, field),
            'is_sensitive': field in SENSITIVE_FIELDS,
            'badge': BADGE_FIELDS.get(field, {})
        }
        visible_columns.append(col)

    students_filtered = []
    for student in result['students']:
        bd = student.get('birth_date')
        if not bd and student.get('id_card'):
            bd = utils.extract_birth_date_from_id_card(student['id_card'])
        student['age'] = utils.calculate_age(bd) if bd else '-'
        filtered = auth.filter_student_data(student)
        for key in filtered:
            if key in ('id_card', 'parent_id_card', 'parent_phone', 'student_phone'):
                filtered[key] = auth.mask_sensitive_data(filtered[key], key)
        students_filtered.append(filtered)

    current_mode = request.args.get('mode', 'actual')

    return render_template('user/query.html', 
                          students=students_filtered, 
                          pagination=result,
                          filter_values=filter_values,
                          visible_fields=visible_fields,
                          visible_columns=visible_columns,
                          current_filters=filters or {},
                          current_mode=current_mode,
                          allowed_schools=allowed_schools,
                          current_school=current_school)

@bp.route('/user/stats')
@auth.login_required
def user_stats():
    """统计。"""
    school_filter = request.args.get('school', '')
    registration_filter = request.args.get('registration_status', '')
    if session.get('role') == 'admin':
        visible_codes = [st['code'] for st in models.get_school_types()]
    else:
        visible_codes = models.parse_visible_school_codes(session.get('school_visible'))
        if visible_codes is None:
            visible_codes = [st['code'] for st in models.get_school_types()]
    school_names = models.get_school_type_map()
    allowed_codes = [str(c) for c in visible_codes]
    allowed_schools = [{'code': str(c), 'name': school_names.get(c, str(c))} for c in visible_codes]
    
    if school_filter and school_filter in allowed_codes:
        filters = {'school': int(school_filter)}
    else:
        filters = auth.apply_permission_filters({})
        school_filter = ''
    
    if registration_filter in ('正式学籍', '未注册'):
        filters['registration_status'] = registration_filter
    else:
        registration_filter = ''
    
    stats = models.get_stats(filters)
    
    return render_template('user/stats.html', stats=stats, current_school=school_filter, allowed_schools=allowed_schools, current_registration=registration_filter)
