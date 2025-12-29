from . import func

def hackrf_info():
    func.init()
    print(f"libhackrf version: {func.library_release()} ({func.library_version()})")
    list = func.device_list()

    if list["devicecount"] < 1:
        print("No HackRF boards found")
        return

    for i in range(list["devicecount"]):
        print("Found HackRF")
        print(f"Index: {i}")
        if list["serial_numbers"][i]:
            print(f"Serial number: {list['serial_numbers'][i].decode()}")

        device = func.device_list_open(list["ptr"], i)
        board_id = func.board_id_read(device)
        print(f"Board ID Number: {board_id}")

        version = func.get_version_string(device)
        usb_version = func.usb_api_version_read(device)

        print(f"Firmware version: {version} (API:{(usb_version >> 8) & 0xFF}.{usb_version & 0xFF})")

        partid_serialno = func.board_pardid_serialno_read(device)
        print(f"Part ID Number: 0x{partid_serialno.part_id[0]:08x} 0x{partid_serialno.part_id[1]:08x}")

        func.close(device)

    func.device_list_free(list["ptr"])
    func.exit()
