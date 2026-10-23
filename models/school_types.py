"""学校类别字典服务层。

学校类别存储于 school_types 专表，编码为整数且稳定不复用；
students.school / users.school_visible 的存储格式保持不变。
"""
import sqlite3
import logging

import config
from models.db import get_db
from models.logs import update_sync_timestamp

logger = logging.getLogger(__name__)


def parse_visible_school_codes(school_visible):
    """解析 users.school_visible（编码 CSV）为整数列表。

    返回 None 表示不限制（空值或 '*'）；返回 [] 表示显式无可用学校。
    兼容历史数据中的空格与非法片段。
    """
    if school_visible is None:
        return None
    text = str(school_visible).strip()
    if not text or text == '*':
        return None
    codes = []
    for part in text.split(','):
        part = part.strip()
        if part.isdigit():
            code = int(part)
            if code not in codes:
                codes.append(code)
    return codes


def get_school_types(active_only=False):
    """获取学校类别列表，按编码升序。"""
    conn = get_db()
    try:
        sql = 'SELECT code, name, is_active, created_at FROM school_types'
        if active_only:
            sql += ' WHERE is_active = 1'
        sql += ' ORDER BY code'
        rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error as e:
        logger.warning("读取学校类别失败，回退配置默认值: %s", e)
        return [{'code': c, 'name': n, 'is_active': 1, 'created_at': None}
                for c, n in sorted(config.SCHOOL_TYPES.items())]
    finally:
        conn.close()


def get_school_type_map(active_only=False):
    """获取 {编码: 名称} 映射。"""
    return {r['code']: r['name'] for r in get_school_types(active_only)}


def get_school_type_name(code):
    """查询单个学校类别名称，数据库异常时回退 config.SCHOOL_TYPES。"""
    try:
        code = int(code)
    except (TypeError, ValueError):
        return ''
    try:
        conn = get_db()
        try:
            row = conn.execute('SELECT name FROM school_types WHERE code = ?', (code,)).fetchone()
        finally:
            conn.close()
        if row:
            return row['name']
    except sqlite3.Error as e:
        logger.warning("查询学校类别名称失败，回退配置默认值 code=%s: %s", code, e)
    return config.SCHOOL_TYPES.get(code, '')


def get_school_type(code):
    """获取单个学校类别记录，不存在返回 None。"""
    try:
        code = int(code)
    except (TypeError, ValueError):
        return None
    conn = get_db()
    try:
        row = conn.execute(
            'SELECT code, name, is_active, created_at FROM school_types WHERE code = ?',
            (code,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_next_school_type_code():
    """建议的下一个编码（max(code) + 1，表为空时从 1 开始）。"""
    conn = get_db()
    try:
        row = conn.execute('SELECT MAX(code) FROM school_types').fetchone()
        current = row[0] if row and row[0] is not None else 0
        return int(current) + 1
    finally:
        conn.close()


def create_school_type(code, name):
    """新增学校类别，返回新建记录。编码非法/重名时抛出 ValueError。"""
    try:
        code = int(code)
    except (TypeError, ValueError):
        raise ValueError('编码必须为正整数')
    if code <= 0:
        raise ValueError('编码必须为正整数')
    name = (name or '').strip()
    if not name:
        raise ValueError('学校类别名称不能为空')

    conn = get_db()
    try:
        if conn.execute('SELECT 1 FROM school_types WHERE code = ?', (code,)).fetchone():
            raise ValueError(f'编码 {code} 已存在')
        if conn.execute('SELECT 1 FROM school_types WHERE name = ?', (name,)).fetchone():
            raise ValueError(f'名称「{name}」已存在')
        conn.execute(
            'INSERT INTO school_types (code, name, is_active) VALUES (?, ?, 1)',
            (code, name)
        )
        conn.commit()
    finally:
        conn.close()
    update_sync_timestamp()
    return get_school_type(code)


def update_school_type(code, name=None, is_active=None):
    """改名/停用/启用，记录不存在返回 False。"""
    existing = get_school_type(code)
    if not existing:
        return False

    fields = []
    params = []
    if name is not None:
        name = name.strip()
        if not name:
            raise ValueError('学校类别名称不能为空')
        conn = get_db()
        try:
            dup = conn.execute(
                'SELECT 1 FROM school_types WHERE name = ? AND code != ?',
                (name, existing['code'])
            ).fetchone()
        finally:
            conn.close()
        if dup:
            raise ValueError(f'名称「{name}」已存在')
        fields.append('name = ?')
        params.append(name)

    if is_active is not None:
        fields.append('is_active = ?')
        params.append(1 if is_active else 0)

    if not fields:
        return True

    conn = get_db()
    try:
        params.append(existing['code'])
        conn.execute(f"UPDATE school_types SET {', '.join(fields)} WHERE code = ?", params)
        conn.commit()
    finally:
        conn.close()
    update_sync_timestamp()
    return True


def append_code_to_admin_visibility(code):
    """将新增的学校类别编码追加到所有 admin 用户的可见范围。

    school_visible 为空或 '*' 表示不限制，无需追加；返回追加的用户数。
    """
    try:
        code = int(code)
    except (TypeError, ValueError):
        return 0
    conn = get_db()
    updated = 0
    try:
        rows = conn.execute(
            "SELECT id, school_visible FROM users WHERE role = 'admin'"
        ).fetchall()
        for row in rows:
            text = (row['school_visible'] or '').strip()
            if not text or text == '*':
                continue
            codes = [c.strip() for c in text.split(',') if c.strip()]
            if str(code) in codes:
                continue
            codes.append(str(code))
            conn.execute(
                'UPDATE users SET school_visible = ? WHERE id = ?',
                (','.join(codes), row['id'])
            )
            updated += 1
        if updated:
            conn.commit()
    finally:
        conn.close()
    return updated


def count_school_type_references(code):
    """统计引用该学校类别的学生数（含回收站）与用户数。"""
    try:
        code = int(code)
    except (TypeError, ValueError):
        return {'students': 0, 'users': 0}
    conn = get_db()
    try:
        students = conn.execute(
            'SELECT COUNT(*) FROM students WHERE school = ?', (code,)
        ).fetchone()[0]
        users = conn.execute(
            "SELECT COUNT(*) FROM users "
            "WHERE (',' || REPLACE(COALESCE(school_visible, ''), ' ', '') || ',') LIKE ?",
            (f'%,{code},%',)
        ).fetchone()[0]
        return {'students': int(students or 0), 'users': int(users or 0)}
    finally:
        conn.close()


def delete_school_type(code):
    """删除学校类别，仅未被学生（含回收站）或用户权限引用时允许。

    记录不存在返回 False；被引用时抛出 ValueError（提示改用停用）。
    """
    existing = get_school_type(code)
    if not existing:
        return False

    refs = count_school_type_references(existing['code'])
    if refs['students'] or refs['users']:
        raise ValueError(
            f'该学校类别已被 {refs["students"]} 名学生、{refs["users"]} 个用户引用，'
            f'不能删除，请改用停用'
        )

    conn = get_db()
    try:
        conn.execute('DELETE FROM school_types WHERE code = ?', (existing['code'],))
        conn.commit()
    finally:
        conn.close()
    update_sync_timestamp()
    return True


def seed_school_types():
    """仅当表为空时写入 config.SCHOOL_TYPES 默认值，返回写入条数。"""
    conn = get_db()
    try:
        count = conn.execute('SELECT COUNT(*) FROM school_types').fetchone()[0]
        if count:
            return 0
        inserted = 0
        for code, name in sorted(config.SCHOOL_TYPES.items()):
            conn.execute(
                'INSERT INTO school_types (code, name, is_active) VALUES (?, ?, 1)',
                (code, name)
            )
            inserted += 1
        conn.commit()
        return inserted
    except sqlite3.Error as e:
        logger.warning("学校类别种子化失败: %s", e)
        return 0
    finally:
        conn.close()
