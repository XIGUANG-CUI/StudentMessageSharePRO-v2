"""
birth_date 存量数据回填脚本
遍历所有 birth_date 为空但身份证号有效的学生，从身份证号提取出生日期并 UPDATE。
幂等安全，可反复运行。

用法：
    python backfill_birth_date.py              # 预览模式，不写入（默认）
    python backfill_birth_date.py --apply      # 实际写入数据库
"""

import os
import sys
import sqlite3
from datetime import datetime

# 项目根目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'data', 'student.db')

# PyInstaller 打包环境：user_data 下的数据库
if getattr(sys, 'frozen', False):
    user_data_dir = os.environ.get('USER_DATA_DIR') or os.path.join(
        os.path.dirname(sys.executable), 'user_data'
    )
    DB_PATH = os.path.join(user_data_dir, 'data', 'student.db')
elif not os.path.exists(DB_PATH):
    # 非打包环境但脚本放在生产目录中运行时，数据库在 user_data/data/ 下
    alt_path = os.path.join(BASE_DIR, 'user_data', 'data', 'student.db')
    if os.path.exists(alt_path):
        DB_PATH = alt_path


def extract_birth_date(id_card):
    """从 18 位身份证号提取出生日期 YYYY-MM-DD"""
    try:
        if not id_card or len(id_card) != 18:
            return None
        birth_str = id_card[6:14]
        # 简洁校验：年/月/日范围
        y, m, d = int(birth_str[:4]), int(birth_str[4:6]), int(birth_str[6:8])
        if not (1900 <= y <= datetime.now().year):
            return None
        if not (1 <= m <= 12):
            return None
        if not (1 <= d <= 31):
            return None
        return f'{y:04d}-{m:02d}-{d:02d}'
    except (ValueError, IndexError):
        return None


def main():
    dry_run = '--apply' not in sys.argv
    mode = '预览模式 (不会写入)' if dry_run else '执行模式 (写入数据库)'

    print('=' * 60)
    print(f'birth_date 存量回填脚本 — {mode}')
    print(f'数据库路径: {DB_PATH}')
    print('=' * 60)

    if not os.path.exists(DB_PATH):
        print(f'\n[错误] 数据库文件不存在: {DB_PATH}')
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 查询 birth_date 为空（NULL 或空串）但有身份证的学生
    rows = cursor.execute(
        """SELECT id, name, id_card, birth_date
           FROM students
           WHERE (birth_date IS NULL OR birth_date = '')
             AND id_card IS NOT NULL
             AND id_card != ''
             AND LENGTH(id_card) = 18
             AND is_deleted = 0"""
    ).fetchall()

    print(f'\n找到 {len(rows)} 条需要回填的记录')

    if not rows:
        print('无需处理')
        conn.close()
        return

    updated = 0
    skipped = 0
    errors = []

    for row in rows:
        id_card = row['id_card']
        birth_date = extract_birth_date(id_card)

        if not birth_date:
            print(f'  [跳过] ID={row["id"]} 姓名={row["name"]} 身份证={id_card} → 无法提取有效出生日期')
            skipped += 1
            continue

        old_bd = row['birth_date'] or '(空)'
        print(f'  {"[预览]" if dry_run else "[更新]"} ID={row["id"]} 姓名={row["name"]} '
              f'身份证={id_card} → {old_bd} → {birth_date}')

        if not dry_run:
            try:
                cursor.execute(
                    'UPDATE students SET birth_date = ? WHERE id = ?',
                    (birth_date, row['id'])
                )
                updated += 1
            except Exception as e:
                errors.append(f'ID={row["id"]}: {e}')
                print(f'    [失败] {e}')
        else:
            updated += 1  # 预览计数

    if not dry_run:
        conn.commit()

    conn.close()

    print('\n' + '=' * 60)
    print(f'完成: {updated} 条更新, {skipped} 条跳过, {len(errors)} 条失败')
    if dry_run:
        print('这是预览模式，未实际写入。使用 --apply 参数执行写入。')
    print('=' * 60)


if __name__ == '__main__':
    main()
