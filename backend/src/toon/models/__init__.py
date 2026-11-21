"""Registers every SQLAlchemy model on the shared metadata (Base)."""

from toon.models import audit, ci, cli, feedback, identity, notifications, publication, slugs
from toon.models.base import Base

__all__ = ["Base", "audit", "ci", "cli", "feedback", "identity", "notifications", "publication", "slugs"]
