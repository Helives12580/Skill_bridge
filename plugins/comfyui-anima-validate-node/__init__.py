from . import h3_guide_stack, number_to_text
from .anima_validate_node import (
    NODE_CLASS_MAPPINGS as _VALIDATE_CLASSES,
    NODE_DISPLAY_NAME_MAPPINGS as _VALIDATE_DISPLAY,
)

NODE_CLASS_MAPPINGS = {
    **_VALIDATE_CLASSES,
    **number_to_text.NODE_CLASS_MAPPINGS,
    **h3_guide_stack.NODE_CLASS_MAPPINGS,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    **_VALIDATE_DISPLAY,
    **number_to_text.NODE_DISPLAY_NAME_MAPPINGS,
    **h3_guide_stack.NODE_DISPLAY_NAME_MAPPINGS,
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
