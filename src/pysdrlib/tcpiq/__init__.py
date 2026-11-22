from .tcpiq import TcpIq
from ..base.convert import NAMES as FORMATS   # the sample_format names open() takes (e.g. a --format option's choices)

__doc__ = TcpIq.__doc__
Device = TcpIq

find = TcpIq.find   # a stream can't be enumerated: open it with pysdrlib.open("tcpiq", host=..., port=...)
