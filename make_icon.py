# -*- coding: utf-8 -*-
"""生成应用图标 app.ico (16/24/32/48/64/128/256) + 预览 PNG。"""
from PIL import Image, ImageDraw, ImageFont
import os

SIZE = 256


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def base_image(size):
    """圆角方形渐变底 + GPU 芯片 + 迁移箭头。"""
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # ---- 渐变圆角底 (蓝 #1E63D0 -> 紫 #7B2FBE) ----
    c1, c2 = (30, 99, 208), (123, 47, 190)
    radius = int(s * 0.22)
    grad = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(s):
        gd.line([(0, y), (s, y)], fill=lerp(c1, c2, y / s) + (255,))
    mask = Image.new("L", (s, s), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([0, 0, s - 1, s - 1], radius=radius, fill=255)
    img.paste(grad, (0, 0), mask)

    lw = max(2, s // 32)

    # ---- GPU 芯片 (中央偏左) ----
    cx, cy = s * 0.42, s * 0.52
    chip = s * 0.34            # 芯片半边长
    x0, y0, x1, y1 = cx - chip, cy - chip, cx + chip, cy + chip
    # 引脚
    pin = s * 0.045
    for i in range(4):
        t = (i + 0.5) / 4
        px = x0 + (x1 - x0) * t
        d.line([(px, y0 - pin * 1.6), (px, y0)], fill=(255, 255, 255, 230), width=lw)
        d.line([(px, y1), (px, y1 + pin * 1.6)], fill=(255, 255, 255, 230), width=lw)
        py = y0 + (y1 - y0) * t
        d.line([(x0 - pin * 1.6, py), (x0, py)], fill=(255, 255, 255, 230), width=lw)
    # 芯片体 (深色) + 内芯 (绿)
    d.rounded_rectangle([x0, y0, x1, y1], radius=int(s * 0.03),
                        fill=(28, 34, 56, 255), outline=(255, 255, 255, 230),
                        width=lw)
    inner = chip * 0.52
    d.rounded_rectangle([cx - inner, cy - inner, cx + inner, cy + inner],
                        radius=int(s * 0.02), fill=(76, 217, 100, 255))

    # ---- 迁移箭头 (右上, 白色粗箭头指向右上) ----
    ax, ay = s * 0.70, s * 0.28
    ah = s * 0.16              # 半边长
    color = (255, 255, 255, 255)
    # 箭杆: 左下 -> 右上
    d.line([(ax - ah * 0.9, ay + ah * 0.9), (ax + ah * 0.45, ay - ah * 0.45)],
           fill=color, width=int(s * 0.055))
    # 箭头三角
    tip = (ax + ah, ay - ah)
    tri = [tip,
           (tip[0] - ah * 0.72, tip[1] + ah * 0.08),
           (tip[0] - ah * 0.08, tip[1] + ah * 0.72)]
    d.polygon(tri, fill=color)

    return img


sizes = [256, 128, 64, 48, 32, 24, 16]
imgs = {sz: base_image(sz) for sz in sizes}
imgs[256].save("app.ico", sizes=[(sz, sz) for sz in sizes],
               append_images=[imgs[sz] for sz in sizes[1:]])
imgs[256].save("icon_preview.png")
print("app.ico + icon_preview.png written")
