"""Logging module for aria2bot"""
import logging
import sys

_initialized = False


def setup_logger(name: str = "aria2bot", level: int = logging.INFO) -> logging.Logger:
    """Initialize and configure the root logger."""
    global _initialized
    logger = logging.getLogger(name)
    if not _initialized:
        logger.setLevel(level)
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(handler)
        _initialized = True
    return logger


def get_logger(name: str) -> logging.Logger:
    """Get a child logger for a specific module."""
    return logging.getLogger(f"aria2bot.{name}")
