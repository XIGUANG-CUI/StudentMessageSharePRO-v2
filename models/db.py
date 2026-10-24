import sqlite3
import os
import logging
from datetime import datetime, timedelta
from flask import g, has_app_context
from config import DATABASE_PATH, PHOTOS_DIR

logger = logging.getLogger(__name__)


import sqlite3
import os
import logging
from datetime import datetime, timedelta
from flask import g, has_app_context
from config import DATABASE_PATH, PHOTOS_DIR

logger = logging.getLogger(__name__)


# ============================================================
# 区划字典（district.db）DDL —— 由 models.district.import_dictionary 执行
# 该库独立于 student.db，不参与备份；运行期只读，仅导入时一次性写入
# ============================================================
DISTRICT_DDL = """
CREATE TABLE IF NOT EXISTS dm_district_code (
    district_code TEXT NOT NULL,
    district_name TEXT NOT NULL,
    admin_level   INTEGER NOT NULL,
    parent_code   TEXT,
    province_code TEXT,
    city_code     TEXT,
    PRIMARY KEY (district_code)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_dm_district_name
    ON dm_district_code (district_name, admin_level);
CREATE INDEX IF NOT EXISTS idx_dm_district_parent
    ON dm_district_code (parent_code);
CREATE INDEX IF NOT EXISTS idx_dm_district_level
    ON dm_district_code (admin_level);
"""


# ============================================================
# 区划匹配业务表（student.db）—— 批次 / 结果 / 历史映射
# 幂等：全部 CREATE TABLE IF NOT EXISTS
# ============================================================
DISTRICT_MATCH_DDL = """
CREATE TABLE IF NOT EXISTS dm_match_batch (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_no TEXT NOT NULL UNIQUE,
    source TEXT DEFAULT 'batch',
    total INTEGER DEFAULT 0,
    status TEXT DEFAULT 'running',
    stats_json TEXT DEFAULT '',
    source_headers TEXT DEFAULT '',
    created_by TEXT DEFAULT '',
    is_deleted INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dm_match_result (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER,
    student_id INTEGER,
    student_name TEXT,
    id_card TEXT,
    address TEXT,
    province_code TEXT,
    province_name TEXT,
    city_code TEXT,
    city_name TEXT,
    district_code TEXT,
    district_name TEXT,
    match_status TEXT,
    engine1_detail TEXT,
    engine2_detail TEXT,
    decision_path TEXT,
    raw_row TEXT DEFAULT '',
    source TEXT DEFAULT 'batch',
    is_deleted INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_dm_result_batch
    ON dm_match_result (batch_id);
CREATE INDEX IF NOT EXISTS idx_dm_result_student
    ON dm_match_result (student_id);
CREATE INDEX IF NOT EXISTS idx_dm_result_id_card
    ON dm_match_result (id_card);

CREATE TABLE IF NOT EXISTS dm_history_mapping (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    old_code TEXT,
    new_code TEXT,
    old_name TEXT,
    new_name TEXT,
    change_type TEXT,
    change_date TEXT,
    remark TEXT,
    is_deleted INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# 历史区划映射内置种子（16 条）：仅当表为空时写入一次
HISTORY_MAPPING_SEED = [
    ("230105", "230104", "太平区", "道外区", "撤销合并", "2004", "太平区2004年并入道外区"),
    ("231121", "231183", "嫩江县", "嫩江市", "撤县设市", "2019", "嫩江县2019年撤县设市"),
    ("231024", "231086", "东宁县", "东宁市", "撤县设市", "2015", "东宁县2015年撤县设市"),
    ("232723", "232701", "漠河县", "漠河市", "撤县设市", "2018", "漠河县2018年撤县设市"),
    ("230182", "230113", "双城市", "双城区", "撤市设区", "2014", "双城市2014年撤市设区"),
    ("210282", "210214", "普兰店市", "普兰店区", "撤市设区", "2015", "普兰店市2015年撤市设区"),
    ("310230", "310151", "崇明县", "崇明区", "撤县设区", "2016", "崇明县2016年撤县设区"),
    ("320584", "320509", "吴江市", "吴江区", "撤市设区", "2012", "吴江市2012年撤市设区"),
    ("320124", "320117", "溧水县", "溧水区", "撤县设区", "2013", "溧水县2013年撤县设区"),
    ("320125", "320118", "高淳县", "高淳区", "撤县设区", "2013", "高淳县2013年撤县设区"),
    ("320323", "320312", "铜山县", "铜山区", "撤县设区", "2010", "铜山县2010年撤县设区"),
    ("330621", "330603", "绍兴县", "柯桥区", "撤县设区", "2013", "绍兴县2013年撤县设区（柯桥区）"),
    ("500227", "500120", "璧山县", "璧山区", "撤县设区", "2014", "璧山县2014年撤县设区"),
    ("440183", "440118", "增城市", "增城区", "撤市设区", "2014", "增城市2014年撤市设区"),
    ("320482", "320413", "金坛市", "金坛区", "撤市设区", "2015", "金坛市2015年撤市设区"),
    ("321088", "321012", "江都市", "江都区", "撤市设区", "2011", "江都市2011年撤市设区"),
]


# ============================================================
# 档案派遣业务表（student.db）—— 人才机构字典 / 转递单配置 / 派遣记录
# 幂等：全部 CREATE TABLE IF NOT EXISTS
# ============================================================
ARCHIVE_DDL = """
CREATE TABLE IF NOT EXISTS talent_agency (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agency_no   TEXT NOT NULL UNIQUE,
    county_code TEXT NOT NULL,
    county_name TEXT DEFAULT '',
    level       TEXT NOT NULL DEFAULT '县区',
    agency_name TEXT NOT NULL,
    address     TEXT DEFAULT '',
    phone       TEXT DEFAULT '',
    matchable   INTEGER DEFAULT 1,
    status      INTEGER DEFAULT 1,
    remark      TEXT DEFAULT '',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_talent_agency_county
    ON talent_agency (county_code);
CREATE INDEX IF NOT EXISTS idx_talent_agency_level
    ON talent_agency (level, status);

CREATE TABLE IF NOT EXISTS archive_settings (
    school          INTEGER PRIMARY KEY,
    school_name     TEXT DEFAULT '',
    handler         TEXT DEFAULT '',
    handler_phone   TEXT DEFAULT '',
    receipt_address TEXT DEFAULT '',
    receipt_postal  TEXT DEFAULT '',
    updated_by      TEXT DEFAULT '',
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS archive_dispatch (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_no        TEXT DEFAULT '',
    dispatch_no     TEXT DEFAULT '',
    student_id      INTEGER NOT NULL,
    student_no      TEXT DEFAULT '',
    id_card         TEXT DEFAULT '',
    student_name    TEXT DEFAULT '',
    graduate_year   TEXT DEFAULT '',
    school          INTEGER,
    grade           TEXT DEFAULT '',
    admin_class     TEXT DEFAULT '',
    county_code     TEXT DEFAULT '',
    county_name     TEXT DEFAULT '',
    origin_place    TEXT DEFAULT '',
    student_phone   TEXT DEFAULT '',
    parent_phone    TEXT DEFAULT '',
    match_level     TEXT DEFAULT '',
    match_status    TEXT DEFAULT '',
    match_reason    TEXT DEFAULT '',
    agency_id       INTEGER,
    agency_no       TEXT DEFAULT '',
    agency_name     TEXT DEFAULT '',
    agency_address  TEXT DEFAULT '',
    agency_phone    TEXT DEFAULT '',
    school_name     TEXT DEFAULT '',
    handler         TEXT DEFAULT '',
    handler_phone   TEXT DEFAULT '',
    receipt_address TEXT DEFAULT '',
    receipt_postal  TEXT DEFAULT '',
    archive_volume  TEXT DEFAULT '',
    employer_name   TEXT DEFAULT '',
    employer_address TEXT DEFAULT '',
    dispatch_status TEXT DEFAULT '待派遣',
    dispatch_date   TEXT DEFAULT '',
    operator        TEXT DEFAULT '',
    remark          TEXT DEFAULT '',
    is_deleted      INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_dispatch_student_active
    ON archive_dispatch (student_id) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_dispatch_batch  ON archive_dispatch (batch_no);
CREATE INDEX IF NOT EXISTS idx_dispatch_status ON archive_dispatch (dispatch_status);
CREATE INDEX IF NOT EXISTS idx_dispatch_county ON archive_dispatch (county_code);
"""

# 档案机构导入用的补充历史映射（旧码 → 现行码），已存在则跳过
ARCHIVE_HISTORY_EXTRA = [
    # 2023 年区划代码调整：加格达奇区/松岭区/新林区/呼中区由 232701-232704
    # 调整为 232761-232764，232701 改指漠河市（原 232723）。
    # engine1 仅在现行字典查无此码时才应用映射，故不影响现行学生数据匹配。
    ("232701", "232761", "加格达奇区", "加格达奇区", "编码调整", "", "档案机构表旧码映射"),
    ("232702", "232762", "松岭区", "松岭区", "编码调整", "", "档案机构表旧码映射"),
    ("232703", "232763", "新林区", "新林区", "编码调整", "", "档案机构表旧码映射"),
    ("232704", "232764", "呼中区", "呼中区", "编码调整", "", "档案机构表旧码映射"),
]


# ============================================================
# 共享过滤函数：统一排除学籍状态为"毕业"的学生
# 所有业务查询（学生列表/搜索/统计/导出等）必须调用此函数
# ============================================================
def _exclude_graduated_clause(alias=None):
    """返回排除毕业学生的 SQL WHERE 子句。
       alias='s' 用于带别名的查询（如 FROM students s），alias=None 用于无别名的查询"""
    if alias:
        return f"({alias}.registration_status IS NULL OR {alias}.registration_status != '毕业')"
    return "(registration_status IS NULL OR registration_status != '毕业')"


def _non_graduated_where(alias=None):
    """返回排除毕业+未删除学生的标准 WHERE 子句。
       alias='s' 用于带别名的查询，alias=None 用于无别名的查询"""
    if alias:
        return f"{alias}.is_deleted = 0 AND ({alias}.registration_status IS NULL OR {alias}.registration_status != '毕业')"
    return "is_deleted = 0 AND (registration_status IS NULL OR registration_status != '毕业')"

class _RequestDB:
    """请求级数据库连接包装器 — close() 为 no-op，真实关闭由 app.teardown_appcontext 处理"""
    def __init__(self, conn):
        self._conn = conn

    def close(self):
        """No-op：不真正关闭连接，由 teardown 统一清理"""
        pass

    def _real_close(self):
        """realclose。"""
        self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)

def get_db():
    # 请求级连接复用：同一请求内多次调用返回同一连接，请求结束自动关闭
    """获取数据库。"""
    if has_app_context():
        cached = g.get('_db_conn')
        if cached is not None:
            return cached

    conn = sqlite3.connect(DATABASE_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute('PRAGMA busy_timeout=5000')
    conn.execute('PRAGMA cache_size=-8000')

    if has_app_context():
        # 包装为 _RequestDB，使 close() 变成 no-op，防止调用方误关连接
        wrapper = _RequestDB(conn)
        g._db_conn = wrapper
        return wrapper

    return conn

def init_db():
    """初始化数据库：建表 + 增量迁移 + schema_version 版本推断。"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_no TEXT UNIQUE,
            national_no TEXT UNIQUE,
            id_card TEXT,
            name TEXT NOT NULL,
            gender TEXT,
            birth_date TEXT,
            ethnicity TEXT,
            address TEXT,
            major TEXT,
            admin_class TEXT,
            virtual_class TEXT,
            parent_name TEXT,
            parent_id_card TEXT,
            parent_phone TEXT,
            student_phone TEXT,
            grade TEXT,
            school INTEGER NOT NULL,
            registration_status TEXT DEFAULT '正式学籍',
            attendance_status TEXT DEFAULT '在校',
            photo_url TEXT DEFAULT '',
            is_deleted INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    try:
        cursor.execute('ALTER TABLE students ADD COLUMN photo_url TEXT DEFAULT \'\'')
    except sqlite3.OperationalError:
        pass

    new_columns = [
        ('issue_authority', "TEXT DEFAULT ''"),
        ('valid_start', "TEXT DEFAULT ''"),
        ('valid_end', "TEXT DEFAULT ''"),
        ('major_direction', "TEXT DEFAULT ''"),
        ('household_type', "TEXT DEFAULT ''"),
        ('junior_school', "TEXT DEFAULT ''"),
        ('admission_type', "TEXT DEFAULT ''"),
        ('junior_graduation_time', "TEXT DEFAULT ''"),
        ('change_info', "TEXT DEFAULT ''"),
        ('remark', "TEXT DEFAULT ''"),
    ]
    for col_name, col_def in new_columns:
        try:
            cursor.execute(f'ALTER TABLE students ADD COLUMN {col_name} {col_def}')
        except sqlite3.OperationalError:
            pass

    try:
        cursor.execute('ALTER TABLE students ADD COLUMN enrollment_year INTEGER DEFAULT 0')
    except sqlite3.OperationalError:
        pass

    try:
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_enrollment_year ON students(enrollment_year)')
    except sqlite3.OperationalError:
        pass

    new_columns_v2 = [
        ('name_pinyin', "TEXT DEFAULT ''"),
        ('student_insurance_no', "TEXT DEFAULT ''"),
        ('student_insurance_pay_date', "TEXT DEFAULT ''"),
        ('student_insurance_expiry', "TEXT DEFAULT ''"),
        ('insurance_premium', "TEXT DEFAULT '静默'"),
        ('internship_insurance_pay_date', "TEXT DEFAULT ''"),
        ('supervisor_flag', "TEXT DEFAULT ''"),
        ('declaration_date', "TEXT DEFAULT ''"),
        ('insurance_update_date', "TEXT DEFAULT ''"),
        ('tuition_status', "TEXT DEFAULT ''"),
    ]
    for col_name, col_def in new_columns_v2:
        try:
            cursor.execute(f'ALTER TABLE students ADD COLUMN {col_name} {col_def}')
        except sqlite3.OperationalError:
            pass

    # ========== 区划匹配：students 7 列（只增列，不改其他字段）==========
    # 代码列必须 TEXT（12 位、保留前导零），默认空串表示未匹配
    district_columns = [
        ('district_province_name', "TEXT DEFAULT ''"),
        ('district_province_code', "TEXT DEFAULT ''"),
        ('district_city_name', "TEXT DEFAULT ''"),
        ('district_city_code', "TEXT DEFAULT ''"),
        ('district_county_name', "TEXT DEFAULT ''"),
        ('district_county_code', "TEXT DEFAULT ''"),
        ('district_match_status', "TEXT DEFAULT ''"),
    ]
    for col_name, col_def in district_columns:
        try:
            cursor.execute(f'ALTER TABLE students ADD COLUMN {col_name} {col_def}')
        except sqlite3.OperationalError:
            pass

    # ========== 区划匹配：批次 / 结果 / 历史映射表 ==========
    for statement in DISTRICT_MATCH_DDL.split(';'):
        if statement.strip():
            cursor.execute(statement)

    # 上传清单匹配：留存原始表头与整行，导出时原样带回上传表单的列
    for table, column, col_def in (
            ('dm_match_batch', 'source_headers', "TEXT DEFAULT ''"),
            ('dm_match_result', 'raw_row', "TEXT DEFAULT ''")):
        try:
            cursor.execute(f'ALTER TABLE {table} ADD COLUMN {column} {col_def}')
        except sqlite3.OperationalError:
            pass

    # 历史映射种子：仅当表为空时写入一次，之后以表内记录为准
    try:
        if cursor.execute('SELECT COUNT(1) FROM dm_history_mapping').fetchone()[0] == 0:
            cursor.executemany(
                'INSERT INTO dm_history_mapping '
                '(old_code, new_code, old_name, new_name, change_type, change_date, remark) '
                'VALUES (?, ?, ?, ?, ?, ?, ?)', HISTORY_MAPPING_SEED)
    except sqlite3.OperationalError:
        pass
    # 立即提交种子事务，避免后续 migrate_* 新开连接时撞上写锁
    conn.commit()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            display_name TEXT,
            school_visible TEXT DEFAULT '*',
            fields_visible TEXT DEFAULT '[]',
            grade_visible TEXT DEFAULT '*',
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS update_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER,
            id_card TEXT,
            field_name TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            updated_by TEXT,
            batch_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_student_no ON students(student_no)')
    cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_national_no ON students(national_no)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_name ON students(name)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_id_card ON students(id_card)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_school ON students(school)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_grade ON students(grade)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_admin_class ON students(admin_class)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_virtual_class ON students(virtual_class)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_major ON students(major)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_registration_status ON students(registration_status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_attendance_status ON students(attendance_status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_is_deleted ON students(is_deleted)')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sync_timestamp (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS attendance_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_card TEXT NOT NULL,
            attendance_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT '出勤',
            update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_attendance_unique ON attendance_records(id_card, attendance_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_attendance_date ON attendance_records(attendance_date)')

    migrate_attendance_status()
    migrate_attendance_dates()
    migrate_attendance_id_card()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS dicts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dict_type TEXT NOT NULL,
            dict_value TEXT NOT NULL,
            create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_dict_type ON dicts(dict_type)')

    # ========== 学校类别字典表（编码稳定不复用，不建外键，引用校验在服务层）==========
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS school_types (
            code INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # ========== 模板导出引擎表 ==========
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS export_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_type TEXT NOT NULL,
            placeholders TEXT NOT NULL DEFAULT '[]',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS export_field_mappings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            template_id INTEGER NOT NULL,
            scheme_name TEXT DEFAULT '',
            mappings TEXT NOT NULL DEFAULT '[]',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (template_id) REFERENCES export_templates(id) ON DELETE CASCADE
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS export_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            template_id INTEGER,
            template_name TEXT,
            scheme_name TEXT,
            row_count INTEGER DEFAULT 0,
            file_path TEXT,
            status TEXT DEFAULT 'completed',
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # ========== 档案派遣：机构字典 / 转递单配置 / 派遣记录 ==========
    for statement in ARCHIVE_DDL.split(';'):
        if statement.strip():
            cursor.execute(statement)

    # 档案派遣增量列：最近一次退档时间（非空即「曾退档」，用于筛出重派单）
    for table, column, col_def in (
            ('archive_dispatch', 'returned_at', "TEXT DEFAULT ''"),):
        try:
            cursor.execute(f'ALTER TABLE {table} ADD COLUMN {column} {col_def}')
        except sqlite3.OperationalError:
            pass

    # 补充历史映射（幂等，逐条判重后插入）
    for old_code, new_code, old_name, new_name, change_type, change_date, remark in ARCHIVE_HISTORY_EXTRA:
        cursor.execute(
            'INSERT INTO dm_history_mapping '
            '(old_code, new_code, old_name, new_name, change_type, change_date, remark) '
            'SELECT ?, ?, ?, ?, ?, ?, ? WHERE NOT EXISTS ('
            'SELECT 1 FROM dm_history_mapping WHERE old_code = ? AND new_code = ?)',
            (old_code, new_code, old_name, new_name, change_type, change_date, remark,
             old_code, new_code)
        )

    # 转递单配置：按校别各一行（默认空，由转递单配置页填写）
    for code in ('1', '2'):
        cursor.execute('INSERT OR IGNORE INTO archive_settings (school) VALUES (?)', (int(code),))

    # ========== 迁移版本管理（P0-3）==========
    # 记录当前 schema 版本，便于后续迁移追踪与可观测性。
    # 兼容策略：首次运行时通过检测 students 表已有列推断当前版本并写入 schema_version 表，
    # 不改变现有迁移逻辑（仍为幂等 try/except ALTER），仅增加版本可观测性，向后完全兼容。
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS schema_version (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            version INTEGER NOT NULL DEFAULT 0,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    try:
        row = cursor.execute('SELECT version FROM schema_version WHERE id = 1').fetchone()
        if row is None:
            cols = {r[1] for r in cursor.execute('PRAGMA table_info(students)').fetchall()}
            if 'tuition_status' in cols:
                inferred_version = 2
            elif 'enrollment_year' in cols:
                inferred_version = 1
            else:
                inferred_version = 0
            cursor.execute('INSERT INTO schema_version (id, version) VALUES (1, ?)', (inferred_version,))
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()

    # 学校类别种子化：仅当表为空时写入 config.SCHOOL_TYPES（1=中职 / 2=技工）
    # 局部导入避免模块级循环依赖
    from models.school_types import seed_school_types
    seed_school_types()

def migrate_attendance_id_card():
    """数据库迁移：出勤id身份证（幂等，列已存在则跳过）。"""
    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT id_card FROM attendance_records LIMIT 1")
    except sqlite3.OperationalError:
        try:
            cursor.execute("ALTER TABLE attendance_records ADD COLUMN id_card TEXT")
        except sqlite3.OperationalError:
            pass

        cursor.execute("""
            UPDATE attendance_records
            SET id_card = (SELECT s.id_card FROM students s WHERE s.student_no = attendance_records.student_no)
            WHERE id_card IS NULL
        """)

        cursor.execute("""
            UPDATE attendance_records
            SET id_card = student_no
            WHERE id_card IS NULL
        """)

        conn.commit()

    try:
        cursor.execute("DROP INDEX IF EXISTS idx_attendance_unique")
    except sqlite3.OperationalError:
        pass

    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_attendance_unique ON attendance_records(id_card, attendance_date)")

    try:
        cursor.execute("ALTER TABLE attendance_records DROP COLUMN status_new")
    except sqlite3.OperationalError:
        pass

    try:
        cursor.execute("ALTER TABLE attendance_records DROP COLUMN student_no")
        conn.commit()
    except sqlite3.OperationalError:
        pass

    try:
        cursor.execute("DROP INDEX IF EXISTS idx_attendance_student_no_date")
    except sqlite3.OperationalError:
        pass

    conn.close()

def migrate_attendance_status():
    """数据库迁移：出勤状态（幂等，列已存在则跳过）。"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE attendance_records ADD COLUMN status_new TEXT DEFAULT '出勤'")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("UPDATE attendance_records SET status_new = CASE WHEN status = 1 THEN '出勤' WHEN status = 0 THEN '缺勤' ELSE '缺勤' END WHERE status_new IS NULL")
        cursor.execute("UPDATE attendance_records SET status = '出勤' WHERE status = 1")
        cursor.execute("UPDATE attendance_records SET status = '缺勤' WHERE status = 0")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("DROP COLUMN status_new")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()

def migrate_attendance_dates():
    """数据库迁移：出勤日期（幂等，列已存在则跳过）。"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS attendance_dates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            attendance_date TEXT UNIQUE NOT NULL,
            total_present INTEGER DEFAULT 0,
            total_absent INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

