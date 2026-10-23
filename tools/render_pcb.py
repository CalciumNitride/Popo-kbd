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


def parse_clip(text: str) -> tuple[float, float, float, float]:
    try:
        x1, y1, x2, y2 = (float(v) for v in text.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError("--clip は X1,Y1,X2,Y2 (mm) の形式で指定する")
    return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)


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


def render(pdf: Path, png: Path, clip_mm, dpi: int) -> None:
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
    args = ap.parse_args()

    if not args.pcb.is_file():
        sys.stderr.write(f"pcb ファイルが見つかりません: {args.pcb}\n")
        sys.exit(1)

    kicad_cli = os.environ.get("KICAD_CLI") or DEFAULT_KICAD_CLI
    args.output.mkdir(parents=True, exist_ok=True)
    stem = args.pcb.stem
    suffix = "_clip" if args.clip is not None else ""

    with tempfile.TemporaryDirectory() as tmp:
        for view in args.views:
            pdf = Path(tmp) / f"{stem}_{view}.pdf"
            export_pdf(kicad_cli, args.pcb, VIEWS[view], pdf)
            png = args.output / f"{stem}_{view}{suffix}.png"
            render(pdf, png, args.clip, args.dpi)
            print(png.resolve())


if __name__ == "__main__":
    main()
