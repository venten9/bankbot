"""Admin Authorization Filter for RBAC protection."""

from aiogram.filters import Filter
from aiogram.types import TelegramObject

from app.config import settings


class IsAdminFilter(Filter):
    """Filter accepting only users listed in settings.ADMIN_IDS."""

    def __init__(self, admin_ids: list[int] | None = None):
        self.admin_ids = admin_ids

    async def __call__(self, event: TelegramObject) -> bool:
        user = getattr(event, "from_user", None)
        if not user:
            return False
        admin_list = self.admin_ids if self.admin_ids is not None else settings.ADMIN_IDS
        return user.id in admin_list
