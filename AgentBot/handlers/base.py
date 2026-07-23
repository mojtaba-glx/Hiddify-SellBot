import logging
from typing import Optional, Dict, Any

from telegram import Update
from telegram.ext import ContextTypes

from Shared import agent_db
from AgentBot.constants import UD_AGENT_ID, UD_AGENT_DATA, UD_STATE

logger = logging.getLogger(__name__)


def get_agent_id(context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
    return context.user_data.get(UD_AGENT_ID)


def set_agent_id(context: ContextTypes.DEFAULT_TYPE, agent_id: int) -> None:
    context.user_data[UD_AGENT_ID] = agent_id


def get_agent_data(context: ContextTypes.DEFAULT_TYPE) -> Optional[Dict[str, Any]]:
    return context.user_data.get(UD_AGENT_DATA)


def set_agent_data(context: ContextTypes.DEFAULT_TYPE, data: Dict[str, Any]) -> None:
    context.user_data[UD_AGENT_DATA] = data


def clear_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(UD_STATE, None)
    context.user_data.pop("new_card_draft", None)
    context.user_data.pop("edit_card_field", None)
    context.user_data.pop("broadcast_state", None)


async def authenticate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[Dict[str, Any]]:
    user = update.effective_user
    if not user:
        return None
    agent = agent_db.get_agent_by_telegram_id(user.id)
    if not agent or not int(agent.get("is_active", 0)):
        return None
    set_agent_id(context, agent["id"])
    set_agent_data(context, agent)
    return agent
