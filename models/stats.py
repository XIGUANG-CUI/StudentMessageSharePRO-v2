import sqlite3
import re
import logging
from datetime import datetime
from models.db import get_db, _exclude_graduated_clause
# 注意：natural_key 不在顶层导入，因 utils.py 顶层 import models 会循环依赖。
# 各函数内已有懒加载：from utils import natural_key as _natural_key（P2-2）

logger = logging.getLogger(__name__)


def get_stats(filters=None):
    """获取统计。 参数: filters"""
    conn = get_db()
    cursor = conn.cursor()
    
    where_clauses = ['is_deleted = 0', _exclude_graduated_clause()]
    params = []
    
    if filters and filters.get('school'):
        school = filters['school']
        if isinstance(school, list):
            placeholders = ','.join(['?' for _ in school])
            where_clauses.append(f'school IN ({placeholders})')
            params.extend(school)
        else:
            where_clauses.append('school = ?')
            params.append(school)
    
    if filters and filters.get('enrollment_year'):
        where_clauses.append('enrollment_year = ?')
        params.append(filters['enrollment_year'])
    
    if filters and filters.get('registration_status'):
        where_clauses.append('registration_status = ?')
        params.append(filters['registration_status'])
    
    where_sql = ' AND '.join(where_clauses)
    
    cursor.execute(f'SELECT COUNT(*) FROM students WHERE {where_sql}', params)
    total = cursor.fetchone()[0]
    
    cursor.execute(f"SELECT COUNT(*) FROM students WHERE {where_sql} AND attendance_status IN ('在校', '托管')", params)
    attendance_in = cursor.fetchone()[0]
    attendance_out = total - attendance_in

    cursor.execute(f'SELECT COUNT(*) FROM students WHERE {where_sql} AND registration_status = ?', params + ['正式学籍'])
    registration_registered = cursor.fetchone()[0]
    
    cursor.execute(f'SELECT COUNT(*) FROM students WHERE {where_sql} AND registration_status = ?', params + ['未注册'])
    registration_unregistered = cursor.fetchone()[0]
    
    cursor.execute(f'SELECT grade, COUNT(*) as count FROM students WHERE {where_sql} GROUP BY grade ORDER BY grade', params)
    grade_distribution = [dict(row) for row in cursor.fetchall()]
    
    cursor.execute(f'SELECT major, COUNT(*) as count FROM students WHERE {where_sql} GROUP BY major ORDER BY count DESC', params)
    major_distribution = [dict(row) for row in cursor.fetchall()]
    
    cursor.execute(f'SELECT admin_class, COUNT(*) as count FROM students WHERE {where_sql} GROUP BY admin_class ORDER BY count DESC', params)
    class_distribution = [dict(row) for row in cursor.fetchall()]

    cursor.execute(f'''
        SELECT admin_class, COUNT(*) as total,
               SUM(CASE WHEN gender = '男' THEN 1 ELSE 0 END) as male,
               SUM(CASE WHEN gender = '女' THEN 1 ELSE 0 END) as female
        FROM students WHERE {where_sql} AND admin_class IS NOT NULL AND admin_class != ''
        GROUP BY admin_class
    ''', params)
    class_rows = [dict(row) for row in cursor.fetchall()]

    cursor.execute(f'''
        SELECT admin_class, major, COUNT(*) as count
        FROM students WHERE {where_sql} AND major IS NOT NULL AND major != ''
        GROUP BY admin_class, major
        ORDER BY admin_class, major
    ''', params)
    major_rows = [dict(row) for row in cursor.fetchall()]

    major_by_class = {}
    for row in major_rows:
        cls = row['admin_class']
        major_by_class.setdefault(cls, []).append({'major': row['major'], 'count': row['count']})

    class_detail = []
    detail_total_male = 0
    detail_total_female = 0
    detail_grand_total = 0
    for row in class_rows:
        cls = row['admin_class']
        class_detail.append({
            'admin_class': cls,
            'total': row['total'],
            'male': row['male'],
            'female': row['female'],
            'majors': major_by_class.get(cls, [])
        })
        detail_total_male += row['male']
        detail_total_female += row['female']
        detail_grand_total += row['total']

    import re
    from utils import natural_key as _natural_key  # P2-2: 统一自然排序键

    def _class_sort_key(item):
        name = item.get('admin_class') or ''
        m = re.match(r'^(\d{2})', name)
        year = int(m.group(1)) if m else 0
        rest = name[2:] if m else name
        return (year, _natural_key(rest))
    class_detail.sort(key=_class_sort_key)

    cursor.execute(f'''
        SELECT virtual_class, COUNT(*) as total,
               SUM(CASE WHEN gender = '男' THEN 1 ELSE 0 END) as male,
               SUM(CASE WHEN gender = '女' THEN 1 ELSE 0 END) as female
        FROM students WHERE {where_sql} AND virtual_class IS NOT NULL AND virtual_class != ''
        GROUP BY virtual_class
    ''', params)
    vclass_rows = [dict(row) for row in cursor.fetchall()]

    cursor.execute(f'''
        SELECT virtual_class, major, COUNT(*) as count
        FROM students WHERE {where_sql} AND major IS NOT NULL AND major != ''
              AND virtual_class IS NOT NULL AND virtual_class != ''
        GROUP BY virtual_class, major
        ORDER BY virtual_class, major
    ''', params)
    vclass_major_rows = [dict(row) for row in cursor.fetchall()]

    vclass_major_by_class = {}
    for row in vclass_major_rows:
        vc = row['virtual_class']
        vclass_major_by_class.setdefault(vc, []).append({'major': row['major'], 'count': row['count']})

    virtual_class_detail = []
    vclass_total_male = 0
    vclass_total_female = 0
    vclass_grand_total = 0
    for row in vclass_rows:
        vc = row['virtual_class']
        virtual_class_detail.append({
            'virtual_class': vc,
            'total': row['total'],
            'male': row['male'],
            'female': row['female'],
            'majors': vclass_major_by_class.get(vc, [])
        })
        vclass_total_male += row['male']
        vclass_total_female += row['female']
        vclass_grand_total += row['total']

    def _vclass_sort_key(item):
        """vclass排序key。 参数: item"""
        name = item['virtual_class']
        m = re.match(r'^(\d{2})', name)
        year = int(m.group(1)) if m else 0
        rest = name[2:] if m else name
        return (year, _natural_key(rest))
    virtual_class_detail.sort(key=_vclass_sort_key)

    cursor.execute(f'SELECT gender, COUNT(*) as count FROM students WHERE {where_sql} GROUP BY gender', params)
    gender_distribution = [dict(row) for row in cursor.fetchall()]
    
    cursor.execute(f'SELECT registration_status, COUNT(*) as count FROM students WHERE {where_sql} GROUP BY registration_status', params)
    registration_status_distribution = [dict(row) for row in cursor.fetchall()]

    cursor.execute(f'''SELECT CASE school WHEN 1 THEN '中职' WHEN 2 THEN '技工' ELSE '未知' END as school_name,
                              registration_status, COUNT(*) as count
                       FROM students WHERE {where_sql}
                       GROUP BY school, registration_status ORDER BY school, registration_status''', params)
    registration_by_school = [dict(row) for row in cursor.fetchall()]

    cursor.execute(f"""SELECT admin_class,
                              COUNT(*) as total,
                              SUM(CASE WHEN attendance_status IN ('在校', '托管') THEN 1 ELSE 0 END) as in_school
                       FROM students WHERE {where_sql} AND attendance_status != '非在校' AND admin_class IS NOT NULL AND admin_class != ''
                       GROUP BY admin_class""", params)
    attendance_by_class = [dict(row) for row in cursor.fetchall()]

    def _rate_sort_key(row):
        name = row.get('admin_class') or ''
        m = re.match(r'^(\d{2})', name)
        year = int(m.group(1)) if m else 0
        rest = name[2:] if m else name
        return (year, _natural_key(rest))
    attendance_by_class.sort(key=_rate_sort_key)

    cursor.execute(f'SELECT attendance_status, COUNT(*) as count FROM students WHERE {where_sql} GROUP BY attendance_status', params)
    attendance_status_distribution = [dict(row) for row in cursor.fetchall()]

    today = datetime.now().strftime('%Y-%m-%d')
    cursor.execute("SELECT MAX(attendance_date) FROM attendance_records")
    last_date_row = cursor.fetchone()
    attendance_date = last_date_row[0] if last_date_row and last_date_row[0] else today

    att_extra = ''
    att_extra_params = []
    if filters:
        school = filters.get('school')
        if school:
            if isinstance(school, list):
                placeholders = ','.join(['?' for _ in school])
                att_extra += f' AND s.school IN ({placeholders})'
                att_extra_params.extend(school)
            else:
                att_extra += ' AND s.school = ?'
                att_extra_params.append(school)
        if filters.get('enrollment_year'):
            att_extra += ' AND s.enrollment_year = ?'
            att_extra_params.append(filters['enrollment_year'])
        if filters.get('registration_status'):
            att_extra += ' AND s.registration_status = ?'
            att_extra_params.append(filters['registration_status'])
    cursor.execute(f'''
        SELECT ar.status, COUNT(DISTINCT ar.id_card) as count
        FROM attendance_records ar
        INNER JOIN students s ON ar.id_card = s.id_card AND s.is_deleted = 0 AND s.attendance_status NOT IN ('非在校', '退学办结') AND (s.registration_status IS NULL OR s.registration_status != '毕业'){att_extra}
        WHERE ar.attendance_date = ?
        GROUP BY ar.status
    ''', att_extra_params + [attendance_date])
    today_attendance_rows = {row['status']: row['count'] for row in [dict(r) for r in cursor.fetchall()]}
    today_attendance_present = today_attendance_rows.get('出勤', 0)
    today_attendance_absent = today_attendance_rows.get('缺勤', 0)
    today_attendance_total = today_attendance_present + today_attendance_absent

    cursor.execute(f'''
        SELECT s.admin_class,
               COUNT(DISTINCT s.id) as total_students,
               SUM(CASE WHEN ar.status = '出勤' THEN 1 ELSE 0 END) as present,
               SUM(CASE WHEN ar.status = '缺勤' THEN 1 ELSE 0 END) as absent
        FROM students s
        LEFT JOIN attendance_records ar ON s.id_card = ar.id_card AND ar.attendance_date = ?
        WHERE {where_sql} AND s.attendance_status NOT IN ('非在校', '退学办结') AND s.admin_class IS NOT NULL AND s.admin_class != ''
        GROUP BY s.admin_class
    ''', [attendance_date] + params)
    today_attendance_by_class = [dict(row) for row in cursor.fetchall()]
    today_attendance_by_class.sort(key=_rate_sort_key)

    conn.close()
    
    return {
        'total': total,
        'attendance_in': attendance_in,
        'attendance_out': attendance_out,
        'registration_registered': registration_registered,
        'registration_unregistered': registration_unregistered,
        'grade_distribution': grade_distribution,
        'major_distribution': major_distribution,
        'class_distribution': class_distribution,
        'class_detail': class_detail,
        'class_detail_total_male': detail_total_male,
        'class_detail_total_female': detail_total_female,
        'class_detail_grand_total': detail_grand_total,
        'virtual_class_detail': virtual_class_detail,
        'virtual_class_detail_total_male': vclass_total_male,
        'virtual_class_detail_total_female': vclass_total_female,
        'virtual_class_detail_grand_total': vclass_grand_total,
        'gender_distribution': gender_distribution,
        'registration_status_distribution': registration_status_distribution,
        'registration_by_school': registration_by_school,
        'attendance_by_class': attendance_by_class,
        'attendance_status_distribution': attendance_status_distribution,
        'today_attendance_present': today_attendance_present,
        'today_attendance_absent': today_attendance_absent,
        'today_attendance_total': today_attendance_total,
        'today_attendance_by_class': today_attendance_by_class,
        'last_attendance_date': attendance_date
    }

def get_filter_values(school_visible=None, grade_visible=None):
    """获取筛选values。 参数: school_visible, grade_visible"""
    conn = get_db()
    cursor = conn.cursor()
    
    where_clauses = ['is_deleted = 0', _exclude_graduated_clause()]
    params = []
    
    if school_visible:
        school_list = [int(s.strip()) for s in school_visible.split(',')]
        placeholders = ','.join(['?' for _ in school_list])
        where_clauses.append(f'school IN ({placeholders})')
        params.extend(school_list)
    
    if grade_visible and grade_visible != '*':
        grade_list = [g.strip() for g in grade_visible.split(',')]
        placeholders = ','.join(['?' for _ in grade_list])
        where_clauses.append(f'grade IN ({placeholders})')
        params.extend(grade_list)
    
    where_sql = ' AND '.join(where_clauses)
    
    cursor.execute(f'SELECT DISTINCT grade FROM students WHERE {where_sql} AND grade IS NOT NULL ORDER BY grade', params)
    grades = [row[0] for row in cursor.fetchall()]
    
    cursor.execute(f'SELECT DISTINCT major FROM students WHERE {where_sql} AND major IS NOT NULL ORDER BY major', params)
    majors = [row[0] for row in cursor.fetchall()]
    
    cursor.execute(f'SELECT DISTINCT admin_class FROM students WHERE {where_sql} AND admin_class IS NOT NULL ORDER BY admin_class', params)
    classes = [row[0] for row in cursor.fetchall()]
    
    cursor.execute(f'SELECT DISTINCT virtual_class FROM students WHERE {where_sql} AND virtual_class IS NOT NULL ORDER BY virtual_class', params)
    virtual_classes = [row[0] for row in cursor.fetchall()]

    cursor.execute(f'SELECT DISTINCT attendance_status FROM students WHERE {where_sql} AND attendance_status IS NOT NULL AND attendance_status != \'\' ORDER BY attendance_status', params)
    attendance_statuses = [row[0] for row in cursor.fetchall()]

    cursor.execute(f'SELECT DISTINCT registration_status FROM students WHERE {where_sql} AND registration_status IS NOT NULL AND registration_status != \'\' ORDER BY registration_status', params)
    registration_statuses = [row[0] for row in cursor.fetchall()]

    cursor.execute(f'SELECT DISTINCT ethnicity FROM students WHERE {where_sql} AND ethnicity IS NOT NULL AND ethnicity != \'\' ORDER BY ethnicity', params)
    ethnicities = [row[0] for row in cursor.fetchall()]

    cursor.execute(f'SELECT DISTINCT major_direction FROM students WHERE {where_sql} AND major_direction IS NOT NULL AND major_direction != \'\' ORDER BY major_direction', params)
    major_directions = [row[0] for row in cursor.fetchall()]

    cursor.execute(f'SELECT DISTINCT household_type FROM students WHERE {where_sql} AND household_type IS NOT NULL AND household_type != \'\' ORDER BY household_type', params)
    household_types = [row[0] for row in cursor.fetchall()]

    cursor.execute(f'SELECT DISTINCT admission_type FROM students WHERE {where_sql} AND admission_type IS NOT NULL AND admission_type != \'\' ORDER BY admission_type', params)
    admission_types = [row[0] for row in cursor.fetchall()]

    cursor.execute(f'SELECT DISTINCT change_info FROM students WHERE {where_sql} AND change_info IS NOT NULL AND change_info != \'\' ORDER BY change_info', params)
    change_infos = [row[0] for row in cursor.fetchall()]

    cursor.execute(f'SELECT DISTINCT enrollment_year FROM students WHERE {where_sql} AND enrollment_year IS NOT NULL AND enrollment_year > 0 ORDER BY enrollment_year DESC', params)
    enrollment_years = [row[0] for row in cursor.fetchall()]

    conn.close()

    return {
        'grades': grades,
        'majors': majors,
        'classes': classes,
        'virtual_classes': virtual_classes,
        'attendance_statuses': attendance_statuses,
        'registration_statuses': registration_statuses,
        'ethnicities': ethnicities,
        'major_directions': major_directions,
        'household_types': household_types,
        'admission_types': admission_types,
        'change_infos': change_infos,
        'enrollment_years': enrollment_years
    }

def get_classes_by_grade(grade, school_visible=None):
    """获取班级by年级。 参数: grade, school_visible"""
    conn = get_db()
    cursor = conn.cursor()
    
    where_clauses = ['is_deleted = 0', _exclude_graduated_clause(), 'grade = ?']
    params = [grade]
    
    if school_visible:
        school_list = [int(s.strip()) for s in school_visible.split(',')]
        placeholders = ','.join(['?' for _ in school_list])
        where_clauses.append(f'school IN ({placeholders})')
        params.extend(school_list)
    
    where_sql = ' AND '.join(where_clauses)
    
    cursor.execute(f'SELECT DISTINCT admin_class FROM students WHERE {where_sql} AND admin_class IS NOT NULL ORDER BY admin_class', params)
    classes = [row[0] for row in cursor.fetchall()]
    
    conn.close()
    return classes

def get_class_comparison(school=None, registration_status=None, grade=None):
    """获取班级对比。 参数: school, registration_status, grade"""
    conn = get_db()
    cursor = conn.cursor()

    where_clauses = ['is_deleted = 0', _exclude_graduated_clause()]
    params = []

    if school:
        if isinstance(school, list):
            placeholders = ','.join(['?' for _ in school])
            where_clauses.append(f'school IN ({placeholders})')
            params.extend(school)
        else:
            where_clauses.append('school = ?')
            params.append(int(school))

    if grade:
        if isinstance(grade, list):
            placeholders = ','.join(['?' for _ in grade])
            where_clauses.append(f'grade IN ({placeholders})')
            params.extend(grade)
        else:
            where_clauses.append('grade = ?')
            params.append(grade)

    if registration_status:
        where_clauses.append('registration_status = ?')
        params.append(registration_status)

    where_sql = ' AND '.join(where_clauses)

    cursor.execute(f'''
        SELECT admin_class, virtual_class, major, COUNT(*) as count
        FROM students
        WHERE {where_sql}
        GROUP BY admin_class, virtual_class, major
    ''', params)
    rows = [dict(row) for row in cursor.fetchall()]

    conn.close()

    import re
    from utils import natural_key as _natural_key  # P2-2: 统一自然排序键

    def _class_sort_key(row):
        admin = row.get('admin_class') or ''
        virtual = row.get('virtual_class') or ''
        major = row.get('major') or ''
        m = re.match(r'^(\d{2})', admin)
        year = int(m.group(1)) if m else 0
        rest = admin[2:] if m else admin
        return (year, _natural_key(rest), _natural_key(virtual), _natural_key(major))

    rows.sort(key=_class_sort_key)
    return rows

def get_class_comparison_detail(school=None, registration_status=None, grade=None):
    """获取班级对比详情。 参数: school, registration_status, grade"""
    conn = get_db()
    cursor = conn.cursor()

    where_clauses = ['is_deleted = 0', _exclude_graduated_clause()]
    params = []

    if school:
        if isinstance(school, list):
            placeholders = ','.join(['?' for _ in school])
            where_clauses.append(f'school IN ({placeholders})')
            params.extend(school)
        else:
            where_clauses.append('school = ?')
            params.append(int(school))

    if grade:
        if isinstance(grade, list):
            placeholders = ','.join(['?' for _ in grade])
            where_clauses.append(f'grade IN ({placeholders})')
            params.extend(grade)
        else:
            where_clauses.append('grade = ?')
            params.append(grade)

    if registration_status:
        where_clauses.append('registration_status = ?')
        params.append(registration_status)

    where_sql = ' AND '.join(where_clauses)

    cursor.execute(f'''
        SELECT admin_class, virtual_class, major,
               GROUP_CONCAT(name, ',') as student_names,
               COUNT(*) as count
        FROM students
        WHERE {where_sql}
        GROUP BY admin_class, virtual_class, major
        ORDER BY admin_class, virtual_class, major
    ''', params)
    rows = [dict(row) for row in cursor.fetchall()]

    conn.close()

    import re
    from utils import natural_key as _natural_key  # P2-2: 统一自然排序键

    def _detail_sort_key(row):
        admin = row.get('admin_class') or ''
        virtual = row.get('virtual_class') or ''
        major = row.get('major') or ''
        m = re.match(r'^(\d{2})', admin)
        year = int(m.group(1)) if m else 0
        rest = admin[2:] if m else admin
        return (year, _natural_key(rest), _natural_key(virtual), _natural_key(major))

    rows.sort(key=_detail_sort_key)
    return rows

