from .router import api

# Import modules to register routes
from . import availability, demand, locations, rules, scheduling, special_days  # noqa: F401

__all__ = ["api"]
