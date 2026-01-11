"""
Step handler registry.

All step handlers are registered here and imported by the runner.
"""

from typing import Callable, Dict
from ..runner import StepResult, StepContext

# Handler registry - maps step names to handler functions
STEP_HANDLERS: Dict[str, Callable[[StepContext], StepResult]] = {}


def register_handler(step_name: str):
    """Decorator to register a step handler."""
    def decorator(func: Callable[[StepContext], StepResult]):
        STEP_HANDLERS[step_name] = func
        return func
    return decorator


# Import all handler modules to populate the registry
from . import night
from . import day
from . import postgame
