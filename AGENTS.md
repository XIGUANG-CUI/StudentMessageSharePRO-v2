# AGENTS.md — 索引说明

> 本文件仅为入口索引，**不是**完整开发文档。
> 项目的权威开发文档已统一维护在 [`.trae/rules/AGENTS.MD`](.trae/rules/AGENTS.MD)（含项目结构、数据库表、路由一览、状态机、设计模式、86 条开发注意事项）。
> 用户手册见 [`README.md`](README.md)。
> 最后更新：2026-11-13

---

## 项目概述

基于 **Flask + SQLite + Bootstrap 5** 的学生数据管理系统（StudentMessageSharePRO-v2），桌面部署（PyInstaller + 系统托盘），覆盖学生学籍全生命周期：批量导入/更新、多维查询统计、手机端点名、区划匹配、毕业生档案派遣、重点监管、保险业务、模板导出（含预览）。

- **技术栈**：Python 3.10+ / Flask 3.x / SQLite（WAL）/ Bootstrap 5.3 / Chart.js / openpyxl / python-docx / docx-preview
- **启动**：`python run_with_tray.py`（推荐）或 `python app.py`，默认端口 `5001`
- **测试**：`python -m pytest tests/ -q`

## 文档地图

| 文档 | 内容 |
|---|---|
| [`.trae/rules/AGENTS.MD`](.trae/rules/AGENTS.MD) | 完整开发文档：项目结构、角色权限矩阵、数据库表结构、路由一览、保险状态机、设计模式、开发注意事项 |
| [`.trae/rules/git-commit-message.md`](.trae/rules/git-commit-message.md) | Git 提交信息规范（type/scope/示例） |
| [`.trae/rules/保险模块重构设计提案.md`](.trae/rules/保险模块重构设计提案.md) | 保险模块 v3 设计提案与 Phase 落地记录 |
| [`README.md`](README.md) | 面向使用者的系统操作手册（14 章 + 附录） |
| [`docs/`](docs/) | 专项设计与移植文档（区划匹配、学校类别升级、Linux 部署改造），**历史归档，不作为现行规范** |

## 代码结构速览

```
app.py                 应用入口（12 个蓝图注册、CSRF、安全响应头）
auth.py                7 种权限装饰器 + 字段/学校/年级过滤 + 脱敏
config.py              FIELD_MAPPING 50 项、InsuranceStatus 保险枚举
blueprints/            admin / api / archive / backup / district_match / export /
                       insurance / leave_cancel / lifecycle / mobile / public / user
models/                db / students / insurance / archive / district / lifecycle /
                       attendance / export_template / school_types / dicts / users / logs / stats
services/              card_reader / backup_service / import_service / insurance_service / district_match
templates/             admin / archive / insurance / user / mobile / public
static/vendor/         docx-preview.min.js / jszip.min.js（本地依赖，离线可用）
tests/                 pytest（保险、区划、重点监管、路由、工具）
```

## 关键约定（详见 `.trae/rules/AGENTS.MD`）

1. 前端禁用 `alert()` / `confirm()`，统一用 `showToast()` / `showConfirm()`。
2. JSON 接口必须用 `_require_admin_json()` / `_require_login_json()`，避免 302 重定向返回 HTML。
3. 学生删除为软删除（`is_deleted=1`），仅「退学办结」状态可删。
4. 保险数据以 `insurance_records` 为唯一事实来源，`students` 的 9 个保险字段由 `sync_student_snapshot()` 单向投影。
5. 保险办结标记统一 `YYYY年度学平险办结` / `YYYY年度实习就业险办结`（4 位年，取自批次学年）。
6. 模板改动需重启服务（Jinja 模板缓存，`debug=False`）。

## 维护说明

- 新增/修改功能时，**只更新 `.trae/rules/AGENTS.MD`**，本文件无需同步。
- 用户可见行为变化时，同步更新 `README.md` 对应章节与附录 B（导出文件命名）。
