"""
Thumbnail rendering: headline text + face cutout + optional prop image (e.g. a book cover),
in YouTube wide (1280x720), Instagram feed (1080x1350) and Story/Reel (1080x1920) formats.
"""
import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_PATH = os.path.join(HERE, "assets", "fonts", "Poppins-Bold.ttf")

WHITE = (255, 255, 255)
YELLOW = (255, 214, 0)
RED = (214, 24, 32)
PALETTE = [WHITE, YELLOW, RED]

FORMATS = {
    "youtube_wide": (1280, 720),
    "instagram_feed": (1080, 1350),
    "instagram_story": (1080, 1920),
}


def _prep_face(face_path, flip=False):
    im = Image.open(face_path).convert("RGBA")
    im = im.crop(im.getchannel("A").getbbox()) if im.getchannel("A").getbbox() else im
    if flip:
        im = im.transpose(Image.FLIP_LEFT_RIGHT)
    r, g, b, a = im.split()
    rgb = Image.merge("RGB", (r, g, b))
    rgb = ImageEnhance.Contrast(rgb).enhance(1.12)
    rgb = ImageEnhance.Color(rgb).enhance(1.15)
    rgb = ImageEnhance.Brightness(rgb).enhance(1.08)
    return Image.merge("RGBA", (*rgb.split(), a))


DEFAULT_BG = (214, 24, 32)  # matches the original red vignette


def _bg(W, H, cx, cy, base=DEFAULT_BG):
    """Radial vignette from near-black edges to `base` at the glow center,
    so any chosen background color keeps the same look-and-feel."""
    edge = (10, 8, 10)
    small = Image.new("RGB", (W // 8, H // 8))
    sp = small.load()
    for y in range(small.height):
        for x in range(small.width):
            dx = (x * 8 - cx) / W
            dy = (y * 8 - cy) / H
            d = (dx * dx + dy * dy) ** 0.5
            t = max(0, 1 - d * 1.5) ** 1.6
            sp[x, y] = (
                int(edge[0] + t * (base[0] - edge[0])),
                int(edge[1] + t * (base[1] - edge[1])),
                int(edge[2] + t * (base[2] - edge[2])),
            )
    return small.resize((W, H), Image.BICUBIC).convert("RGBA")


def _stroke_text(img, xy, txt, font, fill, anchor, sw):
    d = ImageDraw.Draw(img)
    d.text((xy[0] + sw // 2, xy[1] + sw // 2 + 2), txt, font=font, fill=(0, 0, 0),
           anchor=anchor, stroke_width=sw, stroke_fill=(0, 0, 0))
    d.text(xy, txt, font=font, fill=fill, anchor=anchor, stroke_width=sw, stroke_fill=(0, 0, 0))


def _fit(txt, maxw, maxsize):
    s = maxsize
    f = ImageFont.truetype(FONT_PATH, s)
    while s > 10:
        f = ImageFont.truetype(FONT_PATH, s)
        if f.getlength(txt) <= maxw:
            return f
        s -= 2
    return f


def _paste_glow(canvas, layer, pos, color=RED, blur=18, grow=8):
    pad = blur * 3 + grow * 2
    a = Image.new("L", (layer.width + 2 * pad, layer.height + 2 * pad), 0)
    a.paste(layer.getchannel("A"), (pad, pad))
    al = a.filter(ImageFilter.MaxFilter(grow * 2 + 1)).filter(ImageFilter.GaussianBlur(blur))
    glow = Image.new("RGBA", a.size, color + (0,))
    glow.putalpha(al.point(lambda v: int(v * 0.9)))
    x, y = pos[0] - pad, pos[1] - pad
    cx0, cy0 = max(0, x), max(0, y)
    canvas.alpha_composite(glow.crop((cx0 - x, cy0 - y, glow.width, glow.height)), (cx0, cy0))


def _shadow(canvas, layer, pos, off=14, blur=14):
    al = layer.getchannel("A").filter(ImageFilter.GaussianBlur(blur)).point(lambda v: int(v * 0.75))
    sh = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    sh.putalpha(al)
    canvas.alpha_composite(sh, (pos[0] + off, pos[1] + off))


def auto_lines(headline):
    """Split a headline into <=4 punchy lines, cycling white/yellow/red."""
    words = headline.upper().split()
    n_lines = 2 if len(words) <= 5 else (3 if len(words) <= 9 else 4)
    per = max(1, -(-len(words) // n_lines))
    lines = [" ".join(words[i:i + per]) for i in range(0, len(words), per)][:4]
    return [(l, PALETTE[i % 3]) for i, l in enumerate(lines)]


def render_thumbnail(fmt, headline, tag=None, face_path=None, prop_path=None, bg_color=None,
                      flip_face=False):
    """Returns a PIL Image. fmt is a key in FORMATS. bg_color is an (r,g,b) tuple
    for the vignette's hot color; defaults to the original red. flip_face mirrors
    the face PNG horizontally (for an upload that came out backwards)."""
    W, H = FORMATS[fmt]
    land = W > H
    lines = auto_lines(headline)
    base = bg_color or DEFAULT_BG

    canvas = _bg(W, H, W * 0.65, H * 0.4, base=base)

    face = _prep_face(face_path, flip=flip_face) if face_path and os.path.exists(face_path) else None
    if face:
        pw = int(W * (0.60 if land else (0.98 if H / W > 1.6 else 0.98)))
        pp = face.resize((pw, int(pw * face.height / face.width)), Image.LANCZOS)
        pos = ((W - pw + int(W * 0.04)) if land else (W - pw) // 2, H - pp.height)
        _paste_glow(canvas, pp, pos, color=base, blur=int(H * 0.03), grow=int(H * 0.008))
        canvas.alpha_composite(pp, pos)

    maxw = int(W * (0.60 if land else 0.90))
    fonts = [_fit(t, maxw, int(H * (0.125 if land else 0.07))) for t, _ in lines]
    x = int(W * 0.035) if land else W // 2
    y = int(H * 0.06) if land else int(H * 0.045)
    anchor = "lt" if land else "mt"
    sw = max(5, int(H * 0.008)) if land else max(6, int(H * 0.004))
    for (txt, col), f in zip(lines, fonts):
        _stroke_text(canvas, (x, y), txt, f, col, anchor, sw)
        y += int(f.size * 1.08)
    text_bottom = y

    if tag:
        f = _fit(tag, int(maxw * 0.95), int(H * (0.05 if land else 0.032)))
        tw = f.getlength(tag)
        pad = int(f.size * 0.35)
        tx = x if land else (W - tw) // 2
        ty = text_bottom + int(H * 0.015)
        d = ImageDraw.Draw(canvas)
        d.rounded_rectangle([tx - pad, ty - pad * 0.4, tx + tw + pad, ty + f.size * 1.15 + pad * 0.4],
                             radius=pad // 2, fill=YELLOW)
        d.text((tx, ty), tag, font=f, fill=(10, 10, 10), anchor="lt")
        text_bottom = int(ty + f.size * 1.6)

    if prop_path and os.path.exists(prop_path):
        rem = H - text_bottom - int(H * 0.05)
        bw = int(min(rem / 1.5 * 0.88, W * (0.19 if land else 0.25)))
        prop = Image.open(prop_path).convert("RGBA")
        bh = int(bw * prop.height / prop.width)
        prop = prop.resize((bw, bh), Image.LANCZOS)
        prop_r = prop.rotate(7, expand=True, resample=Image.BICUBIC)
        bpos = (int(W * 0.03), H - prop_r.height - int(H * 0.03))
        _shadow(canvas, prop_r, bpos)
        canvas.alpha_composite(prop_r, bpos)

    return canvas.convert("RGB")
