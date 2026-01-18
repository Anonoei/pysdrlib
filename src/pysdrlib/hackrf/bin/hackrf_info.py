from .. import lib

def hackrf_info():
    lib.hackrf.init() # pyright: ignore[reportAttributeAccessIssue]
    print(f"libhackrf version: {lib.hackrf.library_release()} ({lib.hackrf.library_version()})") # pyright: ignore[reportAttributeAccessIssue]
    devs = lib.hackrf.device_list() # pyright: ignore[reportAttributeAccessIssue]

    if devs.device_count < 1:
        print("No HackRF boards found")
        return

    for i in range(devs.device_count):
        print("Found HackRF")
        print(f"Index: {i}")
        if devs.serial_numbers[i]:
            print(f"Serial number: {devs.serial_numbers[i]}")

        device = lib.hackrf.device_list_open(devs, i) # pyright: ignore[reportAttributeAccessIssue]
        board_id = lib.hackrf.board_id_read(device) # pyright: ignore[reportAttributeAccessIssue]
        print(f"Board ID number: {board_id}")

        version = lib.hackrf.version_string_read(device) # pyright: ignore[reportAttributeAccessIssue]
        usb_version = lib.hackrf.usb_api_version_read(device) # pyright: ignore[reportAttributeAccessIssue]

        print(f"Firmware version: {version} (API:{usb_version})")

        part_id, serial_no = lib.hackrf.board_partid_serialno_read(device) # pyright: ignore[reportAttributeAccessIssue]
        print(f"Part ID Number: 0x{part_id}")

        device.close()
    devs.close()
    lib.hackrf.exit() # pyright: ignore[reportAttributeAccessIssue]
