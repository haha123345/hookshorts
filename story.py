# -*- coding: utf-8 -*-
"""
사연 모드: 실제 있었던 기이한·감동 실화를 내 말로 풀어주는 쇼츠

  1) 소재: 해외 실화 뉴스 RSS(UPI 기이한 뉴스, Good News Network) 후보 중 Gemini가 고르기
          또는 텔레그램으로 받은 주제/기사 링크
  2) 대본: 기사 본문에 있는 사실만으로 장면 8~12개 (후킹 + 나레이션 + 자료화면 검색어)
  3) 화면: 장면마다 Pixabay 무료 영상 (없으면 무료 사진 + 천천히 확대/이동)
  4) 합성: compose.py (자막·나레이션 싱크, 전환, 배경음악) → 기존과 같은 방식으로 업로드

텔레그램: '사연'  /  '사연 고양이 구조'  /  '사연 https://기사링크'
"""
import html
import json
import random
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import requests

import compose

BASE = Path(__file__).resolve().parent
INPUT_DIR = BASE / "input"
STORY_DIR = INPUT_DIR / "story"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"}
PAGE_HEADERS = dict(UA, **{
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ko;q=0.8", "Cache-Control": "no-cache", "Upgrade-Insecure-Requests": "1"})
DEFAULT_FEEDS = ["https://feeds.feedburner.com/OddityCentral",          # 기이한 실화 (RSS 에 본문 통째로)
                 "https://www.goodnewsnetwork.org/feed/",               # 감동 실화
                 "https://rss.upi.com/news/odd_news.rss"]               # 기이한 뉴스 (사이트가 자주 막음)
SKIP_TITLE = re.compile(r"(?i)good news in history|^watch:|quiz|crossword|horoscope|deal|sale\b|podcast")


# ================================================================ 소재 찾기
def clean_text(s):
    s = re.sub(r"(?is)<(script|style|noscript|header|footer|nav|aside|form)[^>]*>.*?</\1>", " ", s or "")
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


MEDIA = "{http://search.yahoo.com/mrss/}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}encoded"


def feed_items(url):
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if not (title and link):
            continue
        desc = it.findtext("description") or ""
        content = it.findtext(CONTENT) or ""
        img = ""                                            # RSS 에 들어 있는 기사 사진 (사이트가 막혀도 쓸 수 있게)
        for tag in (MEDIA + "content", MEDIA + "thumbnail", "enclosure"):
            el = it.find(tag)
            if el is not None and el.get("url") and not el.get("type", "image").startswith(("video", "audio")):
                img = el.get("url")
                break
        if not img:
            m = re.search(r'(?is)<img[^>]+src=["\']([^"\']+)', desc + content)
            img = html.unescape(m.group(1)) if m else ""
        out.append({"title": clean_text(title), "link": link,
                    "date": (it.findtext("pubDate") or "")[:16],
                    "summary": clean_text(desc)[:300],
                    "content": clean_text(content)[:9000], "image": img})
    return out


_HTML = {}


def fetch_page(url):
    """기사 페이지 받기 (막히면 2번까지 다시 시도)"""
    last = None
    for wait in (0, 3, 8):
        time.sleep(wait)
        try:
            r = requests.get(url, headers=PAGE_HEADERS, timeout=30)
            if r.status_code in (403, 429, 503):
                last = RuntimeError(f"{r.status_code} 사이트가 접속을 막음")
                continue
            r.raise_for_status()
            return r.text
        except requests.RequestException as e:
            last = e
    raise last


def article_text(url):
    raw = fetch_page(url)
    _HTML[url] = raw
    title = clean_text((re.search(r"(?is)<title[^>]*>(.*?)</title>", raw) or [None, ""])[1])
    body = " ".join(clean_text(p) for p in re.findall(r"(?is)<p[^>]*>(.*?)</p>", raw))
    if len(body) < 400:
        body = clean_text(raw)
    return title, body[:9000]


def article_image(url, fallback=""):
    """기사 대표 사진(og:image, 없으면 RSS 사진) 받기. 반환: (사진 경로, 언론사 이름) 또는 (None, None)"""
    from urllib.parse import urljoin, urlparse
    raw = _HTML.get(url)
    if raw is None:
        try:
            raw = _HTML[url] = fetch_page(url)
        except Exception:
            raw = ""

    def meta(*names):
        for n in names:
            m = (re.search(rf'(?is)<meta[^>]+(?:property|name)=["\']{n}["\'][^>]*content=["\']([^"\']+)', raw)
                 or re.search(rf'(?is)<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\']{n}["\']', raw))
            if m:
                return html.unescape(m.group(1)).strip()
        return ""
    src = meta("og:image", "og:image:url", "twitter:image", "twitter:image:src") or fallback
    site = meta("og:site_name") or urlparse(url).netloc.replace("www.", "")
    if not src or re.search(r"(?i)logo|default|placeholder|avatar|icon|sprite", src):
        return None, None
    STORY_DIR.mkdir(parents=True, exist_ok=True)
    out = STORY_DIR / f"real_{abs(hash(src)) % 10**8}.jpg"
    _download(urljoin(url, src), out)
    from PIL import Image
    try:
        with Image.open(out) as im:
            w, h = im.size
    except Exception:
        return None, None
    if w < 480 or not 0.5 <= w / h <= 2.6:
        return None, None
    return out, site[:30]


def real_photo_clip(img, out, seconds, box=1080):
    """기사 사진 → 정사각형(흐린 배경 + 사진 전체) 위에서 천천히 확대되는 영상"""
    from PIL import Image, ImageFilter, ImageEnhance
    with Image.open(img) as im:
        im = im.convert("RGB")
        bg = im.copy()
        k = max(box / bg.width, box / bg.height)
        bg = bg.resize((round(bg.width * k) + 2, round(bg.height * k) + 2))
        bg = bg.crop(((bg.width - box) // 2, (bg.height - box) // 2,
                      (bg.width - box) // 2 + box, (bg.height - box) // 2 + box))
        bg = ImageEnhance.Brightness(bg.filter(ImageFilter.GaussianBlur(28))).enhance(0.55)
        k = min(box * 0.94 / im.width, box * 0.94 / im.height)
        fg = im.resize((round(im.width * k), round(im.height * k)), Image.LANCZOS)
        bg.paste(fg, ((box - fg.width) // 2, (box - fg.height) // 2))
        still = Path(out).with_suffix(".png")
        bg.save(still)
    frames = int(seconds * 30)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(still), "-vf",
                    f"scale=2160:-2:flags=lanczos,zoompan=z='min(zoom+0.0006,1.08)':x='iw/2-(iw/zoom/2)':"
                    f"y='ih/2-(ih/zoom/2)':d={frames}:s={box}x{box}:fps=30,format=yuv420p",
                    "-t", f"{seconds:.2f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(out)],
                   check=True, timeout=600)
    return out


PICK_PROMPT = """너는 한국 쇼츠 '실화 사연' 채널 작가야. 아래 해외 뉴스 후보 중 한국 시청자가 끝까지 볼 만한
이야기를 골라. ([본문] 표시가 있는 후보는 내용이 확실히 확보된 것이라 비슷하면 우선.) 기준: 분명한 주인공(사람·동물), 위기나 기이한 상황, 반전·감동·놀라운 결말이 있는 것.
제외: 사람이 죽거나 크게 다친 사건, 아이가 위험에 처한 사건, 범죄 피해자 이야기, 정치·종교, 복권 당첨, 광고성 기사,
'오늘의 역사' 같은 모음 기사.
{topic_rule}
후보:
{items}

좋은 순서대로 최대 4개. JSON만 출력: {{"indexes": [번호, 번호, ...], "reason": "1순위 이유 한 줄"}}
(적당한 게 없으면 "indexes": [])
"""

SEARCH_PROMPT = """구글 검색으로 '{topic}' 와 관련된 실제 해외(또는 국내) 실화 뉴스 기사 하나를 찾아 줘.
최근 1년 이내, 믿을 만한 언론사 기사, 사람이 죽거나 크게 다친 사건·범죄 피해 이야기는 제외.
마지막 줄에 JSON 한 줄만: {{"title": "기사 제목", "url": "기사 주소"}}
"""


READ_PROMPT = """아래 주소의 기사를 읽고, 기사에 나온 사실(누가·어디서·언제·무슨 일·숫자·결과)을
빠짐없이 영어 문단으로 정리해 줘. 기사에 없는 내용은 절대 추가하지 마. 기사를 못 읽었으면 NO_ACCESS 한 단어만.
{url}"""


def read_with_gemini(client, models, url):
    """사이트가 프로그램 접속을 막을 때: Gemini 가 대신 기사를 읽어서 사실만 정리"""
    from google.genai import types
    try:
        conf = types.GenerateContentConfig(tools=[types.Tool(url_context=types.UrlContext())], temperature=0.1)
    except Exception:
        return ""
    for model in models[:4]:
        try:
            resp = client.models.generate_content(model=model, contents=READ_PROMPT.format(url=url), config=conf)
            text = (resp.text or "").strip()
            if len(text) > 300 and "NO_ACCESS" not in text:
                return text
            return ""                                           # 읽었는데 내용이 없음 → 다른 모델도 같음
        except Exception as e:
            print(f"   (Gemini 기사 읽기 실패: {str(e)[:90]})")
            time.sleep(4)
    return ""


def get_article(client, models, url, title="", rss_text=""):
    """기사 본문 얻기: RSS 본문 → 직접 → Gemini 대신 읽기. 반환: (제목, 본문) — 실패 시 본문 ''"""
    if len(rss_text) > 800:                                         # RSS 에 본문이 통째로 있으면 사이트 접속 안 함
        return title, rss_text
    try:
        t, text = article_text(url)
        if len(text) > 300:
            return title or t, text
    except Exception as e:
        print(f"   (기사 페이지 막힘: {str(e)[:100]})")
    if len(rss_text) > 400:
        return title, rss_text
    print("   Gemini 가 대신 기사 읽는 중...")
    return title, read_with_gemini(client, models, url)


def pick_story(env, cfg, state, topic=None):
    """반환: {title, url, text, image} 또는 None"""
    import finder
    used = set(state.get("stories_used", []))
    client = finder.gemini_client(env)
    models = finder.model_order(client, cfg.get("gemini_model", "gemini-flash-latest"))
    if topic and re.fullmatch(r"[\d\s개편]*", topic):              # '사연1', '사연 1개' → 주제 없음
        topic = None

    if topic and re.match(r"https?://", topic):                    # 기사 링크를 직접 보낸 경우
        title, text = get_article(client, models, topic)
        if len(text) < 300:
            raise RuntimeError("그 기사 사이트가 접속을 막아서 내용을 못 읽었어요. 다른 언론사 링크로 보내 주세요.")
        return {"title": title, "url": topic, "text": text, "image": ""}

    items = []
    for f in cfg.get("story_feeds", DEFAULT_FEEDS):
        try:
            items += feed_items(f)
        except Exception as e:
            print(f"   (RSS 실패 {f}: {e})")
    items = [it for it in items if it["link"] not in used and not SKIP_TITLE.search(it["title"])][:60]

    if items:
        from google.genai import types
        listing = "\n".join(f"{i}. {'[본문] ' if len(it.get('content', '')) > 800 else ''}{it['title']} - "
                            f"{it['summary'][:120]}" for i, it in enumerate(items, 1))
        rule = f"[시청자가 요청한 주제: '{topic}'] 이 주제와 맞는 후보만 골라. 없으면 빈 목록." if topic else ""
        conf = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.4)
        try:
            res = finder.gemini_json(client, models, [PICK_PROMPT.format(items=listing, topic_rule=rule)],
                                     conf, "사연 소재 고르는 중")
        except Exception as e:                                     # AI 가 다 바쁘면: 본문 있는 최신 기사부터
            print(f"   (소재 고르기 AI 실패 → 본문 있는 기사 순서대로: {str(e)[:80]})")
            res = {}
        ranked = []
        for k in (res.get("indexes") or []) + [res.get("index")]:
            try:
                k = int(k)
            except (TypeError, ValueError):
                continue
            if 1 <= k <= len(items) and k not in ranked:
                ranked.append(k)
        if not topic:                    # Gemini 추천 다음엔 본문이 RSS 에 있는(막힐 일 없는) 기사들
            ranked += [i for i, it in enumerate(items, 1) if i not in ranked and len(it.get("content", "")) > 800]
        tried = 0
        for k in ranked:                                           # 1순위 기사가 막히면 다음 순위로
            it = items[k - 1]
            if len(it.get("content", "")) <= 800:
                tried += 1
                if tried > 3:                                      # 페이지를 받아야 하는 기사는 3개까지만 시도
                    continue
            title, text = get_article(client, models, it["link"], it["title"], it.get("content", ""))
            if len(text) > 300:
                return {"title": title, "url": it["link"], "text": text, "image": it.get("image", "")}
            print(f"   ({it['title'][:40]} → 본문을 못 읽어서 다음 후보로)")

    if topic:                                                       # RSS 에 없으면 구글 검색으로
        from google.genai import types
        try:
            conf = types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())])
            resp = client.models.generate_content(model=models[0], contents=SEARCH_PROMPT.format(topic=topic),
                                                  config=conf)
            m = re.search(r"\{[^{}]*\"url\"[^{}]*\}", resp.text or "")
            if m:
                found = json.loads(m.group(0))
                title, text = get_article(client, models, found["url"], found.get("title", ""))
                if len(text) > 300:
                    return {"title": title, "url": found["url"], "text": text, "image": ""}
        except Exception as e:
            print(f"   (구글 검색 실패: {e})")
    return None


# ================================================================ 대본
SCRIPT_PROMPT = """너는 조회수 수백만 회를 만든 한국 '실화 사연' 쇼츠 작가야. 아래 기사를 한국 시청자에게
친구한테 이야기하듯 들려주는 1분~1분 20초 쇼츠 대본을 써 줘.

[기사 제목] {title}
[기사 본문]
{text}

[사실 규칙 - 가장 중요]
- 기사에 있는 사실만. 숫자·장소·동물 종류·결과는 기사 그대로. 기사에 없는 대사·감정·장면을 지어내지 마.
- 일반인 실명은 쓰지 말고 "미국의 한 할머니", "한 소방관"처럼. 나라·도시 이름은 써도 돼.

[구성]
- scenes 8~12개. 각 scene 의 text = 나레이션 1문장 (15~45자). 전체 글자 수 420~560자.
- 1번 장면: 바로 위기나 기이한 상황으로 시작 ("아침에 눈을 떴는데, ~"). 배경 설명으로 시작하지 마.
- 중간: '그런데', '바로 그때', '알고 보니' 같은 연결어로 긴장 → 반전.
- 마지막 장면: 결과를 짧고 세게 + 댓글 유도 질문 ("여러분이라면 어떻게 했을까요?").
- 구어체 끝맺음('~예요/~거든요/~죠/~는데요/~니다'), 문장부호 필수, 강조 단어 1개만 {{중괄호}}.

[자료화면 visual]
- 장면마다 무료 자료화면 사이트(Pixabay)에서 찾을 영어 검색어 visual (1~3 단어, 흔한 장면) 과 대체 검색어 alt.
  특정 인물·사건 장면은 없으니 분위기가 맞는 일반 장면으로. 예) "dog sleeping", "firefighter ladder", "hospital hallway",
  "police car night", "old woman smiling", "computer screen code"
- 연속된 장면은 서로 다른 검색어로.

[후킹] hook1(주인공+상황, 16자 이내) / hook2(반전·결과, 16자 이내, 노란색, 핵심 단어 1개 {{중괄호}})
  예) "강아지가 총을 쐈는데" / "{{컴퓨터}}가 주인을 살렸습니다"
  hook1+hook2 를 이어 읽으면 매끄러운 한 문장 (조사 빠진 전보체·번역투·명사 나열 금지, 소리 내어 읽어 어색하면 다시).
  금지: "결과는 마지막에", "끝까지 보세요", "충격 주의", "결말 주의", "레전드"

[업로드] title(40자 이내, 끝에 #shorts), description(2~3문장, 마지막에 댓글 유도 질문), hashtags(5개, 한국어 위주)

JSON만 출력:
{{"hook1":"","hook2":"","scenes":[{{"text":"","visual":"","alt":""}}],"title":"","description":"","hashtags":["#"]}}
"""


def write_story_script(env, cfg, story):
    import finder
    from google.genai import types
    client = finder.gemini_client(env)
    models = finder.model_order(client, cfg.get("gemini_model", "gemini-flash-latest"))
    conf = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.7)
    prompt = SCRIPT_PROMPT.format(title=story["title"], text=story["text"][:8000])
    plan = finder.gemini_json(client, models, [prompt], conf, "사연 대본 쓰는 중")
    scenes = [s for s in plan.get("scenes") or [] if str(s.get("text", "")).strip()]
    if len(scenes) < 4:
        raise RuntimeError("사연 대본이 너무 짧아요")
    plan["scenes"] = scenes[:14]
    return plan


# ================================================================ 자료화면 (Pixabay)
def _get(url, **params):
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def _download(url, out):
    with requests.get(url, stream=True, timeout=120, headers=UA) as r:
        r.raise_for_status()
        with open(out, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    return out


def pixabay_video(key, q, used, min_dur=4.0):
    data = _get("https://pixabay.com/api/videos/", key=key, q=q, safesearch="true", per_page=12,
                video_type="film")
    for h in data.get("hits", []):
        if f"v{h['id']}" in used or h.get("duration", 0) < max(4.0, min_dur):   # 나레이션보다 짧으면 멈춤 화면이 생김
            continue
        vids = h.get("videos", {})
        best = next((vids[k] for k in ("medium", "large", "small")
                     if vids.get(k, {}).get("url") and vids[k].get("width", 0) >= 1280), None)
        if not best:
            continue
        out = STORY_DIR / f"pxv_{h['id']}.mp4"
        if not out.exists():
            _download(best["url"], out)
        used.add(f"v{h['id']}")
        return out, float(h["duration"]), h.get("user", "")
    return None


def pixabay_photo(key, q, used, seconds):
    data = _get("https://pixabay.com/api/", key=key, q=q, safesearch="true", per_page=12,
                image_type="photo", min_width=1280)
    for h in data.get("hits", []):
        if f"p{h['id']}" in used or not h.get("largeImageURL"):
            continue
        img = STORY_DIR / f"pxp_{h['id']}.jpg"
        if not img.exists():
            _download(h["largeImageURL"], img)
        used.add(f"p{h['id']}")
        out = STORY_DIR / f"pxp_{h['id']}_{int(seconds * 10)}.mp4"
        photo_to_clip(img, out, seconds)
        return out, seconds, h.get("user", "")
    return None


def photo_to_clip(img, out, seconds, fps=30):
    """사진 한 장 → 천천히 확대(또는 좌우 이동)하는 영상"""
    frames = int(seconds * fps)
    mode = random.choice(["in", "in", "pan"])
    if mode == "in":
        z = "min(zoom+0.0009,1.15)"
        x, y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    else:
        z = "1.12"
        x, y = f"(iw-iw/zoom)*on/{frames}", "ih/2-(ih/zoom/2)"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(img), "-vf",
                    f"scale=2560:-2:flags=lanczos,zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s=1920x1080:fps={fps},"
                    "format=yuv420p", "-t", f"{seconds:.2f}", "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "20", str(out)], check=True, timeout=600)


def fetch_visual(key, scene, used, seconds):
    """영상 → 대체 검색어 영상 → 사진 → 대체 사진 → 일반 장면 순서로 찾기"""
    tries = [("v", scene.get("visual")), ("v", scene.get("alt")),
             ("p", scene.get("visual")), ("p", scene.get("alt")), ("p", "city night"), ("p", "sky clouds")]
    for kind, q in tries:
        if not q:
            continue
        try:
            got = pixabay_video(key, q, used, seconds) if kind == "v" else pixabay_photo(key, q, used, seconds)
            if got:
                return got
        except Exception as e:
            print(f"   (자료화면 실패 '{q}': {e})")
    raise RuntimeError(f"자료화면을 못 찾았어요: {scene.get('visual')}")


# ================================================================ 대본 → 합성
def build_story_script(cfg, plan, visuals, bgm):
    lines = [f"HOOK1: {plan.get('hook1', '').strip()}", f"HOOK2: {plan.get('hook2', '').strip()}",
             "CREDIT: 자료화면 Pixabay · 실제 기사 재구성 (출처 설명란)",
             f"VOICE: {cfg['voice']}", f"RATE: {cfg['rate']}", "AUDIO: off", "XFADE: 0.35"]
    if bgm:
        lines += [f"BGM: {bgm}", f"BGM_VOLUME: {cfg.get('bgm_volume', '0.16')}"]
    lines.append("---")
    for i, (sc, (path, a, b)) in enumerate(zip(plan["scenes"], visuals)):
        rel = Path(path).relative_to(INPUT_DIR).as_posix()
        label = (plan.get("_labels") or {}).get(i, "")
        lines.append(f"[clip: {rel} | {a:.1f}-{b:.1f} | {label}]")
        text = str(sc["text"]).strip().replace("|", ",")
        lines.append(f"@0.2 | {text}")
    return "\n".join(lines) + "\n"


def make_story_episode(env, cfg, state, tg, topic=None):
    import finder
    if not env.get("PIXABAY_KEY"):
        raise RuntimeError(".env 에 PIXABAY_KEY 가 없어요 (사연 모드 자료화면용)")
    STORY_DIR.mkdir(parents=True, exist_ok=True)
    tg.send("📰 사연 영상 만들기 시작" + (f" (주제: {topic})" if topic else "") + " — 실화 기사 찾는 중...")
    story = pick_story(env, cfg, state, topic)
    if not story:
        raise RuntimeError("조건에 맞는 실화 기사를 못 찾았어요. 기사 링크를 직접 보내 주세요: 사연 https://...")
    tg.send(f"📰 소재: {story['title']}\n{story['url']}\n\n✍️ 대본 쓰는 중... (1~2분)")
    t0 = time.time()

    plan = write_story_script(env, cfg, story)
    state["episode"] = state.get("episode", 0) + 1
    ep = f"ep{state['episode']:03d}"
    state.setdefault("stories_used", []).append(story["url"])
    state["stories_used"] = state["stories_used"][-300:]
    finder.save_state(state)
    print(f"\n[{ep}] 사연: {story['title']}")

    chars = sum(len(compose.plain(str(sc["text"]))) for sc in plan["scenes"])
    tg.send(f"✍️ 대본 완성 — 장면 {len(plan['scenes'])}개, 약 {chars / 7.0 + 3:.0f}초\n"
            f"후킹: {compose.plain(plan.get('hook1', ''))} / {compose.plain(plan.get('hook2', ''))}\n"
            f"첫 문장: {compose.plain(str(plan['scenes'][0]['text']))}\n\n🎞 자료화면 받는 중...")
    used, visuals, owners = set(), [], []
    for sc in plan["scenes"]:
        est = len(compose.plain(str(sc["text"]))) / 7.0 + 0.9
        path, dur, owner = fetch_visual(env["PIXABAY_KEY"], sc, used, est + 0.8)
        a = 0.5 if dur > est + 1.5 else 0.0
        b = min(dur, a + est + 0.6)
        visuals.append((path, a, b))
        owners.append(owner)
        time.sleep(0.3)                                   # Pixabay 요청 간격

    # ---- 마지막 장면은 기사 속 실제 사진으로
    real_site = None
    if cfg.get("story_real_photo", True):
        try:
            img, real_site = article_image(story["url"], story.get("image", ""))
            if img:
                last = plan["scenes"][-1]
                est = len(compose.plain(str(last["text"]))) / 7.0 + 0.9
                secs = est + 1.6                                       # 사진은 조금 더 오래 보여 주기
                clip = real_photo_clip(img, STORY_DIR / f"{Path(img).stem}_{int(secs * 10)}.mp4", secs)
                visuals[-1] = (clip, 0.0, secs)
                owners[-1] = ""
                plan["_labels"] = {len(visuals) - 1: f"실제 사진 · {real_site}"}
                print(f"  마지막 장면: 기사 실제 사진 ({real_site})")
            else:
                print("  (기사에 쓸 만한 대표 사진이 없어서 자료화면으로 끝내요)")
        except Exception as e:
            real_site = None
            print(f"   (기사 사진 실패: {e})")

    bgm = finder.pick_bgm(cfg)
    script = INPUT_DIR / f"{ep}.txt"
    script.write_text(build_story_script(cfg, plan, visuals, bgm), encoding="utf-8")
    out = finder.OUTPUT_DIR / f"{ep}_final.mp4"
    print("  사연 영상 합성 중...")
    tg.send(f"🎬 자료화면 {len(visuals)}개 준비 완료{' (마지막: 기사 실제 사진)' if real_site else ''} — 영상 합성 중... (PC 성능에 따라 3~8분)")
    total = compose.compose(script, out)
    note = compose.tts_note()
    if note:
        tg.send(note)

    # ---- 업로드 문구 (기사 출처 + 자료화면 표기)
    title = plan.get("title", "").strip()
    desc = plan.get("description", "").strip()
    tags = " ".join(plan.get("hashtags", []))
    src = f"출처: {story['title']} ({story['url']})"
    if real_site:
        src += f"\n마지막 사진 출처: {real_site} (기사 사진)"
    pix = "자료화면: Pixabay" + (f" ({', '.join(sorted(set(o for o in owners if o))[:6])})" if any(owners) else "")
    ups = {"youtube": f"{title}\n\n{desc}\n\n{src}\n{pix}\n※ 실제 기사를 바탕으로 재구성한 영상이에요.\n\n{tags}",
           "tiktok": f"{re.sub(r'#shorts', '', title, flags=re.I).strip()} {tags}\n\n{src}",
           "reels": f"{re.sub(r'#shorts', '', title, flags=re.I).strip()}\n\n{desc}\n\n{src}\n\n{tags}"}
    (finder.OUTPUT_DIR / f"{ep}_upload.txt").write_text(
        f"[유튜브]\n{ups['youtube']}\n\n[틱톡]\n{ups['tiktok']}\n\n[인스타 릴스]\n{ups['reels']}\n", encoding="utf-8")
    meta = {"title": title, "description": ups["youtube"].split("\n", 1)[1].strip(),
            "tags": plan.get("hashtags", [])}
    (finder.OUTPUT_DIR / f"{ep}_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                                                       encoding="utf-8")
    tg.video(finder.telegram_copy(out), f"✅ {ep} 사연 영상 완성 ({total:.0f}초, 제작 {(time.time() - t0) / 60:.0f}분)\n"
                                        f"후킹: {compose.plain(plan.get('hook1', ''))} / "
                                        f"{compose.plain(plan.get('hook2', ''))}")
    uploaded = finder.youtube_upload(tg, out, meta, ep, state) if cfg.get("youtube_upload", True) else None
    state.setdefault("episodes", {})[ep] = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"), "seconds": round(total), "theme": "사연",
        "hook": f"{compose.plain(plan.get('hook1', ''))} / {compose.plain(plan.get('hook2', ''))}",
        "youtube": uploaded, "channels": [], "story": story["url"]}
    finder.save_state(state)
    if not uploaded:
        tg.send("📺 유튜브 (첫 줄=제목, 나머지=설명란)")
        tg.send(ups["youtube"])
    tg.send("🎵 틱톡 캡션")
    tg.send(ups["tiktok"])
    tg.send("📷 인스타 릴스 캡션")
    tg.send(ups["reels"])
    print(f"[{ep}] 사연 완료 ({total:.0f}초)")
    return ep
