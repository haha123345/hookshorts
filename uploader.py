# -*- coding: utf-8 -*-
"""
유튜브 자동 업로드 (공식 YouTube Data API, 무료)

  ※ 구글 심사를 받지 않은 개인 프로젝트는 '비공개'로만 올라가요.
     업로드 후 YouTube Studio 앱에서 공개/예약으로 바꿔 주세요.

처음 한 번:  youtube_login.bat  (브라우저에서 새 채널 계정으로 로그인)
수동 업로드: python uploader.py ep001     (output/ep001_final.mp4 + ep001_meta.json)
자동 업로드: finder.py 가 영상을 다 만들면 알아서 호출
"""
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
OUTPUT_DIR = BASE / "output"
CLIENT_SECRET = BASE / "client_secret.json"
TOKEN = BASE / "youtube_token.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly",
          "https://www.googleapis.com/auth/yt-analytics.readonly"]   # 시청 지속률 분석용
ANALYTICS_SCOPE = SCOPES[2]


class NotReady(Exception):
    """업로드 준비(설정 파일/로그인)가 안 된 상태"""


def find_client_secret():
    if CLIENT_SECRET.exists():
        return CLIENT_SECRET
    # 구글에서 받은 파일 이름 그대로 넣어도 찾아 줌 (client_secret_xxxx.apps.googleusercontent.com.json)
    for p in sorted(BASE.glob("client_secret*.json")):
        return p
    return None


def get_credentials(interactive=False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = None
    if TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN))
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            TOKEN.write_text(creds.to_json(), encoding="utf-8")
            return creds
        except Exception:
            creds = None
    if not interactive:
        raise NotReady("유튜브 로그인이 필요해요. youtube_login.bat 을 실행해 주세요.")

    secret = find_client_secret()
    if not secret:
        raise NotReady("client_secret.json 파일이 없어요. 구글 클라우드에서 받은 OAuth 파일을 "
                       f"이 폴더에 넣어 주세요: {BASE}")
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
    print("브라우저가 열리면 '새 채널' 구글 계정으로 로그인하고 허용을 눌러 주세요.")
    print("('Google에서 확인하지 않은 앱' 화면이 나오면 → 고급 → 이동(안전하지 않음) 을 누르면 돼요)")
    creds = flow.run_local_server(port=0, prompt="consent",
                                  success_message="로그인 완료! 이 창은 닫아도 돼요.")
    TOKEN.write_text(creds.to_json(), encoding="utf-8")
    return creds


# ---------------------------------------------------------------- 폰으로 로그인 (텔레그램)
REMOTE_REDIRECT = "http://localhost:8765/"


def start_remote_login():
    """폰에서 열 로그인 주소를 만듦. 반환: (flow, url)"""
    secret = find_client_secret()
    if not secret:
        raise NotReady("client_secret.json 파일이 PC의 hookshorts 폴더에 없어요.")
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES, redirect_uri=REMOTE_REDIRECT)
    url, _ = flow.authorization_url(prompt="consent", access_type="offline")
    return flow, url


def finish_remote_login(flow, pasted):
    """로그인 후 폰 주소창의 localhost:8765/?...code=... 주소(또는 code)를 받아 로그인 완료"""
    from urllib.parse import parse_qs, urlparse
    pasted = pasted.strip()
    if "code=" in pasted:
        code = parse_qs(urlparse(pasted).query).get("code", [None])[0]
    elif "://" in pasted or "localhost" in pasted:
        code = None                       # 주소인데 code 가 없음 (로그인 취소 등)
    else:
        code = pasted
    if not code or " " in code or len(code) < 10:
        raise ValueError("주소에서 code 를 찾지 못했어요")
    flow.fetch_token(code=code)
    TOKEN.write_text(flow.credentials.to_json(), encoding="utf-8")
    _CHANNEL.clear()
    from googleapiclient.discovery import build
    return channel_name(build("youtube", "v3", credentials=flow.credentials, cache_discovery=False))


def login_status():
    """(ok, 설명)"""
    try:
        get_credentials()
        return True, "연결됨"
    except NotReady as e:
        return False, str(e)
    except Exception as e:
        return False, f"확인 실패: {e}"


def has_analytics():
    try:
        return ANALYTICS_SCOPE in (get_credentials().scopes or [])
    except Exception:
        return False


def service(interactive=False):
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=get_credentials(interactive), cache_discovery=False)


def channel_name(yt):
    r = yt.channels().list(part="snippet", mine=True).execute()
    items = r.get("items", [])
    return items[0]["snippet"]["title"] if items else "(채널 없음 - 유튜브에서 채널을 먼저 만들어 주세요)"


_CHANNEL = {}


def channel_info(refresh=False):
    """로그인된 업로드 대상 채널: (채널 이름, 채널 ID). 브랜드 계정이면 로그인 때 고른 채널."""
    if refresh or not _CHANNEL:
        r = service().channels().list(part="snippet", mine=True).execute()
        items = r.get("items", [])
        _CHANNEL.clear()
        _CHANNEL.update(title=items[0]["snippet"]["title"] if items else "(채널 없음)",
                        id=items[0]["id"] if items else "")
    return _CHANNEL["title"], _CHANNEL["id"]


def clean_meta(meta):
    """유튜브 규칙에 맞게 정리: 제목 100자, 설명 5000자, < > 사용 불가, 태그 합계 500자"""
    def strip_brackets(t):
        return re.sub(r"[<>]", "", t or "").strip()
    title = strip_brackets(meta.get("title", "")).replace("{", "").replace("}", "")
    if meta.get("long"):                                   # 롱폼은 #shorts 를 붙이지 않음
        title = re.sub(r"\s*#shorts", "", title, flags=re.I)[:100]
    elif "#shorts" not in title.lower():
        title = (title + " #shorts").strip()
    if len(title) > 100:
        title = title[:100 - len(" #shorts")].rstrip() + " #shorts"
    desc = strip_brackets(meta.get("description", ""))[:5000]
    tags, total = [], 0
    for t in meta.get("tags", []):
        t = strip_brackets(t).lstrip("#").strip()
        if t and total + len(t) + 1 <= 480:
            tags.append(t)
            total += len(t) + 1
    return title, desc, tags


PUBLISH_AT = {"at": None}      # 클라우드가 정해 주는 자동 공개 시각 (datetime, 없으면 비공개로만)
LAST = {"scheduled": None}


def upload(video_path, meta, progress=print):
    """비공개로 업로드하고 영상 ID를 반환 (PUBLISH_AT 이 있으면 그 시각에 자동 공개 예약)"""
    from datetime import datetime, timedelta, timezone
    from googleapiclient.http import MediaFileUpload
    yt = service()
    title, desc, tags = clean_meta(meta)
    at = meta.get("publish_at") or PUBLISH_AT["at"]
    if at is not None:
        now = datetime.now(timezone.utc)
        if at <= now + timedelta(minutes=10):              # 제작이 늦어져 시간이 지났으면 15분 뒤 공개
            at = now + timedelta(minutes=15)
    LAST["scheduled"] = at
    body = {
        "snippet": {"title": title, "description": desc, "tags": tags,
                    "categoryId": meta.get("category", "24"),          # 24 = 엔터테인먼트
                    "defaultLanguage": "ko", "defaultAudioLanguage": "ko"},
        "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False,
                   "embeddable": True},
    }
    if at is not None:
        body["status"]["publishAt"] = at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    media = MediaFileUpload(str(video_path), mimetype="video/mp4",
                            chunksize=8 * 1024 * 1024, resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp, last = None, -1
    while resp is None:
        status, resp = req.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            if pct // 25 != last // 25:
                progress(f"  유튜브 업로드 {pct}%")
            last = pct
    return resp["id"]


def meta_from_upload_txt(text):
    """예전 버전이 만든 epXXX_upload.txt 에서 제목/설명/태그 읽기"""
    text = text.replace("\r", "")
    m = re.search(r"\[제목\]\n(.+?)\n\n\[설명\]\n(.*)", text, re.S)        # 옛 형식
    if m:
        title, desc = m.group(1).strip(), m.group(2).strip()
    else:                                                               # 새 형식
        body = re.split(r"\n\[틱톡\]", text)[0]
        body = re.sub(r"^\[유튜브[^\]]*\]\n", "", body.strip())
        title, _, desc = body.partition("\n")
        desc = desc.strip()
    tags = re.findall(r"#([^\s#]+)", desc)
    tags = [t for t in tags if t.lower() != "shorts"]
    return {"title": title.strip(), "description": desc, "tags": tags}


def explain_error(e):
    """업로드 오류를 한국어로. 반환: (설명, 몇 시간 뒤 다시 시도할지 또는 None, 로그인 필요 여부)"""
    t = f"{type(e).__name__} {e}"
    low = t.lower()
    if "uploadlimitexceeded" in low or "exceeded the number of videos" in low:
        return ("채널의 '하루 업로드 개수 한도'를 넘었어요. 새 채널은 한도가 낮고, 오늘 테스트로 많이 올려서 그래요.\n"
                "한도는 '최근 24시간' 기준이라 조금씩 풀려요. 4시간마다 조용히 다시 시도하고, 올라가면 알려 드릴게요.",
                4, False)
    if "quotaexceeded" in low or "dailylimitexceeded" in low or "ratelimitexceeded" in low:
        return ("유튜브 API 하루 사용량을 다 썼어요. 매일 오후 4~5시(한국 시간)에 초기화돼요.\n"
                "초기화 뒤 자동으로 다시 올릴게요.", 6, False)
    if "invalid_grant" in low or "refresherror" in low or "token has been expired or revoked" in low:
        return ("유튜브 로그인이 만료됐어요. 텔레그램에 '로그인' 이라고 보내서 다시 연결하면 자동으로 올릴게요.",
                None, True)
    if "insufficientpermissions" in low or "insufficient authentication scopes" in low or "forbidden" in low:
        return ("업로드 권한이 없어요. '로그인'으로 다시 연결할 때 권한 체크박스를 모두 체크해 주세요.", None, True)
    if "youtubesignuprequired" in low:
        return ("로그인한 구글 계정에 유튜브 채널이 없어요. 채널 계정으로 '로그인' 해 주세요.", None, True)
    if any(k in low for k in ("timed out", "timeout", "connection", "temporarily", "503", "500", "backenderror")):
        return ("인터넷이나 유튜브 서버가 잠깐 불안정했어요. 1시간 뒤 자동으로 다시 시도할게요.", 1, False)
    return (f"알 수 없는 오류: {t[:250]}\n3시간 뒤 한 번 더 자동으로 시도할게요.", 3, False)


def links(video_id):
    return (f"https://youtu.be/{video_id}",
            f"https://studio.youtube.com/video/{video_id}/edit")


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--login":
        try:
            yt = service(interactive=True)
            print(f"\n✅ 유튜브 연결 완료! 채널: {channel_name(yt)}")
            print("이제 find_videos.bat 으로 만든 영상이 이 채널에 비공개로 자동 업로드돼요.")
        except NotReady as e:
            print(f"\n❌ {e}")
        return

    ep = args[0].replace("_final.mp4", "").replace(".mp4", "")
    video = OUTPUT_DIR / f"{ep}_final.mp4"
    meta_path = OUTPUT_DIR / f"{ep}_meta.json"
    txt_path = OUTPUT_DIR / f"{ep}_upload.txt"
    if not video.exists():
        sys.exit(f"{video.name} 이 output 폴더에 없어요.")
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    elif txt_path.exists():
        meta = meta_from_upload_txt(txt_path.read_text(encoding="utf-8"))
    else:
        sys.exit(f"{meta_path.name} / {txt_path.name} 이 없어서 제목을 알 수 없어요.")
    print(f"제목: {meta['title']}")
    try:
        vid = upload(video, meta)
    except NotReady as e:
        sys.exit(f"❌ {e}")
    watch, studio = links(vid)
    print(f"\n✅ 비공개 업로드 완료\n영상: {watch}\nStudio에서 공개로 바꾸기: {studio}")


if __name__ == "__main__":
    main()
