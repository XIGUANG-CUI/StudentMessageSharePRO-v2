---
alwaysApply: true
scene: git_message
---

# Git 提交信息规范 — StudentMessageShare

## 格式

```
<type>(<scope>): <subject>

<body>

<footer>
```

**Header 为必填**，一行，不超过 72 字符。Body 与 Footer 省略时，直接连写 subject 后空一行结束提交。

## Type（必填）

| type | 含义 | 使用场景 |
|------|------|---------|
| `feat` | 新功能 | 新增页面/路由/API/前端交互/模块 |
| `fix` | 缺陷修复 | Bug 修复、异常处理加固 |
| `refactor` | 重构 | 调整代码结构/模式/拆分/合并，不改变行为 |
| `perf` | 性能优化 | 索引/查询优化/缓存/PRAGMA 调整 |
| `db` | 数据库变更 | 建表/ALTER/CREATE INDEX/数据迁移 |
| `test` | 测试相关 | 新增/修改单元测试/压力测试 |
| `docs` | 文档变更 | README/注释/AGENT.MD 更新 |
| `style` | 代码格式 | 缩进/空格/换行，不影响运行逻辑 |
| `chore` | 工具/依赖 | requirements/SDK/启动器/配置文件更新 |
| `ui` | 界面调整 | Bootstrap 布局/CSS/响应式/样式微调 |
| `security` | 安全加固 | 鉴权/Session/输入校验/JSON 防泄漏 |

## Scope（推荐，优先使用以下范围）

使用受影响的功能模块，非文件路径：

| scope | 范围 | 常见改动 |
|--------|------|---------|
| `students` | 学生管理 | 学生 CRUD、列表筛选、编辑弹窗、导入导出 |
| `students-form` | 学生表单 | 表单字段增删、布局调整、分组合并、必填项标识 |
| `students-grade` | 年级/入学形式 | 年级自动推导、转学生 grade ±1、入学形式变更联动 ⭐新增 |
| `attendance` | 出勤点名 | 移动端点名、批量 UPSERT、状态计算、主键迁移 |
| `attendance-db` | 出勤数据库 | attendance_records 表结构变更、ALTER/迁移、约束重建 |
| `attendance-status` | 在校状态 | 在校状态更新、保护机制、待销假流转 |
| `leave-cancel` | 销假处理 | 销假页面 UI、搜索 API、批量销假确认、在校状态联动 |
| `mobile` | 移动端 | 手机端 UI、年级/班级页面、触摸适配 |
| `photos` | 照片模块 | 读卡器拍照、照片存储/调取/导出 ZIP |
| `photos-export` | 照片导出 | ZIP 生成、year 目录、过滤器导出 |
| `dicts` | 字典管理 | 字典增删改、Excel 导入/导出、datalist 联动 |
| `qr-code` | 二维码生成 | 手机端二维码（qrcode[pil]）、LAN IP 检测、URL 手动切换 |
| `auth` | 权限认证 | 装饰器/鉴权/Session/字段权限/学校可见性 |
| `backup` | 备份服务 | AB 轮换、定时备份、SQLite backup API |
| `export` | 数据导出 | Excel 导出（列表/筛选/出勤透视表）、ZIP 打包 |
| `export-attendance` | 出勤导出 | 出勤明细对照表 Excel（透视格式、日期筛选、openpyxl 样式） |
| `import` | 批量导入 | 上传/预览/进度轮询/daemon 线程 |
| `batch-update` | 批量更新 | Upsert 匹配逻辑、自动新增、数据校验 |
| `id-card` | 身份证读卡 | ctypes DLL / WebSocket 双通道、校验算法 |
| `id-card-validate` | 身份证校验 | 7 层校验、前后端规则对齐、单元测试 |
| `phone-validate` | 电话验证 | 号段库、前后端双重验证 |
| `year-filter` | 年份过滤 | enrollment_year 隔离、session 切换、全校视图 |
| `dashboard` | 控制台 | 统计卡片、图表布局、打印样式、在校状态更新按钮 |
| `query` | 查询统计 | 多维查询、班级对照表、分布表 |
| `api` | 接口层 | JSON 端点/分页/过滤/响应结构 |
| `models` | 数据层 | SQL 查询函数、事务、upsert、透视查询 |
| `config` | 配置 | config.py 配置项、启动参数 |
| `tray` | 托盘启动 | run_with_tray.py、Tkinter、LAN IP 检测 |
| `toast` | 通知系统 | showToast/showConfirm、flash JSON 通道 |
| `print` | 打印功能 | @media print、data-print-id、页面布局 |
| `student-dropout` | 退学学生处理 | 退学办结列表、待处理/已处理标记、Excel 导出、软删除联动 ⭐新增 |
| `transfer-pending` | 转学待办 | 转学生未注册列表、学号填写+校验、学籍状态变更、确认弹窗 ⭐新增 |
| `pinyin` | 拼音转换 | pinyin_utils.py 中文姓名→拼音转换、name_pinyin 字段自动生成、backfill 回填脚本 ⭐新增 |
| `insurance` | 保险模块 | 保险缴费登记/审批/待办/初始化导入 4 页面、3 新角色（财务主管/财务人员/保险员）、9 保险字段 ⭐新增 |
| `insurance-register` | 保险缴费登记 | 财务人员批量填写学平险/实习险缴费信息、保费金额、筛选查询 ⭐新增 |
| `insurance-approve` | 保险审批 | 财务主管批量审批通过/否决、supervisor_flag 状态机 ⭐新增 |
| `insurance-todo` | 保险待办 | 保险员批量办结、自动生成申报日期、学平险保单号填写 ⭐新增 |
| `insurance-init` | 保险初始化导入 | 管理员 Excel 导入主管标记基数、模板含已有数据、进度弹窗、空行静默跳过 ⭐更新 |
| `insurance-roles` | 保险角色权限 | finance_chief/finance_staff/insurance_officer 角色体系、权限装饰器、登录跳转 ⭐新增 |
| `graduation` | 毕业管理 | 毕业处理（批量标记学籍状态→毕业）、撤销毕业、毕业生查询、全业务毕业学生过滤 ⭐新增 |
| `graduation-filter` | 毕业过滤 | _exclude_graduated_clause/_non_graduated_where 共享过滤函数、API/导出排除毕业学生 ⭐新增 |
| `graduation-query` | 毕业生查询 | 按学校类别/年级/班级/姓名筛选项联动查询已毕业学生、CSV 导出、姓名拼音+身份证搜索 ⭐新增 |
| `export` | 模板导出引擎 | 模板上传/解析、占位符字段映射、Excel/Word 模板填充导出、映射方案保存复用、导出历史 ⭐新增 |

## Subject 规范

- **语言**：全中文
- **动词选择**：
  - 新增功能 → 添加/新增
  - 修复缺陷 → 修复/解决
  - 删除代码 → 删除/移除
  - 格式调整 → 调整/优化
  - 重构变更 → 重构/优化（结构）/改进（性能）
- **不写句号**
- **说明做什么，不说明怎么做**

## Body 规范（推荐）

- 描述**修改动机**与**主要变更**
- 与上一版本有何不同
- 涉及多文件时分行列举
- 每行不超过 72 字符

## Footer 规范

| 类型 | 格式 |
|------|------|
| 关闭 Issue | `Closes #123` |
| 关联 Issue | `Related-to: #456` |
| Breaking Change | `BREAKING CHANGE: API 接口变更说明` |

## 示例模板

### feat — 新增功能

```
feat(students): 添加学生列表按筛选条件导出照片 ZIP 功能

- 新增 /admin/students/export/photos 路由，复用 _get_filtered_students()
- 支持 has_photo/school/grade 等筛选条件，携带 query_string 到导出接口
- ZIP 内部按 enrollment_year 分目录组织，count=0 时 flash 警告并重定向
- 添加"导出筛选照片"按钮到 students.html 卡片头部

Closes #45
```

### feat — 新增 API

```
feat(api): 新增按筛选条件导出学生 Excel 接口

- GET /admin/students/export/excel，admin 权限，调用 _get_filtered_students()
- 计算 age 字段后传入 export_students_to_excel()
- 空结果 flash 警告并重定向回列表页
- 按钮携带当前 query_string 实现筛选联动
```

### fix — 修复 Bug

```
fix(attendance): 修复移动端点名批量提交时 student_no 为空导致 NOT NULL 异常

- 问题原因：get_all_students() 返回数据中部分记录 student_no 为 None
- 修复方案：写入前过滤 student_no 为空的记录，UPSERT 时使用过滤后列表
- 同时修正 burst_write() 测试函数中同样问题
```

### refactor — 重构

```
refactor(auth): 重构 AJAX 端点鉴权为 _require_admin_json() 模式

- 将 confirm/progress/api/year 系列共 8 个路由从 @admin_required 迁移
- _require_admin_json() 返回 JSON 401/403，杜绝 session 失效时 302 重定向
- 配合 main.js safeJSON() 检测 Content-Type，防止 HTML 泄漏到 response.json()
```

### perf — 性能优化

```
perf(models): 优化 get_db() 添加 PRAGMA 配置

- 添加 PRAGMA synchronous=NORMAL：WAL 模式下降低 fsync 频率，写入性能 2-5x
- 添加 PRAGMA busy_timeout=5000：写锁冲突等待 5 秒而非立即报错
- 添加 PRAGMA cache_size=-8000：分配 8MB 页面缓存，减少磁盘 I/O
```

### db — 数据库变更

```
db: 新增 attendance_records 表及联合作唯一索引

- 建表：id/id_card/attendance_date/status/update_time
- 唯一约束：(id_card, attendance_date)，防止同天同学生重复记录
- status 为 TEXT 类型（'出勤'/'缺勤'），非 INTEGER
- 索引：idx_attendance_date ON attendance_date，优化按日期查询性能
- upsert 使用 INSERT ... ON CONFLICT ... DO UPDATE
```

### test — 测试

```
test: 新增多用户并发压力测试脚本

- 覆盖 4 类场景：纯读/纯写/混合/压力冲刺，共 10 并发用户
- 统计锁冲突率/响应时间 P50/P95/P99/吞吐量/数据一致性
- 实测 981 ops 零失败，吞吐量 73 ops/s
- 验证 WAL 模式 + BEGIN IMMEDIATE 在 10 人场景下完全安全
```

### docs — 文档

```
docs: 更新 AGENT.MD 新增照片模块及按年份分组设计说明

- 补充 photos-xxx API 路由及 scope 说明
- 新增 year-filter scope 及 enrollment_year 与 grade 独立字段设计说明
- 更新开发注意事项第 28-33 条（表单弹窗重构/年级自动推导等）
```

### chore — 工具/依赖

```
chore: 升级托盘启动器，增加 Tkinter 启动完成弹窗

- run_with_tray.py 使用 make_server 管理 Flask 替代 app.run()
- 新增 Tkinter 弹窗显示"启动完成+访问地址"
- 增强局域网 IP 检测三层回退策略
```

### ui — 界面

```
ui(students): 重构学生表单为左右两栏布局

- 左栏 col-lg-9：表单主体（6 个分组，13px 粗体标签）
- 右栏 col-lg-3：照片预览卡片（120×156px 虚线框），sticky-top 固定
- 11 个必填项统一红色 * 标识
- 保存/取消按钮固定于右侧栏底部
```

### db — 数据库主键迁移

```
db(attendance-db): 迁移 attendance_records 主键 student_no 为 id_card

- 新增 migrate_attendance_id_card() 在 init_db() 中自动执行
- ADD COLUMN id_card → 从 students 表回填 → 重建唯一约束 → DROP 旧列
- status 列类型 INTEGER→TEXT，旧值 1→'出勤'、0→'缺勤'
- 手机端/查询/销假所有出勤操作同步适配 id_card
```

### feat — 销假处理

```
feat(leave-cancel): 新增销假处理页面及搜索销假 API

- 新增 /admin/leave-cancel 页面（独立 --lc-* CSS 设计系统）
- 4 个 JSON API：search-absences / search-realtime / search / confirm
- 输入学号/姓名搜索 → 缺勤列表 → 点击销假 → 确认弹窗 → 批量 upsert
- 缺勤行淡红底高亮，表头 sticky，空状态/加载中/无结果三态设计
- 在校状态「待销假」↔「在校」联动流转
```

### feat — 二维码生成

```
feat(qr-code): 销假页面集成手机端二维码生成工具

- 新增 /admin/api/mobile-qr（PNG）和 /admin/api/mobile-url（JSON）
- 依赖 qrcode[pil]>=7.4，utils.get_lan_ip() 三层回退检测局域网 IP
- 前端 240×240px 固定二维码 + 手动切换输入 URL
- 卡片 flex-grow 填充左侧栏，visibility:hidden 防页面抖动
```

### feat — 出勤导出

```
feat(export-attendance): 新增出勤明细对照表 Excel 导出功能

- GET /admin/leave-cancel/export-attendance 支持 ?start_date=&end_date=
- models.get_attendance_pivot_data() 透视查询返回 {dates, students, records}
- Excel 格式：固定列（学号/姓名/身份证号/校别/入学年）+ 日期列透视
- 状态着色（出勤=翠绿, 缺勤=淡红），freeze_panes 冻结信息列，auto_filter
- 文件名含日期范围（如 出勤明细_2026-05-30_至_2026-05-31_20260531_1700.xlsx）
```

### feat — 在校状态保护

```
feat(attendance-status): 强化在校状态更新，仅覆盖流水状态

- update_attendance_status_from_latest_attendance() 增加保护条件
- 仅当 attendance_status IN ('在校', '待销假') 时写入最新出勤状态
- 跳过托管/非在校/退学办结等非流水状态，防止误覆盖
- 新增 total_skipped 统计，flash 消息显示跳过人数及原因
- config 中 ATTENDANCE_STATUS 新增「待销假」
```

### ui — 表单精简

```
ui(students-form): 删除国网学籍号字段，精简表单布局

- 删除 national_no 输入框及后端 fieldMap 映射
- 合并「班级与学业」→「学籍信息」分组
- 合并「联系方式」→「家长信息」分组（电话同行）
- 学生电话与家长电话并排放同一行（col-6）
- 表单分组从 6 个精简约 4 个
```

### feat — 年级推导

```
feat(students-grade): 新增年级自动推导及转学生入学形式 grade 联动规则

- 新增时 grade 推导：初中毕业时间前4位 → enrollment_year → 当前年份
- admission_type='转学生' 时 grade = int(grade) - 1，enrollment_year 不变
- 编辑弹窗入学形式变更联动：转学生→其他 +1，其他→转学生 -1
- 原始 grade 从 openEditModal 加载的 window._originalGrade 获取
- 编辑 API 补充 grade/virtual_class 字段读取至 student_data 字典
```

### fix — 筛选面板

```
fix(students): 修复基础筛选隐藏面板无法展开/收起

- 原因：按钮 id="toggleMoreFilters" 与函数名冲突，DOM 遮盖全局函数
- 修复：id 改为 toggleMoreFiltersBtn，函数名保持不变
```

### ui — 表列扩充

```
ui(students): 学生列表表列扩充至16列，新增常用字段显示

- 新增列：身份证号、民族、入学年份、专业方向、虚拟班级
- 空数据行 colspan 从 11 同步更新为 16
```

### fix — 手机验证

```
fix(students-form): 新增学生弹窗添加手机号码前端验证功能

- edit_parent_phone/edit_student_phone 添加 input 事件实时验证
- saveEditStudent() 保存时拦截非法手机号并提示
- 输入框下方添加 invalid-feedback 视觉反馈
```

### feat — 拼音转换

```
feat(pinyin): 新增中文姓名拼音自动生成及存量回填功能

- 移植 name_tr 项目的 26671 条汉字→拼音映射字典（纯 Python 零依赖）
- 支持 27 个特殊姓氏（复姓优先匹配）及 49 个异体字/繁体转换
- 5 处自动生成触发点：弹窗新增/表单新增/Excel 导入/批量新增/编辑保存
- 新增 backfill_pinyin.py 独立命令行脚本（--force/--dry-run）
- 学生列表新增「姓名拼音」列（第 17 列）
```

### feat — 保险模块

```
feat(insurance): 新增保险管理模块（4页面+3角色+9字段+6API）

- 4 页面：保险缴费登记/保险审批/保险待办/保险初始化导入
- 3 角色：finance_chief（财务主管）/ finance_staff（财务人员）/ insurance_officer（保险员）
- 9 保险字段：学平险保单号/缴费日期/到期时间/保费金额/实习险缴费日期/主管标记/申报日期/保单更新日期/学费总体情况
- 业务流程：管理员初始化导入 → 财务人员缴费登记 → 财务主管审批 → 保险员办结
- 权限装饰器：finance_required / finance_chief_required / insurance_officer_required
- base.html 导航增强（admin 保险管理下拉菜单 + 保险角色独立导航）
```

## 禁止写法

```
❌ feat: 添加新功能
❌ fix: 修复了一些bug
❌ Update app.py
❌ Merge branch 'xxx'
❌ WIP
❌ fixbug
❌ [feat] 新增功能
```

## 提交粒度原则

1. **一个提交只做一件事**：功能添加/Bug 修复/工具升级/文档更新不混在一个提交
2. **小步提交**：每次提交逻辑完整、可独立部署
3. **批量修改先拆分**：一次改了多个 scope 或 type → 拆成多个提交
4. **优先推断 scope**：根据实际修改文件推断 scope，而非根据文件路径写 scope

## 自动推断规则（AI 辅助）

修改文件 → 推断 scope：

| 修改文件 | 推荐 scope |
|---------|-----------|
| `models.py` | `models` / `db` / `attendance-db`（涉及 attendance_records 表变更） |
| `app.py` | 对应功能 scope 或 `api` |
| `auth.py` | `auth` |
| `config.py` | `config` |
| `utils.py` | 对应功能（phone→`phone-validate`，id-card→`id-card-validate`，ip→`qr-code`）|
| `templates/admin/students.html` | `students` / `students-form` / `students-grade`（年级推导/转学生联动）/ `students`（删除按钮验证） |
| `templates/admin/student_change.html` | `student-change` / `transfer-pending`（转学待办表）⭐更新 |
| `templates/admin/student_dropout.html` | `student-dropout` ⭐新增 |
| `templates/admin/leave_cancel.html` | `leave-cancel` / `qr-code` / `export-attendance` |
| `templates/mobile/` | `mobile` / `attendance` |
| `services/card_reader.py` | `id-card` |
| `services/backup_service.py` | `backup` |
| `run_with_tray.py` | `tray` |
| `static/js/main.js` | `toast` / `auth` |
| `static/css/style.css` | `ui` |
| `requirements.txt` | `chore` / `qr-code`（qrcode[pil]） |
| `pinyin_utils.py` | `pinyin` ⭐新增 |
| `backfill_pinyin.py` | `pinyin` ⭐新增 |
| `templates/insurance/register.html` | `insurance-register` ⭐新增 |
| `templates/insurance/approve.html` | `insurance-approve` ⭐新增 |
| `templates/insurance/todo.html` | `insurance-todo` ⭐新增 |
| `templates/insurance/init.html` | `insurance-init` ⭐新增 |
| `templates/base.html` | `insurance`（保险导航菜单）/ `ui` / `graduation-query`（毕业生查询导航入口）⭐更新 |
| `templates/admin/graduation.html` | `graduation` ⭐新增 |
| `templates/admin/graduation_query.html` | `graduation-query` ⭐新增 |
| `templates/admin/export_engine.html` | `export` ⭐新增 |
| `data/templates/` | `export`（模板文件存储）⭐新增 |
| `data/exports/` | `export`（导出文件存储）⭐新增 |
| `.trae/rules/AGENT.MD` | `docs` |
| `.trae/rules/git-commit-message.md` | `docs` |
