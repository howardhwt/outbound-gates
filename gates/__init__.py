"""Deterministic checks for outbound email drafts."""
from .checker import Finding, check, load_config, load_claims

__all__ = ["Finding", "check", "load_config", "load_claims"]
