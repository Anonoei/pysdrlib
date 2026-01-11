import logging
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

if LOGGING:
    handler_file = RotatingFileHandler("pysdrlib.log", maxBytes=1024*1024, backupCount=3)
    handler_file.setLevel(logging.NOTSET)
    handler_file.setFormatter(formatter)

    handler_console = logging.StreamHandler()
    handler_console.setLevel(logging.INFO)
    handler_console.setFormatter(formatter)

def new(name: str, level = None, ch=True, fh=True):
    if level is None:
        level = logging.DEBUG
    logger = TraceLogger(f"pysdrlib.{name}")

    if LOGGING and ch:
        logger.addHandler(handler_console)
    if LOGGING and fh:
        logger.addHandler(handler_file)
    return logger
