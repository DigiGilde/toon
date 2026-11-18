"""Site feedback in the admin API: reading it, answering it and its settings per site."""

from toon.api.admin.feedback.routes import add_feedback_routes

__all__ = ["add_feedback_routes"]
