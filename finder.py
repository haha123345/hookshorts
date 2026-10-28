# -*- coding: utf-8 -*-
"""
후킹 숏츠 자동 제작기

  1) 유튜브 CC 영상 + Pixabay 영상에서 후보 검색
  2) 텔레그램으로 후보 전송  →  번호로 답장 (예: 1 4 7)
  3) 고른 클립 다운로드
  4) Gemini가 영상을 보고 하이라이트 구간 + 후킹 + 나레이션 대본 작성
  5) compose.py로 1분~1분 30초 영상 합성
  6) 완성 영상 + 업로드용 제목/설명/해시태그를 텔레그램으로 전송

실행: find_videos.bat 더블클릭 (또는 python finder.py)
설정: config.json  /  키: .env
"""
import json
import random
import socket
from datetime import datetime, timedelta
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests

import compose

BASE = Path(__file__).resolve().parent
INPUT_DIR = BASE / "input"
CLIP_DIR = INPUT_DIR / "clips"
OUTPUT_DIR = BASE / "output"
STATE_PATH = BASE / "state.json"
CONFIG_PATH = BASE / "config.json"
VERSION = "v22 (쇼츠 보관 + 매주 일요일 롱폼 자동)"


# ================================================================ 설정/상태
def load_env():
    env = {}
    # 메모장이 .txt를 붙였거나, 압축을 풀면서 폴더가 한 겹 더 생긴 경우도 찾아봄
    cands = [d / n for d in (BASE, BASE.parent) for n in (".env", ".env.txt", "env.txt", "env")]
    p = next((c for c in cands if c.is_file()), None)
    if p is None:
        sys.exit(f".env 파일을 못 찾았어요.\n"
                 f"이 폴더에 .env 파일을 넣어 주세요: {BASE}")
    print(f"키 파일: {p}")
    for line in p.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    OPENAI["key"] = env.get("OPENAI_KEY", "")
    OPENAI["model"] = env.get("OPENAI_MODEL", "") or OPENAI["model"]
    if OPENAI["key"]:
        print(f"GPT 예비 연결: 있음 ({OPENAI['model']}) — Gemini 가 막히면 글쓰기 작업은 GPT 가 대신해요")
    missing = [k for k in ("TELEGRAM_TOKEN", "GEMINI_KEY", "YOUTUBE_KEY", "PIXABAY_KEY") if not env.get(k)]
    if missing:
        print(f"  (비어 있는 키: {', '.join(missing)})")
    return env


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return default


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


# ================================================================ 텔레그램
class Telegram:
    def __init__(self, token, state):
        if not token:
            sys.exit(".env 에 TELEGRAM_TOKEN 이 비어 있어요.")
        self.base = f"https://api.telegram.org/bot{token}/"
        self.state = state

    def call(self, method, files=None, timeout=60, **params):
        r = requests.post(self.base + method, data=params, files=files, timeout=timeout)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"텔레그램 오류({method}): {data.get('description')}")
        return data["result"]

    def updates(self, timeout=50):
        params = {"offset": self.state.get("tg_offset", 0), "timeout": timeout}
        try:
            data = requests.post(self.base + "getUpdates", data=params, timeout=timeout + 15).json()
        except requests.RequestException:   # 인터넷 잠깐 끊김 등
            time.sleep(5)
            return []
        if not data.get("ok"):
            raise RuntimeError(f"텔레그램 오류(getUpdates): {data.get('description')}")
        res = data["result"]
        if res:
            self.state["tg_offset"] = res[-1]["update_id"] + 1
            save_state(self.state)
        return res

    def skip_old(self):
        """프로그램 실행 전에 와 있던 메시지는 무시"""
        res = self.call("getUpdates", offset=-1, timeout=20)
        if res:
            self.state["tg_offset"] = res[-1]["update_id"] + 1
            save_state(self.state)

    def connect(self):
        if self.state.get("chat_id"):
            return
        print("텔레그램 연결: 새로 만든 봇에게 아무 메시지나 보내 주세요... (기다리는 중)")
        while True:
            for u in self.updates():
                msg = u.get("message") or {}
                if msg.get("chat", {}).get("id"):
                    self.state["chat_id"] = msg["chat"]["id"]
                    save_state(self.state)
                    self.send("✅ 연결됐어요! 이제 여기로 후보 영상과 완성 영상을 보내 드릴게요.")
                    print("텔레그램 연결 완료")
                    return

    def send(self, text):
        for i in range(0, len(text), 4000):   # 텔레그램 글자 수 제한
            self.call("sendMessage", chat_id=self.state["chat_id"], text=text[i:i + 4000],
                      disable_web_page_preview="true")

    def photo(self, url, caption):
        try:
            self.call("sendPhoto", chat_id=self.state["chat_id"], photo=url, caption=caption[:1000])
        except Exception:
            self.send(caption)

    def video(self, path, caption):
        with open(path, "rb") as f:
            self.call("sendVideo", files={"video": f}, timeout=600,
                      chat_id=self.state["chat_id"], caption=caption[:1000],
                      supports_streaming="true")

    def poll(self, timeout=50):
        """지금까지 온 내 메시지들 (최대 timeout초 기다림)"""
        out = []
        for u in self.updates(timeout):
            msg = u.get("message") or {}
            if msg.get("chat", {}).get("id") == self.state.get("chat_id") and msg.get("text"):
                out.append(msg["text"].strip())
        return out

    def wait_text(self, minutes):
        end = time.time() + minutes * 60
        while time.time() < end:
            for u in self.updates():
                msg = u.get("message") or {}
                if msg.get("chat", {}).get("id") == self.state["chat_id"] and msg.get("text"):
                    return msg["text"].strip()
        return None


# ================================================================ 검색
def active_keywords(cfg, state):
    """config 키워드 + 분석으로 추가한 키워드 - 뺀 키워드"""
    off = set(state.get("disabled_keywords", []))
    kws = [k for k in cfg.get("keywords", []) + state.get("extra_keywords", []) if k not in off]
    return list(dict.fromkeys(kws)) or cfg.get("keywords", [])


def iso_seconds(d):
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d or "")
    if not m:
        return 0
    dd, h, mi, s = (int(x or 0) for x in m.groups())
    return dd * 86400 + h * 3600 + mi * 60 + s


def human(n):
    n = int(n)
    if n >= 10000:
        return f"{n / 10000:.1f}만".replace(".0만", "만")
    return f"{n:,}"


def mmss(s):
    return f"{int(s) // 60}:{int(s) % 60:02d}"


def search_youtube(key, keyword, cfg, seen):
    api = "https://www.googleapis.com/youtube/v3/"
    r = requests.get(api + "search", timeout=30, params={
        "part": "snippet", "q": keyword, "type": "video", "videoLicense": "creativeCommon",
        "videoDefinition": "high",
        "order": "viewCount", "maxResults": 15, "safeSearch": "moderate", "key": key}).json()
    if "error" in r:
        raise RuntimeError(f"유튜브 검색 오류: {r['error'].get('message')}")
    ids = [it["id"]["videoId"] for it in r.get("items", []) if f"yt:{it['id']['videoId']}" not in seen]
    if not ids:
        return []
    v = requests.get(api + "videos", timeout=30, params={
        "part": "snippet,contentDetails,statistics,status", "id": ",".join(ids), "key": key}).json()
    items = v.get("items", [])
    ch_ids = list({it["snippet"]["channelId"] for it in items})
    subs = {}
    if ch_ids:
        c = requests.get(api + "channels", timeout=30, params={
            "part": "statistics", "id": ",".join(ch_ids), "key": key}).json()
        subs = {it["id"]: int(it["statistics"].get("subscriberCount", 0) or 0)
                for it in c.get("items", [])}
    out = []
    for it in items:
        sec = iso_seconds(it["contentDetails"].get("duration"))
        views = int(it["statistics"].get("viewCount", 0) or 0)
        if it["status"].get("license") != "creativeCommon":
            continue
        if not (cfg["clip_min_seconds"] <= sec <= cfg["clip_max_seconds"]):
            continue
        if views < cfg["min_views"]:
            continue
        cd = it["contentDetails"]
        rr = cd.get("regionRestriction", {})
        if "KR" in rr.get("blocked", []) or ("allowed" in rr and "KR" not in rr["allowed"]):
            continue                      # 한국에서 재생 안 되는 영상
        if cd.get("contentRating", {}).get("ytRating") == "ytAgeRestricted":
            continue                      # 연령 제한 (로그인 없이 다운로드 불가)
        if it["status"].get("privacyStatus") != "public":
            continue
        sn = it["snippet"]
        title_l = sn["title"].lower()
        if any(w in title_l for w in cfg.get("exclude_words", [])):
            continue                      # 효과음·배경영상·퍼온 모음 등 제외
        out.append({
            "id": f"yt:{it['id']}", "source": "유튜브 CC", "keyword": keyword,
            "title": sn["title"], "url": f"https://www.youtube.com/watch?v={it['id']}",
            "thumb": sn["thumbnails"].get("high", sn["thumbnails"].get("default", {})).get("url"),
            "views": views, "seconds": sec, "channel": sn["channelTitle"],
            "channel_url": f"https://www.youtube.com/channel/{sn['channelId']}",
            "subs": subs.get(sn["channelId"], 0), "channel_id": sn["channelId"],
        })
    return out


def search_pixabay(key, keyword, cfg, seen):
    r = requests.get("https://pixabay.com/api/videos/", timeout=30, params={
        "key": key, "q": keyword, "order": "popular", "safesearch": "true",
        "per_page": 5}).json()
    out = []
    for h in r.get("hits", []):
        if f"px:{h['id']}" in seen or h.get("duration", 0) < cfg["clip_min_seconds"]:
            continue
        vids = h.get("videos", {})
        best = next((vids[k] for k in ("large", "medium", "small") if vids.get(k, {}).get("url")), None)
        if not best:
            continue
        out.append({
            "id": f"px:{h['id']}", "source": "Pixabay", "keyword": keyword,
            "title": h.get("tags", ""), "url": h.get("pageURL"), "thumb": best.get("thumbnail"),
            "views": h.get("views", 0), "seconds": h.get("duration", 0),
            "channel": h.get("user", ""), "download": best["url"], "subs": 0,
        })
        if len(out) >= cfg["pixabay_per_keyword"]:
            break
    return out


def find_candidates(env, cfg, state):
    """주제(검색어)별로 후보를 묶어서 반환 - 같은 주제끼리 묶어야 영상이 자연스러움"""
    seen = set(state.get("shown", [])) | set(state.get("used", []))
    allk = active_keywords(cfg, state)
    kws = random.sample(allk, min(cfg["keywords_per_run"], len(allk)))
    per_yt = cfg.get("youtube_per_keyword", 4)
    cands = []
    for kw in kws:
        print(f"  검색: {kw}")
        group = []
        if env.get("YOUTUBE_KEY"):
            try:
                group += sorted(search_youtube(env["YOUTUBE_KEY"], kw, cfg, seen),
                                key=lambda c: -c["views"])[:per_yt]
            except Exception as e:
                print(f"   (유튜브 검색 실패: {e})")
        if cfg.get("use_pixabay") and env.get("PIXABAY_KEY"):
            try:
                group += search_pixabay(env["PIXABAY_KEY"], kw, cfg, seen)
            except Exception as e:
                print(f"   (Pixabay 검색 실패: {e})")
        blocked = set(state.get("blocked_channels", []))
        for c in group:
            if c.get("channel_id") in blocked:
                continue
            if c["id"] not in seen:
                seen.add(c["id"])
                cands.append(c)
    return cands[:cfg["max_candidates"]]


def send_candidates(tg, cands):
    tg.send(f"🎬 오늘의 후보 {len(cands)}개\n"
            "⚠️ 유튜브 CC는 채널을 눌러서 직접 찍은 영상을 올리는 채널인지 꼭 확인하세요. "
            "남의 영상을 퍼온 채널이면 쓰면 안 돼요.")
    theme = None
    for i, c in enumerate(cands, 1):
        if c["keyword"] != theme:
            theme = c["keyword"]
            nums = [str(j) for j, x in enumerate(cands, 1) if x["keyword"] == theme]
            tg.send(f"━━━━━━━━━━\n🗂 주제: {theme}  (#{nums[0]}~#{nums[-1]})")
        sub = f" (구독 {human(c['subs'])})" if c["subs"] else ""
        cap = (f"#{i} [{c['source']}] {c['title']}\n"
               f"조회수 {human(c['views'])} · 길이 {mmss(c['seconds'])}\n"
               f"채널: {c['channel']}{sub}\n{c['url']}")
        tg.photo(c["thumb"], cap)
    tg.send("👉 만들 영상의 클립 번호를 띄어쓰기로 보내 주세요.\n"
            "· 한 줄 = 영상 1편. ⭐같은 주제 안에서 3~5개를 고르면 자연스러워요  예: 1 2 4\n"
            "· 여러 편이면 줄을 바꿔서 보내기\n"
            "· 마음에 드는 게 없으면: 다시\n"
            "· 그만하려면: 취소")


def parse_choice(text, n):
    """'1 4 7\\n2 3' -> [[1,4,7],[2,3]] (1부터 시작하는 번호)"""
    groups = []
    for line in text.splitlines():
        nums = [int(x) for x in re.findall(r"\d+", line) if 1 <= int(x) <= n]
        nums = list(dict.fromkeys(nums))   # 중복 제거, 순서 유지
        if nums:
            groups.append(nums)
    return groups


# ================================================================ 다운로드
def video_size(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
                         capture_output=True, text=True, check=True).stdout.strip().splitlines()[0]
    w, h = (int(x) for x in out.split("x")[:2])
    return w, h


def download(c):
    CLIP_DIR.mkdir(parents=True, exist_ok=True)
    name = c["id"].replace(":", "_")
    out = CLIP_DIR / f"{name}.mp4"
    if out.exists() and out.stat().st_size > 0:
        return out
    print(f"  다운로드: {c['title'][:40]}")
    if c["source"] == "Pixabay":
        with requests.get(c["download"], stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(out, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
    else:
        # res = 짧은 변 기준 → 가로 1920x1080, 세로 1080x1920 모두 제 화질로
        base = [sys.executable, "-m", "yt_dlp", "--no-playlist", "--no-progress",
                "-f", "bv*+ba/b", "-S", "res:1080,ext:mp4:m4a",
                "--merge-output-format", "mp4", "-o", str(out)]
        err = ""
        for extra in ([], ["--extractor-args", "youtube:player_client=default,mweb"]):
            r = subprocess.run(base + extra + ["--", c["url"]], capture_output=True,
                               text=True, encoding="utf-8", errors="replace")
            if r.returncode == 0 and out.exists():
                break
            lines = [l for l in (r.stderr or "").splitlines() if "ERROR" in l or "WARNING" in l]
            err = " / ".join(lines[-3:]) or (r.stderr or "").strip()[-300:]
        else:
            raise RuntimeError(f"다운로드 실패 ({c['url']}): {err}")
    if not out.exists():
        raise RuntimeError(f"다운로드 실패: {c['url']}")
    return out


# ================================================================ Gemini 대본
PROMPT = """너는 조회수 수백만 회를 만든 한국 쇼츠 PD야. 위에 첨부한 클립 {n}개를 직접 보고
'어느 구간을 어떤 순서로 쓸지'와 후킹을 정해 줘. 나레이션은 다음 단계에서 쓰니까 여기선 쓰지 마.

[클립 확인 clips]
- 클립마다 사람 목소리(나레이션·대화·인터뷰)가 들리는지 speech(true/false), 무슨 일이 일어나는지 what(한 줄),
  가장 결정적인 순간(충돌·구조·탈출·반전 등)이 몇 초인지 key(클립 기준 초)를 적어.
- quality: 화질 점수 1~5 (5=선명한 HD·안정적, 3=보통, 1~2=흐림·심한 흔들림·저화질·화면 녹화). 2점 이하 클립은 쓰지 마.
- 화면에 원래 박혀 있는 글자(자막·제목·워터마크·채널명)가 있으면 text_where("none"/"bottom"/"top"/"both")와
  그 글자 띠가 화면 높이에서 차지하는 비율 text_height(0~0.35, 예: 아래 자막 두 줄이면 0.18)를 적어.
  화면 구석의 작은 로고는 "none". 비슷한 클립이면 글자 없는 클립을 우선 골라.

[클립 고르기 - 한 편의 영상처럼 이어지게]
- 받은 클립을 다 쓸 필요 없어. 같은 종류의 상황(예: 전부 '물살에 휩쓸림', 전부 '맹수와 마주침')이고
  분위기·촬영 방식(블랙박스끼리, 손으로 찍은 영상끼리, 드론끼리)이 비슷한 2~4개만 골라. 결이 다른 클립은 빼.
- 순서는 '긴장이 점점 커지게'. 앞 구간 끝과 다음 구간 시작의 느낌(움직임 방향·밝기·장소)이 자연스럽게 이어지면 더 좋아.

[영상 구성] 총 1분~1분 30초, 처음부터 끝까지 '다음이 궁금해서' 못 넘기게
1) teaser: 맨 앞 3.5~5초. 가장 강렬한 클립(=마지막 순서 클립)의 '결정적 순간 바로 직전' 긴장 장면. 결말은 보여주지 마.
2) segments: 2~4개. 덜 강렬한 것부터, 티저 클립은 반드시 맨 마지막.
   - 각 구간 10~35초. 그 클립의 key(결정적 순간)가 반드시 구간 안에 있어야 하고, key 앞에 3~10초 긴장 쌓기, key 뒤 2~5초 결과.
   - 지루한 시작·끝(아무 일 없는 장면, 화면 흔들림, 검은 화면, 인트로 자막)은 잘라내.
   - 시작은 장면이 막 시작되는 자연스러운 지점, 끝은 결과가 보이고 화면이 잠깐 안정된 지점으로.
     원본 안에서 장면이 바뀌는 순간(컷) 바로 앞뒤로 자르지 마.
   - 각 구간의 key도 같이 적어 (클립 기준 초). focus = key 순간에 시선이 가야 할 대상(차·사람·동물)의
     화면 위치 [가로, 세로] (원본 화면 기준 0~1, 왼쪽 위가 [0,0]). 그 위치를 확대하고 동그라미로 강조해.
3) 마지막 구간 뒤에는 프로그램이 key 순간을 슬로모션으로 한 번 더 보여줘(약 6초).
4) 티저 + segments 길이(end-start) 합 = {tmin}~{tmax}초.
5) theme: 클립들을 묶는 주제 (예: "브레이크가 말을 안 들은 순간들")
6) label: 순위 구성이 자연스러우면 "3위","2위","1위"(마지막이 1위), 아니면 "".

[후킹] 첫 1초에 엄지를 멈추게 하는 게 전부야. 마지막 클립 이야기로.
- 먼저 hook_candidates 에 서로 다른 유형으로 5개를 써:
  ① 구체적 위기+궁금한 결과  ② 숫자/규모 충격  ③ 시청자에게 질문("당신이라면?")
  ④ 금지·경고("절대 따라 하면 안 되는")  ⑤ 반전 예고("다들 ~인 줄 알았는데")
- 그중 가장 강한 것을 hook1/hook2 로 골라. 기준: 구체적인 주인공이 있는가, 결과가 정말 궁금한가,
  영상 결말이 그 궁금증에 '시원하게' 답하는가, 짧고 입에 붙는가.
- hook1: 주인공 + 상황, 16자 이내. hook2: 궁금증/반전, 16자 이내. 핵심 단어 1개를 {{중괄호}}(형광펜 강조).
- [자연스러움 - 가장 중요] hook1 과 hook2 를 이어 읽으면 사람이 말하듯 매끄러운 한 문장이어야 해.
  · 조사·어미를 빼먹은 전보체("트럭 앞 소년 정체") 금지, 번역투("~하는 것의") 금지, 명사만 나열 금지.
  · 첫 화면(티저)은 마지막 클립의 결정적 순간이야. 후킹은 그 장면에 지금 보이는 것과 맞아야 해 (안 보이는 걸 말하지 마).
  · 소리 내어 읽었을 때 어색하면 다시 써. 뉴스 헤드라인처럼 딱딱하지 말고, 친구에게 "이거 봐봐" 하듯.
  좋은 예) "물에 빠진 새끼 구하려다" / "어미가 {{공격}}한 이유" , "시속 100km 트럭 앞에" / "멈춰 선 {{소년}}"
- 금지(진부함): "결과는 마지막에", "끝까지 보세요", "끝까지 보면", "마지막 장면 실화", "충격 주의", "결말 주의", "레전드"
- 숫자는 영상이나 제목에 보이는 것만, 추정이면 '약'. 지어낸 사실 금지.

[업로드 문구]
- title: 40자 이내, hook1+hook2를 자연스러운 한 문장으로 + 끝에 #shorts
- description: 2~3문장. 첫 문장에 궁금증, 마지막에 "여러분이라면 어떻게 했을까요?" 같은 댓글 유도 질문
- hashtags: 관련 해시태그 5개 (#포함, 한국어 위주)

[클립 정보]
{clip_info}

반드시 아래 형식의 JSON만 출력해 (시간은 각 클립 기준 초):
{{"clips":[{{"clip":1,"speech":false,"what":"","key":0.0,"quality":4,"text_where":"none","text_height":0.0}}],
"theme":"","hook_candidates":[{{"type":"","hook1":"","hook2":""}}],"hook1":"","hook2":"",
"teaser":{{"clip":1,"start":0.0,"end":0.0}},
"segments":[{{"clip":1,"start":0.0,"end":0.0,"key":0.0,"focus":[0.5,0.5],"label":""}}],
"solo":{{"clip":1,"start":0.0,"end":0.0}},
"title":"","description":"","hashtags":["#",""]}}
"""

NARRATION_PROMPT = """너는 한국 쇼츠 나레이션 전문 작가야. 위 영상들은 완성 영상에 '그대로' 들어가는 구간이고
각 영상은 0초부터 시작해. 화면 장면에 딱 맞춰 읽을, 몰입감 있는 나레이션을 써 줘.

영상 목록:
{seg_info}

후킹(화면 상단 고정, 영상 맨 앞에서 이미 읽음): "{hook1} / {hook2}"
주제: {theme}

[1. 장면 싱크 - 제일 중요]
- beats = [{{"at": 초, "text": "문장"}}]. at = 그 문장이 말하는 일이 화면에 '막 보이기 시작하는' 시각.
- 화면에 아직 안 나온 일을 미리 말하지 마. 결정적 순간 문장은 그 순간이 나오는 바로 그 시각에.
- 화면에 없는 사실은 지어내지 마. 영상 속 원래 말소리나 글자는 내용 파악에만 참고.
- 한 문장 8~30자. 1초에 약 6.5글자를 읽으니 다음 at ≥ 이전 at + (이전 글자 수 ÷ 6.5) + 0.4.
- 결정적 순간 직전 1~2초는 말을 멈추고 화면만 보여줘 (긴장감).

[2. 끝까지 보게 만드는 장치]
- 구간이 바뀔 때마다 궁금증을 다시 걸어: "근데 진짜는 지금부터예요.", "여기서 반전이 있어요.", "이번엔 차원이 달라요."
- 결정적 순간 직전엔 질문으로 긴장: "과연 멈출 수 있을까요?", "여러분이라면 어떻게 하시겠어요?"
- 결과는 한 문장으로 짧고 세게: "결국, {{무사히}} 빠져나왔습니다." / "단 1초 차이였어요."
- 단순히 화면을 읽지 말고 '왜 위험한지 / 어떤 원리인지 / 얼마나 아슬아슬했는지'를 한 마디씩.

[3. 문체]
- 친구에게 놀라운 영상 보여주듯 구어체. 끝은 '~예요/~거든요/~죠/~는데요/~니다'. 명사형 끝('~하는 모습') 금지.
- 문장부호(. , ?) 꼭. 강조 단어는 문장마다 최대 1개 {{중괄호}}.

[4. 영상별 규칙]
- SEGMENT 1~{n_seg}: 본편. SEGMENT 1 첫 문장(at 0.3~1)은 주제 소개 + 기대감. 예) "{theme}, {n_seg}개 준비했는데 마지막이 진짜예요."
  이후 SEGMENT 첫 문장은 "두 번째는~", "마지막은~"처럼 이어 붙이되 매번 새로운 궁금증을 걸어.
- REPLAY: 마지막 구간의 결정적 순간을 0.5배 슬로모션으로 다시 보여주는 6초 안팎 영상.
  "다시 볼게요." 로 시작해서, 슬로모션에서만 보이는 디테일 1개를 짚고,
  마지막 문장은 처음 후킹으로 자연스럽게 이어지게(다시 보고 싶게) 마무리. 2~3문장.
{solo_rule}
반드시 아래 JSON만 출력해:
{{"segments":[{{"index":1,"beats":[{{"at":0.5,"text":""}}]}}],"replay_beats":[{{"at":0.3,"text":""}}]{solo_json}}}
"""

SOLO_RULE = """- SOLO: 유튜브용 단독 영상. 시작하자마자 후킹을 약 3초 읽으니 첫 at은 3.5 이상, 후킹·순위 언급 금지.
"""


def gemini_client(env):
    from google import genai
    from google.genai import types
    if not env.get("GEMINI_KEY"):
        sys.exit(".env 에 GEMINI_KEY 가 비어 있어요.")
    # 응답이 3분 넘게 없으면 끊고 다시 시도
    return genai.Client(api_key=env["GEMINI_KEY"], http_options=types.HttpOptions(timeout=180_000))


def model_order(client, preferred):
    """설정한 모델 → 다른 flash 모델 → lite 모델 순서로 시도할 목록"""
    names = []
    try:
        for m in client.models.list():
            n = m.name.replace("models/", "")
            acts = getattr(m, "supported_actions", None) or []
            if acts and "generateContent" not in acts:
                continue
            names.append(n)
    except Exception:
        pass
    bad = ("tts", "image", "live", "audio", "embedding", "transcribe", "exp", "translate", "omni", "nano")
    flash = sorted([n for n in names if "flash" in n and "lite" not in n
                    and not any(x in n for x in bad)], reverse=True)
    lite = sorted([n for n in names if "flash" in n and "lite" in n
                   and not any(x in n for x in bad)], reverse=True)
    # 젬마(Gemma): 글 전용 무료 모델. 제미나이가 붐빌 때 글쓰기 작업의 예비로 사용
    gemma = sorted([n for n in names if n.startswith("gemma") and n.endswith("-it")],
                   key=lambda n: [int(x) for x in re.findall(r"\d+", n)], reverse=True)
    GEMMA[:] = gemma[:2] or ["gemma-3-27b-it"]
    flash = [n for n in flash if n != preferred]
    lite = [n for n in lite if n != preferred]
    return [preferred] + flash[:3] + lite[:3]          # 붐비지 않는 lite 모델도 꼭 포함


def is_busy(e):
    t = str(e)
    return any(k in t for k in ("503", "UNAVAILABLE", "overloaded", "high demand", "429",
                                "RESOURCE_EXHAUSTED", "timed out", "Timeout", "500", "INTERNAL"))


def upload_files(client, paths):
    files = []
    for path in paths:
        f = client.files.upload(file=str(path))
        while "PROCESSING" in str(getattr(f.state, "name", f.state)):
            time.sleep(3)
            f = client.files.get(name=f.name)
        if "FAILED" in str(getattr(f.state, "name", f.state)):
            raise RuntimeError(f"Gemini 영상 처리 실패: {Path(path).name}")
        files.append(f)
    return files


def video_part(f, fps=None):
    from google.genai import types
    if not fps:
        return f
    try:
        return types.Part(file_data=types.FileData(file_uri=f.uri, mime_type=f.mime_type),
                          video_metadata=types.VideoMetadata(fps=fps))
    except Exception:
        return f


OPENAI = {"key": "", "model": "gpt-5-mini"}
GEMMA = ["gemma-3-27b-it"]


def gemma_json(client, contents, conf, what):
    """제미나이가 모두 붐빌 때: 같은 키로 쓰는 무료 젬마 모델 (JSON 모드가 없어서 글로 받아 정리)"""
    from google.genai import types
    temp = getattr(conf, "temperature", None)
    plain_conf = types.GenerateContentConfig(temperature=temp if temp is not None else 0.5)
    prompt = "\n\n".join(contents) + "\n\n(설명 없이 JSON 객체 하나만 출력)"
    last = None
    for model in GEMMA:
        print(f"  Gemma({model}) {what}...")
        try:
            resp = client.models.generate_content(model=model, contents=prompt, config=plain_conf)
            text = resp.text or ""
            m = re.search(r"\{.*\}", text, re.S)
            return json.loads(m.group(0) if m else text)
        except Exception as e:
            last = e
            print(f"   (젬마 실패: {str(e)[:90]})")
    raise RuntimeError(f"젬마도 실패: {str(last)[:150]}")


def openai_json(prompt, what):
    """GPT 로 JSON 받기 (글만 있는 작업용 예비). .env 의 OPENAI_KEY 필요 (유료, 아주 저렴)"""
    import requests
    model = OPENAI["model"]
    body = {"model": model, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": "Reply with one valid JSON object only."},
                         {"role": "user", "content": prompt}]}
    if model.startswith(("gpt-5", "o")):
        body["reasoning_effort"] = "low"
    last = None
    for attempt in range(3):
        print(f"  GPT({model}) {what}...")
        try:
            r = requests.post("https://api.openai.com/v1/chat/completions", json=body, timeout=240,
                              headers={"Authorization": f"Bearer {OPENAI['key']}"})
            if r.status_code == 401:
                raise RuntimeError("OPENAI_KEY 가 틀렸어요 (.env 확인)")
            if r.status_code == 429 and "insufficient_quota" in r.text:
                raise RuntimeError("OpenAI 크레딧이 없어요 (platform.openai.com → Billing 에서 충전)")
            if r.status_code in (429, 500, 502, 503):
                last = RuntimeError(f"GPT {r.status_code}")
                time.sleep(10 * (attempt + 1))
                continue
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"] or ""
            text = re.sub(r"^```(?:json)?|```$", "", text.strip()).strip()
            return json.loads(text)
        except json.JSONDecodeError as e:
            last = e
            print("   (GPT 형식이 이상해서 다시 요청)")
    raise RuntimeError(f"GPT {what} 실패: {str(last)[:200]}")


def gemini_json(client, models, contents, conf, what):
    last = None
    text_only = all(isinstance(c, str) for c in contents)
    for rnd in range(2):                           # 모델 목록을 최대 2바퀴
        for model in models:
            print(f"  Gemini({model}) {what}...")
            try:
                resp = client.models.generate_content(model=model, contents=contents, config=conf)
                text = re.sub(r"^```(?:json)?|```$", "", (resp.text or "").strip()).strip()
                return json.loads(text)
            except json.JSONDecodeError as e:
                last = e
                print("   (형식이 이상해서 다시 요청)")
            except Exception as e:
                last = e
                msg = str(e)[:100].replace("\n", " ")
                if is_busy(e):
                    print(f"   (서버 혼잡 → 다른 모델로: {msg})")
                    time.sleep(5)
                elif "404" in str(e) or "NOT_FOUND" in str(e):
                    print("   (이 모델은 없음 → 다음 모델)")
                else:
                    print(f"   (오류 → 다음 모델: {msg})")
        if text_only:                              # 글만 있는 작업 → 무료 젬마로
            print("   (제미나이가 모두 붐빔 → 젬마로 대신해요)")
            try:
                return gemma_json(client, contents, conf, what)
            except Exception as e:
                last = e
        if text_only and OPENAI["key"]:            # 그래도 안 되면 GPT 로
            print("   (Gemini 실패 → GPT 로 대신해요)")
            try:
                return openai_json("\n\n".join(contents), what)
            except Exception as e:
                last = e
                print(f"   ({e})")
        if rnd == 0:
            print("   (모든 모델이 바빠서 1분 쉬고 한 번 더 시도해요)")
            time.sleep(60)
    raise RuntimeError(f"Gemini {what} 실패 (잠시 후 다시 실행해 주세요): {str(last)[:300]}")


def learned_notes(state, for_narration=False):
    """주간 분석에서 배운 점 → 대본 프롬프트에 붙일 글 (없으면 빈 문자열)"""
    st = state.get("style_notes") or {}
    hp = state.get("hook_perf") or {}
    out = []
    if not for_narration:
        if st.get("patterns"):
            out.append("- 최근 잘 되는 한국 쇼츠 패턴: " + " / ".join(st["patterns"][:4]))
        if st.get("hook_examples"):
            out.append("- 참고 후킹 예시(그대로 베끼지 말고 형식만): " + " / ".join(st["hook_examples"][:5]))
        if hp.get("top"):
            out.append("- 우리 채널에서 조회수가 높았던 후킹: " + " / ".join(f"\"{h}\"({v}회)" for h, v, _ in hp["top"]))
        if hp.get("bottom"):
            out.append("- 우리 채널에서 반응이 낮았던 후킹(피할 것): " + " / ".join(f"\"{h}\"" for h, _, _ in hp["bottom"]))
    else:
        if st.get("structure"):
            out.append("- 잘 되는 영상의 전개: " + st["structure"])
        if st.get("patterns"):
            out.append("- 잘 되는 패턴: " + " / ".join(st["patterns"][:3]))
    if not out:
        return ""
    return ("\n[이번 주 분석에서 배운 점 - 위 규칙을 지키는 범위에서 반영해]\n" + "\n".join(out) + "\n")


def write_script_with_gemini(env, cfg, clips, notes=""):
    """1단계: 원본 클립 전체를 보고 구성(구간·순서·후킹·제목) 결정. clips: [(candidate, path, seconds)]"""
    from google.genai import types
    client = gemini_client(env)
    models = model_order(client, cfg["gemini_model"])
    print("  Gemini에 영상 올리는 중...")
    uploaded = []
    try:
        uploaded = upload_files(client, [p for _, p, _ in clips])
        parts, info = [], []
        for i, ((c, path, sec), f) in enumerate(zip(clips, uploaded), 1):
            parts += [f"CLIP {i}", f]
            info.append(f"CLIP {i}: 길이 {sec:.1f}초, 원제목 \"{c['title']}\", 검색어 \"{c['keyword']}\"")
        prompt = PROMPT.format(n=len(clips), tmin=cfg["target_seconds_min"],
                               tmax=cfg["target_seconds_max"], clip_info="\n".join(info)) + notes
        conf = types.GenerateContentConfig(
            response_mime_type="application/json", temperature=0.8,
            media_resolution=types.MediaResolution.MEDIA_RESOLUTION_LOW,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
        plan = gemini_json(client, models, parts + [prompt], conf, "영상 구성 짜는 중")
    finally:
        for f in uploaded:
            try:
                client.files.delete(name=f.name)
            except Exception:
                pass
    normalize_plan(plan, clips)
    return plan


def _range(d, clips, default_len=15.0, min_len=3.0, max_len=None):
    """Gemini가 준 구간을 안전한 값으로: (클립번호, 시작, 끝) 또는 None"""
    try:
        k = int(d.get("clip", 0))
    except (TypeError, ValueError, AttributeError):
        return None
    if not 1 <= k <= len(clips):
        return None
    sec = clips[k - 1][2]
    try:
        a = max(0.0, float(d.get("start", 0) or 0))
        b = min(sec, float(d.get("end", sec) or sec))
    except (TypeError, ValueError):
        a, b = 0.0, min(sec, default_len)
    if b - a < min_len:
        a, b = max(0.0, min(a, sec - default_len)), min(sec, max(a, 0.0) + default_len)
    if max_len:
        b = min(b, a + max_len)
    return k, round(a, 1), round(b, 1)


def normalize_plan(plan, clips):
    """구간 값을 한 번만 정리해 두고 (2단계 영상 자르기와 합성에 똑같이 사용)"""
    keys = {}
    for c in plan.get("clips") or []:
        try:
            keys[int(c.get("clip"))] = float(c.get("key"))
        except (TypeError, ValueError, AttributeError):
            pass
    segs = []
    for sg in plan.get("segments") or []:
        r = _range(sg, clips)
        if not r:
            continue
        k, a, b = r
        sec = clips[k - 1][2]
        try:
            key = float(sg.get("key"))
        except (TypeError, ValueError):
            key = keys.get(k)
        if key is not None and 0 <= key <= sec:
            a = max(0.0, min(a, key - 3.0))              # 결정적 순간이 반드시 들어가게
            b = min(sec, max(b, key + 2.5))
            if b - a > 40:                               # 너무 길면 key 중심으로 줄이기
                a = max(0.0, key - 25.0)
                b = min(sec, a + 40)
        else:
            key = None
        fxy = None
        try:
            fxy = [min(max(float(v), 0.0), 1.0) for v in (sg.get("focus") or [])[:2]]
            if len(fxy) != 2:
                fxy = None
        except (TypeError, ValueError):
            fxy = None
        na, nb = snap_to_cuts(clips[k - 1][1], a, b, key)
        if nb - na >= 6:
            if (na, nb) != (a, b):
                print(f"  (CLIP {k}: 원본 장면 컷에 맞춰 구간 조정 {a:.1f}-{b:.1f} → {na:.1f}-{nb:.1f})")
            a, b = na, nb
        segs.append({"k": k, "a": round(a, 2), "b": round(b, 2), "key": key, "focus": fxy,
                     "label": str(sg.get("label", "") or "").replace("|", " ").strip()})
    if not segs:
        raise RuntimeError("Gemini 구성에 쓸 수 있는 구간이 없어요")
    quality = {}
    for c in plan.get("clips") or []:
        try:
            quality[int(c.get("clip"))] = float(c.get("quality"))
        except (TypeError, ValueError, AttributeError):
            pass
    good = [sg for sg in segs[:-1] if quality.get(sg["k"], 3) > 2] + segs[-1:]
    if len(good) >= 2 and len(good) < len(segs):
        print(f"  (화질 낮은 구간 {len(segs) - len(good)}개 뺐어요)")
        segs = good
    plan["_segs"] = segs
    last = segs[-1]                                      # 다시보기: 마지막 구간의 결정적 순간 ±1.5초
    if last.get("key") is not None:
        sec = clips[last["k"] - 1][2]
        ra = max(0.0, last["key"] - 1.5)
        plan["_replay"] = (last["k"], round(ra, 1), round(min(sec, ra + 3.0), 1))
    else:
        plan["_replay"] = None
    plan["_teaser"] = _range(plan.get("teaser") or {}, clips, default_len=4.0, min_len=2.5, max_len=6.0)
    if plan["_teaser"]:
        tk, ta, tb = plan["_teaser"]
        na, nb = snap_to_cuts(clips[tk - 1][1], ta, tb, margin=0.8)
        if nb - na >= 2.5:
            plan["_teaser"] = (tk, na, nb)
    solo = _range(plan.get("solo") or {}, clips, default_len=25.0, min_len=8.0, max_len=45.0)
    plan["_solo"] = solo or (segs[-1]["k"], segs[-1]["a"], segs[-1]["b"])
    speech = set()
    for c in plan.get("clips") or []:
        try:
            if c.get("speech"):
                speech.add(int(c.get("clip")))
        except (TypeError, ValueError, AttributeError):
            pass
    plan["_speech"] = sorted(speech)
    cuts = {}
    for c in plan.get("clips") or []:
        try:
            k = int(c.get("clip"))
            where = str(c.get("text_where", "none")).lower()
            h = min(max(float(c.get("text_height") or 0), 0.0), 0.35)
        except (TypeError, ValueError, AttributeError):
            continue
        if where == "none" or h < 0.04:
            continue
        h = min(h + 0.03, 0.35)                          # 여유 있게
        cuts[k] = (h if where in ("top", "both") else 0.0, h if where in ("bottom", "both") else 0.0)
    plan["_cuts"] = cuts
    for sg in segs:                                       # 포커스 위치를 정사각형 화면 기준으로 변환
        if not sg.get("focus"):
            continue
        try:
            w, h = video_size(clips[sg["k"] - 1][1])
        except Exception:
            sg["focus"] = None
            continue
        ct, cb = cuts.get(sg["k"], (0.0, 0.0))
        uh = h * (1 - ct - cb)
        if w / uh < compose.PORTRAIT_RATIO:              # 세로 영상: 자르지 않고 가운데에 통째로
            fx = 0.5 + (sg["focus"][0] - 0.5) * (w / uh)
            fy = (sg["focus"][1] * h - h * ct) / uh
        else:
            side = min(w, uh)
            x0, y0 = (w - side) / 2, h * ct + (uh - side) / 2
            fx = (sg["focus"][0] * w - x0) / side
            fy = (sg["focus"][1] * h - y0) / side
        sg["focus"] = [round(min(max(fx, 0.12), 0.88), 3), round(min(max(fy, 0.12), 0.88), 3)]


_cut_cache = {}


def scene_cuts(path):
    """원본 안에서 장면이 바뀌는 시각들 (초)"""
    key = str(path)
    if key in _cut_cache:
        return _cut_cache[key]
    cuts = []
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-an", "-vf",
                            "scale=320:-2,select='gt(scene,0.33)',showinfo", "-f", "null", "-"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        cuts = [float(x) for x in re.findall(r"pts_time:([\d.]+)", r.stderr)]
    except Exception:
        pass
    _cut_cache[key] = cuts
    return cuts


def snap_to_cuts(path, a, b, key=None, margin=1.0):
    """구간 시작/끝 근처(1초 안)에 원본 컷이 있으면, 컷 바로 뒤에서 시작·바로 앞에서 끝나게 조정
    (다른 장면이 0.1초 깜빡이는 것 방지). 결정적 순간(key)은 꼭 남김"""
    cuts = scene_cuts(path)
    for c in cuts:
        if a < c <= a + margin and (key is None or c + 0.05 < key - 1.5):
            a = round(c + 0.05, 2)
    for c in sorted(cuts, reverse=True):
        if b - margin <= c < b and (key is None or c - 0.05 > key + 1.5):
            b = round(c - 0.05, 2)
    # 구간 안에 큰 컷이 있어도 그대로 두되(원본 편집), 너무 짧아지면 원래대로
    return (a, b)


def cut_for_review(path, a, b, out, slow=False):
    """2단계용: 실제로 쓰일 구간만 잘라서 가볍게 (slow=True 면 완성본처럼 0.5배속)"""
    vf = "scale=-2:480"
    if slow:
        vf = f"setpts={1 / compose.REPLAY_SPEED:.3f}*PTS," + vf
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{a:.2f}", "-t", f"{b - a:.2f}",
           "-i", str(path), "-vf", vf, "-r", "15", "-c:v", "libx264", "-preset", "veryfast", "-crf", "30"]
    cmd += ["-an"] if slow else ["-c:a", "aac", "-b:a", "64k"]
    subprocess.run(cmd + [str(out)], check=True)


def write_narration(env, cfg, plan, clips, notes=""):
    """2단계: 잘라낸 구간을 고화질로 보면서 '몇 초에 무슨 말을 할지' 작성 → plan 에 beats 추가"""
    import tempfile
    from google.genai import types
    client = gemini_client(env)
    models = model_order(client, cfg["gemini_model"])
    tmp = Path(tempfile.mkdtemp(prefix="hs_review_"))
    segs, solo = plan["_segs"], plan["_solo"]
    want_solo = cfg.get("youtube_short_version", False)
    jobs = [(f"SEGMENT {i}", sg["k"], sg["a"], sg["b"]) for i, sg in enumerate(segs, 1)]
    if plan.get("_replay"):
        jobs.append(("REPLAY", *plan["_replay"]))
    if want_solo:
        jobs.append(("SOLO", *solo))
    uploaded = []
    try:
        paths = []
        for i, (name, k, a, b) in enumerate(jobs):
            out = tmp / f"review_{i}.mp4"
            cut_for_review(clips[k - 1][1], a, b, out, slow=(name == "REPLAY"))
            paths.append(out)
        print("  나레이션용 장면 올리는 중...")
        uploaded = upload_files(client, paths)
        parts, info = [], []
        for (name, k, a, b), f in zip(jobs, uploaded):
            parts += [name, video_part(f, fps=cfg.get("review_fps", 2))]
            ln = (b - a) / (compose.REPLAY_SPEED if name == "REPLAY" else 1)
            info.append(f"- {name}: 길이 {ln:.1f}초 (CLIP {k})" + (" - 0.5배 슬로모션" if name == "REPLAY" else ""))
        prompt = NARRATION_PROMPT.format(seg_info="\n".join(info), hook1=compose.plain(plan.get("hook1", "")),
                                         hook2=compose.plain(plan.get("hook2", "")),
                                         theme=plan.get("theme", "") or "아찔한 순간들", n_seg=len(segs),
                                         solo_rule=SOLO_RULE if want_solo else "",
                                         solo_json=',"solo_beats":[{"at":3.5,"text":""}]' if want_solo else "") + notes
        conf = types.GenerateContentConfig(
            response_mime_type="application/json", temperature=0.7,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
        nar = gemini_json(client, models, parts + [prompt], conf, "장면 보고 나레이션 쓰는 중")
    finally:
        for f in uploaded:
            try:
                client.files.delete(name=f.name)
            except Exception:
                pass
        shutil.rmtree(tmp, ignore_errors=True)

    def clean(beats, length):
        out = []
        for bt in beats or []:
            try:
                at = float(bt.get("at", 0))
                text = str(bt.get("text", "")).strip().replace("|", ",")
            except (TypeError, ValueError, AttributeError):
                continue
            if text and 0 <= at < length:
                out.append((round(at, 1), text))
        return sorted(out)

    by_index = {}
    for item in nar.get("segments") or []:
        try:
            by_index[int(item.get("index"))] = item.get("beats")
        except (TypeError, ValueError, AttributeError):
            pass
    for i, sg in enumerate(segs, 1):
        sg["beats"] = clean(by_index.get(i), sg["b"] - sg["a"])
    plan["_solo_beats"] = clean(nar.get("solo_beats"), solo[2] - solo[1])
    if plan.get("_replay"):
        k, a, b = plan["_replay"]
        plan["_replay_beats"] = clean(nar.get("replay_beats"), (b - a) / compose.REPLAY_SPEED)
    if not any(sg["beats"] for sg in segs):
        raise RuntimeError("Gemini 나레이션이 비어 있어요")
    return plan


def pick_bgm(cfg):
    """input/bgm 폴더의 음악 중 하나를 무작위로 (없으면 None)"""
    d = INPUT_DIR / "bgm"
    files = [p for p in d.glob("*") if p.suffix.lower() in (".mp3", ".m4a", ".wav", ".ogg")] if d.exists() else []
    return random.choice(files).relative_to(INPUT_DIR).as_posix() if files else None


def build_script_text(plan, clips, cfg, parts, teaser=True, extra=(), bgm=None):
    """compose.py 대본 만들기. parts: [{k, a, b, label, beats, flags}]"""
    has_yt = any(c["source"] != "Pixabay" for c, _, _ in clips)
    mute_speech = cfg.get("mute_speech_clips", True)
    speech = set(plan.get("_speech", []))
    lines = [f"HOOK1: {plan.get('hook1', '').strip()}",
             f"HOOK2: {plan.get('hook2', '').strip()}"]
    if has_yt:
        lines.append("CREDIT: 원본 영상 출처는 설명란 참고 (CC BY)")
    lines += [f"VOICE: {cfg['voice']}", f"RATE: {cfg['rate']}"]
    if bgm:
        lines += [f"BGM: {bgm}", f"BGM_VOLUME: {cfg.get('bgm_volume', '0.16')}"]
        mode = cfg.get("original_audio", "auto")
        if mode == "off" or mode == "auto":
            lines.append("AUDIO: off")          # 배경음악이 있으면 원본 소리는 끔 (저작권 신고 줄이기)
    elif cfg.get("original_audio") == "off":
        lines.append("AUDIO: off")
    lines += [*extra, "---"]

    def header(k, a, b, label, flags=""):
        rel = clips[k - 1][1].relative_to(INPUT_DIR).as_posix()
        fl = [f for f in flags.split() if f]
        if mute_speech and k in speech and "mute" not in fl:
            fl.append("mute")
        ct, cb = plan.get("_cuts", {}).get(k, (0.0, 0.0))
        if ct:
            fl.append(f"cutt={ct:.2f}")
        if cb:
            fl.append(f"cutb={cb:.2f}")
        return f"[clip: {rel} | {a:.1f}-{b:.1f} | {label}" + (f" | {' '.join(fl)}]" if fl else "]")

    if teaser and plan.get("_teaser"):
        k, a, b = plan["_teaser"]
        lines.append("# 티저 (후킹 문구를 읽는 동안 마지막 장면 미리보기)")
        lines.append(header(k, a, b, "", "teaser"))
    for pt in parts:
        lines.append(header(pt["k"], pt["a"], pt["b"], pt.get("label", ""), pt.get("flags", "")))
        for at, text in pt.get("beats", []):
            lines.append(f"@{at:.1f} | {text}")
    return "\n".join(lines) + "\n"


def long_parts(plan, with_replay=True):
    parts = []
    for sg in plan["_segs"]:
        flags = ""
        if sg.get("key") is not None and sg["a"] <= sg["key"] < sg["b"]:
            flags = f"key={sg['key'] - sg['a']:.1f}"
            if sg.get("focus"):
                flags += f" fx={sg['focus'][0]:.2f} fy={sg['focus'][1]:.2f}"
        parts.append({"k": sg["k"], "a": sg["a"], "b": sg["b"], "label": sg["label"],
                      "beats": sg.get("beats", []), "flags": flags})
    if with_replay and plan.get("_replay"):
        k, a, b = plan["_replay"]
        parts.append({"k": k, "a": a, "b": b, "label": "▶ 다시 보기 0.5배", "flags": "replay",
                      "beats": plan.get("_replay_beats") or [(0.3, "다시 볼게요, 바로 이 순간이에요.")]})
    return parts


def solo_parts(plan):
    k, a, b = plan["_solo"]
    beats = plan.get("_solo_beats") or []
    if not beats:                                   # 단독용 대사가 없으면 마지막 구간 대사를 시간 맞춰 재사용
        last = plan["_segs"][-1]
        if last["k"] == k:
            shift = last["a"] - a
            beats = [(round(at + shift, 1), t) for at, t in last.get("beats", [])
                     if 3.5 <= at + shift < b - a]
    return [{"k": k, "a": a, "b": b, "label": "", "beats": beats}]


def make_youtube_cut(plan, clips, cfg, ep, limit):
    """59초 이하 유튜브용 영상. 기본은 '이야기 하나'(단독 구간, 티저 없이 바로 위기부터)"""
    single = cfg.get("youtube_style", "single") == "single"
    vol = cfg.get("youtube_original_volume", "0.15")
    out = OUTPUT_DIR / f"{ep}_yt.mp4"
    script = INPUT_DIR / f"{ep}_yt.txt"
    if single:
        variants = [(solo_parts(plan), False)]
    else:
        lp = long_parts(plan, with_replay=False)
        variants = [(lp[d:], True) for d in range(len(lp))]
    for parts, teaser in variants:
        script.write_text(build_script_text(plan, clips, cfg, parts, teaser=teaser, bgm=plan.get("_bgm"),
                                            extra=[f"ORIGINAL_VOLUME: {vol}"]), encoding="utf-8")
        print(f"  유튜브용 버전 합성 중 ({'이야기 하나' if single else f'구간 {len(parts)}개'})...")
        total = compose.compose(script, out)
        if total <= limit + 0.5:
            return out, total
    # 그래도 길면 끝부분(결말)을 지키면서 앞을 잘라냄
    cut = out.with_name(f"{ep}_yt_cut.mp4")
    start = max(0.0, total - limit)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.2f}", "-i", str(out),
                    "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-c:a", "aac", "-b:a", "160k",
                    "-movflags", "+faststart", str(cut)], check=True)
    cut.replace(out)
    return out, limit


def build_upload_texts(plan, clips):
    """플랫폼별 업로드 문구 3개 (텔레그램에서 하나씩 길게 눌러 복사)"""
    full, short = [], []
    for c, _, _ in clips:
        if c["source"] == "Pixabay":
            full.append(f"· Pixabay / {c['channel']}")
            short.append(f"Pixabay/{c['channel']}")
        else:
            full.append(f"· \"{c['title']}\" - {c['channel']} ({c['url']}) / CC BY")
            short.append(f"{c['channel']}(YouTube, CC BY)")
    tags = " ".join(plan.get("hashtags", []))
    title = plan.get("title", "").strip()
    title_plain = re.sub(r"\s*#shorts\s*$", "", title, flags=re.I).strip()
    desc = plan.get("description", "").strip()
    youtube = (f"{title}\n\n{desc}\n\n원본 영상 출처 (Creative Commons 저작자 표시)\n"
               + "\n".join(full) + f"\n\n{tags}")
    tiktok = f"{title_plain} {tags}\n\n영상 출처: " + ", ".join(short)
    reels = f"{title_plain}\n\n{desc}\n\n영상 출처: " + ", ".join(short) + f"\n\n{tags}"
    return {"youtube": youtube, "tiktok": tiktok, "reels": reels}


def telegram_copy(path):
    """텔레그램 봇은 50MB까지만 보낼 수 있어서 크면 용량을 줄인 사본을 만듦"""
    if path.stat().st_size < 48 * 1024 * 1024:
        return path
    small = path.with_name(path.stem + "_tg.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-c:v", "libx264",
                    "-preset", "veryfast", "-crf", "26", "-maxrate", "3500k", "-bufsize", "7000k",
                    "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(small)], check=True)
    return small


# ================================================================ 한 편 만들기
def make_episode(env, cfg, state, tg, picks):
    state["episode"] = state.get("episode", 0) + 1
    ep = f"ep{state['episode']:03d}"
    save_state(state)
    print(f"\n[{ep}] 클립 {len(picks)}개로 제작 시작")
    tg.send(f"⏳ {ep} 제작 시작 (클립 {len(picks)}개). 몇 분 걸려요.")

    clips = []
    min_side = int(cfg.get("min_clip_resolution", 480))
    low = []                                              # 화질 기준 미달이지만 받기는 된 클립
    for c in picks:
        try:
            path = download(c)
            sec, _ = compose.probe(path)
            w, h = video_size(path)
            if min(w, h) < min_side:
                low.append((min(w, h), (c, path, sec)))
                print(f"  (화질 낮음 {w}x{h} → 다른 클립이 부족할 때만 사용)")
                continue
            clips.append((c, path, sec))
        except Exception as e:
            print(f"  [건너뜀] {e}")
            tg.send(f"⚠️ 클립 하나를 못 받아서 빼고 진행해요: {c['title'][:40]}\n({str(e)[:300]})")
            state.setdefault("used", []).append(c["id"])   # 다음 후보에 다시 안 나오게
    if len(clips) < 3 and low:                            # 화질 때문에 제작이 멈추지 않게
        for _, item in sorted(low, key=lambda x: -x[0])[:3 - len(clips)]:
            clips.append(item)
    if not clips:
        raise RuntimeError("고른 클립을 하나도 받지 못했어요")
    total_src = sum(s for _, _, s in clips)
    if total_src < 60:
        tg.send(f"⚠️ 고른 클립 원본 길이를 다 합쳐도 {total_src:.0f}초라서 1분이 안 될 수 있어요.")

    plan = write_script_with_gemini(env, cfg, clips, learned_notes(state))            # 1단계: 구성
    write_narration(env, cfg, plan, clips, learned_notes(state, for_narration=True))   # 2단계: 나레이션
    script = INPUT_DIR / f"{ep}.txt"
    plan["_bgm"] = pick_bgm(cfg)
    script.write_text(build_script_text(plan, clips, cfg, long_parts(plan), bgm=plan["_bgm"]),
                      encoding="utf-8")
    ups = build_upload_texts(plan, clips)
    (OUTPUT_DIR / f"{ep}_upload.txt").write_text(
        f"[유튜브 - 첫 줄이 제목, 나머지는 설명]\n{ups['youtube']}\n\n"
        f"[틱톡]\n{ups['tiktok']}\n\n[인스타 릴스]\n{ups['reels']}\n", encoding="utf-8")

    out = OUTPUT_DIR / f"{ep}_final.mp4"
    print("  영상 합성 중 (틱톡·릴스용)...")
    total = compose.compose(script, out)
    note = compose.tts_note()
    if note:
        tg.send(note)

    # ---- 유튜브용 60초 미만 버전 (1분 넘는 쇼츠는 저작권 신고 하나만 걸려도 재생이 막힘)
    yt_out, yt_total = out, total
    limit = float(cfg.get("youtube_max_seconds", 59))
    if cfg.get("youtube_short_version", False) and total > limit:
        try:
            yt_out, yt_total = make_youtube_cut(plan, clips, cfg, ep, limit)
        except Exception as e:
            print(f"  (유튜브용 짧은 버전 실패 → 긴 버전 사용: {e})")
            yt_out, yt_total = out, total
    for c, _, _ in clips:
        state.setdefault("used", []).append(c["id"])
    save_state(state)

    note = "" if total >= 61 else "\n⚠️ 1분 미만이라 틱톡 수익화 대상은 아니에요."
    if yt_out != out:
        note += f"\n(유튜브에는 {yt_total:.0f}초 짧은 버전을 따로 올려요)"
    tg.video(telegram_copy(out), f"✅ {ep} 완성 · 틱톡/릴스용 {total:.0f}초{note}\n"
                                 f"후킹: {compose.plain(plan.get('hook1', ''))} / {compose.plain(plan.get('hook2', ''))}")
    # ---- 유튜브 자동 업로드 (비공개)
    meta = {"title": plan.get("title", ""),
            "description": ups["youtube"].split("\n", 1)[1].strip() if "\n" in ups["youtube"] else "",
            "tags": plan.get("hashtags", [])}
    (OUTPUT_DIR / f"{ep}_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                                                encoding="utf-8")
    uploaded = None
    if cfg.get("youtube_upload", True):
        uploaded = youtube_upload(tg, yt_out, meta, ep, state)
    else:
        print("  (config.json 에서 youtube_upload 가 꺼져 있어서 업로드 안 함)")
    state.setdefault("episodes", {})[ep] = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"), "seconds": round(total),
        "yt_seconds": round(yt_total),
        "theme": max((c.get("keyword", "") for c, _, _ in clips),
                     key=lambda k: sum(1 for x, _, _ in clips if x.get("keyword") == k)),
        "hook": f"{compose.plain(plan.get('hook1', ''))} / {compose.plain(plan.get('hook2', ''))}",
        "youtube": uploaded,
        "channels": [[c.get("channel_id", ""), c.get("channel", ""), c["source"]] for c, _, _ in clips]}
    save_state(state)
    if not uploaded:
        if yt_out != out:
            tg.video(telegram_copy(yt_out), f"📺 {ep} 유튜브용 ({yt_total:.0f}초) — 유튜브에는 이걸 올리세요")
        tg.send("📺 유튜브 (첫 줄=제목, 나머지=설명란)")
        tg.send(ups["youtube"])
    tg.send("🎵 틱톡 캡션")
    tg.send(ups["tiktok"])
    tg.send("📷 인스타 릴스 캡션")
    tg.send(ups["reels"])
    print(f"[{ep}] 완료: {out.name} ({total:.0f}초)")
    return ep


def channel_line():
    import uploader
    try:
        return f" — 업로드 채널: 「{uploader.channel_info()[0]}」"
    except Exception:
        return ""


def youtube_upload(tg, video, meta, ep, state=None, quiet=False):
    """성공하면 유튜브 영상 ID, 아니면 None (state 를 주면 실패한 영상을 자동 재시도 목록에 넣음)"""
    import uploader
    try:
        print("  유튜브에 비공개 업로드 중...")
        vid = uploader.upload(video, meta)
        watch, studio = uploader.links(vid)
        try:
            ch = f"「{uploader.channel_info()[0]}」 채널에 "
        except Exception:
            ch = ""
        sch = uploader.LAST.get("scheduled")
        if sch:
            from datetime import timezone as _tz, timedelta as _td
            when = sch.astimezone(_tz(_td(hours=9))).strftime("%H:%M")
            tg.send(f"📺 {ep} {ch}올렸어요 — 오늘 {when}에 자동 공개 예약\n"
                    f"👉 Studio 에서 저작권 '문제 없음'인지만 확인하세요 (문제 있으면 예약 취소)\n{studio}")
        else:
            tg.send(f"📺 {ep} {ch}비공개로 올렸어요 (제목·설명·태그 입력 완료)\n"
                    f"👉 저작권 '문제 없음' 확인 후 '공개' 또는 '예약'으로 바꾸세요\n{studio}")
        print(f"  업로드 완료: {watch}")
        if state is not None:
            state["pending_uploads"] = [p for p in state.get("pending_uploads", []) if p["ep"] != ep]
            if ep in state.get("episodes", {}):
                state["episodes"][ep]["youtube"] = vid
            save_state(state)
        return vid
    except uploader.NotReady as e:
        print(f"  (유튜브 자동 업로드 건너뜀: {e})")
        msg, hours, login = "유튜브 로그인이 필요해요. 텔레그램에 '로그인' 이라고 보내 주세요.", None, True
    except Exception as e:
        print(f"  (유튜브 업로드 실패: {e})")
        msg, hours, login = uploader.explain_error(e)
    queued = ""
    if state is not None:
        pend = [p for p in state.get("pending_uploads", []) if p["ep"] != ep]
        old = next((p for p in state.get("pending_uploads", []) if p["ep"] == ep), {})
        tries = old.get("tries", 0) + 1
        if tries <= 8:
            pend.append({"ep": ep, "video": Path(video).name, "tries": tries,
                         "retry_at": time.time() + hours * 3600 if hours else None,
                         "wait_login": login})
            queued = "\n(자동 재시도 목록에 넣었어요. '상태'에서 확인 가능)"
        state["pending_uploads"] = pend
        save_state(state)
    if quiet and queued and not login:        # 자동 재시도 중 같은 실패면 조용히 (성공하면 알림)
        print(f"  ({ep} 재시도 실패 → 다음에 다시)")
        return None
    if not queued and state is not None:
        msg += "\n(8번 시도해도 안 돼서 자동 재시도를 멈췄어요)"
    tg.send(f"⚠️ {ep} 유튜브 업로드 실패\n{msg}{queued}\n지금 바로 다시 하려면: 업로드 {ep}")
    return None


def retry_pending_uploads(tg, state, after_login=False, only=None):
    """재시도 시간이 된 업로드 하나를 다시 올림 (봇이 대기 중일 때 자동 호출)"""
    now = time.time()
    for p in list(state.get("pending_uploads", [])):
        if only and p["ep"] != only:
            continue
        due = (after_login and p.get("wait_login")) or (p.get("retry_at") and p["retry_at"] <= now)
        if not due:
            continue
        video = OUTPUT_DIR / p.get("video", f"{p['ep']}_final.mp4")
        meta_path = OUTPUT_DIR / f"{p['ep']}_meta.json"
        if not video.exists() or not meta_path.exists():
            state["pending_uploads"].remove(p)
            save_state(state)
            continue
        print(f"  {p['ep']} 유튜브 업로드 재시도 ({p.get('tries', 1) + 1}번째)")
        youtube_upload(tg, video, json.loads(meta_path.read_text(encoding="utf-8")), p["ep"], state,
                       quiet=not after_login)
        return True
    return False


# ================================================================ 후보 고르기 (사람이 선택)
def choose_session(env, cfg, state, tg):
    """후보 보내기 → 번호 답장 → 제작. 끝나면 True(계속)/False(종료) 반환"""
    while True:
        print("\n후보 영상 찾는 중...")
        tg.send("🔎 후보 영상 찾는 중... (1분 정도)")
        cands = find_candidates(env, cfg, state)
        if not cands:
            tg.send("😥 새 후보를 못 찾았어요. config.json 의 keywords 를 바꾸거나 min_views 를 낮춰 보세요.")
            return
        state.setdefault("shown", []).extend(c["id"] for c in cands)
        state["shown"] = state["shown"][-3000:]
        save_state(state)
        send_candidates(tg, cands)
        print(f"텔레그램으로 후보 {len(cands)}개를 보냈어요. 답장을 기다리는 중...")

        reply = tg.wait_text(cfg.get("wait_reply_minutes", 120))
        if reply is None or reply in ("취소", "그만"):
            tg.send("👌 후보 고르기를 끝냈어요.")
            return
        if reply.startswith("다시"):
            continue
        groups = parse_choice(reply, len(cands))
        if not groups:
            tg.send("번호를 못 읽었어요. 예: 1 4 7  (다시 찾기: 다시 / 끝내기: 취소)")
            reply = tg.wait_text(30)
            groups = parse_choice(reply or "", len(cands))
            if not groups:
                return
        for g in groups:
            picks = [cands[i - 1] for i in g]
            while True:
                try:
                    make_episode(env, cfg, state, tg, picks)
                    break
                except Exception as e:
                    print(f"[실패] {e}")
                    tg.send(f"❌ 제작 실패: {str(e)[:500]}\n\n"
                            "같은 클립으로 다시 하려면: 재시도  /  건너뛰려면: 아무 말이나")
                    r = tg.wait_text(60)
                    if not r or not r.startswith("재시도"):
                        break
        tg.send("🎉 요청한 영상을 다 만들었어요. 더 고르려면 '다시', 끝내려면 '취소'")
        reply = tg.wait_text(30)
        if not reply or not reply.startswith("다시"):
            tg.send("👌 후보 고르기를 끝냈어요.")
            return


# ================================================================ 자동 선택 (사람 없이)
def suspicious_channel(c, cfg):
    name = (c.get("channel") or "").lower()
    return any(w in name for w in cfg.get("auto_exclude_channel_words", []))


def auto_pick(env, cfg, state):
    """주제 하나를 골라 그 안에서 클립 3~4개 자동 선택. 반환: (주제, 클립들)"""
    if not env.get("YOUTUBE_KEY"):
        raise RuntimeError(".env 에 YOUTUBE_KEY 가 없어요")
    seen = set(state.get("shown", [])) | set(state.get("used", []))
    blocked = set(state.get("blocked_channels", []))
    last = state.setdefault("kw_last", {})
    # 오래 안 쓴 주제 우선 + 성적 좋은 주제는 최대 3일 먼저 차례가 옴
    score = state.get("theme_scores", {})
    kws = sorted(active_keywords(cfg, state),
                 key=lambda k: (last.get(k, 0) - score.get(k, 0) * 3 * 86400, random.random()))
    # 1차: 원래 기준(조회수·클립 3개) → 못 찾으면 2차: 조회수 기준 1/4, 클립 2개, 검색어 더 많이
    for pass_no, (views_min, need, n_kw) in enumerate(
            [(cfg["min_views"], 3, 8), (max(3000, cfg["min_views"] // 4), 2, 14)], 1):
        pcfg = dict(cfg, min_views=views_min)
        if pass_no == 2:
            print(f"  (기준을 낮춰 다시 찾아요: 조회수 {views_min:,}회 이상, 클립 {need}개)")
        for kw in kws[:n_kw]:
            last[kw] = time.time()
            print(f"  자동 검색: {kw}")
            try:
                res = search_youtube(env["YOUTUBE_KEY"], kw, pcfg, seen)
            except Exception as e:
                print(f"   (검색 실패: {e})")
                continue
            res = [c for c in res if c.get("channel_id") not in blocked and not suspicious_channel(c, cfg)]
            res.sort(key=lambda c: -c["views"])
            picks, chans, total = [], set(), 0
            for c in res:
                if c["channel_id"] in chans:          # 채널 하나당 클립 1개 (위험 분산)
                    continue
                picks.append(c)
                chans.add(c["channel_id"])
                total += min(c["seconds"], 30)
                if len(picks) >= int(cfg.get("auto_candidates", 5)) or (len(picks) >= 4 and total >= 110):
                    break
            if len(picks) >= need:
                save_state(state)
                return kw, picks
    save_state(state)
    return None, []


def auto_make(env, cfg, state, tg, count, reason="요청"):
    count = max(1, min(int(count), 6))
    tg.send(f"🤖 자동 제작 시작 ({reason}): {count}편\n주제와 클립을 알아서 골라서 만들고 비공개로 올려요.")
    done = []
    for i in range(count):
        try:
            kw, picks = auto_pick(env, cfg, state)
            if not picks:
                tg.send("😥 기준을 낮춰도 쓸 만한 해외 클립을 못 찾았어요. (클라우드에서는 사연 영상으로 대신 만들어요)")
                break
            tg.send(f"🎯 {i + 1}/{count}: 주제 '{kw}' — " + ", ".join(c["channel"] for c in picks))
            ep = make_episode(env, cfg, state, tg, picks)
            done.append(ep)
        except Exception as e:
            print(f"[자동 제작 실패] {e}")
            tg.send(f"❌ {i + 1}번째 자동 제작 실패: {str(e)[:400]}")
    if done:
        eps = state.get("episodes", {})
        res = [f"{ep} {'📺 유튜브 비공개 업로드됨' if eps.get(ep, {}).get('youtube') else '⚠️ 유튜브 업로드 안 됨'}"
               for ep in done]
        tg.send("✅ 자동 제작 완료\n" + "\n".join(res) + "\n\n"
                "📌 Studio 앱에서 각 영상의 '저작권' 항목이 '문제 없음'인지 확인한 뒤 공개하세요.\n"
                "문제 있는 영상이 있으면 '차단 ep번호' 라고 보내면 그 채널들은 다시 안 써요."
                + ("\n업로드 안 된 영상은 '업로드 ep번호' 로 다시 올릴 수 있어요." if any("안 됨" in r for r in res) else ""))
    return done


# ================================================================ 텔레그램 봇 모드
HELP = """🤖 hookshorts 리모컨 명령어

후보 — 후보 영상을 보내 줘요 (번호로 골라서 제작)
자동 3 — 지금 바로 3편 자동 제작 + 비공개 업로드
상태 — 자동 제작 시간, 유튜브 연결, 최근 영상
시간 06:30 — 매일 자동 제작 시간 바꾸기 (여러 개: 시간 06:30 18:00)
개수 3 — 매일 자동으로 만들 편 수
자동 켜기 / 자동 끄기 — 매일 자동 제작 켜고 끄기
차단 ep005 — 그 영상에 쓴 채널들을 앞으로 안 씀 (저작권 문제 생겼을 때)
업로드 ep005 — 그 영상을 유튜브에 다시 올리기
로그인 — 유튜브 로그인 (7일마다 풀릴 때 폰에서 다시 연결)
사연 — 실화 사연 영상 1편 (해외 실화 뉴스 자동 선택)
사연 고양이 구조 / 사연 https://기사링크 — 주제나 기사를 정해서 사연 영상
사연 개수 1 — 매일 자동 제작 중 사연 영상 편 수 (0이면 안 만듦)
채널 — 영상이 올라가는 유튜브 채널 확인
업데이트 — 새 버전이 있는지 바로 확인 (평소엔 30분마다 자동)
진단 — 최근 영상이 왜 조회수가 안 나오는지 점검 (비공개·저작권 차단·연령 제한)
분석 — 내 채널 + 경쟁 채널 분석 보고서 (매주 일요일 밤 자동)
적용 — 분석 추천을 반영 (기본은 매주 자동 반영)
배운점 — 지금 대본·후킹에 반영 중인 분석 내용
되돌리기 — 마지막 분석 반영 취소
키워드 — 지금 쓰는 검색어 목록
도움 — 이 안내"""


def daily_settings(cfg, state):
    times = state.get("daily_times") or cfg.get("daily_times", ["06:30"])
    count = int(state.get("daily_count") or cfg.get("daily_count", 3))
    on = state.get("daily_on", cfg.get("daily_on", True))
    return times, count, on


def due_daily(cfg, state, now):
    """지금 돌려야 할 예약 시간이 있으면 그 키를 반환 (PC가 늦게 켜져도 6시간 안이면 실행)"""
    times, _, on = daily_settings(cfg, state)
    if not on:
        return None
    done = state.setdefault("daily_done", [])
    for t in times:
        try:
            hh, mm = (int(x) for x in t.split(":"))
        except ValueError:
            continue
        at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        key = f"{at:%Y-%m-%d} {t}"
        if at <= now < at + timedelta(hours=6) and key not in done:
            return key
    return None


def next_daily(cfg, state, now):
    times, _, on = daily_settings(cfg, state)
    if not on:
        return None
    cands = []
    for t in times:
        try:
            hh, mm = (int(x) for x in t.split(":"))
        except ValueError:
            continue
        at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        cands.append(at if at > now else at + timedelta(days=1))
    return min(cands) if cands else None


def status_text(cfg, state):
    import uploader
    times, count, on = daily_settings(cfg, state)
    ok, msg = uploader.login_status()
    nxt = next_daily(cfg, state, datetime.now())
    eps = list(state.get("episodes", {}).items())[-5:]
    import selfupdate
    cv = selfupdate.current()
    lines = [f"📊 상태 ({VERSION}{' · 코드 ' + cv['date'] if cv.get('date') else ''})",
             f"매일 자동 제작: {'켜짐' if on else '꺼짐'} / {', '.join(times)} / {count}편"
             f" (그중 사연 {min(int(state.get('daily_story', cfg.get('daily_story_count', 1))), count)}편)",
             f"다음 자동 제작: {nxt:%m월 %d일 %H:%M}" if nxt else "다음 자동 제작: 없음",
             f"유튜브 연결: {'✅ ' + msg + channel_line() if ok else '❌ ' + msg}",
             f"유튜브 자동 업로드: {'켜짐' if cfg.get('youtube_upload', True) else '꺼짐 (config.json youtube_upload)'}",
             f"차단한 채널: {len(state.get('blocked_channels', []))}개"]
    pend = state.get("pending_uploads", [])
    if pend:
        lines.append("업로드 대기: " + ", ".join(
            f"{p['ep']}({'로그인 필요' if p.get('wait_login') else datetime.fromtimestamp(p['retry_at']).strftime('%m/%d %H:%M') + ' 재시도'})"
            for p in pend))
    lines += ["", "최근 영상:"]
    for ep, info in reversed(eps):
        yt = "업로드됨" if info.get("youtube") else "업로드 안 됨"
        lines.append(f"· {ep} ({info.get('date', '')}, {info.get('seconds', '?')}초, {yt}) {info.get('hook', '')}")
    if not eps:
        lines.append("· 아직 없음")
    return "\n".join(lines)


def run_story(env, cfg, state, tg, topic=None):
    import story
    try:
        return story.make_story_episode(env, cfg, state, tg, topic)
    except (Exception, SystemExit) as e:
        import traceback
        traceback.print_exc()
        print(f"[사연 실패] {e}")
        tg.send(f"❌ 사연 영상 실패: {str(e)[:400]}")
        return None


def apply_report(cfg, state, rec):
    """분석 추천을 반영 (되돌리기용 백업 저장). 반환: 바뀐 내용 설명"""
    state["learn_backup"] = {k: json.loads(json.dumps(state.get(k)))
                             for k in ("extra_keywords", "disabled_keywords", "style_notes")}
    add = [k for k in rec.get("recommended_keywords", []) if k]
    drop = [k for k in rec.get("drop_keywords", []) if k]
    ex = state.setdefault("extra_keywords", [])
    ex += [k for k in add if k not in ex and k not in cfg.get("keywords", [])]
    state["extra_keywords"] = ex[-30:]
    off = state.setdefault("disabled_keywords", [])
    off += [k for k in drop if k not in off]
    if len(active_keywords(cfg, state)) < 8:          # 검색어가 너무 줄면 제외는 취소
        for k in drop:
            if k in off:
                off.remove(k)
        drop = []
    notes = {"patterns": rec.get("competitor_patterns", [])[:4], "structure": rec.get("structure", ""),
             "hook_examples": rec.get("hook_examples", [])[:5],
             "updated": datetime.now().strftime("%Y-%m-%d")}
    if any(notes[k] for k in ("patterns", "structure", "hook_examples")):
        state["style_notes"] = notes
    rec["recommended_keywords"], rec["drop_keywords"] = [], []
    save_state(state)
    return (f"추가 검색어: {', '.join(add) or '-'}\n제외 검색어: {', '.join(drop) or '-'}\n"
            f"대본·후킹 참고: 경쟁 패턴 {len(notes['patterns'])}개, 후킹 예시 {len(notes['hook_examples'])}개")


def run_report(env, cfg, state, tg):
    import analyzer
    tg.send("📊 채널·경쟁 분석 중... (2~3분)")
    msgs, rec = analyzer.build_report(env, cfg, state, active_keywords(cfg, state))
    state["theme_scores"] = rec.pop("_theme_scores", {}) or state.get("theme_scores", {})
    rec.pop("_watched", None)
    hp = rec.pop("_hook_perf", None)
    if hp:
        state["hook_perf"] = hp
    state["last_report"] = rec
    state["last_report_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    save_state(state)
    auto = cfg.get("auto_apply_report", True)
    for m in msgs:
        if auto and m.startswith("🔧"):
            continue                                   # 자동 반영이면 '적용하세요' 안내 대신 결과를 보냄
        tg.send(m)
    if auto and (rec.get("recommended_keywords") or rec.get("drop_keywords") or rec.get("hook_examples")):
        summary = apply_report(cfg, state, rec)
        tg.send("🤖 이번 주 분석 결과를 자동으로 반영했어요\n" + summary +
                "\n\n다음 영상부터 적용돼요. 마음에 안 들면 '되돌리기'")


def due_weekly(cfg, state, now):
    w = cfg.get("weekly_report", {"weekday": 6, "time": "21:00"})   # 0=월 ... 6=일
    if not w or not w.get("on", True):
        return None
    try:
        hh, mm = (int(x) for x in w.get("time", "21:00").split(":"))
    except ValueError:
        return None
    at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    key = f"{at:%Y-%m-%d}"
    if now.weekday() == int(w.get("weekday", 6)) and at <= now < at + timedelta(hours=12) \
            and key not in state.get("report_done", []):
        return key
    return None


def keep_awake():
    """봇이 켜져 있는 동안 윈도우가 절전 모드로 들어가지 않게 함 (화면은 꺼져도 됨)"""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
        except Exception:
            pass


_lock = None


def single_instance():
    """이미 실행 중이면 False (두 개가 동시에 돌면 텔레그램이 충돌함)"""
    global _lock
    _lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _lock.bind(("127.0.0.1", 47321))
        return True
    except OSError:
        return False


def update_tools():
    """매일 한 번: 다운로드·목소리 도구 최신으로 (사이트가 바뀌어 막히면 보통 업데이트로 풀림)"""
    def ver():
        try:
            from importlib.metadata import version
            return version("edge-tts")
        except Exception:
            return ""
    before = ver()
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-U", "--disable-pip-version-check",
                    "--no-warn-script-location", "yt-dlp", "edge-tts", "gtts"], capture_output=True)
    after = ver()
    if before != after:                             # 새 edge-tts → 다시 불러오기 (리모컨 재시작 없이)
        for name in [m for m in sys.modules if m == "edge_tts" or m.startswith("edge_tts.")]:
            del sys.modules[name]
        print(f"  (edge-tts 업데이트: {before} → {after})")


def handle_command(text, env, cfg, state, tg, ctx):
    t = text.strip()
    low = t.lower().lstrip("/")
    nums = re.findall(r"\d+", t)
    import uploader

    if ctx.get("login_flow"):                         # 로그인 주소 붙여넣기 기다리는 중
        if "localhost" in t or "code=" in t:
            try:
                name = uploader.finish_remote_login(ctx.pop("login_flow"), t)
                tg.send(f"✅ 유튜브 다시 연결됐어요! 채널: {name}")
                for ep_ in [p["ep"] for p in state.get("pending_uploads", []) if p.get("wait_login")]:
                    retry_pending_uploads(tg, state, after_login=True, only=ep_)
            except Exception as e:
                tg.send(f"❌ 로그인 실패: {str(e)[:300]}\n'로그인' 이라고 보내서 처음부터 다시 해 주세요.")
            return
        ctx.pop("login_flow", None)

    if "localhost" in t and "code=" in t:
        tg.send("로그인 요청이 만료됐어요 (리모컨이 다시 켜졌을 수 있어요). '로그인' 이라고 다시 보내 주세요.")
    elif low in ("start", "help", "도움", "도움말", "명령어", "?"):
        tg.send(HELP)
    elif low.startswith(("후보", "find", "찾기")):
        choose_session(env, cfg, state, tg)
    elif low.startswith("자동") and ("켜" in t or "on" in low):
        state["daily_on"] = True
        save_state(state)
        tg.send("✅ 매일 자동 제작을 켰어요.\n" + status_text(cfg, state).split("\n")[2])
    elif low.startswith("자동") and ("끄" in t or "꺼" in t or "off" in low):
        state["daily_on"] = False
        save_state(state)
        tg.send("⏸ 매일 자동 제작을 껐어요. 다시 켜려면 '자동 켜기'")
    elif low.startswith(("자동", "auto")):
        auto_make(env, cfg, state, tg, int(nums[0]) if nums else daily_settings(cfg, state)[1])
    elif low.startswith(("상태", "status")):
        tg.send(status_text(cfg, state))
    elif low.startswith("시간"):
        times = [f"{int(h):02d}:{int(m):02d}" for h, m in re.findall(r"(\d{1,2})[:시](\d{2})", t)
                 if int(h) < 24 and int(m) < 60]
        if not times:
            tg.send("시간을 못 읽었어요. 예: 시간 06:30  /  여러 개: 시간 06:30 18:00")
        else:
            state["daily_times"] = sorted(set(times))
            save_state(state)
            tg.send(f"⏰ 매일 {', '.join(state['daily_times'])} 에 자동 제작할게요.")
    elif low.startswith("개수") and nums:
        state["daily_count"] = max(1, min(int(nums[0]), 6))
        save_state(state)
        tg.send(f"👌 매일 {state['daily_count']}편씩 만들게요.")
    elif low.startswith("차단"):
        ep = f"ep{int(nums[0]):03d}" if nums else ""
        info = state.get("episodes", {}).get(ep)
        if not info:
            tg.send("어느 영상인지 못 찾았어요. 예: 차단 ep005")
            return
        bl = state.setdefault("blocked_channels", [])
        names = []
        for cid, name, src in info.get("channels", []):
            if cid and cid not in bl:
                bl.append(cid)
                names.append(name)
        save_state(state)
        tg.send(f"🚫 {ep} 에 쓴 채널을 앞으로 안 써요: {', '.join(names) or '(유튜브 채널 없음)'}\n"
                "Studio에서 그 영상은 공개하지 말고 삭제해 주세요.")
    elif low.startswith(("업로드", "upload")):
        ep = f"ep{int(nums[0]):03d}" if nums else ""
        video, meta_path = OUTPUT_DIR / f"{ep}_final.mp4", OUTPUT_DIR / f"{ep}_meta.json"
        txt = OUTPUT_DIR / f"{ep}_upload.txt"
        if not video.exists():
            tg.send("그 영상 파일이 없어요. 예: 업로드 ep005")
            return
        meta = (json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists()
                else uploader.meta_from_upload_txt(txt.read_text(encoding="utf-8")) if txt.exists() else None)
        if not meta:
            tg.send("제목 정보 파일이 없어서 올릴 수 없어요.")
            return
        youtube_upload(tg, video, meta, ep, state)
    elif low.startswith("사연") and ("개수" in t):
        n = int(nums[0]) if nums else 1
        state["daily_story"] = max(0, min(n, 6))
        save_state(state)
        tg.send(f"👌 매일 자동 제작 중 {state['daily_story']}편은 사연 영상으로 만들게요.")
    elif low.startswith(("사연", "story")):
        topic = re.sub(r"^(사연|story)\s*", "", t, flags=re.I).strip() or None
        run_story(env, cfg, state, tg, topic)
    elif low in ("업데이트", "update", "버전"):
        import selfupdate
        if selfupdate.check_and_apply(cfg, env, tg, force_msg=True):
            print("새 버전을 받았어요. 다시 시작합니다...")
            sys.exit(0)
    elif low in ("채널", "채널확인", "채널 확인"):
        try:
            title, cid = uploader.channel_info(refresh=True)
            tg.send(f"📺 영상이 올라가는 채널: 「{title}」\nhttps://studio.youtube.com/channel/{cid}/videos/upload\n\n"
                    "다른 채널이면: '로그인' 보내고 → 구글 계정 고른 다음 나오는 '채널 선택' 화면에서 "
                    "올릴 채널(브랜드 계정)을 고르세요.")
        except Exception as e:
            tg.send(f"❌ 채널 확인 실패: {str(e)[:300]}")
    elif low.startswith(("진단", "점검", "조회수")):
        try:
            import analyzer
            tg.send("🩺 최근 영상 상태 확인 중...")
            tg.send(analyzer.diagnose(state))
        except Exception as e:
            tg.send(f"❌ 점검 실패: {str(e)[:300]}")
    elif low.startswith(("분석", "report", "리포트")):
        run_report(env, cfg, state, tg)
    elif low.startswith(("적용", "apply")):
        rec = state.get("last_report", {})
        if not (rec.get("recommended_keywords") or rec.get("drop_keywords") or rec.get("hook_examples")):
            tg.send("반영할 추천이 없어요 (이미 반영됐거나 분석 전이에요). '배운점' 으로 확인해 보세요.")
            return
        tg.send("✅ 반영했어요.\n" + apply_report(cfg, state, rec))
    elif low.startswith(("되돌리기", "undo")):
        bk = state.get("learn_backup")
        if not bk:
            tg.send("되돌릴 내용이 없어요.")
            return
        for k, v in bk.items():
            if v is None:
                state.pop(k, None)
            else:
                state[k] = v
        state.pop("learn_backup", None)
        save_state(state)
        tg.send("↩️ 지난번 분석 반영 전으로 되돌렸어요.")
    elif low.startswith(("배운점", "배운 점", "학습")):
        st, hp = state.get("style_notes") or {}, state.get("hook_perf") or {}
        if not st and not hp:
            tg.send("아직 반영된 분석이 없어요. 매주 일요일 밤 9시에 자동으로 분석해요 (지금 하려면 '분석').")
            return
        lines = [f"🧠 지금 대본에 반영 중인 내용 ({st.get('updated', '')})"]
        if st.get("patterns"):
            lines += ["\n[경쟁 쇼츠 패턴]"] + [f"· {x}" for x in st["patterns"]]
        if st.get("structure"):
            lines += ["\n[전개 방식]", st["structure"]]
        if st.get("hook_examples"):
            lines += ["\n[후킹 예시]"] + [f"· {x}" for x in st["hook_examples"]]
        if hp.get("top"):
            lines += ["\n[우리 채널 잘 된 후킹]"] + [f"· {h} ({v}회)" for h, v, _ in hp["top"]]
        if hp.get("bottom"):
            lines += ["\n[피하는 후킹]"] + [f"· {h} ({v}회)" for h, v, _ in hp["bottom"]]
        tg.send("\n".join(lines))
    elif low.startswith("키워드") and ("제외" in t or "복구" in t or "추가" in t):
        word = re.sub(r"^키워드\s*(제외|복구|추가)\s*", "", t).strip()
        if not word:
            tg.send("예: 키워드 제외 drone crash  /  키워드 추가 shark encounter")
            return
        off = state.setdefault("disabled_keywords", [])
        ex = state.setdefault("extra_keywords", [])
        if "제외" in t:
            off.append(word) if word not in off else None
            msg = f"🚫 '{word}' 을(를) 안 쓸게요."
        else:
            if word in off:
                off.remove(word)
            if word not in ex and word not in cfg.get("keywords", []):
                ex.append(word)
            msg = f"✅ '{word}' 을(를) 쓸게요."
        save_state(state)
        tg.send(msg)
    elif low.startswith(("키워드", "keyword")):
        sc = state.get("theme_scores", {})
        ks = active_keywords(cfg, state)
        tg.send(f"🔑 검색어 {len(ks)}개 (⭐ = 성적 좋아서 더 자주 씀)\n"
                + "\n".join(f"· {k}{' ⭐' if sc.get(k, 0) >= 0.6 else ''}" for k in ks)
                + (f"\n\n제외됨: {', '.join(state.get('disabled_keywords', []))}"
                   if state.get("disabled_keywords") else "")
                + "\n\n다시 넣으려면: 키워드 복구 검색어  /  빼려면: 키워드 제외 검색어")
        return
    elif low.startswith(("로그인", "login")):
        try:
            flow, url = uploader.start_remote_login()
        except Exception as e:
            tg.send(f"❌ {e}")
            return
        ctx["login_flow"] = flow
        tg.send("🔑 유튜브 다시 연결하기\n"
                "1) 아래 주소를 눌러서 '크롬'으로 열기 (텔레그램 안 브라우저면 ⋮ → 크롬에서 열기)\n"
                "2) 채널 계정 선택 → '확인하지 않은 앱'이 나오면 고급 → 이동 → 모두 허용\n"
                "3) 마지막에 '사이트에 연결할 수 없음' 화면이 나오면 정상이에요!\n"
                "   그 화면의 주소창 주소(localhost:8765/?...)를 통째로 복사해서 여기에 보내 주세요.")
        tg.send(url)
    else:
        tg.send("무슨 명령인지 모르겠어요. '도움' 이라고 보내면 명령어를 보여 드려요.")


def bot_main():
    print(f"후킹 숏츠 자동 제작기 {VERSION} — 텔레그램 리모컨 모드")
    if not single_instance():
        print("이미 다른 창에서 실행 중이에요. 이 창은 닫아도 돼요.")
        time.sleep(5)
        sys.exit(3)
    INPUT_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)
    env = load_env()
    cfg = load_json(CONFIG_PATH, {})
    state = load_json(STATE_PATH, {})
    tg = Telegram(env.get("TELEGRAM_TOKEN"), state)
    keep_awake()

    while True:                       # PC 켜자마자 인터넷이 아직 안 될 수 있음
        try:
            tg.connect()
            tg.skip_old()
            break
        except Exception as e:
            print(f"(텔레그램 연결 대기: {e})")
            time.sleep(30)

    times, count, on = daily_settings(cfg, state)
    tg.send(f"🟢 PC에서 hookshorts 리모컨이 켜졌어요.\n"
            f"매일 자동 제작: {'켜짐' if on else '꺼짐'} ({', '.join(times)}, {count}편)\n"
            "'도움' 이라고 보내면 명령어를 보여 드려요.")
    import selfupdate
    if selfupdate.check_and_apply(cfg, env, tg):          # GitHub 에 새 코드가 있으면 받아서 다시 시작
        print("새 버전을 받았어요. 다시 시작합니다...")
        sys.exit(0)
    last_update_check = time.time()
    if cfg.get("cloud_mode"):                            # GitHub 클라우드가 대신 만들고 있으면 PC 리모컨은 쉼
        if not state.get("cloud_notice"):
            tg.send("☁️ 이제 GitHub 클라우드에서 매일 4편 자동으로 만들어요. PC 리모컨은 꺼둘게요.\n"
                    "(PC 를 꺼 둬도 돼요. 텔레그램 메시지는 클라우드가 하루 4번 확인해요)")
            state["cloud_notice"] = True
            save_state(state)
        print("클라우드 모드라서 PC 리모컨은 끕니다. 이 창은 닫아도 돼요.")
        time.sleep(5)
        sys.exit(3)
    print("텔레그램 명령을 기다리는 중... (이 창을 닫으면 리모컨도 꺼져요)")
    ctx = {}
    while True:
        try:
            if time.time() - last_update_check > 1800 and not ctx.get("login_flow"):
                last_update_check = time.time()
                if selfupdate.check_and_apply(cfg, env, tg):
                    print("새 버전을 받았어요. 다시 시작합니다...")
                    sys.exit(0)
            cfg = load_json(CONFIG_PATH, cfg)          # config.json 을 고치면 바로 반영
            key = due_daily(cfg, state, datetime.now())
            if key:
                state.setdefault("daily_done", []).append(key)
                state["daily_done"] = state["daily_done"][-60:]
                save_state(state)
                update_tools()
                total_n = daily_settings(cfg, state)[1]
                n_story = min(int(state.get("daily_story", cfg.get("daily_story_count", 1))), total_n)
                if total_n - n_story > 0:
                    auto_make(env, cfg, state, tg, total_n - n_story, reason=f"매일 {key[-5:]}")
                for _ in range(n_story):
                    run_story(env, cfg, state, tg)
                continue
            wkey = due_weekly(cfg, state, datetime.now())
            if wkey:
                state.setdefault("report_done", []).append(wkey)
                state["report_done"] = state["report_done"][-20:]
                save_state(state)
                run_report(env, cfg, state, tg)
                continue
            if retry_pending_uploads(tg, state):
                continue
            for text in tg.poll(timeout=50):
                print(f"[명령] {text}")
                handle_command(text, env, cfg, state, tg, ctx)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"[오류] {e}")
            try:
                tg.send(f"⚠️ 오류가 났지만 계속 대기할게요: {str(e)[:300]}")
            except Exception:
                pass
            time.sleep(20)


# ================================================================ 메인 (PC에서 직접 실행)
def main():
    print(f"후킹 숏츠 자동 제작기 {VERSION}")
    if not single_instance():
        print("텔레그램 리모컨(start_bot)이 이미 켜져 있어요.\n"
              "텔레그램에서 '후보' 라고 보내면 같은 일을 할 수 있어요.")
        return
    INPUT_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)
    env = load_env()
    cfg = load_json(CONFIG_PATH, {})
    state = load_json(STATE_PATH, {})
    tg = Telegram(env.get("TELEGRAM_TOKEN"), state)
    tg.connect()
    tg.skip_old()
    choose_session(env, cfg, state, tg)


if __name__ == "__main__":
    try:
        bot_main() if "--bot" in sys.argv else main()
    except KeyboardInterrupt:
        print("\n중단했어요.")
