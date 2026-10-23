# 学校类别字典化升级方案

> 适用项目：StudentMessageSharePRO-v2
> 状态：待实施
> 执行方式：先只实施 Phase 0 + Phase 1，完成并验证后，等待确认再进入 Phase 2 及之后阶段。

## 1. 背景与目标

当前数据库 `students.school` 使用整数编号表示学校类别，代码中大量硬编码 `1=中职`、`2=技工`。目标是让学校类别可以通过"字典"在后台维护，后续可新增 3、4、5 等类别，同时保证现有 1/2 的数据和业务行为完全不变。

## 2. 已确认的决策

1. **新建 `school_types` 专表**，不扩展通用 `dicts` 表，也不修改 `students.school` / `users.school_visible` 的存储方式。
2. **编码继续使用整数 1..N**，编码稳定、不复用；新增时默认建议 `max(code)+1`。
3. **停用 + 禁止删除被引用**：新增 `is_active` 字段；被学生（含回收站学生）或用户权限引用的类别只能停用，不能物理删除。
4. **学校类别仅在字典页面维护**，不做 Excel 批量导入/导出；通用字典的导入导出逻辑保持不变。
5. **权限保持显式授权**：新增类别不自动授予普通用户；建议自动追加到 admin 角色，避免管理员看不到新类别数据。
6. **3/4/5 完全复用现有业务流程**：保险、毕业、转学、出勤等逻辑不按学校类别分支。
7. 在现有"字典"页面增加「学校类别」页签，作为学校类别的管理入口。

## 3. 现有硬编码清单（改造范围摘要）

### 3.1 配置与名称映射

- `config.py`：`SCHOOL_TYPES = {1: '中职', 2: '技工'}`，保留为种子和数据库异常时的兜底。
- `utils.py`：`get_school_type_name()` 改为读取服务层。
- `models/stats.py`：SQL 中的 `CASE school WHEN 1 ... ELSE '未知'` 需要去掉，改为 Python 端映射。
- `models/students.py`：已删除学生列表中的学校 `CASE` 同样需要改为 Python 端映射。
- `blueprints/export.py`、`blueprints/admin.py`：所有 `config.SCHOOL_TYPES` 运行时读取改为服务层映射。

### 3.2 权限默认值与可见性

- `models/db.py`：`users.school_visible DEFAULT '1,2'`。
- `models/users.py`、`app.py`：创建用户/初始 admin 的默认 `'1,2'`。
- `auth.py`：session 与权限过滤的多处 `'1,2'` fallback。
- `blueprints/api.py`、`blueprints/user.py`：多处 `session.get('school_visible', '1,2')`。
- `templates/admin/users.html`：新增/编辑用户只有两个学校勾选框，JS 只回填 `editSchool1/editSchool2`。
- 注意：`'*'` 语义目前不统一，`get_filter_values()` 对 `'*'` 会执行 `int('*')` 报错，`user/stats.html` 也不会为 `'*'` 渲染按钮。改造时需要统一处理。

### 3.3 模板静态选项/按钮

- `templates/admin/students.html`：顶部筛选按钮、编辑弹窗学校下拉、学校选择弹窗。
- `templates/admin/student_form.html`：新增/编辑表单学校下拉。
- `templates/admin/student_change.html`：转学确认弹窗、前端学校标签三元表达式。
- `templates/admin/graduation_query.html`：筛选下拉、`SCHOOL_LABELS`、CSV 导出标签。
- `templates/user/query.html`：学校下拉，且页面要求必须先选学校才加载。
- `templates/admin/dashboard.html`、`templates/user/stats.html`：学校筛选按钮。
- `templates/admin/export_engine.html`：筛选下拉与 `valueMap`。
- `templates/admin/users.html`：学校权限勾选框。

### 3.4 保险模块

- `blueprints/insurance.py`：`by_school = {1, 2}`、`grand_total` 按 `[1,2]` 求和；`schools = {1: [], 2: []}`。
- `templates/insurance/todo.html`：静态的中职/技工批量按钮、`schoolKeys = [1, 2]`、`schoolName()`。
- `templates/insurance/register.html`、`templates/insurance/approve.html`：学校名称映射写死。

### 3.5 导入与专业推导

- `utils.py`：学生导入校验白名单 `['1', '2']`；导入模板说明与示例数据写死 1/2。
- `blueprints/admin.py`：导入落库 `int(...)`、表单新增默认 `school='1'`。
- `utils.py`：`_derive_school()` 通过"该专业已有学生所属学校"反推，限制结果必须是启用类别；同一专业跨学校时存在歧义，学校选择应允许人工指定。

### 3.6 文档与测试

- `AGENTS.md`、`.trae/rules/AGENTS.MD`、`README.md` 中的学校枚举说明。
- `tests/test_utils.py` 需要补充 `school=3` 的回归用例。

## 4. 目标设计

### 4.1 表结构

在 `models/db.py` 的初始化逻辑中新增：

```sql
CREATE TABLE IF NOT EXISTS school_types (
    code INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

要求：

- `code` 手动指定，不使用 AUTOINCREMENT。
- `name` 唯一。
- 仅当表为空时，从 `config.SCHOOL_TYPES` 种子化 1/2，避免把管理员主动删除的类别重新加回。
- 不建外键，引用校验在服务层完成。

### 4.2 服务层

新增 `models/school_types.py`，至少包含：

- `get_school_types(active_only=False)`：按 code 排序返回列表。
- `get_school_type_map(active_only=False)`：返回 `{code: name}`。
- `get_school_type_name(code)`：单个名称查询，数据库异常时回退 `config.SCHOOL_TYPES`。
- `create_school_type(code, name)`：校验正整数编码、名称唯一。
- `update_school_type(code, name=None, is_active=None)`：改名、停用、启用。
- `delete_school_type(code)`：仅未被引用时允许删除，否则提示改用停用。
- `seed_school_types()`：表为空时写入默认 1/2。

引用校验必须覆盖：

- `students.school = code`，包含 `is_deleted=1` 的回收站学生。
- `users.school_visible` 逗号列表包含该 code，使用 `',' || REPLACE(school_visible, ' ', '') || ',' LIKE '%,3,%'` 这类方式，避免 `13` 误匹配 `3`。

### 4.3 展示层

- 在 `app.py` 增加 context processor，向所有模板注入 `school_types`。
- 在 `templates/base.html` 注入 `window.SCHOOL_TYPES`，前端统一使用一个 `schoolName(code)` helper。
- 所有静态学校选项改为循环渲染。
- 编辑一个已停用的学校类别时，下拉仍需保留该选项并标注"已停用"，避免旧学生编辑时被清空。

停用后的显示规则：

- 新增/编辑可选列表只显示启用类别。
- 筛选下拉保留有历史数据的停用类别。
- 历史数据标签始终能解析。

### 4.4 权限

- `users.school_visible` 继续存编码 CSV。
- 用户管理页的学校勾选框从 `school_types` 动态生成。
- 新增用户默认勾选所有启用类别；编辑时按数据库值回填。
- 新类别创建后不自动授予普通用户；建议自动追加到 `role='admin'` 用户。
- 修改自己的权限后，需要同步 session 或提示重新登录。
- 统一 `'*'` 语义，二选一：完整支持 `'*'`，或统一改为显式编码集合。

### 4.5 导入

- `utils.py` 的学生导入白名单改为"编码存在于 `school_types` 且启用"。
- 导入模板说明、示例数据动态生成。
- 表单新增学生时学校必须显式选择，不再默认 `1`。

### 4.6 保险

- `blueprints/insurance.py` 的 `by_school`、`grand_total`、`schools` 改为按实际类别动态构建。
- `templates/insurance/todo.html` 的批量按钮按类别动态生成，事件使用委托绑定。

## 5. 分阶段实施

| 阶段 | 内容 | 完成标志 |
|---|---|---|
| Phase 0 | 建表、种子、服务层、context processor | 现有 1/2 行为完全不变，测试通过 |
| Phase 1 | 字典页「学校类别」页签 + 新增/改名/停用/删除保护 | 可新增 3，停用和删除保护生效 |
| Phase 2 | 去掉 SQL CASE，统一名称映射 | 新增 3 后统计、导出不再显示"未知" |
| Phase 3 | 学生/查询/统计/毕业/导出引擎/用户权限模板动态化 | 3 类学生可录入、筛选、授权 |
| Phase 4 | 保险分组与批量按钮动态化 | 保险三页能看到 3 类 |
| Phase 5 | 导入校验、模板说明、专业推导兜底 | 学校类型填 3 可正常导入 |
| Phase 6 | 测试与文档 | `school=3` 全链路回归通过 |

## 6. 验收标准

1. 现有 1/2 数据、筛选、统计、导出、保险、权限行为不变。
2. 可在字典页新增 `code=3` 的学校类别，并出现在学生筛选、学生编辑、用户权限、毕业查询、导出引擎、保险页面。
3. 非 admin 用户且 `school_visible` 不含 3 时，看不到 3 的数据。
4. admin 可给用户授予 3 的可见权限。
5. 停用 3 后，新增/编辑下拉不再显示 3，但历史学生仍显示"3 的名称（已停用）"。
6. 有学生或用户引用 3 时，删除被阻止并提示改用停用。
7. 学生导入时学校类型填 3 可以正常通过校验。
8. 仪表盘与导出中的学校名称正确，不出现"未知"或空值。
9. 保险登记/审批/待办页面能看到并正确处理 3 类学生。
10. 新增测试覆盖 `school=3` 的导入、筛选、权限、统计、保险分组。

## 7. 约束

- 不修改 `students.school` 和 `users.school_visible` 的存储格式。
- 不把 SDK 二进制、许可证、数据库、照片、日志提交到 Git。
- 遵循 `.trae/rules/git-commit-message.md` 的提交规范。
- 每个阶段结束运行 `pytest` 和必要的 `python -m py_compile`。
- 不一次性改动全站；按 Phase 顺序推进，每阶段可独立验证。

## 8. 第一阶段执行指令（给 AI Agent）

本轮只执行 **Phase 0 + Phase 1**：

1. 新增 `school_types` 表、种子逻辑和服务层。
2. 在字典页面增加「学校类别」页签及 CRUD：
   - 新增（编码自动建议 `max(code)+1`，允许手工修改）
   - 改名
   - 停用/启用
   - 删除（仅未被引用时允许）
3. 注入 `school_types` 到模板上下文，但**暂不修改**学生、权限、保险、导入等其他页面。
4. 保证现有 1/2 行为完全不变。
5. 完成后运行测试，汇报：
   - 改动文件清单
   - 新增表结构与种子结果
   - 验证结果（现有 1/2 是否无变化、新增 3 是否成功、停用/删除保护是否生效）
6. 等待确认后再进入 Phase 2。
