"""Generate PWA icons: simple PNGs with medical teal background + white Rx cross."""
import struct, zlib, os

def create_png(width, height, r, g, b):
    """Create a minimal PNG with a medical cross + pill symbol."""
    def chunk(chunk_type, data):
        c = chunk_type + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)

    raw = b''
    cx, cy = width // 2, height // 2
    for y in range(height):
        raw += b'\x00'  # filter none
        for x in range(width):
            cross_w = max(3, width // 8)
            cross_h = max(3, height // 3)
            in_h = (cy - cross_w <= y <= cy + cross_w) and (cx - cross_h <= x <= cx + cross_h)
            in_v = (cy - cross_h <= y <= cy + cross_h) and (cx - cross_w <= x <= cx + cross_w)
            pill_cx, pill_cy = cx, int(cy * 0.6)
            pill_r = width // 5
            in_pill = ((x - pill_cx)**2 + (y - pill_cy)**2) < pill_r**2

            if in_h or in_v or in_pill:
                raw += struct.pack('BBBB', 255, 255, 255, 255)
            else:
                raw += struct.pack('BBBB', r, g, b, 255)

    ihdr = struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)
    png = b'\x89PNG\r\n\x1a\n'
    png += chunk(b'IHDR', ihdr)
    png += chunk(b'IDAT', zlib.compress(raw))
    png += chunk(b'IEND', b'')
    return png

out_dir = os.path.join(os.path.dirname(__file__), '..', 'frontend', 'icons')
os.makedirs(out_dir, exist_ok=True)

# Teal medical: #0D9488 = RGB(13, 148, 136)
for size, name in [(192, 'icon-192.png'), (512, 'icon-512.png'), (180, 'apple-touch-icon.png')]:
    png = create_png(size, size, 13, 148, 136)
    path = os.path.join(out_dir, name)
    with open(path, 'wb') as f:
        f.write(png)
    print(f'Created {path} ({len(png)} bytes)')

# Favicon 32x32
png = create_png(32, 32, 13, 148, 136)
path = os.path.join(out_dir, 'favicon.png')
with open(path, 'wb') as f:
    f.write(png)
print(f'Created {path}')
print('All icons generated!')
