import logging
import weakref
from logging.handlers import RotatingFileHandler

LOGGING = False
LOG_LEVEL_TRACE = 5

logging.addLevelName(LOG_LEVEL_TRACE, "TRACE")

class TraceLogger(logging.Logger):
    def trace(self, msg, *args, **kwargs):
        if self.isEnabledFor(LOG_LEVEL_TRACE):
            self._log(LOG_LEVEL_TRACE, msg, args, **kwargs)

formatter = logging.Formatter(
    "%(asctime)s <%(name)s> %(levelname)s: %(message)s",
    datefmt="%Y%m%d_%H%M%S"
)

_loggers = weakref.WeakSet()
_handlers = []

def _default_handlers():
    if not LOGGING:
        return []
    fh = RotatingFileHandler("pysdrlib.log", maxBytes=1024*1024, backupCount=3)
    fh.setLevel(logging.NOTSET)
    fh.setFormatter(formatter)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(formatter)
    return [(ch, True, False), (fh, False, True)]

_handlers = _default_handlers()   # (handler, console, file)

def configure(level=logging.INFO, stream=None, timestamps=True):
    """Send every pysdrlib logger (existing and future) to a stream handler: `pysdrlib server` uses this so a service's
    journal shows what it is doing. timestamps=False leaves them to journald."""
    handler = logging.StreamHandler(stream)
    handler.setLevel(level)
    handler.setFormatter(formatter if timestamps else logging.Formatter("<%(name)s> %(levelname)s: %(message)s"))
    _handlers.append((handler, True, False))
    for logger in list(_loggers):
        logger.addHandler(handler)
    return handler

def new(name: str, level = None, ch=True, fh=True):
    if level is None:
        level = logging.DEBUG
    logger = TraceLogger(f"pysdrlib.{name}")
    logger.setLevel(level)
    for handler, is_console, is_file in _handlers:
        if (is_console and ch) or (is_file and fh):
            logger.addHandler(handler)
    _loggers.add(logger)
    return logger
