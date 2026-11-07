"""可以安全交给API层处理的基础设施异常。"""


class DatabaseUnavailableError(Exception):
    """数据库配置或操作失败，不携带原始连接信息。"""
