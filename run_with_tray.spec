# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules
from PyInstaller.building.api import COLLECT

# ====================== 基础配置 ======================
# 获取 spec 文件所在目录作为项目根路径
if '__file__' not in locals():
    spec_file = os.path.abspath(sys.argv[0])
else:
    spec_file = os.path.abspath(__file__)
base_path = os.path.dirname(spec_file)
block_cipher = None

# ====================== 版本信息配置 ======================
# version.txt 包含 PyInstaller 所需的 VSVersionInfo 结构
version_file = os.path.join(base_path, 'version.txt')

# 从 version.txt 动态读取产品名称和版本号
import re
exe_name = '学生数据管理系统'
if os.path.exists(version_file):
    with open(version_file, 'r', encoding='utf-8') as f:
        version_content = f.read()
    # 提取 ProductName
    match_name = re.search(r"StringStruct\(u'ProductName',\s*u'([^']+)'\)", version_content)
    if match_name:
        exe_name = match_name.group(1)
    # 提取 ProductVersion
    match_version = re.search(r"StringStruct\(u'ProductVersion',\s*u'([^']+)'\)", version_content)
    if match_version:
        exe_name += match_version.group(1)

# ====================== 收集 DLL 二进制文件 ======================
binaries = []

# SDK 目录下的身份证读卡器 DLL（ctypes 运行时动态加载）
sdk_dir = os.path.join(base_path, 'sdk')
if os.path.exists(sdk_dir):
    for file in os.listdir(sdk_dir):
        if file.lower().endswith(('.dll', '.so', '.dylib')):
            binaries.append((os.path.join(sdk_dir, file), 'sdk'))

# ====================== 收集数据文件 ======================
# 原则：只打包只读资源（模板/静态文件/SDK/图标/版本号）。
# 可写运行时不打包，由 run_with_tray.py 启动时自动创建：
#   data/         → init_db() 首次建库
#   databackup/   → backup_service 轮换备份
#   uploads/      → 用户上传文件
#   static/photos/ → 读卡器拍照
datas = []

# 1. Jinja2 模板文件（保持子目录结构）
templates_dir = os.path.join(base_path, 'templates')
if os.path.exists(templates_dir):
    for root, dirs, files in os.walk(templates_dir):
        for file in files:
            src = os.path.join(root, file)
            rel = os.path.relpath(root, base_path)
            datas.append((src, rel))

# 2. 静态资源（CSS / JS，排除运行时创建的 photos/ 子目录）
static_dir = os.path.join(base_path, 'static')
if os.path.exists(static_dir):
    for root, dirs, files in os.walk(static_dir):
        # 跳过运行时创建的照片目录（含子目录）
        if os.path.basename(root) == 'photos':
            dirs.clear()
            continue
        for file in files:
            src = os.path.join(root, file)
            rel = os.path.relpath(root, base_path)
            datas.append((src, rel))

# 区划数据包（约 5.16 MiB）。缺失直接让打包失败，避免产出无字典的 EXE
pack_path = os.path.join(base_path, 'data', 'district_pack.tsv.gz')
if not os.path.exists(pack_path):
    raise SystemExit(f'[spec] 缺少区划数据包：{pack_path}')
datas.append((pack_path, 'data'))

# 校验文件随包分发，便于现场核对数据包完整性（运行期校验依赖 pack.py 内联常量）
sha_path = os.path.splitext(pack_path)[0] + '.sha256'
if os.path.exists(sha_path):
    datas.append((sha_path, 'data'))

# 3. SDK 文件（读卡器 DLL + license.dat）
if os.path.exists(sdk_dir):
    for file in os.listdir(sdk_dir):
        datas.append((os.path.join(sdk_dir, file), 'sdk'))

# 4. 图标文件（收集到 static/ 子目录，供 Flask url_for('static') 访问）
for icon_name in ['favicon.ico', 'favicon.png', 'icon.ico', 'icon.png']:
    icon_path = os.path.join(base_path, icon_name)
    if os.path.exists(icon_path):
        datas.append((icon_path, 'static'))

# 6. openpyxl 和 qrcode 的隐式依赖数据
datas += collect_data_files('openpyxl')
datas += collect_data_files('qrcode')

# 7. python-docx（Word 模板导出）的隐式依赖数据
#    含 docx/templates/default.docx 等内置模板文件，缺失会导致 Document() 初始化失败
datas += collect_data_files('docx')

# ====================== 隐藏导入（防止分析遗漏） ======================
hiddenimports = []

# openpyxl 全部子模块（Excel 读写需要，约 189 个子模块）
hiddenimports += collect_submodules('openpyxl')

# python-docx 全部子模块（Word 模板解析/填充需要，含 docx.oxml / docx.opc / docx.templates 等）
hiddenimports += collect_submodules('docx')

# lxml（python-docx 的底层 XML 引擎，C 扩展需显式声明防止打包遗漏）
hiddenimports += [
    'lxml',
    'lxml.etree',
    'lxml._elementpath',
]

# qrcode 二维码生成
hiddenimports += [
    'qrcode',
    'qrcode.image.pil',
]

# PIL/Pillow（托盘图标绘制 + 二维码图片生成）
hiddenimports += [
    'PIL',
    'PIL.Image',
    'PIL.ImageFile',
    'PIL.ImageDraw',
    'PIL.ImageFont',
    'PIL._imaging',
]

# pystray 系统托盘（Windows 平台 _win32 后端）
hiddenimports += [
    'pystray',
    'pystray._win32',
]

# tkinter（启动完成弹窗）
hiddenimports += [
    'tkinter',
    'tkinter.messagebox',
]

# 项目 services 子模块
hiddenimports += [
    'services',
    'services.backup_service',
    'services.card_reader',
    'services.insurance_service',
    'services.import_service',
]

# 项目 models 包子模块（P1-C：models.py 拆分为包）
hiddenimports += [
    'models',
    'models.db',
    'models.users',
    'models.students',
    'models.lifecycle',
    'models.attendance',
    'models.stats',
    'models.dicts',
    'models.export_template',
    'models.logs',
]

# 项目 blueprints 子模块（P1-1 路由模块化拆分，10 个 Blueprint）
hiddenimports += [
    'blueprints',
    'blueprints.mobile',
    'blueprints.public',
    'blueprints.backup',
    'blueprints.export',
    'blueprints.insurance',
    'blueprints.user',
    'blueprints.admin',
    'blueprints.leave_cancel',
    'blueprints.lifecycle',
    'blueprints.api',
]

# 其他必要模块
hiddenimports += [
    'ipaddress',       # 局域网 IP 检测
    'threading',       # 定时备份 daemon 线程
    'datetime',        # 时间处理
    'shutil',          # 备份文件复制
    'sqlite3',         # 数据库引擎
    'logging',         # 日志记录（P0-NEW-1：6 个模块使用 logging.getLogger）
    'logging.handlers',# 日志轮转（RotatingFileHandler）
]

# ====================== 排除模块（减小体积） ======================
# 注意：scripts/ 目录（backfill 脚本 + 旧 spec）不参与打包，无需在此声明。
excludes = [
    'pandas',           # P2-3：已从 requirements.txt 移除，此处保留作为安全网防止误装
    'matplotlib',       # 不在本项目中绘图
    'numpy',            # pandas 依赖，随 pandas 一起排除（安全网）
    'scipy',            # 科学计算，不需要
    'PyQt5',            # GUI 框架，不需要
    'wx',               # GUI 框架，不需要
    'tensorflow',       # ML 框架，不需要
    'torch',            # ML 框架，不需要
    'sklearn',          # 机器学习，不需要
    'notebook',         # Jupyter，不需要
    'ipython',          # 交互式终端，不需要
    'prompt_toolkit',   # IPython 依赖
    'PIL.ImageQt',      # Qt 后端，不需要
    'PIL.ImageTk',      # ImageTk 不直接使用
    'PIL._webp',        # WebP 格式，不需要
    'tkinter.test',     # tkinter 测试
    'tkinter.ttk',      # ttk 主题组件，不需要
    'unittest',         # 单元测试框架
    'test',             # 测试目录
    'pydoc',            # 文档工具
    'distutils',        # 已废弃
    'setuptools',       # 安装工具
]

# ====================== Analysis 分析配置 ======================
a = Analysis(
    ['run_with_tray.py'],
    pathex=[base_path],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# ====================== EXE 入口 ======================
# 子进程不需要 DLL 和资源（由 COLLECT 集中管理），exclude_binaries=True
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=exe_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,              # 窗口化运行，不显示控制台黑窗
    disable_windowed_traceback=False,
    argv_emulation=False,
    version=version_file,        # 从 version.txt 读取版本信息
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(base_path, 'favicon.ico'),
)

# ====================== COLLECT 目录输出 ======================
# onedir 模式：将所有依赖收集到 dist/学生数据管理系统V1.0/ 目录
# 可写运行时数据（data/databackup/uploads/photos）不在 datas 中，
# 由 run_with_tray.py 启动时自动创建在 exe 同级目录，方便直接维护
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=exe_name,
)
