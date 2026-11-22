"""A radio hosted by a server and used through it: the same API as a local one. The server runs in this process on
127.0.0.1 here; normally it is `pysdrlib server --bind tcp://0.0.0.0:5555` on the machine with the radios.
Needs pip install pysdrlib[remote]. Runs on the simulated radio."""
import pysdrlib
from pysdrlib.remote import Client, Server

# --8<-- [start:server]
server = Server("tcp://127.0.0.1:0", devices=["sim"])   # port 0: any free one (server.endpoint says which)
server.start()                                          # or server.serve_forever()
# --8<-- [end:server]
address = server.endpoint

# --8<-- [start:connect]
sdr = pysdrlib.connect(address, "sim", program="example")   # a new device on the server: you control it
sdr.initialize(cf=100e6, Fs=2e6)
sdr.start_rx()
x = sdr.read(65536)                                          # complex64, like any device

view = pysdrlib.connect(address, sdr.id)                     # another program attaches to the same device
y, m = view.read(65536, meta=True)                           # the same stream, at its own pace; it can't retune it
# --8<-- [end:connect]

# --8<-- [start:client]
with Client(address) as c:
    print(c.types())                                         # device types the server can open
    print(c.list())                                          # the devices it hosts, who controls each
    print(c.metrics(sdr.id)["devices"])                      # what it has published, to whom (`pysdrlib client ... stats`)
# --8<-- [end:client]

view.close()
sdr.close()                                                  # also closes the device on the server
server.stop()
