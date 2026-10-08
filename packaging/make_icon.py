"""アプリのアイコン（ghms.ico）を作る。ビルドのときに実行する（Pillow が必要）。"""
import sys

from PIL import Image, ImageDraw

out = sys.argv[1] if len(sys.argv) > 1 else "ghms.ico"
size = 256
img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((8, 8, size - 8, size - 8), radius=56, fill=(255, 138, 61, 255))
# 家の形
d.polygon([(128, 52), (44, 128), (212, 128)], fill=(255, 255, 255, 255))
d.rectangle((70, 124, 186, 204), fill=(255, 255, 255, 255))
d.rounded_rectangle((112, 150, 144, 204), radius=6, fill=(255, 138, 61, 255))
# 花
for dx, dy in [(0, -14), (14, 0), (0, 14), (-14, 0)]:
    d.ellipse((188 + dx - 11, 60 + dy - 11, 188 + dx + 11, 60 + dy + 11), fill=(255, 214, 102, 255))
d.ellipse((179, 51, 197, 69), fill=(255, 255, 255, 255))
img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("wrote", out)
