#!/usr/bin/env python3
"""CS:GO Legacy Panorama PBIN utility used by the revival updater.

Run from <CSGO>/csgo/panorama:
    py -3 pbin.py unpack _code.pbin
    py -3 pbin.py pack
    py -3 pbin.py patch_panorama
    py -3 pbin.py restore_panorama

The packer preserves the original PBIN per-file slot sizes. Modified files must
fit inside their original slots; shorter files are padded with spaces.
"""

from __future__ import annotations

import hashlib
import os
import pickle
import shutil
import struct
import sys
from pathlib import Path

PBIN_MAGIC = b"PAN\x02"
LOCAL_MAGIC = b"PK\x03\x04"
TABLE_FILE = Path("code.pbin.table")
STAGE_ROOT = Path("panorama")
PATCH_OFFSET = 1_308_935

# Known CS:GO Legacy panorama.dll hashes used by the revival build.
KNOWN_ORIGINAL_DLLS = {
    "cd469787211a122125faa44138263b62",
    "26fdf0b873cf92478780acdb464e4c1a",
}
KNOWN_PATCHED_DLLS = {
    "41e8682aa02de3b7fe275dc1f2187439",
    "79aaed383b356a4fdaabf39a4d05deec",
}


def md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def disk_path(pbin_name: str) -> Path:
    return Path(pbin_name.replace("\\", os.sep))


def unpack(src_name: str) -> int:
    src = Path(src_name)
    if not src.is_file():
        print(f"[pbin] missing input: {src}")
        return 1

    data = src.read_bytes()
    if len(data) < 516 or data[:4] != PBIN_MAGIC:
        print("[pbin] invalid PBIN signature")
        return 2

    pos = 4 + 512
    table: dict[str, object] = {}

    while pos + 30 <= len(data):
        header = data[pos:pos + 30]
        if header[:4] != LOCAL_MAGIC:
            table["__CODE_PBIN_END__"] = data[pos:]
            break

        comp_size = int.from_bytes(header[18:22], "little")
        uncomp_size = int.from_bytes(header[22:26], "little")
        name_len = int.from_bytes(header[26:28], "little")
        extra_len = int.from_bytes(header[28:30], "little")
        if comp_size != uncomp_size:
            print("[pbin] compressed PBIN entry is unsupported")
            return 2

        pos += 30
        if pos + name_len + extra_len + uncomp_size > len(data):
            print("[pbin] truncated PBIN entry")
            return 2

        name = data[pos:pos + name_len].decode("utf-8")
        pos += name_len + extra_len
        payload = data[pos:pos + uncomp_size]
        pos += uncomp_size

        out = disk_path(name)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(payload)
        table[name] = uncomp_size
        print(name)

    if "__CODE_PBIN_END__" not in table:
        print("[pbin] PBIN tail was not found")
        return 2

    with TABLE_FILE.open("wb") as fh:
        pickle.dump(table, fh, protocol=4)
    print(f"[pbin] unpacked {len(table) - 1} files")
    return 0


def pack() -> int:
    if not TABLE_FILE.is_file():
        print("[pbin] code.pbin.table missing; unpack _code.pbin first")
        return 1

    with TABLE_FILE.open("rb") as fh:
        table = pickle.load(fh)

    if not isinstance(table, dict) or "__CODE_PBIN_END__" not in table:
        print("[pbin] invalid code.pbin.table")
        return 1

    out = bytearray(PBIN_MAGIC)
    out.extend(b"\x00" * 512)

    for name, original_size in table.items():
        if name == "__CODE_PBIN_END__":
            continue
        if not isinstance(name, str) or not isinstance(original_size, int):
            print("[pbin] invalid table entry")
            return 1

        path = disk_path(name)
        if not path.is_file():
            print(f"[pbin] staged file missing: {name}")
            return 1

        payload = path.read_bytes()
        if len(payload) > original_size:
            print(f"[pbin] {name}: {len(payload)} B > {original_size} B")
            return 2

        name_bytes = name.encode("utf-8")
        # Stored entries are uncompressed and retain the original fixed slot size.
        header = bytearray()
        header += LOCAL_MAGIC
        header += b"\x0A\x00"       # version
        header += b"\x00\x00"       # flags
        header += b"\x00\x00"       # compression = store
        header += b"\x00\x00"       # time
        header += b"\x00\x00"       # date
        header += b"\x82\xC2\xA9\x51"
        header += struct.pack("<I", original_size)
        header += struct.pack("<I", original_size)
        header += struct.pack("<H", len(name_bytes))
        header += b"\x00\x00"       # extra length

        out += header
        out += name_bytes
        out += payload
        out += b" " * (original_size - len(payload))

    tail = table["__CODE_PBIN_END__"]
    if not isinstance(tail, (bytes, bytearray)):
        print("[pbin] invalid PBIN tail")
        return 1
    out += tail

    Path("code.pbin").write_bytes(out)
    print(f"[pbin] wrote code.pbin ({len(out)} bytes)")
    return 0


def panorama_dll() -> Path:
    # Tool runs from <CSGO>/csgo/panorama.
    return (Path.cwd() / ".." / ".." / "bin" / "panorama.dll").resolve()


def patch_panorama() -> int:
    dll = panorama_dll()
    if not dll.is_file():
        print(f"[pbin] panorama.dll not found: {dll}")
        return 3

    current_hash = md5(dll)
    if current_hash in KNOWN_PATCHED_DLLS:
        print("[pbin] panorama.dll already patched")
        return 0

    raw = bytearray(dll.read_bytes())
    if len(raw) <= PATCH_OFFSET:
        print("[pbin] panorama.dll is too small / unsupported")
        return 1

    # A previously patched DLL with a different timestamp/hash is still safe to
    # recognize by the patched branch byte, provided its backup is a known build.
    backup = Path(str(dll) + ".bak")
    if raw[PATCH_OFFSET] == 0xEB:
        if backup.is_file() and md5(backup) in KNOWN_ORIGINAL_DLLS:
            print("[pbin] panorama.dll already patched (known backup)")
            return 0
        print("[pbin] panorama.dll has patched byte but unknown build")
        return 1

    if current_hash not in KNOWN_ORIGINAL_DLLS:
        print(f"[pbin] unsupported panorama.dll MD5: {current_hash}")
        return 1

    if not backup.exists():
        shutil.copy2(dll, backup)

    raw[PATCH_OFFSET] = 0xEB
    dll.write_bytes(raw)
    print("[pbin] patched panorama.dll")
    return 0


def restore_panorama() -> int:
    dll = panorama_dll()
    backup = Path(str(dll) + ".bak")
    if not backup.is_file():
        print("[pbin] panorama.dll.bak not found")
        return 1
    if md5(backup) not in KNOWN_ORIGINAL_DLLS:
        print("[pbin] backup is not a known Legacy panorama.dll")
        return 2
    shutil.copy2(backup, dll)
    print("[pbin] restored panorama.dll")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: pbin.py unpack <file> | pack | patch_panorama | restore_panorama")
        return 1

    cmd = argv[1].lower()
    if cmd == "unpack":
        if len(argv) != 3:
            print("usage: pbin.py unpack <file>")
            return 1
        return unpack(argv[2])
    if cmd == "pack":
        return pack()
    if cmd == "patch_panorama":
        return patch_panorama()
    if cmd == "restore_panorama":
        return restore_panorama()

    print(f"unknown command: {argv[1]}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
