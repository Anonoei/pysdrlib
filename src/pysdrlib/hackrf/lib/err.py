from ...base.errors import make_errors

make_errors(globals(), "HackRFError", {
    "INVALID_PARAM": "",
    "NOT_FOUND": "",
    "BUSY": "",
    "NO_MEM": "",
    "LIBUSB": "",
    "THREAD": "",
    "STREAMING_THREAD_ERR": "",
    "STREAMING_STOPPED": "",
    "STREAMING_EXIT_CALLED": "",
    "USB_API_VERSION": "",
    "NOT_LAST_DEVICE": "",
    "OTHER": "",
})
