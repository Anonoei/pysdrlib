from ...base.errors import make_errors

make_errors(globals(), "UHDError", {
    "INVALID_DEVICE": "",
    "INDEX": "",
    "KEY": "",
    "NOT_IMPLEMENTED": "",
    "USB": "",
    "IO": "",
    "OS": "",
    "ASSERTION": "",
    "LOOKUP": "",
    "TYPE": "",
    "VALUE": "",
    "RUNTIME": "",
    "ENVIRONMENT": "",
    "SYSTEM": "",
    "NOT_FOUND": "No USRP matched (none attached, or it could not be found at the address)",
    "NOT_OPEN": "",
    "TIMEOUT": "",
    "OVERFLOW": "",
    "OTHER": "",
})
