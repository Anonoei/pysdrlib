# Drivers of your own

A new radio, or a stream from some other source, needs no change to pysdrlib. Register a `Device` subclass in your
program, or ship it as a package that declares an entry point. Every program that has the package installed then
sees it: `find()`, `open()`, `pysdrlib server`.

## In a script

```python
--8<-- "examples/custom_driver.py:driver"
```

```python
--8<-- "examples/custom_driver.py:use"
```

A `Device` in Python provides the samples itself (`_get_samples`), and pysdrlib builds `read`, `stream`, gaps, gain
scaling and range checks on it. A radio with a C/C++ library is a `NativeDevice` over a compiled extension, which
streams without the GIL: see [Adding a driver](../api/contributing.md).

## As a package

```
python -m pysdrlib.scaffold my_radio --out-of-tree      # writes pysdrlib-my-radio/: Device, pyproject.toml, a test
```

The package declares an entry point:

```toml
[project.entry-points."pysdrlib.drivers"]
my_radio = "my_package:MyRadio"
```

Once it is installed (`pip install -e .`), `pysdrlib.open("my_radio", host="10.0.0.5")` works in any program; the
keywords go to the class's `_open`. The entry point is imported only when the driver is first used, and one that fails
to import is skipped by `find`. `PYSDRLIB_NO_PLUGINS=1` ignores entry points.

## Network radios

`pysdrlib.find(addresses=["192.168.10.2"])` also looks at network addresses, for the drivers whose `find` takes
`addresses`. Plain `find()` probes no network.

## Checking it

`pysdrlib.testing.check_driver(MyRadio)` and `check_package("my_radio")` list what is missing or wrong before a
radio is involved: see [`pysdrlib.testing`](../api/testing.md).
