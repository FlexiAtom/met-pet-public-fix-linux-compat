import logging
import os
import re
import datetime
import traceback
import sys

# ====================== 全局配置项 ======================
LOG_LEVEL = "DEBUG"          # 默认级别，可设为 "DEBUG","INFO","WARN","ERROR"
LOG_DIR = "logs"             # 日志文件存放目录
LOG_KEEP_DAYS = 7            # 保留最近几天的日志文件
# ====================================================

class DailyFileHandler(logging.Handler):
    """每天滚动一次，文件名带日期，首次写入时自动清理过期文件"""

    def __init__(self, log_dir, name, keep_days=7, encoding='utf-8'):
        super().__init__()
        self.log_dir = log_dir
        self.name = name
        self.keep_days = keep_days
        self.encoding = encoding
        self.stream = None
        self.current_date = None
        self.terminator = '\n'

    def emit(self, record):
        try:
            now = datetime.datetime.now()
            date_str = now.strftime("%Y-%m-%d")
            if self.current_date != date_str:
                self._rollover(date_str)
            if self.stream:
                msg = self.format(record)
                self.stream.write(msg + self.terminator)
                self.stream.flush()
        except Exception:
            # 日志系统异常不能影响主程序
            self.handleError(record)

    def _rollover(self, date_str):
        # 关闭旧文件
        if self.stream:
            self.stream.close()
            self.stream = None

        os.makedirs(self.log_dir, exist_ok=True)
        filename = f"{self.name}_{date_str}.log"
        fullpath = os.path.join(self.log_dir, filename)
        self.stream = open(fullpath, 'a', encoding=self.encoding)
        self.current_date = date_str
        self._cleanup()

    def _cleanup(self):
        if self.keep_days <= 0:
            return
        cutoff = datetime.datetime.now() - datetime.timedelta(days=self.keep_days)
        cutoff_str = cutoff.strftime("%Y-%m-%d")
        pattern = re.compile(rf"^{re.escape(self.name)}_(\d{{4}}-\d{{2}}-\d{{2}})\.log$")
        for f in os.listdir(self.log_dir):
            m = pattern.match(f)
            if m:
                file_date = m.group(1)
                if file_date < cutoff_str:
                    try:
                        os.remove(os.path.join(self.log_dir, f))
                    except OSError:
                        pass  # 忽略删除失败（如权限问题）

    def close(self):
        if self.stream:
            self.stream.close()
            self.stream = None
        super().close()


def get_color_logger(name="app", log_dir=LOG_DIR, keep_days=LOG_KEEP_DAYS,
                     console_level=None, file_level=None, enable_file=True):
    """
    获取带彩色控制台输出和文件写入的日志记录器（支持分别控制控制台与文件的级别）

    :param name:          logger 名称
    :param log_dir:       日志文件目录
    :param keep_days:     保留天数（滚动文件数量）
    :param console_level: 控制台输出级别（字符串，如 "DEBUG"），None 则使用全局级别
    :param file_level:    文件输出级别（字符串），None 则使用全局级别
    :param enable_file:   是否启用文件输出
    :return:              logging.Logger 实例
    """
    # ---------- 确定级别 ----------
    if console_level is None:
        console_level = LOG_LEVEL
    if file_level is None:
        file_level = LOG_LEVEL

    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARN": logging.WARNING,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
    }
    console_level_num = level_map.get(console_level.upper(), logging.INFO)
    file_level_num = level_map.get(file_level.upper(), logging.INFO)

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)   # logger 本身设为最低，由 handler 控制实际输出
    logger.handlers.clear()

    # ---------- 控制台 Handler（彩色） ----------
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level_num)

    class ColorFormatter(logging.Formatter):
        LEVEL_COLORS = {
            'DEBUG':    '\033[36m',   # 青色
            'INFO':     '\033[32m',   # 绿色
            'WARNING':  '\033[33m',   # 黄色
            'WARN':     '\033[33m',   # 黄色
            'ERROR':    '\033[31m',   # 红色
        }
        RESET  = '\033[0m'
        PURPLE = '\033[35m'

        # 匹配消息中的 [xxx]（方括号内非空且不含方括号）
        BRACKET_RE = re.compile(r'\[[^\[\]]+\]')

        def format(self, record):
            try:
                level_color = self.LEVEL_COLORS.get(record.levelname, '')
                reset = self.RESET

                # ---- 1. 先格式化时间 ----
                asctime = self.formatTime(record, self.datefmt)

                # ---- 2. 获取原始消息 ----
                msg = record.getMessage()

                # ---- 3. 对消息中的 [xxx] 加紫色 ----
                msg = self.BRACKET_RE.sub(
                    lambda m: f'{self.PURPLE}{m.group(0)}{reset}',
                    msg
                )

                # ---- 4. 拼装最终字符串，手动着色各部分 ----
                colored_time    = f'{level_color}{asctime}{reset}'
                colored_level   = f'{level_color}[{record.levelname}]{reset}'

                result = f'{colored_time} {colored_level} {msg}'

                # 如果有异常信息，也追加上去
                if record.exc_info and not record.exc_text:
                    record.exc_text = self.formatException(record.exc_info)
                if record.exc_text:
                    result = f'{result}\n{record.exc_text}'

                return result

            except Exception:
                # 着色失败，退回普通输出，绝不影响程序运行
                return f"{self.formatTime(record, self.datefmt)} [{record.levelname}] {record.getMessage()}"

    console_formatter = ColorFormatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    # ---------- 文件 Handler（自定义滚动，带日期文件名） ----------
    if enable_file:
        file_handler = DailyFileHandler(log_dir, name, keep_days=keep_days)
        file_handler.setLevel(file_level_num)
        file_formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

    return logger