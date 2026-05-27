from .base import EventMiddleware
from .pipeline import (
    SelfReceiptFilterMiddleware,
    AuditLoggingMiddleware,
    SecurityWhiteListMiddleware,
    GroupMessageFilterMiddleware,
    SecurityInterceptionMiddleware,
    SessionControlMiddleware,
    QuickReplyMiddleware,
    TaskDispatcherMiddleware,
)

def get_default_middlewares() -> list[EventMiddleware]:
    return [
        SelfReceiptFilterMiddleware(),
        AuditLoggingMiddleware(),
        SecurityWhiteListMiddleware(),
        GroupMessageFilterMiddleware(),
        SecurityInterceptionMiddleware(),
        SessionControlMiddleware(),
        QuickReplyMiddleware(),
        TaskDispatcherMiddleware(),
    ]
