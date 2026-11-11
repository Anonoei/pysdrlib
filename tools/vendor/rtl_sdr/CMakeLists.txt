# Copied to src/pysdrlib/rtl_sdr/vendor/ by tools/vendor.py: edit it here.
cmake_minimum_required(VERSION 3.15)
project(rtlsdr LANGUAGES C)

# librtlsdr only, without upstream's tools, install rules or its CMake project (which assumes it is the
# top-level project). pysdrlib's extension leaves librtlsdr's symbols undefined and resolves them from
# this library at import time.

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()
set(CMAKE_C_STANDARD 99)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

find_package(Threads REQUIRED)
find_package(PkgConfig REQUIRED)
pkg_check_modules(LIBUSB REQUIRED IMPORTED_TARGET libusb-1.0)

set(RTL librtlsdr)
add_library(rtlsdr SHARED
    ${RTL}/src/librtlsdr.c
    ${RTL}/src/tuner_e4k.c
    ${RTL}/src/tuner_fc0012.c
    ${RTL}/src/tuner_fc0013.c
    ${RTL}/src/tuner_fc2580.c
    ${RTL}/src/tuner_r82xx.c)
target_include_directories(rtlsdr PRIVATE ${RTL}/include)
target_link_libraries(rtlsdr PRIVATE PkgConfig::LIBUSB Threads::Threads)
target_compile_definitions(rtlsdr PRIVATE
    rtlsdr_EXPORTS
    # Dongles are claimed by the kernel's DVB driver (dvb_usb_rtl28xxu) unless it is blacklisted;
    # detach it instead of failing to open
    DETACH_KERNEL_DRIVER=1)
set_target_properties(rtlsdr PROPERTIES SOVERSION 0 C_VISIBILITY_PRESET hidden)
