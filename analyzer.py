# -*- coding: utf-8 -*-
"""
채널 분석 + 경쟁 채널 분석

  1) 내 채널: 공개된 영상의 조회수·좋아요·시청 지속률 → 주제(검색어)별 성적
  2) 경쟁 채널: 최근 2주간 한국에서 잘 나간 비슷한 쇼츠 → 제목/후킹 패턴, 급성장 채널
  3) Gemini: 숫자 + 상위 경쟁 영상(직접 시청)을 보고 다음에 할 일을 정리
     → 추천 키워드는 텔레그램에서 '적용' 이라고 보내면 자동 제작에 반영

텔레그램: '분석' / 매주 자동 보고
"""
import json
import re
import statistics
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests

API = "https://www.googleapis.com/youtube/v3/"
HANGUL = re.compile(r"[가-힣]")


# ================================================================ 공통
def iso_seconds(d):
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d or "")
    if not m:
        return 0
    dd, h, mi, s = (int(x or 0) for x in m.groups())
    return dd * 86400 + h * 3600 + mi * 60 + s


def human(n):
    n = int(n or 0)
    if n >= 10000:
        return f"{n / 10000:.1f}만".replace(".0만", "만")
    return f"{n:,}"


def get(endpoint, key, **params):
    r = requests.get(API + endpoint, params={**params, "key": key}, timeout=30).json()
    if "error" in r:
        raise RuntimeError(f"유튜브 API 오류({endpoint}): {r['error'].get('message')}")
    return r


def chunks(xs, n=50):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


# ================================================================ 1) 내 채널
def diagnose(state, n=15):
    """최근 올린 영상이 왜 안 보이는지 점검 (공개 상태·저작권 차단·연령 제한·처리 상태). 반환: 텔레그램 문구"""
    import uploader
    eps = state.get("episodes", {})
    pairs = [(ep, info["youtube"]) for ep, info in eps.items() if info.get("youtube")][-n:]
    if not pairs:
        return "봇으로 올린 영상 기록이 없어요."
    yt = uploader.service()
    got = {}
    for part in chunks([v for _, v in pairs]):
        r = yt.videos().list(part="status,contentDetails,statistics,snippet", id=",".join(part)).execute()
        got.update({it["id"]: it for it in r.get("items", [])})
    lines, issues = [], defaultdict(int)
    for ep, vid in pairs:
        it = got.get(vid)
        if not it:
            lines.append(f"{ep}: ❓ 삭제됐거나 찾을 수 없음")
            continue
        st, cd = it.get("status", {}), it.get("contentDetails", {})
        views = int(it.get("statistics", {}).get("viewCount", 0) or 0)
        secs = iso_seconds(cd.get("duration"))
        flags = []
        priv = st.get("privacyStatus")
        if priv != "public":
            flags.append("🔒 비공개" if priv == "private" else "🔗 일부공개")
            issues["private"] += 1
            if st.get("publishAt"):
                flags.append(f"예약 {st['publishAt'][:16]}")
        if st.get("uploadStatus") not in ("processed", None):
            flags.append(f"⚠️ 처리 상태 {st.get('uploadStatus')} {st.get('rejectionReason') or st.get('failureReason') or ''}")
            issues["upload"] += 1
        rr = cd.get("regionRestriction") or {}
        if "allowed" in rr and not rr["allowed"] or len(rr.get("blocked", [])) > 50:
            flags.append("⛔ 전 세계 차단 (저작권)")
            issues["blocked"] += 1
        elif "KR" in rr.get("blocked", []) or ("allowed" in rr and "KR" not in rr["allowed"]):
            flags.append("⛔ 한국 차단 (저작권)")
            issues["blocked"] += 1
        elif rr:
            flags.append(f"일부 국가 차단 {len(rr.get('blocked', []))}개")
        if (cd.get("contentRating") or {}).get("ytRating") == "ytAgeRestricted":
            flags.append("🔞 연령 제한")
            issues["age"] += 1
        if st.get("madeForKids"):
            flags.append("👶 아동용 (추천 거의 안 됨)")
            issues["kids"] += 1
        if secs > 180:
            flags.append("⏱ 3분 초과 → 쇼츠 아님")
            issues["long"] += 1
        theme = eps[ep].get("theme", "")
        lines.append(f"{ep} ({theme}, {secs}초) 조회 {views} — " + (" · ".join(flags) if flags else "✅ 정상 공개"))
    tips = []
    if issues["private"]:
        tips.append(f"🔒 {issues['private']}개가 아직 공개가 아니에요. 비공개 영상의 조회수 0~3은 본인이 본 거예요.\n"
                    "   Studio → 콘텐츠에서 '공개'로 바꿔 주세요. 자물쇠(잠김) 표시가 있으면 구글 미인증 앱 업로드라 "
                    "공개 전환이 막힌 거예요 → 이 경우 알려 주세요.")
    if issues["blocked"]:
        tips.append(f"⛔ {issues['blocked']}개가 저작권(Content ID) 때문에 차단됐어요. 1분 넘는 쇼츠는 신고 하나만 걸려도 "
                    "재생이 막혀요. Studio → 콘텐츠 → '제한사항' 칸에서 어떤 소리/영상이 걸렸는지 확인해 주세요.")
    if issues["age"]:
        tips.append("🔞 연령 제한 영상은 쇼츠 피드에 거의 안 떠요 (사고·부상 장면).")
    if issues["kids"]:
        tips.append("👶 '아동용'으로 설정된 영상은 추천이 크게 줄어요.")
    if not tips:
        tips.append("✅ 기술적인 차단은 없어요. 그렇다면 알고리즘 문제예요: 초반 2~3초 이탈률(스와이프)이 높거나, "
                    "비슷한 영상을 짧은 시간에 몰아서 올렸을 때 노출이 줄어요. Studio → 분석 → '조회함 vs 스와이프' 비율을 봐 주세요.")
    return "🩺 최근 영상 점검\n\n" + "\n".join(lines) + "\n\n" + "\n\n".join(tips)


def my_channel(state):
    """봇이 올린 영상들의 성적. 반환: dict"""
    import uploader
    eps = state.get("episodes", {})
    ids = {info["youtube"]: ep for ep, info in eps.items() if info.get("youtube")}
    if not ids:
        return {"videos": [], "note": "아직 봇으로 올린 영상이 없어요."}
    yt = uploader.service()
    rows = []
    for part in chunks(list(ids)):
        r = yt.videos().list(part="statistics,status,snippet,contentDetails", id=",".join(part)).execute()
        for it in r.get("items", []):
            ep = ids[it["id"]]
            st = it.get("statistics", {})
            rows.append({
                "ep": ep, "id": it["id"], "title": it["snippet"]["title"],
                "public": it["status"].get("privacyStatus") == "public",
                "published": it["snippet"].get("publishedAt", "")[:10],
                "views": int(st.get("viewCount", 0) or 0), "likes": int(st.get("likeCount", 0) or 0),
                "comments": int(st.get("commentCount", 0) or 0),
                "seconds": iso_seconds(it["contentDetails"].get("duration")),
                "theme": eps[ep].get("theme") or eps[ep].get("keyword") or "(기록 없음)",
                "hook": eps[ep].get("hook", ""),
            })
    deleted = [ids[i] for i in ids if i not in {r["id"] for r in rows}]

    # 시청 지속률 (yt-analytics 권한이 있을 때만)
    retention_note = ""
    if uploader.has_analytics():
        try:
            from googleapiclient.discovery import build
            ya = build("youtubeAnalytics", "v2", credentials=uploader.get_credentials(), cache_discovery=False)
            end = datetime.now().date()
            r = ya.reports().query(ids="channel==MINE", startDate=str(end - timedelta(days=90)),
                                   endDate=str(end), dimensions="video", sort="-views", maxResults=200,
                                   metrics="views,averageViewDuration,averageViewPercentage,subscribersGained"
                                   ).execute()
            heads = [h["name"] for h in r.get("columnHeaders", [])]
            stats = {row[0]: dict(zip(heads, row)) for row in r.get("rows", [])}
            for row in rows:
                s = stats.get(row["id"])
                if s:
                    row["avg_pct"] = round(float(s.get("averageViewPercentage", 0)), 1)
                    row["avg_sec"] = round(float(s.get("averageViewDuration", 0)), 1)
                    row["subs_gained"] = int(s.get("subscribersGained", 0))
        except Exception as e:
            msg = str(e)
            if "accessNotConfigured" in msg or "has not been used" in msg or "disabled" in msg:
                retention_note = ("시청 지속률을 보려면 구글 클라우드 hookshorts 프로젝트에서 "
                                  "'YouTube Analytics API'를 '사용'으로 켜 주세요.")
            else:
                retention_note = f"시청 지속률을 못 가져왔어요: {msg[:150]}"
    else:
        retention_note = "시청 지속률은 다음에 텔레그램 '로그인'을 한 번 더 하면 보여요 (분석 권한 추가)."

    pub = [r for r in rows if r["public"]]
    by_theme = defaultdict(list)
    for r in pub:
        by_theme[r["theme"]].append(r)
    themes = []
    for th, rs in by_theme.items():
        pcts = [r["avg_pct"] for r in rs if "avg_pct" in r]
        themes.append({"theme": th, "count": len(rs),
                       "median_views": int(statistics.median([r["views"] for r in rs])),
                       "avg_pct": round(sum(pcts) / len(pcts), 1) if pcts else None})
    themes.sort(key=lambda t: -t["median_views"])
    return {"videos": rows, "public": pub, "themes": themes, "deleted": deleted,
            "retention_note": retention_note}


# ================================================================ 2) 경쟁 채널
def competitors(key, cfg):
    """최근 N일 한국 쇼츠 중 우리와 비슷한 '아찔한 순간/결말' 류 인기 영상"""
    days = int(cfg.get("competitor_days", 14))
    after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    queries = cfg.get("competitor_queries", [])[:int(cfg.get("competitor_queries_per_report", 6))]
    found = {}
    for q in queries:
        try:
            r = get("search", key, part="snippet", q=q, type="video", videoDuration="short",
                    regionCode="KR", relevanceLanguage="ko", order="viewCount",
                    publishedAfter=after, maxResults=25)
        except Exception as e:
            print(f"   (경쟁 검색 실패 {q}: {e})")
            continue
        for it in r.get("items", []):
            vid = it["id"].get("videoId")
            if vid and HANGUL.search(it["snippet"]["title"]):
                found.setdefault(vid, q)
    if not found:
        return {"videos": [], "channels": []}

    videos = []
    for part in chunks(list(found)):
        r = get("videos", key, part="snippet,statistics,contentDetails", id=",".join(part))
        for it in r.get("items", []):
            sec = iso_seconds(it["contentDetails"].get("duration"))
            if sec > 180:
                continue
            st, sn = it.get("statistics", {}), it["snippet"]
            videos.append({"id": it["id"], "title": sn["title"], "channel": sn["channelTitle"],
                           "channel_id": sn["channelId"], "published": sn["publishedAt"][:10],
                           "views": int(st.get("viewCount", 0) or 0), "likes": int(st.get("likeCount", 0) or 0),
                           "comments": int(st.get("commentCount", 0) or 0), "seconds": sec,
                           "query": found[it["id"]],
                           "url": f"https://www.youtube.com/shorts/{it['id']}"})
    ch_ids = list({v["channel_id"] for v in videos})
    chans = {}
    for part in chunks(ch_ids):
        r = get("channels", key, part="snippet,statistics", id=",".join(part))
        for it in r.get("items", []):
            st = it.get("statistics", {})
            chans[it["id"]] = {"channel": it["snippet"]["title"],
                               "subs": int(st.get("subscriberCount", 0) or 0),
                               "videos": int(st.get("videoCount", 0) or 0),
                               "total_views": int(st.get("viewCount", 0) or 0),
                               "created": it["snippet"].get("publishedAt", "")[:10],
                               "url": f"https://www.youtube.com/channel/{it['id']}"}
    for v in videos:
        c = chans.get(v["channel_id"], {})
        v["subs"] = c.get("subs", 0)
        v["ratio"] = round(v["views"] / max(c.get("subs", 0), 100), 1)   # 구독자 대비 조회수 (급성장 신호)
    videos.sort(key=lambda v: -v["views"])

    per_ch = defaultdict(list)
    for v in videos:
        per_ch[v["channel_id"]].append(v)
    channels = []
    for cid, vs in per_ch.items():
        c = chans.get(cid, {})
        channels.append({**c, "hits": len(vs), "best_views": max(v["views"] for v in vs),
                         "median_views": int(statistics.median([v["views"] for v in vs]))})
    # 구독자가 적은데 조회수가 큰 채널 = 지금 따라 하기 좋은 채널
    channels.sort(key=lambda c: -(c["best_views"] / max(c.get("subs", 0), 100)))
    return {"videos": videos[:40], "channels": channels[:12]}


# ================================================================ 3) Gemini 정리
REPORT_PROMPT = """너는 한국 유튜브 쇼츠 성장 컨설턴트야. 아래 데이터는 '해외 CC 영상 + 후킹 문구 + 한국어 해설 나레이션'
형식의 쇼츠 채널(부업, 수익화 목표)의 성적과, 최근 2주간 한국에서 잘 된 비슷한 쇼츠들이야.
첨부한 영상은 경쟁 쇼츠 중 상위 영상이야 (볼 수 있으면 직접 보고 구성을 분석해).

[우리 채널]
{mine}

[경쟁 쇼츠 상위]
{comp}

[경쟁 채널 (구독자 대비 조회수 높은 순)]
{chans}

[현재 자동 제작 검색어 (영어, 유튜브 CC 영상 검색용)]
{keywords}

데이터에 있는 것만 근거로 말하고, 데이터가 적으면 '데이터 부족'이라고 솔직히 말해. 지어내지 마.
반드시 아래 JSON만 출력해:
{{
 "one_line": "이번 주 핵심 한 줄",
 "my_channel": ["우리 채널 분석 3줄 이내 (잘 된 주제/안 된 주제, 시청 지속률 해석)"],
 "competitor_patterns": ["경쟁 쇼츠에서 반복되는 제목·후킹·전개·길이 패턴 4개 이내, 구체적 예시 포함"],
 "structure": "상위 경쟁 영상의 전개 방식 (처음 3초, 중간, 결말 처리, 자막·나레이션 스타일) 3문장 이내",
 "hook_examples": ["우리 형식에 바로 쓸 수 있는 후킹 문구 예시 5개 (hook1 / hook2 형태)"],
 "recommended_keywords": ["새로 추가할 영어 검색어 5개 이내 - 유튜브 CC 영상이 실제로 많을 법한 구체적인 것"],
 "drop_keywords": ["현재 검색어 중 빼는 게 좋은 것 (근거 있을 때만, 없으면 빈 배열)"],
 "actions": ["다음 1주일 동안 할 일 3개 (구체적으로)"]
}}
"""


def summarize(env, cfg, mine, comp, keywords):
    from google.genai import types
    import finder                                    # Gemini 클라이언트·모델 순서 재사용
    client = finder.gemini_client(env)
    models = finder.model_order(client, cfg.get("gemini_model", "gemini-flash-latest"))

    def mine_text():
        if not mine.get("public"):
            return "공개된 영상 없음 (데이터 부족)"
        lines = ["주제별: " + "; ".join(
            f"{t['theme']} {t['count']}편 중앙값 {t['median_views']}회"
            + (f" 지속률 {t['avg_pct']}%" if t["avg_pct"] is not None else "") for t in mine["themes"])]
        for r in sorted(mine["public"], key=lambda r: -r["views"])[:15]:
            lines.append(f"- {r['title']} | 조회 {r['views']} 좋아요 {r['likes']} | {r['seconds']}초"
                         + (f" | 평균 시청 {r['avg_pct']}%" if "avg_pct" in r else "")
                         + f" | 후킹 '{r['hook']}' | 주제 {r['theme']}")
        return "\n".join(lines)

    comp_text = "\n".join(f"- {v['title']} | {v['channel']}(구독 {v['subs']}) | 조회 {v['views']} "
                          f"좋아요 {v['likes']} 댓글 {v['comments']} | {v['seconds']}초 | {v['published']}"
                          for v in comp.get("videos", [])[:30]) or "없음"
    chan_text = "\n".join(f"- {c.get('channel')} | 구독 {c.get('subs')} | 영상 {c.get('videos')}개 | "
                          f"최고 {c['best_views']}회 | 개설 {c.get('created')}" for c in comp.get("channels", [])) or "없음"
    prompt = REPORT_PROMPT.format(mine=mine_text(), comp=comp_text, chans=chan_text,
                                  keywords=", ".join(keywords))
    conf = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.4)

    tops = [v["url"].replace("/shorts/", "/watch?v=") for v in comp.get("videos", [])[:3]]
    attempts = []
    if tops and cfg.get("analyze_competitor_videos", True):
        attempts.append([types.Part.from_uri(file_uri=u, mime_type="video/*") for u in tops] + [prompt])
    attempts.append([prompt])                        # 영상 보기가 안 되면 숫자만으로
    last = None
    for contents in attempts:
        for model in models:
            try:
                resp = client.models.generate_content(model=model, contents=contents, config=conf)
                text = re.sub(r"^```(?:json)?|```$", "", (resp.text or "").strip()).strip()
                out = json.loads(text)
                out["_watched"] = len(contents) > 1
                return out
            except Exception as e:
                last = e
                print(f"   (분석 재시도: {str(e)[:100]})")
                time.sleep(3)
    raise RuntimeError(f"Gemini 분석 실패: {str(last)[:200]}")


# ================================================================ 보고서
def theme_scores(mine):
    """주제별 점수 0~1 (자동 제작 때 잘 되는 주제를 더 자주 고르는 데 사용)"""
    ts = [t for t in mine.get("themes", []) if t["count"] >= 2]
    if len(ts) < 2:
        return {}
    vals = [t["median_views"] for t in ts]
    lo, hi = min(vals), max(vals)
    return {t["theme"]: round((t["median_views"] - lo) / (hi - lo), 2) if hi > lo else 0.5 for t in ts}


def build_report(env, cfg, state, keywords):
    """반환: (텔레그램 메시지 목록, 추천 dict)"""
    msgs = []
    try:
        mine = my_channel(state)
    except Exception as e:
        mine = {"videos": [], "public": [], "themes": [], "retention_note": f"내 채널 정보를 못 가져왔어요: {e}"}
    comp = competitors(env.get("YOUTUBE_KEY", ""), cfg) if env.get("YOUTUBE_KEY") else {"videos": [], "channels": []}

    # ---- 숫자 요약 (Gemini 없이도 보이는 부분)
    pub = mine.get("public", [])
    head = [f"📈 주간 채널 분석 ({datetime.now():%m/%d})", ""]
    head.append(f"▶ 내 채널: 업로드 {len(mine.get('videos', []))}편 · 공개 {len(pub)}편 · "
                f"총 조회 {human(sum(r['views'] for r in pub))}")
    for medal, r in zip("🥇🥈🥉", sorted(pub, key=lambda r: -r["views"])[:3]):
        pct = f" · 지속 {r['avg_pct']}%" if "avg_pct" in r else ""
        head.append(f"  {medal} {r['ep']} {human(r['views'])}회{pct} — {r['title'][:30]}")
    if len(pub) >= 4:
        for r in sorted(pub, key=lambda r: r["views"])[:2]:
            head.append(f"  🔻 {r['ep']} {human(r['views'])}회 — {r['title'][:30]}")
    if mine.get("themes"):
        head.append("  주제별(조회수 중앙값): " + ", ".join(
            f"{t['theme']} {human(t['median_views'])}({t['count']}편)" for t in mine["themes"][:6]))
    if mine.get("retention_note"):
        head.append(f"  ℹ️ {mine['retention_note']}")
    head.append("")
    head.append(f"▶ 경쟁 쇼츠 (최근 {cfg.get('competitor_days', 14)}일, 비슷한 주제 한국 쇼츠)")
    for v in comp.get("videos", [])[:5]:
        head.append(f"  · {human(v['views'])}회 | {v['title'][:34]} — {v['channel']}({human(v['subs'])})")
    if comp.get("channels"):
        head.append("  🚀 구독자 대비 잘 나가는 채널:")
        for c in comp["channels"][:3]:
            head.append(f"   - {c.get('channel')} (구독 {human(c.get('subs'))}, 최고 {human(c['best_views'])}회) {c.get('url', '')}")
    msgs.append("\n".join(head))

    # ---- Gemini 정리
    rec = {}
    try:
        rec = summarize(env, cfg, mine, comp, keywords)
        g = [f"🧠 분석 정리{' (경쟁 영상 직접 시청)' if rec.get('_watched') else ''}",
             f"\n💡 {rec.get('one_line', '')}"]
        for title, k in (("내 채널", "my_channel"), ("경쟁 쇼츠 패턴", "competitor_patterns"),
                         ("이번 주 할 일", "actions")):
            if rec.get(k):
                g.append(f"\n[{title}]")
                g += [f"· {x}" for x in rec[k]]
        if rec.get("structure"):
            g.append(f"\n[잘 되는 영상의 전개]\n{rec['structure']}")
        if rec.get("hook_examples"):
            g.append("\n[바로 쓸 후킹 예시]")
            g += [f"· {x}" for x in rec["hook_examples"]]
        msgs.append("\n".join(g))
        add, drop = rec.get("recommended_keywords", []), rec.get("drop_keywords", [])
        if add or drop:
            msgs.append("🔧 자동 제작 검색어 추천\n"
                        + (f"추가: {', '.join(add)}\n" if add else "")
                        + (f"제외: {', '.join(drop)}\n" if drop else "")
                        + "\n반영하려면 '적용' 이라고 보내 주세요.")
    except Exception as e:
        msgs.append(f"⚠️ AI 정리는 실패했어요 (숫자 요약만 봐 주세요): {str(e)[:200]}")
    rec["_theme_scores"] = theme_scores(mine)
    ranked = sorted([r for r in mine.get("public", []) if r.get("hook")], key=lambda r: -r["views"])
    if len(ranked) >= 6:                       # 공개 영상이 충분할 때만 잘 된/안 된 후킹을 학습
        rec["_hook_perf"] = {"top": [(r["hook"], r["views"], r.get("avg_pct")) for r in ranked[:3]],
                             "bottom": [(r["hook"], r["views"], r.get("avg_pct")) for r in ranked[-3:]]}
    return msgs, rec
