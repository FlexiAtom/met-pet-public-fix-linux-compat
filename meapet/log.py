"""日志模块，提供彩色控制台+按天滚动文件日志"""
import ctypes
import logging
import logging.handlers
import os
import re
import sys

# ====================== 全局配置项 ======================
CONSOLE_LOG_LEVEL = "TRACE"    # 控制台默认 INFO；TRACK 载荷日志需显式调到 TRACE 或 MEA_PET_DEBUG=1
# 文件默认 INFO：DEBUG 与 TRACE（含用户聊天/长期记忆等隐私载荷）默认不落盘，
# 需排障时可设环境变量 MEA_PET_DEBUG=1 临时提升。
FILE_LOG_LEVEL = os.environ.get("MEA_PET_FILE_LOG_LEVEL", "").strip().upper() or "INFO"

# 日志路径：打包便携模式下与配置/缓存同在 get_data_dir()（_internal）。
from meapet.paths import get_data_dir as _get_data_dir

LOG_DIR = os.path.join(_get_data_dir(), "logs")
LOG_KEEP_DAYS = 7            # 保留最近几天的日志文件
# ====================================================

# ====================== 工具函数 ======================
def enable_vt():
    """开启 stdout 和 stderr 的 VT 转译支持 自动判断平台"""
    if sys.platform != 'win32':
        return True

    kernel32 = ctypes.windll.kernel32
    ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004

    # logging 默认输出到 stderr(-12)，但也可能配置为 stdout(-11)
    # 必须同时开启两个流的 VT 支持
    handles = [-11, -12]
    success_count = 0

    for handle_id in handles:
        try:
            handle = kernel32.GetStdHandle(handle_id)
            mode = ctypes.c_ulong()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                new_mode = mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
                if kernel32.SetConsoleMode(handle, new_mode):
                    success_count += 1
        except Exception:
            pass

    return success_count > 0


# ---------- 自定义 namer：把后缀式日期改为插入式 ----------
def _daily_namer(default_name):
    base, ext = os.path.splitext(default_name)   # base=logs/app.log  ext=.2026-07-13
    date_part = ext.lstrip('.')                   # 2026-07-13
    # 再拆一次，把 .log 分离出来
    root, real_ext = os.path.splitext(base)       # root=logs/app  real_ext=.log
    return f"{root}.{date_part}{real_ext}"


class ColorFormatter(logging.Formatter):
    """控制台彩色格式化器"""

    LEVEL_COLORS = {
        'TRACE':    '\033[37m',   # 灰色
        'DEBUG':    '\033[36m',   # 青色
        'INFO':     '\033[32m',   # 绿色
        'WARNING':  '\033[33m',   # 黄色
        'WARN':     '\033[33m',   # 黄色
        'ERROR':    '\033[31m',   # 红色
    }
    RESET  = '\033[0m'
    PURPLE = '\033[35m'
    NAME   = '\033[34m'  # 暗蓝色

    # 匹配消息中的 [xxx]（方括号内非空且不含方括号）
    BRACKET_RE = re.compile(r'\[[^\[\]]+\]')

    def format(self, record):
        try:
            level_color = self.LEVEL_COLORS.get(record.levelname, '')
            reset = self.RESET

            asctime = self.formatTime(record, self.datefmt)
            msg = record.getMessage()

            # 对消息中的 [xxx] 加紫色
            msg = self.BRACKET_RE.sub(
                lambda m: f'{self.PURPLE}{m.group(0)}{reset}',
                msg
            )

            colored_time  = f'{level_color}{asctime}{reset}'
            colored_level = f'{level_color}[{record.levelname}]{reset}'
            colored_name  = f'{self.NAME}[{record.name}]{reset}'

            result = f'{colored_time} {colored_level} {colored_name} {msg}'

            if record.exc_info and not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
            if record.exc_text:
                result = f'{result}\n{record.exc_text}'

            return result

        except Exception:
            return f"{self.formatTime(record, self.datefmt)} [{record.levelname}] {record.getMessage()}"


# ====================== 自定义 Logger 类 ======================
# 数值 5 < DEBUG(10)，确保只有显式设置为 TRACE 时才会输出
TRACE = 5
logging.addLevelName(TRACE, "TRACE")

class ColorLogger(logging.Logger):
    """支持 TRACE 级别的自定义 Logger"""

    def trace(self, message, *args, **kwargs):
        """
        载荷级调试追踪。仅在 logger 级别 <= TRACE 时输出。
        支持 %s 惰性格式化；若需 f-string 惰性求值，请传入 lambda。
        """
        if self.isEnabledFor(TRACE):
            if callable(message):
                try:
                    message = message()
                except Exception as e:
                    message = f"[LOG ERROR] TRACE BUILD ERROR: {e}"
            self._log(TRACE, message, args, **kwargs)

# 注册自定义 Logger 类，须在 getLogger 之前调用
logging.setLoggerClass(ColorLogger)


# ====================== 文本附件日志脱敏 ======================
class TextAttachmentSanitizer(logging.Filter):
    """
    日志过滤器：将日志记录中附带的 TextAttachment 对象序列化为脱敏摘要。

    脱敏规则（对齐草案"日志需要脱敏"）：
    - 仅保留 file_name / char_count / sha256 前 8 位
    - 绝不记录 text_content（文件全文）
    """

    def filter(self, record):
        # 处理 record 上直接挂载的 text_attachments 属性（列表/元组）
        atts = getattr(record, "text_attachments", None)
        if atts:
            try:
                record.text_attachments = [
                    self._summarize(a) for a in atts
                ]
            except Exception:
                record.text_attachments = "<sanitize-error>"
        return True

    @staticmethod
    def _summarize(att) -> dict:
        """从 TextAttachment 或 dict 提取脱敏摘要。"""
        # 数据类路径：TextAttachment(file_name, text_content, char_count, sha256_hash)
        name = getattr(att, "file_name", None)
        chars = getattr(att, "char_count", None)
        sha = getattr(att, "sha256_hash", None)
        # dict 路径（兼容序列化后的字典）
        if name is None and isinstance(att, dict):
            name = att.get("file_name")
        if chars is None and isinstance(att, dict):
            chars = att.get("char_count")
        if sha is None and isinstance(att, dict):
            sha = att.get("sha256") or att.get("sha256_hash")
        return {
            "file_name": name,
            "char_count": chars,
            "sha256": (sha[:8] + "…") if isinstance(sha, str) and len(sha) >= 8 else sha,
        }


def log_text_attachments(logger, level, attachments, extra_msg=""):
    """
    以脱敏方式记录文本附件摘要。

    :param logger: logging.Logger 实例
    :param level: 日志级别字符串（"INFO"/"DEBUG"/"WARNING" 等）
    :param attachments: TextAttachment 列表/元组
    :param extra_msg: 附加说明（如 "用户附加 2 个文件"）
    使用示例：
        log_text_attachments(log, "INFO", self._text_attachments,
                             extra_msg="聊天提交")
    """
    if not attachments:
        return
    summaries = [TextAttachmentSanitizer._summarize(a) for a in attachments]
    total_chars = sum((s.get("char_count") or 0) for s in summaries)
    log_fn = getattr(logger, level.lower(), logger.info)
    log_fn(
        f"[text_attachment] {extra_msg} files={len(summaries)} "
        f"total_chars={total_chars} items={summaries}"
    )


# ====================== get_color_logger ======================
def get_color_logger(name="app", log_dir=LOG_DIR, keep_days=LOG_KEEP_DAYS,
                     console_level=None, file_level=None, enable_file=True):
    """
    获取带彩色控制台输出和文件写入的日志记录器
    首次配置后，同名 Logger 会复用 Handler，同名 Logger 初始化后复用时不支持重新配置

    :param name:          logger 名称
    :param log_dir:       日志文件目录
    :param keep_days:     保留天数
    :param console_level: 控制台输出级别（字符串），None 则使用全局 CONSOLE_LOG_LEVEL
    :param file_level:    文件输出级别（字符串），None 则使用全局 FILE_LOG_LEVEL
    :param enable_file:   是否启用文件输出
    :return:              logging.Logger 实例
    """
    # ---------- 确定级别 ----------
    if console_level is None:
        console_level = CONSOLE_LOG_LEVEL
    if file_level is None:
        file_level = FILE_LOG_LEVEL

    level_map = {
        "TRACE": TRACE,
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARN": logging.WARNING,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
    }
    console_level_num = level_map.get(console_level.upper(), logging.INFO)
    file_level_num = level_map.get(file_level.upper(), logging.INFO)

    logger = logging.getLogger(name)
    logger.setLevel(TRACE)

    # 仅在没有 Handler 时才进行配置
    if not logger.handlers:
        # 控制台彩色 Handler。窗口化打包下 sys.stderr 为 None，此时
        # StreamHandler 每条记录都会在 emit 里抛异常再被吞掉，直接跳过。
        if sys.stderr is not None:
            console_handler = logging.StreamHandler()
            console_handler.setLevel(console_level_num)
            console_formatter = ColorFormatter(
                "%(asctime)s [%(levelname)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"
            )
            console_handler.setFormatter(console_formatter)
            logger.addHandler(console_handler)

        # 文件 Handler
        if enable_file:
            # 只读安装目录（如 C:\Program Files）下建目录会抛 PermissionError。
            # 日志系统自身绝不能阻断启动，失败就只保留控制台 Handler。
            try:
                os.makedirs(log_dir, exist_ok=True)
            except OSError:
                return logger
            log_path = os.path.join(log_dir, f"{name}.log")

            try:
                file_handler = logging.handlers.TimedRotatingFileHandler(
                    filename=log_path,
                    when='midnight',
                    interval=1,
                    backupCount=keep_days,
                    encoding='utf-8',
                    utc=False,
                )
            except OSError:
                return logger
            file_handler.setLevel(file_level_num)
            file_handler.namer = _daily_namer

            file_formatter = logging.Formatter(
                "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"
            )
            file_handler.setFormatter(file_formatter)

            # 挂载文本附件脱敏过滤器（仅作用于本 logger 的文件 handler）
            file_handler.addFilter(TextAttachmentSanitizer())

            logger.addHandler(file_handler)

    return logger
