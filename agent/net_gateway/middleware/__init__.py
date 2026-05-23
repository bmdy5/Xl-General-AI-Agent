from .base import EventMiddleware
from .pipeline import (
    SelfReceiptFilterMiddleware,
    AuditLoggingMiddleware,
    SecurityWhiteListMiddleware,
    GroupMessageFilterMiddleware,
    SecurityInterceptionMiddleware,
    AdminCommandMiddleware,
    SleepFreezeMiddleware,
    PendingPermissionMiddleware,
    PodcastTopicMiddleware,
    VoiceCommandMiddleware,
    TaskDispatcherMiddleware
)

def get_default_middlewares() -> list[EventMiddleware]:
    return [
        SelfReceiptFilterMiddleware(),
        AuditLoggingMiddleware(),
        SecurityWhiteListMiddleware(),
        GroupMessageFilterMiddleware(),
        SecurityInterceptionMiddleware(),
        AdminCommandMiddleware(),
        SleepFreezeMiddleware(),
        PendingPermissionMiddleware(),
        PodcastTopicMiddleware(),
        VoiceCommandMiddleware(),
        TaskDispatcherMiddleware()
    ]
