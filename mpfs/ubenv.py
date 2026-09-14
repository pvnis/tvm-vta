#!/usr/bin/env python3
"""Read or edit a U-Boot 'env in FAT' blob (4-byte LE CRC32 + NUL-separated key=value).

usage: ubenv.py show <file> [key ...]
       ubenv.py set  <in> <out> KEY=VALUE [KEY=VALUE ...]
"""
import sys, zlib

def load(path):
    blob = open(path, "rb").read()
    stored = int.from_bytes(blob[:4], "little")
    payload = blob[4:]
    calc = zlib.crc32(payload) & 0xFFFFFFFF
    env = {}
    order = []
    for entry in payload.split(b"\x00"):
        if not entry:
            break
        k, _, v = entry.partition(b"=")
        k = k.decode(); env[k] = v.decode()
        order.append(k)
    return blob, stored, calc, env, order

def save(path, size, env, order):
    payload = b"".join(f"{k}={env[k]}".encode() + b"\x00" for k in order)
    payload += b"\x00"                       # terminating empty entry
    if len(payload) > size - 4:
        raise SystemExit(f"env too big: {len(payload)} > {size-4}")
    payload += b"\xff" * (size - 4 - len(payload))
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    open(path, "wb").write(crc.to_bytes(4, "little") + payload)
    return crc

mode = sys.argv[1]
blob, stored, calc, env, order = load(sys.argv[2])
if mode == "show":
    print(f"# size={len(blob)} crc_stored=0x{stored:08x} crc_calc=0x{calc:08x} "
          f"{'OK' if stored == calc else 'MISMATCH'} vars={len(env)}")
    keys = sys.argv[3:] or order
    for k in keys:
        print(f"{k}={env.get(k, '<unset>')}")
elif mode == "set":
    out = sys.argv[3]
    for kv in sys.argv[4:]:
        k, _, v = kv.partition("=")
        if k not in env:
            order.append(k)
        env[k] = v
    crc = save(out, len(blob), env, order)
    print(f"wrote {out} crc=0x{crc:08x}")
else:
    raise SystemExit(__doc__)
