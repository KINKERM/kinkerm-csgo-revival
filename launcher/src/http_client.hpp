#pragma once

#include <string>

// Minimal HTTP/1.0 GET client over raw sockets. Plain HTTP only (no TLS) - the
// revival server is self-hosted and the inventory endpoint carries no secrets.
// If you expose it publicly, front it with a reverse proxy for TLS.
namespace http
{
    struct Response
    {
        bool ok = false;
        int statusCode = 0;
        std::string body;
        std::string error;
    };

    // Perform a GET request against a full URL (http://host[:port]/path).
    Response Get(const std::string &url);
}
