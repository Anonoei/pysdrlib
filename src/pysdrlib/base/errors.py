"""Error classes for a driver's compiled extension, declared rather than written out

The extension raises errors by name (`_native/<name>/<name>_pybind.cpp` maps each C++ error code to a class in
`pysdrlib.<name>.lib.err`), and the driver catches them (`except lib.err.NOT_FOUND`). Those classes are all empty, so
`lib/err.py` lists them:

    from ...base.errors import make_errors

    make_errors(globals(), "MyRadioError", {
        "NOT_FOUND": "No unit matched",
        "BUSY": "",
    })

This defines `MyRadioError` (an Exception) and, in that module, `NOT_FOUND` and `BUSY`, both subclasses of it. The value
is the class's docstring ("" for none); a list of names works when none needs one.
"""

def make_errors(namespace, base, codes):
    """Define the exception `base` and one subclass of it per entry of `codes` (names, or {name: docstring}) in
    `namespace` (pass `globals()`). Returns the base class"""
    module = namespace.get("__name__", __name__)
    docs = codes if isinstance(codes, dict) else {name: "" for name in codes}
    root = type(base, (Exception,), {"__module__": module, "__doc__": f"Base of {module}'s errors"})
    namespace[base] = root
    for name, doc in docs.items():
        namespace[name] = type(name, (root,), {"__module__": module, "__doc__": doc or None})
    return root
