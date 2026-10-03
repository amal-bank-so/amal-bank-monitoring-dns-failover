#!/usr/bin/env python3
"""Build lambda/notify.zip from notify.py, byte-for-byte reproducibly (fixed timestamps), so the committed zip can be checked.

  python lambda/build.py          write notify.zip
  python lambda/build.py --check  exit 1 if the committed notify.zip is not what notify.py builds
"""
import io
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))


def build():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in ("notify.py", "logo.png"):  # logo.png is optional: the email falls back to a text header without it
            path = os.path.join(HERE, name)
            if not os.path.exists(path):
                continue
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            with open(path, "rb") as fh:
                z.writestr(info, fh.read())
    return buf.getvalue()


if __name__ == "__main__":
    target = os.path.join(HERE, "notify.zip")
    data = build()
    if "--check" in sys.argv:
        with open(target, "rb") as fh:
            same = fh.read() == data
        print("notify.zip is up to date" if same else "notify.zip is STALE: run python lambda/build.py")
        sys.exit(0 if same else 1)
    with open(target, "wb") as fh:
        fh.write(data)
    print("wrote", target, len(data), "bytes")
