from __future__ import annotations

import json
import struct
import sys
from pathlib import Path


def read_png_size(path: Path) -> dict[str, object]:
    with path.open("rb") as handle:
        signature = handle.read(8)
        if signature != b"\x89PNG\r\n\x1a\n":
            raise ValueError(f"{path} is not a PNG file")
        length = struct.unpack(">I", handle.read(4))[0]
        chunk_type = handle.read(4)
        if chunk_type != b"IHDR" or length != 13:
            raise ValueError(f"{path} has an invalid PNG header")
        width, height, bit_depth, color_type, *_ = struct.unpack(">IIBBBBB", handle.read(13))
    has_alpha = color_type in {4, 6}
    return {
        "path": str(path),
        "width": width,
        "height": height,
        "bit_depth": bit_depth,
        "color_type": color_type,
        "has_alpha": has_alpha,
    }


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: probe_media.py <png-path>")
    path = Path(sys.argv[1]).resolve()
    print(json.dumps(read_png_size(path), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
