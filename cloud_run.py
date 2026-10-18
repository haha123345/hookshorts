# -*- coding: utf-8 -*-
"""
GitHub Actions 에서 PC 없이 자동 제작·업로드 (하루 4편)

  python cloud_run.py           → 지금 시각에 맞는 칸(config.json 의 cloud_schedule)의 영상 1편
  python cloud_run.py story     → 사연 영상 1편
  python cloud_run.py clip      → 해외 클립 영상 1편 (실패하면 사연 영상으로 대신)
  python cloud_run.py report    → 주간 채널·경쟁 분석

텔레그램: 실행할 때마다 그동안 온 메시지를 확인해서
  '사연 주제/링크' 는 다음 사연 영상 소재로 예약, '진단'·'상태'·'도움' 은 바로 답장
"""
import re
import sys
from datetime import datetime, timedelta, timezone

import finder

KST = timezone(timedelta(hours=9))

CLOUD_HELP = """☁️ 클라우드 자동 제작 (PC 꺼져 있어도 매일 4편)

텔레그램 메시지는 다음 제작 때(하루 4번) 확인해요.
사연 고양이 구조 — 다음 사연 영상 소재로 예약
사연 https://기사링크 — 그 기사로 다음 사연 영상
진단 — 최근 영상 점검 (비공개·저작권 차단 등)
상태 — 오늘 만든 영상과 다음 예정
도움 — 이 안내

바로 만들기: GitHub 저장소 → Actions → hookshorts 자동 제작 → Run workflow"""


def now_kst():
    return datetime.now(KST)


def slot_type(cfg):
    """지금 시각에 해당하는 칸 (GitHub 예약 실행은 수십 분 늦을 수 있어서 가장 가까운 이전 칸)"""
    sched = cfg.get("cloud_schedule") or []
    now = now_kst()
    mins = now.hour * 60 + now.minute
    best, best_gap = None, None
    for s in sched:
        h, m = (int(x) for x in s["time"].split(":"))
        gap = (mins - (h * 60 + m)) % (24 * 60)
        if best_gap is None or gap < best_gap:
            best, best_gap = s, gap
    return (best or {}).get("type", "story"), (best or {}).get("time", "")


def handle_messages(env, cfg, state, tg):
    """지난 실행 이후 온 텔레그램 메시지 처리"""
    try:
        msgs = tg.poll(timeout=1)
    except Exception as e:
        print(f"(텔레그램 메시지 확인 실패: {e})")
        return
    for text in msgs:
        t = text.strip()
        low = t.lower().lstrip("/")
        print(f"[메시지] {t}")
        if low.startswith(("사연", "story")) and "개수" not in t:
            topic = re.sub(r"^(사연|story)\s*", "", t, flags=re.I).strip()
            if topic and not re.fullmatch(r"[\d\s개편]*", topic):
                q = state.setdefault("story_requests", [])
                q.append(topic)
                tg.send(f"📝 다음 사연 영상 소재로 예약했어요: {topic}\n(대기 {len(q)}개)")
        elif low.startswith(("진단", "점검", "조회수")):
            try:
                import analyzer
                tg.send(analyzer.diagnose(state))
            except Exception as e:
                tg.send(f"❌ 점검 실패: {str(e)[:300]}")
        elif low.startswith(("상태", "status")):
            today = now_kst().strftime("%Y-%m-%d")
            eps = [(ep, i) for ep, i in state.get("episodes", {}).items() if i.get("date", "").startswith(today)]
            lines = [f"☁️ 클라우드 자동 제작 ({finder.VERSION})",
                     "오늘 만든 영상: " + (", ".join(f"{ep}({i.get('theme', '')})" for ep, i in eps) or "아직 없음"),
                     "하루 일정: " + " · ".join(f"{s['time']} {'사연' if s['type'] == 'story' else '해외 클립'}"
                                             for s in cfg.get("cloud_schedule", [])),
                     f"예약된 사연 소재: {len(state.get('story_requests', []))}개"]
            tg.send("\n".join(lines))
        elif low in ("start", "help", "도움", "도움말", "명령어", "?"):
            tg.send(CLOUD_HELP)
        else:
            tg.send("☁️ 지금은 클라우드에서 자동으로 돌아가요. 가능한 명령은 '도움' 을 보내 보세요.")


def make_story(env, cfg, state, tg):
    q = state.get("story_requests") or []
    topic = q.pop(0) if q else None
    state["story_requests"] = q
    return finder.run_story(env, cfg, state, tg, topic)


def make_clip(env, cfg, state, tg):
    try:
        done = finder.auto_make(env, cfg, state, tg, 1, reason="클라우드 자동")
    except Exception as e:
        print(f"[클립 실패] {e}")
        done = []
    if done:
        return done[0]
    tg.send("↪️ 해외 클립을 못 받아서 이번 칸은 사연 영상으로 대신 만들어요.")
    return make_story(env, cfg, state, tg)


def main():
    env = finder.load_env()
    cfg = finder.load_json(finder.CONFIG_PATH, {})
    state = finder.load_json(finder.STATE_PATH, {})
    if env.get("TELEGRAM_CHAT_ID"):
        state["chat_id"] = int(env["TELEGRAM_CHAT_ID"])
    tg = finder.Telegram(env.get("TELEGRAM_TOKEN"), state)
    finder.INPUT_DIR.mkdir(exist_ok=True)
    finder.OUTPUT_DIR.mkdir(exist_ok=True)

    arg = (sys.argv[1] if len(sys.argv) > 1 else "auto").lower()
    if arg in ("auto", "", "report") and not cfg.get("cloud_mode"):
        print("config.json 의 cloud_mode 가 꺼져 있어서 예약 실행은 건너뛰어요 (수동 실행은 가능)")
        return
    kind, at = slot_type(cfg) if arg in ("auto", "") else (arg, "수동")
    print(f"[클라우드] {now_kst():%Y-%m-%d %H:%M} 칸={at} 종류={kind}")

    handle_messages(env, cfg, state, tg)
    finder.save_state(state)
    try:
        if kind == "report":
            finder.run_report(env, cfg, state, tg)
        elif kind == "messages":
            pass                                          # 메시지 확인만
        elif kind == "clip":
            make_clip(env, cfg, state, tg)
        else:
            make_story(env, cfg, state, tg)
    finally:
        # 클라우드는 실행이 끝나면 영상 파일이 사라져서, 나중에 다시 올리기 목록은 비움
        # (실패한 영상은 텔레그램으로 받은 파일을 직접 올리면 돼요)
        state["pending_uploads"] = []
        finder.save_state(state)


if __name__ == "__main__":
    main()
