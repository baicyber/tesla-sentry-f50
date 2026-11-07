# -*- coding: utf-8 -*-
"""裁切 README「💬 交流群」里的 QQ / 微信群二维码为只含码的 400x400 正方形。

与 tools/gen_sponsor.py 的 web_*.jpg 保持同一规格（正方形、去掉标题/说明文字），
但**不写进 sentry-viewer.py**：这两张群码只在 README 使用，网页端常驻模块用不到。

用法: python tools/gen_group_qr.py   (需要 Pillow)
源图: docs/sponsor/qq_group.jpg, docs/sponsor/wechat_group.png
输出: docs/sponsor/web_qqgroup.jpg, docs/sponsor/web_wechatgroup.jpg
"""
import io
import os
from PIL import Image

SP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "sponsor")
OUT_SIZE = 400
QUIET = 0.07          # 二维码四周静区比例，与 gen_sponsor.py 一致
STEP = 2

# (源文件, 检测模式, 画布填充色)
JOBS = [
    ("qq_group.jpg",     "qq",      "web_qqgroup.jpg",     (39, 38, 43)),
    ("wechat_group.png", "wechat",  "web_wechatgroup.jpg", (255, 255, 255)),
]


def is_hit(r, g, b, mode):
    if mode == "qq":
        # QQ 是浅蓝/浅紫圆点印在深灰卡片上的反色码；白色标题字 b-r≈0 会被排除
        return b > 140 and (b - r) > 35 and (b - g) > 10 and (max(r, g, b) - min(r, g, b)) > 45
    # 微信群码是黑模块白底
    return max(r, g, b) < 110


def longest_run(counts, gap=4):
    """找密度最高的最长连续段；允许 <=gap 的短空隙，用于跨过二维码内部空行"""
    mask = [c[1] for c in counts]
    # 把夹在两个 True 之间的短 False 段补上
    i = 0
    while i < len(mask):
        if not mask[i]:
            j = i
            while j < len(mask) and not mask[j]:
                j += 1
            if i > 0 and j < len(mask) and (j - i) <= gap:
                for k in range(i, j):
                    mask[k] = True
            i = j
        else:
            i += 1
    best = (0, 0)
    cur = None
    for i, v in enumerate(mask + [False]):
        if v and cur is None:
            cur = i
        elif not v and cur is not None:
            if i - cur > best[1] - best[0]:
                best = (cur, i)
            cur = None
    return best


def square_qr(path, mode, fill):
    im = Image.open(path).convert("RGB")
    w, h = im.size
    px = im.load()
    cols = (w + STEP - 1) // STEP
    rows = (h + STEP - 1) // STEP
    row_hit = [0] * rows
    col_hit = [0] * cols
    for y in range(0, h, STEP):
        for x in range(0, w, STEP):
            if is_hit(*px[x, y], mode):
                row_hit[y // STEP] += 1
                col_hit[x // STEP] += 1
    # 阈值按采样宽度取相对值：文字行/列的密度远低于二维码主体
    ry = [(i, c > max(4, int(cols * 0.02))) for i, c in enumerate(row_hit)]
    cx = [(i, c > max(4, int(rows * 0.02))) for i, c in enumerate(col_hit)]
    y0s, y1s = longest_run(ry)
    x0s, x1s = longest_run(cx)
    if y1s - y0s < rows * 0.15 or x1s - x0s < cols * 0.15:
        raise SystemExit("cannot locate QR region in " + path)
    x0, x1 = x0s * STEP, x1s * STEP
    y0, y1 = y0s * STEP, y1s * STEP
    side = max(x1 - x0, y1 - y0)
    cxm, cym = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    half = side / 2.0 * (1 + QUIET * 2)
    L = int(round(half * 2))
    ox, oy = int(round(cxm - half)), int(round(cym - half))
    canvas = Image.new("RGB", (L, L), fill)
    canvas.paste(im, (-ox, -oy))   # 超出原图的部分保持填充色
    if mode == "wechat":
        # 裁切边缘偶尔带进一两个浅灰的到期说明字, 统一白化; 黑色码块保持不变
        p = canvas.load()
        for y in range(L):
            for x in range(L):
                r, g, b = p[x, y]
                if max(r, g, b) > 150 and max(r, g, b) - min(r, g, b) < 40:
                    p[x, y] = fill
    print("   %-20s bbox (%d,%d)-(%d,%d) side=%d -> square %d" % (
        os.path.basename(path), x0, y0, x1, y1, side, L))
    return canvas.resize((OUT_SIZE, OUT_SIZE), Image.LANCZOS)


for src, mode, dst, fill in JOBS:
    p = os.path.join(SP, src)
    if not os.path.isfile(p):
        raise SystemExit("missing source image: " + p)
    img = square_qr(p, mode, fill)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=86, optimize=True)
    raw = buf.getvalue()
    with open(os.path.join(SP, dst), "wb") as f:
        f.write(raw)
    print("%-22s %7d B  %s" % (dst, len(raw), img.size))
