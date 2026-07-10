"""modern-di integration for arq.

The integration manipulates arq's ``ctx`` dict, its lifecycle hook callables,
and a settings class/dict structurally, so this module needs no arq import.
"""

_ROOT_CONTAINER_KEY = "modern_di_container"
_CHILD_CONTAINER_KEY = "modern_di_request_container"
