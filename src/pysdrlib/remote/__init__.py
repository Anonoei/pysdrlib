from .._ext import utils as _utils

__doc__ = _utils.readme(__file__)

try:
    import zmq  # noqa: F401
except ImportError as e:
    raise ImportError("pysdrlib's remote support needs pyzmq: pip install pysdrlib[remote]") from e

from .client import Client, RemoteDevice
from .server import Server

Device = RemoteDevice
