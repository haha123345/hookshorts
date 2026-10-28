# -*- coding: utf-8 -*-
"""
쇼츠 모아서 롱폼 만들기 (가로 16:9, 8분 이상 → 중간 광고 가능 길이)

  - 클라우드: 쇼츠를 만들 때마다 GitHub 'Releases' 에 영상 파일을 보관 (archive_short)
  - 매주 일요일: 아직 롱폼에 안 쓴 사연 쇼츠 8~12개를 모아 한 편으로 (make_weekly_long)
  - PC: long_pc.bat → PC output 폴더에 있는 예전 사연 쇼츠로 롱폼 한 편 (make_long_from_files)

화면: 가운데 세로 쇼츠 + 양옆은 같은 영상을 흐리게, 왼쪽에 "몇 번째 이야기 · 제목", 챕터(타임스탬프) 자동
"""
import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

import compose

W, H = 1920, 1080
KST = timezone(timedelta(hours=9))
INK, ACCENT = (17, 17, 20), (255, 72, 54)


# ================================================================ 보관 (GitHub Releases)
def _gh(*args, check=True):
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=check)


def archive_short(state, ep, video, meta, theme):
    """만든 쇼츠를 GitHub Releases 에 보관 (클라우드에서만). 실패해도 쇼츠 제작에는 영향 없음"""
    try:
        tag = "shorts-" + datetime.now(KST).strftime("%Y-%m")
        if _gh("release", "view", tag, check=False).returncode != 0:
            _gh("release", "create", tag, "--title", f"쇼츠 보관함 {tag[7:]}",
                "--notes", "자동 보관된 쇼츠 원본 (롱폼 제작용)", "--latest=false")
        name = f"{datetime.now(KST):%Y%m%d_%H%M}_{ep}.mp4"
        tmp = Path(video).with_name(name)
        shutil.copy2(video, tmp)
        _gh("release", "upload", tag, str(tmp), "--clobber")
        state.setdefault("archive", []).append({
            "tag": tag, "asset": name, "ep": ep, "theme": theme,
            "title": re.sub(r"\s*#shorts", "", meta.get("title", ""), flags=re.I).strip(),
            "seconds": round(compose.probe(video)[0]), "used": False})
        print(f"  쇼츠 보관 완료: {tag}/{name}")
    except Exception as e:
        print(f"  (쇼츠 보관 실패 — 롱폼에서만 빠짐: {getattr(e, 'stderr', '') or e})")


# ================================================================ 화면 요소
def _font(size):
    return compose.load_font(size)


def _wrap(text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if font.getlength(t) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def side_panel(path, idx, total, title):
    """왼쪽: '3 / 10' + 이야기 제목 (흰 반투명 카드)"""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x0, y0, w = 70, 330, 520
    f_no, f_t = _font(46), _font(52)
    lines = _wrap(title, f_t, w - 60)[:4]
    h = 150 + 72 * len(lines)
    d.rounded_rectangle([x0, y0, x0 + w, y0 + h], 36, fill=(255, 255, 255, 235))
    d.rounded_rectangle([x0 + 30, y0 + 30, x0 + 30 + f_no.getlength(f"{idx} / {total}") + 40, y0 + 100], 24,
                        fill=ACCENT + (255,))
    d.text((x0 + 50, y0 + 38), f"{idx} / {total}", font=f_no, fill=(255, 255, 255))
    for i, ln in enumerate(lines):
        d.text((x0 + 30, y0 + 125 + i * 72), ln, font=f_t, fill=INK)
    img.save(path)


def title_card(path, big, small):
    img = Image.new("RGB", (W, H), (250, 250, 252))
    d = ImageDraw.Draw(img)
    fb, fs = _font(104), _font(52)
    lines = _wrap(big, fb, W - 300)[:3]
    y = H / 2 - (len(lines) * 130) / 2 - 40
    for ln in lines:
        d.text(((W - fb.getlength(ln)) / 2, y), ln, font=fb, fill=INK)
        y += 130
    d.rounded_rectangle([W / 2 - 60, y + 20, W / 2 + 60, y + 30], 5, fill=ACCENT)
    d.text(((W - fs.getlength(small)) / 2, y + 60), small, font=fs, fill=(110, 110, 118))
    img.save(path)


def thumbnail(path, frame_png, text):
    """1280x720 썸네일: 대표 장면 + 큰 글씨"""
    bg = Image.open(frame_png).convert("RGB")
    k = max(1280 / bg.width, 720 / bg.height)
    bg = bg.resize((int(bg.width * k) + 1, int(bg.height * k) + 1))
    bg = bg.crop(((bg.width - 1280) // 2, (bg.height - 720) // 2, (bg.width - 1280) // 2 + 1280,
                  (bg.height - 720) // 2 + 720))
    shade = Image.new("RGBA", (1280, 720), (0, 0, 0, 0))
    ImageDraw.Draw(shade).rectangle([0, 360, 1280, 720], fill=(0, 0, 0, 150))
    img = Image.alpha_composite(bg.convert("RGBA"), shade.filter(ImageFilter.GaussianBlur(40)))
    d = ImageDraw.Draw(img)
    f = _font(92)
    lines = _wrap(text, f, 1180)[:2]
    y = 690 - 110 * len(lines)
    for i, ln in enumerate(lines):
        d.text((50, y + i * 110), ln, font=f, fill=(255, 228, 0) if i == len(lines) - 1 else (255, 255, 255),
               stroke_width=8, stroke_fill=(0, 0, 0))
    img.convert("RGB").save(path, quality=92)


# ================================================================ 합성
def _segment(src, out, panel_png):
    """세로 쇼츠 1편 → 1920x1080 (가운데 원본, 양옆 흐림, 왼쪽 제목 카드)"""
    fc = ("[0:v]split[a][b];"
          "[a]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,boxblur=40:3,"
          "eq=brightness=-0.18[bg];"
          "[b]scale=-2:1080:flags=lanczos[fg];"
          "[bg][fg]overlay=(W-w)/2+250:0[v0];[v0][1:v]overlay=0:0,fps=30,format=yuv420p[v]")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-i", str(panel_png),
                    "-filter_complex", fc, "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "20", "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2", str(out)], check=True)


def _still(png, out, seconds):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", "30", "-i", str(png),
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", f"{seconds}", "-vf",
                    "scale=1920:1080,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    "-c:a", "aac", "-b:a", "160k", "-shortest", str(out)], check=True)


def ts(sec):
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60:02d}:{sec % 60:02d}"


def build_long(items, out, big_title, work):
    """items: [(쇼츠 파일, 제목)] → 롱폼 파일. 반환: (총 길이, 챕터 [(초, 제목)], 썸네일용 프레임 png)"""
    work = Path(work)
    parts, chapters, t = [], [], 0.0
    intro = work / "intro.png"
    title_card(intro, big_title, f"오늘의 이야기 {len(items)}가지")
    _still(intro, work / "p_intro.mp4", 3)
    parts.append(work / "p_intro.mp4")
    chapters.append((0, "오프닝"))
    t += 3
    for i, (src, title) in enumerate(items, 1):
        panel = work / f"panel_{i}.png"
        side_panel(panel, i, len(items), title)
        seg = work / f"p_{i:02d}.mp4"
        _segment(src, seg, panel)
        chapters.append((t, f"{i}. {title}"))
        t += compose.probe(seg)[0]
        parts.append(seg)
    outro = work / "outro.png"
    title_card(outro, "다음 이야기도 궁금하다면", "구독하고 매일 새로운 해외 실화를 만나 보세요")
    _still(outro, work / "p_outro.mp4", 4)
    parts.append(work / "p_outro.mp4")
    lst = work / "list.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "160k",
                    "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-movflags", "+faststart", str(out)], check=True)
    frame = work / "thumb_src.png"
    first = items[0][0]
    dur = compose.probe(first)[0]
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{dur * 0.4:.2f}", "-i", str(first),
                    "-frames:v", "1", str(frame)], check=True)
    return compose.probe(out)[0], chapters, frame


# ================================================================ 제목·설명
LONG_PROMPT = """한국 유튜브 '해외 실화 몰아보기' 롱폼 영상의 제목과 설명을 써 줘.
이번 영상에 들어간 이야기 제목들:
{titles}

- title: 60자 이내, 한국어 문법에 맞게, 클릭하고 싶게 (예: "믿기 힘든 해외 실화 10가지 몰아보기"). '#shorts' 금지.
- thumb: 썸네일에 크게 쓸 문구 2줄 분량 (14자 이내), 가장 강한 이야기 하나를 짧게.
- intro: 영상 첫 화면 큰 제목 (16자 이내).
- description: 3~4문장 소개 + 마지막에 댓글 유도 질문.
- hashtags: 5개 (#포함, 한국어).
JSON만: {{"title":"","thumb":"","intro":"","description":"","hashtags":[]}}
"""


def write_texts(env, cfg, titles):
    import finder
    from google.genai import types
    default = {"title": f"믿기 힘든 해외 실화 {len(titles)}가지 몰아보기", "thumb": titles[0][:14] if titles else "",
               "intro": "해외 실화 몰아보기", "description": "이번 주 화제가 된 해외 실화를 한 번에 모았어요.\n"
               "가장 놀라웠던 이야기는 몇 번이었나요?", "hashtags": ["#실화", "#해외사연", "#몰아보기", "#사연", "#해외이슈"]}
    try:
        client = finder.gemini_client(env)
        models = finder.model_order(client, cfg.get("gemini_model", "gemini-flash-latest"))
        conf = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.6)
        res = finder.gemini_json(client, models, [LONG_PROMPT.format(titles="\n".join(f"- {t}" for t in titles))],
                                 conf, "롱폼 제목 쓰는 중")
        for k in default:
            if res.get(k):
                default[k] = res[k]
    except Exception as e:
        print(f"   (롱폼 제목 AI 실패 → 기본 문구: {e})")
    default["title"] = re.sub(r"\s*#shorts", "", str(default["title"]), flags=re.I).strip()[:95]
    return default


def description(texts, chapters, sources):
    lines = [str(texts["description"]).strip(), "", "📌 목차"]
    lines += [f"{ts(s)} {t}" for s, t in chapters]
    lines += ["", "※ 실제 해외 기사를 바탕으로 재구성한 이야기예요. 각 이야기의 출처는 쇼츠 설명란에 있어요."]
    if sources:
        lines += ["자료화면: " + ", ".join(sources)]
    lines += ["", " ".join(texts.get("hashtags", []))]
    return "\n".join(lines)


# ================================================================ 실행
def publish_and_report(env, cfg, state, tg, out, thumb, texts, chapters, total, publish_at=None):
    import finder
    import uploader
    meta = {"title": texts["title"], "description": description(texts, chapters, ["Pixabay", "Pexels", "Openverse"]),
            "tags": texts.get("hashtags", []), "long": True, "publish_at": publish_at}
    vid = None
    try:
        vid = uploader.upload(out, meta)
        try:
            uploader.service().thumbnails().set(
                videoId=vid, media_body=__import__("googleapiclient.http", fromlist=["x"]).MediaFileUpload(
                    str(thumb), mimetype="image/jpeg")).execute()
        except Exception as e:
            print(f"   (썸네일 등록 실패 — Studio 에서 직접: {str(e)[:120]})")
        when = ""
        if uploader.LAST.get("scheduled"):
            when = f" — {uploader.LAST['scheduled'].astimezone(KST):%m/%d %H:%M} 자동 공개 예약"
        tg.send(f"🎬 롱폼 업로드 완료{when}\n{texts['title']} ({total / 60:.0f}분, 이야기 {len(chapters) - 1}개)\n"
                f"https://studio.youtube.com/video/{vid}/edit")
    except Exception as e:
        tg.send(f"⚠️ 롱폼 업로드 실패: {str(e)[:300]}")
    try:
        with open(thumb, "rb") as f:
            tg.call("sendPhoto", files={"photo": f}, chat_id=tg.state["chat_id"], caption=f"롱폼 썸네일: {texts['thumb']}")
    except Exception:
        pass
    state.setdefault("longs", []).append({"date": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
                                          "title": texts["title"], "youtube": vid, "minutes": round(total / 60, 1)})
    finder.save_state(state)
    return vid


def make_weekly_long(env, cfg, state, tg, publish_at=None):
    """보관함에서 아직 안 쓴 사연 쇼츠를 모아 롱폼 1편"""
    pool = [a for a in state.get("archive", []) if not a.get("used") and a.get("theme") == "사연"]
    min_n, max_n = int(cfg.get("long_min_shorts", 6)), int(cfg.get("long_max_shorts", 12))
    if len(pool) < min_n:
        tg.send(f"📼 롱폼: 모인 쇼츠가 {len(pool)}개라 다음 주에 같이 만들게요 (최소 {min_n}개)")
        return None
    pick = pool[-max_n:]
    work = Path(tempfile.mkdtemp(prefix="long_"))
    try:
        tg.send(f"🎬 이번 주 쇼츠 {len(pick)}개로 롱폼 만드는 중... (10~20분)")
        items = []
        for a in pick:
            _gh("release", "download", a["tag"], "--pattern", a["asset"], "--dir", str(work), "--clobber")
            items.append((work / a["asset"], a["title"] or a["ep"]))
        texts = write_texts(env, cfg, [t for _, t in items])
        out = work / "long.mp4"
        total, chapters, frame = build_long(items, out, texts["intro"], work)
        thumb = work / "thumb.jpg"
        thumbnail(thumb, frame, texts["thumb"])
        vid = publish_and_report(env, cfg, state, tg, out, thumb, texts, chapters, total, publish_at)
        if vid:
            for a in pick:
                a["used"] = True
            import finder
            finder.save_state(state)
        return vid
    finally:
        shutil.rmtree(work, ignore_errors=True)


def make_long_from_files(env, cfg, state, tg, files):
    """PC 용: [(파일, 제목)] 로 롱폼 1편"""
    work = Path(tempfile.mkdtemp(prefix="long_"))
    try:
        texts = write_texts(env, cfg, [t for _, t in files])
        out = work / "long.mp4"
        total, chapters, frame = build_long(files, out, texts["intro"], work)
        thumb = work / "thumb.jpg"
        thumbnail(thumb, frame, texts["thumb"])
        keep = Path(files[0][0]).parent / f"long_{datetime.now(KST):%Y%m%d_%H%M}.mp4"
        shutil.copy2(out, keep)
        shutil.copy2(thumb, keep.with_suffix(".jpg"))
        print(f"롱폼 저장: {keep}")
        return publish_and_report(env, cfg, state, tg, keep, keep.with_suffix(".jpg"), texts, chapters, total)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def pc_main():
    """long_pc.bat: PC output 폴더의 예전 사연 쇼츠로 롱폼"""
    import finder
    env = finder.load_env()
    cfg = finder.load_json(finder.CONFIG_PATH, {})
    state = finder.load_json(finder.STATE_PATH, {})
    tg = finder.Telegram(env.get("TELEGRAM_TOKEN"), state)
    files = []
    for ep, info in state.get("episodes", {}).items():
        if info.get("theme") != "사연" or info.get("in_long"):
            continue
        p = finder.OUTPUT_DIR / f"{ep}_final.mp4"
        mp = finder.OUTPUT_DIR / f"{ep}_meta.json"
        if p.exists():
            title = ep
            if mp.exists():
                title = re.sub(r"\s*#shorts", "", json.loads(mp.read_text(encoding="utf-8")).get("title", ep),
                               flags=re.I).strip()
            files.append((p, title, ep))
    if len(files) < 3:
        print(f"PC 에 있는 사연 쇼츠가 {len(files)}개뿐이라 롱폼을 만들기엔 적어요 (3개 이상 필요).")
        return
    print(f"사연 쇼츠 {len(files)}개로 롱폼을 만들어요...")
    vid = make_long_from_files(env, cfg, state, tg, [(p, t) for p, t, _ in files[-12:]])
    if vid:
        for _, _, ep in files[-12:]:
            state["episodes"][ep]["in_long"] = True
        finder.save_state(state)


if __name__ == "__main__":
    pc_main()
