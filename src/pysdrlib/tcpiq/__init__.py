from .tcpiq import TcpIq

__doc__ = TcpIq.__doc__
Device = TcpIq

find = TcpIq.find   # a stream can't be enumerated: open it with pysdrlib.open("tcpiq", host=..., port=...)
