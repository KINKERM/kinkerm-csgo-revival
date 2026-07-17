#include "http_client.hpp"

#include <cstdio>
#include <cstring>
#include <string>

#if defined(_WIN32)
#  include <winsock2.h>
#  include <ws2tcpip.h>
#  pragma comment(lib, "ws2_32.lib")
using socket_t = SOCKET;
static const socket_t kInvalidSocket = INVALID_SOCKET;
#else
#  include <arpa/inet.h>
#  include <netdb.h>
#  include <netinet/in.h>
#  include <sys/socket.h>
#  include <sys/types.h>
#  include <unistd.h>
using socket_t = int;
static const socket_t kInvalidSocket = -1;
#endif

namespace
{
    struct SocketSystem
    {
        SocketSystem()
        {
#if defined(_WIN32)
            WSADATA data;
            WSAStartup(MAKEWORD(2, 2), &data);
#endif
        }
        ~SocketSystem()
        {
#if defined(_WIN32)
            WSACleanup();
#endif
        }
    };

    void CloseSocket(socket_t s)
    {
#if defined(_WIN32)
        closesocket(s);
#else
        close(s);
#endif
    }

    // Parse http://host[:port]/path into components. Returns false if not http.
    bool ParseUrl(const std::string &url, std::string &host, std::string &port, std::string &path)
    {
        const std::string scheme = "http://";
        if (url.compare(0, scheme.size(), scheme) != 0)
            return false;

        std::string rest = url.substr(scheme.size());
        size_t slash = rest.find('/');
        std::string authority = (slash == std::string::npos) ? rest : rest.substr(0, slash);
        path = (slash == std::string::npos) ? "/" : rest.substr(slash);

        size_t colon = authority.find(':');
        if (colon == std::string::npos)
        {
            host = authority;
            port = "80";
        }
        else
        {
            host = authority.substr(0, colon);
            port = authority.substr(colon + 1);
        }
        return !host.empty();
    }
}

namespace http
{
    Response Get(const std::string &url)
    {
        Response resp;
        static SocketSystem socketSystem; // init winsock once

        std::string host, port, path;
        if (!ParseUrl(url, host, port, path))
        {
            resp.error = "only http:// URLs are supported: " + url;
            return resp;
        }

        addrinfo hints;
        std::memset(&hints, 0, sizeof(hints));
        hints.ai_family = AF_UNSPEC;
        hints.ai_socktype = SOCK_STREAM;

        addrinfo *result = nullptr;
        if (getaddrinfo(host.c_str(), port.c_str(), &hints, &result) != 0 || !result)
        {
            resp.error = "DNS resolution failed for " + host;
            return resp;
        }

        socket_t sock = kInvalidSocket;
        for (addrinfo *ai = result; ai != nullptr; ai = ai->ai_next)
        {
            sock = socket(ai->ai_family, ai->ai_socktype, ai->ai_protocol);
            if (sock == kInvalidSocket)
                continue;
            if (connect(sock, ai->ai_addr, static_cast<int>(ai->ai_addrlen)) == 0)
                break;
            CloseSocket(sock);
            sock = kInvalidSocket;
        }
        freeaddrinfo(result);

        if (sock == kInvalidSocket)
        {
            resp.error = "could not connect to " + host + ":" + port;
            return resp;
        }

        std::string request =
            "GET " + path + " HTTP/1.0\r\n" +
            "Host: " + host + "\r\n" +
            "User-Agent: csgo-revival-launcher/1.0\r\n" +
            "Connection: close\r\n\r\n";

        const char *ptr = request.c_str();
        size_t remaining = request.size();
        while (remaining > 0)
        {
            int sent = static_cast<int>(send(sock, ptr, static_cast<int>(remaining), 0));
            if (sent <= 0)
            {
                resp.error = "send failed";
                CloseSocket(sock);
                return resp;
            }
            ptr += sent;
            remaining -= static_cast<size_t>(sent);
        }

        std::string raw;
        char buffer[4096];
        for (;;)
        {
            int received = static_cast<int>(recv(sock, buffer, sizeof(buffer), 0));
            if (received < 0)
            {
                resp.error = "recv failed";
                CloseSocket(sock);
                return resp;
            }
            if (received == 0)
                break;
            raw.append(buffer, static_cast<size_t>(received));
        }
        CloseSocket(sock);

        size_t headerEnd = raw.find("\r\n\r\n");
        if (headerEnd == std::string::npos)
        {
            resp.error = "malformed HTTP response";
            return resp;
        }

        std::string headers = raw.substr(0, headerEnd);
        resp.body = raw.substr(headerEnd + 4);

        // status line: HTTP/1.x <code> <reason>
        size_t sp = headers.find(' ');
        if (sp != std::string::npos)
            resp.statusCode = std::atoi(headers.c_str() + sp + 1);

        resp.ok = (resp.statusCode >= 200 && resp.statusCode < 300);
        if (!resp.ok && resp.error.empty())
            resp.error = "HTTP status " + std::to_string(resp.statusCode);
        return resp;
    }
}
