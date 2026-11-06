"""
Application logging configuration.
Ensures standardized log output and prevents accidental credential leaking.
"""

import logging
import sys


def get_logger(name: str = "agentfi.backend") -> logging.Logger:
    """
    Configure and return a structured console logger.
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            fmt="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


logger = get_logger()

