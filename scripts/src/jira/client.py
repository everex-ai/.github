"""Jira Cloud REST API v2 최소 클라이언트 (표준 라이브러리만 사용).

- scoped API 토큰은 api.atlassian.com/ex/jira/<cloudId> 주소로만 호출할 수 있다.
- v2를 쓰는 이유: description, comment, TL;DR을 위키 마크업 문자열로 다루기 위해서.
- 환경 변수: JIRA_CLOUD_ID, JIRA_EMAIL, JIRA_API_TOKEN, (선택) JIRA_SITE_URL, JIRA_TLDR_FIELD_ID
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from src.constant.jira import PROPERTY_KEY


class JiraError(RuntimeError):
    """Jira API 호출이 실패했을 때의 예외."""

    pass


class Jira:
    """Jira Cloud REST API v2 클라이언트. 인증 정보는 환경 변수에서 읽는다."""

    def __init__(self) -> None:
        cloud = os.environ["JIRA_CLOUD_ID"]
        email = os.environ["JIRA_EMAIL"]
        token = os.environ["JIRA_API_TOKEN"]
        self.base = f"https://api.atlassian.com/ex/jira/{cloud}/rest/api/2"
        self._auth = base64.b64encode(f"{email}:{token}".encode()).decode()
        self.tldr_field = os.environ.get("JIRA_TLDR_FIELD_ID", "")
        self._site = os.environ.get("JIRA_SITE_URL", "").rstrip("/")

    # ------------------------------------------------------------------ 기본 호출
    def _req(
        self, method: str, path: str, params: dict | None = None, body: dict | None = None, ok404: bool = False
    ) -> Any:
        """API를 호출한다. 일시 오류(429, 5xx, 연결 실패)는 최대 4번까지 다시 시도한다.

        Args:
            method: HTTP 메서드.
            path: base 뒤에 붙는 경로.
            params: 쿼리 문자열 인자.
            body: JSON 요청 본문.
            ok404: True이면 404를 오류로 보지 않고 None을 돌려준다.

        Returns:
            JSON으로 읽은 응답. 응답 본문이 비면 None.

        Raises:
            JiraError: 재시도 뒤에도 실패했거나 재시도할 수 없는 HTTP 오류일 때.
        """
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
        """issue 하나를 읽는다.

        Args:
            key: ticket 키.
            fields: 읽을 필드 이름 목록.

        Returns:
            issue 응답.
        """
        return self._req("GET", f"/issue/{key}", {"fields": ",".join(fields)})

    def comments(self, key: str) -> list[dict]:
        """issue의 comment를 작성 순서대로 모두 읽는다.

        Args:
            key: ticket 키.

        Returns:
            comment 목록.
        """
        out, start = [], 0
        while True:
            page = self._req(
                "GET", f"/issue/{key}/comment", {"orderBy": "created", "maxResults": 100, "startAt": start}
            )
            items = page.get("comments", [])
            out.extend(items)
            start += len(items)
            if not items or start >= page.get("total", 0):
                return out

    def changelog(self, key: str) -> list[dict]:
        """issue의 변경 이력을 모두 읽는다.

        Args:
            key: ticket 키.

        Returns:
            변경 이력 목록.
        """
        out, start = [], 0
        while True:
            page = self._req("GET", f"/issue/{key}/changelog", {"maxResults": 100, "startAt": start})
            items = page.get("values", [])
            out.extend(items)
            start += len(items)
            if page.get("isLast", True) or not items:
                return out

    def search(self, jql: str, fields: list[str], limit: int = 500) -> list[dict]:
        """JQL로 issue를 검색한다.

        Args:
            jql: 검색 질의문.
            fields: 읽을 필드 이름 목록.
            limit: 돌려줄 issue 수의 상한.

        Returns:
            issue 목록. 최대 limit개.
        """
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
        """issue property(PROPERTY_KEY)의 값을 읽는다.

        Args:
            key: ticket 키.

        Returns:
            property 값. 없으면 빈 dict.
        """
        res = self._req("GET", f"/issue/{key}/properties/{PROPERTY_KEY}", ok404=True)
        return (res or {}).get("value", {}) or {}

    # ------------------------------------------------------------------ 쓰기 (apply.py만 사용)
    def prop_set(self, key: str, value: dict) -> None:
        """issue property(PROPERTY_KEY)의 값을 쓴다.

        Args:
            key: ticket 키.
            value: 쓸 값.
        """
        self._req("PUT", f"/issue/{key}/properties/{PROPERTY_KEY}", body=value)

    def set_fields(self, key: str, fields: dict) -> None:
        """issue의 필드를 바꾼다.

        Args:
            key: ticket 키.
            fields: 필드 이름 -> 새 값.
        """
        self._req("PUT", f"/issue/{key}", body={"fields": fields})

    def add_comment(self, key: str, body: str) -> dict:
        """issue에 comment를 단다.

        Args:
            key: ticket 키.
            body: comment 본문(위키 마크업).

        Returns:
            만든 comment 응답.
        """
        return self._req("POST", f"/issue/{key}/comment", body={"body": body})
