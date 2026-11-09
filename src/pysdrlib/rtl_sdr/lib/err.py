from ...base.errors import make_errors

make_errors(globals(), "RTLSDRError", {
    "INVALID_PARAM": "",
    "NOT_FOUND": "",
    "NOT_OPEN": "",
    "OPEN_FAILED": "The dongle is there but could not be opened: no permission, or something else has it",
    "BUSY": "",
})
