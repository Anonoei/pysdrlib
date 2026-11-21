from .._ext import utils as _utils

__doc__ = _utils.readme(__file__)

from . import err, sigmf
from .file import File
from .sink import FileSink
from .replay import Replay

Device = Replay   # pysdrlib.open("file", path=...): a recording played back like a radio
find = Replay.find   # nothing to list: open it with a path
