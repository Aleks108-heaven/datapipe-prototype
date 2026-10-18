"""Draws the program icon: python packaging/make_icon.py        Output: packaging/icons/datapipe.{png,ico,icns}  (committed; run again only to change the design)

A white "d" (a ring and a stem: the data in a pipe) on a blue rounded square. Pure Python, no image library: shapes are described by
distances and every pixel is averaged over a grid of sample points, which is what gives smooth edges. The three files hold the same picture
at the sizes Windows (.ico) and macOS (.icns) ask for."""
import math
import struct
import sys
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent / "icons"
TOP, BOTTOM = (0x35, 0x63, 0xE9), (0x1B, 0x42, 0xB8)        # the app's accent blue, lighter at the top
WHITE = (255, 255, 255)
SAMPLES = 3                                                  # 3 x 3 sample points per pixel


def _rounded_square(x, y, margin=0.055, radius=0.21):
    """Distance outside (positive) or inside (negative) a rounded square that fills the picture except for a margin."""
    half = 0.5 - margin
    qx, qy = abs(x - 0.5) - (half - radius), abs(y - 0.5) - (half - radius)
    return math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - radius


def _glyph(x, y):
    """Distance to the white "d": a ring on the left and a stem with round ends on the right."""
    ring = abs(math.hypot(x - 0.425, y - 0.565) - 0.165) - 0.0575
    t = min(max((y - 0.25) / (0.80 - 0.25), 0), 1)           # the stem is a capsule between two points
    stem = math.hypot(x - 0.655, y - (0.25 + t * (0.80 - 0.25))) - 0.0575
    return min(ring, stem)


def render(size):
    """size x size pixels of (r, g, b, a) bytes, top row first."""
    rows = bytearray()
    step = 1.0 / (size * SAMPLES)
    edge = 1.0 / size                                        # a distance this far from the shape is fully outside
    for py in range(size):
        for px in range(size):
            cover_bg = cover_fg = 0
            for sy in range(SAMPLES):
                for sx in range(SAMPLES):
                    x, y = px / size + (sx + 0.5) * step, py / size + (sy + 0.5) * step
                    if _rounded_square(x, y) <= 0:
                        cover_bg += 1
                        if _glyph(x, y) <= 0:
                            cover_fg += 1
            n = SAMPLES * SAMPLES
            if not cover_bg:
                rows += b"\x00\x00\x00\x00"
                continue
            t = py / size
            base = [TOP[i] + (BOTTOM[i] - TOP[i]) * t for i in range(3)]
            mix = cover_fg / cover_bg                        # inside the square: how much of it is the white glyph
            rgb = [round(base[i] * (1 - mix) + WHITE[i] * mix) for i in range(3)]
            rows += bytes(rgb) + bytes([round(255 * cover_bg / n)])
    _ = edge
    return bytes(rows)


def png(size, rgba):
    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + rgba[y * size * 4:(y + 1) * size * 4] for y in range(size))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def dib(size, rgba):
    """The old bitmap form of an icon image (what every version of Windows reads): bottom row first, BGRA, then an empty AND mask."""
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, size * size * 4, 0, 0, 0, 0)
    pixels = b"".join(bytes([p[2], p[1], p[0], p[3]]) for y in range(size - 1, -1, -1)
                      for p in (rgba[(y * size + x) * 4:(y * size + x) * 4 + 4] for x in range(size)))
    mask = b"\x00" * (((size + 31) // 32) * 4 * size)
    return header + pixels + mask


def ico(images):
    """images: {size: rgba}. Small sizes as bitmaps, 128 and larger as PNG (Windows Vista and later)."""
    entries, blobs, offset = [], [], 6 + 16 * len(images)
    for size in sorted(images):
        blob = png(size, images[size]) if size >= 128 else dib(size, images[size])
        entries.append(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(blob), offset))
        blobs.append(blob)
        offset += len(blob)
    return struct.pack("<HHH", 0, 1, len(images)) + b"".join(entries) + b"".join(blobs)


ICNS_TYPES = {16: b"icp4", 32: b"icp5", 64: b"icp6", 128: b"ic07", 256: b"ic08", 512: b"ic09", 1024: b"ic10"}
ICNS_RETINA = {32: b"ic11", 64: b"ic12", 256: b"ic13", 512: b"ic14"}          # the same pictures, for the 2x sizes of 16, 32, 128 and 256


def icns(images):
    blobs = {size: png(size, rgba) for size, rgba in images.items()}
    parts = [(ICNS_TYPES[s], blobs[s]) for s in sorted(blobs) if s in ICNS_TYPES] + [(ICNS_RETINA[s], blobs[s]) for s in sorted(blobs) if s in ICNS_RETINA]
    body = b"".join(kind + struct.pack(">I", len(blob) + 8) + blob for kind, blob in parts)
    return b"icns" + struct.pack(">I", len(body) + 8) + body


def main():
    sizes = [16, 24, 32, 48, 64, 128, 256, 512, 1024]
    images = {}
    for s in sizes:
        print(f"drawing {s}x{s}", flush=True)
        images[s] = render(s)
    OUT.mkdir(exist_ok=True)
    (OUT / "datapipe.png").write_bytes(png(512, images[512]))
    (OUT / "datapipe.ico").write_bytes(ico({s: images[s] for s in (16, 24, 32, 48, 64, 128, 256)}))
    (OUT / "datapipe.icns").write_bytes(icns({s: images[s] for s in ICNS_TYPES}))
    print("wrote", ", ".join(sorted(p.name for p in OUT.iterdir())))


if __name__ == "__main__":
    sys.exit(main())
