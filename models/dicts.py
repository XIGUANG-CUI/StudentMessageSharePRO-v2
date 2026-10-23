import sqlite3
import os
import logging
from config import PHOTOS_DIR
from models.db import get_db, _non_graduated_where
from models.logs import update_sync_timestamp

logger = logging.getLogger(__name__)


def get_all_classes_with_counts(grade_visible=None):
    """获取全部班级withcounts。 参数: grade_visible"""
    conn = get_db()
    cursor = conn.cursor()
    where = f"WHERE {_non_graduated_where()} AND attendance_status != '非在校' AND admin_class IS NOT NULL AND admin_class != ''"
    params = []
    if grade_visible and grade_visible != '*':
        grades = [g.strip() for g in grade_visible.split(',')]
        placeholders = ','.join(['?' for _ in grades])
        where += f' AND grade IN ({placeholders})'
        params = grades
    cursor.execute(f'''
        SELECT admin_class, COUNT(*) as total,
               GROUP_CONCAT(DISTINCT grade) as grades
        FROM students {where}
        GROUP BY admin_class ORDER BY admin_class
    ''', params)
    rows = cursor.fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        if d.get('grades'):
            sorted_grades = sorted(d['grades'].split(','), key=lambda x: int(x) if x.isdigit() else x)
            d['grades'] = ','.join(sorted_grades)
        result.append(d)
    return result

def get_students_by_class(admin_class):
    """获取学生by班级。 参数: admin_class"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(f'''SELECT student_no, name, name_pinyin, grade, attendance_status, id_card FROM students
                      WHERE admin_class = ? AND {_non_graduated_where()} AND attendance_status NOT IN ('非在校', '退学办结')
                      ORDER BY grade, student_no''',
                   (admin_class,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_all_grades():
    """获取全部年级。"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(f'SELECT DISTINCT grade FROM students WHERE {_non_graduated_where()} ORDER BY grade DESC')
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in rows]

def get_enrollment_years():
    """获取入学年份。"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(f'''SELECT DISTINCT enrollment_year FROM students
                      WHERE {_non_graduated_where()} AND enrollment_year > 0
                      ORDER BY enrollment_year DESC''')
    student_years = set(r[0] for r in cursor.fetchall())
    cursor.execute("SELECT dict_value FROM dicts WHERE dict_type = 'enrollment_year' AND dict_value IS NOT NULL AND dict_value != ''")
    dict_years = set(int(r[0]) for r in cursor.fetchall() if str(r[0]).isdigit())
    all_years = student_years | dict_years
    conn.close()
    return sorted(all_years, reverse=True) if all_years else []

def sync_enrollment_years_to_dicts():
    """同步入学年份to字典。"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(f'''SELECT DISTINCT enrollment_year FROM students
                      WHERE {_non_graduated_where()} AND enrollment_year > 0''')
    student_years = set(r[0] for r in cursor.fetchall())
    cursor.execute("SELECT dict_value FROM dicts WHERE dict_type = 'enrollment_year'")
    dict_years = set(int(r[0]) for r in cursor.fetchall() if str(r[0]).isdigit())
    new_years = student_years - dict_years
    for year in new_years:
        try:
            cursor.execute('INSERT INTO dicts (dict_type, dict_value) VALUES (?, ?)',
                         ('enrollment_year', str(year)))
        except Exception as e:
            logger.debug("同步入学年份到字典失败 year=%s: %s", year, e, exc_info=True)
    conn.commit()
    conn.close()
    return len(new_years)

def get_grades_by_enrollment_year(enrollment_year):
    """获取年级by入学年份。 参数: enrollment_year"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(f'''SELECT DISTINCT grade FROM students
                      WHERE enrollment_year = ? AND {_non_graduated_where()}
                        AND grade IS NOT NULL AND grade != ''
                      ORDER BY grade''',
                   (enrollment_year,))
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in rows]

def get_dicts(dict_type=None, search=None):
    """获取字典。 参数: dict_type, search"""
    conn = get_db()
    cursor = conn.cursor()
    if dict_type:
        cursor.execute('SELECT * FROM dicts WHERE dict_type = ? ORDER BY id', (dict_type,))
    elif search:
        keyword = f'%{search}%'
        cursor.execute(
            'SELECT * FROM dicts WHERE dict_type LIKE ? OR dict_value LIKE ? ORDER BY dict_type, id',
            (keyword, keyword)
        )
    else:
        cursor.execute('SELECT * FROM dicts ORDER BY dict_type, id')
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_grouped_dicts():
    """获取分组字典。"""
    rows = get_dicts()
    grouped = {}
    for r in rows:
        t = r['dict_type']
        if t not in grouped:
            grouped[t] = []
        grouped[t].append(r['dict_value'])
    return grouped

def add_dict(dict_type, dict_value):
    """add字典。 参数: dict_type, dict_value"""
    conn = get_db()
    cursor = conn.cursor()
    existing = get_dict_by_type_value(dict_type, dict_value)
    if existing:
        conn.close()
        return False
    cursor.execute('INSERT INTO dicts (dict_type, dict_value) VALUES (?, ?)', (dict_type, dict_value))
    conn.commit()
    update_sync_timestamp()
    conn.close()
    return True

def get_dict_by_type_value(dict_type, dict_value):
    """获取字典bytypevalue。 参数: dict_type, dict_value"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM dicts WHERE dict_type = ? AND dict_value = ?', (dict_type, dict_value))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def delete_dict(dict_id):
    """删除字典。 参数: dict_id"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM dicts WHERE id = ?', (dict_id,))
    conn.commit()
    update_sync_timestamp()
    conn.close()
    return cursor.rowcount > 0

def batch_import_dicts(dict_type, values):
    """批量导入字典。 参数: dict_type, values"""
    conn = get_db()
    cursor = conn.cursor()
    imported = 0
    skipped = 0
    for v in values:
        v = v.strip()
        if not v:
            continue
        existing = get_dict_by_type_value(dict_type, v)
        if not existing:
            cursor.execute('INSERT INTO dicts (dict_type, dict_value) VALUES (?, ?)', (dict_type, v))
            imported += 1
        else:
            skipped += 1
    conn.commit()
    update_sync_timestamp()
    conn.close()
    return imported, skipped

def has_photo(id_card, enrollment_year=None):
    """has照片。 参数: id_card, enrollment_year"""
    import os
    photos_dir = PHOTOS_DIR

    if enrollment_year:
        year_dir = os.path.join(photos_dir, str(enrollment_year))
        for ext in ['.jpg', '.jpeg', '.png', '.bmp']:
            photo_path = os.path.join(year_dir, f'{id_card}{ext}')
            if os.path.exists(photo_path):
                return True

    for ext in ['.jpg', '.jpeg', '.png', '.bmp']:
        photo_path = os.path.join(photos_dir, f'{id_card}{ext}')
        if os.path.exists(photo_path):
            return True

    return False


def get_students_with_photo_status(students, enrollment_year=None):
    """获取学生with照片状态。 参数: students, enrollment_year"""
    import os
    photos_dir = PHOTOS_DIR

    for student in students:
        id_card = student.get('id_card', '')
        # 全校视图时使用学生自身的入学年份查找照片，否则用统一年份
        year = enrollment_year or student.get('enrollment_year')
        student['has_photo'] = has_photo(id_card, year)
    return students

