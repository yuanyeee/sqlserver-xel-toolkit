import os
import re


def safe_name(name: str) -> str:
    s = os.path.basename(name)
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("._-")
    return s or "input"
