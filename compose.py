# -*- coding: utf-8 -*-
"""
후킹 숏츠 합성기
input 폴더의 대본(.txt)을 읽어 output 폴더에 9:16 완성 영상(mp4)을 만듭니다.

  상단: 후킹 2줄 (1줄 흰색, 2줄 노란색)
  가운데: 원본 영상 (정사각형 크롭 또는 원본 비율)
  영상 위 왼쪽: 구간 라벨 (예: 3위)          (모음 영상일 때 선택)
  영상 하단: 해설 자막 + 나레이션
  맨 아래: 출처 표기 (선택)

사용법:
  python compose.py              -> input 폴더의 대본 전체 처리 (이미 만든 건 건너뜀)
  python compose.py ep001.txt    -> 그 대본 하나만 처리
  python compose.py --force      -> 이미 만든 것도 다시 만들기

------------------------------------------------------------ 대본 형식
(1) 클립 하나짜리: 대본 이름 = 영상 이름 (skate01.mp4 + skate01.txt)

    HOOK1: 시속 80km로 달리다
    HOOK2: 급정거 하면?
    CREDIT: 원본: 채널명 (CC BY)
    TRIM: 2.5-17
    ---
    지금 속도 시속 80km
    앞에 {급커브}가 보이는데

(2) 여러 클립 모음 (1분 이상 만들 때): [clip: 파일 | 시작-끝 | 라벨]

    HOOK1: 브레이크 없이 달리던
    HOOK2: 마지막 장면은?
    ---
    [clip: skate01.mp4 | 3-15 | 3위]
    첫 번째는 프랑스 알프스
    [clip: skate02.mp4 | 0-12 | 2위]
    두 번째는 ...

설정 (--- 위에, 전부 선택)
    CREDIT: 출처 문구            CROP: square / fit          TRIM: 시작-끝 (클립 하나일 때)
    AUDIO: on / off (원본 소리)   ORIGINAL_VOLUME: 0.3
    NARRATION: on / off          VOICE: ko-KR-InJoonNeural (남) / ko-KR-SunHiNeural (여)
    RATE: +10%                   SPEAK_HOOK: on / off        GAP: 0.25 (문장 사이 쉬는 초)
    BGM: music.mp3 (input 폴더)   BGM_VOLUME: 0.12            FONT: 폰트 파일 경로

자막 규칙
    · 한 줄 = 자막 한 장면.  {중괄호} 안 글자는 노란색 강조
    · 나레이션 on: 자막을 한 줄씩 읽어 주고 시간도 목소리에 맞춰 자동
    · 나레이션 off: "시작-끝 | 문장" 으로 시간 지정, 안 쓰면 자동 분배
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BASE = Path(__file__).resolve().parent
INPUT_DIR = BASE / "input"
OUTPUT_DIR = BASE / "output"
VIDEO_EXT = [".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"]

W, H = 1080, 1920          # 숏츠 해상도
VIDEO_TOP = 600            # 영상이 시작되는 세로 위치
VIDEO_BOX = 1080           # 영상 영역 (정사각형)
VIDEO_X = 0                # 영상 영역 왼쪽 위치
STYLE = "clean"            # clean = 흰 바탕 카드형 (기본) / dark = 예전 검은 바탕
INK = (17, 17, 20)
MUTED = (110, 110, 118)
ACCENT = (255, 72, 54)     # 진행바·강조 색 (대본에 ACCENT: #ff4836 으로 바꿀 수 있음)
CARD_R = 36                # 카드 모서리 둥글기


def set_layout(cfg):
    """대본의 STYLE 에 맞게 화면 배치를 정함"""
    global VIDEO_TOP, VIDEO_BOX, VIDEO_X, STYLE, ACCENT
    STYLE = cfg.get("STYLE", "clean").lower()
    if STYLE == "dark":
        VIDEO_TOP, VIDEO_BOX, VIDEO_X = 600, 1080, 0
    else:
        STYLE = "clean"
        # 위: 진행바·출처 → 제목(후킹) → 영상 카드 → 자막. 쇼츠 하단 UI(y>1520)에 안 가리게
        VIDEO_TOP, VIDEO_BOX, VIDEO_X = 470, 900, 90
    m = re.match(r"#?([0-9a-fA-F]{6})$", cfg.get("ACCENT", "").strip())
    ACCENT = tuple(int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4)) if m else (255, 72, 54)
FPS = 30
REPLAY_SPEED = 0.5         # 슬로모션 다시보기 속도
REPLAY_ZOOM = 1.18         # 다시보기 때 확대 비율
PORTRAIT_RATIO = 0.8       # 가로/세로가 이보다 작으면 세로 영상 → 잘라내지 않고 흐린 배경 위에 통째로

WHITE = (255, 255, 255)
YELLOW = (255, 228, 0)
BLACK = (0, 0, 0)

FONT_CANDIDATES = [
    ("C:/Windows/Fonts/malgunbd.ttf", 0),      # 맑은 고딕 Bold (윈도우 기본)
    ("C:/Windows/Fonts/malgun.ttf", 0),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 1),
    ("/System/Library/Fonts/AppleSDGothicNeo.ttc", 0),
]


# ================================================================ 대본 읽기
def parse_script(path):
    """반환: cfg(dict), segments([{clip, start, end, label, lines:[[a,b,text]]}])"""
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    head, sep, body = text.partition("---")
    if not sep:
        raise ValueError("대본에 --- 줄이 없어요 (설정과 자막을 구분하는 줄)")
    cfg = {}
    for line in head.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            cfg[k.strip().upper()] = v.strip()

    segments, cur = [], None
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^\[clip:\s*(.+?)\]$", line, re.I)
        if m:
            parts = [p.strip() for p in m.group(1).split("|")]
            cur = {"clip": parts[0], "start": None, "end": None,
                   "label": parts[2] if len(parts) > 2 else "", "lines": [], "beats": [],
                   "mute": len(parts) > 3 and ("mute" in parts[3].lower() or "replay" in parts[3].lower()),
                   "replay": len(parts) > 3 and "replay" in parts[3].lower(),
                   "teaser": len(parts) > 3 and "teaser" in parts[3].lower(),
                   "cut_top": _flag_num(parts, "cutt"), "cut_bottom": _flag_num(parts, "cutb"),
                   "key": _flag_val(parts, "key", 0, 999), "fx": _flag_val(parts, "fx", 0, 1),
                   "fy": _flag_val(parts, "fy", 0, 1)}
            if len(parts) > 1 and parts[1]:
                a, b = parts[1].split("-")
                cur["start"], cur["end"] = float(a), float(b)
            segments.append(cur)
            continue
        if cur is None:   # 클립 하나짜리 (옛 형식)
            cur = {"clip": None, "start": None, "end": None, "label": "", "lines": [], "beats": [],
                   "mute": False}
            if cfg.get("TRIM"):
                a, b = cfg["TRIM"].split("-")
                cur["start"], cur["end"] = float(a), float(b)
            segments.append(cur)
        bm = re.match(r"^@\s*([\d.]+)\s*\|\s*(.+)$", line)      # @3.5 | 그 장면에서 읽을 문장
        if bm:
            cur["beats"].append((float(bm.group(1)), bm.group(2).strip()))
            continue
        tm = re.match(r"^([\d.]+)\s*-\s*([\d.]+)\s*\|\s*(.+)$", line)
        if tm:
            cur["lines"].append([float(tm.group(1)), float(tm.group(2)), tm.group(3)])
        else:
            cur["lines"].append([None, None, line])

    if not segments:  # 자막 없이 영상만
        segments = [{"clip": None, "start": None, "end": None, "label": "", "lines": [], "beats": [],
                     "mute": False}]
        if cfg.get("TRIM"):
            a, b = cfg["TRIM"].split("-")
            segments[0]["start"], segments[0]["end"] = float(a), float(b)
    for seg in segments:
        seg["clip"] = resolve_clip(seg["clip"], path)
    return cfg, segments


def _flag_num(parts, key):
    """[clip: ... | ... | ... | mute cutb=0.18] 에서 cutb 값 (원본 자막 띠 비율, 0~0.35)"""
    if len(parts) < 4:
        return 0.0
    m = re.search(rf"{key}=([\d.]+)", parts[3])
    return min(max(float(m.group(1)), 0.0), 0.35) if m else 0.0


def _flag_val(parts, key, lo, hi):
    """key=12.5 fx=0.4 처럼 적힌 값 (없으면 None)"""
    if len(parts) < 4:
        return None
    m = re.search(rf"(?<![a-z]){key}=([\d.]+)", parts[3])
    return min(max(float(m.group(1)), lo), hi) if m else None


def resolve_clip(name, script_path):
    if name:
        p = Path(name)
        for cand in (p, script_path.parent / p, INPUT_DIR / p):
            if cand.exists():
                return cand
        raise FileNotFoundError(f"클립 파일을 못 찾았어요: {name}")
    for ext in VIDEO_EXT:
        cand = script_path.with_suffix(ext)
        if cand.exists():
            return cand
    raise FileNotFoundError(f"{script_path.name} 와 같은 이름의 영상이 없어요")


def distribute(lines, t0, length):
    """시간이 안 적힌 자막을 구간 안에 글자 수 비례로 배분"""
    if not lines:
        return []
    if all(l[0] is not None for l in lines):
        return [[t0 + a, t0 + b, s] for a, b, s in lines]
    weights = [max(len(l[2]), 4) for l in lines]
    total, t, out = sum(weights), t0, []
    for l, w in zip(lines, weights):
        d = length * w / total
        out.append([round(t, 2), round(t + d, 2), l[2]])
        t += d
    return out


# ================================================================ 글자 그리기
_font_cache = {}


def load_font(size, custom=None):
    key = (size, custom)
    if key in _font_cache:
        return _font_cache[key]
    cands = ([(custom, 0)] if custom else []) + FONT_CANDIDATES
    for p, idx in cands:
        if p and os.path.exists(p):
            _font_cache[key] = ImageFont.truetype(p, size, index=idx)
            return _font_cache[key]
    raise FileNotFoundError("한글 폰트를 찾지 못했어요. 대본에 FONT: 경로를 적어 주세요.")


def split_runs(text, base_color):
    runs = []
    for part in re.split(r"(\{[^}]*\})", text):
        if not part:
            continue
        if part.startswith("{") and part.endswith("}"):
            runs.append((part[1:-1], YELLOW))
        else:
            runs.append((part, base_color))
    return runs


def wrap_runs(runs, font, max_w):
    chars = [(ch, c) for t, c in runs for ch in t]
    lines, cur = [], []

    def width(cs):
        return font.getlength("".join(ch for ch, _ in cs))

    for item in chars:
        cur.append(item)
        if width(cur) > max_w and len(cur) > 1:
            cut = max((i for i, (ch, _) in enumerate(cur) if ch == " "), default=-1)
            if cut > 0:
                lines.append(cur[:cut])
                cur = cur[cut + 1:]
            else:
                lines.append(cur[:-1])
                cur = [cur[-1]]
    if cur:
        lines.append(cur)
    return lines


def draw_lines(img, lines, font, y, stroke, line_gap=1.18, anchor="center"):
    d = ImageDraw.Draw(img)
    asc, desc = font.getmetrics()
    lh = int((asc + desc) * line_gap)
    block_h = lh * len(lines)
    top = y - block_h // 2 if anchor == "center" else y - block_h
    for i, line in enumerate(lines):
        text = "".join(ch for ch, _ in line)
        x = (W - font.getlength(text)) / 2
        yy = top + i * lh
        for ch, color in line:
            d.text((x, yy), ch, font=font, fill=color, stroke_width=stroke, stroke_fill=BLACK)
            x += font.getlength(ch)


def fit_font(runs_list, size, min_size, max_w, custom):
    while size > min_size:
        f = load_font(size, custom)
        if all(f.getlength("".join(t for t, _ in r)) <= max_w for r in runs_list):
            return f
        size -= 4
    return load_font(min_size, custom)


def render_base_layers(cfg, base_path, mask_path):
    """clean 스타일: 흰 바탕 + 카드 그림자 + 진행바 바탕(base), 카드 모서리를 둥글게 덮는 판(mask)"""
    from PIL import ImageFilter
    base = Image.new("RGBA", (W, H), WHITE + (255,))
    x0, y0, x1, y1 = VIDEO_X, VIDEO_TOP, VIDEO_X + VIDEO_BOX, VIDEO_TOP + VIDEO_BOX
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([x0 + 6, y0 + 22, x1 - 6, y1 + 22], CARD_R, fill=(0, 0, 0, 60))
    base.alpha_composite(sh.filter(ImageFilter.GaussianBlur(26)))
    ImageDraw.Draw(base).rounded_rectangle([40, 40, W - 40, 48], 4, fill=(228, 228, 233, 255))
    base.save(base_path)
    hole = Image.new("L", (W, H), 255)                       # 카드 안쪽만 뚫린 판
    ImageDraw.Draw(hole).rounded_rectangle([x0, y0, x1 - 1, y1 - 1], CARD_R, fill=0)
    mask = base.copy()
    mask.putalpha(hole)
    mask.save(mask_path)


def render_progress_gaps(cfg, segments, total, img):
    """진행바를 장면 수만큼 나누는 흰 틈 + 양 끝 정리"""
    d = ImageDraw.Draw(img)
    d.rectangle([0, 34, 39, 54], fill=WHITE + (255,))
    d.rectangle([W - 39, 34, W, 54], fill=WHITE + (255,))
    span = W - 80
    for seg in segments[1:]:
        x = 40 + span * seg["t0"] / max(total, 0.1)
        d.rectangle([x - 5, 34, x + 5, 54], fill=WHITE + (255,))


def draw_marked_line(img, runs, font, y, color):
    """한 줄 그리기: {강조} 부분 아래에 형광펜 (clean 스타일)"""
    d = ImageDraw.Draw(img)
    text = "".join(t for t, _ in runs)
    x = (W - font.getlength(text)) / 2
    size = font.size
    for t, c in runs:
        w = font.getlength(t)
        if c == YELLOW and t.strip():
            mark = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ImageDraw.Draw(mark).rounded_rectangle([x - 8, y + size * 0.55, x + w + 8, y + size * 1.12], 8,
                                                   fill=ACCENT + (105,))
            img.alpha_composite(mark)
            d = ImageDraw.Draw(img)
        d.text((x, y), t, font=font, fill=color)
        x += w


def render_static_layer_clean(cfg, path, segments, total):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    custom = cfg.get("FONT")
    render_progress_gaps(cfg, segments, total, img)
    credit = cfg.get("CREDIT")
    if credit:                                                 # 출처: 진행바 아래 작은 칩
        f = load_font(28, custom)
        d = ImageDraw.Draw(img)
        tw = f.getlength(credit)
        asc, desc = f.getmetrics()
        d.rounded_rectangle([40, 70, 40 + tw + 40, 70 + asc + desc + 18], 22, fill=(240, 240, 243, 255))
        d.text((60, 79), credit, font=f, fill=MUTED)
    hooks = [split_runs(cfg.get("HOOK1", ""), INK), split_runs(cfg.get("HOOK2", ""), INK)]
    hooks = [h for h in hooks if h]
    if hooks:
        font = fit_font(hooks, 94, 56, W - 120, custom)
        asc, desc = font.getmetrics()
        lh = int((asc + desc) * 1.12)
        top_space, bottom = 140, VIDEO_TOP - 30                # 출처 칩 아래 ~ 카드 위
        y = top_space + (bottom - top_space - lh * len(hooks)) / 2
        for r in hooks:
            draw_marked_line(img, r, font, y, INK)
            y += lh
    img.save(path)


def render_static_layer(cfg, path):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    custom = cfg.get("FONT")
    hooks = [split_runs(cfg.get("HOOK1", ""), WHITE), split_runs(cfg.get("HOOK2", ""), YELLOW)]
    hooks = [h for h in hooks if h]
    if hooks:
        font = fit_font(hooks, 96, 56, W - 80, custom)
        lines = [[(ch, c) for t, c in r for ch in t] for r in hooks]
        draw_lines(img, lines, font, VIDEO_TOP - 40, stroke=6, anchor="bottom")
    credit = cfg.get("CREDIT")
    if credit:
        f = load_font(32, custom)
        d = ImageDraw.Draw(img)
        d.text(((W - f.getlength(credit)) / 2, VIDEO_TOP + VIDEO_BOX + 24), credit,
               font=f, fill=(190, 190, 190))
    img.save(path)


def render_sub_layer(text, cfg, path):
    if STYLE == "clean":                                      # 카드 아래, 흰 바탕 위 진한 글씨
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        font = load_font(62, cfg.get("FONT"))
        runs = [(t, ACCENT if c == YELLOW else INK) for t, c in split_runs(text, INK)]
        lines = wrap_runs(runs, font, 920)[:2]
        asc, desc = font.getmetrics()
        lh = int((asc + desc) * 1.16)
        cy = VIDEO_TOP + VIDEO_BOX + 100
        top = cy - lh * len(lines) / 2
        d = ImageDraw.Draw(img)
        for i, line in enumerate(lines):
            t = "".join(ch for ch, _ in line)
            x = (W - font.getlength(t)) / 2
            for ch, col in line:
                d.text((x, top + i * lh), ch, font=font, fill=col)
                x += font.getlength(ch)
        img.save(path)
        return
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    font = load_font(70, cfg.get("FONT"))
    lines = wrap_runs(split_runs(text, WHITE), font, 900)
    cy = VIDEO_TOP + VIDEO_BOX - 150
    if cfg.get("SUB_BOX", "on").lower() != "off":       # 반투명 배경 (원본 화면 위에서도 잘 읽히게)
        asc, desc = font.getmetrics()
        lh = int((asc + desc) * 1.18)
        bw = max(font.getlength("".join(ch for ch, _ in ln)) for ln in lines) + 56
        bh = lh * len(lines) + 30
        box = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(box).rounded_rectangle(
            [(W - bw) / 2, cy - bh / 2 + 4, (W + bw) / 2, cy + bh / 2 + 4], radius=22, fill=(0, 0, 0, 120))
        img = Image.alpha_composite(img, box)
    draw_lines(img, lines, font, cy, stroke=7)
    img.save(path)


def render_ring_layer(cx, cy, path, r=118):
    """결정적 순간 포커스 링"""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([cx - r - 4, cy - r - 4, cx + r + 4, cy + r + 4], outline=(0, 0, 0, 140), width=14)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=YELLOW + (255,), width=9)
    img.save(path)


def render_flash_layer(path):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(img).rectangle([VIDEO_X, VIDEO_TOP, VIDEO_X + VIDEO_BOX, VIDEO_TOP + VIDEO_BOX],
                                  fill=(255, 255, 255, 110))
    img.save(path)


def measure_luma(clip, start, length):
    """구간의 평균 밝기(0~255). 실패하면 None"""
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-ss", f"{start:.2f}", "-t", f"{min(length, 30):.2f}",
                            "-i", str(clip), "-vf", "fps=2,scale=160:-2,signalstats,"
                            "metadata=print:key=lavfi.signalstats.YAVG", "-an", "-f", "null", "-"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        vals = [float(x) for x in re.findall(r"YAVG=([\d.]+)", r.stderr)]
        return sum(vals) / len(vals) if vals else None
    except Exception:
        return None


def make_whoosh(path):
    """구간 전환용 '휙' 효과음 (직접 합성 - 저작권 없음)"""
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", "anoisesrc=d=0.45:c=pink:a=0.6:r=44100",
                    "-af", "highpass=f=350,lowpass=f=5500,afade=t=in:d=0.28,afade=t=out:st=0.28:d=0.17,"
                           "volume=0.20,aformat=channel_layouts=stereo", str(path)], check=True)


def render_label_layer(label, cfg, path):
    """구간 라벨 (예: 3위) - 영상 왼쪽 위 노란 박스"""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    f = load_font(64, cfg.get("FONT"))
    d = ImageDraw.Draw(img)
    tw = f.getlength(label)
    asc, desc = f.getmetrics()
    x, y, px, py = VIDEO_X + 30, VIDEO_TOP + 30, 26, 10
    if STYLE == "clean":
        f = load_font(48, cfg.get("FONT"))
        tw = f.getlength(label)
        asc, desc = f.getmetrics()
        d.rounded_rectangle([x, y, x + tw + px * 2, y + asc + desc + py * 2], radius=30, fill=INK + (235,))
        d.text((x + px, y + py), label, font=f, fill=WHITE)
    else:
        d.rounded_rectangle([x, y, x + tw + px * 2, y + asc + desc + py * 2], radius=18, fill=YELLOW)
        d.text((x + px, y + py), label, font=f, fill=BLACK)
    img.save(path)


# ================================================================ 나레이션
DEFAULT_VOICE = "ko-KR-InJoonNeural"


# ---------------------------------------------------------------- 나레이션 목소리 (TTS)
# 1) edge-tts (무료, 기본)  2) 구글 클라우드 TTS (월 100만 자 무료, .env GOOGLE_TTS_KEY)
# 3) OpenAI TTS (유료·아주 저렴, .env OPENAI_KEY)  4) gTTS (무료, 품질 낮음, 최후 수단)
# edge 가 막히면 (2026-10 MS 차단처럼) 자동으로 다음 순서로 넘어가요.
TTS_STATE = {"edge_dead": False, "used": set(), "keys": None}


def _tts_keys():
    if TTS_STATE["keys"] is None:
        keys = {}
        for d in (BASE, BASE.parent):
            for n in (".env", ".env.txt", "env.txt", "env"):
                f = d / n
                if f.is_file():
                    for line in f.read_text(encoding="utf-8-sig", errors="replace").splitlines():
                        if "=" in line and not line.strip().startswith("#"):
                            k, v = line.split("=", 1)
                            keys.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        TTS_STATE["keys"] = keys
    return TTS_STATE["keys"]


def _rate_num(rate):
    m = re.match(r"([+-]?\d+)%", str(rate or "").strip())
    return 1.0 + (int(m.group(1)) / 100 if m else 0.0)


def _tts_edge(text, path, voice, rate):
    import asyncio
    import edge_tts

    async def run(v):
        words = []
        com = edge_tts.Communicate(text, v, rate=rate, boundary="WordBoundary")
        with open(path, "wb") as f:
            async for ch in com.stream():
                if ch["type"] == "audio":
                    f.write(ch["data"])
                elif ch["type"] == "WordBoundary":
                    st = ch["offset"] / 1e7
                    words.append((st, st + ch["duration"] / 1e7, ch["text"]))
        return words

    last = None
    for attempt in range(3):
        v = voice if attempt < 2 else DEFAULT_VOICE      # 고른 목소리가 안 되면 기본 목소리로
        try:
            words = asyncio.run(run(v))
            if Path(path).exists() and Path(path).stat().st_size > 0:
                return words
        except Exception as e:
            last = e
            if "403" in str(e):                            # MS 가 막음 → 이번 실행 동안은 edge 건너뜀
                TTS_STATE["edge_dead"] = True
                break
    raise RuntimeError(f"edge-tts: {last}")


def _tts_google(text, path, rate, key):
    import base64
    import requests
    keys = _tts_keys()
    body = {"input": {"text": text},
            "voice": {"languageCode": "ko-KR", "name": keys.get("GOOGLE_TTS_VOICE") or "ko-KR-Neural2-C"},
            "audioConfig": {"audioEncoding": "MP3", "speakingRate": round(_rate_num(rate), 2)}}
    r = requests.post(f"https://texttospeech.googleapis.com/v1/text:synthesize?key={key}", json=body, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"구글 TTS {r.status_code}: {r.text[:160]}")
    Path(path).write_bytes(base64.b64decode(r.json()["audioContent"]))
    return []


def _tts_openai(text, path, rate, key):
    import requests
    keys = _tts_keys()
    body = {"model": keys.get("OPENAI_TTS_MODEL") or "gpt-4o-mini-tts", "voice": keys.get("OPENAI_TTS_VOICE") or "onyx",
            "input": text, "response_format": "mp3", "speed": round(_rate_num(rate), 2),
            "instructions": "자연스러운 한국어 원어민 남성 나레이터. 유튜브 쇼츠처럼 또렷하고 리듬감 있게, 외국어 억양 없이."}
    r = requests.post("https://api.openai.com/v1/audio/speech", json=body, timeout=120,
                      headers={"Authorization": f"Bearer {key}"})
    if r.status_code != 200:
        raise RuntimeError(f"OpenAI TTS {r.status_code}: {r.text[:160]}")
    Path(path).write_bytes(r.content)
    return []


def _tts_gtts(text, path, rate):
    try:
        from gtts import gTTS
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check",
                        "--no-warn-script-location", "gtts"], capture_output=True)
        from gtts import gTTS
    raw = Path(path).with_suffix(".raw.mp3")
    gTTS(text, lang="ko").save(str(raw))
    speed = max(0.5, min(2.0, _rate_num(rate) * 1.12))    # gTTS 는 느려서 조금 빠르게
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-filter:a", f"atempo={speed:.2f}",
                    "-c:a", "libmp3lame", "-q:a", "3", str(path)], check=True)
    raw.unlink(missing_ok=True)
    return []


def tts(text, path, voice, rate):
    """나레이션 mp3 만들기.
    반환: 단어별 타이밍 [(시작초, 끝초, 단어)] (edge 만 제공, 나머지는 [] → 자막은 글자 수 비례로 맞춤)"""
    keys = _tts_keys()
    chain = []
    if not TTS_STATE["edge_dead"]:
        chain.append(("edge", lambda: _tts_edge(text, path, voice, rate)))
    gkey = keys.get("GOOGLE_TTS_KEY")
    if gkey:
        chain.append(("google", lambda: _tts_google(text, path, rate, gkey)))
    if keys.get("OPENAI_KEY"):
        chain.append(("openai", lambda: _tts_openai(text, path, rate, keys["OPENAI_KEY"])))
    chain.append(("gtts", lambda: _tts_gtts(text, path, rate)))
    errors = []
    for name, fn in chain:
        try:
            words = fn()
            if Path(path).exists() and Path(path).stat().st_size > 0:
                if name not in TTS_STATE["used"] and name != "edge":
                    print(f"  (나레이션 목소리: {TTS_NAMES[name]})")
                TTS_STATE["used"].add(name)
                return words
        except Exception as e:
            errors.append(f"{name}: {str(e)[:160]}")
    raise RuntimeError("나레이션 생성 실패 — " + " / ".join(errors))


TTS_NAMES = {"edge": "기본(edge)", "google": "구글 클라우드 TTS", "openai": "OpenAI TTS",
             "gtts": "구글 번역기 음성 (임시 — 품질 낮음)"}


def tts_note():
    """이번 영상에 기본 목소리 말고 다른 목소리를 썼으면 알림 문구 (텔레그램용)"""
    other = [TTS_NAMES[n] for n in ("google", "openai", "gtts") if n in TTS_STATE["used"]]
    TTS_STATE["used"] = set()
    if not other:
        return ""
    return ("🔊 기본 목소리(edge)가 마이크로소프트 쪽 문제로 막혀서 " + ", ".join(other) + " 목소리로 만들었어요."
            + (" (임시 음성이라 품질이 낮아요)" if "gtts" in " ".join(other) or "번역기" in " ".join(other) else ""))


def _norm(t):
    return re.sub(r"[\W_]+", "", t)


def align_lines(lines, words, total):
    """자막 줄마다 시작 시간을 단어 타이밍에서 찾아 줌 (못 찾으면 글자 수 비례)"""
    lens = [max(len(_norm(l)), 1) for l in lines]
    wl = [len(_norm(w)) for _, _, w in words]
    if words and abs(sum(wl) - sum(lens)) <= max(3, 0.25 * sum(lens)):
        starts, pos, cum, j = [], 0, 0, 0
        scale = sum(wl) / sum(lens)
        for L in lens:
            target = pos * scale
            while j < len(words) - 1 and cum + wl[j] <= target:
                cum += wl[j]
                j += 1
            starts.append(words[j][0] if pos else 0.0)
            pos += L
    else:
        starts, acc = [], 0
        for L in lens:
            starts.append(total * acc / sum(lens))
            acc += L
    ends = starts[1:] + [total]
    return [(round(a, 2), round(max(b, a + 0.3), 2)) for a, b in zip(starts, ends)]


def chunk_sentence(text, max_len=17):
    """긴 문장을 화면 자막 크기로 어절 단위 분할 (길이를 고르게, 쉼표 뒤에서 끊는 걸 선호).
    {강조}가 잘리면 양쪽에 다시 붙여 줌"""
    words = text.split()
    total = len(plain(text))
    if total <= max_len + 1 or len(words) < 2:
        chunks = [text.strip()]
    else:
        n = -(-total // max_len)                       # 필요한 줄 수 (올림)
        chunks, rest_words = [], list(words)
        while rest_words:
            remaining_lines = n - len(chunks)
            if remaining_lines <= 1:
                chunks.append(" ".join(rest_words))
                break
            target = len(plain(" ".join(rest_words))) / remaining_lines
            best, best_cost = 1, None
            for k in range(1, len(rest_words)):
                left = " ".join(rest_words[:k])
                L = len(plain(left))
                if L > max_len + 2:
                    break
                prev_w, next_w = plain(rest_words[k - 1]), plain(rest_words[k])
                cost = abs(L - target) - (4 if left.endswith((",", "，")) else 0)
                # '한 마리', '골프공만 한 얼음' 처럼 붙어 다니는 말은 안 끊기게
                if len(re.sub(r"\W", "", prev_w)) <= 1 or len(re.sub(r"\W", "", next_w)) <= 1:
                    cost += 6
                if re.match(r"^(마리|명|대|개|번|채|척|살|초|분|시간|미터|km|m|도|쪽|때|것|수|줄|데|뿐|만큼)", next_w):
                    cost += 6
                if best_cost is None or cost < best_cost:
                    best, best_cost = k, cost
            chunks.append(" ".join(rest_words[:best]))
            rest_words = rest_words[best:]
    out, open_ = [], False
    for c in chunks:
        if open_:
            c = "{" + c
        open_ = c.count("{") > c.count("}")
        if open_:
            c = c + "}"
        out.append(c)
    return out


def display(text):
    """자막에는 문장 끝 마침표/쉼표를 빼고 보여 줌 (읽을 때는 사용)"""
    return re.sub(r"[.,。]+$", "", text.strip())


def plain(text):
    return text.replace("{", "").replace("}", "").strip()


# ================================================================ ffmpeg
def video_dims(path):
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
                             capture_output=True, text=True, check=True).stdout.strip().splitlines()[0]
        w, h = (int(x) for x in out.split("x")[:2])
        return w, h
    except Exception:
        return 16, 9


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,width,height", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", check=True).stdout
    info = json.loads(out)
    has_audio = any(s.get("codec_type") == "audio" for s in info["streams"])
    return float(info["format"]["duration"]), has_audio


def audio_len(path):
    return probe(path)[0]


def xfade_len(cfg, segments):
    """구간 사이 크로스페이드 길이 (끄면 0)"""
    if cfg.get("TRANSITION", "on").lower() == "off" or len(segments) < 2:
        return 0.0
    return float(cfg.get("XFADE", "0.4"))


def plan_timeline(cfg, segments, tmp):
    """구간별 길이, 자막 시간, 나레이션 위치를 계산"""
    narration = cfg.get("NARRATION", "on").lower() != "off"
    voice = cfg.get("VOICE", "ko-KR-InJoonNeural")
    rate = cfg.get("RATE", "+5%")
    gap = float(cfg.get("GAP", "0.35"))
    single = len(segments) == 1
    xd = xfade_len(cfg, segments)

    T = 0.0
    subs, voices, labels = [], [], []
    for si, seg in enumerate(segments):
        full, has_audio = probe(seg["clip"])
        start = seg["start"] if seg["start"] is not None else 0.0
        end = min(seg["end"], full) if seg["end"] is not None else full
        if end - start < 0.5:
            raise ValueError(f"{seg['clip'].name}: 구간({start}-{end})이 너무 짧아요")
        speed = REPLAY_SPEED if seg.get("replay") else 1.0
        seg.update(start=start, src_len=round(end - start, 3),
                   clip_len=round((end - start) / speed, 3), has_audio=has_audio)

        if narration and (seg["lines"] or seg["beats"] or (si == 0 and cfg.get("HOOK1"))):
            cursor = T + (0.2 if si == 0 else 0.15)
            if si == 0 and cfg.get("SPEAK_HOOK", "on").lower() != "off":
                hook = " ".join(plain(cfg.get(k, "")) for k in ("HOOK1", "HOOK2")).strip()
                if hook:
                    p = tmp / "tts_hook.mp3"
                    tts(hook, p, voice, rate)
                    voices.append((p, cursor))
                    cursor += audio_len(p) + gap + 0.15
            for bi, (at, sentence) in enumerate(seg["beats"]):
                start_at = max(T + at, cursor)
                if start_at - (T + at) > 1.5:
                    print(f"  (자막 {bi + 1}번이 앞 문장 때문에 {start_at - T - at:.1f}초 늦게 나와요)")
                p = tmp / f"tts_{si:02d}_{bi:02d}.mp3"
                words = tts(plain(sentence), p, voice, rate)
                d = audio_len(p)
                voices.append((p, start_at))
                parts = chunk_sentence(sentence)
                for (a, b), t in zip(align_lines([plain(x) for x in parts], words, d), parts):
                    subs.append([round(start_at + a, 2), round(start_at + b, 2), display(t)])
                cursor = start_at + d + gap
            if seg["lines"]:
                texts = [l[2] for l in seg["lines"]]
                p = tmp / f"tts_{si:02d}.mp3"
                words = tts(" ".join(plain(t) for t in texts), p, voice, rate)
                d = audio_len(p)
                voices.append((p, cursor))
                for (a, b), t in zip(align_lines([plain(t) for t in texts], words, d), texts):
                    subs.append([round(cursor + a, 2), round(cursor + b, 2), display(t)])
                cursor += d + gap
            seg_len = max(seg["clip_len"], cursor - T + 0.3)
        else:
            seg_len = seg["clip_len"]
            if single:
                subs += [[a, b, display(t)] for a, b, t in distribute(seg["lines"], T, seg_len)]
            else:
                subs += [[a, b, display(t)] for a, b, t in
                         distribute([[None, None, l[2]] for l in seg["lines"]], T, seg_len)]

        seg["len"] = round(seg_len, 3)
        seg["t0"] = round(T, 3)
        if seg_len > seg["clip_len"] + 0.6:
            print(f"  ({seg['clip'].name}: 나레이션이 {seg_len - seg['clip_len']:.1f}초 길어서 "
                  f"마지막 장면을 멈춘 채로 늘려요 → 대본을 줄이면 더 좋아요)")
        if seg["label"]:
            labels.append((seg["label"], T, T + seg_len))
        T += seg_len - (xd if si < len(segments) - 1 else 0.0)   # 다음 구간과 겹치는 만큼
    return round(T, 3), subs, voices, labels


def compose(script, out_path):
    cfg, segments = parse_script(script)
    set_layout(cfg)
    TTS_STATE["edge_dead"] = False                 # 영상마다 기본 목소리(edge)부터 다시 시도
    tmp = Path(tempfile.mkdtemp(prefix="hookshorts_"))
    try:
        total, subs, voices, labels = plan_timeline(cfg, segments, tmp)

        # ---- 글자 레이어 이미지
        overlays = []                                    # (png, 시작, 끝)
        static_png = tmp / "static.png"
        if STYLE == "clean":
            render_static_layer_clean(cfg, static_png, segments, total)
            base_png, mask_png = tmp / "base.png", tmp / "mask.png"
            render_base_layers(cfg, base_png, mask_png)
        else:
            render_static_layer(cfg, static_png)
        for i, (label, a, b) in enumerate(labels):
            p = tmp / f"label_{i:02d}.png"
            render_label_layer(label, cfg, p)
            overlays.append((p, a, b, "label"))
        for i, (a, b, text) in enumerate(subs):
            p = tmp / f"sub_{i:03d}.png"
            render_sub_layer(text, cfg, p)
            overlays.append((p, a, b, "sub"))
        motion = cfg.get("MOTION", "on").lower() != "off"
        sfx = []
        if motion:
            flash = tmp / "flash.png"
            render_flash_layer(flash)
            whoosh = tmp / "whoosh.wav"
            make_whoosh(whoosh)
            xd = xfade_len(cfg, segments)
            for i, seg in enumerate(segments):
                if i > 0:                                  # 구간이 바뀔 때 은은한 '휙'
                    if not xd:
                        overlays.append((flash, seg["t0"], seg["t0"] + 0.07, "flash"))
                    sfx.append((whoosh, max(0.0, seg["t0"] + xd / 2 - 0.26)))
                if seg.get("key") is not None and not seg.get("replay") and seg["key"] < seg["clip_len"]:
                    fx = seg.get("fx") if seg.get("fx") is not None else 0.5
                    fy = seg.get("fy") if seg.get("fy") is not None else 0.5
                    ring = tmp / f"ring_{i:02d}.png"
                    render_ring_layer(int(VIDEO_X + fx * VIDEO_BOX), int(VIDEO_TOP + fy * VIDEO_BOX), ring)
                    k = seg["t0"] + seg["key"]
                    overlays.append((ring, k - 0.1, k + 0.9, "ring"))

        fit = cfg.get("CROP", "square").lower() == "fit"
        audio_on = cfg.get("AUDIO", "on").lower() != "off"
        orig_vol = float(cfg.get("ORIGINAL_VOLUME", "0.3" if voices else "1.0"))

        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-stats"]
        fc, concat_in = [], ""
        for i, seg in enumerate(segments):
            cmd += ["-ss", str(seg["start"]), "-t", str(seg["src_len"]), "-i", str(seg["clip"])]
            src = f"{i}:v"
            if fit:
                v = (f"scale={VIDEO_BOX}:{VIDEO_BOX}:force_original_aspect_ratio=decrease,"
                     f"pad={VIDEO_BOX}:{VIDEO_BOX}:(ow-iw)/2:(oh-ih)/2")
            else:
                ct, cb = seg.get("cut_top", 0.0), seg.get("cut_bottom", 0.0)
                vw, vh = video_dims(seg["clip"])
                portrait = vw / max(vh * (1 - ct - cb), 1) < PORTRAIT_RATIO
                if portrait:                             # 세로 영상: 통째로 + 양옆은 같은 영상을 흐리게
                    pre = f"setpts={1 / REPLAY_SPEED:.3f}*PTS," if seg.get("replay") else ""
                    band = f"crop=iw:ih*{1 - ct - cb:.3f}:0:ih*{ct:.3f}," if (ct or cb) else ""
                    fc.append(f"[{i}:v]{pre}{band}split[bs{i}][fs{i}]")
                    fc.append(f"[bs{i}]scale={VIDEO_BOX}:{VIDEO_BOX}:force_original_aspect_ratio=increase,"
                              f"crop={VIDEO_BOX}:{VIDEO_BOX},boxblur=24:2,eq=brightness=-0.12[bb{i}]")
                    fc.append(f"[fs{i}]scale=-2:{VIDEO_BOX}:flags=lanczos[ff{i}]")
                    fc.append(f"[bb{i}][ff{i}]overlay=(W-w)/2:0[sq{i}]")
                    src = f"sq{i}"
                    v = "null"
                elif ct or cb:                           # 원본 자막이 있는 위/아래 띠를 피해서 정사각형으로 자르기
                    uh = f"(ih*{1 - ct - cb:.3f})"
                    side = f"min(iw\\,{uh})"
                    v = (f"crop='{side}':'{side}':'(iw-{side})/2':'ih*{ct:.3f}+({uh}-{side})/2',"
                         f"scale={VIDEO_BOX}:{VIDEO_BOX}:flags=lanczos")
                else:
                    v = f"crop='min(iw,ih)':'min(iw,ih)',scale={VIDEO_BOX}:{VIDEO_BOX}:flags=lanczos"
            if seg.get("replay"):                         # 슬로모션 + 살짝 확대
                if src == f"{i}:v":
                    v = f"setpts={1 / REPLAY_SPEED:.3f}*PTS," + v
                v += f",scale=iw*{REPLAY_ZOOM}:-2,crop={VIDEO_BOX}:{VIDEO_BOX}"
            if cfg.get("GRADE", "on").lower() != "off":   # 영상마다 다른 밝기를 맞추고 공통 색감으로
                y = measure_luma(seg["clip"], seg["start"], seg["src_len"])
                br = 0.0 if y is None else max(-0.10, min(0.10, (112 - y) / 255 * 0.8))
                v += f",eq=brightness={br:.3f}:contrast=1.06:saturation=1.12,vignette=angle=PI/5.5"
            if cfg.get("SHARPEN", "on").lower() != "off":
                v += ",unsharp=5:5:0.55:5:5:0.0"           # 업스케일된 원본을 살짝 선명하게
            v += f",setsar=1,fps={FPS},format=yuv420p"
            extra = seg["len"] - seg["clip_len"]
            if extra > 0.01:
                v += f",tpad=stop_mode=clone:stop_duration={extra + 0.2:.2f}"
            v += f",trim=duration={seg['len']},setpts=PTS-STARTPTS"
            key = seg.get("key")
            if (cfg.get("MOTION", "on").lower() != "off" and key is not None and not seg.get("replay")
                    and key < seg["clip_len"]):              # 결정적 순간 '펀치인' 확대 (1.2초)
                fx = seg.get("fx") if seg.get("fx") is not None else 0.5
                fy = seg.get("fy") if seg.get("fy") is not None else 0.5
                z = 1.16
                fc.append(f"[{src}]{v},split[pa{i}][pb{i}]")
                fc.append(f"[pb{i}]scale=iw*{z}:-2:flags=lanczos,"
                          f"crop={VIDEO_BOX}:{VIDEO_BOX}:'(iw-{VIDEO_BOX})*{fx:.3f}':'(ih-{VIDEO_BOX})*{fy:.3f}'[pz{i}]")
                fc.append(f"[pa{i}][pz{i}]overlay=0:0:enable='between(t,{key - 0.12:.2f},{key + 1.1:.2f})'[v{i}]")
            else:
                fc.append(f"[{src}]{v}[v{i}]")
            if seg["has_audio"] and audio_on and not seg.get("mute") and not seg.get("replay"):
                fc.append(f"[{i}:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                          f"volume={orig_vol},apad,atrim=duration={seg['len']},asetpts=PTS-STARTPTS[a{i}]")
            else:
                fc.append(f"anullsrc=r=44100:cl=stereo,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                          f"atrim=duration={seg['len']}[a{i}]")
            concat_in += f"[v{i}][a{i}]"
        xd = xfade_len(cfg, segments)
        if xd:                                            # 구간 사이 크로스페이드
            cv, ca = "v0", "a0"
            for i in range(1, len(segments)):
                if segments[i].get("replay"):
                    tr = "fadewhite"                       # 다시보기: 하얗게 번지며
                elif segments[i - 1].get("teaser"):
                    tr = "fadeblack"                       # 티저 → 본편: 암전
                else:
                    tr = cfg.get("XFADE_TYPE", "fade")     # 본편 사이: 디졸브
                nv, na = f"xv{i}", f"xa{i}"
                fc.append(f"[{cv}][v{i}]xfade=transition={tr}:duration={xd}:offset={segments[i]['t0']:.3f}[{nv}]")
                fc.append(f"[{ca}][a{i}]acrossfade=d={xd}:c1=tri:c2=tri[{na}]")
                cv, ca = nv, na
            fc.append(f"[{cv}]null[vcat]")
            fc.append(f"[{ca}]anull[acat]")
        else:
            fc.append(f"{concat_in}concat=n={len(segments)}:v=1:a=1[vcat][acat]")

        idx = len(segments)
        if STYLE == "clean":
            acc = "0x%02x%02x%02x" % ACCENT
            cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{total:.3f}", "-i", str(base_png),
                    "-i", str(mask_png),
                    "-f", "lavfi", "-i", f"color=c={acc}:s={W - 80}x8:r={FPS}:d={total:.3f}",
                    "-i", str(static_png)]
            bg, mk, bar, st = idx, idx + 1, idx + 2, idx + 3
            idx += 4
            fc.append(f"[{bg}:v][vcat]overlay={VIDEO_X}:{VIDEO_TOP}:shortest=1[b0]")
            fc.append(f"[b0][{mk}:v]overlay=0:0[b0m]")
            # 진행바: 시간에 따라 왼쪽에서 오른쪽으로 차오름
            fc.append(f"[b0m][{bar}:v]overlay=x='40-{W - 80}+{W - 80}*t/{total:.3f}':y=40[b0p]")
            fc.append(f"[b0p][{st}:v]overlay=0:0[b1]")
        else:
            cmd += ["-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={FPS}:d={total}",
                    "-i", str(static_png)]
            bg, st = idx, idx + 1
            idx += 2
            fc.append(f"[{bg}:v][vcat]overlay=0:{VIDEO_TOP}[b0]")
            fc.append(f"[b0][{st}:v]overlay=0:0[b1]")
        last = "b1"
        for i, (p, a, b, kind) in enumerate(overlays):
            cmd += ["-i", str(p)]
            y = "0"
            if kind == "sub" and motion:                 # 자막이 살짝 올라오며 등장
                y = f"'if(lt(t-{a:.2f},0.12),26*(1-(t-{a:.2f})/0.12),0)'"
            fc.append(f"[{last}][{idx}:v]overlay=0:{y}:enable='between(t,{a:.2f},{b:.2f})'[o{i}]")
            last = f"o{i}"
            idx += 1

        # ---- 소리: 원본 + 나레이션 + 배경음악
        #   나레이션이 나오는 동안 원본 소리는 자동으로 작아짐(덕킹), 말이 없을 땐 다시 커짐
        narr = []
        for i, (p, t0) in enumerate(voices):
            cmd += ["-i", str(p)]
            ms = int(t0 * 1000)
            fc.append(f"[{idx}:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                      f"adelay={ms}|{ms}[n{i}]")
            narr.append(f"[n{i}]")
            idx += 1
        bed = "[acat]"
        bgm = cfg.get("BGM")
        if bgm:
            bp = Path(bgm) if Path(bgm).is_absolute() else INPUT_DIR / bgm
            if bp.exists():
                cmd += ["-stream_loop", "-1", "-i", str(bp)]
                fc.append(f"[{idx}:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                          f"volume={float(cfg.get('BGM_VOLUME', '0.16'))}[bgm]")
                fc.append("[acat][bgm]amix=inputs=2:normalize=0:duration=first[bed]")
                bed = "[bed]"
                idx += 1
            else:
                print(f"  (배경음악 {bgm} 파일을 못 찾아서 빼고 만들어요)")
        if narr:
            fc.append(f"{''.join(narr)}amix=inputs={len(narr)}:normalize=0:duration=longest,"
                      f"apad,atrim=duration={total}[narr]")
            if cfg.get("DUCK", "on").lower() != "off":
                fc.append("[narr]asplit=2[narr_out][narr_sc]")
                fc.append(f"{bed}[narr_sc]sidechaincompress=threshold=0.015:ratio=12:attack=15:"
                          "release=350:makeup=1[bed_duck]")
                mix = ["[bed_duck]", "[narr_out]"]
            else:
                mix = [bed, "[narr]"]
        else:
            mix = [bed]
        for j, (sp, st0) in enumerate(sfx if motion else []):
            cmd += ["-i", str(sp)]
            ms = int(st0 * 1000)
            fc.append(f"[{idx}:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                      f"adelay={ms}|{ms}[fx{j}]")
            mix.append(f"[fx{j}]")
            idx += 1
        fade_st = max(0.0, total - 1.2)
        post = f"atrim=duration={total},afade=t=out:st={fade_st:.2f}:d=1.2"
        if cfg.get("LOUDNORM", "on").lower() != "off":
            post += ",loudnorm=I=-14:TP=-1.5:LRA=11,aresample=44100"
        fc.append(f"{''.join(mix)}amix=inputs={len(mix)}:normalize=0:duration=first,{post}[aout]")

        cmd += ["-filter_complex", ";".join(fc), "-map", f"[{last}]", "-map", "[aout]",
                "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
                "-t", str(total), str(out_path)]
        subprocess.run(cmd, check=True)
        return total
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ================================================================ 실행
def main():
    force = "--force" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--force"]
    INPUT_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)

    if args:
        scripts = [Path(a) if Path(a).exists() else INPUT_DIR / a for a in args]
        scripts = [s.with_suffix(".txt") if s.suffix.lower() in VIDEO_EXT else s for s in scripts]
    else:
        scripts = sorted(INPUT_DIR.glob("*.txt"))

    done = skipped = failed = 0
    for script in scripts:
        out = OUTPUT_DIR / f"{script.stem}_final.mp4"
        if out.exists() and not force:
            print(f"[건너뜀] {script.name}: 이미 만들어짐 (다시 만들려면 --force)")
            skipped += 1
            continue
        print(f"[제작중] {script.name}")
        try:
            total = compose(script, out)
            note = "" if total >= 61 else "  ※ 1분 미만 (틱톡 수익화 대상 아님)"
            print(f"[완료]   {out.name}  ({total:.0f}초){note}")
            done += 1
        except Exception as e:
            print(f"[실패]   {script.name}: {e}")
            failed += 1
    print(f"\n완료 {done}개 / 건너뜀 {skipped}개 / 실패 {failed}개  ->  {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
