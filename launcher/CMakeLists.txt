cmake_minimum_required(VERSION 3.15)
project(csgo_revival_launcher CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(csgo-launcher
    src/main.cpp
    src/config.cpp
    src/http_client.cpp
)

target_include_directories(csgo-launcher PRIVATE src)

if(WIN32)
    # Winsock is linked via #pragma comment in http_client.cpp for MSVC.
    # For MinGW, link it explicitly:
    if(NOT MSVC)
        target_link_libraries(csgo-launcher PRIVATE ws2_32)
    endif()
endif()

# On some Linux toolchains std::filesystem needs an explicit link.
if(UNIX AND NOT APPLE)
    target_link_libraries(csgo-launcher PRIVATE stdc++fs)
endif()
