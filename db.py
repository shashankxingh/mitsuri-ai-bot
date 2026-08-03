import logging
import time

from motor.motor_asyncio import AsyncIOMotorClient

from config import MONGODB_URI

logger = logging.getLogger(__name__)

_client: AsyncIOMotorClient | None = None
_db = None

if MONGODB_URI:
    _client = AsyncIOMotorClient(MONGODB_URI)
    _db = _client.get_default_database(default="mitsuri")
    users_col = _db["users"]
    groups_col = _db["groups"]
else:
    logger.warning("MONGODB_URI not set — first-time user/group tracking is disabled.")
    users_col = None
    groups_col = None


async def register_user_if_new(user_id: int, username: str | None, first_name: str | None) -> bool:
    """Save a user on their first /start. Returns True only if this is
    genuinely their first time (atomic upsert avoids a race between
    two /start calls landing at the same moment)."""
    if users_col is None:
        return False
    try:
        result = await users_col.update_one(
            {"_id": user_id},
            {
                "$setOnInsert": {
                    "_id": user_id,
                    "username": username,
                    "first_name": first_name,
                    "started_at": time.time(),
                }
            },
            upsert=True,
        )
        return result.upserted_id is not None
    except Exception as e:
        logger.error("Mongo error registering user %s: %s", user_id, e)
        return False


async def register_group_if_new(chat_id: int, title: str | None) -> bool:
    """Save a group the first time the bot is added to it. Returns True
    only on genuine first add."""
    if groups_col is None:
        return False
    try:
        result = await groups_col.update_one(
            {"_id": chat_id},
            {
                "$setOnInsert": {
                    "_id": chat_id,
                    "title": title,
                    "added_at": time.time(),
                }
            },
            upsert=True,
        )
        return result.upserted_id is not None
    except Exception as e:
        logger.error("Mongo error registering group %s: %s", chat_id, e)
        return False


async def get_all_user_ids() -> list[int]:
    """All user IDs ever /start'd the bot — used by /cast to broadcast."""
    if users_col is None:
        return []
    try:
        return [doc["_id"] async for doc in users_col.find({}, {"_id": 1})]
    except Exception as e:
        logger.error("Mongo error fetching user list: %s", e)
        return []
