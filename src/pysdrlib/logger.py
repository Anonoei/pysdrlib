"""pysdrlib's loggers: `pysdrlib.<name>` in the standard `logging` hierarchy.

As a library pysdrlib adds no handlers of its own: its warnings reach stderr through `logging`'s last resort until the
program configures logging (`logging.basicConfig(...)`, or handlers on the `pysdrlib` logger), and then go wherever the
program sends them. A program that only wants pysdrlib's own log on a stream (the `pysdrlib server` command) calls
`configure`, and `unconfigure` when it is done.
"""
import logging

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

ROOT = "pysdrlib"
root = logging.getLogger(ROOT)
_configured = None      # (handler, the root's level and propagate before configure)

def configure(level=logging.INFO, stream=None, timestamps=True) -> logging.Handler:
    """Send pysdrlib's log at `level` and above to `stream` (default stderr) and nowhere else, replacing what an earlier
    `configure` set up: `pysdrlib server` uses this so a service's journal shows what it is doing. timestamps=False leaves
    them to journald. Undo with `unconfigure`."""
    global _configured
    before = unconfigure()
    handler = logging.StreamHandler(stream)
    handler.setLevel(level)
    handler.setFormatter(formatter if timestamps else logging.Formatter("<%(name)s> %(levelname)s: %(message)s"))
    _configured = (handler, root.level, root.propagate) if before is None else (handler, *before)
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False      # it has its own output now: not also the program's
    return handler

def unconfigure():
    """Remove what `configure` set up, leaving the `pysdrlib` logger as it was before. -> its (level, propagate) then, or
    None when nothing was configured"""
    global _configured
    if _configured is None:
        return None
    handler, level, propagate = _configured
    _configured = None
    root.removeHandler(handler)
    handler.flush()
    root.setLevel(level)
    root.propagate = propagate
    return level, propagate

def new(name: str, level=None, ch=True, fh=True) -> TraceLogger:
    """The logger `pysdrlib.<name>`. level: its own level (default: inherit, from the `pysdrlib` logger and up). `ch` and
    `fh` are ignored (pysdrlib no longer adds handlers of its own)."""
    logger = logging.getLogger(f"{ROOT}.{name}")
    if not isinstance(logger, TraceLogger):
        logger.__class__ = TraceLogger      # only adds `trace`: no state of its own
    if level is not None:
        logger.setLevel(level)
    return logger
