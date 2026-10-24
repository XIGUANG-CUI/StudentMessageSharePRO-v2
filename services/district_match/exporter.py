"""匹配结果导出（xlsx / csv），含公式注入防护。"""
import csv
import json

EXPORT_COLUMNS = [
    ("student_name", "学生姓名"),
    ("id_card", "身份证号"),
    ("address", "地址"),
    ("province_code", "省代码"),
    ("province_name", "省"),
    ("city_code", "市代码"),
    ("city_name", "市"),
    ("district_code", "区县代码"),
    ("district_name", "区县"),
    ("match_status", "状态"),
    ("engine1_result", "引擎①过程"),
    ("engine2_result", "引擎②过程"),
    ("remark", "说明"),
]

# 上传清单导出：匹配结果追加在原始表单列之后（前缀“匹配_”避免与原始列重名）
MATCH_COLUMNS = [
    ("province_code", "匹配_省代码"),
    ("province_name", "匹配_省"),
    ("city_code", "匹配_市代码"),
    ("city_name", "匹配_市"),
    ("district_code", "匹配_区县代码"),
    ("district_name", "匹配_区县"),
    ("match_status", "匹配_状态"),
    ("remark", "匹配_说明"),
]

_FORMULA_PREFIX = ("=", "+", "-", "@", "\t", "\r")


def _safe_cell(value):
    """公式注入防护：以 = + - @ 等开头的文本前置单引号。"""
    if value is None:
        return ""
    text = str(value)
    if text.startswith(_FORMULA_PREFIX):
        return "'" + text
    return text


def _raw_row(row):
    raw = row.get("raw_row")
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _build_matrix(rows, headers):
    """返回 (表头, 数据行)。

    headers 非空时（上传清单）：原始表单列按原顺序在前，匹配结果列追加在后；
    否则（取学生）输出固定的 EXPORT_COLUMNS。
    """
    if not headers:
        titles = [title for _key, title in EXPORT_COLUMNS]
        data = [[_safe_cell(row.get(key)) for key, _title in EXPORT_COLUMNS]
                for row in rows]
        return titles, data

    titles = list(headers) + [title for _key, title in MATCH_COLUMNS]
    data = []
    for row in rows:
        raw = _raw_row(row)
        values = [_safe_cell(raw.get(header)) for header in headers]
        values.extend(_safe_cell(row.get(key)) for key, _title in MATCH_COLUMNS)
        data.append(values)
    return titles, data


def export_results(rows, out_path, fmt="xlsx", headers=None):
    """把匹配结果写成 xlsx / csv，返回 out_path。

    rows: dict 列表，键见 EXPORT_COLUMNS（缺键按空字符串输出）。
    headers: 上传清单的原始表头；提供时保留原始列并把匹配结果追加在最后。
    """
    ext = (fmt or "").lower().lstrip(".")
    titles, data = _build_matrix(rows, [h for h in (headers or []) if h])
    if ext in ("xlsx", "xlsm"):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "区划匹配结果"
        ws.append(titles)
        for values in data:
            ws.append(values)
        wb.save(out_path)
        return out_path

    if ext in ("csv", "tsv"):
        delimiter = "\t" if ext == "tsv" else ","
        with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f, delimiter=delimiter)
            writer.writerow(titles)
            writer.writerows(data)
        return out_path

    raise ValueError("仅支持导出 xlsx / csv")
