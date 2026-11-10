"""Arare_primary の親指基板を裏返し、本体基板とマウスバイトでつないだ面付け用の基板ファイルを生成する。

KiCad 付属の Python で実行する:
    & "C:\\Program Files\\KiCad\\10.0\\bin\\python.exe" tools/panelize_primary.py <入力.kicad_pcb> <出力.kicad_pcb>
"""

import argparse
import shutil
import sys
from pathlib import Path

import pcbnew

# 親指基板の範囲。本体基板の左列は y = 110.4375 まで伸びるが x <= 35.625 なので入らない
THUMB_MIN_X = 54.0
THUMB_MIN_Y = 83.0

# 元の下辺 y = 117.5625 が y = 83.9375 に来て、本体基板の下辺との隙間が 2mm になる
FLIP_CENTER_Y = 100.75

MAIN_EDGE_Y = 81.9375
THUMB_EDGE_Y = 83.9375

# (左端, 右端)。本体側は J3 の配線と右下のソケット、親指側は key16* の配線と SW16/SW17 のソケットを避けた位置
TABS = [(55.0, 60.0), (91.0, 96.0)]

HOLE_DIAMETER = 0.6
HOLE_PITCH = 0.975
HOLE_OFFSETS = [-2, -1, 0, 1, 2]
# 穴の 1/3 を基板側に食い込ませる
MAIN_HOLE_Y = MAIN_EDGE_Y - 0.1
THUMB_HOLE_Y = THUMB_EDGE_Y + 0.1

mm = pcbnew.FromMM
to_mm = pcbnew.ToMM


def in_thumb(item) -> bool:
    # J2 (USB-C) は本体が基板端から外へはみ出しているので、フットプリントは外形ではなく位置で判定する
    if isinstance(item, pcbnew.FOOTPRINT):
        pos = item.GetPosition()
        return pos.x >= mm(THUMB_MIN_X) and pos.y >= mm(THUMB_MIN_Y)
    bbox = item.GetBoundingBox()
    return bbox.GetLeft() >= mm(THUMB_MIN_X) and bbox.GetTop() >= mm(THUMB_MIN_Y)


def straddles_thumb(item) -> bool:
    bbox = item.GetBoundingBox()
    return bbox.GetRight() >= mm(THUMB_MIN_X) and bbox.GetBottom() >= mm(THUMB_MIN_Y)


def flip_thumb(board: pcbnew.BOARD) -> int:
    items = (
        list(board.GetFootprints())
        + list(board.GetTracks())
        + list(board.Zones())
        + list(board.GetDrawings())
    )
    centre = pcbnew.VECTOR2I(0, mm(FLIP_CENTER_Y))
    flipped = 0
    for item in items:
        if in_thumb(item):
            item.Flip(centre, pcbnew.FLIP_DIRECTION_TOP_BOTTOM)
            flipped += 1
        elif straddles_thumb(item):
            # 範囲の境界をまたぐものは本体側として残す。想定外の物があれば気付けるように出す
            bbox = item.GetBoundingBox()
            print(
                f"範囲の境界をまたぐため移さない: {item.GetClass()} "
                f"({to_mm(bbox.GetLeft()):.3f}, {to_mm(bbox.GetTop()):.3f})-"
                f"({to_mm(bbox.GetRight()):.3f}, {to_mm(bbox.GetBottom()):.3f})",
                file=sys.stderr,
            )
    return flipped


def find_edge_line(board: pcbnew.BOARD, y: float, x1: float, x2: float) -> pcbnew.PCB_SHAPE:
    for d in board.GetDrawings():
        if d.GetLayer() != pcbnew.Edge_Cuts or not isinstance(d, pcbnew.PCB_SHAPE):
            continue
        if d.GetShape() != pcbnew.SHAPE_T_SEGMENT:
            continue
        s, e = d.GetStart(), d.GetEnd()
        if s.y != mm(y) or e.y != mm(y):
            continue
        if min(s.x, e.x) <= mm(x1) and max(s.x, e.x) >= mm(x2):
            return d
    raise RuntimeError(f"y = {y} に x = {x1}〜{x2} を含む Edge.Cuts の線がない")


def add_edge_line(board: pcbnew.BOARD, start: pcbnew.VECTOR2I, end: pcbnew.VECTOR2I, width: int) -> None:
    line = pcbnew.PCB_SHAPE(board)
    line.SetShape(pcbnew.SHAPE_T_SEGMENT)
    line.SetLayer(pcbnew.Edge_Cuts)
    line.SetWidth(width)
    line.SetStart(start)
    line.SetEnd(end)
    board.Add(line)


def open_edge(board: pcbnew.BOARD, y: float, x1: float, x2: float) -> int:
    line = find_edge_line(board, y, x1, x2)
    # GetStart/GetEnd は内部の座標への参照を返すため、書き換える前に複製しておく
    s = pcbnew.VECTOR2I(line.GetStart().x, line.GetStart().y)
    e = pcbnew.VECTOR2I(line.GetEnd().x, line.GetEnd().y)
    left, right = (s, e) if s.x <= e.x else (e, s)
    width = line.GetWidth()
    line.SetStart(left)
    line.SetEnd(pcbnew.VECTOR2I(mm(x1), mm(y)))
    add_edge_line(board, pcbnew.VECTOR2I(mm(x2), mm(y)), right, width)
    return width


def add_tabs(board: pcbnew.BOARD) -> None:
    for x1, x2 in TABS:
        width = open_edge(board, MAIN_EDGE_Y, x1, x2)
        open_edge(board, THUMB_EDGE_Y, x1, x2)
        for x in (x1, x2):
            add_edge_line(
                board,
                pcbnew.VECTOR2I(mm(x), mm(MAIN_EDGE_Y)),
                pcbnew.VECTOR2I(mm(x), mm(THUMB_EDGE_Y)),
                width,
            )


def add_mouse_bite(board: pcbnew.BOARD, ref: str, cx: float, y: float) -> None:
    fp = pcbnew.FOOTPRINT(board)
    fp.SetReference(ref)
    fp.SetValue("MouseBite")
    fp.Reference().SetVisible(False)
    fp.Value().SetVisible(False)
    board.Add(fp)
    fp.SetPosition(pcbnew.VECTOR2I(mm(cx), mm(y)))
    for i in HOLE_OFFSETS:
        pad = pcbnew.PAD(fp)
        pad.SetAttribute(pcbnew.PAD_ATTRIB_NPTH)
        pad.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
        pad.SetSize(pcbnew.VECTOR2I(mm(HOLE_DIAMETER), mm(HOLE_DIAMETER)))
        pad.SetDrillSize(pcbnew.VECTOR2I(mm(HOLE_DIAMETER), mm(HOLE_DIAMETER)))
        pad.SetLayerSet(pcbnew.PAD.UnplatedHoleMask())
        fp.Add(pad)
        pad.SetPosition(pcbnew.VECTOR2I(mm(cx + i * HOLE_PITCH), mm(y)))


def add_mouse_bites(board: pcbnew.BOARD) -> None:
    n = 1
    for x1, x2 in TABS:
        cx = (x1 + x2) / 2
        for y in (MAIN_HOLE_Y, THUMB_HOLE_Y):
            add_mouse_bite(board, f"MB{n}", cx, y)
            n += 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input", type=Path, help="設計の .kicad_pcb")
    parser.add_argument("output", type=Path, help="出力する面付けの .kicad_pcb")
    args = parser.parse_args()

    src = args.input.resolve()
    dst = args.output.resolve()
    if src == dst:
        print("入力と出力が同じファイルになっている", file=sys.stderr)
        return 1

    board = pcbnew.LoadBoard(str(src))
    flipped = flip_thumb(board)
    add_tabs(board)
    add_mouse_bites(board)

    pcbnew.ZONE_FILLER(board).Fill(board.Zones())

    dst.parent.mkdir(parents=True, exist_ok=True)
    # プロジェクト設定は設計の .kicad_pro をそのまま複製するので、SaveBoard には書かせない
    if not pcbnew.SaveBoard(str(dst), board, True):
        print(f"保存に失敗した: {dst}", file=sys.stderr)
        return 1
    shutil.copyfile(src.with_suffix(".kicad_pro"), dst.with_suffix(".kicad_pro"))
    for table in ("fp-lib-table", "sym-lib-table"):
        shutil.copyfile(src.parent / table, dst.parent / table)

    print(f"親指基板から移した要素: {flipped}")
    print(f"出力: {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
