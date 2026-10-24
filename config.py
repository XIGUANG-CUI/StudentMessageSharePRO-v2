import os
import secrets
from datetime import datetime

# 程序所在目录（代码层，打包后位于 _internal/）
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# 用户数据目录（数据层，打包后位于 EXE 同级的 user_data/）
# 通过 run_with_tray.py 在导入 config.py 之前设置环境变量 USER_DATA_DIR
_USER_DATA_DIR = os.environ.get('USER_DATA_DIR') or BASE_DIR

# ========== 端口配置 ==========
_CONFIG_FILE = os.path.join(BASE_DIR, 'config.txt')
_DEFAULT_PORT = 5001


def load_port():
    """加载端口号：环境变量 STUDENT_PORT > config.txt > 自动生成 config.txt

    自动生成时写入 _DEFAULT_PORT，确保代码中无硬编码端口。
    """
    env_port = os.environ.get('STUDENT_PORT', '').strip()
    if env_port:
        try:
            return int(env_port)
        except ValueError:
            print(f"[config] 环境变量 STUDENT_PORT={env_port} 不是有效整数，已忽略")

    if os.path.exists(_CONFIG_FILE):
        try:
            with open(_CONFIG_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if line.startswith('PORT='):
                        port_str = line.split('=', 1)[1].strip()
                        return int(port_str)
        except (ValueError, OSError) as e:
            print(f"[config] 读取 {_CONFIG_FILE} 失败: {e}")

    try:
        with open(_CONFIG_FILE, 'w', encoding='utf-8') as f:
            f.write(f"# 学生数据管理系统 — 运行端口\n")
            f.write(f"# 修改数字后重启应用生效，环境变量 STUDENT_PORT 优先\n")
            f.write(f"PORT={_DEFAULT_PORT}\n")
        print(f"[config] 已自动生成 {_CONFIG_FILE}")
        return _DEFAULT_PORT
    except OSError as e:
        raise RuntimeError(f"无法创建配置文件 {_CONFIG_FILE}: {e}，请检查目录权限") from e


PORT = load_port()

# ========== 其他配置 ==========

def _load_or_create_secret_key():
    """加载或创建持久化 SECRET_KEY：环境变量优先，其次读取 data/secret.key，不存在则生成并持久化。
    确保重启后 session/CSRF 仍然有效；OSError 时回退为内存随机值（保持原有降级行为）。"""
    env_key = os.environ.get('SECRET_KEY')
    if env_key:
        return env_key
    key_file = os.path.join(_USER_DATA_DIR, 'data', 'secret.key')
    try:
        if os.path.exists(key_file):
            with open(key_file, 'r', encoding='utf-8') as f:
                key = f.read().strip()
                if key:
                    return key
        os.makedirs(os.path.dirname(key_file), exist_ok=True)
        key = secrets.token_hex(32)
        with open(key_file, 'w', encoding='utf-8') as f:
            f.write(key)
        try:
            os.chmod(key_file, 0o600)
        except OSError:
            pass
        return key
    except OSError:
        return secrets.token_hex(32)

SECRET_KEY = _load_or_create_secret_key()


def _load_or_create_public_access_key():
    """加载或创建公开页面访问密钥：环境变量 PUBLIC_ACCESS_KEY 优先，
    其次读取 data/public_access.key，不存在则生成并持久化。
    用于 /public/* 与 /api/public/*（免登录公示页）的口令校验；
    持久化确保重启后已分发的带 k 链接仍然有效。"""
    env_key = os.environ.get('PUBLIC_ACCESS_KEY')
    if env_key:
        return env_key
    key_file = os.path.join(_USER_DATA_DIR, 'data', 'public_access.key')
    try:
        if os.path.exists(key_file):
            with open(key_file, 'r', encoding='utf-8') as f:
                key = f.read().strip()
                if key:
                    return key
        os.makedirs(os.path.dirname(key_file), exist_ok=True)
        key = secrets.token_urlsafe(24)
        with open(key_file, 'w', encoding='utf-8') as f:
            f.write(key)
        try:
            os.chmod(key_file, 0o600)
        except OSError:
            pass
        return key
    except OSError:
        return secrets.token_urlsafe(24)


PUBLIC_ACCESS_KEY = _load_or_create_public_access_key()

DATABASE_PATH = os.path.join(_USER_DATA_DIR, 'data', 'student.db')

# SDK 目录（读卡器 DLL 和控件安装包，位于程序目录而非用户数据目录）
SDK_DIR = os.path.join(BASE_DIR, 'sdk')

UPLOAD_FOLDER = os.path.join(_USER_DATA_DIR, 'uploads')
LOG_FOLDER = os.path.join(_USER_DATA_DIR, 'logs')

MAX_CONTENT_LENGTH = 10 * 1024 * 1024

# 身份证读卡器 WebSocket 地址持久化配置文件
CARD_READER_CONFIG_FILE = os.path.join(_USER_DATA_DIR, 'data', 'card_reader_config.json')
# 默认值（首次运行自动生成配置文件）
DEFAULT_CARD_READER_WS_URL = 'ws://127.0.0.1:9001'

ALLOWED_EXTENSIONS = {'xlsx', 'xls', 'csv'}

PERMANENT_SESSION_LIFETIME = 3600 * 2

SCHOOL_TYPES = {
    1: '中职',
    2: '技工'
}

REGISTRATION_STATUS = ['正式学籍', '未注册']

ATTENDANCE_STATUS = ['在校', '非在校', '托管', '待销假', '退学办结']

GENDER = ['男', '女']

ALL_STUDENT_FIELDS = [
    '学号', '国网学籍号', '学生身份证号', '学生姓名', '性别', '年龄', 
    '民族', '住址', '专业', '行政班级', '虚拟班级', '家长姓名', 
    '家长身份证号', '家长电话', '学生电话', '年级', '学籍状态', '在校状态',
    '签发机关', '有效期开始', '有效期结束', '专业方向', '户口性质',
    '初中毕业学校', '入学形式', '初中毕业时间', '异动信息', '备注',
    '入学年份',
    '姓名拼音', '学平险保单号', '学平险缴费日期', '学平险到期时间',
    '学平险保费金额', '实习险缴费日期', '主管标记', '申报日期',
    '保单更新日期', '学费总体情况',
    # 区划匹配 7 列：系统自动生成，不进导入模板（导入侧忽略用户填值）
    '省', '省区划代码', '市', '市区划代码', '县', '县区划代码', '匹配状态'
]

# 仅用于字段权限配置的附加字段（不在导入模板/学生导出白名单内）。
# 「派遣状态」勾选后，普通用户可在毕业生查询中看到派遣状态列（明细仍仅管理员可见）。
PERMISSION_EXTRA_FIELDS = ['派遣状态']

FIELD_MAPPING = {
    '学号': 'student_no',
    '国网学籍号': 'national_no',
    '学生身份证号': 'id_card',
    '学生姓名': 'name',
    '性别': 'gender',
    '年龄': 'age',
    '民族': 'ethnicity',
    '住址': 'address',
    '专业': 'major',
    '行政班级': 'admin_class',
    '虚拟班级': 'virtual_class',
    '家长姓名': 'parent_name',
    '家长身份证号': 'parent_id_card',
    '家长电话': 'parent_phone',
    '学生电话': 'student_phone',
    '年级': 'grade',
    '学籍状态': 'registration_status',
    '在校状态': 'attendance_status',
    '签发机关': 'issue_authority',
    '有效期开始': 'valid_start',
    '有效期结束': 'valid_end',
    '专业方向': 'major_direction',
    '户口性质': 'household_type',
    '初中毕业学校': 'junior_school',
    '入学形式': 'admission_type',
    '初中毕业时间': 'junior_graduation_time',
    '异动信息': 'change_info',
    '备注': 'remark',
    '入学年份': 'enrollment_year',
    '姓名拼音': 'name_pinyin',
    '学平险保单号': 'student_insurance_no',
    '学平险缴费日期': 'student_insurance_pay_date',
    '学平险到期时间': 'student_insurance_expiry',
    '学平险保费金额': 'insurance_premium',
    '实习险缴费日期': 'internship_insurance_pay_date',
    '主管标记': 'supervisor_flag',
    '申报日期': 'declaration_date',
    '保单更新日期': 'insurance_update_date',
    '学费总体情况': 'tuition_status',
    # 区划匹配 7 列（自动生成；导出引擎白名单由本映射派生）
    '省': 'district_province_name',
    '省区划代码': 'district_province_code',
    '市': 'district_city_name',
    '市区划代码': 'district_city_code',
    '县': 'district_county_name',
    '县区划代码': 'district_county_code',
    '匹配状态': 'district_match_status',
    '学校类别': 'school',
}

REVERSE_FIELD_MAPPING = {v: k for k, v in FIELD_MAPPING.items()}

TUITION_STATUS = ['已缴清', '部分欠费', '未缴', '免学费']

# ========== 保险状态机（单一事实来源） ==========
# supervisor_flag 实际只有 6 个语义状态（含空），此前散落在 blueprints/insurance.py、
# 多个前端 JS、config.py 三处；且原 INSURANCE_SUPERVISOR_FLAGS 漏列了"实习就业险"（已是 bug）。
# 现统一在此定义为唯一来源，所有读写都引用它，不再允许自由字符串写入 supervisor_flag。
#
# 办结态带年份前缀，格式为 {YYYY}年度（4 位年，如 2026年度学平险办结），由年份感知的
# 格式化函数生成，绝不硬编码某一年 —— 2027 → 2027年度学平险办结，2099 → 2099年度… 均正确，
# 且无 2 位年的世纪翻转歧义。
CURRENT_SCHOOL_YEAR = datetime.now().year  # 系统当前学年基准 = 保险批次年份来源；如需固定某学年可在此改值


class InsuranceStatus:
    UNREGISTERED = ''                       # NULL / ''
    PENDING_XUEPING = '按计划办理学平险'      # 学平险待办结（第1/2年复用同一存储值）
    PENDING_INTERN = '按计划办理实习就业险'   # 实习险待办结
    EXEMPT = '不参保'                        # 不参保
    EXTERNAL_PAY_MARKER = '新生默认首保'        # 新生默认首保占位标记（非真实缴费日期），前端据此渲染灰显徽标

    @staticmethod
    def yyyy(y):
        return str(y)

    @staticmethod
    def done_xueping(y):
        """{YYYY}年度学平险办结"""
        return f'{InsuranceStatus.yyyy(y)}年度学平险办结'

    @staticmethod
    def done_intern(y):
        """{YYYY}年度实习就业险办结"""
        return f'{InsuranceStatus.yyyy(y)}年度实习就业险办结'

    @staticmethod
    def parse_flag_year(flag):
        """从办结态存储值反向解析年份，如 '2026年度学平险办结' → 2026；非办结/未知 → None"""
        import re
        if not flag:
            return None
        m = re.search(r'(\d{4})年度', flag)
        return int(m.group(1)) if m else None

    @staticmethod
    def is_done(flag):
        return bool(flag) and '办结' in flag

    @staticmethod
    def done_type(flag):
        """返回 'xueping' / 'intern' / None"""
        if not flag:
            return None
        if '实习就业险' in flag and '办结' in flag:
            return 'intern'
        if '学平险' in flag and '办结' in flag:
            return 'xueping'
        return None


# 待办结可选值（下拉 / 校验集合），由枚举派生 —— 顺手修复原 config.py 漏列"实习就业险"的 bug
INSURANCE_SUPERVISOR_FLAGS = [
    InsuranceStatus.PENDING_XUEPING,
    InsuranceStatus.PENDING_INTERN,
    InsuranceStatus.EXEMPT,
]

BATCH_UPDATE_ALLOWED_FIELDS = [
    'registration_status', 'attendance_status', 'admin_class', 
    'virtual_class', 'major', 'grade', 'student_phone', 
    'parent_phone', 'address'
]

INSURANCE_BATCH_FIELDS = [
    'student_insurance_no', 'student_insurance_pay_date',
    'student_insurance_expiry', 'insurance_premium',
    'internship_insurance_pay_date', 'supervisor_flag'
]

BACKUP_ENABLED = True
BACKUP_ON_STARTUP = True
BACKUP_AUTO_ENABLED = True
BACKUP_AUTO_INTERVAL = 1800

BACKUP_STATUS_POLL_INTERVAL = 5000
BACKUP_COUNTDOWN_POLL_FAST = 3000
BACKUP_COUNTDOWN_POLL_SLOW = 30000
BACKUP_COUNTDOWN_THRESHOLD_SECONDS = 60

# ========== 模板导出引擎配置 ==========
TEMPLATE_FOLDER = os.path.join(_USER_DATA_DIR, 'data', 'templates')
EXPORT_OUTPUT_FOLDER = os.path.join(_USER_DATA_DIR, 'data', 'exports')

# ========== 照片存储目录 ==========
# 用户照片存储在 user_data/static/photos 下（与代码分离）
PHOTOS_DIR = os.path.join(_USER_DATA_DIR, 'static', 'photos')
TEMPLATE_ALLOWED_EXTENSIONS = {'xlsx', 'xls', 'docx'}
EXPORT_MAX_ROWS = 10000
EXPORT_TEMP_FILE_TTL_SECONDS = 3600  # 临时导出文件 1 小时后清理

class Config:
    SECRET_KEY = SECRET_KEY
    UPLOAD_FOLDER = UPLOAD_FOLDER
    LOG_FOLDER = LOG_FOLDER
    MAX_CONTENT_LENGTH = MAX_CONTENT_LENGTH
    # P0-NEW-2: SESSION_COOKIE_SECURE 可观测化。
    # 本地部署默认 False（HTTP），如启用 HTTPS 部署请设置环境变量 SESSION_COOKIE_SECURE=1
    # 或直接修改此处为 True，否则 session cookie 可被中间人嗅探。
    SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', '0').strip() == '1'
    SESSION_COOKIE_HTTPONLY = True
    SESSION_PERMANENT = True
    PERMANENT_SESSION_LIFETIME = PERMANENT_SESSION_LIFETIME