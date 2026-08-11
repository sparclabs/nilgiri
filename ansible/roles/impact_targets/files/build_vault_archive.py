#!/usr/bin/env python3
"""Build the M10 records-vault export: gzip -> AES-256-CBC -> split segments.

Runs on the ANSIBLE CONTROL HOST (the libvirt host), not in the range: the
guests have no egress and no crypto tooling we want to depend on, and building
here keeps the archive byte-identical across re-runs so `make m10` stays
idempotent.

  plaintext : ~`--rows` synthetic record lines, objective row LAST
  archive   : gzip(plaintext)
  at rest   : iv || AES-256-CBC(key = SHA-256(master secret ASCII), PKCS7)
  segments  : the ciphertext cut into `--parts` equal slices,
              <basename>.000 .. <basename>.<parts-1>

The objective row sits at the end of the plaintext, so a partial stream inflates
to everything except the flag -- which is why the last segment (held by the
monitor until m10.s3) is what gates m10.s4.

Everything is derived from (master secret, flag): same inputs -> same bytes.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import io
import json
import os
import random

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

DEPARTMENTS = (
    "claims", "underwriting", "actuarial", "billing", "compliance",
    "member-services", "provider-network", "appeals",
)
REGIONS = ("emea-1", "emea-2", "amer-1", "amer-2", "apac-1")


def build_plaintext(rows: int, flag: str, rng: random.Random) -> bytes:
    out = io.StringIO()
    out.write("record_id,subject_ref,department,region,opened,amount,digest\n")
    for i in range(rows):
        out.write(
            "REC-%07d,SUBJ-%s,%s,%s,20%02d-%02d-%02d,%d.%02d,%s\n"
            % (
                i,
                rng.getrandbits(48).to_bytes(6, "big").hex(),
                rng.choice(DEPARTMENTS),
                rng.choice(REGIONS),
                rng.randint(20, 26),
                rng.randint(1, 12),
                rng.randint(1, 28),
                rng.randint(0, 99999),
                rng.randint(0, 99),
                rng.getrandbits(128).to_bytes(16, "big").hex(),
            )
        )
    # The objective row is the LAST line of the archive on purpose.
    out.write("OBJECTIVE,records-vault-export,%s\n" % flag)
    return out.getvalue().encode("utf-8")


def encrypt(plaintext_gz: bytes, master_secret: str, flag: str) -> bytes:
    key = hashlib.sha256(master_secret.encode("ascii")).digest()
    # Deterministic IV so repeated provisioning writes identical files; it is
    # prefixed to the archive exactly as the runbook documents.
    iv = hashlib.sha256((master_secret + "|" + flag).encode("ascii")).digest()[:16]
    pad = 16 - (len(plaintext_gz) % 16)
    padded = plaintext_gz + bytes([pad]) * pad
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return iv + encryptor.update(padded) + encryptor.finalize()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--master-secret", required=True, help="M9 objective secret (m9.s4)")
    ap.add_argument("--flag", required=True, help="m10.s4 UUID -- the objective row")
    ap.add_argument("--rows", type=int, default=12000)
    ap.add_argument("--parts", type=int, default=16)
    ap.add_argument("--basename", default="records-export.gz")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    if args.parts < 2:
        raise SystemExit("--parts must be >= 2 (the last segment is the gate)")

    rng = random.Random(hashlib.sha256(args.flag.encode("ascii")).digest())
    plaintext = build_plaintext(args.rows, args.flag, rng)
    # mtime=0 so gzip framing is stable across runs.
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=9, mtime=0) as gz:
        gz.write(plaintext)
    blob = encrypt(buf.getvalue(), args.master_secret, args.flag)

    os.makedirs(args.out_dir, exist_ok=True)
    size = len(blob)
    step = size // args.parts
    segments = []
    for idx in range(args.parts):
        start = idx * step
        end = size if idx == args.parts - 1 else (idx + 1) * step
        path = os.path.join(args.out_dir, "%s.%03d" % (args.basename, idx))
        with open(path, "wb") as fh:
            fh.write(blob[start:end])
        segments.append(path)

    with open(segments[-1], "rb") as fh:
        final_b64 = base64.b64encode(fh.read()).decode("ascii")

    print(json.dumps({
        "plaintext_bytes": len(plaintext),
        "archive_bytes": size,
        "segments": segments,
        "host_segments": segments[:-1],
        "final_segment": segments[-1],
        "final_segment_b64": final_b64,
    }))


if __name__ == "__main__":
    main()
