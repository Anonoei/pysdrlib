"""pysdrlib's loggers: in the `logging` hierarchy, no handlers of their own, and `configure` can be undone"""
import io
import logging
import subprocess
import sys

from pysdrlib import logger


def test_loggers_are_under_pysdrlib_and_can_trace():
    log = logger.new("test.hierarchy")
    assert log is logging.getLogger("pysdrlib.test.hierarchy") and log.parent is logger.root
    assert log.level == logging.NOTSET and not log.handlers
    log.trace("fine")


def test_configure_replaces_and_unconfigure_restores():
    before = (list(logger.root.handlers), logger.root.level, logger.root.propagate)
    first, second = io.StringIO(), io.StringIO()
    logger.configure(logging.INFO, first, timestamps=False)
    try:
        logger.configure(logging.DEBUG, second, timestamps=False)       # replaces, not adds
        assert len(logger.root.handlers) == len(before[0]) + 1
        logger.new("test.configure").debug("hello")
        assert "hello" in second.getvalue() and not first.getvalue()
    finally:
        logger.unconfigure()
    assert (list(logger.root.handlers), logger.root.level, logger.root.propagate) == before
    assert logger.unconfigure() is None


def test_a_plain_program_sees_warnings_on_stderr_and_nothing_less():
    code = ("from pysdrlib import logger; log = logger.new('x'); log.info('quiet'); log.warning('loud')")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert "loud" in out.stderr and "quiet" not in out.stderr
