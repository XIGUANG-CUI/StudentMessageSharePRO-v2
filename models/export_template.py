import sqlite3
import logging
from models.db import get_db

logger = logging.getLogger(__name__)


def create_template(name, file_path, file_type, placeholders):
    """创建模板。 参数: name, file_path, file_type, placeholders"""
    import json
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO export_templates (name, file_path, file_type, placeholders) VALUES (?, ?, ?, ?)',
        (name, file_path, file_type, json.dumps(placeholders, ensure_ascii=False))
    )
    conn.commit()
    tid = cursor.lastrowid
    conn.close()
    return tid

def get_template(template_id):
    """获取模板。 参数: template_id"""
    import json
    conn = get_db()
    row = conn.execute('SELECT * FROM export_templates WHERE id = ?', (template_id,)).fetchone()
    conn.close()
    if row:
        r = dict(row)
        r['placeholders'] = json.loads(r['placeholders'])
        return r
    return None

def get_all_templates():
    """获取全部模板。"""
    import json
    conn = get_db()
    rows = conn.execute('SELECT * FROM export_templates ORDER BY created_at DESC').fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d['placeholders'] = json.loads(d['placeholders'])
        result.append(d)
    return result

def delete_template(template_id):
    """删除模板。 参数: template_id"""
    conn = get_db()
    conn.execute('DELETE FROM export_templates WHERE id = ?', (template_id,))
    conn.execute('DELETE FROM export_field_mappings WHERE template_id = ?', (template_id,))
    conn.execute('DELETE FROM export_history WHERE template_id = ?', (template_id,))
    conn.commit()
    conn.close()

def save_field_mapping(template_id, scheme_name, mappings):
    """保存字段映射方案。mappings 格式: [{'placeholder': '${name}', 'source_table': 'students', 'source_field': 'name', 'default_value': ''}]"""
    import json
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO export_field_mappings (template_id, scheme_name, mappings) VALUES (?, ?, ?)',
        (template_id, scheme_name, json.dumps(mappings, ensure_ascii=False))
    )
    conn.commit()
    mid = cursor.lastrowid
    conn.close()
    return mid

def get_field_mappings_by_template(template_id):
    """获取字段mappingsby模板。 参数: template_id"""
    import json
    conn = get_db()
    rows = conn.execute(
        'SELECT * FROM export_field_mappings WHERE template_id = ? ORDER BY created_at DESC',
        (template_id,)
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d['mappings'] = json.loads(d['mappings'])
        result.append(d)
    return result

def get_field_mapping(mapping_id):
    """获取字段映射。 参数: mapping_id"""
    import json
    conn = get_db()
    row = conn.execute('SELECT * FROM export_field_mappings WHERE id = ?', (mapping_id,)).fetchone()
    conn.close()
    if row:
        d = dict(row)
        d['mappings'] = json.loads(d['mappings'])
        return d
    return None

def delete_field_mapping(mapping_id):
    """删除字段映射。 参数: mapping_id"""
    conn = get_db()
    conn.execute('DELETE FROM export_field_mappings WHERE id = ?', (mapping_id,))
    conn.commit()
    conn.close()

def add_export_history(template_id, template_name, scheme_name, row_count, file_path, created_by):
    """add导出history。 参数: template_id, template_name, scheme_name, row_count, file_path, ..."""
    conn = get_db()
    conn.execute(
        'INSERT INTO export_history (template_id, template_name, scheme_name, row_count, file_path, status, created_by) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (template_id, template_name, scheme_name, row_count, file_path, 'completed', created_by)
    )
    conn.commit()
    conn.close()

def get_export_history(limit=50):
    """获取导出history。 参数: limit"""
    conn = get_db()
    rows = conn.execute('SELECT * FROM export_history ORDER BY created_at DESC LIMIT ?', (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def clear_export_history():
    """清空所有导出历史记录"""
    conn = get_db()
    conn.execute('DELETE FROM export_history')
    conn.commit()
    conn.close()
