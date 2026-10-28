# -*- coding: utf-8 -*-
"""
사연 영상 자료화면 고르기 — 여러 무료 사이트에서 후보를 모으고, AI 가 썸네일을 직접 보고 장면에 가장 맞는 걸 고름

  소스 (모두 무료·상업적 이용 가능):
   - Pixabay 영상/사진 (PIXABAY_KEY)
   - Pexels 영상/사진 (PEXELS_KEY 가 있을 때만)
   - Openverse 사진 (키 필요 없음 · 위키미디어·플리커 등의 CC0/CC BY/CC BY-SA 사진 → 출처 표기 자동)
   - AI 이미지 (CF_ACCOUNT_ID + CF_API_TOKEN 이 있을 때만 · Cloudflare Workers AI 무료 한도 하루 약 200장)
     → 자료화면이 장면과 안 맞을 때 AI 가 그린 장면을 후보로. 쓰면 유튜브에 'AI 생성 콘텐츠' 표시를 자동으로 켬
"""
import re
import time
from pathlib import Path

import requests

UA = {"User-Agent": "hookshorts/1.0 (+https://github.com/haha123345/hookshorts)"}
OPENVERSE_LICENSES = "cc0,pdm,by,by-sa"            # 상업적 이용 + 편집 가능한 라이선스만


def _get(url, headers=None, **params):
    r = requests.get(url, params=params, headers=headers or UA, timeout=30)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------- 후보 모으기
def pixabay_candidates(key, q, min_dur, used):
    out = []
    try:
        data = _get("https://pixabay.com/api/videos/", key=key, q=q, safesearch="true", per_page=10,
                    video_type="film")
        for h in data.get("hits", []):
            vid = f"pxv{h['id']}"
            if vid in used or h.get("duration", 0) < max(4.0, min_dur):
                continue
            vids = h.get("videos", {})
            best = next((vids[k] for k in ("medium", "large", "small")
                         if vids.get(k, {}).get("url") and vids[k].get("width", 0) >= 1280), None)
            if not best:
                continue
            thumb = best.get("thumbnail") or (vids.get("tiny") or {}).get("thumbnail") or ""
            out.append({"id": vid, "kind": "video", "src": "Pixabay", "url": best["url"], "thumb": thumb,
                        "dur": float(h["duration"]), "credit": f"Pixabay · {h.get('user', '')}"})
            if len(out) >= 3:
                break
    except Exception as e:
        print(f"   (Pixabay 영상 검색 실패 '{q}': {e})")
    try:
        data = _get("https://pixabay.com/api/", key=key, q=q, safesearch="true", per_page=6,
                    image_type="photo", min_width=1280)
        for h in data.get("hits", [])[:2]:
            pid = f"pxp{h['id']}"
            if pid in used or not h.get("largeImageURL"):
                continue
            out.append({"id": pid, "kind": "photo", "src": "Pixabay", "url": h["largeImageURL"],
                        "thumb": h.get("webformatURL") or h.get("previewURL"),
                        "credit": f"Pixabay · {h.get('user', '')}"})
    except Exception as e:
        print(f"   (Pixabay 사진 검색 실패 '{q}': {e})")
    return out


def pexels_candidates(key, q, min_dur, used):
    out, hdr = [], dict(UA, Authorization=key)
    try:
        data = _get("https://api.pexels.com/videos/search", headers=hdr, query=q, per_page=6,
                    orientation="landscape")
        for v in data.get("videos", []):
            vid = f"pev{v['id']}"
            if vid in used or v.get("duration", 0) < max(4.0, min_dur):
                continue
            files = sorted([f for f in v.get("video_files", []) if f.get("width", 0) >= 1280
                            and f.get("file_type") == "video/mp4"], key=lambda f: f.get("width", 0))
            if not files:
                continue
            best = next((f for f in files if f.get("width", 0) >= 1920), files[-1])
            out.append({"id": vid, "kind": "video", "src": "Pexels", "url": best["link"], "thumb": v.get("image"),
                        "dur": float(v["duration"]), "credit": f"Pexels · {(v.get('user') or {}).get('name', '')}"})
            if len(out) >= 2:
                break
        data = _get("https://api.pexels.com/v1/search", headers=hdr, query=q, per_page=3,
                    orientation="landscape")
        for p in data.get("photos", [])[:1]:
            pid = f"pep{p['id']}"
            if pid not in used:
                out.append({"id": pid, "kind": "photo", "src": "Pexels", "url": p["src"]["large2x"],
                            "thumb": p["src"]["medium"], "credit": f"Pexels · {p.get('photographer', '')}"})
    except Exception as e:
        print(f"   (Pexels 검색 실패 '{q}': {e})")
    return out


def openverse_candidates(q, used):
    """특정 대상(로봇청소기, 이구아나, 법정 등)의 실제 사진 — 저작자 표기 필요한 것도 있어서 출처를 같이 저장"""
    out = []
    try:
        data = _get("https://api.openverse.org/v1/images/", q=q, license=OPENVERSE_LICENSES, page_size=8,
                    mature="false", aspect_ratio="wide,square")
        for r in data.get("results", []):
            oid = f"ov{r['id']}"
            if oid in used or (r.get("width") or 0) < 1000 or not r.get("url"):
                continue
            lic = (r.get("license") or "").upper()
            ver = r.get("license_version") or ""
            name = "CC0" if lic == "CC0" else ("Public Domain" if lic == "PDM" else f"CC {lic} {ver}".strip())
            credit = (f"\"{(r.get('title') or 'photo')[:60]}\" by {(r.get('creator') or 'unknown')[:40]}, {name}"
                      f" ({r.get('foreign_landing_url') or r['url']})")
            out.append({"id": oid, "kind": "photo", "src": "Openverse", "url": r["url"],
                        "thumb": r.get("thumbnail") or r["url"], "credit": credit})
            if len(out) >= 2:
                break
    except Exception as e:
        print(f"   (Openverse 검색 실패 '{q}': {e})")
    return out


def ai_image_candidate(env, scene, work_dir):
    """Cloudflare Workers AI (flux-1-schnell, 무료 한도) 로 장면 이미지 1장"""
    prompt = scene.get("image_prompt") or ""
    if not prompt or not (env.get("CF_ACCOUNT_ID") and env.get("CF_API_TOKEN")):
        return []
    import base64
    try:
        r = requests.post(
            f"https://api.cloudflare.com/client/v4/accounts/{env['CF_ACCOUNT_ID']}/ai/run/@cf/black-forest-labs/flux-1-schnell",
            headers={"Authorization": f"Bearer {env['CF_API_TOKEN']}"}, timeout=90,
            json={"prompt": prompt + ", photorealistic, cinematic lighting, natural colors, no text, no watermark",
                  "steps": 6})
        r.raise_for_status()
        img = base64.b64decode(r.json()["result"]["image"])
        n = len(list(Path(work_dir).glob("ai_*.jpg")))
        path = Path(work_dir) / f"ai_{n:02d}.jpg"
        path.write_bytes(img)
        return [{"id": f"ai{n}", "kind": "photo", "src": "AI", "url": path.as_uri(), "local": str(path),
                 "thumb": path.as_uri(), "credit": "AI"}]
    except Exception as e:
        print(f"   (AI 이미지 실패: {str(e)[:100]})")
        return []


def gather(env, scene, min_dur, used, work_dir=None):
    cands = []
    for q in [scene.get("visual"), scene.get("alt")]:
        if not q:
            continue
        if env.get("PIXABAY_KEY"):
            cands += pixabay_candidates(env["PIXABAY_KEY"], q, min_dur, used)
        if env.get("PEXELS_KEY"):
            cands += pexels_candidates(env["PEXELS_KEY"], q, min_dur, used)
        time.sleep(0.2)
    subject = scene.get("subject") or scene.get("visual")
    if subject:
        cands += openverse_candidates(subject, used)
    seen, uniq = set(), []
    for c in cands:
        if c["id"] not in seen:
            seen.add(c["id"])
            uniq.append(c)
    uniq = uniq[:7]
    if work_dir:
        uniq += ai_image_candidate(env, scene, work_dir)
    return uniq


# ---------------------------------------------------------------- AI 가 썸네일 보고 고르기
CHOOSE_PROMPT = """너는 쇼츠 편집자야. 아래 실화 사연의 장면마다 자료화면 후보 썸네일이 있어.
각 장면 나레이션 내용과 '가장 직접적으로 맞는' 후보 하나를 골라.

규칙:
- 나레이션에 나오는 대상(사람·동물·물건·장소·행동)이 실제로 보이는 후보가 1순위.
- 비슷하게 맞으면 사진보다 움직이는 영상(video)을 골라.
- 'AI가 그린 그림' 후보는 실제 자료화면 중에 맞는 게 없을 때만 골라 (손·얼굴이 이상하게 그려졌으면 고르지 마).
- 내용과 반대되거나 엉뚱한 분위기(예: 슬픈 장면에 웃는 사람, 한국 이야기가 아닌데 한글 간판 등)는 고르지 마.
- 글자·워터마크·로고가 크게 보이는 후보는 피해.
- 연속된 장면에서 거의 같은 화면이 반복되지 않게.
- 맞는 게 하나도 없으면 "none".

JSON만: {{"picks": [{{"scene": 1, "pick": "후보 번호 또는 none"}}]}}

사연 제목: {title}
"""


def choose(env, cfg, plan, all_cands):
    """장면마다 고른 후보 인덱스 목록 (실패하면 기본 순서: 영상 우선)"""
    default = []
    for cands in all_cands:
        vids = [i for i, c in enumerate(cands) if c["kind"] == "video"]
        default.append(vids[0] if vids else (0 if cands else None))
    try:
        import finder
        from google.genai import types
        client = finder.gemini_client(env)
        models = finder.model_order(client, cfg.get("gemini_model", "gemini-flash-latest"))
        parts = [CHOOSE_PROMPT.format(title=plan.get("title", ""))]
        n_img = 0
        for si, (sc, cands) in enumerate(zip(plan["scenes"], all_cands), 1):
            parts.append(f"\n[장면 {si}] {re.sub(r'[{}]', '', str(sc['text']))}")
            for ci, c in enumerate(cands, 1):
                try:
                    img = (Path(c["local"]).read_bytes() if c.get("local")
                           else requests.get(c["thumb"], headers=UA, timeout=20).content)
                    if len(img) < 500:
                        continue
                    kind = "AI가 그린 그림" if c["src"] == "AI" else ("영상" if c["kind"] == "video" else "사진")
                    parts.append(f"후보 {ci} ({kind}, {c['src']}):")
                    parts.append(types.Part.from_bytes(data=img, mime_type="image/jpeg"))
                    n_img += 1
                except Exception:
                    continue
        if not n_img:
            return default
        conf = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.2,
                                           media_resolution=types.MediaResolution.MEDIA_RESOLUTION_LOW)
        res = finder.gemini_json(client, models, parts, conf, f"자료화면 {n_img}개 보고 고르는 중")
        picks = list(default)
        for p in res.get("picks", []):
            try:
                si = int(p.get("scene")) - 1
                v = str(p.get("pick", "")).strip().lower()
                if 0 <= si < len(picks):
                    picks[si] = None if v == "none" else int(re.findall(r"\d+", v)[0]) - 1
            except (TypeError, ValueError, IndexError):
                continue
        return [pk if pk is not None and 0 <= pk < len(all_cands[i]) else None for i, pk in enumerate(picks)]
    except Exception as e:
        print(f"   (AI 자료화면 고르기 실패 → 기본 순서: {str(e)[:120]})")
        return default


# ---------------------------------------------------------------- 받기
def download(url, out):
    with requests.get(url, stream=True, timeout=120, headers=UA) as r:
        r.raise_for_status()
        with open(out, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    return out


def materialize(c, seconds, story_dir, photo_to_clip):
    """고른 후보를 실제 파일로. 반환: (경로, 길이초, 출처문구)"""
    safe = re.sub(r"[^\w]", "", c["id"])[:40]
    if c["kind"] == "video":
        out = Path(story_dir) / f"{safe}.mp4"
        if not out.exists():
            download(c["url"], out)
        return out, c["dur"], c["credit"]
    img = Path(story_dir) / f"{safe}.jpg"
    if c.get("local"):
        img = Path(c["local"])
    elif not img.exists():
        download(c["url"], img)
    out = Path(story_dir) / f"{safe}_{int(seconds * 10)}.mp4"
    photo_to_clip(img, out, seconds)
    return out, seconds, c["credit"]


def pick_all(env, cfg, plan, story_dir, photo_to_clip, fallback, est_fn):
    """장면마다 자료화면을 골라 받기. 반환: [(경로, 시작, 끝)], [출처문구]"""
    used = set()
    all_cands = []
    for sc in plan["scenes"]:
        est = est_fn(sc)
        cands = gather(env, sc, est + 0.8, used, story_dir)
        all_cands.append(cands)
        print(f"   장면 후보 {len(cands)}개: {sc.get('visual')}")
    picks = choose(env, cfg, plan, all_cands)
    visuals, credits, taken = [], [], set()
    for sc, cands, pk in zip(plan["scenes"], all_cands, picks):
        est = est_fn(sc)
        order = ([pk] if pk is not None else []) + [i for i in range(len(cands)) if i != pk]
        got = None
        for i in order:
            c = cands[i]
            if c["id"] in taken:
                continue
            try:
                got = materialize(c, est + 0.8, story_dir, photo_to_clip)
                taken.add(c["id"])
                break
            except Exception as e:
                print(f"   (자료화면 받기 실패 {c['src']}: {e})")
        if got is None:                                   # 후보가 다 안 되면 예전 방식(일반 장면)
            got = fallback(sc, est + 0.8)
        path, dur, credit = got
        a = 0.5 if dur > est + 1.5 else 0.0
        b = min(dur, a + est + 0.6)
        visuals.append((path, a, b))
        credits.append(credit)
    return visuals, credits
