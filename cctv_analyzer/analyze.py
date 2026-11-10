"""
防犯カメラ映像 解析ツール
========================

防犯カメラの録画ファイル（またはフォルダ）を読み込み、
  - 人・車・自転車・動物などを検出して追跡（YOLO + ByteTrack）
  - 「いつ・何が・何秒映っていたか」をイベント一覧（CSV）にまとめる
  - 各イベントの代表スナップショット画像を保存
  - 動きのあった場面だけをつないだダイジェスト動画を作る
  - 監視エリア（ROI）への侵入・長時間滞在（うろつき）を検知
  - 通過ライン（入口など）の通過人数・台数を方向別にカウント
  - 結果を report.html（ブラウザで見られるレポート）にまとめる

使い方の例:
    python analyze.py 録画.mp4
    python analyze.py 録画フォルダ/ --out 結果
    python analyze.py cam1.mp4 --start "2026-11-10 08:00:00"
    python analyze.py cam1.mp4 --roi "0.1,0.5;0.6,0.5;0.6,1;0.1,1" --loiter 30
    python analyze.py cam1.mp4 --line "0.5,0;0.5,1"

座標はすべて画面に対する割合（左上 0,0 ／ 右下 1,1）で指定します。
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".m4v", ".wmv", ".flv", ".ts", ".mts", ".m2ts", ".3gp", ".webm", ".dav", ".h264", ".264"}

# COCO クラス名 → 日本語表示名
JA_LABELS = {
    "person": "人", "bicycle": "自転車", "car": "車", "motorcycle": "バイク",
    "bus": "バス", "truck": "トラック", "cat": "猫", "dog": "犬", "bird": "鳥",
    "backpack": "リュック", "handbag": "ハンドバッグ", "suitcase": "スーツケース",
    "umbrella": "傘",
}
DEFAULT_CLASSES = "person,bicycle,car,motorcycle,bus,truck,cat,dog"

# 描画色（BGR）
COLORS = {
    "person": (60, 60, 230), "bicycle": (40, 170, 250), "car": (230, 160, 40),
    "motorcycle": (200, 80, 200), "bus": (60, 200, 200), "truck": (180, 120, 60),
    "cat": (90, 200, 90), "dog": (90, 200, 90),
}


# ---------------------------------------------------------------------------
# 引数・設定
# ---------------------------------------------------------------------------

def parse_points(text: str | None) -> list[tuple[float, float]] | None:
    """'x,y;x,y;...' 形式（0〜1 の割合）を点のリストにする。"""
    if not text:
        return None
    pts = []
    for part in text.replace(" ", "").split(";"):
        if not part:
            continue
        x, y = part.split(",")
        x, y = float(x), float(y)
        if not (0 <= x <= 1 and 0 <= y <= 1):
            raise ValueError(f"座標は 0〜1 の割合で指定してください: {part}")
        pts.append((x, y))
    return pts


TS_PATTERN = re.compile(
    r"(20\d{2})[-_.]?(\d{2})[-_.]?(\d{2})[ _T.-]?(\d{2})[-_:.]?(\d{2})[-_:.]?(\d{2})"
)


def guess_start_time(path: Path) -> datetime | None:
    """ファイル名から録画開始時刻を推測する（例: 20261110_083000, 2026-11-10_08-30-00）。"""
    m = TS_PATTERN.search(path.stem)
    if not m:
        return None
    try:
        return datetime(*map(int, m.groups()))
    except ValueError:
        return None


def fmt_sec(sec: float) -> str:
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}"


# ---------------------------------------------------------------------------
# 追跡データ
# ---------------------------------------------------------------------------

@dataclass
class Track:
    tid: int
    cls: str
    first_t: float
    last_t: float
    first_frame: int
    best_score: float = 0.0
    best_conf: float = 0.0
    snapshot: bytes | None = None          # JPEG バイト列
    snapshot_t: float = 0.0
    hits: int = 0
    path: list = field(default_factory=list)  # (t, cx, cy) 正規化座標（間引き）
    roi_first_t: float | None = None
    roi_time: float = 0.0
    last_in_roi_t: float | None = None
    loiter_flag: bool = False
    crossings: list = field(default_factory=list)  # (t, "A→B" / "B→A")
    side: int = 0                 # 線のどちら側にいるか（確定値）
    anchor: tuple | None = None   # 側が確定した最後の位置
    cls_votes: Counter = field(default_factory=Counter)

    @property
    def duration(self) -> float:
        return self.last_t - self.first_t


def side_of_line(p, a, b) -> int:
    v = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
    return 1 if v > 0 else (-1 if v < 0 else 0)


def line_dist(p, a, b) -> float:
    """点 p と直線 a-b の符号付き距離（正規化座標）。"""
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = (dx * dx + dy * dy) ** 0.5 or 1.0
    return (dx * (p[1] - a[1]) - dy * (p[0] - a[0])) / n


def crossing_label(p_from, p_to, a, b) -> str:
    """通過方向を「上→下」「左→右」などで表す（線が横向きなら上下、縦向きなら左右）。"""
    if abs(b[0] - a[0]) >= abs(b[1] - a[1]):
        return "上→下" if p_to[1] > p_from[1] else "下→上"
    return "左→右" if p_to[0] > p_from[0] else "右→左"


def segments_intersect(p1, p2, a, b) -> bool:
    """線分 p1-p2 と a-b が交わるか（通過判定用）。"""
    d1 = side_of_line(p1, a, b)
    d2 = side_of_line(p2, a, b)
    d3 = side_of_line(a, p1, p2)
    d4 = side_of_line(b, p1, p2)
    return d1 != d2 and d3 != d4 and d1 != 0 and d2 != 0


# ---------------------------------------------------------------------------
# 1 本の動画を解析
# ---------------------------------------------------------------------------

class VideoAnalyzer:
    def __init__(self, args, model, class_ids: list[int]):
        self.args = args
        self.model = model
        self.class_ids = class_ids
        self.names = model.names

    def analyze(self, video_path: Path, out_dir: Path) -> dict:
        args = self.args
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise RuntimeError(f"動画を開けませんでした: {video_path}")

        fps = cap.get(cv2.CAP_PROP_FPS)
        if not fps or fps <= 0 or fps > 240 or np.isnan(fps):
            fps = 30.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        stride = max(1, int(round(fps / args.fps))) if args.fps > 0 else 1
        eff_fps = fps / stride

        if args.start:
            start_dt = datetime.strptime(args.start, "%Y-%m-%d %H:%M:%S")
        else:
            start_dt = guess_start_time(video_path)

        roi = parse_points(args.roi)
        roi_px = np.array([[x * W, y * H] for x, y in roi], np.int32) if roi else None
        line = parse_points(args.line)
        if line and len(line) != 2:
            raise ValueError("--line は 2 点（始点;終点）で指定してください")

        out_dir.mkdir(parents=True, exist_ok=True)
        snap_dir = out_dir / "snapshots"
        snap_dir.mkdir(exist_ok=True)

        # 背景差分（動き検知）— 縮小画像で軽量に
        small_w = 320
        small_h = max(1, int(H * small_w / max(W, 1)))
        mog = cv2.createBackgroundSubtractorMOG2(history=int(eff_fps * 20) or 100, varThreshold=25, detectShadows=False)
        kernel = np.ones((3, 3), np.uint8)

        # ダイジェスト動画
        digest_tmp = out_dir / "_digest_raw.mp4"
        writer = None
        pre_buf: deque = deque(maxlen=max(1, int(eff_fps * args.pad)))
        post_left = 0
        digest_frames = 0

        tracks: dict[int, Track] = {}
        motion_per_bucket = defaultdict(float)   # 分ごとの動き量
        last_yolo_t = -1e9
        last_active_t = -1e9
        frames_analyzed = 0
        yolo_runs = 0
        t0 = time.time()

        if args.track:
            # 新規動画ごとにトラッカーを初期化
            if hasattr(self.model, "predictor") and self.model.predictor is not None:
                self.model.predictor.trackers = None
                self.model.predictor = None

        idx = -1
        while True:
            ok = cap.grab()
            if not ok:
                break
            idx += 1
            if idx % stride:
                continue
            ok, frame = cap.retrieve()
            if not ok or frame is None:
                continue
            frames_analyzed += 1
            t = idx / fps

            # --- 動き検知 ---
            small = cv2.resize(frame, (small_w, small_h), interpolation=cv2.INTER_AREA)
            gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
            fg = mog.apply(gray)
            _, fg = cv2.threshold(fg, 200, 255, cv2.THRESH_BINARY)
            fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, kernel)
            motion = float(np.count_nonzero(fg)) / fg.size
            warmup = frames_analyzed <= 3
            moving = (motion >= args.motion) and not warmup
            motion_per_bucket[int(t // 60)] += motion

            # --- 物体検出（動きがある／直前まで何か居た／定期チェック のときだけ）---
            run_yolo = (
                args.no_motion_gate or moving
                or (t - last_active_t) <= args.hold
                or (t - last_yolo_t) >= args.idle_check
            )
            dets = []
            if run_yolo:
                last_yolo_t = t
                yolo_runs += 1
                kw = dict(conf=args.conf, imgsz=args.imgsz, classes=self.class_ids, verbose=False, device=args.device)
                if args.track:
                    res = self.model.track(frame, persist=True, tracker=args.tracker, **kw)[0]
                else:
                    res = self.model.predict(frame, **kw)[0]
                boxes = res.boxes
                if boxes is not None and len(boxes):
                    xyxy = boxes.xyxy.cpu().numpy()
                    confs = boxes.conf.cpu().numpy()
                    clss = boxes.cls.cpu().numpy().astype(int)
                    ids = boxes.id.cpu().numpy().astype(int) if boxes.id is not None else None
                    for i in range(len(xyxy)):
                        tid = int(ids[i]) if ids is not None else None
                        dets.append((xyxy[i], float(confs[i]), self.names[int(clss[i])], tid))

            if dets:
                last_active_t = t

            # --- 追跡情報の更新 ---
            for box, conf, cname, tid in dets:
                if tid is None:
                    continue
                x1, y1, x2, y2 = box
                foot = ((x1 + x2) / 2 / W, y2 / H)   # 足元（下端中央）
                tr = tracks.get(tid)
                if tr is None:
                    tr = Track(tid=tid, cls=cname, first_t=t, last_t=t, first_frame=idx)
                    tracks[tid] = tr
                tr.cls_votes[cname] += 1
                tr.last_t = t
                tr.hits += 1
                if not tr.path or t - tr.path[-1][0] >= 0.5:
                    tr.path.append((t, foot[0], foot[1]))

                area = (x2 - x1) * (y2 - y1) / (W * H)
                score = conf * min(area, 0.25) ** 0.5
                if score > tr.best_score:
                    tr.best_score = score
                    tr.best_conf = conf
                    tr.snapshot_t = t
                    tr.snapshot = crop_jpeg(frame, box, cname, conf)

                if roi_px is not None:
                    inside = cv2.pointPolygonTest(roi_px, (foot[0] * W, foot[1] * H), False) >= 0
                    if inside:
                        if tr.roi_first_t is None:
                            tr.roi_first_t = t
                        if tr.last_in_roi_t is not None and t - tr.last_in_roi_t <= args.hold + 1:
                            tr.roi_time += t - tr.last_in_roi_t
                        tr.last_in_roi_t = t
                        if tr.roi_time >= args.loiter:
                            tr.loiter_flag = True

                if line:
                    # ふらつきで何度も数えないよう、線から一定距離（margin）離れて初めて「側」を確定
                    a, b = line
                    d = line_dist(foot, a, b)
                    if abs(d) >= args.line_margin:
                        s_now = 1 if d > 0 else -1
                        if tr.side and s_now != tr.side and tr.anchor is not None \
                                and segments_intersect(tr.anchor, foot, a, b):
                            tr.crossings.append((t, crossing_label(tr.anchor, foot, a, b)))
                        tr.side = s_now
                        tr.anchor = foot

            # --- ダイジェスト動画 ---
            active = bool(dets) or moving
            if args.digest:
                annotated = draw_frame(frame, dets, t, start_dt, roi_px, line, W, H)
                if writer is None:
                    writer = cv2.VideoWriter(str(digest_tmp), cv2.VideoWriter_fourcc(*"mp4v"), eff_fps, (W, H))
                if bool(dets):
                    while pre_buf:
                        writer.write(pre_buf.popleft())
                        digest_frames += 1
                    writer.write(annotated)
                    digest_frames += 1
                    post_left = int(eff_fps * args.pad)
                elif post_left > 0:
                    writer.write(annotated)
                    digest_frames += 1
                    post_left -= 1
                else:
                    pre_buf.append(annotated)

            if frames_analyzed % 200 == 0:
                show_progress(video_path.name, idx, total, t0)

        cap.release()
        if writer is not None:
            writer.release()
        show_progress(video_path.name, total or idx, total or idx, t0, done=True)
        elapsed = time.time() - t0
        duration = (idx + 1) / fps if idx >= 0 else 0.0

        # --- イベント確定（短すぎる・誤検出らしいものを除外）---
        events = []
        for tr in sorted(tracks.values(), key=lambda x: x.first_t):
            if tr.cls_votes:
                tr.cls = tr.cls_votes.most_common(1)[0][0]
            if tr.duration < args.min_duration or tr.hits < args.min_hits:
                continue
            events.append(tr)

        # 同一物体の ID の付け直しなどで連番を振り直す
        rows = []
        for n, tr in enumerate(events, 1):
            snap_name = None
            if tr.snapshot:
                snap_name = f"snapshots/{n:04d}_{tr.cls}.jpg"
                (out_dir / snap_name).write_bytes(tr.snapshot)
            row = {
                "no": n,
                "video": video_path.name,
                "class": tr.cls,
                "label": JA_LABELS.get(tr.cls, tr.cls),
                "start_sec": round(tr.first_t, 1),
                "end_sec": round(tr.last_t, 1),
                "start_video": fmt_sec(tr.first_t),
                "end_video": fmt_sec(tr.last_t),
                "start_clock": (start_dt + timedelta(seconds=tr.first_t)).strftime("%Y-%m-%d %H:%M:%S") if start_dt else "",
                "end_clock": (start_dt + timedelta(seconds=tr.last_t)).strftime("%Y-%m-%d %H:%M:%S") if start_dt else "",
                "duration_sec": round(tr.duration, 1),
                "confidence": round(tr.best_conf, 2),
                "in_roi": "" if roi is None else ("はい" if tr.roi_first_t is not None else "いいえ"),
                "roi_sec": "" if roi is None else round(tr.roi_time, 1),
                "loitering": "" if roi is None else ("はい" if tr.loiter_flag else ""),
                "line_cross": " / ".join(f"{d}@{fmt_sec(tt)}" for tt, d in tr.crossings),
                "snapshot": snap_name or "",
            }
            rows.append(row)

        with open(out_dir / "events.csv", "w", newline="", encoding="utf-8-sig") as f:
            if rows:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)
            else:
                f.write("該当するイベントはありませんでした\n")

        # --- ダイジェスト動画をブラウザ再生できる形式に変換 ---
        digest_name = None
        if args.digest and digest_tmp.exists():
            if digest_frames == 0:
                digest_tmp.unlink()
            else:
                digest_name = "digest.mp4"
                final = out_dir / digest_name
                if shutil.which("ffmpeg"):
                    r = subprocess.run(
                        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(digest_tmp),
                         "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
                         "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(final)],
                    )
                    if r.returncode == 0:
                        digest_tmp.unlink()
                    else:
                        digest_tmp.replace(final)
                else:
                    digest_tmp.replace(final)

        # --- 集計 ---
        counts = Counter(r["class"] for r in rows)
        crossings = Counter()
        for tr in events:
            for _, d in tr.crossings:
                crossings[(tr.cls, d)] += 1

        summary = {
            "video": video_path.name,
            "path": str(video_path),
            "resolution": f"{W}x{H}",
            "fps": round(fps, 2),
            "analysis_fps": round(eff_fps, 2),
            "duration_sec": round(duration, 1),
            "duration": fmt_sec(round(duration)),
            "start_clock": start_dt.strftime("%Y-%m-%d %H:%M:%S") if start_dt else None,
            "frames_analyzed": frames_analyzed,
            "detector_runs": yolo_runs,
            "processing_sec": round(elapsed, 1),
            "event_count": len(rows),
            "counts": dict(counts),
            "loitering": sum(1 for tr in events if tr.loiter_flag),
            "roi_entries": sum(1 for tr in events if tr.roi_first_t is not None) if roi else None,
            "line_crossings": {f"{c}:{d}": n for (c, d), n in crossings.items()},
            "motion_per_minute": {int(k): round(v / max(1, eff_fps * 60), 5) for k, v in sorted(motion_per_bucket.items())},
            "digest": digest_name,
            "roi": roi,
            "line": line,
        }
        (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

        # ROI・ラインの確認用に 1 枚目のフレームを保存
        save_layout_preview(video_path, out_dir / "layout.jpg", roi_px, line)

        write_report(out_dir / "report.html", summary, rows, start_dt, duration, args)
        return {"summary": summary, "rows": rows, "out_dir": out_dir}


# ---------------------------------------------------------------------------
# 描画・保存ヘルパー
# ---------------------------------------------------------------------------

def crop_jpeg(frame, box, cname, conf) -> bytes:
    H, W = frame.shape[:2]
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    pad = 0.25
    cx1 = int(max(0, x1 - bw * pad)); cy1 = int(max(0, y1 - bh * pad))
    cx2 = int(min(W, x2 + bw * pad)); cy2 = int(min(H, y2 + bh * pad))
    crop = frame[cy1:cy2, cx1:cx2].copy()
    if crop.size == 0:
        crop = frame.copy()
        cx1 = cy1 = 0
    cv2.rectangle(crop, (int(x1 - cx1), int(y1 - cy1)), (int(x2 - cx1), int(y2 - cy1)), COLORS.get(cname, (0, 255, 255)), 2)
    # 小さすぎる切り抜きは拡大、大きすぎるものは縮小
    h, w = crop.shape[:2]
    target = 240
    scale = target / max(h, 1)
    scale = min(scale, 4.0)
    if scale != 1:
        crop = cv2.resize(crop, (max(1, int(w * scale)), max(1, int(h * scale))),
                          interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return buf.tobytes() if ok else b""


def draw_frame(frame, dets, t, start_dt, roi_px, line, W, H, stamp=True):
    img = frame.copy()
    if roi_px is not None:
        overlay = img.copy()
        cv2.fillPoly(overlay, [roi_px], (0, 200, 255))
        img = cv2.addWeighted(overlay, 0.15, img, 0.85, 0)
        cv2.polylines(img, [roi_px], True, (0, 200, 255), 2)
    if line:
        a = (int(line[0][0] * W), int(line[0][1] * H))
        b = (int(line[1][0] * W), int(line[1][1] * H))
        cv2.line(img, a, b, (255, 0, 255), 2)
    th = max(1, int(round(min(W, H) / 400)))
    for box, conf, cname, tid in dets:
        x1, y1, x2, y2 = map(int, box)
        c = COLORS.get(cname, (0, 255, 255))
        cv2.rectangle(img, (x1, y1), (x2, y2), c, th + 1)
        label = f"{cname}" + (f" #{tid}" if tid is not None else "") + f" {conf:.2f}"
        (tw, tht), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5 * th, th)
        cv2.rectangle(img, (x1, max(0, y1 - tht - 8)), (x1 + tw + 6, y1), c, -1)
        cv2.putText(img, label, (x1 + 3, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5 * th, (255, 255, 255), th)
    if stamp:
        text = (start_dt + timedelta(seconds=t)).strftime("%Y-%m-%d %H:%M:%S") if start_dt else f"T+{fmt_sec(t)}"
        fs = 0.6 * th
        (tw, tht), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, fs, th)
        cv2.rectangle(img, (0, 0), (tw + 16, tht + base + 14), (0, 0, 0), -1)
        cv2.putText(img, text, (8, tht + 7), cv2.FONT_HERSHEY_SIMPLEX, fs, (255, 255, 255), th)
    return img


def save_layout_preview(video_path, dest, roi_px, line):
    cap = cv2.VideoCapture(str(video_path))
    ok, frame = cap.read()
    cap.release()
    if not ok:
        return
    H, W = frame.shape[:2]
    img = draw_frame(frame, [], 0, None, roi_px, line, W, H, stamp=False)
    # 位置合わせ用の 10% グリッド
    for i in range(1, 10):
        x, y = int(W * i / 10), int(H * i / 10)
        cv2.line(img, (x, 0), (x, H), (255, 255, 255), 1)
        cv2.line(img, (0, y), (W, y), (255, 255, 255), 1)
        fs = max(0.5, min(W, H) / 900)
        for (px, py) in ((x + 3, int(22 * fs + 4)), (4, y - 4)):
            cv2.putText(img, f"{i/10:.1f}", (px, py), cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 0, 0), 4)
            cv2.putText(img, f"{i/10:.1f}", (px, py), cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 255, 255), 1)
    cv2.imwrite(str(dest), img)


def show_progress(name, idx, total, t0, done=False):
    el = time.time() - t0
    if total > 0:
        p = min(1.0, idx / total)
        eta = el / p - el if p > 0 else 0
        msg = f"\r  {name}: {p*100:5.1f}%  経過 {fmt_sec(el)}  残り約 {fmt_sec(eta)}   "
    else:
        msg = f"\r  {name}: {idx} フレーム  経過 {fmt_sec(el)}   "
    if done:
        msg = f"\r  {name}: 完了（{fmt_sec(el)}）" + " " * 30 + "\n"
    sys.stdout.write(msg)
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# HTML レポート
# ---------------------------------------------------------------------------

REPORT_CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#1d2330;--mut:#667085;--line:#e3e6ec;--acc:#2f6fed;--warn:#d9480f}
@media (prefers-color-scheme:dark){:root{--bg:#14161b;--card:#1d2027;--fg:#e8eaf0;--mut:#9aa3b2;--line:#2c313b;--acc:#6e9bff;--warn:#ff8a5b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 system-ui,-apple-system,"Hiragino Sans","Yu Gothic UI",Meiryo,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 60px}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 10px}
.mut{color:var(--mut)}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-top:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}.card b{display:block;font-size:24px}
.warn b{color:var(--warn)}table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:middle;font-size:14px}th{color:var(--mut);font-weight:600;white-space:nowrap}
.tbl{overflow-x:auto}td img{height:86px;border-radius:6px;display:block}.tag{display:inline-block;padding:1px 8px;border-radius:99px;background:var(--line);font-size:12px}
.tag.w{background:var(--warn);color:#fff}.bars{display:flex;align-items:flex-end;gap:2px;height:120px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px}
.bars div{flex:1;background:var(--acc);border-radius:2px 2px 0 0;min-height:1px}.axis{display:flex;justify-content:space-between;font-size:12px;color:var(--mut);padding:2px 10px}
.tl{position:relative;height:auto;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px 10px}
.tlrow{display:flex;align-items:center;gap:8px;height:22px}.tlrow span{width:70px;font-size:12px;color:var(--mut);flex:none}
.tltrack{position:relative;flex:1;height:12px;background:var(--line);border-radius:3px}.tltrack i{position:absolute;top:0;height:12px;background:var(--acc);border-radius:2px;min-width:2px;opacity:.85}
video{width:100%;max-height:520px;background:#000;border-radius:10px}.grid2{display:grid;grid-template-columns:1fr;gap:12px}
.filter{margin:6px 0 10px;display:flex;flex-wrap:wrap;gap:6px}.filter button{border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:99px;padding:3px 12px;cursor:pointer;font:inherit;font-size:13px}
.filter button.on{background:var(--acc);border-color:var(--acc);color:#fff}
"""


def write_report(dest: Path, summary: dict, rows: list[dict], start_dt, duration: float, args):
    e = html.escape
    counts = summary["counts"]
    cards = [f'<div class="card"><span class="mut">イベント数</span><b>{summary["event_count"]}</b></div>']
    for cls, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        cards.append(f'<div class="card"><span class="mut">{e(JA_LABELS.get(cls, cls))}</span><b>{n}</b></div>')
    if summary.get("roi_entries") is not None:
        cards.append(f'<div class="card"><span class="mut">エリア侵入</span><b>{summary["roi_entries"]}</b></div>')
        cards.append(f'<div class="card warn"><span class="mut">うろつき（{args.loiter:g}秒以上）</span><b>{summary["loitering"]}</b></div>')
    for k, n in summary.get("line_crossings", {}).items():
        cls, d = k.split(":")
        cards.append(f'<div class="card"><span class="mut">ライン通過 {e(JA_LABELS.get(cls, cls))}（{e(d)}）</span><b>{n}</b></div>')

    # 時間帯ヒストグラム（時刻が分かれば 1 時間ごと、なければ動画内の時間で 20 分割）
    hist_html = ""
    if rows:
        if start_dt and duration >= 3600:
            buckets = Counter()
            first_h = start_dt.replace(minute=0, second=0)
            nb = int(((start_dt + timedelta(seconds=duration)) - first_h).total_seconds() // 3600) + 1
            for r in rows:
                dt = start_dt + timedelta(seconds=r["start_sec"])
                buckets[int((dt - first_h).total_seconds() // 3600)] += 1
            labels = [(first_h + timedelta(hours=i)).strftime("%H時") for i in range(nb)]
            vals = [buckets[i] for i in range(nb)]
            unit = "1時間ごと"
        else:
            nb = 20
            step = max(duration, 1) / nb
            vals = [0] * nb
            for r in rows:
                vals[min(nb - 1, int(r["start_sec"] // step))] += 1
            if start_dt:
                labels = [(start_dt + timedelta(seconds=i * step)).strftime("%H:%M:%S") for i in range(nb)]
            else:
                labels = [fmt_sec(i * step) for i in range(nb)]
            unit = f"{step:.1f}秒ごと" if step < 60 else (f"{step / 60:.0f}分ごと" if step < 3600 else f"{step / 3600:.1f}時間ごと")
        mx = max(vals) or 1
        bars = "".join(f'<div title="{e(labels[i])}: {v}件" style="height:{v / mx * 100:.1f}%"></div>' for i, v in enumerate(vals))
        hist_html = (f'<h2>時間帯別の出現数 <span class="mut">（{unit}）</span></h2><div class="bars">{bars}</div>'
                     f'<div class="axis"><span>{e(labels[0])}</span><span>{e(labels[len(labels)//2])}</span><span>{e(labels[-1])}</span></div>')

    # タイムライン（クラスごと）
    tl_html = ""
    if rows and duration > 0:
        by_cls = defaultdict(list)
        for r in rows:
            by_cls[r["class"]].append(r)
        lines = []
        for cls, rs in sorted(by_cls.items(), key=lambda kv: -len(kv[1])):
            segs = "".join(
                f'<i title="#{r["no"]} {e(r["start_clock"] or r["start_video"])} 〜 {r["duration_sec"]}秒" '
                f'style="left:{r["start_sec"] / duration * 100:.2f}%;width:{max(r["duration_sec"], 0.1) / duration * 100:.2f}%"></i>'
                for r in rs)
            lines.append(f'<div class="tlrow"><span>{e(JA_LABELS.get(cls, cls))}</span><div class="tltrack">{segs}</div></div>')
        st = start_dt.strftime("%H:%M:%S") if start_dt else "0:00:00"
        en = (start_dt + timedelta(seconds=duration)).strftime("%H:%M:%S") if start_dt else fmt_sec(duration)
        tl_html = (f'<h2>タイムライン</h2><div class="tl">{"".join(lines)}</div>'
                   f'<div class="axis"><span>{st}</span><span>{en}</span></div>')

    # イベント表
    has_roi = summary.get("roi") is not None
    has_line = summary.get("line") is not None
    head = "<tr><th>No</th><th>画像</th><th>種類</th><th>時刻</th><th>映っていた時間</th><th>信頼度</th>"
    if has_roi:
        head += "<th>エリア</th>"
    if has_line:
        head += "<th>ライン通過</th>"
    head += "</tr>"
    trs = []
    for r in rows:
        img = f'<a href="{e(r["snapshot"])}" target="_blank"><img src="{e(r["snapshot"])}" loading="lazy" alt=""></a>' if r["snapshot"] else ""
        when = r["start_clock"] or f'動画内 {r["start_video"]}'
        tags = ""
        if has_roi:
            t = ""
            if r["in_roi"] == "はい":
                t = f'<span class="tag">侵入 {r["roi_sec"]}秒</span> '
            if r["loitering"]:
                t += '<span class="tag w">うろつき</span>'
            tags += f"<td>{t}</td>"
        if has_line:
            tags += f'<td>{e(r["line_cross"])}</td>'
        trs.append(
            f'<tr data-c="{e(r["class"])}"><td>{r["no"]}</td><td>{img}</td><td>{e(r["label"])}</td>'
            f'<td>{e(when)}<br><span class="mut">動画内 {r["start_video"]}〜{r["end_video"]}</span></td>'
            f'<td>{r["duration_sec"]} 秒</td><td>{r["confidence"]}</td>{tags}</tr>')
    btns = '<button class="on" data-f="">すべて</button>' + "".join(
        f'<button data-f="{e(c)}">{e(JA_LABELS.get(c, c))}（{n}）</button>' for c, n in sorted(counts.items(), key=lambda kv: -kv[1]))
    table = (f'<h2>イベント一覧</h2><div class="filter">{btns}</div><div class="tbl"><table>{head}{"".join(trs)}</table></div>'
             if rows else '<h2>イベント一覧</h2><p class="mut">設定した条件に当てはまる検出はありませんでした。</p>')

    video_html = (f'<h2>ダイジェスト動画 <span class="mut">（何か映っていた場面だけ）</span></h2>'
                  f'<video src="{e(summary["digest"])}" controls preload="metadata"></video>') if summary.get("digest") else ""

    start_txt = summary["start_clock"] or "不明（ファイル名から読み取れず。--start で指定可）"
    doc = f"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>防犯カメラ解析レポート</title><style>{REPORT_CSS}</style></head><body><main>
<h1>防犯カメラ解析レポート</h1>
<div class="mut">{e(summary["video"])} ／ 録画時間 {summary["duration"]} ／ 開始 {e(start_txt)} ／ {summary["resolution"]} {summary["fps"]}fps ／ 解析時間 {fmt_sec(summary["processing_sec"])}</div>
<div class="cards">{"".join(cards)}</div>
{video_html}{hist_html}{tl_html}{table}
<p class="mut" style="margin-top:24px">自動検出の結果です。見逃し・誤検出がありえるため、重要な場面は元の録画で確認してください。<br>
条件: 信頼度 {args.conf} 以上 ／ {args.min_duration:g} 秒以上映ったもの ／ 解析 {summary["analysis_fps"]} fps ／ 動き検知のしきい値 {args.motion}</p>
</main><script>
document.querySelectorAll('.filter button').forEach(b=>b.onclick=()=>{{document.querySelectorAll('.filter button').forEach(x=>x.classList.toggle('on',x===b));
const f=b.dataset.f;document.querySelectorAll('tr[data-c]').forEach(r=>r.style.display=(!f||r.dataset.c===f)?'':'none')}});
</script></body></html>"""
    dest.write_text(doc, encoding="utf-8")


def write_index(dest: Path, results: list[dict]):
    e = html.escape
    rows = []
    for r in results:
        s = r["summary"]
        rel = r["out_dir"].name
        cnt = "、".join(f'{JA_LABELS.get(c, c)} {n}' for c, n in sorted(s["counts"].items(), key=lambda kv: -kv[1])) or "なし"
        rows.append(f'<tr><td><a href="{e(rel)}/report.html">{e(s["video"])}</a></td><td>{e(s["start_clock"] or "")}</td>'
                    f'<td>{s["duration"]}</td><td>{s["event_count"]}</td><td>{e(cnt)}</td></tr>')
    total = Counter()
    for r in results:
        total.update(r["summary"]["counts"])
    cards = "".join(f'<div class="card"><span class="mut">{e(JA_LABELS.get(c, c))}</span><b>{n}</b></div>' for c, n in total.most_common())
    doc = f"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>防犯カメラ解析 一覧</title><style>{REPORT_CSS}</style></head><body><main>
<h1>防犯カメラ解析 一覧</h1><div class="mut">{len(results)} 本の動画</div><div class="cards">{cards}</div>
<h2>動画ごとの結果</h2><div class="tbl"><table><tr><th>ファイル</th><th>開始時刻</th><th>長さ</th><th>イベント</th><th>内訳</th></tr>{"".join(rows)}</table></div>
<p class="mut">全イベントの一覧は events_all.csv（Excel で開けます）にもあります。</p></main></body></html>"""
    dest.write_text(doc, encoding="utf-8")


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def collect_videos(inputs: list[str]) -> list[Path]:
    vids = []
    for s in inputs:
        p = Path(s)
        if p.is_dir():
            vids += sorted(q for q in p.rglob("*") if q.suffix.lower() in VIDEO_EXTS)
        elif p.is_file():
            vids.append(p)
        else:
            print(f"見つかりません: {s}", file=sys.stderr)
    return vids


def build_parser():
    ap = argparse.ArgumentParser(description="防犯カメラ映像の解析（人・車などの検出、イベント一覧、ダイジェスト、侵入・うろつき・通過カウント）")
    ap.add_argument("inputs", nargs="+", help="動画ファイル、または動画の入ったフォルダ")
    ap.add_argument("--out", default="cctv_results", help="結果の保存先フォルダ（既定: cctv_results）")
    ap.add_argument("--start", help='録画開始の日時 "YYYY-MM-DD HH:MM:SS"（省略時はファイル名から推測）')
    ap.add_argument("--classes", default=DEFAULT_CLASSES, help=f"検出する種類（カンマ区切り、既定: {DEFAULT_CLASSES}）")
    ap.add_argument("--model", default="yolo11n.pt", help="YOLO モデル（精度重視なら yolo11s.pt / yolo11m.pt）")
    ap.add_argument("--conf", type=float, default=0.35, help="検出の信頼度しきい値（既定 0.35）")
    ap.add_argument("--imgsz", type=int, default=640, help="推論サイズ（遠くの小さい人を拾うなら 960〜1280）")
    ap.add_argument("--fps", type=float, default=5, help="1 秒あたり何フレーム解析するか（既定 5。0 で全フレーム）")
    ap.add_argument("--motion", type=float, default=0.003, help="動きありと判定する画面変化の割合（既定 0.003 = 0.3%%）")
    ap.add_argument("--no-motion-gate", action="store_true", help="動き検知で間引かず、全解析フレームで検出する")
    ap.add_argument("--idle-check", type=float, default=5, help="動きがなくても何秒ごとに検出を走らせるか（既定 5）")
    ap.add_argument("--hold", type=float, default=3, help="最後に検出してから何秒は検出を続けるか（既定 3）")
    ap.add_argument("--min-duration", type=float, default=1.0, help="この秒数以上映ったものだけイベントにする（既定 1.0）")
    ap.add_argument("--min-hits", type=int, default=3, help="最低検出回数（既定 3）")
    ap.add_argument("--roi", help='監視エリア（多角形）"x,y;x,y;x,y;..."（0〜1 の割合）')
    ap.add_argument("--loiter", type=float, default=30, help="監視エリアにこの秒数以上いたら「うろつき」（既定 30）")
    ap.add_argument("--line", help='通過カウント用の線 "x1,y1;x2,y2"（0〜1 の割合）')
    ap.add_argument("--pad", type=float, default=2, help="ダイジェストで検出の前後に何秒残すか（既定 2）")
    ap.add_argument("--line-margin", type=float, default=0.02, help=argparse.SUPPRESS)
    ap.add_argument("--no-digest", dest="digest", action="store_false", help="ダイジェスト動画を作らない（速くなる）")
    ap.add_argument("--no-track", dest="track", action="store_false", help=argparse.SUPPRESS)
    ap.add_argument("--tracker", default="bytetrack.yaml", help=argparse.SUPPRESS)
    ap.add_argument("--device", default=None, help="推論デバイス（例: cpu, 0）")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    videos = collect_videos(args.inputs)
    if not videos:
        print("解析する動画が見つかりませんでした。", file=sys.stderr)
        return 1

    from ultralytics import YOLO
    model = YOLO(args.model)
    name_to_id = {v: k for k, v in model.names.items()}
    wanted = [c.strip() for c in args.classes.split(",") if c.strip()]
    unknown = [c for c in wanted if c not in name_to_id]
    if unknown:
        print(f"未対応の種類: {unknown}\n使える名前: {', '.join(model.names.values())}", file=sys.stderr)
        return 1
    class_ids = [name_to_id[c] for c in wanted]

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    analyzer = VideoAnalyzer(args, model, class_ids)

    print(f"{len(videos)} 本の動画を解析します → {out_root.resolve()}")
    results = []
    used = set()
    for v in videos:
        name = re.sub(r"[^\w\-.]+", "_", v.stem)
        base, k = name, 2
        while name in used:
            name = f"{base}_{k}"; k += 1
        used.add(name)
        try:
            results.append(analyzer.analyze(v, out_root / name))
        except Exception as ex:  # 1 本壊れていても残りは続ける
            print(f"\n  {v.name}: エラーのためスキップ（{ex}）", file=sys.stderr)

    if not results:
        return 1

    # 全動画まとめ
    all_rows = [r for res in results for r in res["rows"]]
    with open(out_root / "events_all.csv", "w", newline="", encoding="utf-8-sig") as f:
        if all_rows:
            w = csv.DictWriter(f, fieldnames=["folder"] + list(all_rows[0].keys()))
            w.writeheader()
            for res in results:
                for r in res["rows"]:
                    w.writerow({"folder": res["out_dir"].name, **r})
    if len(results) > 1:
        write_index(out_root / "index.html", results)

    print("\n=== 結果 ===")
    for res in results:
        s = res["summary"]
        cnt = "、".join(f'{JA_LABELS.get(c, c)} {n}' for c, n in sorted(s["counts"].items(), key=lambda kv: -kv[1])) or "検出なし"
        extra = ""
        if s.get("roi_entries") is not None:
            extra += f" ／ エリア侵入 {s['roi_entries']}（うろつき {s['loitering']}）"
        if s.get("line_crossings"):
            extra += " ／ 通過 " + "、".join(f"{k} {n}" for k, n in s["line_crossings"].items())
        print(f"  {s['video']}（{s['duration']}）: {s['event_count']} 件 — {cnt}{extra}")
        print(f"    レポート: {(res['out_dir'] / 'report.html').resolve()}")
    if len(results) > 1:
        print(f"  一覧: {(out_root / 'index.html').resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
