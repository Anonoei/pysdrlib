"""Client/server access to SDRs over ZeroMQ. Needs `pip install pysdrlib[remote]`.

    python -m pysdrlib server --bind tcp://0.0.0.0:5555
    python -m pysdrlib client tcp://host:5555 list

    sdr = pysdrlib.device("remote", "tcp://host:5555", "hackrf")
"""
try:
    import zmq  # noqa: F401
except ImportError as e:
    raise ImportError("pysdrlib's remote support needs pyzmq: pip install pysdrlib[remote]") from e

from .client import Client, RemoteDevice
from .server import Server

Device = RemoteDevice
