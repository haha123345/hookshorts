# -*- coding: utf-8 -*-
"""GitHub 클라우드 설정용: GitHub Secrets 에 넣을 이름과 값을 한 화면에 보여 줌 (이 PC 에서만, 아무 데도 안 보냄)"""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
env = {}
for n in (".env", ".env.txt", "env.txt", "env"):
    for d in (BASE, BASE.parent):
        p = d / n
        if p.is_file():
            for line in p.read_text(encoding="utf-8-sig", errors="replace").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
try:
    chat = json.loads((BASE / "state.json").read_text(encoding="utf-8-sig")).get("chat_id", "")
except Exception:
    chat = ""
try:
    token = json.dumps(json.loads((BASE / "youtube_token.json").read_text(encoding="utf-8")))
except Exception:
    token = ""

rows = [("TELEGRAM_TOKEN", env.get("TELEGRAM_TOKEN", "")), ("TELEGRAM_CHAT_ID", str(chat)),
        ("GEMINI_KEY", env.get("GEMINI_KEY", "")), ("YOUTUBE_KEY", env.get("YOUTUBE_KEY", "")),
        ("PIXABAY_KEY", env.get("PIXABAY_KEY", "")), ("YT_TOKEN_JSON", token)]
if env.get("OPENAI_KEY"):
    rows.append(("OPENAI_KEY", env["OPENAI_KEY"]))
if env.get("GOOGLE_TTS_KEY"):
    rows.append(("GOOGLE_TTS_KEY", env["GOOGLE_TTS_KEY"]))

print("=" * 70)
print(" GitHub → hookshorts 저장소 → Settings → Secrets and variables → Actions")
print(" → New repository secret 에서 아래 Name / Secret 을 하나씩 복사해 넣으세요.")
print(" ※ 이 값들은 비밀번호예요. 채팅이나 다른 곳에 붙여넣지 마세요.")
print("=" * 70)
for k, v in rows:
    print(f"\n[Name]   {k}\n[Secret] {v if v else '(없음 — 이 PC 에서 찾지 못했어요)'}")
print("\n" + "=" * 70)
(BASE / "cloud_keys.txt").write_text("\n\n".join(f"{k}\n{v}" for k, v in rows), encoding="utf-8")
print(f"같은 내용을 메모장 파일로도 저장했어요: {BASE / 'cloud_keys.txt'}")
print("다 넣은 뒤에는 이 파일을 꼭 지워 주세요.")
