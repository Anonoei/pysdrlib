from ...base.errors import make_errors

make_errors(globals(), "ExtIOError", {
    "INVALID_PARAM": "",
    "NOT_FOUND": "",
    "NOT_OPEN": "",
})
