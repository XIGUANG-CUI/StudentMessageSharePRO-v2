import sys
import os
import sqlite3

sys.path.insert(0, os.path.dirname(__file__))
import pinyin_utils

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'student.db')


def main():
    dry_run = '--dry-run' in sys.argv
    force = '--force' in sys.argv

    if not os.path.exists(DB_PATH):
        print(f'[ERROR] Database not found: {DB_PATH}')
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if force:
        rows = cursor.execute(
            "SELECT id, name FROM students WHERE is_deleted = 0"
        ).fetchall()
    else:
        rows = cursor.execute(
            "SELECT id, name FROM students WHERE is_deleted = 0 AND (name_pinyin IS NULL OR name_pinyin = '')"
        ).fetchall()

    total = len(rows)
    if total == 0:
        print('No records to update.')
        conn.close()
        return

    mode = 'DRY RUN' if dry_run else ('FORCE' if force else 'UPDATE')
    print(f'Mode: {mode}')
    print(f'Total: {total} records\n')

    updated = 0
    for i, row in enumerate(rows, 1):
        name = row['name'] or ''
        pinyin = pinyin_utils.name_to_pinyin(name)
        if i <= 10 or i % 100 == 0:
            print(f'  [{i}/{total}] {name} -> {pinyin}')

        if not dry_run:
            cursor.execute(
                "UPDATE students SET name_pinyin = ? WHERE id = ?",
                (pinyin, row['id'])
            )
        updated += 1

    if dry_run:
        print(f'\n[DRY RUN] Would update {updated} records. No changes made.')
    else:
        conn.commit()
        print(f'\n[DONE] Updated {updated} records.')

    conn.close()


if __name__ == '__main__':
    main()
