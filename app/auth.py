from typing import Optional

from fastapi import Header


async def get_current_user(x_user_id: Optional[str] = Header(default=None)) -> dict:
    return {"user_id": x_user_id or "test-user"}
