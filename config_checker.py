"""
配置文件健康检查模块
检查 config.json 的行数是否小于 10，用于检测文件是否被截断或损坏。
"""
import os

def check_config_lines(config_path: str) -> bool:
    """
    检查配置文件的行数是否足够。
    参数：
        config_path: config.json 的绝对或相对路径
    返回：
        True 如果行数 >= 10
        False 如果行数 < 10 或文件无法读取
    """
    if not os.path.exists(config_path):
        return False
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        return len(lines) >= 10
    except Exception:
        return False

