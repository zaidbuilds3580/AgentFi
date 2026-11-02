"""Router package for AgentFi."""

from graph.router.router import route_query
from graph.router.prompts import ROUTER_SYSTEM_PROMPT

__all__ = ["route_query", "ROUTER_SYSTEM_PROMPT"]

