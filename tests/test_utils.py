# -*- coding: utf-8 -*-
"""utils.py 工具函数测试。"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import (
    natural_key, extract_birth_date_from_id_card, calculate_age,
    validate_id_card, validate_phone, generate_random_password,
    allowed_file, generate_unique_filename, school_visible_contains,
    get_school_type_name, is_private_ip,
)


class TestNaturalKey:
    """自然排序键测试。"""

    def test_natural_order(self):
        """班级2 < 班级10（自然排序，非字典序）。"""
        assert natural_key('班级2') < natural_key('班级10')
        assert natural_key('班级10') > natural_key('班级2')

    def test_empty_string(self):
        assert natural_key('') == ['']

    def test_none(self):
        assert natural_key(None) == ['']

    def test_no_digits(self):
        assert natural_key('abc') == ['abc']

    def test_leading_digit(self):
        assert natural_key('1班') == ['', 1, '班']

    def test_sort_list(self):
        names = ['班级10', '班级2', '班级1', '班级20']
        assert sorted(names, key=natural_key) == ['班级1', '班级2', '班级10', '班级20']


class TestExtractBirthDate:
    """身份证提取生日测试。"""

    def test_valid_id_card(self):
        assert extract_birth_date_from_id_card('110101199003073456') == '1990-03-07'

    def test_invalid_length(self):
        assert extract_birth_date_from_id_card('123') is None

    def test_empty(self):
        assert extract_birth_date_from_id_card('') is None
        assert extract_birth_date_from_id_card(None) is None

    def test_invalid_date(self):
        """无效日期（13月）应返回 None。"""
        assert extract_birth_date_from_id_card('110101199013073456') is None


class TestCalculateAge:
    """年龄计算测试。"""

    def test_returns_string(self):
        result = calculate_age('1990-03-07')
        assert result is not None
        assert '岁' in result
        assert '个月' in result

    def test_empty(self):
        assert calculate_age('') is None
        assert calculate_age(None) is None

    def test_invalid_format(self):
        assert calculate_age('invalid-date') is None


class TestValidateIdCard:
    """身份证校验测试。"""

    def test_valid(self):
        # 110101199003073458 — 校验码 8（total%11=4 → check_codes[4]='8'）
        assert validate_id_card('110101199003073458') is True

    def test_invalid_length(self):
        assert validate_id_card('123') is False

    def test_empty(self):
        assert validate_id_card('') is False
        assert validate_id_card(None) is False

    def test_wrong_checksum(self):
        # 最后一位改为 0（原校验码是 6）
        assert validate_id_card('110101199003073450') is False


class TestValidatePhone:
    """手机号校验测试。"""

    def test_valid_mobile(self):
        assert validate_phone('13800138000') is True

    def test_invalid_short(self):
        assert validate_phone('123') is False

    def test_empty(self):
        # 空字符串视为有效（允许不填手机号）
        assert validate_phone('') is True


class TestPassword:
    """密码生成测试。"""

    def test_length(self):
        pwd = generate_random_password()
        assert len(pwd) >= 8

    def test_uniqueness(self):
        pwd1 = generate_random_password()
        pwd2 = generate_random_password()
        assert pwd1 != pwd2


class TestFileUtils:
    """文件工具测试。"""

    def test_allowed_file(self):
        assert allowed_file('test.xlsx') is True
        assert allowed_file('test.xls') is True
        assert allowed_file('test.csv') is True
        assert allowed_file('test.txt') is False

    def test_unique_filename(self):
        name1 = generate_unique_filename('test.xlsx')
        name2 = generate_unique_filename('test.xlsx')
        assert name1 != name2
        assert name1.endswith('.xlsx')


class TestSchoolVisible:
    """学校可见性测试。"""

    def test_admin_wildcard(self):
        assert school_visible_contains('*', 1) is True
        assert school_visible_contains('*', 2) is True

    def test_empty_means_all(self):
        assert school_visible_contains('', 1) is True
        assert school_visible_contains(None, 1) is True

    def test_single_school(self):
        assert school_visible_contains('1', 1) is True
        assert school_visible_contains('1', 2) is False

    def test_multiple_schools(self):
        assert school_visible_contains('1,2', 1) is True
        assert school_visible_contains('1,2', 2) is True
        assert school_visible_contains('1,2', 3) is False

    def test_with_spaces(self):
        assert school_visible_contains(' 1 , 2 ', 1) is True


class TestSchoolTypeName:
    """学校类别名称测试。"""

    def test_known_types(self):
        assert get_school_type_name(1) is not None
        assert get_school_type_name(2) is not None


class TestPrivateIP:
    """私有 IP 判断测试。"""

    def test_private_10(self):
        assert is_private_ip('10.0.0.1') is True

    def test_private_172(self):
        assert is_private_ip('172.16.0.1') is True

    def test_private_192(self):
        assert is_private_ip('192.168.1.1') is True

    def test_public(self):
        assert is_private_ip('8.8.8.8') is False

    def test_loopback(self):
        assert is_private_ip('127.0.0.1') is True
