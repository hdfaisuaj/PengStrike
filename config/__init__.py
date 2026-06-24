"""
config 包入口

兼容旧版写法:
    from config import settings

新版推荐写法:
    from config.settings import get_settings
    settings = get_settings()
"""

from .settings import Settings, get_settings

# 与旧版 config.py 保持兼容: 直接提供一个 settings 单例
settings = get_settings()

__all__ = ["Settings", "settings", "get_settings"]
