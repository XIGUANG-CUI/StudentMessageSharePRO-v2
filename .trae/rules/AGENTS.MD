# AGENT.MD — 学生数据管理系统

## 项目概述

这是一个基于 **Flask + SQLite + Bootstrap 5** 的学生数据管理系统（StudentMessageSharePRO），支持多学校类别（默认中职/技工，可在字典管理中动态新增）的学生信息管理、Excel 批量导入/更新、多维度查询统计、手机端出勤点名、保险管理、毕业/实习管理、退学处理、模板导出引擎，以及基于角色的多层级权限管理。

- **技术栈**：Python 3.10+ / Flask 3.0 / Bootstrap 5.3 / SQLite (WAL模式) / Chart.js / openpyxl / qrcode[pil] / python-docx
- **启动方式**：`python run_with_tray.py`（带系统托盘图标，**推荐**）或 `python app.py`
- **默认端口**：`5001`
- **项目根目录**：`StudentMessageSharePRO`

---

## 📅 文档更新记录

| 更新日期 | 更新内容摘要 | 触发方式 |
|---------|------------|---------|
| 2026-06-27 | 全量同步代码（保险/毕业/实习/导出引擎/拼音/退学/转学/备份额度/安全防护等模块补充） | AI Agent 全量扫描 |
| 2026-06-27 | 细节增强：字段赋值规则/路由鉴权列/代码位置引用/工具函数签名/业务规则状态机 | AI Agent 细节补充 |
| 2026-09-10 | 新增公开班级花名册页；新增公开页面口令校验机制（PUBLIC_ACCESS_KEY + 限流 scope 隔离），控制台按钮改为带口令链接 ⭐ |
| 2026-10-23 | 新增学校类别字典化：`school_types` 表 + 服务层 + 字典页「学校类别」页签；学生/查询/统计/毕业/导出引擎/用户权限/保险页面动态化；停用语义与删除保护；导入校验白名单动态化 ⭐ | AI Agent 细节补充 |

---

## 项目结构

```
StudentMessageSharePRO/
├── app.py                     # Flask 主应用（路由、业务逻辑、安全中间件）
├── auth.py                    # 认证模块（登录/7种权限装饰器/字段/学校/年级权限/敏感数据脱敏）
├── config.py                  # 全局配置（数据库路径、端口、字段映射、备份/导出参数、安全配置）⭐更新
├── models.py                  # 数据模型层（SQLite CRUD、建表、索引、毕业/实习/退学/导出引擎表操作）
├── utils.py                   # 工具函数（身份证解析与校验、电话验证、Excel模板、过滤解析）
├── pinyin_utils.py            # 中文姓名→拼音转换（26671字映射、27复姓、49异体字）⭐新增
├── run_with_tray.py           # Windows 托盘启动器（pystray，含打开/重启/退出）
├── run_with_tray.spec         # PyInstaller 打包配置
├── requirements.txt           # Python 依赖
├── version.txt                # 版本信息文件（for PyInstaller）
├── backfill_pinyin.py         # 拼音存量回填脚本（--force/--dry-run）⭐新增
├── backfill_birth_date.py     # 出生日期回填脚本
├── _test_confirm.py           # 测试工具
├── data/
│   ├── student.db             # SQLite 数据库（自动创建）
│   ├── card_reader_config.json# 身份证读卡器 WebSocket 配置
│   ├── templates/             # 模板导出引擎的上传模板存储 ⭐新增
│   └── exports/               # 模板导出引擎的结果文件存储 ⭐新增
├── databackup/                # 数据库备份目录（AB 轮换）⭐更新
│   ├── student_A.db           # 备份 A 文件
│   ├── student_B.db           # 备份 B 文件
│   ├── backup_rotation.txt    # 轮换记录
│   ├── backup_config.json     # 备份间隔配置 ⭐新增
│   └── backup_log.txt         # 备份日志
├── services/
│   ├── __init__.py            # 服务模块初始化
│   ├── card_reader.py         # 身份证读卡器服务（ctypes DLL / 华视 SDK）
│   └── backup_service.py      # 数据库备份服务（AB轮换+SQLite原生API+定时备份+间隔动态调整+日志清理）
├── sdk/                       # 华视身份证读卡器 SDK DLL（termb.dll, sdtapi.dll, WltRS.dll, DLL_File.dll）
├── static/
│   ├── css/style.css          # 自定义样式（Toast、确认弹窗、打印CSS）
│   ├── js/
│   │   ├── main.js            # 前端公共JS（Toast、showConfirm、safeJSON、validatePhone、CSRF自动注入）
│   │   └── card_reader.js     # 浏览器端 WebSocket 身份证读卡器
│   └── photos/                # 照片目录（运行时用户数据目录）
├── templates/
│   ├── base.html              # 母版页（导航栏、flash JSON注入、CSRF Token注入、保险导航菜单）
│   ├── login.html             # 桌面端登录页
│   ├── _field_help.html       # 字段帮助组件
│   ├── admin/                 # 管理员端模板
│   │   ├── dashboard.html     # 控制台/仪表盘（统计卡片+图表+分布表+打印+备份管理面板+更新在校状态按钮）
│   │   ├── year_selector.html # 入学年份选择页
│   │   ├── students.html      # 学生列表（含新增/编辑弹窗、身份证读卡、datalist字典联动、17列表格）
│   │   ├── student_form.html  # 添加/编辑学生独立表单
│   │   ├── student_change.html# 学籍异动快速处理 + 转学待办表 + 实习处理面板 ⭐更新
│   │   ├── student_dropout.html# 退学学生处理（退学办结列表 + 导出Excel + 软删除标记 + 日期筛选）
│   │   ├── import.html        # 批量导入（上传→预览→导入进度）
│   │   ├── batch_update.html  # 批量更新（上传→预览→更新进度）
│   │   ├── leave_cancel.html  # 销假处理（搜索→缺勤列表→一键销假+二维码+导出Excel）
│   │   ├── users.html         # 用户管理（CRUD、状态切换、密码重置）
│   │   ├── dicts.html         # 字典管理（添加/导入/导出/筛选/删除）
│   │   ├── export_engine.html # 模板驱动数据导出引擎 ⭐新增
│   │   ├── graduation.html    # 毕业管理（毕业处理/撤销毕业/预览弹窗搜索）⭐新增
│   │   ├── graduation_query.html # 毕业生查询页（学校/年级/班级/姓名筛选联动+CSV导出）⭐新增
│   │   └── insurance_query.html  # 保险综合查询页 ⭐新增
│   ├── user/                  # 普通用户端模板
│   │   ├── query.html         # 多维查询（含班级对照表+打印）
│   │   └── stats.html         # 统计面板（图表+分布表+打印）
│   ├── mobile/                # 手机端出勤点名模板（独立UI）
│   │   ├── login.html         # 手机端登录页
│   │   ├── grades.html        # 年级选择页
│   │   ├── classes.html       # 班级列表页（含完成状态标记）
│   │   └── attendance.html    # 学生出勤点名页（滑动开关+批量提交）
│   ├── insurance/             # 保险管理模板 ⭐新增
│   │   ├── register.html      # 保险缴费登记（财务人员/财务主管）
│   │   ├── approve.html       # 保险审批（财务主管）
│   │   ├── todo.html          # 保险待办（保险员）
│   │   └── init.html          # 保险初始化导入（管理员）
│   └── public/                # 公开页面（免登录，统一需口令 k）
│       ├── pending_leave.html # 待销假公示页
│       └── roster.html        # 班级花名册公示页 ⭐新增
└── uploads/                   # 上传文件临时目录
```

---

## 角色体系

| 角色 | 权限说明 | 登录跳转 | 数据视图 |
|---|---|---|---|
| `admin` | 全部权限：增删改查、导入导出、用户管理、统计、出勤点名、保险管理、毕业管理 | 控制台 `/admin` | 全量数据，无脱敏 |
| `user` | 只读查询 + 统计面板，字段/学校/年级按 `*_visible` 限制 | 查询页 `/user/query` | 字段过滤+脱敏 |
| `attendance` | 仅手机端出勤点名，按 `grade_visible` 限制可见年级 | 年级选择 `/mobile/grades` | 仅出勤相关字段 |
| `finance_chief` ⭐ | 财务主管：保险缴费登记 + 保险审批 + 多维查询 | 保险缴费登记 `/insurance/register` | 全量数据视图 |
| `finance_staff` ⭐ | 财务人员：保险缴费登记 + 多维查询 | 保险缴费登记 `/insurance/register` | 全量数据视图 |
| `insurance_officer` ⭐ | 保险员：保险待办 + 多维查询 | 保险待办 `/insurance/todo` | 全量数据视图 |

> **权限生效位置**：`auth.py:6-74`（6个装饰器函数），保险角色通过 `auth.py:158` 的 `_is_insurance_role()` 绕过 `check_school_permission()` / `check_grade_permission()` 的学校/年级限制。

### 角色 × 功能模块权限矩阵

> 依据各 Blueprint 路由装饰器实际整理（`admin.py`/`api.py`/`lifecycle.py`/`insurance.py`/`export.py`/`leave_cancel.py`/`mobile.py`）。✅=可访问，❌=重定向 `/user/query` 或 JSON 403。

| 功能模块（主要路由） | admin | attendance | finance_staff | finance_chief | insurance_officer | user |
|---|:--:|:--:|:--:|:--:|:--:|:--:|
| 控制台 `/admin` | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 学生列表/增删改查/导出/照片 `/admin/students*`、`/api/admin/student*` | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 批量新增导入 / 批量更新 `/admin/import*`、`/admin/batch-update*` | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 学籍异动 / 退学 / 毕业管理 `/admin/student-change|student-dropout|graduation` | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 销假处理 / 回收站 `/admin/leave-cancel`、`/api/admin/trash*` | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 用户管理 / 字典管理 / 备份 `/admin/users`、`/admin/dicts`、`/api/backup*` | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 模板导出引擎 `/admin/export-engine`、`/api/export/*` | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 保险初始化导入 `/insurance/init`、`/api/insurance/init-*` | ✅ | ❌ | ❌ | ❌ | ✅ | ❌ |
| 保险缴费登记 `/insurance/register`、`/api/insurance/register*` | ✅ | ❌ | ✅ | ✅ | ❌ | ❌ |
| 保险审批 `/insurance/approve`、`/api/insurance/approve*` | ✅ | ❌ | ❌ | ✅ | ❌ | ❌ |
| 保险待办/办结/导出 `/insurance/todo`、`/api/insurance/todo*`、`/api/insurance/complete` | ✅ | ❌ | ❌ | ❌ | ✅ | ❌ |
| 保单查询 `/admin/insurance-query`、`/api/insurance/query` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 移动端出勤点名 `/mobile/classes/*/attendance` | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| 移动端年级/班级浏览 `/mobile/grades`、`/mobile/grades/<g>/classes` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 多维查询 `/user/query` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 统计面板 `/user/stats` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 毕业生查询 `/admin/graduation/query` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 公开待处理销假 `/public/pending-leave`、`/api/public/pending-leave`（免登录 + 口令 k + IP 限流） | 所有人（无需登录） | — | — | — | — | — |
| 公开班级花名册 `/public/roster`、`/api/public/roster`（免登录 + 口令 k + IP 限流）⭐新增 | 所有人（无需登录） | — | — | — | — | — |

> 说明：保单查询、多维查询、统计面板、毕业生查询底层均为 `login_required`，对全部登录角色开放；保险三角色在导航栏默认隐藏「多维查询/统计面板」入口（`base.html:140-161`），但直接访问 URL 仍可用。保险三角色中，`finance_staff`/`finance_chief` 仅能进登记页，`finance_chief` 追加审批页，`insurance_officer` 进待办页（互不越权）。

### 数据级权限（跨所有角色生效）

| 权限维度 | admin | 其他角色（出勤/财务/保险/普通） | 代码位置 |
|---|---|---|---|
| 字段可见性 | 全部字段 | 按 `fields_visible` 白名单过滤；空值默认仅 9 个基础字段（学号/姓名/性别/年龄/专业/行政班级/年级/学籍状态/在校状态） | `auth.py:214-252` `filter_student_data()` / `get_visible_fields()` |
| 学校可见性 | 全校 | 按 `school_visible` 过滤；空值或 `*` 表示不限制 | `auth.py` `check_school_permission()` / `parse_visible_school_codes()` |
| 年级可见性 | 全年级 | 按 `grade_visible`（默认 `*`）过滤 | `auth.py:173-185` `check_grade_permission()` |
| 敏感字段脱敏 | 不脱敏 | 身份证 `前3+********+后4`；电话 `前3+****+后4` | `auth.py:254-267` `mask_sensitive_data()` |

> ⚠️ 保险三角色（`finance_chief`/`finance_staff`/`insurance_officer`）仅在 `check_school_permission()` 与 `check_grade_permission()` 中直接放行（全校全年级可见），**字段白名单过滤与脱敏仍生效**（脱敏只豁免 `admin`）。另：`apply_permission_filters()`（`auth.py:186-211`）**没有保险角色分支**，仅 `admin` 直接返回原 filters，故其文档描述以实际代码为准。

---

## 数据库表结构

### students（学生表）⚠️ 全量 40 字段

| 字段 | 类型 | 业务含义 | 赋值规则 / 格式约束 |
|---|---|---|---|
| id | INTEGER PK | 系统主键 | 自增，不允许手动赋值 |
| student_no | TEXT UNIQUE | 学号 | 6位数字（`^\d{6}$`），转学待办三段校验，新增时自动生成或手动填写 |
| national_no | TEXT UNIQUE | 国网学籍号 | ⚠️ 已废弃：前端表单已移除，DB列保留但不参与CRUD |
| id_card | TEXT | 身份证号 | 18位，`validate_id_card_detailed()` 7层校验，出勤匹配主键 |
| name | TEXT NOT NULL | 姓名 | 必填，触发 `pinyin_utils.name_to_pinyin()` 自动生成 `name_pinyin` |
| gender | TEXT | 性别 | 枚举：`男`/`女`，新增时从身份证第17位自动推导（奇数男偶数女） |
| birth_date | TEXT | 出生日期 | `YYYY-MM-DD`，从身份证第7-14位提取 `utils.extract_birth_date_from_id_card()` |
| ethnicity | TEXT | 民族 | 自由文本，通过字典 `<datalist>` 提供建议值 |
| address | TEXT | 住址 | 自由文本 |
| major | TEXT | 专业 | 自由文本，修改时触发 `_derive_school(major)` 自动推导 `school` 类型 |
| admin_class | TEXT | 行政班级 | 自由文本，班级变更记录在 `update_logs` 中 |
| virtual_class | TEXT | 虚拟班级 | 自由文本，可选字段 |
| parent_name | TEXT | 家长姓名 | 自由文本 |
| parent_id_card | TEXT | 家长身份证号 | 18位格式 |
| parent_phone | TEXT | 家长电话 | `validate_phone()` 校验：`1\d{10}` + 号段库白名单，空格和连字符自动去除 |
| student_phone | TEXT | 学生电话 | 同 `parent_phone` 校验规则 |
| grade | TEXT | 年级 | 如 `2023级`，新增时自动推导（初中毕业时间→session年份），转学生自动-1 |
| enrollment_year | INTEGER | 入学年份 | 新增时从 session `enrollment_year` 写入，编辑不可变，非admin被过滤 |
| school | INTEGER NOT NULL | 学校类别编码 | 取值来自 `school_types` 字典（默认 `1`=中职 / `2`=技工），新增时由 `_derive_school(major)` 推导，未推导出则取首个启用类别 |
| registration_status | TEXT | 学籍状态 | 枚举：`正式学籍`/`未注册`/`毕业`，新增默认 `'未注册'` |
| attendance_status | TEXT | 在校状态 | 枚举：`在校`/`非在校`/`托管`/`待销假`/`退学办结` |
| photo_url | TEXT | 照片 | URL或Base64，由身份证读卡器拍照填充 |
| issue_authority | TEXT | 签发机关 | 身份证签发机关 |
| valid_start | TEXT | 有效期开始 | `YYYYMMDD` 格式 |
| valid_end | TEXT | 有效期结束 | `YYYYMMDD` 格式 |
| major_direction | TEXT | 专业方向 | 自由文本 |
| household_type | TEXT | 户口性质 | 自由文本 |
| junior_school | TEXT | 初中毕业学校 | 自由文本 |
| admission_type | TEXT | 入学形式 | `普通入学`/`转学生`等，变更时联动 grade ±1 |
| junior_graduation_time | TEXT | 初中毕业时间 | `YYYYMM` 格式，用于计算 `enrollment_year` 和推导 `grade` |
| change_info | TEXT | 异动信息 | 分号分隔追加，格式 `操作类型&文件编号`，不覆盖 |
| remark | TEXT | 备注 | 自由文本，毕业/实习处理时自动追加备份标记 |
| is_deleted | INTEGER | 软删除标记 | `0`=正常 / `1`=已删除，所有查询默认加 `is_deleted=0` |
| created_at | TIMESTAMP | 创建时间 | 自动写入 |
| updated_at | TIMESTAMP | 更新时间 | 每次更新自动刷新 |
| name_pinyin | TEXT | 姓名拼音 | 如 `ShanYaoPang`，由 `pinyin_utils.name_to_pinyin()` 在5处触发点自动生成 ⭐新增 |
| student_insurance_no | TEXT | 学平险保单号 | 保险缴费登记时填写 ⭐新增 |
| student_insurance_pay_date | TEXT | 学平险缴费日期 | `YYYY-MM-DD` ⭐新增 |
| student_insurance_expiry | TEXT | 学平险到期时间 | `YYYY-MM-DD` ⭐新增 |
| insurance_premium | TEXT | 学平险保费金额 | 初始化导入默认 `'静默'`，缴费登记可修改 ⭐新增 |
| internship_insurance_pay_date | TEXT | 实习险缴费日期 | `YYYY-MM-DD` ⭐新增 |
| supervisor_flag | TEXT | 主管标记 | 见下方状态机规则 ⭐新增 |
| declaration_date | TEXT | 申报日期 | 办结时自动填入当前日期 `YYYY-MM-DD` ⭐新增 |
| insurance_update_date | TEXT | 保单更新日期 | 办结时自动填入当前日期 `YYYY-MM-DD` ⭐新增 |
| tuition_status | TEXT | 学费总体情况 | 枚举：`已缴清`/`部分欠费`/`未缴`/`免学费` ⭐新增 |

### dicts（字典表）

| 字段 | 类型 | 业务含义 | 赋值规则 |
|---|---|---|---|
| id | INTEGER PK | 自增主键 | 自动 |
| dict_type | TEXT NOT NULL | 字典类型（如 `major`/`ethnicity`/`admin_class`） | 必填 |
| dict_value | TEXT NOT NULL | 字典值 | 必填 |
| created_at | TIMESTAMP | 创建时间 | 自动 |

- 联合唯一约束：`UNIQUE(dict_type, dict_value)`
- 前端 `<datalist>` + 异步加载 `/api/dicts`
- 批量导入通过 `/api/dicts/batch` Excel 文件上传

### school_types（学校类别字典表）⭐新增

| 字段 | 类型 | 业务含义 | 赋值规则 |
|---|---|---|---|
| code | INTEGER PK | 学校类别编码 | 手动指定，稳定不复用 |
| name | TEXT NOT NULL UNIQUE | 类别名称（如 `中职`/`技工`） | 不可重名 |
| is_active | INTEGER | 启用状态 | `1`=启用 / `0`=停用 |
| created_at | TIMESTAMP | 创建时间 | 自动 |

- 服务层：`models/school_types.py`（`get_school_types()` / `get_school_type_map()` / `get_school_type_name()` / `create_school_type()` / `update_school_type()` / `delete_school_type()` / `seed_school_types()`）
- 仅当表为空时从 `config.SCHOOL_TYPES` 种子化 1/2，避免把管理员主动删除的类别重新加回
- `students.school` 与 `users.school_visible` 仍存编码，不建外键；名称解析走服务层，字典异常时回退 `config.SCHOOL_TYPES`
- 停用语义：新增/编辑下拉只显示启用类别，历史数据保留原类别并标注「（已停用）」
- 删除保护：被 `students.school` 或 `users.school_visible` 引用的类别不允许删除
- 前端：`app.py` context_processor 注入 `school_types`；`base.html` 注入 `window.SCHOOL_TYPES` + `schoolName(code)`

### users（用户表）

| 字段 | 类型 | 业务含义 | 赋值规则 |
|---|---|---|---|
| id | INTEGER PK | 自增主键 | 自动 |
| username | TEXT UNIQUE | 登录用户名 | 创建时指定 |
| password_hash | TEXT | PBKDF2+HMAC+SHA256 哈希 | `werkzeug.security.generate_password_hash()` |
| role | TEXT | 角色 | `admin`/`user`/`attendance`/`finance_chief`/`finance_staff`/`insurance_officer` |
| display_name | TEXT | 显示名称 | 用于操作日志和UI显示 |
| school_visible | TEXT | 可见学校 | 学校类别编码逗号分隔（如 `"1,2"`）；空值或 `"*"` 表示不限制 |
| fields_visible | TEXT | 可见字段 | JSON数组如 `["学生姓名","学号"]` 或 `["*"]`，控制 `filter_student_data()` |
| grade_visible | TEXT | 可见年级 | `"*"` 或 `"2024,2025"`，控制 `check_grade_permission()` |
| is_active | INTEGER | 启用状态 | `1`=启用 / `0`=禁用 |
| created_at | TIMESTAMP | 创建时间 | 自动 |

### update_logs（更新日志表）

| 字段 | 类型 | 业务含义 | 赋值规则 |
|---|---|---|---|
| id | INTEGER PK | 自增主键 | 自动 |
| student_id | INTEGER | 学生ID | 关联 `students.id` |
| id_card | TEXT | 身份证号 | 冗余存储，方便跨表追溯 |
| field_name | TEXT | 变更字段名（`admin_class`/`attendance_status`/`remark` 等） | `FIELD_MAPPING` 中的英文 key |
| old_value | TEXT | 变更前值 | 可为空 |
| new_value | TEXT | 变更后值 | 可为空 |
| updated_by | TEXT | 操作人 `display_name` | session 取值 |
| batch_id | TEXT | 批次ID（批量操作用） | UUID前12位 |
| created_at | TIMESTAMP | 操作时间 | 自动 |

### attendance_records（出勤记录表）⚠️ 已迁移到身份证号

| 字段 | 类型 | 业务含义 | 赋值规则 |
|---|---|---|---|
| id | INTEGER PK | 自增主键 | 自动 |
| id_card | TEXT NOT NULL | 身份证号 ✅ 已从 student_no 迁移 | 关联 `students.id_card` |
| attendance_date | TEXT NOT NULL | 点名日期 `YYYY-MM-DD` | 前端传入 |
| status | TEXT DEFAULT '出勤' | `'出勤'` / `'缺勤'` | 手机端滑块开关状态 |
| update_time | TIMESTAMP | 更新时间 | 自动 |

- 唯一约束：`(id_card, attendance_date)` — 同一人同一天仅一条记录
- SQL：`INSERT ... ON CONFLICT(id_card, attendance_date) DO UPDATE SET status=excluded.status`

### export_templates（导出模板表）⭐新增

| 字段 | 类型 | 业务含义 |
|---|---|---|
| id | INTEGER PK | 自增主键 |
| name | TEXT NOT NULL | 模板文件名（原始上传文件名） |
| file_path | TEXT NOT NULL | `data/templates/xxx.xlsx` |
| file_type | TEXT NOT NULL | `xlsx` / `docx` |
| placeholders | TEXT NOT NULL | 解析出的 `${xxx}` 占位符列表（JSON数组） |
| created_at | TIMESTAMP | 创建时间 |
| updated_at | TIMESTAMP | 更新时间 |

### export_field_mappings（导出字段映射表）⭐新增

| 字段 | 类型 | 业务含义 |
|---|---|---|
| id | INTEGER PK | 自增主键 |
| template_id | INTEGER NOT NULL | FK→export_templates.id，ON DELETE CASCADE |
| scheme_name | TEXT | 方案名称（用户自定义） |
| mappings | TEXT NOT NULL | `{"${姓名}": "name", ...}` JSON |
| created_at | TIMESTAMP | 创建时间 |
| updated_at | TIMESTAMP | 更新时间 |

### export_history（导出历史表）⭐新增

| 字段 | 类型 | 业务含义 |
|---|---|---|
| id | INTEGER PK | 自增主键 |
| template_id | INTEGER | 使用的模板ID（模板删除后可保留） |
| template_name | TEXT | 模板名称（冗余存储） |
| scheme_name | TEXT | 使用的映射方案名称 |
| row_count | INTEGER | 导出的数据行数 |
| file_path | TEXT | 生成的导出文件路径 |
| status | TEXT | 固定 `completed` |
| created_by | TEXT | 操作人 display_name |
| created_at | TIMESTAMP | 导出时间 |

---

## 路由一览

> **鉴权列说明**：`🛡️JSON` = 使用 `_require_admin_json()`（返回JSON 401/403，绝不302重定向）；`🛡️admin装饰器` = 使用 `@auth.admin_required`（未登录302跳转）；`🛡️公开口令` = 命中 `blueprints/public.py` 的 blueprint 级口令校验（session / `?k=` / `X-Public-Key`）；空白 = 无鉴权

### 公共路由

| 路由 | 方法 | 鉴权 | 说明 | 前端交互 |
|---|---|---|---|---|
| `/` | GET | session检查 | 首页，根据 `session['role']` 跳转 | Flask `redirect()` |
| `/login` | GET/POST | 无 | 桌面端登录（60秒5次限流） | 表单POST + 失败计数 |
| `/logout` | GET | 无 | 清空session并302跳转 | `auth.clear_session()` |

### 公开公示路由（免登录）⭐新增

| 路由 | 方法 | 鉴权 | 说明 | 前端交互 |
|---|---|---|---|---|
| `/public/pending-leave` | GET | 🛡️公开口令 | 待销假公示页（最近一次点名缺勤名单，按行政班级聚合） | 侧边栏班级计数 + 学生卡片，localStorage 缓存 60s |
| `/public/roster` ⭐新增 | GET | 🛡️公开口令 | 班级花名册公示页（在册学生，按行政班级聚合） | 同上；卡片=姓名 + 学籍状态彩色徽章（文字明确「正式学籍」/「未注册」） |
| `/api/public/pending-leave` | GET | 🛡️公开口令 | 待销假数据接口 | `get_pending_leave_students()`，IP 限流 scope=`pending_leave` |
| `/api/public/roster` ⭐新增 | GET | 🛡️公开口令 | 花名册数据接口 | `get_public_roster()`，IP 限流 scope=`roster` |

> 两个公开页共用同一套口令校验；限流默认 30 次/60 秒且按 `scope` 隔离额度。完整访问地址形如 `http://<host>:5001/public/roster?k=<PUBLIC_ACCESS_KEY>`，控制台顶部「待销假查询」「班级花名册」按钮已带口令，点击后可直接从地址栏复制分发。

### 手机端出勤路由

| 路由 | 方法 | 鉴权 | 说明 | 前端交互 |
|---|---|---|---|---|
| `/mobile/login` | GET/POST | 无 | 手机端登录 | 独立紫色UI，表单POST |
| `/mobile/grades` | GET | 🛡️login装饰器 | 年级选择页 | 卡片列表，点击跳转班级 |
| `/mobile/grades/<grade>/classes` | GET | 🛡️login装饰器 | 班级列表 + 完成状态 | 绿色已完成/灰色待点名 |
| `/mobile/classes/<grade>/<cls>/attendance` | GET/POST | 🛡️attendance装饰器 | 点名页，滑动开关+批量提交 | `main.js` POST + `safeJSON()` |

### 管理员路由

| 路由 | 方法 | 鉴权 | 说明 | 前端交互 |
|---|---|---|---|---|
| `/admin/year-select` | GET | 🛡️admin装饰器 | 入学年份选择页 | 卡片列表点击 |
| `/admin/year-clear` | GET | 🛡️admin装饰器 | 清除session年份过滤 | 302跳转 |
| `/admin` | GET | 🛡️admin装饰器 | 控制台仪表盘 | Chart.js 图表 + 分布表 + 备份面板 |
| `/admin/students` | GET | 🛡️admin装饰器 | 学生列表（分页/筛选/弹窗/datalist） | 18列表格 + 筛选面板 + 新增编辑弹窗 |
| `/admin/students/add` | GET/POST | 🛡️admin装饰器 | 添加学生（独立表单页） | POST → Flash → 302 |
| `/admin/students/edit/<id>` | GET/POST | 🛡️admin装饰器 | 编辑学生 | POST → Flash → 302 |
| `/admin/students/delete/<id>` | POST | 🛡️admin装饰器 | 软删除（仅退学办结状态可删） | showConfirm → fetch POST |
| `/admin/students/clear` | POST | 🛡️admin装饰器 | 清空所有学生 | `showConfirm()` → POST |
| `/admin/students/export/photos` | GET | 🛡️admin装饰器 | 导出筛选学生照片 ZIP | URL参数继承筛选条件 |
| `/admin/students/export/excel` | GET | 🛡️admin装饰器 | 导出筛选学生 Excel | URL参数继承筛选条件 |
| `/admin/import` | GET/POST | 🛡️admin装饰器 | 批量导入（上传→预览→确认导入） | 两步流程：上传 → preview → confirm |
| `/admin/import/template` | GET | 🛡️admin装饰器 | 下载导入模板 Excel | `utils.create_import_template()` |
| `/admin/import/confirm` | POST | 🛡️JSON | 确认导入 | fetch → 返回progress_id → 轮询进度 |
| `/admin/import/progress/<id>` | GET | 🛡️JSON | 轮询导入进度 | EventSource SSE / fetch轮询 |
| `/admin/batch-update` | GET/POST | 🛡️admin装饰器 | 批量更新（上传→预览→确认更新） | 两步流程 |
| `/admin/batch-update/template` | GET | 🛡️admin装饰器 | 下载批量更新模板（含已有数据+出勤） | `utils.create_batch_update_template()` |
| `/admin/batch-update/confirm` | POST | 🛡️JSON | 确认更新 | fetch → progress_id → 轮询 |
| `/admin/batch-update/progress/<id>` | GET | 🛡️JSON | 轮询更新进度 | fetch 500ms 轮询 |
| `/admin/users` | GET/POST | 🛡️admin装饰器 | 用户管理 | 表格 + 新增弹窗 + 启用/禁用切换 |
| `/admin/dicts` | GET | 🛡️admin装饰器 | 字典管理页面 | datalist卡片 + 导入导出按钮 |
| `/admin/leave-cancel` | GET/POST | 🛡️admin装饰器 | 销假处理页 | 搜索表单 + 销假结果展示 |
| `/admin/leave-cancel/export-attendance` | GET | 🛡️admin装饰器 | 导出出勤明细对照表 Excel | `openpyxl` 透视表，带颜色 |
| `/admin/update-attendance-status` | POST | 🛡️admin装饰器 | 在校状态批量更新 | Flash → 302 |
| `/admin/attendance/cleanup` | POST | 🛡️admin装饰器 | 清理过期出勤记录 | `models.delete_attendance_before_date()` |
| `/admin/student-change` | GET | 🛡️admin装饰器 | 学籍异动处理页 + 转学待办表 + 实习处理面板 | 三个Tab面板 |
| `/admin/student-dropout` | GET | 🛡️admin装饰器 | 退学学生处理（分页+日期筛选+软删除） | 分页表格 + date filter + 导出按钮 |
| `/admin/student-dropout/export` | GET | 🛡️admin装饰器 | 导出退学学生 Excel（绿/黄着色） | `openpyxl` 带颜色 |
| `/admin/export-engine` | GET | 🛡️admin装饰器 | 模板导出引擎页面（含备份管理面板） | 3步骤：上传模板→映射→导出 |
| `/admin/graduation` | GET | 🛡️admin装饰器 | 毕业管理（毕业处理/撤销毕业/预览搜索） | 年级选择→班级/学生预览→确认处理 |
| `/admin/graduation/export` | GET | 🛡️admin装饰器 | 导出毕业学生列表 Excel | `openpyxl` |
| `/admin/graduation/query` | GET | 🛡️login装饰器 | 毕业生查询页 | 学校/年级/班级/姓名联动筛选 |

### 保险模块页面路由 ⭐新增

| 路由 | 方法 | 鉴权 | 说明 | 前端交互 |
|---|---|---|---|---|
| `/insurance/register` | GET | 🛡️finance装饰器 | 保险缴费登记页 | 学年树形数据 + 单条登记弹窗 + 批量登记 |
| `/insurance/approve` | GET | 🛡️finance_chief装饰器 | 保险审批页 | 学年树形数据 + 单条审批 + 批量审批 |
| `/insurance/todo` | GET | 🛡️insurance_officer装饰器 | 保险待办页 | 学年树形数据 + 批量办结 + 导出 |
| `/insurance/init` | GET/POST | 🛡️admin装饰器 | 保险初始化导入（上传Excel → 进度轮询） | daemon线程异步导入 |
| `/insurance/init/template` | GET | 🛡️admin装饰器 | 下载保险初始化模板（含已有数据） | 含现有学生数据的模板 |

### API 路由 — 学生/班级/字典

| 路由 | 方法 | 鉴权 | 说明 | 前端交互 |
|---|---|---|---|---|
| `/api/school/derive` | GET | 🛡️JSON | 从专业名推导学校类型（`1`/`2`/`0`） | fetch → `safeJSON()` |
| `/api/grades` | GET | 无 | 获取所有年级列表 | fetch 公共API |
| `/api/students/search` | GET | 无 | 学生模糊搜索 | fetch 异步搜索 |
| `/api/class-comparison` | GET | 无 | 班级对照表数据 | fetch Charts.js |
| `/api/class-comparison-detail` | GET | 无 | 班级对照表明细 | fetch |
| `/api/admin/student/<id>` | GET | 🛡️JSON | 获取单个学生完整JSON | 编辑弹窗初始化 |
| `/api/admin/student/add` | POST | 🛡️JSON | JSON新增学生（含拼音生成+学校推导） | fetch POST JSON |
| `/api/admin/student/<id>` | POST | 🛡️JSON | JSON更新学生（白名单字段过滤） | fetch POST JSON |
| `/api/read_id_card` | POST | 🛡️JSON | 身份证读卡（ctypes DLL） | fetch → 自动填充表单 |
| `/api/photos/<id_card>` | GET | 无 | 按身份证号返回照片文件 | `<img>` src |
| `/api/photos/export/zip` | GET | 🛡️admin装饰器 | 一键导出全部照片 ZIP | 直接浏览器下载 |
| `/api/students/check_id_card` | GET | 🛡️JSON | 身份证校验+查重 | fetch + 实时校验防抖 |
| `/api/students/search-quick` | GET | 🛡️JSON | 快速搜索（姓名/拼音/身份证后4位/精确） | fetch → 下拉列表 |
| `/api/students/class-options` | GET | 🛡️JSON | 获取所有行政班级选项 | fetch → 下拉框 |
| `/api/students/change-record` | POST | 🛡️JSON | 学籍异动（班级变更/专业变更/退学办结） | fetch → showConfirm → POST |
| `/api/transfer-pending/students` | GET | 🛡️JSON | 转学待办表数据（分页） | fetch → 表格 |
| `/api/students/check-student-no` | GET | 🛡️JSON | 学号重复校验（三条校验：非空→6位→未被占用） | fetch + 实时验证 |
| `/api/transfer-pending/process` | POST | 🛡️JSON | 处理转学（写入学号+注册状态→正式学籍） | fetch POST |
| `/api/dicts` | GET | 无 | 获取字典列表 | fetch → `<datalist>` |
| `/api/dicts/types` | GET | 无 | 获取字典类型列表 | fetch |
| `/api/dicts` | POST | 🛡️JSON | 添加字典项 | fetch POST |
| `/api/dicts/<id>` | DELETE | 🛡️JSON | 删除字典项 | fetch DELETE |
| `/api/dicts/batch` | POST | 🛡️JSON | Excel批量导入字典 | fetch + FormData |
| `/api/dicts/export` | GET | 🛡️admin装饰器 | 导出字典 Excel | 浏览器下载 |
| `/api/dicts/template` | GET | 🛡️admin装饰器 | 下载字典导入模板 | 浏览器下载 |
| `/api/school-types` | GET | 🛡️JSON | 获取学校类别列表（含停用项） | fetch → 学校类别页签/下拉 |
| `/api/school-types` | POST | 🛡️JSON | 新增/改名/停用启用学校类别（`action: create/update`） | fetch → 字典页 |
| `/api/school-types/<code>` | DELETE | 🛡️JSON | 删除学校类别（被学生或用户权限引用时拒绝） | fetch DELETE |
| `/api/enrollment-years` | GET | 🛡️JSON | 获取入学年份列表 | fetch |
| `/api/year/select` | POST | 🛡️JSON | 写入session的enrollment_year | fetch POST |
| `/api/year/create` | POST | 🛡️JSON | 创建新入学年份 | fetch POST |

### API 路由 — 销假/二维码

| 路由 | 方法 | 鉴权 | 说明 | 前端交互 |
|---|---|---|---|---|
| `/api/leave-cancel/search-absences` | GET | 🛡️JSON | 按学号/姓名搜索缺勤记录 | fetch → 缺勤列表渲染 |
| `/api/leave-cancel/search-realtime` | GET | 🛡️JSON | 实时搜索缺勤学生（`LEFT JOIN attendance_records`） | fetch 实时搜索 |
| `/api/leave-cancel/search` | GET | 🛡️JSON | 精确查找学生+当日出勤记录（多策略：ID→学号→姓名→拼音→身份证部分） | fetch |
| `/api/leave-cancel/confirm` | POST | 🛡️JSON | 确认销假 → `batch_upsert_attendance()` → `update_sync_timestamp()` | fetch → showToast |
| `/admin/api/mobile-qr` | GET | 🛡️admin装饰器 | 生成手机端QR码 PNG（`qrcode[pil]`） | `<img>` src |
| `/admin/api/mobile-url` | GET | 🛡️admin装饰器 | 获取手机端URL JSON | fetch |

### API 路由 — 软删除/回收站

| 路由 | 方法 | 鉴权 | 说明 | 前端交互 |
|---|---|---|---|---|
| `/api/admin/trash/students` | GET | 🛡️JSON | 回收站列表 | fetch → 弹窗列表 |
| `/api/admin/trash/restore/<id>` | POST | 🛡️JSON | 恢复单个（`is_deleted=0`） | showConfirm → fetch POST |
| `/api/admin/trash/delete/<id>` | POST | 🛡️JSON | 永久删除（`DELETE FROM students`） | showConfirm → fetch POST |
| `/api/admin/trash/restore-all` | POST | 🛡️JSON | 全部恢复 | showConfirm → fetch POST |
| `/api/admin/trash/delete-all` | POST | 🛡️JSON | 全部永久删除 | showConfirm → fetch POST |

### API 路由 — 毕业管理 ⭐新增

| 路由 | 方法 | 鉴权 | 说明 | 前端交互 |
|---|---|---|---|---|
| `/api/admin/graduation/grades` | GET | 🛡️admin装饰器 | 获取可毕业年级列表 | fetch → 下拉框 |
| `/api/admin/graduation/preview/<grade>` | GET | 🛡️admin装饰器 | 预览待毕业学生（`?admin_class=`筛选） | fetch → 列表+全选checkbox |
| `/api/admin/graduation/classes/<grade>` | GET | 🛡️admin装饰器 | 获取可毕业班级列表 | fetch |
| `/api/admin/graduation/process` | POST | 🛡️admin装饰器 | 批量毕业（`BEGIN IMMEDIATE` 事务，≤500人，备注备份） | showConfirm → fetch POST |
| `/api/admin/graduation/undo` | POST | 🛡️admin装饰器 | 批量撤销毕业（正则从备注解析恢复） | showConfirm → fetch POST |
| `/api/admin/graduation/filters` | GET | 🛡️login装饰器 | 毕业生筛选联动（学校→年级→班级级联） | fetch → 级联下拉 |
| `/api/admin/graduation/query` | GET | 🛡️login装饰器 | 毕业生查询（含权限过滤+字段脱敏，≤500条） | fetch → 动态列渲染 |

### API 路由 — 实习处理 ⭐新增

| 路由 | 方法 | 鉴权 | 说明 | 前端交互 |
|---|---|---|---|---|
| `/api/admin/internship/grades` | GET | 🛡️admin装饰器 | 获取可实习处理年级 | fetch → 下拉框 |
| `/api/admin/internship/preview/<grade>` | GET | 🛡️admin装饰器 | 预览待实习学生（排除已处理） | fetch → 列表 |
| `/api/admin/internship/classes/<grade>` | GET | 🛡️admin装饰器 | 获取可实习班级列表 | fetch |
| `/api/admin/internship/process` | POST | 🛡️admin装饰器 | 批量实习处理（事务，备注备份 `[实习处理：原始在校状态=XXX]`） | showConfirm → fetch POST |
| `/api/admin/internship/undo` | POST | 🛡️admin装饰器 | 批量撤销实习（正则从备注解析恢复） | showConfirm → fetch POST |
| `/api/admin/internship/processed/grades` | GET | 🛡️admin装饰器 | 获取已处理年级列表 | fetch |
| `/api/admin/internship/processed/<grade>` | GET | 🛡️admin装饰器 | 获取已处理学生列表 | fetch |

### API 路由 — 保险模块 ⭐新增

| 路由 | 方法 | 鉴权 | 说明 | 前端交互 |
|---|---|---|---|---|
| `/api/insurance/query` | GET | 🛡️login装饰器 | 保险综合查询（多维度筛选） | fetch |
| `/api/insurance/students` | GET | 🛡️finance装饰器 | 分页查询学生列表 | fetch → 分页表格 |
| `/api/insurance/register-tree` | GET | 🛡️finance装饰器 | 缴费登记页学年树形结构 | fetch → 树形导航 |
| `/api/insurance/register-single` | POST | 🛡️finance装饰器 | 单条登记（仅更新有值字段） | fetch POST |
| `/api/insurance/register-batch` | POST | 🛡️finance装饰器 | 批量登记 | fetch POST |
| `/api/insurance/approve-tree` | GET | 🛡️finance_chief装饰器 | 审批页树形 | fetch → 树形导航 |
| `/api/insurance/approve-single` | POST | 🛡️finance_chief装饰器 | 单条审批（修改 `supervisor_flag`） | showConfirm → fetch POST |
| `/api/insurance/approve-summary` | GET | 🛡️finance_chief装饰器 | 审批汇总统计 | fetch → 统计卡片 |
| `/api/insurance/approve` | POST | 🛡️finance_chief装饰器 | 批量审批/否决 | showConfirm → fetch POST |
| `/api/insurance/todo/export` | GET | 🛡️insurance_officer装饰器 | 待办导出 Excel | 浏览器下载 |
| `/api/insurance/todo-tree` | GET | 🛡️insurance_officer装饰器 | 待办页树形 | fetch → 树形导航 |
| `/api/insurance/complete` | POST | 🛡️insurance_officer装饰器 | 批量办结（自动填入 `declaration_date` + `insurance_update_date`） | showConfirm → fetch POST |
| `/api/insurance/init-progress/<id>` | GET | 🛡️admin装饰器 | 初始化导入进度轮询 | fetch 轮询 → 进度条 |

### API 路由 — 模板导出引擎 ⭐新增

| 路由 | 方法 | 鉴权 | 说明 | 前端交互 |
|---|---|---|---|---|
| `/api/export/upload-template` | POST | 🛡️JSON | 上传模板（xlsx/docx），解析 `${xxx}` 占位符 | fetch + FormData |
| `/api/export/templates` | GET | 🛡️JSON | 获取所有模板列表 | fetch → 卡片列表 |
| `/api/export/template/<id>` | DELETE | 🛡️JSON | 删除模板及其关联映射+导出记录 | showConfirm → fetch DELETE |
| `/api/export/available-fields` | GET | 🛡️JSON | 获取数据源字段白名单（含记录总数+样本值） | fetch → 字段映射面板 |
| `/api/export/save-mapping` | POST | 🛡️JSON | 保存字段映射方案 `{scheme_name, mappings}` | fetch POST |
| `/api/export/mappings/<template_id>` | GET | 🛡️JSON | 获取模板的所有映射方案 | fetch → 方案列表 |
| `/api/export/mapping/<id>` | DELETE | 🛡️JSON | 删除映射方案 | fetch DELETE |
| `/api/export/update-filter` | POST | 🛡️JSON | 更新导出筛选条件（存入session） | fetch POST |
| `/api/export/preview` | POST | 🛡️JSON | 预览填充结果（前5条） | fetch → 预览弹窗 |
| `/api/export/execute` | POST | 🛡️JSON | 同步导出（<10000行），保存导出历史 | fetch → 下载 |
| `/api/export/execute-async` | POST | 🛡️JSON | 异步导出（当前501未实现） | fetch POST |
| `/api/export/task-status/<task_id>` | GET | 无 | 查询异步任务进度 | fetch |
| `/api/export/history` | GET/DELETE | 🛡️JSON | 导出历史查询/清空 | fetch → 历史表格 |

### 普通用户路由

| 路由 | 方法 | 鉴权 | 说明 | 前端交互 |
|---|---|---|---|---|
| `/user/query` | GET | 🛡️login装饰器 | 多维查询 | fetch + 表格 + 打印 |
| `/user/stats` | GET | 🛡️login装饰器 | 统计面板 | Chart.js |

---

## 权限控制（auth.py）

| 装饰器/函数 | 位置 | 签名 | 说明 |
|---|---|---|---|
| `login_required` | auth.py:6 | `login_required(f)` | 要求登录，否则 redirect `/login` |
| `admin_required` | auth.py:15 | `admin_required(f)` | 要求 `role=='admin'`，否则 302 |
| `attendance_required` | auth.py:27 | `attendance_required(f)` | 要求 `admin` 或 `attendance` 角色 |
| `finance_required` ⭐ | auth.py:39 | `finance_required(f)` | 要求 `admin/finance_chief/finance_staff` |
| `finance_chief_required` ⭐ | auth.py:51 | `finance_chief_required(f)` | 要求 `admin/finance_chief` |
| `insurance_officer_required` ⭐ | auth.py:63 | `insurance_officer_required(f)` | 要求 `admin/insurance_officer` |
| `check_field_permission(field)` | auth.py:108 | `check_field_permission(field) → bool` | 检查字段在 `fields_visible` 中 |
| `check_school_permission(type)` | auth.py:126 | `check_school_permission(type) → bool` | 检查学校类型在 `school_visible` 中 |
| `check_grade_permission(grade)` | auth.py:136 | `check_grade_permission(grade) → bool` | 检查年级在 `grade_visible` 中 |
| `apply_permission_filters()` | auth.py:186 | `apply_permission_filters(filters={}) → dict` | 返回 `{school, grade}` 过滤条件；仅 `admin` 直接返回原 filters，**无保险角色豁免分支**（以实际代码为准） |
| `create_session(user)` | auth.py:76 | `create_session(user: Row) → None` | 写入 session（`user_id/username/role/display_name/enrollment_year` 等），兼容6种角色登录跳转 |
| `filter_student_data(data)` | auth.py:192 | `filter_student_data(data: dict) → dict` | 根据 `fields_visible` 白名单过滤字段，未包含的字段返回 `***` |
| `mask_sensitive_data(value, field)` | auth.py:213 | `mask_sensitive_data(value, field) → str` | 非admin角色身份证中间4位置换为 `****`，手机号中间4位置换 |
| `get_visible_fields()` | auth.py:175 | `get_visible_fields() → list` | 从session解析 `fields_visible` |
| `_require_admin_json()` | app.py:376 | `_require_admin_json() → Response|None` | JSON 401/403，绝不302重定向 |
| `_require_login_json()` | app.py:399 | `_require_login_json() → Response|None` | JSON 401 未登录 |
| `_is_insurance_role()` | auth.py:148 | `_is_insurance_role() → bool` | 保险角色（finance_chief/staff/officer）返回True |

---

## 安全防护体系 ⭐更新

### 暴力破解防护
- **位置**：`app.py:291-333` `POST /login`
- 登录失败记录存储于全局 `login_attempts` 字典（`{ip: [(timestamp, ...)]}`）
- 60秒滑动窗口内最多5次尝试，超出后返回 `429 Too Many Requests`
- 成功登录后清除该IP记录

### CSRF 防护
- **位置**：`app.py:46-84` `_csrf_protect()`
- Token生成：`secrets.token_hex(32)`，每次请求更新
- 豁免：`/login`、`/mobile/login`、GET/HEAD/OPTIONS 请求
- 前端注入：`main.js` 在所有 fetch 和表单中自动附加 `X-CSRF-Token` header
- 校验失败返回 403 `CSRF token 验证失败`

### 安全响应头
- **位置**：`app.py:128-146` `@app.after_request`
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: SAMEORIGIN`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Content-Security-Policy`：限制 `default-src 'self'`，`script-src/style-src` 含 `https://cdn.jsdelivr.net`，`img-src 'self' data: blob:`

### SQL 注入防护
- **位置**：`models.py` 全文件
- 全程参数化查询（`cursor.execute(query, params)`）
- LIKE 搜索通配符转义：`escape_like_pattern()` 将 `%`→`\%`，`_`→`\_`
- 动态 `IN` 子句：`','.join(['?' for _ in list])` 安全拼接占位符，参数化传入

### 输入验证
| 验证项 | 位置 | 规则 |
|---|---|---|
| 身份证 | `utils.py:92` `validate_id_card_detailed()` | 7层：空值→18位→前17数字→末位X/数字→地区码→出生日期→校验位加权 |
| 手机号 | `utils.py:181` `validate_phone()` | `^1\d{10}$` + 200+号段库白名单 |
| 文件上传 | `utils.py:208` `allowed_file()` | 扩展名 `{xlsx, xls, csv}` + `config.MAX_CONTENT_LENGTH=10MB` |
| 年龄 | `utils.py:703` `parse_filters_from_request()` | `0 ≤ min_age ≤ 120`，`0 ≤ max_age ≤ 120` |
| 学号 | `app.py:2525` `/api/transfer-pending/process` | `^\d{6}$` |
| 年份 | `app.py:484` | `2000 ≤ year ≤ 2100` |

### 会话安全
- **位置**：`config.py:187-190` `Config` 类
- `SESSION_COOKIE_HTTPONLY=True`（防XSS读取Cookie）
- `SESSION_COOKIE_SECURE=False`（当前HTTP部署，HTTPS后应改为True）
- `PERMANENT_SESSION_LIFETIME=7200s`（2小时超时）

### 公开页面口令校验（免登录公示页）⭐新增
- **位置**：`blueprints/public.py:64-81` `@bp.before_request _require_public_access()`
- **覆盖范围**：`/public/*` 与 `/api/public/*` 全部路由（待销假公示页 + 班级花名册）
- **口令来源**：`config.PUBLIC_ACCESS_KEY`，加载优先级 环境变量 `PUBLIC_ACCESS_KEY` > `data/public_access.key` > 自动生成 `secrets.token_urlsafe(24)` 并持久化（`config.py:85-113`，与 `SECRET_KEY` 同款降级策略）
- **通过条件（满足其一即放行）**：
  1. `session['public_access_ok']` 已置位（首次校验通过后写入，随会话滑动续期）
  2. URL 参数 `?k=<口令>`（首次分发链接用这种方式）
  3. 请求头 `X-Public-Key: <口令>`（供程序化访问）
- **安全细节**：口令比较使用 `secrets.compare_digest()` 防时序攻击；`?k=` 通过后写入 session，前端同源 `fetch()` 自动携带 Cookie，**前端代码无需带参**
- **失败响应**：页面请求返回 403 极简提示页（`_ACCESS_DENIED_HTML`）；API 请求返回 403 JSON
- **限流隔离**：`_check_public_rate_limit(ip, scope)` 用 `scope` 区分不同公开页（`pending_leave` / `roster`），额度互不挤占；默认 30 次/60 秒，条目超 200 时仅清理已过期项
- ⚠️ **变更口令即失效**：改环境变量或删除 `data/public_access.key` 后，所有已分发链接立即失效，需重新分发；反之持久化保证重启后链接仍有效

---

## 关键设计模式

### 1. Flash 消息 → Toast 通知
- **位置**：`templates/base.html:底部script块` → `static/js/main.js:showToast()`
- **逻辑**：后端 `flash('消息', 'success')` → `base.html` 将 `get_flashed_messages()` 序列化为 JSON 存入 `window.__FLASH__` → `main.js` 读取并逐条调用 `showToast()` 渲染Bootstrap Toast
- **通知类型**：`success`（绿色）、`danger`（红色）、`warning`（黄色）、`info`（蓝色）

### 2. 确认弹窗
- **位置**：`static/js/main.js:showConfirm()`
- **签名**：`showConfirm(message, type='warning') → Promise<boolean>`
- **逻辑**：动态创建Bootstrap Modal，用户点击"确认"resolve true，"取消"resolve false，支持键盘ESC取消

### 3. 异步导入/更新
- **位置**：`app.py:941-1085`（导入）、`app.py:1214-1438`（批量更新）
- **逻辑**：上传文件 → 解析验证 → 前端通过 `fetch` 调用 `confirm` 端点 → 后端启动 `threading.Thread(daemon=True)` → 返回 `progress_id` → 前端500ms轮询 `progress/<id>` → 返回进度JSON（`{current, total, success, error, status}`）
- **线程内每50条 `conn.commit()` 一次**

### 4. 打印系统
- **位置**：`static/css/style.css:@media print`
- **逻辑**：`data-print-id` 属性精确控制打印区域，`@media print` 隐藏导航/按钮等非必要元素

### 5. 控制台图表布局
- **位置**：`templates/admin/dashboard.html`
- **逻辑**：Chart.js + chartjs-plugin-datalabels CDN，左侧年级分布+专业分布表格，右侧出勤率柱形图+性别饼图+学籍状态饼图+在校状态饼图
- **数据源**：`models.get_stats(filters)` 返回 `{grade_distribution, major_distribution, gender_stats, status_stats, attendance_rate, ...}`

### 6. 手机端出勤点名 ⚠️ 已迁移到身份证号
- **位置**：`templates/mobile/attendance.html` → `app.py` `/mobile/classes/.../attendance`
- **逻辑**：独立UI渐变紫色背景，每行学生有滑动开关（`id_card` + `status`），批量提交调用 `models.batch_upsert_attendance(records)` 执行 `BEGIN IMMEDIATE` → `INSERT ... ON CONFLICT(id_card, attendance_date) DO UPDATE SET status=excluded.status` → `COMMIT`
- **唯一约束**：`(id_card, attendance_date)`

### 7. 销假处理
- **位置**：`app.py:1534-1815` → `templates/admin/leave_cancel.html`
- **逻辑**：搜索（学号/身份证/姓名/拼音/身份证部分匹配）→ 缺勤列表渲染（淡红底高亮）→ 一键销假 `models.batch_upsert_attendance([{id_card, attendance_date, status:'出勤'}])` → `models.update_sync_timestamp()`
- **5个API端点**：`search-absences` / `search-realtime` / `search` / `confirm` / `export-attendance`

### 8. 手机端二维码生成
- **位置**：`app.py` `/admin/api/mobile-qr` + `/admin/api/mobile-url`
- **逻辑**：`qrcode[pil]>=7.4` 生成 PNG，URL 通过 `utils.get_lan_ip()` 三层检测回退（socket.connect → hostname解析 → '127.0.0.1'）

### 9. 导出出勤明细对照表
- **位置**：`app.py` `/admin/leave-cancel/export-attendance`
- **逻辑**：`models.get_attendance_pivot_data(start_date, end_date)` 透视查询返回 `{dates: [], students: [], records: {id_card: {date: status}}}` → `openpyxl` 固定列（学号/姓名/身份证/校别/入学年）+日期列透视 → 状态着色（出勤翠绿、缺勤淡红）

### 10. 更新在校状态强化
- **位置**：`models.py:2607` `update_attendance_status_from_latest_attendance()`
- **逻辑**：遍历当天出勤记录，**仅** `attendance_status IN ('在校', '待销假')` 时写入最新状态（出勤→在校，缺勤→待销假），跳过托管/非在校/退学办结
- **返回值**：`{date, updated, present, absent, skipped, total_skipped, message}`

### 11. 按入学年份分组
- **位置**：`app.py:492-550` 年份选择相关路由 + `auth.py:76` session初始化
- **逻辑**：`enrollment_year` 与 `grade` 独立字段，session中存 `enrollment_year`，admin通过年份选择页切换，非admin不可见，`_exclude_graduated_clause()` 等共享过滤函数统一排除毕业学生

### 12. 批量更新自动新增（Upsert）
- **位置**：`app.py:1214-1438` + `utils.py:512` `validate_batch_update_data()`
- **逻辑**：四态判定：`pending`（匹配成功有待更新字段）、`no_change`（匹配但无变更）、`new_record`（未匹配自动新增）、`not_found`（未匹配且非新增）
- 后台线程中使用 `models.create_student_with_conn(conn, data)` 创建新记录

### 13. JSON API 鉴权模式（防 302 重定向泄漏）
- **位置**：`app.py:376-415`
- **逻辑**：`_require_admin_json()` 检查session → 返回 JSON `{error, code: 401/403}` → 前端 `safeJSON(response)` 检测 `Content-Type: application/json`，非JSON直接报错不解析，防止HTML泄漏到 `response.json()`

### 14. 数据库备份服务
- **位置**：`services/backup_service.py` `BackupService` 类
- **逻辑**：AB轮换（`student_A.db` ↔ `student_B.db`）→ SQLite原生 `conn.backup()` API → 失败回退 `shutil.copy2()`（含WAL/SHM文件复制）→ `PRAGMA integrity_check` 完整性验证 → `backup_log.txt` 日志记录
- **定时备份**：`threading.Timer`，间隔可动态调整（`set_interval(seconds)`，60~86400秒）
- **倒计时API**：`get_countdown_info()` 返回 `{remaining_seconds, percent, next_backup_at, status}`
- **日志清理**：每10次备份清理一次，保留最近500条且30天内

### 15. 中文姓名→拼音自动生成
- **位置**：`pinyin_utils.py:21` `name_to_pinyin(name)` → 5处触发点
- **5处触发点**：
  1. `app.py:748` `POST /admin/students/add`（弹窗新增）
  2. `app.py:763` `POST /admin/student_form.html`（表单新增）
  3. `app.py:1012` `do_import()` 中的导入
  4. `app.py:1303` `do_batch_update()` 中的自动新增
  5. `app.py:801` `POST /admin/students/edit/<id>`（编辑保存）
- **算法**：26671汉字→拼音映射+27个复姓优先匹配（如单于→ShanYu, 欧阳→OuYang）+49个异体字/繁体转换
- **存量回填**：`backfill_pinyin.py --dry-run`（预览）/ `--force`（强制重写）

### 16. 毕业管理
- **位置**：`models.py:1238` `process_graduation()` / `models.py:1292` `undo_graduation()`
- **处理逻辑**：`BEGIN IMMEDIATE` 事务 → `UPDATE students SET registration_status='毕业', attendance_status='非在校', remark=old_remark+' [毕业处理前：学籍=X，在校=Y]'` → 事务内直接写 `update_logs`
- **撤销逻辑**：`re.search(r'\[毕业处理前：学籍=(.+?)，在校=(.+?)\]', remark)` 解析备份 → 恢复原状态 → 删除备份标记
- **共享过滤**：`models.py:12` `_exclude_graduated_clause(alias)` 返回 `AND {alias}.registration_status != '毕业'`，已应用到20+查询函数中

### 17. 实习处理
- **位置**：`models.py:1416` `process_internship()` / `models.py:1468` `undo_internship()`
- **处理逻辑**：`UPDATE students SET attendance_status='非在校', remark=old_remark+' [实习处理：原始在校状态=XXX]'`
- **撤销逻辑**：`re.search(r'\[实习处理：原始在校状态=(.+?)\]', remark)` 解析恢复
- **排除条件**：`remark NOT LIKE '%[实习处理：%'`

### 18. 保险模块
- **位置**：`app.py:4140-4624`（页面路由）+ `app.py:2798-4795`（API路由）
- **4阶段业务流程**：初始化导入 → 缴费登记 → 审批 → 办结
- **仅更新有值字段**：保险update逻辑中仅当新值非空时才写入，不覆盖已有空值
- **`declaration_date` + `insurance_update_date`**：办结时自动填入 `datetime.now().strftime('%Y-%m-%d')`
- **保险角色全量视图**：`auth.py:148` `_is_insurance_role()` 使 `apply_permission_filters()` 返回空dict

### 19. 模板驱动数据导出引擎
- **位置**：`app.py:4921-5282` + `templates/admin/export_engine.html`
- **流程**：上传模板(`.xlsx/.docx`) → 解析 `${xxx}` 占位符 → 配置字段映射方案 → 预览前5条 → 执行导出
- **智能匹配**：占位符名称与 `FIELD_MAPPING` 键名模糊匹配自动建议
- **字段白名单**：仅允许 `config.ALL_STUDENT_FIELDS` 中定义的字段（40个），防止占位符注入
- **数据源**：从 `students` 表读取，复用 `get_students()` + 筛选条件
- **上限**：单次导出 ≤ `config.EXPORT_MAX_ROWS=10000`

### 20. 退学学生处理
- **位置**：`models.py:1014` `get_dropout_students()` + `templates/admin/student_dropout.html`
- **逻辑**：仅显示 `attendance_status='退学办结'` 学生，子查询提取 `dropout_date`（从 `update_logs` 的 `attendance_status→退学办结` 时间）和 `process_date`（`is_deleted→1` 时间）
- **Excel导出**：待处理行黄色底，已处理行绿色底
- **筛选**：支持 `date_from` / `date_to` 日期范围

### 21. 转学待办
- **位置**：`models.py:1582` `get_transfer_pending_students()` + `templates/admin/student_change.html`
- **查询条件**：`admission_type='转学生' AND registration_status='未注册' AND is_deleted=0`
- **学号校验**：3段校验 — ① 前端JS实时 `input` 事件 `^\d{6}$` → ② 提交时 `showConfirm()` 前置拦截 → ③ 后端 `app.py:2525` `re.match(r'^\d{6}$')` + 重复校验

---

## config.py 关键配置

| 配置 | 值 | 说明 | 代码位置 |
|---|---|---|---|
| PORT | 5001 | 运行端口（环境变量 `STUDENT_PORT` > `config.txt`） | config.py:13-47 |
| SECRET_KEY | 环境变量或随机生成 | Flask 密钥，`secrets.token_hex(32)` | config.py:56 |
| PUBLIC_ACCESS_KEY ⭐新增 | 环境变量或随机生成（持久化于 `data/public_access.key`） | 公开公示页访问口令，`secrets.token_urlsafe(24)`，链接形如 `?k=<口令>` | config.py:85-113 |
| DATABASE_PATH | `data/student.db` | SQLite 数据库路径 | config.py:58 |
| UPLOAD_FOLDER | `uploads/` | 上传临时目录 | config.py:63 |
| LOG_FOLDER | `logs/` | 日志目录 | config.py:64 |
| MAX_CONTENT_LENGTH | 10MB | 最大上传大小 | config.py:66 |
| ALLOWED_EXTENSIONS | `{xlsx, xls, csv}` | 允许的文件类型 | config.py:73 |
| PERMANENT_SESSION_LIFETIME | 7200s（2小时） | 会话有效期 | config.py:75 |
| SCHOOL_TYPES | `{1:'中职', 2:'技工'}` | 学校类型 | config.py:77-79 |
| REGISTRATION_STATUS | `['正式学籍', '未注册']` | 学籍状态枚举 | config.py:82 |
| ATTENDANCE_STATUS | `['在校','非在校','托管','待销假','退学办结']` | 在校状态枚举 | config.py:84 |
| TUITION_STATUS | `['已缴清','部分欠费','未缴','免学费']` | 学费状态枚举 | config.py:145 |
| INSURANCE_SUPERVISOR_FLAGS | `['按计划办理学平险', '不参保']` | 财务主管可选的审批标记 | config.py:147 |
| FIELD_MAPPING | `{中文名: db字段}` | 40字段映射表 | config.py:100-141 |

### 备份配置

| 配置 | 值 | 说明 |
|---|---|---|
| BACKUP_ENABLED | True | 启用备份功能 |
| BACKUP_ON_STARTUP | True | 启动时立即执行一次备份 |
| BACKUP_AUTO_ENABLED | True | 启用定时备份 |
| BACKUP_AUTO_INTERVAL | 1800s（30分钟） | 定时备份间隔 |

### 模板导出引擎配置

| 配置 | 值 | 说明 |
|---|---|---|
| TEMPLATE_FOLDER | `data/templates/` | 模板文件存储 |
| EXPORT_OUTPUT_FOLDER | `data/exports/` | 导出结果存储 |
| TEMPLATE_ALLOWED_EXTENSIONS | `{xlsx, xls, docx}` | 允许的模板格式 |
| EXPORT_MAX_ROWS | 10000 | 单次导出上限 |
| EXPORT_TEMP_FILE_TTL_SECONDS | 3600 | 临时文件清理周期 |

---

## models.py 关键函数

| 函数 | 签名 | 返回值 | 说明 |
|---|---|---|---|
| `_exclude_graduated_clause(alias)` | `alias: str or None → str` | `AND {alias}.registration_status != '毕业'` | 共享过滤子句 |
| `_non_graduated_where(alias)` | `alias: str or None → str` | 完整 WHERE 子句 | `is_deleted=0 AND registration_status!='毕业'` |
| `get_db()` | `→ sqlite3.Connection` | Row factory 连接 | WAL+PRAGMA优化 |
| `init_db()` | `→ None` | 无 | 建表/索引/ALTER安全迁移 |
| `get_students(filters, page, per_page)` | `filters: dict, page=1, per_page=20, sort_field='id', sort_order='DESC'` | `{students, total, page, pages}` | 多条件分页（含年龄/照片/搜索/LIKE转义） |
| `create_student(data)` | `data: dict → int` | 新 student id | 三唯一校验（学号/国网学籍号/身份证号） |
| `create_student_with_conn(conn, data)` | `conn, data → int` | 新 student id | 复用连接版，用于批量更新自动新增 |
| `update_student(id, data)` | `id: int, data: dict → bool` | 是否成功 | 白名单字段过滤 |
| `soft_delete_student(id, updated_by)` | `id, updated_by='system' → bool` | 是否成功 | 仅退学办结状态可删 |
| `get_student_by_id_card(id_card)` | `id_card → dict or None` | 学生dict | 精确匹配 |
| `get_student_by_student_no(student_no)` | `student_no → dict or None` | 学生dict | 精确匹配 |
| `get_public_roster()` ⭐新增 | `→ {classes, students, total}` | 公开花名册数据 | 仅3字段（姓名/学籍状态/行政班级）；排除毕业+退学办结+软删除+未分班 |
| `get_pending_leave_students()` | `→ {classes, students, date}` | 公开待销假数据 | 最近一次点名缺勤名单，仅4字段 |
| `get_stats(filters)` | `filters: dict → dict` | `{total, grade_distribution, major_distribution, gender_stats, ...}` | 控制台统计 |
| `get_filter_values()` | `→ dict` | `{schools, grades, majors, classes, ...}` | 筛选下拉选项 |
| `get_dropout_students(page, per_page, date_from, date_to)` | `→ pagination dict` | 含 `pending_total/processed_total` | 退学学生查询 |
| `get_graduated_students(page, per_page, grade)` | `→ pagination dict` | 分页 | 毕业学生列表 |
| `get_graduation_eligible_students(grade, admin_class=None)` | `→ list[dict]` | 学生列表 | 可毕业处理 |
| `process_graduation(student_ids, operator)` | `→ (processed: int, details: list)` | 处理结果 | 事务+备注备份 |
| `undo_graduation(student_ids, operator)` | `→ (processed, failed, details)` | 撤销结果 | 正则解析备注 |
| `get_internship_eligible_students(grade, admin_class=None)` | `→ list[dict]` | 排除已处理 | 可实习处理 |
| `process_internship(student_ids, operator)` | `→ (processed, details)` | 处理结果 | 事务+备注备份 |
| `undo_internship(student_ids, operator)` | `→ (processed, failed, details)` | 撤销结果 | 正则解析备注 |
| `get_transfer_pending_students(page=1, per_page=10)` | `→ pagination dict` | 转学待办 | 分页 |
| `get_attendance_pivot_data(start_date, end_date)` | `→ {dates, students, records}` | 透视数据 | 出勤导出 |
| `update_attendance_status_from_latest_attendance()` | `→ dict` | `{date, updated, present, absent, skipped}` | 仅覆盖在校/待销假 |
| `get_student_latest_absence(id_card)` | `id_card → dict or None` | 最近缺勤 | 销假用 |
| `create_user(username, password_hash, role, ...)` | `→ user_id` | 新用户ID | 6角色支持 |
| `update_user(user_id, **kwargs)` | `→ bool` | 是否成功 | 动态关键字 |
| `create_template(name, file_path, file_type, placeholders)` | `→ template_id` | 新模板ID | 导出引擎 |
| `get_all_templates()` | `→ list[dict]` | 模板列表 | 导出引擎 |
| `delete_template(id)` | `→ bool` | 级联删除 | FK ON DELETE CASCADE |
| `save_field_mapping(template_id, scheme_name, mappings)` | `→ mapping_id` | 新映射ID | 导出引擎 |
| `add_export_history(template_id, template_name, scheme_name, row_count, file_path, created_by)` | `→ None` | 无 | 导出历史 |

---

## 工具函数（utils.py）

| 函数 | 签名 | 返回值 | 说明 |
|---|---|---|---|
| `extract_birth_date_from_id_card(id_card)` | `id_card: str → str or None` | `'YYYY-MM-DD'` 或 None | 从身份证7-14位提取出生日期并格式化 |
| `calculate_age(birth_date)` | `birth_date: str or date → str or None` | `'X岁X个月'` 或 None | 计算精确年龄（年月） |
| `validate_id_card(id_card)` | `id_card: str → bool` | True/False | 快捷校验（18位+校验位） |
| `validate_id_card_detailed(id_card)` | `id_card: str → dict` | `{valid, error_code, message, birth_date, gender, region}` | 7层详细校验，返回结构化结果 |
| `validate_phone(phone)` | `phone: str → bool` | True/False | 格式（`1\d{10}`）+ 号段库（200+）白名单 |
| `generate_random_password(length=12)` | `length: int → str` | 随机密码 | 字母+数字+特殊字符 |
| `allowed_file(filename)` | `filename: str → bool` | True/False | 扩展名在 `ALLOWED_EXTENSIONS` 中 |
| `generate_unique_filename(original_filename)` | `filename: str → str` | `'YYYYmmdd_HHMMSS_random8.ext'` | 时间戳+随机串防重名 |
| `create_import_template()` | `→ openpyxl.Workbook` | Workbook对象 | 29列导入模板含2条示例数据 |
| `create_batch_update_template(students, attendance_date)` | `students: list, attendance_date: str → Workbook` | Workbook对象 | 29列+今日出勤含已有数据 |
| `parse_import_file(file_path)` | `file_path: str → (list[dict], error: str or None)` | 数据+错误 | 读取Excel第一行作为headers |
| `parse_batch_update_file(file_path)` | `file_path: str → (list[dict], error)` | 数据+错误 | 同上 |
| `validate_import_data(data)` | `data: list[dict] → list[dict]` | 校验结果列表 | 每行含 `{valid, errors, data}` |
| `validate_batch_update_data(data, attendance_date)` | `→ list[dict]` | 四态判定结果 | `status: pending/no_change/new_record/not_found` |
| `export_students_to_excel(students, fields)` | `→ openpyxl.Workbook` | Excel对象 | 含格式样式 |
| `parse_filters_from_request(request)` | `request: Flask request → dict` | `{school, grade, major, admin_class, ...}` | 标准filters字典 |
| `get_lan_ip()` | `→ str` | IP地址 | 三层回退（DNS→socket→127.0.0.1），结果缓存 |

### 拼音工具（pinyin_utils.py）

| 函数 | 签名 | 返回值 | 说明 |
|---|---|---|---|
| `name_to_pinyin(name)` | `name: str → str` | 如 `'张三丰' → 'ZhangSanFeng'` | 复姓优先匹配（27个）+26671字映射+49异体字转换 |
| `batch_name_to_pinyin(names)` | `names: list[str] → list[str]` | 拼音列表 | 批量转换 |
| `_pinyin_for_char(char)` | `char: str → str or None` | 拼音首字母大写 | 单字查字典 |
| `_normalize_name(name)` | `name: str → str` | 标准化的姓名 | 去除空格+保留中文 |

---

## 业务规则 ⭐新增

### 保险模块 supervisor_flag 状态机

```
初始化导入（admin）
  └→ supervisor_flag = ''（空，待定）
       ├→ 财务主管审批："按计划办理学平险"
       │     └→ 保险员办结 → supervisor_flag = '{year}学平险办结'
       ├→ 财务主管审批："不参保"
       │     └→ 不再进入待办流程
       └→ 未审批 → 保持 『空』
```

**规则**：
- `supervisor_flag` 初始值由管理员Excel导入时写入，可选 `''` / `'按计划办理学平险'` / `'不参保'`
- 财务主管审批页面仅显示 `supervisor_flag IN ('', '按计划办理学平险', '不参保')` 的记录
- 办结时自动设置 `supervisor_flag = f'{当前年份}学平险办结'`（学平险）或 `f'{当前年份}实习就业险办结'`（实习险）
- `declaration_date` 和 `insurance_update_date` 在办结时自动填入当天日期

### 毕业管理状态流转

```
毕业处理前：
  registration_status ≠ '毕业'
  attendance_status ≠ '退学办结'
  is_deleted = 0

毕业处理（process_graduation）：
  registration_status → '毕业'
  attendance_status → '非在校'
  remark → old_remark + ' [毕业处理前：学籍=原学籍，在校=原在校]'

撤销毕业（undo_graduation）：
  正则匹配 remark 中 '[毕业处理前：学籍=...,在校=...]'
  registration_status → 原始学籍
  attendance_status → 原始在校
  remark → 删除备份标记
```

**规则**：
- 资格检查：`registration_status != '毕业' AND attendance_status != '退学办结'`
- 单次处理上限 ≤500人
- 毕业生查询：`registration_status = '毕业' AND is_deleted = 0`
- 毕业生不再参与常规学生列表查询（通过 `_exclude_graduated_clause()` 全局过滤）

### 实习处理状态流转

```
实习处理前：
  attendance_status ≠ '退学办结'
  registration_status ≠ '毕业'
  remark NOT LIKE '%[实习处理：%'

实习处理（process_internship）：
  attendance_status → '非在校'
  remark → old_remark + ' [实习处理：原始在校状态=原状态]'

撤销实习（undo_internship）：
  正则匹配 remark 中 '[实习处理：原始在校状态=...]'
  attendance_status → 原始状态
  remark → 删除备份标记
```

**规则**：
- 资格检查：`attendance_status != '退学办结' AND registration_status != '毕业' AND remark NOT LIKE '%[实习处理：%'`
- 实习处理不改变 `registration_status`（与毕业处理的关键区别）
- 已实习处理学生可通过"已处理"面板查询恢复

### 在校状态和学籍状态交互规则

| 操作 | registration_status 变化 | attendance_status 变化 |
|---|---|---|
| 新增学生 | 默认 `'未注册'` | 默认空 |
| 学籍异动—退学办结 | 不变 | → `'退学办结'` |
| 毕业处理 | → `'毕业'` | → `'非在校'` |
| 撤销毕业 | → 原始值（从备注恢复） | → 原始值（从备注恢复） |
| 实习处理 | 不变 | → `'非在校'` |
| 撤销实习 | 不变 | → 原始值（从备注恢复） |
| 更新在校状态 | 不变 | 仅覆盖 `IN('在校','待销假')` |

### 字段格式规范

| 字段 | 格式 | 示例 |
|---|---|---|
| 日期字段 | `YYYY-MM-DD` | `2026-06-27` |
| 时间戳字段 | `YYYY-MM-DD HH:MM:SS` | `2026-06-27 14:30:00` |
| 身份证号 | 18位大写 | `320102200801151234` |
| 学号 | 6位数字 | `202301` |
| 入学年份 | 4位整数 | `2023` |
| 学校类型 | `1` / `2` | `1`=中职，`2`=技工 |
| 学籍状态 | `正式学籍` / `未注册` / `毕业` | |
| 在校状态 | `在校` / `非在校` / `托管` / `待销假` / `退学办结` | |
| 姓名拼音 | 首字母大写连写 | `ShanYaoPang` |
| 异动信息 | `操作类型&文件编号;操作类型&文件编号` | `班级变更&20240301-001` |
| 毕业备份备注 | `[毕业处理前：学籍=X，在校=Y]` | |
| 实习备份备注 | `[实习处理：原始在校状态=X]` | |
| supervisor_flag | 空 / `按计划办理学平险` / `不参保` / `{year}学平险办结` / `{year}实习就业险办结` | `2026学平险办结` |

---

## 前端约定

- **CSS 框架**：Bootstrap 5.3 CDN
- **图标**：Bootstrap Icons CDN
- **图表**：Chart.js + chartjs-plugin-datalabels CDN
- **手机端**：原生 HTML+CSS，最小触摸面积 44x44px
- **安全 JSON 解析**：`static/js/main.js:safeJSON(response)` 检测 Content-Type，防止 HTML 泄漏崩溃
- **CSRF 自动注入**：`main.js` 在所有 fetch 和表单请求中自动附加 `X-CSRF-Token` header

---

## 身份证读卡器

### 后端：ctypes DLL 方案
- **位置**：`services/card_reader.py`
- 华视电子 SDK，Python ctypes 加载 DLL（termb.dll/sdtapi.dll/WltRS.dll）
- 流程：连接→认证→读取→保存照片→断开

### 前端：WebSocket 方案
- **位置**：`static/js/card_reader.js`
- 连接华视监控软件 `ws://127.0.0.1:9001`
- 双通道：WebSocket优先 → 失败回退 Server API `/api/read_id_card`

---

## 身份证号码校验系统

- **位置**：`utils.py:92-144`
- 7层流水线：空值检查 → 长度验证（18位）→ 前17位纯数字 → 末位X/数字 → 地区码（`_ID_CARD_REGION` 34个省级行政区）→ 出生日期合法 → 加权校验码（ISO 7064:1983 MOD 11-2）
- 通过后自动提取：`birth_date`（YYYYMMDD → YYYY-MM-DD）、`gender`（17位奇男偶女）、`region`（省份中文名）

---

## 开发注意事项

1. ⚠️ 2025-Q4 **不要使用 `alert()` / `confirm()`**：替换为 `showToast()` / `showConfirm()` —— 原因：Bootstrap 5 统一UI风格
2. ⚠️ 2025-Q4 **Flash 消息走 JSON + Toast 通道** —— 原因：支持 AJAX 异步响应
3. ⚠️ 2025-Q4 **所有学生删除是软删除**：`is_deleted=1`，仅退学办结状态可删 —— 原因：数据安全
4. ⚠️ 2025-Q4 **身份证自动提取出生日期**：`utils.extract_birth_date_from_id_card()` —— 原因：减少人工录入
5. ⚠️ 2025-Q4 **数据库使用 WAL 模式**：支持并发读写 —— 原因：daemon线程导入时主请求不阻塞
6. ⚠️ 2025-Q4 **批量导入/更新在后台线程执行**：避免阻塞主Flask请求 —— 原因：WSGI单线程模型限制
7. ⚠️ 2025-Q4 **模板中使用 `url_for()` 生成路由** —— 原因：避免硬编码路径
8. ⚠️ 2025-Q4 **手机端出勤提交使用事务**：`BEGIN IMMEDIATE` → 批量 UPSERT —— 原因：防止并发点名数据不一致
9. ⚠️ 2025-Q4 **启动应用使用 `run_with_tray.py`** —— 原因：打包后路径管理
10. ⚠️ 2025-Q4 **出勤率计算包含"托管"**：`attendance_status IN ('在校', '托管')` —— 原因：托管学生仍属在册生
11. ⚠️ 2025-Q4 **身份证校验使用 `validate_id_card_detailed()`**：7层结构化错误 —— 原因：需要精确的错误定位
12. ⚠️ 2025-Q4 **前后端双重验证身份证**：前端500ms防抖+提交拦截，后端所有入口二次校验 —— 原因：防止非法数据入库
13. ⚠️ 2025-Q4 **字典数据通过 `/api/dicts` 加载**：`<datalist>` 标签 —— 原因：动态字典管理
14. ⚠️ 2025-Q4 **数据库字段通过 ALTER TABLE ADD COLUMN 安全扩展** —— 原因：SQLite不支持完整DDL
15. ⚠️ 2025-Q4 **身份证读卡双通道**：WebSocket优先→Server API回退 —— 原因：兼容不同驱动方式
16. ⚠️ 2025-Q4 **数据库备份使用 AB 轮换**：SQLite原生API+文件复制回退 —— 原因：数据安全+容错
17. ⚠️ 2025-Q4 **前后端双重验证电话号码** —— 原因：数据质量保障
18. ⚠️ 2025-Q4 **入学年份数据隔离**：`enrollment_year`与`grade`独立 —— 原因：跨年级筛选场景
19. ⚠️ 2025-Q4 **AJAX 端点使用 `_require_admin_json()` 鉴权** —— 原因：防止session过期后302重定向泄漏HTML
20. ⚠️ 2025-Q4 **fetch 链统一使用 `safeJSON(response)`** —— 原因：防止HTML被当作JSON解析
21. ⚠️ 2025-Q4 **学生表单弹窗左右两栏布局** —— 原因：Bootstrap响应式优化
22. ⚠️ 2025-Q4 **年级自动推导**：初中毕业时间→session年份→当年 —— 原因：减少人工计算错误
23. ⚠️ 2025-Q4 **school 动态推导**：`_derive_school(major)` 查库缓存 —— 原因：专业与学校类型关联
24. ⚠️ 2025-Q4 **新增默认值**：`registration_status`默认`'未注册'`，`school`动态推导 —— 原因：业务默认值
25. ⚠️ 2025-05 **attendance_records 主键迁移为身份证号**：原 `student_no`→`id_card` —— 原因：学号可能重复分配
26. ⚠️ 2025-05 **更新在校状态保护机制**：仅覆盖`IN('在校','待销假')` —— 原因：防止覆盖退学/毕业状态
27. ⚠️ 2025-05 **ATTENDANCE_STATUS 新增「待销假」** —— 原因：区分缺勤与已请假的待处理状态
28. ⚠️ 2025-05 **国网学籍号已移除**：前端表单删除national_no，DB列保留不参与CRUD —— 原因：实际业务不使用该字段
29. ⚠️ 2025-Q4 **导出 Excel 依赖 openpyxl** —— 原因：pandas不支持样式
30. ⚠️ 2025-05 **年级自动推导（enhanced）**：转学生grade-1 —— 原因：转学生通常需要降级一年
31. ⚠️ 2025-05 **转学生 grade 编辑联动**：入学形式变更自动±1 —— 原因：联动一致性
32. ⚠️ 2025-05 **编辑 API 补充 grade/virtual_class 字段读取** —— 原因：修复编辑弹窗年级丢失Bug
33. ⚠️ 2025-05 **手机号码前端实时验证**：input事件+saveEditStudent拦截 —— 原因：即时反馈
34. ⚠️ 2025-05 **学生列表表列扩充至17列**：含姓名拼音 —— 原因：新增拼音字段后展示需求
35. ⚠️ 2025-05 **姓名 LIKE 搜索加固**：`%/_`转义+最小2字符+参数化 —— 原因：防止LIKE注入和空搜索
36. ⚠️ 2025-05 **query.html 前端全面加固**：XSS修复/AbortController/下拉框值恢复/错误反馈/折叠持久化/年龄校验 —— 原因：安全加固
37. ⚠️ 2025-06 **退学学生处理页面**：`attendance_status='退学办结'`+软删除标记+导出颜色标记+日期筛选 —— 原因：退学处理流程
38. ⚠️ 2025-06 **控制台软删除学生弹窗自动过滤**：排除正式学籍学生 —— 原因：只应展示退学办结学生
39. ⚠️ 2025-06 **转学待办表**：学号6位数字三段校验+实时重复校验+自动填充 —— 原因：学号准确性
40. ⚠️ 2025-06 **学生列表删除按钮验证**：仅退学办结状态可删 —— 原因：防止误删在籍学生
41. ⚠️ 2025-06 **中文姓名→拼音自动生成**：5处触发点+backfill脚本 —— 原因：姓名拼音用于搜索和导出
42. ⚠️ 2025-06 **保险模块 — 数据库字段**：9个保险字段+1个学费字段 —— 原因：保险业务需求
43. ⚠️ 2025-06 **保险模块 — 新增角色**：finance_chief/finance_staff/insurance_officer —— 原因：保险业务流程三权分立
44. ⚠️ 2025-06 **保险模块 — 4个页面**：缴费登记/审批/待办/初始化导入 —— 原因：4阶段业务流程
45. ⚠️ 2025-06 **保险模块 — 业务流程**：4阶段状态机（初始化→登记→审批→办结） —— 原因：业务闭环
46. ⚠️ 2025-06 **保险模块 — 导航增强**：admin下拉菜单+保险角色独立导航 —— 原因：不同角色导航路径
47. ⚠️ 2025-06 **保险模块 — 办结标记统一**：`{year}学平险办结`/`{year}实习就业险办结` —— 原因：防止数据混乱+可追溯年份
48. ⚠️ 2025-06 **保险初始化导入 — 模板含已有数据** —— 原因：便于批量更新
49. ⚠️ 2025-06 **保险初始化导入 — 进度弹窗**：daemon线程+模态进度条 —— 原因：大数据量导入体验
50. ⚠️ 2025-06 **保险初始化导入 — 空行静默跳过** —— 原因：容忍模板格式问题
51. ⚠️ 2025-06 **保险模块 — 仅更新有值字段**：不覆盖不删除 —— 原因：防止误清空已有数据
52. ⚠️ 2025-06 **毕业管理 — 毕业处理+毕业生查询**：事务+备注备份+正则撤销 —— 原因：可逆操作+数据可追溯
53. ⚠️ 2025-06 **共享过滤函数** `_exclude_graduated_clause`/`_non_graduated_where`：已应用到20+核心查询 —— 原因：避免遗漏过滤
54. ⚠️ 2025-06 **模板驱动数据导出引擎**：筛选→模板→映射→预览→导出，字段白名单防注入 —— 原因：通用化导出能力
55. ⚠️ 2025-06 **在校状态更新 BUG 修复**：补充commit()/close()/return —— 原因：原代码缺少commit导致更新无效
56. ⚠️ 2025-06 **退学学生列表排序修复**：按dropout_date DESC —— 原因：最新申请排最前
57. ⚠️ 2025-06 **数据同步时间重构**：仅统计admin账号操作时间 —— 原因：避免非admin操作污染同步时间戳
58. ⚠️ 2025-06 **学生列表新增创建时间列** —— 原因：追溯数据录入时间
59. ⚠️ 2025-06 **实习处理模块**：在校→非在校，备注备份，正则撤销，已处理查询面板 —— 原因：实习期间学生不在校
60. ⚠️ 2025-06 **保险综合查询页**：`/api/insurance/query` 多维度查询 —— 原因：保险数据统计需求
61. ⚠️ 2025-06 **待销假公示页**：公开查询缺勤学生 —— 原因：校园公示需求
62. ⚠️ 2025-06 **备份间隔动态调整**：运行时API调整+持久化+定时器重建 —— 原因：适应不同运维需求
63. ⚠️ 2025-06 **备份状态API**：倒计时/进度/验证详情 —— 原因：运维监控
64. ⚠️ 2025-06 **备份日志自动清理**：500条/30天双阈值 —— 原因：防止日志文件过大
65. ⚠️ 2025-06 **CSRF 防护**：所有写请求强制校验+前端自动注入 —— 原因：安全防护
66. ⚠️ 2025-06 **登录限流**：60秒5次暴力破解防护 —— 原因：安全防护
67. ⚠️ 2025-06 **CSP 安全响应头**：限制资源来源（本站+CDN） —— 原因：防XSS
68. ⚠️ 2025-06 **敏感数据脱敏**：非admin身份证/电话打码 —— 原因：数据隐私
69. ⚠️ 2025-06 **_is_insurance_role() 函数**：保险角色自动跳过学校/年级权限过滤 —— 原因：保险角色需要全校数据视图
70. ⚠️ 2026-09 **公开公示页统一口令校验**：blueprint 级 `before_request`，session / `?k=` / `X-Public-Key` 三通道 —— 原因：免登录页面必须防止任意抓取全校名单
71. ⚠️ 2026-09 **公开页限流按 scope 隔离**：`_check_public_rate_limit(ip, scope)`，清理时只删过期项 —— 原因：多页共用同一 key 会互相挤占额度
72. ⚠️ 2026-09 **花名册仅公开三字段**：姓名/学籍状态/行政班级，排除毕业+退学办结+软删除+未分班，且不复用 `_non_graduated_where()`（该函数不含退学办结条件）—— 原因：数据最小化 + 过滤口径一致
73. ⚠️ 2026-09 **公开页卡片必须带文字**：学籍状态徽章同时用文字与颜色区分，不得仅靠颜色 —— 原因：信息可达性
74. ⚠️ 2026-09 **模板改动需重启服务**：`debug=False` 且未开 `TEMPLATES_AUTO_RELOAD`，Jinja 模板缓存不会热更新 —— 原因：避免改动后误以为未生效
