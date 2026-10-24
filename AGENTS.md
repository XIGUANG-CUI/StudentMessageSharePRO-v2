# AGENTS.md — StudentMessageShareMax 项目指南

> 本文件供 AI 代理（及开发者）快速理解项目架构、约定与开发规范。
> 最后更新：2026-10-24（新增区划匹配引擎 + 档案派遣模块：人才机构字典/派遣工作台/转递单配置/转递单导出）

---

## 项目概述

学生数据管理系统——单体 Flask 3.0 Web 应用，管理学生学籍、出勤、保险、毕业/实习等全生命周期数据。桌面部署（PyInstaller 打包），系统托盘启动，SQLite 存储，身份证读卡器集成。

## 技术栈

- **后端**：Python 3.10+ / Flask 3.0 / SQLite3（原生 sqlite3，WAL 模式）
- **前端**：Jinja2 模板 + Bootstrap 5 + 原生 JS（fetch API）
- **数据**：openpyxl（Excel 读写）/ python-docx（Word 导出）/ qrcode（二维码）
- **桌面**：pystray（系统托盘）/ Pillow（图标）/ tkinter（启动弹窗）
- **硬件**：ctypes 动态加载读卡器 DLL + WebSocket 实时通信
- **打包**：PyInstaller（onedir 模式，run_with_tray.spec）

## 架构设计（v1.0.0.4 当前状态）

```
run_with_tray.py (310行)  启动入口：系统托盘 + 单实例锁 + 数据目录分离 + Flask 服务
├── app.py (414行)         应用核心：3个入口路由 + CSRF防护 + 7个context_processor + Blueprint注册
├── config.py (280行)      配置：SECRET_KEY持久化 + 端口 + 路径 + 40字段FIELD_MAPPING + 模板导出配置 + InsuranceStatus 常量类
├── auth.py (267行)        权限模块：7个装饰器 + 3个JSON辅助 + session管理 + 字段/学校/年级权限过滤
├── models/                数据访问层（P1-C：models.py 2832行 → 子模块包）
│   ├── __init__.py (323)  聚合导出，保持 `from models import get_students` 向后兼容
│   ├── db.py (394)        连接管理（_RequestDB/PRAGMA/get_db）+ init_db + 3个迁移 + 毕业过滤共享函数
│   ├── students.py (743)  学生CRUD + 软删除 + _build_student_where_clause + 退学/转学待办查询
│   ├── lifecycle.py (461) 毕业/实习：批量处理+撤销+年级/班级筛选
│   ├── attendance.py (291) 出勤：记录/UPSERT/透视查询/在校状态联动
│   ├── stats.py (483)     统计：dashboard卡片 + 班级对照表 + 筛选值
│   ├── dicts.py (211)     字典管理 + 入学年份同步 + 照片状态
│   ├── export_template.py (127) 模板导出引擎：模板CRUD + 字段映射 + 导出历史
│   ├── logs.py (142)      更新日志 + 批量更新 + 同步时间戳
│   ├── users.py (81)      用户CRUD + 启用/禁用
│   ├── school_types.py (281) 学校类别字典（编码→名称解析，异常回退 config）
│   ├── district.py (1107) 区划字典（ATTACH district.db）+ 区划匹配批次/结果/历史映射
│   └── archive.py (834)   档案派遣：人才机构字典/三级回退匹配/派遣批次/升学/转递单配置
├── utils.py (960行)       工具函数（身份证7层校验/电话号段校验/自然排序/LAN IP检测/日期标准化等）
├── pinyin_utils.py        汉字拼音字典（26671字，369KB 数据，纯Python零依赖）
├── blueprints/            路由层（12个Blueprint，193路由 + app.py 3路由 + /static = 197路由）
│   ├── admin.py (1206)    核心管理：学生CRUD/导入/批量更新/用户/字典 (30路由)
│   ├── api.py (1097)      数据API：学生/照片/字典/统计/筛选/读卡器配置 (35路由)
│   ├── lifecycle.py (781) 学籍生命周期：毕业/实习/退学/异动/转学 (26路由)
│   ├── export.py (716)    模板导出引擎：Excel/Word 填充 (14路由)
│   ├── insurance.py (906) 保险业务：登记/审批/待办/初始化 + 审批按钮锁定计算 (21路由)
│   ├── district_match.py (653) 区划匹配：批次/执行/结果/历史映射/字典导入 (20路由)
│   ├── archive.py (413)   档案派遣：机构字典/覆盖率/派遣批次/升学名单/转递单导出 (22路由)
│   ├── leave_cancel.py (347) 销假+回收站 (10路由)
│   ├── mobile.py (133)    移动端出勤登记 (5路由)
│   ├── user.py (104)      普通用户查询 (2路由)
│   ├── backup.py (58)     备份管理（延迟导入 _get_backup_service） (4路由)
│   └── public.py (41)     公开查询+IP限流 (4路由)
├── services/              业务服务层（P1-5 抽取）
│   ├── backup_service.py (726)  AB轮换备份+完整性校验+定时
│   ├── card_reader.py (375)     读卡器 ctypes 封装 + WebSocket 通信
│   ├── import_service.py (196)  批量更新后台执行（run_batch_update）
│   ├── insurance_service.py (352) 保险初始化导入（start_insurance_init_import）+ Excel 日期序列号规范化
│   └── district_match/ (1236)   区划匹配引擎（engine1 编码/engine2 地址解析/decider/runner/exporter/pack）
├── tests/                 测试套件（5文件）
│   ├── conftest.py        Flask test client fixture（内存SQLite）
│   ├── test_models.py     数据层测试
│   ├── test_routes.py     路由注册/url_for/HTTP 测试
│   ├── test_auth.py       权限装饰器 + session 测试
│   └── test_utils.py      工具函数单元测试
├── templates/             Jinja2 模板（admin/user/mobile/insurance/public/archive 子目录，34个HTML文件）
├── static/                CSS(519行) + JS(414+115行)（photos/ 运行时创建）
├── scripts/               一次性工具（backfill_pinyin/birth_date + 旧spec，不参与打包）
└── sdk/                   读卡器 DLL（4个）+ license.dat + 控件安装包
```

### 关键枢纽识别

| 枢纽文件 | 角色 | 依赖方 |
|---------|------|--------|
| `models/__init__.py` | 数据层统一出入口，90+ 函数聚合导出 | 全部 blueprints + utils.py + services |
| `auth.py` | 权限中枢，7装饰器+3JSON辅助+字段过滤 | 全部 blueprints + app.py |
| `app.py` | 应用骨架，CSRF+安全头+context_processor+Blueprint注册 | 全部 blueprints（注册入口） |
| `config.py` | 配置单点，FIELD_MAPPING/路径/端口/备份参数 | 全部模块 |
| `utils.py` | 公共工具函数池，身份证校验/电话校验/数据解析 | blueprints/admin/api + services |
| `models/db.py` | 数据库连接 + init_db + 迁移 + 毕业过滤共享函数 | 全部 models 子模块 |

### 高耦合热点

| 热点 | 耦合度 | 说明 |
|------|--------|------|
| `admin.py` ↔ `api.py` | 高 | 大量共享 models 查询函数、utils 工具函数、config 配置 |
| `models/students.py` `_build_student_where_clause` | 高 | 被 admin/api/lifecycle/insurance/export 5 个 Blueprint 间接调用 |
| `models/db.py` 毕业过滤函数 | 高 | `_exclude_graduated_clause` / `_non_graduated_where` 被 stats/dicts/attendance 等 5+ 子模块依赖 |
| `auth.py` 权限装饰器 | 高 | 7 个装饰器被 8 个 Blueprint 使用 |
| `utils.py` 数据校验函数 | 中高 | `validate_id_card`/`validate_phone` 被 admin+api+services 共享 |

### app.py 保留内容（414行）

- **入口路由**：`/`（角色分发，5角色分支）、`/login`（含登录限流5次/60秒）、`/logout`
- **安全防护**：CSRF Token 初始化 + CSRF 校验（before_request）+ 安全响应头（after_request）
- **钩子**：7 个 context_processor（CSRF Token/学校筛选URL/读卡器地址/字段帮助/CSS版本/入学年份/最后更新时间）
- **启动**：`_get_backup_service`（单例）、`_init_backup_on_startup`、`init_admin_user`、`cleanup_temp_files`
- **全局**：`_login_attempts`（登录限流字典+清理逻辑）、`_backup_service`、`_admin_initial_password`
- **Blueprint 注册**：12 个 `app.register_blueprint()`
- **错误处理**：全局 Exception handler（HTTPException 透传，API 路径返回 JSON 500）

### 分层职责

| 层 | 职责 | 示例 |
|----|------|------|
| **路由层**（blueprints/） | 解析 request、权限校验、调用 service/models、返回 jsonify/render_template | `admin_batch_update_confirm` 构造 ctx 调用 service |
| **服务层**（services/） | 复杂业务逻辑、后台线程、进度管理 | `run_batch_update(ctx)`、`start_insurance_init_import` |
| **数据层**（models/） | SQL 查询、CRUD、迁移、统计（13 子模块） | `get_students`、`init_db`、`get_stats` |
| **工具层**（utils.py/auth.py） | 纯函数、权限校验、公共辅助 | `_derive_school`、`school_visible_contains` |

## 保险审批页按钮锁定体系（v1.0.0.4 后新增）

审批页（templates/insurance/approve.html）三个徽章按钮的可用状态，由**后端统一计算后随学生数据下发**，前端只读取、不重算，避免前后端解析不一致。

### 后端锚点

- `blueprints/insurance.py`：
  - `_is_blank(v)`：空值判定（None/空串/「新生默认首保」占位均视为空）。
  - `_pay_date_year(pd)`：从学平险缴费日期提取年份，**同时兼容 `YYYY-MM-DD` 与 Excel 日期序列号**（如 `45893` → 2025）。序列号按 `datetime(1899,12,30)+timedelta(days=serial)` 还原，与前端 `formatDate` 的 `(num-25569)*86400000` 等价。空值/占位/无法解析返回 `None`。
  - `_annotate_xueping_lock(s)`：为每个学生计算 `xueping_locked`(bool) 与 `xueping_lock_hint`(str|None)，在 `api_insurance_approve_tree` 中过滤结果集后逐条调用。
  - `_student_batch(s)`：按 grade/enrollment_year 计算当前年级批次（1~3 年）。第 3 年且无实习险缴费记录的学生不进学平险审批（仅审实习险）。
- `config.py` `InsuranceStatus` 常量类：
  - `EXTERNAL_PAY_MARKER = '新生默认首保'`（占位标记，非真实日期；config 与前端 JS 双镜像，改一处须同步另一处）。
  - `parse_flag_year(flag)`：从「YYYY年度学平险办结」类标记取年份（正则 `\d{4}年度`）。
  - `done_type(flag)`：区分标记归属 `'xueping'` / `'intern'`。
  - `CURRENT_SCHOOL_YEAR = datetime.now().year`：标记年份基准（日历年）。

### 锁定规则（"按计划办理学平险"按钮）

| 条件 | 锁定 | 提示 |
|------|------|------|
| 已交实习就业险（internship_insurance_pay_date 非空） | 锁 | 该生已交实习就业险，无需再办理学平险 |
| 学平缴费日期 == 「新生默认首保」占位（且实习未交） | 锁 | 请录入该生2期学平险缴费日期 |
| 主管标记「YYYY年度学平险已办结」且 YYYY >= 缴费日期年份 | 锁 | 该生{YYYY}年度学平险已办结，无需重复办理 |
| 标记年份 < 缴费日期年份（已录下一年真实日期） | 放行 | — |

> 安全侧约定：年份既不相等也不小于（如回溯跨学年边界使 标记年 > 缴费年）时按锁定处理，避免误放行重复办理。

### "不参保"按钮禁用规则

`iaModalReject`（不参保）在以下任一为真时禁用（**无需提示**）：
- 实习就业险缴费日期非空；或
- 学平险缴费日期非空且**不是**「新生默认首保」占位（即已录真实缴费日期）。

### Excel 日期序列号（跨层共识）

导入时 Excel 日期常被 openpyxl 读成 int 序列号（如 `45893`=2025-08-24）存入 DB。`services/insurance_service.py` 的导入层（约 177~186 行）已对日期型列做规范化：`datetime.datetime(1899,12,30)+timedelta(days=serial)` → `YYYY-MM-DD`。**凡涉及缴费日期年份比较的代码，必须用 `_pay_date_year` 而非裸字符串切片**，否则会误读 `pd[:4]`（如 `45893`→`4589`）导致年份比较失效。

## 保险初始化导入与软删除

- `services/insurance_service.py` `_run_import`：按身份证匹配**全量覆盖**（模板含该生即覆盖写入，用于纠正历史脏数据）。
- **导入跳过 `is_deleted=1` 的学生（回收站/软删除），计入 skipped 而非 error**——这是设计行为，与 `attendance_status` 无关。
- ⚠️ 红线提醒：曾误加「退学办结跳过导入」逻辑，已回滚。原因是导入是纠正脏数据的唯一途径，模板含某人即应覆盖；是否展示由 `is_deleted` 决定，**不要再为 attendance 状态加导入过滤**。若某软删除学生数据需纠正，应先 `is_deleted=0` 再重导，或一次性 UPDATE。

### 已知遗留问题（待用户定夺，未擅改）

- 马瑞楠（`id_card=230112201002214729`，`PECE202523010000000025`）：`is_deleted=1`（软删除）、`attendance_status='退学办结'`，其 `supervisor_flag` 历史脏数据 `25年度学平险办结` 因导入跳过无法被模板覆盖纠正。三条处理路径待选：① 置之不理（孤儿数据无害）；② `is_deleted=0` 后重导覆盖；③ 一次性 UPDATE 清理其及同类已删除脏记录（批量统计：35 名退学办结中 30 人 `is_deleted=1` 全部未更新、5 人正常已更新；活跃学生仅宋晗 `110228200003045415` 存 2 位年脏标记需另行处理）。

## 档案派遣模块（毕业生档案转递单）

入口：`/admin/archive/*`（`blueprints/archive.py`，22 路由），导航「档案派遣」下拉三个页面。

- **人才机构字典**（`talent_agency`）：一县一机构，`county_code` 唯一。`matchable=0` 表示特殊区域（如哈经开/大庆高新等无对应行政区划的自定义码），只能人工指定，不参与自动匹配。
- **单条补录**：`POST /api/archive/agencies`（`models.create_agency`），区划代码 + 机构全称必填，层级/县名/机构编号自动推断，区划码重复直接拒绝。
- **三级回退匹配**：学生「县」→ 县级机构；无则用县码前 4 位 + `00` 找市级机构；再退省级 `XX9900`。县码统一归一化为 6 位（`norm_code`），历史旧码经 `dm_history_mapping` 仅在新字典查无此码时生效。
- **机构表导入**：`_district_index()` 用**6 位前缀**建索引（区划库是 12 位码）；字典查无的市级/省级自定义码由 `_infer_level()` 按编码规则补判（`9900`→省，`99`/`00`→市），避免整表被误判为特殊区域。
- **派遣批次**（`archive_dispatch`）：批次号 `{届}-{县名}-{两位序号}`（如 `2026-道里区-01`），**一县一批**；未匹配机构的学生不进批次。状态机 `待派遣 → 已派遣`，旁支 `已退回` / `已失效` / `升学`（无签收日期）。
- **快照**：生成时把学校名称/经办人/经办人电话/回执地址/回执邮编按校别从 `archive_settings` 写入记录，后续改配置不影响已生成单据。
- **转递单配置**（`archive_settings`）：`school` 主键，中职/技工各一套；`dispatch_no` 转递编号列已建但预留空值。
- **升学**：手动勾选或 Excel 导入名单标记，升学学生不参与派遣，名单可导出 xlsx。
- **转递单导出** `/admin/archive/export/<batch_no>`：复用 `blueprints/export.py` 的 `_export_docx()`，模板 `archive_templates/档案转递单模板.docx`，每人一页、分页符隔开、输出单 docx。
- **撤销毕业联动**：`models/lifecycle.py` `undo_graduation()` 调用 `models.archive.mark_dispatches_invalid()`，把该生派遣记录置 `已失效`（留痕不删）。
- **唯一约束**：`CREATE UNIQUE INDEX ... ON archive_dispatch (student_id) WHERE is_deleted = 0`，防止同一学生产生多条有效派遣。

## 关键约定（红线规则）

> 以下规则在重构过程中确立，后续开发必须遵守：

1. **不改 API 接口**：路由函数签名、参数结构、返回值格式不变
2. **不改 URL 路径**：Blueprint **不加 url_prefix**，`@bp.route` 路径与原 `@app.route` 一致（前端 `fetch('/api/...')` 依赖硬编码路径）
3. **不改全局状态结构**：`import_progress`（admin.py）、`insurance_init_progress`（insurance.py）等 progress 字典保留对应 Blueprint 模块级，service 层接收引用参数
4. **不增删依赖**：仅用标准库 + requirements.txt 已有包
5. **不改用户文案**：flash 消息、JSON message、错误提示逐字保留
6. **向后兼容**：所有修改保证现有功能不破坏，数据库迁移幂等

## 开发指南

### 新增路由

1. 确定业务领域，放入对应 Blueprint（如学生管理→admin.py，API→api.py）
2. 用 `@bp.route`（非 `@app.route`），路径不含 Blueprint 前缀
3. 权限校验用 `@auth.admin_required` 装饰器 或 `from auth import _require_admin_json`（JSON API）
4. url_for 引用同 Blueprint 内端点用 `.endpoint`，跨 Blueprint 用 `blueprint_name.endpoint`

### 新增 Blueprint

1. `blueprints/xxx.py`：`bp = Blueprint('xxx', __name__)`（**不加 url_prefix**）
2. app.py 注册：`from blueprints.xxx import bp as xxx_bp; app.register_blueprint(xxx_bp)`
3. `run_with_tray.spec` 的 hiddenimports 加 `blueprints.xxx`
4. 如有 url_for 引用，全项目加 `xxx.` 前缀

### 新增 Service

1. `services/xxx_service.py`：业务逻辑函数，接收参数（含 progress_dict 引用）
2. Blueprint 内路由构造 ctx/参数调用 service
3. spec hiddenimports 加 `services.xxx_service`
4. service 不依赖 Flask（不导入 request/session/jsonify），保持可测试性

### 数据库迁移

1. `models/db.py` 的 `init_db()` 内追加 `try/except sqlite3.OperationalError` 幂等迁移
2. 更新 `schema_version` 表的版本推断逻辑
3. 迁移必须幂等（列已存在则跳过）
4. 如需新增 model 子模块，在 `models/__init__.py` 聚合导出，保持 `from models import ...` 兼容

### 公共化辅助函数

跨 Blueprint 共用的辅助函数，按依赖归位：
- 用 `session` → `auth.py`（如 `_get_current_enrollment_year`）
- 纯函数/用 config → `utils.py`（如 `school_visible_contains`、`_derive_school`）
- app 级钩子（CSRF/context_processor/teardown）→ 保留 `app.py`

## 数据库

- **引擎**：SQLite3，WAL 模式，busy_timeout=5000，cache_size=-8000（8MB），synchronous=NORMAL
- **18 张表**：students（39 列）、users、update_logs、sync_timestamp、attendance_records、attendance_dates、dicts、school_types、export_templates、export_field_mappings、export_history、schema_version、dm_match_batch、dm_match_result、dm_history_mapping、talent_agency、archive_settings、archive_dispatch
- **独立区划库**：`district.db`（ATTACH，运行期只读）含 `dm_district_code`（约 66 万行，12 位码按 6 位前缀索引，admin_level 1省/2市/3县区/4街道/5社区），不参与备份
- **迁移管理**：`schema_version` 表，首次运行推断版本（v0/v1/v2），幂等 ALTER TABLE
- **连接管理**：请求级复用（`models/db.py` `_RequestDB` 包装器，close 为 no-op，`app.teardown_appcontext` 统一清理）
- **共享过滤函数**：`models/db.py` 导出 `_exclude_graduated_clause()` / `_non_graduated_where()`，供 stats/dicts/attendance 等子模块排除毕业学生
- **学校类别字典（school_types）**：`students.school` 仍是编码，不做外键；名称统一由 `models/school_types.py` 解析（异常时回退 `config.SCHOOL_TYPES`）。新增类别后，学生筛选/编辑/查询统计/毕业查询/导出引擎/用户权限/保险页面与导入校验白名单均自动生效，禁止在代码里硬编码 `1`/`2` 名称。停用类别不出现在新增/编辑下拉，但历史数据仍可解析；被学生或用户权限引用的类别禁止删除
- **路径**：开发环境 `data/student.db`，打包后 `user_data/data/student.db`（通过 `USER_DATA_DIR` 环境变量分离）
- **备份**：`services/backup_service.py` AB 轮换（student_A.db / student_B.db），双策略（SQLite backup API → 文件复制兜底），完整性与空文件校验

## 打包配置

- **入口**：`run_with_tray.py`（系统托盘 + Flask 服务子进程）
- **spec**：`run_with_tray.spec`（onedir 模式）
- **hiddenimports**：含 12 个 `blueprints.*` + 12 个 `models.*` + 4 个 `services.*` + `collect_submodules('services.district_match')` + openpyxl 全子模块 + qrcode/PIL/pystray/tkinter + logging/logging.handlers + ipaddress/threading/datetime/shutil/sqlite3
- **数据文件**：templates/ + static/（排除 photos/）+ `archive_templates/`（档案转递单 Word 模板）+ `data/district_pack.tsv.gz` + sdk/（含 DLL + license.dat）+ 图标 + openpyxl/qrcode 数据
- **数据目录分离**：打包后 `USER_DATA_DIR` 环境变量指向 EXE 同级 `user_data/`，包含 data/databackup/uploads/static/photos/logs 五个子目录
- **可写目录**（不打包，运行时创建）：data/、databackup/、uploads/、static/photos/、logs/
- **单实例锁**：Windows 命名互斥体 `Global\StudentMessageSharePRO_SingleInstance`，重复启动弹出提示后退出
- **不打包**：`scripts/`（backfill 脚本 + 旧 spec，一次性工具）、`data/secret.key`（运行时生成）

## 备份机制

- `services/backup_service.py`：AB 轮换（student_A.db / student_B.db）+ 双策略（SQLite backup API → 文件复制兜底）+ 完整性校验 + 定时（默认 30 分钟）
- 备份目录：`databackup/`（用户数据目录）

## 重构历史摘要

| 阶段 | 内容 | 效果 |
|------|------|------|
| P0 | SECRET_KEY 持久化 + 消除裸 except + schema_version 迁移 | 安全性 + 健壮性 |
| P1-2/3 | get_students 拆分（CC 47→10）+ field_mapping 提取 | 可维护性 |
| P1-1 | app.py 5496→396行，144 路由拆 10 Blueprint | 模块化 |
| P1-5 | 271 行业务逻辑抽 service 层 | 职责分离 |
| P1-C | models.py 2832行 → models/ 包（9 子模块，3164 行），保持 `from models import ...` 向后兼容 | 可维护性 + 可定位性 |
| spec | hiddenimports 加 10 blueprint + 9 model + 4 service | 打包兼容 |
| 第一批 | except 加日志(18处) + natural_key 去重(4处) + 移除 pandas + 死代码清理 + scripts/ 归档 | 可观测性 + 一致性 |
| spec-v2 | hiddenimports 加 logging + excludes 注释更新 + scripts/ 声明 | 打包同步 |
| 保险审批 | 审批页按钮锁定体系（后端 `_annotate_xueping_lock` 算 `xueping_locked`+`xueping_lock_hint` 下发前端禁用）、Excel 日期序列号兼容（`_pay_date_year` + 导入层规范化）、修复审批列表 373 行 `and`→`or` 过滤 BUG | 审批页正确性 + 年份脏数据兼容 |
| 导入澄清 | 回滚「退学办结跳过导入」误改；明确导入按身份证全量覆盖、`is_deleted=1` 学生跳过系设计行为，与 attendance 无关 | 防止历史脏数据无法纠正 + 避免误加 attendance 过滤 |
| 档案派遣 | 新增 `talent_agency`/`archive_settings`/`archive_dispatch` 三表 + `models/archive.py` + `blueprints/archive.py`（22 路由）+ 3 页面 + 转递单 docx 导出 + 撤销毕业联动失效 | 毕业生档案按县分批派遣 |

## 安全性

- **CSRF 防护**：`app.py` before_request 对所有 POST/PUT/DELETE/PATCH 请求校验 CSRF Token（`/login`、`/mobile/login` 豁免），前后端双重通道（form + Header `X-CSRF-Token`）
- **登录限流**：每用户名 60 秒内最多 5 次尝试，超限 flash 提示剩余秒数；1 小时未活跃自动清理记录
- **安全响应头**：`X-Content-Type-Options: nosniff` / `X-Frame-Options: SAMEORIGIN` / `Referrer-Policy: strict-origin-when-cross-origin` / CSP（允许 Bootstrap CDN + WebSocket）
- **SESSION_COOKIE_SECURE**：默认 `False`（本地 HTTP 部署），通过环境变量 `SESSION_COOKIE_SECURE=1` 启用（HTTPS 部署），启动时控制台输出安全状态
- **密码安全**：Werkzeug `generate_password_hash` / `check_password_hash`，登录使用 `secrets.compare_digest` 防时序攻击
- **数据脱敏**：非 admin 用户查询时，身份证号（`410***********1234`）、电话号（`138****1234`）自动脱敏

## 测试

- **5 个测试文件**：`conftest.py`（Flask test client + 内存 SQLite fixture）、`test_models.py`、`test_routes.py`、`test_auth.py`、`test_utils.py`
- 隔离 venv（`flask==3.0.0 openpyxl==3.1.2`）+ Flask test client 验证路由注册/url_for/HTTP
- service 函数可用空数据功能测试（如 `run_batch_update` 空 pending → completed）
- 数据库迁移用内存 SQLite 测试（schema_version 推断/幂等）

---

*本文件随项目演进更新。重大架构变更后同步修改。*
