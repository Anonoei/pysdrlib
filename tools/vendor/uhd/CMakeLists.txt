cmake_minimum_required(VERSION 3.16)
project(uhd_pysdrlib LANGUAGES C CXX)

# libuhd for the B2xx family, built from pysdrlib's trimmed UHD snapshot (see PROVENANCE): only the files a
# B2xx-only build reads, plus the outputs of UHD's Python code generators (generated/), so this needs neither
# Python nor UHD's own CMake. This file is tools/vendor/uhd/CMakeLists.txt, copied here by tools/vendor.py
# together with sources.cmake (the source list, from a traced upstream build): edit it there, not here.

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_VISIBILITY_PRESET hidden)
set(CMAKE_VISIBILITY_INLINES_HIDDEN ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

set(UHD_SNAPSHOT ${CMAKE_CURRENT_SOURCE_DIR})
include(${UHD_SNAPSHOT}/sources.cmake)

# sources.cmake sets VENDOR_VERSION ("4.9.0.0": the upstream tag the snapshot was taken from)
string(REPLACE "." ";" _v ${VENDOR_VERSION})
list(GET _v 0 UHD_VERSION_MAJOR)
list(GET _v 1 UHD_VERSION_API)
list(GET _v 2 UHD_VERSION_ABI)
list(GET _v 3 UHD_VERSION_PATCH)
math(EXPR UHD_VERSION_ADDED "${UHD_VERSION_MAJOR} * 1000000 + ${UHD_VERSION_API} * 10000 + ${UHD_VERSION_ABI} * 100 + ${UHD_VERSION_PATCH}")

find_package(Threads REQUIRED)
# The only compiled Boost libraries a B2xx libuhd links (upstream also asks for program_options, serialization
# and unit_test_framework, which --as-needed drops)
find_package(Boost 1.71 REQUIRED COMPONENTS filesystem thread)
find_package(PkgConfig REQUIRED)
pkg_check_modules(LIBUSB REQUIRED IMPORTED_TARGET libusb-1.0)

# ------------------------------------------------------------------ platform probes (as upstream's lib/*/CMakeLists.txt)
include(CheckCXXSourceCompiles)
include(CheckSymbolExists)
include(CheckIncludeFileCXX)

set(CMAKE_REQUIRED_LIBRARIES Threads::Threads)
check_cxx_source_compiles("#include <pthread.h>
    int main(){ struct sched_param sp; pthread_setschedparam(pthread_self(), SCHED_RR, &sp); return 0; }" HAVE_PTHREAD_SETSCHEDPARAM)
check_cxx_source_compiles("#include <pthread.h>
    int main(){ pthread_t pt; pthread_setname_np(pt, \"test\"); return 0; }" HAVE_PTHREAD_SETNAME)
check_cxx_source_compiles("#include <pthread.h>
    int main(){ pthread_t pt; cpu_set_t cs; pthread_setaffinity_np(pt, sizeof(cpu_set_t), &cs); return 0; }" HAVE_PTHREAD_SETAFFINITY_NP)
set(CMAKE_REQUIRED_LIBRARIES ${CMAKE_DL_LIBS})
check_cxx_source_compiles("#include <dlfcn.h>
    int main(){ dlopen(0, 0); return 0; }" HAVE_DLOPEN)
unset(CMAKE_REQUIRED_LIBRARIES)
check_cxx_source_compiles("#include <ifaddrs.h>
    int main(){ struct ifaddrs *ifap; getifaddrs(&ifap); return 0; }" HAVE_GETIFADDRS)
check_include_file_cxx(emmintrin.h HAVE_EMMINTRIN_H)
check_symbol_exists(log2 "cmath" HAVE_LOG2)

set(CMAKE_REQUIRED_INCLUDES ${LIBUSB_INCLUDE_DIRS})
set(CMAKE_REQUIRED_LIBRARIES ${LIBUSB_LINK_LIBRARIES})
set(LIBUSB_DEFS)
foreach(fn libusb_error_name libusb_handle_events_timeout_completed libusb_strerror)
    string(TOUPPER "HAVE_${fn}" var)
    check_symbol_exists(${fn} "libusb.h" ${var})
    if(${var})
        list(APPEND LIBUSB_DEFS ${var}=1)
    endif()
endforeach()
unset(CMAKE_REQUIRED_INCLUDES)
unset(CMAKE_REQUIRED_LIBRARIES)

macro(pick out)   # pick(<var> COND1 DEF1 COND2 DEF2 ... FALLBACK): first DEFn whose CONDn is true
    set(_args ${ARGN})
    list(POP_BACK _args _fallback)
    set(${out} ${_fallback})
    while(_args)
        list(POP_FRONT _args _cond _def)
        if(${_cond})
            set(${out} ${_def})
            break()
        endif()
    endwhile()
endmacro()
pick(PRIO_DEF HAVE_PTHREAD_SETSCHEDPARAM HAVE_PTHREAD_SETSCHEDPARAM HAVE_THREAD_PRIO_DUMMY)
pick(NAME_DEF HAVE_PTHREAD_SETNAME HAVE_PTHREAD_SETNAME HAVE_THREAD_SETNAME_DUMMY)
pick(AFF_DEF HAVE_PTHREAD_SETAFFINITY_NP HAVE_PTHREAD_SETAFFINITY_NP HAVE_THREAD_SETAFFINITY_DUMMY)
pick(DL_DEF HAVE_DLOPEN HAVE_DLOPEN HAVE_LOAD_MODULES_DUMMY)
pick(IFADDR_DEF HAVE_GETIFADDRS HAVE_GETIFADDRS HAVE_IF_ADDRS_DUMMY)

set(H ${UHD_SNAPSHOT}/src/host)
set_source_files_properties(${H}/lib/utils/thread.cpp PROPERTIES COMPILE_DEFINITIONS "${PRIO_DEF};${NAME_DEF};${AFF_DEF}")
set_source_files_properties(${H}/lib/utils/load_modules.cpp PROPERTIES COMPILE_DEFINITIONS "${DL_DEF}")
set_source_files_properties(${H}/lib/transport/if_addrs.cpp PROPERTIES COMPILE_DEFINITIONS "${IFADDR_DEF}")
set_source_files_properties(${H}/lib/transport/libusb1_zero_copy.cpp PROPERTIES COMPILE_DEFINITIONS "${LIBUSB_DEFS}")
set_source_files_properties(${H}/lib/utils/paths.cpp PROPERTIES
    COMPILE_DEFINITIONS "UHD_LIB_DIR=\"lib\";UHD_PKG_PATH=\"${CMAKE_INSTALL_PREFIX}\"")
if(HAVE_EMMINTRIN_H)
    if(NOT MSVC)
        set_source_files_properties(${UHD_MSSE2_SOURCES} PROPERTIES COMPILE_OPTIONS -msse2)
    endif()
    list(APPEND UHD_SOURCES ${UHD_MSSE2_SOURCES})
endif()

# ------------------------------------------------------------------ the two files that record the host/compiler
set(ENABLE_USB ON)
set(UHD_VERSION ${UHD_VERSION_ADDED})
set(UHD_USER_CONF_FILE "uhd.conf")
if(UNIX)
    set(UHD_SYS_CONF_FILE "/etc/uhd/uhd.conf")
endif()
configure_file(${H}/include/config.h.in ${CMAKE_CURRENT_BINARY_DIR}/include/config.h)

string(TIMESTAMP UHD_BUILD_DATE "%a, %d %b %Y %H:%M:%S" UTC)
set(UHD_C_COMPILER "${CMAKE_C_COMPILER_ID} ${CMAKE_C_COMPILER_VERSION}")
set(UHD_CXX_COMPILER "${CMAKE_CXX_COMPILER_ID} ${CMAKE_CXX_COMPILER_VERSION}")
set(UHD_C_FLAGS "${CMAKE_C_FLAGS}")
set(UHD_CXX_FLAGS "${CMAKE_CXX_FLAGS}")
set(PKG_DATA_DIR "share/uhd")
set(_uhd_enabled_components "LibUHD;LibUHD - C API;USB;B200 (pysdrlib)")
configure_file(${H}/lib/build_info.cpp ${CMAKE_CURRENT_BINARY_DIR}/lib/build_info.cpp @ONLY)

# ------------------------------------------------------------------ resources: none for B2xx (upstream embeds X4xx/ZBX cal data)
list(APPEND CMAKE_MODULE_PATH ${H}/cmake/Modules)
set(UHD_BINARY_DIR ${CMAKE_CURRENT_BINARY_DIR})   # UHD's CMakeRC writes cmrc.hpp under it
include(CMakeRC)
cmrc_add_resource_library(uhd-resources ALIAS uhd_rc NAMESPACE rc)
set_property(TARGET uhd-resources PROPERTY POSITION_INDEPENDENT_CODE ON)

# ------------------------------------------------------------------ libuhd
add_library(uhd SHARED ${UHD_SOURCES})
target_include_directories(uhd PRIVATE ${UHD_INCLUDE_DIRS})
target_compile_definitions(uhd PRIVATE
    HAVE_CONFIG_H UHD_DLL_EXPORTS
    BOOST_ERROR_CODE_HEADER_ONLY BOOST_ASIO_HAS_STD_STRING_VIEW BOOST_ASIO_DISABLE_STD_EXPERIMENTAL_STRING_VIEW
    UHD_LOG_MIN_LEVEL=1 UHD_LOG_CONSOLE_LEVEL=2 UHD_LOG_FILE_LEVEL=2 UHD_LOG_CONSOLE_COLOR)
target_link_libraries(uhd PRIVATE
    Boost::filesystem Boost::thread Boost::headers
    PkgConfig::LIBUSB Threads::Threads ${CMAKE_DL_LIBS} uhd_rc)
set_target_properties(uhd PROPERTIES
    VERSION ${UHD_VERSION_MAJOR}.${UHD_VERSION_API}.${UHD_VERSION_ABI} SOVERSION ${UHD_VERSION_MAJOR})
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    # catch a missing source or library here rather than at import time; drop libraries nothing uses
    target_link_options(uhd PRIVATE -Wl,--no-undefined -Wl,--as-needed)
endif()
