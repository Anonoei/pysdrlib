from ..lib import hackrf

def hackrf_info():
    hackrf.init()
    print(f"libhackrf version: {hackrf.library_release()} ({hackrf.library_version()})")
    devs = hackrf.device_list()

    if devs.device_count < 1:
        print("No HackRF boards found")
        return

    for i in range(devs.device_count):
        print("Found HackRF")
        print(f"Index: {i}")
        if devs.serial_numbers[i]:
            print(f"Serial number: {devs.serial_numbers[i]}")

        device = hackrf.device_list_open(devs, i)
        board_id = hackrf.board_id_read(device)
        print(f"Board ID number: {board_id}")

        version = hackrf.version_string_read(device)
        usb_version = hackrf.usb_api_version_read(device)

        print(f"Firmware version: {version} (API:{usb_version})")

        part_id, serial_no = hackrf.board_partid_serialno_read(device)
        print(f"Part ID Number: 0x{part_id}")

        device.close()
    devs.close()
    hackrf.exit()
