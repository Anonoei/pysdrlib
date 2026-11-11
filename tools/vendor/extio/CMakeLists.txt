# Copied to src/pysdrlib/extio/vendor/ by tools/vendor.py: edit it here.
cmake_minimum_required(VERSION 3.24)
project(sddc_core LANGUAGES C CXX)

# ExtIO_sddc's Core is the RX888 driver (USB + firmware upload + DDC), but upstream only builds it as a
# static library behind a C API that doesn't expose it all. pysdrlib wraps its C++ classes directly, so
# build it as one shared library the extension can resolve its symbols from at import time.

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -Wall")

# Variables Core/CMakeLists.txt expects its parent project to provide
option(USE_SIMD_OPTIMIZATIONS "enable SIMD optimizations" ON)
find_package(PkgConfig REQUIRED)
pkg_check_modules(LIBUSB REQUIRED libusb-1.0)
pkg_check_modules(LIBFFTW REQUIRED fftw3f)

add_subdirectory(ExtIO_sddc/Core)   # -> SDDC_CORE (static)

add_library(sddc_core SHARED ${CMAKE_CURRENT_SOURCE_DIR}/../../_native/extio/usb_guard.cpp)   # everything else is in SDDC_CORE
target_include_directories(sddc_core PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})   # usb_guard.cpp includes ExtIO_sddc/... via the link here
target_link_libraries(sddc_core PRIVATE "$<LINK_LIBRARY:WHOLE_ARCHIVE,SDDC_CORE>")

# See usb_guard.cpp. Apple's linker has no --wrap: there a failed open still crashes.
if(NOT APPLE)
    target_link_options(sddc_core PRIVATE -Wl,--wrap=usb_device_open -Wl,--wrap=usb_device_control)
endif()
