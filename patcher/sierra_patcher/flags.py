import os, sys

def is_dev_mode() -> bool:

    root = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(__file__)
    if os.path.exists(os.path.join(root, "dev.enable")):
        return True

    return False
