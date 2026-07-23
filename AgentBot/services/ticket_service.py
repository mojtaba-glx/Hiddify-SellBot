from typing import List, Dict, Any, Optional
from AgentBot.database import (
    create_ticket as db_create_ticket,
    get_tickets as db_get_tickets,
    get_ticket as db_get_ticket,
    set_ticket_status as db_set_ticket_status,
    add_ticket_message as db_add_ticket_message,
    get_ticket_messages as db_get_ticket_messages,
)


def create_ticket(agent_id: int, customer_id: int, customer_name: str, subject: str) -> Dict[str, Any]:
    return db_create_ticket(agent_id, customer_id, customer_name, subject)


def get_tickets(agent_id: int, status: Optional[str] = None) -> List[Dict[str, Any]]:
    return db_get_tickets(agent_id, status)


def get_ticket(ticket_id: int, agent_id: int) -> Optional[Dict[str, Any]]:
    return db_get_ticket(ticket_id, agent_id)


def close_ticket(ticket_id: int, agent_id: int) -> bool:
    return db_set_ticket_status(ticket_id, agent_id, "closed")


def reply_ticket(ticket_id: int, agent_id: int, sender_name: str, message: str) -> Dict[str, Any]:
    db_set_ticket_status(ticket_id, agent_id, "open")
    return db_add_ticket_message(ticket_id, "agent", agent_id, sender_name, message)


def get_messages(ticket_id: int) -> List[Dict[str, Any]]:
    return db_get_ticket_messages(ticket_id)
