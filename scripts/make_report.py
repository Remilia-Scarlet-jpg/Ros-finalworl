#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拍照索引页生成（make_report）

把 photo_node 存下的 meta.jsonl 变成一个可以直接用浏览器打开的 index.html：
按时间倒序排列，带缩略图、地点、时间、类型标签。

单独用：
    rosrun spark_deliver make_report.py
    rosrun spark_deliver make_report.py --dir ~/.spark_deliver/photos
    rosrun spark_deliver make_report.py --open     # 生成后直接打开
"""

from __future__ import print_function

import argparse
import html
import json
import os
import subprocess
import sys
import time

import cv2

TAG_TEXT = {
    'arrive': '送达',
    'round': '巡拍',
}


def _read_meta(save_dir):
    meta_path = os.path.join(save_dir, 'meta.jsonl')
    items = []
    if not os.path.isfile(meta_path):
        return items
    with open(meta_path, 'r', encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except ValueError:
                continue
    items.sort(key=lambda x: x.get('stamp', 0), reverse=True)
    return items


def _make_thumb(save_dir, rel_path, thumb_width):
    src = os.path.join(save_dir, rel_path)
    if not os.path.isfile(src):
        return None

    thumb_dir = os.path.join(save_dir, 'thumbs')
    if not os.path.isdir(thumb_dir):
        os.makedirs(thumb_dir)

    thumb_name = rel_path.replace(os.sep, '_')
    thumb_path = os.path.join(thumb_dir, thumb_name)
    if os.path.isfile(thumb_path) and \
            os.path.getmtime(thumb_path) >= os.path.getmtime(src):
        return os.path.join('thumbs', thumb_name)

    image = cv2.imread(src)
    if image is None:
        return None
    height, width = image.shape[:2]
    if width <= 0:
        return None
    scale = float(thumb_width) / width
    resized = cv2.resize(image, (thumb_width, max(1, int(height * scale))))
    cv2.imwrite(thumb_path, resized, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return os.path.join('thumbs', thumb_name)


def build_report(save_dir, title='送物拍照记录', thumb_width=320):
    """重建 index.html，返回 (index 路径, 照片张数)。"""
    save_dir = os.path.expanduser(save_dir)
    if not os.path.isdir(save_dir):
        os.makedirs(save_dir)

    items = _read_meta(save_dir)
    cards = []
    for item in items:
        rel_path = item.get('file', '')
        thumb = _make_thumb(save_dir, rel_path, thumb_width) if rel_path else None
        image_src = thumb or rel_path
        place = html.escape(str(item.get('place', '未命名')))
        when = html.escape(str(item.get('time', '')))
        tag = TAG_TEXT.get(item.get('tag'), html.escape(str(item.get('tag', ''))))

        cards.append(
            '<figure class="card">'
            '<a href="{full}" target="_blank">'
            '<img src="{src}" alt="{place}"></a>'
            '<figcaption>'
            '<div class="place">{place}</div>'
            '<div class="meta"><span class="tag">{tag}</span>'
            '<span class="time">{when}</span></div>'
            '</figcaption></figure>'.format(
                full=html.escape(rel_path, quote=True),
                src=html.escape(image_src, quote=True),
                place=place, tag=tag, when=when)
        )

    if not cards:
        cards_html = '<p class="empty">还没有照片。让机器人送一趟东西，或者跑一次巡拍。</p>'
    else:
        cards_html = '\n'.join(cards)

    index_path = os.path.join(save_dir, 'index.html')
    page = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 32px 24px 64px; background: #f7f7f5;
         color: #26262a; font-family: -apple-system, "PingFang SC",
         "Microsoft YaHei", system-ui, sans-serif; }}
  header {{ max-width: 1100px; margin: 0 auto 28px; }}
  h1 {{ font-size: 22px; font-weight: 500; margin: 0 0 6px; }}
  .summary {{ font-size: 13px; color: #6b6b73; }}
  .grid {{ max-width: 1100px; margin: 0 auto; display: grid;
           grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
           gap: 16px; }}
  .card {{ margin: 0; background: #fff; border: 1px solid #e6e6e1;
           border-radius: 10px; overflow: hidden; }}
  .card img {{ display: block; width: 100%; height: 170px;
               object-fit: cover; background: #ecece8; }}
  figcaption {{ padding: 10px 12px 12px; }}
  .place {{ font-size: 14px; font-weight: 500; margin-bottom: 6px; }}
  .meta {{ display: flex; align-items: center; gap: 8px; font-size: 12px;
           color: #6b6b73; }}
  .tag {{ background: #eef3fb; color: #2c5f9e; border-radius: 999px;
          padding: 2px 8px; font-size: 11px; }}
  .empty {{ max-width: 1100px; margin: 0 auto; color: #6b6b73; font-size: 14px; }}
</style>
</head>
<body>
<header>
  <h1>{title}</h1>
  <div class="summary">共 {count} 张 · 目录 {dir} · 生成于 {now}</div>
</header>
<main class="grid">
{cards}
</main>
</body>
</html>
""".format(title=html.escape(title), count=len(items),
           dir=html.escape(save_dir), now=_now_text(), cards=cards_html)

    with open(index_path, 'w', encoding='utf-8') as handle:
        handle.write(page)
    return index_path, len(items)


def _now_text():
    return time.strftime('%Y-%m-%d %H:%M:%S')


def main(argv):
    parser = argparse.ArgumentParser(description='生成送物拍照索引页')
    parser.add_argument('--dir', default='~/.spark_deliver/photos',
                        help='照片根目录，默认 ~/.spark_deliver/photos')
    parser.add_argument('--title', default='送物拍照记录', help='网页标题')
    parser.add_argument('--thumb-width', type=int, default=320, help='缩略图宽度')
    parser.add_argument('--open', action='store_true', help='生成后用浏览器打开')
    args = parser.parse_args(argv)

    path, count = build_report(args.dir, args.title, thumb_width=args.thumb_width)
    print('已生成：%s（%d 张）' % (path, count))
    if args.open:
        subprocess.call(['xdg-open', path])
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
