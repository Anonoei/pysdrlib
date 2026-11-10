cmake_minimum_required(VERSION 3.16)
project(uhd_pysdrlib LANGUAGES C CXX)

# libuhd only, for the B2xx family (B200/B210/B200mini/B205mini), without upstream's utilities, examples,
# tests, Python API or install rules. pysdrlib's extension uses UHD's C API (uhd.h), leaves its symbols
# undefined and resolves them from this library at import time.
#
# `uhd` is a symlink to the vendored EttusResearch/uhd checkout (vendor/uhd).

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

set(ENABLE_B200        ON  CACHE BOOL "" FORCE)
foreach(opt B100 USRP1 USRP2 X300 MPMD N300 N320 E320 E300 OCTOCLOCK
            PYTHON_API EXAMPLES TESTS UTILS MAN_PAGES MANUAL DOXYGEN DOXYGEN_ONLY
            DPDK LIBURING STATIC_LIBS GPSD PYMOD_UTILS RFNOC_TESTS)
    set(ENABLE_${opt} OFF CACHE BOOL "" FORCE)
endforeach()
set(ENABLE_C_API ON CACHE BOOL "" FORCE)
set(ENABLE_RFNOC ON CACHE BOOL "" FORCE)   # B2xx's streaming is built on RFNoC in UHD 4

add_subdirectory(uhd/host ${CMAKE_CURRENT_BINARY_DIR}/uhd EXCLUDE_FROM_ALL)
# Build target (the CMake project's `uhd` shared library)
add_custom_target(pysdrlib_uhd ALL DEPENDS uhd)
