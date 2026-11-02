"""pysdrlib remote protocol.

Three kinds of socket, all ZeroMQ:

    C2 (server -> ROUTER, client -> DEALER)      lifecycle: list / open / attach / claim / release / close / ping
    device control (ROUTER <-> DEALER)           one per hosted device: capabilities, state, method calls
    device RX stream (PUB -> SUB)                one per hosted device: sample blocks

Requests and replies on the C2 and control sockets are single-frame JSON:

    request  {"id": int, "op": str, ...op specific}
    reply    {"id": int, "ok": true,  "result": ...}
             {"id": int, "ok": false, "error": {"type": str, "msg": str}}

Sample blocks are 3 frames: [topic, header, payload]. `topic` is the device id (SUB prefix filter),
`header` is HEADER (below) and `payload` is `nsamples` interleaved I/Q samples in the layout the header
describes: a `convert.SampleFormat`. By default that is the device's own native layout (8-bit for a
HackRF, ...), which is lossless and several times smaller than complex64; clients always hand the
caller complex64. The format may change between blocks (the controller can switch it), so it is
read from every header.

Endpoints handed out by the server may contain `{host}`, which the client replaces with the host
it used to reach the C2 socket (so a server bound to 0.0.0.0 doesn't need to know its own address).
"""
import json
import struct

import numpy as np

from .. import err
from ..base import convert

COMPLEX64_LE = np.dtype("<c8")

VERSION = 2

# version, format kind, bits, storage, flags (FLAG_*), reserved, seq, timestamp (server time.time()), nsamples
HEADER = struct.Struct("<6BQdI")
KINDS = ("int", "uint", "float")
FLAG_PACKED = 1
FLAG_BIG_ENDIAN = 2

# Names accepted wherever a wire format is chosen. "native" is resolved per device (see `resolve_wire`).
WIRE_FORMATS = {
    "complex64": convert.FLOAT32,
    "float32": convert.FLOAT32,
    "int8": convert.INT8,
    "uint8": convert.UINT8,
    "int12": convert.INT12,
    "int12p": convert.INT12_PACKED,
    "int16": convert.INT16,
}

# Largest block published, in samples (1 MiB of complex64). Bigger reads are split.
MAX_BLOCK_SAMPLES = 131072

# Device methods that are never callable remotely: lifecycle is C2's job and samples are streamed.
BLOCKED_METHODS = frozenset({"open", "close", "get_samples"})

# Errors are re-raised on the client as the same type if it is one of these
_ERRORS = {
    cls.__name__: cls for cls in (
        err.InvalidValue, err.NoDevice, err.NotOpen, err.RemoteError, err.NotController, err.Busy, err.ProtocolError,
        ValueError, TypeError, KeyError, AttributeError, NotImplementedError, RuntimeError, TimeoutError,
    )
}

def jsonable(obj):
    """json.dumps default: numpy scalars/arrays and a few containers"""
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (set, frozenset, tuple)):
        return list(obj)
    raise TypeError(f"{type(obj).__name__} is not JSON serializable")

def pack(msg: dict) -> bytes:
    return json.dumps(msg, default=jsonable, separators=(",", ":")).encode()

def unpack(data: bytes) -> dict:
    try:
        msg = json.loads(data)
    except ValueError as e:
        raise err.ProtocolError(f"invalid message: {e}") from None
    if not isinstance(msg, dict):
        raise err.ProtocolError("message must be an object")
    return msg

def ok(req_id, result=None) -> bytes:
    return pack({"id": req_id, "ok": True, "result": result})

def fail(req_id, exc: BaseException) -> bytes:
    return pack({"id": req_id, "ok": False, "error": {"type": type(exc).__name__, "msg": str(exc)}})

def raise_error(error: dict):
    """Raise the exception described by a reply's `error`"""
    cls = _ERRORS.get(error.get("type"), err.RemoteError)
    msg = error.get("msg", "")
    if cls is err.RemoteError and error.get("type"):
        msg = f"{error['type']}: {msg}"
    raise cls(msg)

def wire_format(name: str) -> convert.SampleFormat:
    """Name -> SampleFormat, e.g. "int8"."""
    try:
        return WIRE_FORMATS[name]
    except KeyError:
        raise ValueError(f"unknown wire format {name!r} (have: native, {', '.join(WIRE_FORMATS)})") from None

def wire_name(fmt: convert.SampleFormat) -> str:
    """SampleFormat -> its wire name, or its description if it has none"""
    for name, known in WIRE_FORMATS.items():
        if known == fmt:
            return name
    return str(fmt)

def resolve_wire(name: str, device) -> convert.SampleFormat:
    """The format to publish `device`'s samples in: "native" is its own SAMPLE_FORMAT (complex64 if it has none)"""
    if name == "native":
        return device.SAMPLE_FORMAT or convert.FLOAT32
    return wire_format(name)

def pack_block(seq: int, timestamp: float, samples: np.ndarray, fmt: convert.SampleFormat = convert.FLOAT32):
    """complex64 samples -> (header bytes, payload buffer) in wire format `fmt`"""
    if fmt == convert.FLOAT32:
        payload = np.ascontiguousarray(samples, dtype=COMPLEX64_LE)    # no copy for the usual complex64
    else:
        payload = fmt.from_complex64(samples)
    flags = (FLAG_PACKED if fmt.packed else 0) | (FLAG_BIG_ENDIAN if fmt.byteorder == ">" else 0)
    return HEADER.pack(VERSION, KINDS.index(fmt.kind), fmt.bits, fmt.storage, flags, 0, seq, timestamp, len(samples)), payload

def unpack_block(header: bytes, payload) -> tuple[int, float, np.ndarray]:
    """-> (seq, timestamp, complex64 samples)"""
    if len(header) != HEADER.size:
        raise err.ProtocolError("bad block header")
    version, kind, bits, storage, flags, _, seq, timestamp, n = HEADER.unpack(header)
    if version != VERSION or kind >= len(KINDS):
        raise err.ProtocolError(f"unsupported block (version {version}, format kind {kind})")
    try:
        fmt = convert.SampleFormat(KINDS[kind], bits, storage, bool(flags & FLAG_PACKED),
                                   ">" if flags & FLAG_BIG_ENDIAN else "<")
    except ValueError as e:
        raise err.ProtocolError(f"unsupported block format: {e}") from None
    if memoryview(payload).nbytes != n * fmt.bytes_per_sample:
        raise err.ProtocolError("block length mismatch")
    if fmt == convert.FLOAT32:
        return seq, timestamp, np.frombuffer(payload, dtype=COMPLEX64_LE).astype(np.complex64, copy=False)
    return seq, timestamp, fmt.to_complex64(payload)

def is_readonly(method: str) -> bool:
    """Methods a viewer (no controller token) may call"""
    return method.startswith("get_")
