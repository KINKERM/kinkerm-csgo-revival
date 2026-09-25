#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import socket
import struct

COOKIE = 0x293A206F6C6C6548


def read_client_version(csgo_dir: str) -> int:
    for path in (
        os.path.join(csgo_dir, "csgo", "steam.inf"),
        os.path.join(csgo_dir, "steam.inf"),
    ):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    line = raw.strip()
                    if line.lower().startswith("clientversion="):
                        return int(line.split("=", 1)[1].strip())
        except (OSError, ValueError):
            pass
    return 0


def steamid64_from_account_id(account_id: int) -> int:
    return (1 << 56) | (1 << 52) | (1 << 32) | account_id


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("port", type=int)
    ap.add_argument("account_id", help="decimal or 0x-prefixed AccountID")
    ap.add_argument(
        "--csgo-dir",
        default=r"C:\Program Files (x86)\Steam\steamapps\common\csgo legacy",
    )
    ap.add_argument("--client-version", type=int, default=0)
    ap.add_argument("--stage", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=3.0)
    args = ap.parse_args()

    account_id = int(args.account_id, 0)
    host_version = args.client_version or read_client_version(args.csgo_dir)
    if not host_version:
        print("[probe] ERROR: could not determine ClientVersion from steam.inf; pass --client-version")
        return 2

    resolved = socket.gethostbyname(args.host)
    steamid64 = steamid64_from_account_id(account_id)
    token = account_id

    packet = struct.pack(
        "<IBIIIQQ",
        0xFFFFFFFF,
        0x21,
        host_version,
        token,
        args.stage,
        COOKIE,
        steamid64,
    )
    assert len(packet) == 33

    print(
        f"[probe] -> {args.host}:{args.port} ({resolved}) "
        f"stage={args.stage} ClientVersion={host_version} "
        f"account={account_id} cookie=0x{COOKIE:016x}"
    )

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(args.timeout)
    try:
        sock.sendto(packet, (resolved, args.port))
        data, addr = sock.recvfrom(2048)
    except socket.timeout:
        print("[probe] TIMEOUT: no UDP reply. The 0x21 did not get a 0x25 back through the public endpoint.")
        return 3
    finally:
        sock.close()

    print(f"[probe] <- {addr[0]}:{addr[1]} bytes={len(data)} hex={data.hex()}")

    if len(data) != 19:
        print("[probe] reply arrived, but it is not the 19-byte S2A_RESERVE_CHECK_RESPONSE")
        return 4

    header = struct.unpack_from("<I", data, 0)[0]
    opcode = data[4]
    host_echo = struct.unpack_from("<I", data, 5)[0]
    token_echo = struct.unpack_from("<I", data, 9)[0]
    stage = struct.unpack_from("<I", data, 13)[0]
    awaiting = data[17]
    total = data[18]

    print(
        f"[probe] parsed header=0x{header:08x} opcode=0x{opcode:02x} "
        f"host={host_echo} token={token_echo} stage={stage} "
        f"awaiting={awaiting} total={total}"
    )

    if header == 0xFFFFFFFF and opcode == 0x25:
        print("[probe] SUCCESS: Playit/public UDP carries the native reservation-check response.")
        return 0

    print("[probe] unexpected response")
    return 5


if __name__ == "__main__":
    raise SystemExit(main())
