"""Jira Cloud REST API v2 최소 클라이언트 (표준 라이브러리만 사용).

- scoped API 토큰은 api.atlassian.com/ex/jira/<cloudId> 주소로만 호출할 수 있다.
- v2를 쓰는 이유: description, comment, TL;DR을 위키 마크업 문자열로 다루기 위해서.
- 환경 변수: JIRA_CLOUD_ID, JIRA_EMAIL, JIRA_API_TOKEN, (선택) JIRA_SITE_URL, JIRA_TLDR_FIELD_ID
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

PROPERTY_KEY = "ai-doc-agent"
AGENT_MARK = "[ai-doc-agent]"          # agent가 쓰는 모든 comment의 첫 줄 표식
H2 = re.compile(r"^h2\.\s*(.+?)\s*$", re.M)


class JiraError(RuntimeError):
    pass


class Jira:
    def __init__(self) -> None:
        cloud = os.environ["JIRA_CLOUD_ID"]
        email = os.environ["JIRA_EMAIL"]
        token = os.environ["JIRA_API_TOKEN"]
        self.base = f"https://api.atlassian.com/ex/jira/{cloud}/rest/api/2"
        self._auth = base64.b64encode(f"{email}:{token}".encode()).decode()
        self.tldr_field = os.environ.get("JIRA_TLDR_FIELD_ID", "")
        self._site = os.environ.get("JIRA_SITE_URL", "").rstrip("/")

    # ------------------------------------------------------------------ 기본 호출
    def _req(self, method: str, path: str, params: dict | None = None, body=None, ok404: bool = False):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        headers = {
            "Authorization": "Basic " + self._auth,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        last = None
        for attempt in range(4):
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    txt = r.read().decode("utf-8")
                    return json.loads(txt) if txt.strip() else None
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:600]
                if e.code == 404 and ok404:
                    return None
                if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(2 * (attempt + 1))
                    last = f"{e.code}: {detail}"
                    continue
                raise JiraError(f"{method} {path} -> {e.code}: {detail}")
            except urllib.error.URLError as e:
                last = str(e)
                time.sleep(2 * (attempt + 1))
        raise JiraError(f"{method} {path} failed after retries: {last}")

    # ------------------------------------------------------------------ 읽기
    def site_url(self) -> str:
        """browse 링크용 사이트 주소. serverInfo가 막혀 있으면 JIRA_SITE_URL을 쓴다."""
        if self._site:
            return self._site
        try:
            info = self._req("GET", "/serverInfo")
            self._site = (info or {}).get("baseUrl", "").rstrip("/")
        except JiraError:
            self._site = ""
        return self._site

    def issue(self, key: str, fields: list[str]) -> dict:
        return self._req("GET", f"/issue/{key}", {"fields": ",".join(fields)})

    def comments(self, key: str) -> list[dict]:
        out, start = [], 0
        while True:
            page = self._req("GET", f"/issue/{key}/comment", {"orderBy": "created", "maxResults": 100, "startAt": start})
            items = page.get("comments", [])
            out.extend(items)
            start += len(items)
            if not items or start >= page.get("total", 0):
                return out

    def changelog(self, key: str) -> list[dict]:
        out, start = [], 0
        while True:
            page = self._req("GET", f"/issue/{key}/changelog", {"maxResults": 100, "startAt": start})
            items = page.get("values", [])
            out.extend(items)
            start += len(items)
            if page.get("isLast", True) or not items:
                return out

    def search(self, jql: str, fields: list[str], limit: int = 500) -> list[dict]:
        out, token = [], None
        while True:
            body = {"jql": jql, "fields": fields, "maxResults": 100}
            if token:
                body["nextPageToken"] = token
            page = self._req("POST", "/search/jql", body=body)
            out.extend(page.get("issues", []))
            token = page.get("nextPageToken")
            if not token or page.get("isLast") or len(out) >= limit:
                return out[:limit]

    def prop_get(self, key: str) -> dict:
        res = self._req("GET", f"/issue/{key}/properties/{PROPERTY_KEY}", ok404=True)
        return (res or {}).get("value", {}) or {}

    def transitions(self, key: str) -> list[dict]:
        return self._req("GET", f"/issue/{key}/transitions").get("transitions", [])

    # ------------------------------------------------------------------ 쓰기 (apply.py만 사용)
    def prop_set(self, key: str, value: dict) -> None:
        self._req("PUT", f"/issue/{key}/properties/{PROPERTY_KEY}", body=value)

    def set_fields(self, key: str, fields: dict) -> None:
        self._req("PUT", f"/issue/{key}", body={"fields": fields})

    def add_comment(self, key: str, body: str) -> dict:
        return self._req("POST", f"/issue/{key}/comment", body={"body": body})

    def transition(self, key: str, name: str) -> None:
        cands = [t for t in self.transitions(key) if t.get("name", "").lower() == name.lower()]
        if not cands:
            raise JiraError(f"transition '{name}' not available on {key}")
        self._req("POST", f"/issue/{key}/transitions", body={"transition": {"id": cands[0]["id"]}})


# ---------------------------------------------------------------------- 공통 유틸
def is_agent_comment(c: dict) -> bool:
    """agent 또는 Automation이 쓴 comment인지. 사람이 쓴 comment만 근거로 삼기 위해 쓴다."""
    body = (c.get("body") or "").lstrip()
    if body.startswith(AGENT_MARK):
        return True
    author = c.get("author") or {}
    if author.get("accountType") == "app":
        return True
    name = (author.get("displayName") or "").lower()
    return "automation for jira" in name


def sections(desc: str) -> list[tuple[str, int, int, int]]:
    """description(wiki markup)의 h2 구역 목록: (제목, 제목 시작, 본문 시작, 본문 끝)."""
    desc = desc or ""
    heads = list(H2.finditer(desc))
    out = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(desc)
        out.append((m.group(1), m.start(), m.end(), end))
    return out


def norm_title(title: str) -> str:
    """구역 제목 비교용. 굵게(*), 기울임(_), 밑줄(+), 색 표식과 공백을 뗀다. 'h2. *진행 배경*'도 잡기 위해."""
    return re.sub(r"\{color[^}]*\}|[*_+\s]", "", title or "").lower()


def find_section(desc: str, prefix: str):
    """제목이 prefix로 시작하는 첫 구역. 괄호 설명이 붙은 제목('예상 산출물 (Task 완료 기준)')도 잡는다."""
    p = norm_title(prefix)
    for title, hs, bs, be in sections(desc):
        if norm_title(title).startswith(p):
            return title, hs, bs, be
    return None


def section_body(desc: str, prefix: str) -> str:
    hit = find_section(desc, prefix)
    return (desc or "")[hit[2]:hit[3]].strip() if hit else ""


def set_section(desc: str, prefix: str, body: str, stamp: str, note: str) -> str:
    """prefix 구역의 본문만 body로 바꾼다. 다른 구역은 건드리지 않는다. 구역이 없으면 맨 뒤에 추가."""
    desc = desc or ""
    block = f"\n{{color:#6b778c}}_{note} 마지막 갱신 {stamp}_{{color}}\n{body.strip()}\n\n"
    hit = find_section(desc, prefix)
    if hit:
        title, hs, bs, be = hit
        return desc[:bs] + block + desc[be:]
    return desc.rstrip() + f"\n\nh2. {prefix}" + block


def strip_placeholders(text: str) -> list[str]:
    """템플릿 안내문(괄호로만 된 줄, 빈 글머리표)을 뺀 실제 내용 줄 목록."""
    lines = []
    for raw in (text or "").splitlines():
        s = raw.strip()
        if not s:
            continue
        core = re.sub(r"^[*#\-\s]+", "", s).strip()
        if not core:
            continue
        if re.fullmatch(r"[\(（].*[\)）]", core):     # (안내문)
            continue
        if core.startswith("{color:#6b778c}"):        # agent 안내 줄
            continue
        lines.append(core)
    return lines
