import ctypes
import os
import logging
from ctypes import wintypes
from io import BytesIO
import base64
from datetime import datetime
import glob

logger = logging.getLogger(__name__)


class IDCardReader:

    def __init__(self, sdk_path=None, photos_dir=None):
        current_file_dir = os.path.abspath(os.path.dirname(__file__))
        self.project_root = os.path.abspath(os.path.join(current_file_dir, '..'))
        self.sdk_path = sdk_path or os.path.join(self.project_root, 'sdk')
        self.photos_dir = photos_dir or os.path.join(self.project_root, 'static', 'photos')
        self.dll_loaded = False
        self.connected = False

        os.makedirs(self.photos_dir, exist_ok=True)
        os.makedirs(self.sdk_path, exist_ok=True)

        self._load_dlls()

    def _find_dll(self, dll_name):
        """查找读卡器 SDK DLL 文件路径。 参数: dll_name"""
        name, ext = os.path.splitext(dll_name)
        pattern = os.path.join(self.sdk_path, f"{name}.*")
        files = glob.glob(pattern, recursive=False)
        for file in files:
            if file.lower().endswith('.dll'):
                return file
        return os.path.join(self.sdk_path, dll_name)

    def _load_dlls(self):
        """加载读卡器 SDK 的 DLL 库。"""
        try:
            required_dlls = ['termb.dll', 'sdtapi.dll', 'WltRS.dll']

            for dll in required_dlls:
                dll_path = self._find_dll(dll)
                if not os.path.exists(dll_path):
                    print(f"警告: 找不到DLL文件: {dll_path}")
                    self.dll_loaded = False
                    return

            dll_path = self._find_dll('termb.dll')
            self.dll = ctypes.WinDLL(dll_path)
            self._setup_functions()
            self.dll_loaded = True
            print(f"SDK加载成功: {dll_path}")

        except Exception as e:
            print(f"SDK加载失败: {e}")
            self.dll_loaded = False

    def _setup_functions(self):
        """配置 DLL 函数的参数和返回值类型。"""
        self.dll.CVR_InitComm.argtypes = [ctypes.c_int]
        self.dll.CVR_InitComm.restype = ctypes.c_int

        self.dll.CVR_CloseComm.argtypes = []
        self.dll.CVR_CloseComm.restype = ctypes.c_int

        self.dll.CVR_Authenticate.argtypes = []
        self.dll.CVR_Authenticate.restype = ctypes.c_int

        self.dll.CVR_Read_Content.argtypes = [ctypes.c_int]
        self.dll.CVR_Read_Content.restype = ctypes.c_int

        self.info_function_names = [
            'GetPeopleName', 'GetPeopleSex', 'GetPeopleNation',
            'GetPeopleBirthday', 'GetPeopleAddress', 'GetPeopleIDCode',
            'GetDepartment', 'GetStartDate', 'GetEndDate'
        ]

        if hasattr(self.dll, 'GetBMPData'):
            self.dll.GetBMPData.argtypes = [ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(ctypes.c_int)]
            self.dll.GetBMPData.restype = ctypes.c_int

        if hasattr(self.dll, 'GetJpgData'):
            self.dll.GetJpgData.argtypes = [ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(ctypes.c_int)]
            self.dll.GetJpgData.restype = ctypes.c_int

    def _get_string(self, func_name):
        """从 DLL 缓冲区读取字符串（GBK 解码）。 参数: func_name"""
        try:
            if not hasattr(self.dll, func_name):
                return ""
            func = getattr(self.dll, func_name)
            buffer = ctypes.create_string_buffer(256)
            length = ctypes.c_int(256)
            result = func(buffer, ctypes.byref(length))
            if result == 1:
                raw_data = buffer.raw
                null_pos = raw_data.find(b'\x00')
                if null_pos > 0:
                    data = raw_data[:null_pos]
                else:
                    data = raw_data[:length.value]
                try:
                    return data.decode('gbk').strip()
                except UnicodeDecodeError:
                    try:
                        return data.decode('utf-8', errors='ignore').strip()
                    except UnicodeDecodeError:
                        return str(data)
        except Exception as e:
            print(f"获取字符串失败 {func_name}: {e}")
        return ""

    def connect(self, port=1001):
        """连接读卡器设备。 参数: port"""
        if not self.dll_loaded:
            return False, "SDK未加载"
        try:
            result = self.dll.CVR_InitComm(port)
            if result == 1:
                self.connected = True
                return True, "设备连接成功"
            else:
                error_messages = {
                    2: "端口打开失败",
                    -1: "未找到读卡器",
                    -2: "动态库加载失败",
                }
                return False, error_messages.get(result, f"连接失败，错误码: {result}")
        except Exception as e:
            return False, f"连接异常: {str(e)}"

    def disconnect(self):
        """断开读卡器连接。"""
        if self.connected:
            try:
                self.dll.CVR_CloseComm()
                self.connected = False
            except Exception as e:
                logger.debug("读卡器断开连接失败（已忽略）: %s", e, exc_info=True)

    def authenticate_card(self):
        """读卡器认证（卡片防冲突）。"""
        if not self.connected:
            return False, "未连接设备"
        try:
            if hasattr(self.dll, 'CVR_Authenticate'):
                result = self.dll.CVR_Authenticate()
            elif hasattr(self.dll, 'CVR_AuthenticateForNoJudge'):
                result = self.dll.CVR_AuthenticateForNoJudge()
            else:
                return False, "找不到认证函数"
            if result == 1:
                return True, "卡认证成功"
            elif result == 2:
                return False, "寻卡失败，请放置身份证"
            else:
                return False, f"认证失败，错误码: {result}"
        except Exception as e:
            return False, f"认证异常: {str(e)}"

    def read_content(self):
        """读取身份证芯片内容。"""
        if not self.connected:
            return False, "未连接设备"
        try:
            result = self.dll.CVR_Read_Content(3)
            if result != 1:
                return False, f"读卡失败，错误码: {result}"

            card_info = {}
            info_mappings = [
                ('GetPeopleName', 'name'),
                ('GetPeopleSex', 'gender'),
                ('GetPeopleNation', 'nation'),
                ('GetPeopleBirthday', 'birthday'),
                ('GetPeopleAddress', 'address'),
                ('GetPeopleIDCode', 'id_card'),
                ('GetDepartment', 'issue_authority'),
                ('GetStartDate', 'valid_start'),
                ('GetEndDate', 'valid_end'),
            ]

            for func_name, key in info_mappings:
                value = self._get_string(func_name)
                if value:
                    card_info[key] = value

            if 'birthday' in card_info:
                card_info['birthday'] = self._format_date(card_info['birthday'])
            if 'valid_start' in card_info:
                card_info['valid_start'] = self._format_date(card_info['valid_start'])
            if 'valid_end' in card_info:
                card_info['valid_end'] = self._format_date(card_info['valid_end'])

            photo_data = self._get_photo_data()
            if photo_data:
                card_info['photo_data'] = photo_data
                card_info['photo_base64'] = self._photo_to_base64(photo_data)

            return True, card_info

        except Exception as e:
            return False, f"读取失败: {str(e)}"

    def read_id_card(self, year=None):
        """读取身份证信息（含照片），返回结构化数据。 参数: year"""
        if not self.dll_loaded:
            return False, "SDK未加载，请检查DLL文件"
        try:
            success, message = self.connect()
            if not success:
                return False, message

            success, message = self.authenticate_card()
            if not success:
                self.disconnect()
                return False, message

            success, result = self.read_content()
            if not success:
                self.disconnect()
                return False, result

            id_card = result.get('id_card', '')
            if not id_card:
                self.disconnect()
                return False, "未读取到身份证号码"

            photo_data = result.pop('photo_data', None)

            if photo_data:
                photo_path = self.save_photo_raw(photo_data, id_card, year=year)
                if photo_path:
                    result['photo_path'] = photo_path
                    result['photo_url'] = '/' + os.path.relpath(photo_path, self.project_root).replace('\\', '/')

            self.disconnect()
            return True, result

        except Exception as e:
            self.disconnect()
            return False, f"读取异常: {str(e)}"

    def save_photo_raw(self, photo_data, id_card, year=None):
        """保存原始照片数据到文件。 参数: photo_data, id_card, year"""
        if not photo_data:
            return None
        try:
            ext = self._detect_image_format(photo_data)
            safe_id = ''.join(c for c in str(id_card) if c.isalnum())
            filename = f"{safe_id}{ext}"

            if year is None and id_card:
                # 全校视图时，从数据库查找学生的入学年份
                try:
                    from models import get_db
                    db = get_db()
                    row = db.execute(
                        'SELECT enrollment_year FROM students WHERE id_card = ?', (id_card,)
                    ).fetchone()
                    if row and row['enrollment_year']:
                        year = int(row['enrollment_year'])
                    db.close()
                except Exception as e:
                    logger.debug("保存照片-查询入学年份失败，降级存到根目录: %s", e, exc_info=True)

            if year:
                year_dir = os.path.join(self.photos_dir, str(year))
                os.makedirs(year_dir, exist_ok=True)
                filepath = os.path.join(year_dir, filename)
            else:
                filepath = os.path.join(self.photos_dir, filename)

            if os.path.exists(filepath):
                os.remove(filepath)

            with open(filepath, 'wb') as f:
                f.write(photo_data)

            print(f"照片保存成功: {filepath}")
            return filepath
        except Exception as e:
            print(f"保存照片失败: {e}")
            return None

    def _detect_image_format(self, photo_data):
        """检测照片数据的图像格式（JPG/BMP）。 参数: photo_data"""
        if len(photo_data) >= 2:
            if photo_data[:2] == b'\xff\xd8':
                return '.jpg'
            elif photo_data[:2] == b'BM':
                return '.bmp'
            elif photo_data[:4] == b'\x89PNG':
                return '.png'
        if len(photo_data) == 38862:
            return '.bmp'
        return '.jpg'

    def _photo_to_base64(self, photo_data):
        """将照片数据转为 base64 编码字符串。 参数: photo_data"""
        if not photo_data:
            return None
        try:
            return f"data:image/jpeg;base64,{base64.b64encode(photo_data).decode('utf-8')}"
        except Exception as e:
            print(f"转换Base64失败: {e}")
            return None

    def _get_photo_data(self):
        """获取照片数据（优先 JPG，降级 BMP）。"""
        photo_data = None
        if hasattr(self.dll, 'GetJpgData'):
            photo_data = self._get_photo_jpg()
        if not photo_data and hasattr(self.dll, 'GetBMPData'):
            photo_data = self._get_photo_bmp()
        return photo_data

    def _get_photo_jpg(self):
        """从读卡器获取 JPG 格式照片数据。"""
        try:
            buffer_size = 50000
            buffer = (ctypes.c_ubyte * buffer_size)()
            length = ctypes.c_int(buffer_size)
            result = self.dll.GetJpgData(buffer, ctypes.byref(length))
            if result == 1 and length.value > 0:
                return bytes(buffer[:length.value])
        except Exception as e:
            logger.debug("读取JPG照片数据失败，降级返回None: %s", e, exc_info=True)
        return None

    def _get_photo_bmp(self):
        """从读卡器获取 BMP 格式照片数据。"""
        try:
            buffer_size = 40000
            buffer = (ctypes.c_ubyte * buffer_size)()
            length = ctypes.c_int(buffer_size)
            result = self.dll.GetBMPData(buffer, ctypes.byref(length))
            if result == 1 and length.value > 0:
                return bytes(buffer[:length.value])
        except Exception as e:
            logger.debug("读取BMP照片数据失败，降级返回None: %s", e, exc_info=True)
        return None

    def _format_date(self, date_str):
        """格式化读卡器返回的日期字符串。 参数: date_str"""
        if not date_str or len(date_str) != 6:
            return date_str
        try:
            year_part = date_str[:2]
            month = date_str[2:4]
            day = date_str[4:6]
            year_int = int(year_part)
            current_year = datetime.now().year
            century = (current_year // 100) * 100
            cutoff = current_year % 100
            if year_int <= cutoff:
                year = century + year_int
            else:
                year = century - 100 + year_int
            return f"{year:04d}-{month}-{day}"
        except (ValueError, TypeError):
            return date_str


_id_card_reader = None


def get_id_card_reader(sdk_path=None, photos_dir=None):
    """获取id身份证读卡器。 参数: sdk_path, photos_dir"""
    global _id_card_reader
    if _id_card_reader is None or (sdk_path and _id_card_reader.sdk_path != sdk_path):
        _id_card_reader = IDCardReader(sdk_path=sdk_path, photos_dir=photos_dir)
    return _id_card_reader
