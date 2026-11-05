# /// script
# requires-python = ">=3.10"
# dependencies = ["pymupdf"]
# ///
"""kicad-cli で層ごとの PDF を出力し、PyMuPDF で PNG にラスタライズする。"""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pymupdf

DEFAULT_KICAD_CLI = r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe"

VIEWS = {
    "front": "F.Cu,F.SilkS,Edge.Cuts",
    "back": "B.Cu,B.SilkS,Edge.Cuts",
    "both": "F.Cu,B.Cu,Edge.Cuts",
}

AUTO_MARGIN_MM = 3.0
PT_PER_MM = 72 / 25.4

MARK_COLOR = (1, 0, 1)
# 全体図と拡大図で見た目の大きさを揃えるため、描画範囲の長辺に対する比で決める
MARK_RADIUS_RATIO = 0.025
MARK_WIDTH_RATIO = 0.004
MARK_LABEL_RATIO = 0.04


def parse_clip(text: str) -> tuple[float, float, float, float]:
    try:
        x1, y1, x2, y2 = (float(v) for v in text.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError("--clip は X1,Y1,X2,Y2 (mm) の形式で指定する")
    return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)


def parse_mark(text: str) -> tuple[tuple[float, ...], str]:
    parts = text.split(",")
    if len(parts) not in (3, 5):
        raise argparse.ArgumentTypeError(
            "--mark は X,Y,ラベル (円) か X1,Y1,X2,Y2,ラベル (矩形) の形式で指定する"
        )
    *nums, label = parts
    try:
        coords = tuple(float(v) for v in nums)
    except ValueError:
        raise argparse.ArgumentTypeError(f"--mark の座標が数値ではない: {text}")
    # PyMuPDF の組み込みフォントは ASCII 以外を描けない
    if not all(0x20 <= ord(c) <= 0x7E for c in label):
        raise argparse.ArgumentTypeError(f"--mark のラベルは ASCII の印字可能文字のみ: {label}")
    if len(coords) == 4:
        x1, y1, x2, y2 = coords
        coords = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
    return coords, label


def parse_views(text: str) -> list[str]:
    views = [v.strip() for v in text.split(",") if v.strip()]
    unknown = [v for v in views if v not in VIEWS]
    if unknown or not views:
        raise argparse.ArgumentTypeError(
            f"不明な view: {','.join(unknown)} (使用可能: {','.join(VIEWS)})"
        )
    return views


def export_pdf(kicad_cli: str, pcb: Path, layers: str, out_pdf: Path) -> None:
    # 既定の --scale 1 かつ図枠なしで、PDF 紙面座標 (mm) が KiCad 座標と一致する
    cmd = [kicad_cli, "pcb", "export", "pdf", "--mode-single",
           "-l", layers, "-o", str(out_pdf), str(pcb)]
    # kicad-cli の出力は Shift-JIS で文字化けするため、成否は終了コードだけで判定する
    try:
        proc = subprocess.run(cmd, capture_output=True)
    except FileNotFoundError:
        sys.stderr.write(f"kicad-cli が見つかりません: {kicad_cli} (環境変数 KICAD_CLI で指定可能)\n")
        sys.exit(1)
    if proc.returncode != 0 or not out_pdf.exists():
        sys.stderr.write(f"kicad-cli が失敗しました (終了コード {proc.returncode})\n")
        sys.stderr.write(f"コマンド: {' '.join(cmd)}\n")
        sys.stderr.buffer.write(proc.stdout)
        sys.stderr.buffer.write(proc.stderr)
        sys.stderr.flush()
        sys.exit(proc.returncode or 1)


def drawings_bbox_pt(page: pymupdf.Page) -> pymupdf.Rect | None:
    # 水平・垂直の線分は幅 0 の Rect になり、Rect の和演算では空扱いで無視されるため min/max で求める
    xs: list[float] = []
    ys: list[float] = []
    for d in page.get_drawings():
        r = d["rect"]
        xs += [r.x0, r.x1]
        ys += [r.y0, r.y1]
    if not xs:
        return None
    return pymupdf.Rect(min(xs), min(ys), max(xs), max(ys))


def draw_marks(page: pymupdf.Page, clip: pymupdf.Rect, marks, view: str) -> None:
    long_side = max(clip.width, clip.height)
    radius = MARK_RADIUS_RATIO * long_side
    width = MARK_WIDTH_RATIO * long_side
    fontsize = MARK_LABEL_RATIO * long_side
    for coords, label in marks:
        pts = [v * PT_PER_MM for v in coords]
        if len(pts) == 2:
            cx, cy = pts
            shape_rect = pymupdf.Rect(cx - radius, cy - radius, cx + radius, cy + radius)
        else:
            shape_rect = pymupdf.Rect(*pts)
        if not shape_rect.intersects(clip):
            sys.stderr.write(f"警告: 印 {label} ({','.join(f'{v:g}' for v in coords)}) が"
                             f" {view} の描画範囲外\n")

        shape = page.new_shape()
        if len(pts) == 2:
            shape.draw_circle(pymupdf.Point(cx, cy), radius)
        else:
            shape.draw_rect(shape_rect)
        shape.finish(color=MARK_COLOR, width=width, fill=None)

        pad = fontsize * 0.15
        text_w = pymupdf.get_text_length(label, fontname="hebo", fontsize=fontsize)
        bg = pymupdf.Rect(shape_rect.x1, shape_rect.y0 - fontsize - 2 * pad,
                          shape_rect.x1 + text_w + 2 * pad, shape_rect.y0)
        shape.draw_rect(bg)
        shape.finish(color=None, fill=(1, 1, 1), width=0)
        # insert_text の位置はベースラインなので、ディセンダ分だけ下端から持ち上げる
        shape.insert_text(pymupdf.Point(bg.x0 + pad, bg.y1 - pad - fontsize * 0.2), label,
                          fontname="hebo", fontsize=fontsize, color=MARK_COLOR)
        shape.commit()


def render(pdf: Path, png: Path, clip_mm, dpi: int, marks, view: str) -> None:
    with pymupdf.open(pdf) as doc:
        page = doc[0]
        if clip_mm is not None:
            clip = pymupdf.Rect(*(v * PT_PER_MM for v in clip_mm))
        else:
            bbox = drawings_bbox_pt(page)
            if bbox is None:
                clip = page.rect
            else:
                m = AUTO_MARGIN_MM * PT_PER_MM
                clip = pymupdf.Rect(bbox.x0 - m, bbox.y0 - m, bbox.x1 + m, bbox.y1 + m)
        if marks:
            draw_marks(page, clip, marks, view)
        pix = page.get_pixmap(dpi=dpi, clip=clip)
        pix.save(png)


def main() -> None:
    ap = argparse.ArgumentParser(description="KiCad 基板を層ごとに PNG 化する")
    ap.add_argument("pcb", type=Path, help=".kicad_pcb のパス")
    ap.add_argument("-o", "--output", type=Path, required=True, help="出力ディレクトリ")
    ap.add_argument("--views", type=parse_views, default=parse_views("front,back,both"),
                    help="front,back,both から選択 (カンマ区切り)")
    ap.add_argument("--clip", type=parse_clip, default=None,
                    help="切り抜き範囲 X1,Y1,X2,Y2 (KiCad 座標 mm)")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--mark", type=parse_mark, action="append", default=[],
                    help="印 X,Y,ラベル (円) / X1,Y1,X2,Y2,ラベル (矩形)。繰り返し指定可 (KiCad 座標 mm)")
    args = ap.parse_args()

    if not args.pcb.is_file():
        sys.stderr.write(f"pcb ファイルが見つかりません: {args.pcb}\n")
        sys.exit(1)

    kicad_cli = os.environ.get("KICAD_CLI") or DEFAULT_KICAD_CLI
    args.output.mkdir(parents=True, exist_ok=True)
    stem = args.pcb.stem
    suffix = "_clip" if args.clip is not None else ""
    # 印なしの画像を上書きしないため
    if args.mark:
        suffix += "_mark"

    with tempfile.TemporaryDirectory() as tmp:
        for view in args.views:
            pdf = Path(tmp) / f"{stem}_{view}.pdf"
            export_pdf(kicad_cli, args.pcb, VIEWS[view], pdf)
            png = args.output / f"{stem}_{view}{suffix}.png"
            render(pdf, png, args.clip, args.dpi, args.mark, view)
            print(png.resolve())


if __name__ == "__main__":
    main()
