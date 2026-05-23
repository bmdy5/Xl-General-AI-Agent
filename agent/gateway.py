"""QQ Gateway — 外观模式 Facade 代理桥接层。

保持 100% 向下兼容（systemd、Docker、启动脚本 0 变更无感过渡）。
具体模块化高内聚实现均已安全迁移至 agent/net_gateway/ 子包中。
"""

from .net_gateway.bot import QQGateway, main

__all__ = ["QQGateway", "main"]
