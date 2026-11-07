# -*- coding: utf-8 -*-
"""重新生成 sentry-viewer.py 内的赞助 / 联系作者二维码资源。

开发期工具，需要 Pillow:  python -m pip install Pillow
用法:  python tools/gen_sponsor.py

图片源统一放在 docs/sponsor/:
  alipay.jpg / wechat.jpg          收款码 (原始带横幅/文字)
  douyin.jpg / wechat_author.jpg  作者抖音、微信好友二维码
本脚本自动检测二维码黑色模块的外接框, 裁掉横幅与说明文字, 输出只含二维码的
正方形 400x400 JPEG (web_<key>.jpg), 再把 base64 写进主程序的 SPONSOR_IMGS,
由 /sponsor/<key> 按需返回 —— 不塞进首屏 HTML。
替换旧二维码后重跑本脚本即可，幂等，不会重复插入。
"""
import base64
import io
import os
import re
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SV = os.path.join(ROOT, "sentry-viewer.py")
SP = os.path.join(ROOT, "docs", "sponsor")

SRC = {
    "alipay": ("alipay.jpg", "dark"),
    "wechatpay": ("wechat.jpg", "dark"),
    "douyin": ("douyin.jpg", "lum"),   # 抖音码模块是棕红色, 按亮度检测
    "wechatauthor": ("wechat_author.jpg", "dark"),
}
OUT_SIZE = 400
DARK = 110          # 三通道最大值低于此值视为二维码黑色模块
LUM = 115           # 彩色二维码(抖音)的亮度阈值
QUIET = 0.07        # 二维码四周留白比例


def square_qr(path, mode="dark"):
    """检测二维码模块外接框 -> 裁成正方形(白底), 只保留二维码与静区"""
    im = Image.open(path).convert("RGB")
    w, h = im.size
    px = im.load()
    xs, ys = [], []
    step = 2
    for y in range(0, h, step):
        for x in range(0, w, step):
            r, g, b = px[x, y]
            hit = ((r * 299 + g * 587 + b * 114) // 1000 < LUM) if mode == "lum" else (max(r, g, b) < DARK)
            if hit:
                xs.append(x)
                ys.append(y)
    if len(xs) < 100:
        raise SystemExit("cannot locate QR modules in " + path)
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    side = max(x1 - x0, y1 - y0)
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    half = side / 2.0 * (1 + QUIET * 2)
    L = int(round(half * 2))
    ox, oy = int(round(cx - half)), int(round(cy - half))
    canvas = Image.new("RGB", (L, L), "white")
    canvas.paste(im, (-ox, -oy))       # 超出边界自动裁掉, 留白处补白底
    # 白化残留的彩色背景 (蓝/绿/粉横幅), 让四个码视觉统一为白底
    op = canvas.load()
    for y in range(L):
        for x in range(L):
            r, g, b = op[x, y]
            if max(r, g, b) < DARK:
                continue
            # 明显带色且不是白/灰的像素 -> 白; 二维码中间的彩色头像保留
            if max(r, g, b) - min(r, g, b) > 45 and max(r, g, b) > 120:
                edge = min(x, y, L - 1 - x, L - 1 - y)
                if edge < L * 0.16:
                    op[x, y] = (255, 255, 255)
    print("   bbox %-14s (%d,%d)-(%d,%d) side=%d -> square %d" % (
        os.path.basename(path), x0, y0, x1, y1, side, L))
    return canvas.resize((OUT_SIZE, OUT_SIZE), Image.LANCZOS)


if not os.path.isfile(SV):
    raise SystemExit("sentry-viewer.py not found: " + SV)

imgs = {}
for key, (fn, mode) in SRC.items():
    path = os.path.join(SP, fn)
    if not os.path.isfile(path):
        raise SystemExit("missing source image: " + path)
    im = square_qr(path, mode)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=82, optimize=True)
    raw = buf.getvalue()
    with open(os.path.join(SP, "web_%s.jpg" % key), "wb") as f:
        f.write(raw)
    imgs[key] = base64.b64encode(raw).decode()
    print("%-14s %6d B  %s" % (key, len(raw), im.size))

block = ("# 赞助 / 联系作者二维码 (压缩后 base64 内嵌, 单文件自包含, OTA 随主程序一起更新)\n"
         "# 图片走 /sponsor/<key> 按需加载, 不塞进首页 HTML, 减少窄带下每次刷新的传输量\n"
         "SPONSOR_IMGS = {\n" +
         "".join('    "%s": "data:image/jpeg;base64,%s",\n' % (k, v) for k, v in imgs.items()) +
         "}\n"
         "SPONSOR_BYTES = {k: base64.b64decode(v.split(\",\", 1)[1]) for k, v in SPONSOR_IMGS.items()}\n")

src = open(SV, encoding="utf-8").read()
# 连同块后的空行一起吃掉, 保证重复运行不会不断累积空行 (真正的幂等)
src = re.sub(r"# 赞助 / 联系作者二维码[^\n]*\n"
             r"(?:# [^\n]*\n)?"
             r"SPONSOR_IMGS = \{.*?\n\}\n"
             r"(?:SPONSOR_BYTES = \{[^\n]*\n)?"
             r"\n*",
             "", src, count=1, flags=re.S)
src = re.sub(r"\nfor _k, _v in SPONSOR_IMGS\.items\(\):\n"
             r"    HTML = HTML\.replace\(\"__IMG_%s__\" % _k\.upper\(\), _v\)\n", "", src)

needle = 'HTML = r"""<!DOCTYPE html>'
if needle not in src:
    raise SystemExit("HTML start not found")
src = src.replace(needle, block + "\n\n" + needle, 1)
open(SV, "w", encoding="utf-8", newline="\n").write(src)
print("written", SV, "total base64", sum(len(v) for v in imgs.values()), "chars")
