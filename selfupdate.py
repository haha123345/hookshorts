# -*- coding: utf-8 -*-
"""
자동 업데이트: GitHub 저장소에 새 코드가 올라오면 PC 의 hookshorts 폴더에 알아서 반영

  - 리모컨이 켜질 때 + 30분마다(쉬는 중일 때만) 확인
  - 텔레그램 '업데이트' 로 바로 확인
  - 개인 파일(.env, client_secret*.json, youtube_token.json, state.json, input/, output/)은 절대 안 건드림
  - config.json 은 덮어쓰기 전에 config.backup.json 으로 보관
  - start_bot.bat 은 실행 중이라 건드리지 않음 (바뀌면 start_bot.new.bat 으로 저장)
"""
import io
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
STAMP = BASE / ".code_version"
DEFAULT_REPO = "haha123345/hookshorts"
PROTECT_NAMES = {".env", ".env.txt", "env.txt", "env", "youtube_token.json", "state.json",
                 "config.backup.json", ".code_version"}
PROTECT_DIRS = {"input", "output", "__pycache__", ".git", ".github"}
RUNNING_BAT = {"start_bot.bat"}


def _headers(env):
    h = {"Accept": "application/vnd.github+json", "User-Agent": "hookshorts-updater"}
    tok = (env or {}).get("GITHUB_TOKEN")                  # 저장소가 비공개일 때만 필요
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def repo_of(cfg):
    return (cfg or {}).get("update_repo") or DEFAULT_REPO


def current():
    try:
        return json.loads(STAMP.read_text(encoding="utf-8"))
    except Exception:
        return {}


def latest(cfg, env):
    """GitHub 의 최신 커밋: {sha, message, date}"""
    repo = repo_of(cfg)
    branch = (cfg or {}).get("update_branch", "main")
    r = requests.get(f"https://api.github.com/repos/{repo}/commits/{branch}", headers=_headers(env), timeout=20)
    if r.status_code == 404:
        raise RuntimeError(f"GitHub 저장소 {repo} 를 찾을 수 없어요 (비공개면 .env 에 GITHUB_TOKEN 필요)")
    r.raise_for_status()
    j = r.json()
    return {"sha": j["sha"], "message": j["commit"]["message"].split("\n")[0][:120],
            "date": j["commit"]["committer"]["date"][:16].replace("T", " ")}


def _protected(rel):
    parts = Path(rel).parts
    if not parts or parts[0] in PROTECT_DIRS:
        return True
    name = parts[-1]
    return name in PROTECT_NAMES or name.startswith("client_secret")


def apply(cfg, env, info):
    """최신 코드를 받아 폴더에 덮어씀. 반환: 바뀐 파일 목록"""
    repo = repo_of(cfg)
    r = requests.get(f"https://api.github.com/repos/{repo}/zipball/{info['sha']}", headers=_headers(env),
                     timeout=120)
    r.raise_for_status()
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    changed = []
    for item in zf.infolist():
        if item.is_dir():
            continue
        rel = item.filename.split("/", 1)[1] if "/" in item.filename else ""
        if not rel or _protected(rel):
            continue
        data = zf.read(item)
        dest = BASE / rel
        if dest.exists() and dest.read_bytes() == data:
            continue
        if rel in RUNNING_BAT:                                # 실행 중인 bat 은 바꾸면 꼬임
            dest = BASE / rel.replace(".bat", ".new.bat")
        elif rel == "config.json" and dest.exists():
            shutil.copy2(dest, BASE / "config.backup.json")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        changed.append(rel)
    if (BASE / "requirements.txt").exists() and "requirements.txt" in changed:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check",
                        "--no-warn-script-location", "-r", str(BASE / "requirements.txt")], capture_output=True)
    STAMP.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    return changed


def check_and_apply(cfg, env, tg=None, force_msg=False):
    """새 버전이 있으면 적용. 반환: True = 적용함(재시작 필요)"""
    try:
        info = latest(cfg, env)
    except Exception as e:
        if force_msg and tg:
            tg.send(f"❌ 업데이트 확인 실패: {str(e)[:250]}")
        return False
    if info["sha"] == current().get("sha"):
        if force_msg and tg:
            tg.send(f"✅ 이미 최신이에요 ({info['date']} · {info['message']})")
        return False
    try:
        changed = apply(cfg, env, info)
    except Exception as e:
        if tg:
            tg.send(f"❌ 업데이트 받기 실패: {str(e)[:250]}")
        return False
    if not changed:
        return False
    if tg:
        tg.send(f"🔄 새 버전 적용: {info['message']}\n바뀐 파일: {', '.join(changed[:8])}"
                f"{' 외' if len(changed) > 8 else ''}\n1분 뒤 리모컨이 새 버전으로 다시 켜져요.")
    return True
