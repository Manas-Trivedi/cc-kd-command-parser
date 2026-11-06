"""Pack a model directory into a byte-for-byte reproducible tarball and write its SHA-256.

  python scripts/package_model.py v1 artifacts/v1
  -> artifacts/release/model-v1.tar.gz and artifacts/release/model-v1.sha256

Files are added in sorted order with fixed mtime/owner, and gzip's header timestamp is 0,
so packing the same files always gives the same checksum. model.sha256 in object storage and
the GitHub Release asset are this same checksum.
"""
import gzip
import hashlib
import io
import sys
import tarfile
from pathlib import Path


def pack(version, src):
    src = Path(src)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for f in sorted(p for p in src.iterdir() if p.is_file()):
            info = tarfile.TarInfo(f"model-{version}/{f.name}")
            data = f.read_bytes()
            info.size, info.mtime, info.mode = len(data), 0, 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(data))
    gz = io.BytesIO()
    with gzip.GzipFile(fileobj=gz, mode="wb", mtime=0, filename="") as g:
        g.write(buf.getvalue())
    return gz.getvalue()


def main():
    version, src = sys.argv[1], sys.argv[2]
    out = Path("artifacts/release")
    out.mkdir(parents=True, exist_ok=True)
    blob = pack(version, src)
    digest = hashlib.sha256(blob).hexdigest()
    (out / f"model-{version}.tar.gz").write_bytes(blob)
    (out / f"model-{version}.sha256").write_text(f"{digest}  model-{version}.tar.gz\n")
    print(f"{digest}  {out}/model-{version}.tar.gz ({len(blob) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
