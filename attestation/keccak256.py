#!/usr/bin/env python3
"""Pure-stdlib Keccak-256 (Ethereum's pre-NIST keccak, NOT hashlib's SHA3-256).

The DocChain blockHash is an EIP-712 keccak256 hashStruct; the repo otherwise
shells out to Foundry `cast keccak` (one subprocess per hash), which is far too
slow for the 24,926-day deep-history spine + 819 month-roots. This implements
Keccak-f[1600] + the keccak sponge (rate 1088, pad `0x01…0x80`) with zero
dependencies, so the whole spine computes in-process in seconds.

Validated against the canonical vectors (keccak256("") / "abc") and, in the
spine builder, byte-for-byte against `cast keccak` on sampled days.
"""
from __future__ import annotations

_MASK = (1 << 64) - 1

_RC = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)


def _rol(v: int, n: int) -> int:
    return ((v << n) | (v >> (64 - n))) & _MASK if n else v


def _keccak_f(a: list) -> None:
    for rnd in range(24):
        # theta
        c = [a[x] ^ a[x + 5] ^ a[x + 10] ^ a[x + 15] ^ a[x + 20] for x in range(5)]
        d = [c[(x + 4) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(0, 25, 5):
                a[x + y] ^= d[x]
        # rho + pi
        x, y = 1, 0
        cur = a[x + 5 * y]
        for t in range(24):
            x, y = y, (2 * x + 3 * y) % 5
            cur, a[x + 5 * y] = a[x + 5 * y], _rol(cur, ((t + 1) * (t + 2) // 2) % 64)
        # chi
        for y in range(0, 25, 5):
            row = [a[y + x] for x in range(5)]
            for x in range(5):
                a[y + x] = row[x] ^ ((~row[(x + 1) % 5]) & row[(x + 2) % 5])
        # iota
        a[0] ^= _RC[rnd]


def keccak256(data: bytes) -> bytes:
    rate = 136  # 1088 bits = 17 lanes
    padded = bytearray(data)
    padded.append(0x01)
    while len(padded) % rate != 0:
        padded.append(0x00)
    padded[-1] |= 0x80
    state = [0] * 25
    for base in range(0, len(padded), rate):
        for i in range(17):
            state[i] ^= int.from_bytes(padded[base + i * 8: base + i * 8 + 8], "little")
        _keccak_f(state)
    return b"".join(state[i].to_bytes(8, "little") for i in range(4))


def keccak256_hex(data: bytes) -> str:
    return keccak256(data).hex()
