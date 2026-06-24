"""
时间格式化工具 (utils/time_utils.py)
提供统一的时间格式化函数，将 UTC 时间转为北京时间（UTC+8）
"""


from datetime import datetime, timedelta, timezone


def format_beijing_time(dt: datetime) -> str:
    """
    将 datetime 转为北京时间字符串（UTC+8）
    
    Args:
        dt: datetime 对象（可以是 timezone-aware 或 naive）
        
    Returns:
        格式化的北京时间字符串，格式：YYYY-MM-DD HH:MM:SS
        如果输入为 None，返回空字符串
    """
    if dt is None:
        return ""
    
    # 如果 dt 是 timezone-aware，直接转换
    if dt.tzinfo is not None:
        bj = dt.astimezone(timezone(timedelta(hours=8)))
    else:
        # naive 时间，假设是 UTC
        bj = dt.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=8)))
    
    return bj.strftime("%Y-%m-%d %H:%M:%S")


def format_timestamp(timestamp: float) -> str:
    """
    将 Unix 时间戳转为北京时间字符串
    
    Args:
        timestamp: Unix 时间戳（秒）
        
    Returns:
        格式化的北京时间字符串
    """
    if not timestamp:
        return ""
    
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return format_beijing_time(dt)
