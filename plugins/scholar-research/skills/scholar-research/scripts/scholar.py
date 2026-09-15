#!/usr/bin/env python3
"""scholar.py — 무료 학술 API 통합 CLI (표준 라이브러리만 사용)

scholar-research 스킬의 데이터층. 외부 유료 API 없음 — OpenAlex·Crossref·
arXiv·Europe PMC·Unpaywall·PubMed(E-utilities)·DataCite(전부 무키/이메일)·
Semantic Scholar(무키 공유풀, 429 시 스킵).

subcommands:
  search  "query"        멀티소스 검색 → DOI dedup → 랭킹
  bundle  "query"        원샷: 검색(3소스 병렬) → DOI dedup → 랭킹 → 상위 K편 초록
                         병렬 조회 → JSON 1개(--out) + 요약 표. project add --from 호환
  paper   <doi|W-id>     단일 논문 상세 (초록 복원 포함)
  oa      <doi>          Unpaywall 합법 OA PDF 위치
  cite    <doi>          서지 인용 문자열 (apa|mla|chicago — doi.org CSL)
  verify  <doi> "title"  인용 실재 기계검증 [--author 성] [--year YYYY — ±1년 허용(online-first)]
                         MATCH(0)/NO_DOI·RETRACTION_NA(2 — 수동 확인 후 유지 가능)/
                         NOT_FOUND·MISMATCH·MISMATCH_META·NON_ARTICLE(3)/RETRACTED(4)/UNVERIFIED·
                         RETRACTION_UNCHECKED(5 — 미검증, 부재 아님)/
                         INCOMPLETE_META·BAD_ENTRY(6 — 메타 미기재·스키마 위반, batch 한정)
  verify-batch refs.json 참고문헌 전수 검증 (전부 MATCH=0 / 가짜·오기재·철회=3 /
                         남은 실패가 전부 일시장애=5 / 실패 전원이 NO_DOI=2 /
                         그 외(메타 미기재·스키마 위반)=6 / 빈 파일=2 — 0건은 통과가
                         아니다). 중복 DOI는 요약줄에 ⚠로 표기(근거 폭 부풀리기 방어)
  snowball <doi>         인용 눈덩이 (--direction cites|refs [--year-from Y] [--sort] [--query 키워드])
                         arXiv 논문은 arXiv:<id> 또는 10.48550/arXiv.<id>
  recommend <doi>        S2 유사논문 추천 (의미 근접 — snowball 보완). arXiv 앵커는 arXiv:<id>로 자동 변환
  project ...            논문 테이블 프로젝트: init|list|add|recount|rm|col|set|show|log|refs|render|
                         export-db|import-db
                         (P# 전역 레지스트리 + AI 열 — Liner 논문 테이블 재현.
                         파일은 $SCHOLAR_HOME=~/Documents/mybrain_raw/scholar_projects)
  data ...               1차출처 시계열 + W# 데이터 ledger($SCHOLAR_HOME/data/ledger.json):
                         fred <ID> [--start --end --out] · worldbank <IND> --country US,KR ·
                         list · show W1 [--tail N --stats] · verify [W#…](exit 0/5/3/2) ·
                         vintage [W#…](신선도 표). 무키·표준 라이브러리, 시계열은 캐시 안 함

env: SCHOLAR_EMAIL(권장), OPENALEX_API_KEY(선택), S2_API_KEY(선택), NCBI_API_KEY(선택)
     SCHOLAR_CACHE(sqlite 캐시 경로, 기본 ~/.cache/scholar-research/cache.db),
     SCHOLAR_NO_CACHE=1(캐시 비활성 — --no-cache 플래그와 동일)
cache: DOI별 메타·초록 30일 캐시(paper·bundle 초록·verify 서지·project add). search 결과와
     **철회 판정은 캐시하지 않는다**(철회는 늦게 반영되므로 매번 조회). 히트 시 출력에 "(cache)".
"""
import argparse
import concurrent.futures
import difflib
import hashlib
import html
import json
import os
import re
import sqlite3
import ssl
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

EMAIL = os.environ.get("SCHOLAR_EMAIL", "phone234c@gmail.com")
UA = f"scholar-research-skill/0.1 (mailto:{EMAIL})"
TIMEOUT = 25


def _ssl_ctx():
    # python.org 프레임워크 빌드는 시스템 CA를 안 읽는다 → certifi > /etc/ssl 순서로 명시
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    for cafile in ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"):
        if os.path.exists(cafile):
            return ssl.create_default_context(cafile=cafile)
    return ssl.create_default_context()


SSL_CTX = _ssl_ctx()


_QUOTA_WARNED = set()   # host별 1회만 경고


def _quota_warn(url, code, err):
    """유료화된 API(OpenAlex 2026-02~)의 한도 초과를 '문헌 없음'과 구분해 크게 알린다.
    OpenAlex는 일일 예산 초과·초당 한도 초과 모두 429다(공식 문서). 돈은 나가지 않는다 —
    결제수단 미등록이면 요청이 거부될 뿐. 익명 $0.10/일, 무료 키 $1/일(검색 1,000회)."""
    host = urllib.parse.urlsplit(url).netloc
    if host in _QUOTA_WARNED:
        return
    body = ""
    try:
        body = (err.read() or b"").decode("utf-8", "replace")[:200]
    except Exception:
        pass
    if "openalex.org" in host and code in (401, 402, 403, 429):
        _QUOTA_WARNED.add(host)
        keyed = bool(os.environ.get("OPENALEX_API_KEY"))
        print(f"\n⚠️  OpenAlex HTTP {code} — 일일 한도({'키 $1' if keyed else '익명 $0.10'}/일) 초과 또는 속도 제한."
              f" **이 뒤의 openalex 0건은 '문헌 없음'이 아니다.** "
              f"{'내일 재시도 또는 유료 전환' if keyed else '무료 키(OPENALEX_API_KEY)를 넣으면 한도 10배'}."
              f"{' 응답: ' + body.strip() if body.strip() else ''}\n", file=sys.stderr)


def http_get(url, headers=None, retries=1, backoff=2.0):
    h = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=SSL_CTX) as r:
                return r.read().decode("utf-8", "replace"), r.status
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 429 and attempt < retries:
                time.sleep(backoff * (attempt + 1))
                continue
            _quota_warn(url, e.code, e)
            return None, e.code
        except Exception as e:  # URLError, timeout
            last_err = e
            if attempt < retries:
                time.sleep(backoff)
                continue
            return None, -1
    return None, getattr(last_err, "code", -1)


def norm_doi(doi):
    if not doi:
        return None
    doi = doi.strip().lower()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
    # 레드팀 R5: 'doi:10.x'·'DOI: 10.x'(참고문헌·PDF에서 베낀 꼴)를 안 벗겨 같은 논문이
    # 다른 키로 갈렸다 — verify-batch의 ⚠ 중복 DOI 경보·캐시 키·project _paper_key 분열.
    doi = re.sub(r"^doi:\s*", "", doi)
    # 레드팀 R5: 참고문헌 목록에서 복사한 DOI의 문장 끝 마침표('10.1038/nature14539.')가
    # 그대로 조회 경로에 들어가 3소스 모두 404 → NOT_FOUND(가짜 인용 낙인). 괄호는 벗기지
    # 않는다(Wiley 구형 DOI '10.1002/(SICI)…' 보호). review의 _DOI_RE 후처리와 같은 규칙.
    doi = doi.rstrip(".,;:")
    return doi or None


# ---------------- 로컬 캐시 (sqlite3 — 표준 라이브러리) ----------------
# DOI(또는 arXiv id)별 메타·초록을 30일 캐시한다. 경로 $SCHOLAR_CACHE(기본
# ~/.cache/scholar-research/cache.db), SCHOLAR_NO_CACHE=1 또는 --no-cache면 비활성.
# 적용: paper 조회·bundle 초록 조회·verify 서지 조회(DataCite 전용 판정·PubMed PMID)·
# project add/recount 메타. search 결과는 캐시하지 않는다(신선도).
# ⚠️ 철회 판정은 캐시하지 않는다 — 철회는 OpenAlex·Crossref·PubMed에 늦게 반영되므로
# 매번 실 조회한다. 캐시 히트여도 OpenAlex `select=is_retracted`·Crossref(updated-by·
# relation)·PubMed efetch는 그대로 호출된다. cache_put이 철회 키를 구조적으로 걷어내
# (CACHE_FORBIDDEN_KEYS) 실수로도 저장되지 않는다.
CACHE_TTL_SEC = 30 * 86400
CACHE_FORBIDDEN_KEYS = ("is_retracted", "retracted", "retraction_checked",
                        "retraction_sources", "is_retraction_notice")
_CACHE_OFF = False  # --no-cache
_CACHE_STATS = {"hit": 0, "miss": 0}


def _cache_path():
    return (os.environ.get("SCHOLAR_CACHE")
            or os.path.join(os.path.expanduser("~"), ".cache", "scholar-research", "cache.db"))


def cache_enabled():
    return not _CACHE_OFF and os.environ.get("SCHOLAR_NO_CACHE", "") != "1"


def _cache_conn():
    path = _cache_path()
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    con = sqlite3.connect(path, timeout=5)  # bundle은 스레드 병렬 — 호출마다 연결
    con.execute("CREATE TABLE IF NOT EXISTS entries("
                "kind TEXT NOT NULL, key TEXT NOT NULL, ts REAL NOT NULL, value TEXT NOT NULL,"
                " PRIMARY KEY(kind, key))")
    return con


def cache_get(kind, key, ttl=CACHE_TTL_SEC):
    """히트면 저장된 dict, 미스·만료·비활성·오류면 None. 만료 행은 지운다."""
    if not cache_enabled() or not key:
        return None
    try:
        con = _cache_conn()
        try:
            row = con.execute("SELECT ts, value FROM entries WHERE kind=? AND key=?",
                              (kind, key)).fetchone()
            if row and time.time() - row[0] > ttl:
                con.execute("DELETE FROM entries WHERE kind=? AND key=?", (kind, key))
                con.commit()
                row = None
        finally:
            con.close()
    except (sqlite3.Error, OSError):  # DB 손상·경로 불가·권한 없음 → 조용히 미스(실 조회)
        return None
    if not row:
        _CACHE_STATS["miss"] += 1
        return None
    try:
        v = json.loads(row[1])
    except ValueError:
        return None
    _CACHE_STATS["hit"] += 1
    return v


def cache_put(kind, key, value):
    """dict만 저장. 철회 관련 키는 저장 전에 걷어낸다(철회 미캐시 원칙의 구조적 보장)."""
    if not cache_enabled() or not key or not isinstance(value, dict):
        return False
    clean = {k: v for k, v in value.items() if k not in CACHE_FORBIDDEN_KEYS and k != "cache"}
    try:
        con = _cache_conn()
        try:
            con.execute("INSERT OR REPLACE INTO entries(kind, key, ts, value) VALUES(?,?,?,?)",
                        (kind, key, time.time(), json.dumps(clean, ensure_ascii=False)))
            con.commit()
        finally:
            con.close()
        return True
    except (sqlite3.Error, OSError):
        return False


def _cache_tag(flag):
    return " (cache)" if flag else ""


def uninvert_abstract(inv):
    """OpenAlex abstract_inverted_index → 평문."""
    if not inv:
        return None
    pos = {}
    for word, idxs in inv.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos)) or None


# ---------------- sources ----------------

def search_openalex(q, limit, year_from=None, oa_only=False):
    filters = []
    if year_from:
        filters.append(f"from_publication_date:{year_from}-01-01")
    if oa_only:
        filters.append("is_oa:true")
    params = {
        "search": q,
        "per-page": str(limit),
        "mailto": EMAIL,
        "select": "id,doi,title,display_name,publication_year,cited_by_count,"
                  "primary_location,authorships,open_access,type,is_retracted",
    }
    if filters:
        params["filter"] = ",".join(filters)
    key = os.environ.get("OPENALEX_API_KEY")
    if key:
        params["api_key"] = key
    body, code = http_get("https://api.openalex.org/works?" + urllib.parse.urlencode(params))
    if not body:
        return [], f"openalex HTTP {code}"
    out = []
    for w in json.loads(body).get("results", []):
        loc = w.get("primary_location") or {}
        src = (loc.get("source") or {}).get("display_name")
        out.append({
            "source": "openalex",
            "id": w.get("id"),
            "doi": norm_doi(w.get("doi")),
            "title": _clean_title(w.get("display_name") or w.get("title")),
            "year": w.get("publication_year"),
            "venue": src,
            "citations": w.get("cited_by_count", 0),
            "is_oa": (w.get("open_access") or {}).get("is_oa", False),
            "is_retracted": bool(w.get("is_retracted")),
            # 레드팀 R5: select에 type을 요청하고도 행에 안 실어 merge_and_rank의
            # 비논문 필터가 openalex 행에 무효였다 — Choice Reviews Online 서평(book-review,
            # 초록·저자 없음)이 책의 인용수 1,485를 흡수한 채 [P6]으로 랭크됐다.
            "type": (w.get("type") or "").strip().lower() or None,
            "authors": [a.get("author", {}).get("display_name")
                        for a in (w.get("authorships") or [])[:3]],
        })
    out = [p for p in out if (p.get("type") or "") not in NON_PAPER_TYPES]
    return out, None


# 논문이 아닌 Crossref 레코드 — 심사보고서(RSC·Wiley가 리뷰마다 발급)·개별 그림/표
# component·연구비 grant. Crossref relevance 검색은 이들을 논문과 동일 취급해 반환하며
# 실측상 상위 20슬롯의 절반 이상을 잠식한다(레드팀 R3). 초록·본문·저자가 없는데
# 제목은 원논문 제목을 그대로 인용한 'Review for "<원제목>"' 꼴이라 눈 스크리닝도 속인다.
NON_PAPER_TYPES = {"peer-review", "peer_review", "component", "grant",
                   # 레드팀 R5: OpenAlex 어휘의 비논문 계열 — 도서관용 한 단락 서평(book-review)·
                   # 표지/목차(paratext)·보충자료·정오표·철회 통지. 검색단(search_openalex)과
                   # 병합단(merge_and_rank)에서만 쓴다. verify의 NON_ARTICLE 판정에는 Crossref
                   # type만 쓰므로(OpenAlex는 정상 논문을 erratum으로 오분류 — 레드팀 R4) 이
                   # 확장이 verify를 건드리지 않는다(Crossref 어휘에는 이 값들이 없다).
                   "book-review", "paratext", "supplementary-materials", "erratum",
                   "retraction", "libguides"}

# 정오표·수정통지 계열 — 실재하는 DOI·제목·저자·연도를 갖지만 연구 결과가 0건이라
# [P#] 근거가 될 수 없다. Crossref는 이들을 journal-article로 분류하므로 type만으로는
# 잡히지 않는다(레드팀 R4 실측: 3/3 전부 crossref=journal-article, openalex=erratum).
# 판별은 Crossref `update-to`의 type(정오표는 correction/erratum, 원논문은 빈 배열)과
# 제목 접두 패턴으로 한다.
CORRECTION_UPDATE_TYPES = re.compile(
    r"correction|corrigend|erratum|errata|addend", re.I)
# 'Author Correction: X' / 'Corrigendum to: X' / 'Erratum: X' 꼴
CORRECTION_TITLE_RE = re.compile(
    r"^\s*(?:author|publisher|editorial)?\s*"
    r"(?:correction|corrigendum|corrigenda|erratum|errata|addendum)\b"
    r"\s*(?:to|for)?\s*[::\-–—]", re.I)
# 철회 **통지문** 제목 관행: 'Retraction—X' / 'Retraction notice: X' /
# 'Withdrawal of X' / 'Retracted: X 철회 공고'. 통지문은 철회당한 문서가 아니므로
# RETRACTED가 아니라 NON_ARTICLE로 보낸다(실사용 발견, 2026-07-28).
# 'RETRACTED: X'(원논문에 붙는 Crossref 접두)와 혼동하지 않도록 'retraction/
# withdrawal' 명사형만 잡고, 뒤에 구분자(—/:/of)가 오는 꼴로 제한한다.
RETRACTION_NOTICE_TITLE_RE = re.compile(
    r"^\s*(?:notice\s+of\s+)?(?:retraction|withdrawal)\b"
    r"\s*(?:notice|statement)?\s*(?:to|of|for)?\s*[::\-–—]", re.I)
# 철회당한 원논문의 제목 접두 관행 — 'RETRACTED: X' / 'RETRACTED ARTICLE: X' /
# 'RETRACTED CHAPTER: X' / 'Retracted and replaced: X' / 'RETRACTED — X'. 구분자 없는
# 'Retracted Publications in …'(철회 연구 메타분석)는 정상 논문이라 잡지 않는다(레드팀 R6).
RETRACTED_PREFIX_RE = re.compile(
    r"^\s*retracted(?:\s+(?:article|chapter|paper|publication|and\s+replaced))?\s*[::\-–—]", re.I)


def search_crossref(q, limit, year_from=None):
    # 비논문 레코드를 걸러내면 결과 수가 줄므로 넉넉히 받아서 필터 후 절단한다.
    # (Crossref에는 부정 필터가 없고, type 화이트리스트는 정당한 report·dissertation
    #  까지 날려 리콜을 깎으므로 블록리스트 방식을 쓴다.)
    rows = min(max(limit * 3, limit + 10), 100)
    params = {"query": q, "rows": str(rows), "mailto": EMAIL,
              "select": "DOI,title,author,issued,container-title,is-referenced-by-count,type"}
    if year_from:
        params["filter"] = f"from-pub-date:{year_from}-01-01"
    body, code = http_get("https://api.crossref.org/works?" + urllib.parse.urlencode(params))
    if not body:
        return [], f"crossref HTTP {code}"
    out = []
    for it in json.loads(body).get("message", {}).get("items", []):
        typ = (it.get("type") or "").strip().lower()
        if typ in NON_PAPER_TYPES:
            continue  # 심사보고서·그림 component·grant는 [P#] 후보가 될 수 없다
        year = None
        parts = (it.get("issued") or {}).get("date-parts") or [[None]]
        if parts and parts[0]:
            year = parts[0][0]
        out.append({
            "source": "crossref",
            "doi": norm_doi(it.get("DOI")),
            "title": _clean_title((it.get("title") or [None])[0]),
            "year": year,
            "venue": (it.get("container-title") or [None])[0],
            "citations": it.get("is-referenced-by-count", 0),
            "is_oa": None,
            "type": typ or None,
            "authors": [" ".join(filter(None, [a.get("given"), a.get("family")]))
                        for a in (it.get("author") or [])[:3]],
        })
        if len(out) >= limit:
            break
    return out, None


def search_s2(q, limit, year_from=None):
    params = {"query": q, "limit": str(limit),
              "fields": "title,year,citationCount,externalIds,venue,authors,isOpenAccess"}
    if year_from:
        params["year"] = f"{year_from}-"
    headers = {}
    key = os.environ.get("S2_API_KEY")
    if key:
        headers["x-api-key"] = key
    body, code = http_get(
        "https://api.semanticscholar.org/graph/v1/paper/search?" + urllib.parse.urlencode(params),
        headers=headers, retries=1)
    if not body:
        return [], f"s2 HTTP {code}"
    out = []
    for p in json.loads(body).get("data", []):
        out.append({
            "source": "s2",
            "doi": norm_doi((p.get("externalIds") or {}).get("DOI")),
            "title": p.get("title"),
            "year": p.get("year"),
            "venue": p.get("venue"),
            "citations": p.get("citationCount", 0),
            "citations_s2": p.get("citationCount"),  # bundle: 별도 S2 조회 없이 보존
            "is_oa": p.get("isOpenAccess"),
            "authors": [a.get("name") for a in (p.get("authors") or [])[:3]],
        })
    return out, None


def _arxiv_parse(body):
    out = []
    for m in re.finditer(r"<entry>(.*?)</entry>", body, re.S):
        e = m.group(1)
        def tag(t):
            mm = re.search(rf"<{t}[^>]*>(.*?)</{t}>", e, re.S)
            if not mm:
                return None
            # Atom은 XML이라 '&'·'<'가 엔티티로 이스케이프된다(&amp; 등). 복원하지
            # 않으면 제목에 'amp' 토큰이 끼어 타 소스와의 제목 dedup이 깨지고
            # 인용 출력에도 &amp;가 그대로 노출된다(레드팀 R2).
            return html.unescape(re.sub(r"\s+", " ", mm.group(1)).strip())
        aid = tag("id") or ""
        doi_m = re.search(r'<arxiv:doi[^>]*>(.*?)</arxiv:doi>', e)
        pub = tag("published")
        out.append({
            "source": "arxiv",
            "id": aid,
            "doi": norm_doi(html.unescape(doi_m.group(1))) if doi_m else None,
            "title": tag("title"),
            "year": int(pub[:4]) if pub else None,
            "venue": "arXiv",
            "citations": None,  # arXiv API는 인용수 미제공 — 0(미인용)과 구분해 미상(None)
            "is_oa": True,
            "pdf": aid.replace("/abs/", "/pdf/") if "/abs/" in aid else None,
            "authors": [html.unescape(x) for x in re.findall(r"<name>(.*?)</name>", e)[:3]],
        })
    return out


def _arxiv_query(q):
    """질의를 arXiv 검색식으로. 레드팀 R4: `all:{q}` 한 덩어리로 보내면 arXiv가
    공백을 **암묵 OR**로 파싱한다(실측: 'retrieval augmented generation hallucination'
    → `all:retrieval OR all:augmented OR ...`, 984,488건). 전 소스 중 arXiv만 OR
    시맨틱이라 정밀도가 붕괴하고, merge_and_rank의 _rank<=2 무조건 예약을 타고
    무관 문헌이 최종 슬롯을 30% 잠식했다. 토큰별 AND로 조인하면 1,134건·상위 전부
    주제 적합(따옴표 구문 `all:"..."`은 정확 구문만 잡아 리콜을 깎으므로 쓰지 않는다).
    사용자가 직접 불리언·인용구를 넣은 질의는 그대로 통과시킨다."""
    if re.search(r'\b(AND|OR|ANDNOT)\b|"', q or ""):
        return f"all:{q}"
    toks = (q or "").split()
    if not toks:
        return f"all:{q}"
    return " AND ".join(f"all:{t}" for t in toks)


def search_arxiv(q, limit, year_from=None):
    """arXiv Atom 검색. --year-from은 submittedDate 범위 필터로 전달한다(레드팀 R2:
    다른 소스와 달리 조용히 무시돼 최신성 질문에서 슬롯을 옛 프리프린트가 잠식했음).
    날짜 절이 안 먹으면(구문 미지원 등) 절 없이 재조회 후 클라이언트측으로 거른다.
    레드팀 R4: ①다중 토큰 질의가 OR로 파싱되던 것을 _arxiv_query가 AND로 고정,
    ②재조회 가드가 클라이언트측 필터 **이전**의 out을 보고 있어(날짜 절이 무시되면
    out은 연도 무관 노이즈로 가득 차 비어있지 않다) 절대 발동하지 않던 것을 필터
    **뒤**로 옮겼다 — 그 탓에 필터가 노이즈를 전멸시켜 '무수확'(=문헌 부재 신호)이
    거짓으로 전파됐다(실측: quantum error correction surface, year_from=2025 →
    스크립트 2건 vs 정확 질의 286건)."""
    def _call(sq):
        params = {"search_query": sq, "max_results": str(limit), "sortBy": "relevance"}
        # 레드팀 R6: 재조회가 0초 간격으로 붙어 나가 429를 자초했다 — _fetch_arxiv_meta와
        # 같은 3초 간격 게이트(_ARXIV_LAST)를 공유한다(같은 프로세스의 후속 arXiv 조회도 이 시각을 본다)
        gap = 3.0 - (time.time() - _ARXIV_LAST[0])
        if gap > 0:
            time.sleep(gap)
        r = http_get("https://export.arxiv.org/api/query?" + urllib.parse.urlencode(params))
        _ARXIV_LAST[0] = time.time()
        return r

    base = _arxiv_query(q)
    sq = base
    if year_from:
        sq = f"{base} AND submittedDate:[{int(year_from)}01010000 TO 299912312359]"
    body, code = _call(sq)
    if not body:
        return [], f"arxiv HTTP {code}"
    out = _arxiv_parse(body)
    if not year_from:
        return out, None

    def _filt(items):
        return [p for p in items if p.get("year") is None or p["year"] >= int(year_from)]

    filtered = _filt(out)
    # 날짜 절이 무시된 경우의 실패 양상은 '빈 결과'가 아니라 '연도 무관 노이즈'다 —
    # 필터 뒤에서 판정해야 재조회가 실제로 발동한다
    if not filtered and out:
        body2, c2 = _call(base)
        if body2:
            filtered = _filt(_arxiv_parse(body2))
        else:
            # 레드팀 R6: 재조회가 429/전송오류로 죽으면 '조회했으나 0건'(empty)이 아니라
            # 소스오류로 돌려준다 — 부재와 장애의 구분 원칙
            return [], f"arxiv HTTP {c2} (year_from 재조회 실패)"
    if not filtered:
        # '조회했으나 0건'과 '날짜 절 미적용 후 클라이언트 필터로 전멸'을 구분해 둔다 —
        # 후자를 '문헌 부재'로 읽으면 거짓 부재 결론이 하류로 전파된다
        print(f"# arxiv: year_from={year_from} 적용 후 0건"
              f"(날짜 절 미적용 가능 — 클라이언트 필터 결과)", file=sys.stderr)
    return filtered, None


# 학술 제목에 실제로 섞여 들어오는 마크업 태그만 화이트리스트로 제거한다.
# `</?[a-z]+>` 식 범용 패턴은 'A <Survey>'처럼 꺾쇠 안의 내용어까지 지워 제목을
# 훼손하므로 쓰지 않는다.
MARKUP_TAG_RE = re.compile(
    r"</?(?:i|b|u|em|strong|sub|sup|it|bold|italic|br|p|span|scp|sc)\s*/?>", re.I)


def _clean_title(t):
    """제목의 HTML 엔티티·마크업 태그 제거(레드팀 R4).
    Europe PMC는 '&lt;i&gt;via&lt;/i&gt;' 꼴로, OpenAlex·Crossref는 원시 <i>·<sub>
    태그로 마크업을 흘린다. 검색 출력의 제목이 그대로 refs.json에 옮겨지므로
    (SKILL.md §3 전역 P# 표 → §6), 정리하지 않으면 실재 논문이 MISMATCH(exit 3
    = 가짜 인용, 제거 지시)로 낙인찍히고 DOI 없는 항목의 dedup도 깨진다."""
    if not t:
        return t
    t = MARKUP_TAG_RE.sub("", html.unescape(t))
    return re.sub(r"\s+", " ", t).strip()


def search_europepmc(q, limit, year_from=None):
    """Europe PMC REST(무키·무료). PubMed 상위집합 + 전문(full-text) 색인 검색 +
    프리프린트(source=PPR). 제목·초록만 색인하는 타 소스가 놓치는 '본문에만 등장하는
    기법·장비·데이터셋' 조건을 회수한다(레드팀 R2). METHODS:"..." 같은 섹션 한정
    구문도 query에 그대로 쓸 수 있다."""
    query = q
    if year_from:
        query = f"({q}) AND (FIRST_PDATE:[{int(year_from)}-01-01 TO 2999-12-31])"
    params = {"query": query, "format": "json", "pageSize": str(limit),
              "resultType": "lite", "email": EMAIL}
    body, code = http_get(
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urllib.parse.urlencode(params))
    if not body:
        return [], f"epmc HTTP {code}"
    out = []
    for r in (json.loads(body).get("resultList") or {}).get("result", []):
        yr = re.match(r"(\d{4})", str(r.get("pubYear") or ""))
        cit = r.get("citedByCount")
        out.append({
            "source": "epmc",
            "id": f"{r.get('source')}:{r.get('id')}",
            "doi": norm_doi(r.get("doi")),
            "title": _clean_title((r.get("title") or "").rstrip(".")),
            "year": int(yr.group(1)) if yr else None,
            "venue": r.get("journalTitle") or ("preprint(PPR)" if r.get("source") == "PPR" else None),
            # 미제공 시 None — 0(미인용)과 구분
            "citations": cit if isinstance(cit, int) else None,
            "is_oa": True if r.get("isOpenAccess") == "Y" else None,
            "authors": [x.strip() for x in (r.get("authorString") or "").split(",")[:3] if x.strip()],
        })
    return out, None


def search_pubmed(q, limit, year_from=None):
    """NCBI E-utilities (무키, 3req/s — esearch 후 0.34s 대기). NCBI_API_KEY 있으면 10req/s."""
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    key = os.environ.get("NCBI_API_KEY")
    params = {"db": "pubmed", "term": q, "retmax": str(limit),
              "retmode": "json", "sort": "relevance"}
    if year_from:
        params.update({"datetype": "pdat", "mindate": str(year_from), "maxdate": "3000"})
    if key:
        params["api_key"] = key
    body, code = http_get(f"{base}/esearch.fcgi?" + urllib.parse.urlencode(params))
    if not body:
        return [], f"pubmed HTTP {code}"
    ids = (json.loads(body).get("esearchresult") or {}).get("idlist") or []
    if not ids:
        return [], None
    time.sleep(0.34 if not key else 0.11)
    params2 = {"db": "pubmed", "id": ",".join(ids), "retmode": "json"}
    if key:
        params2["api_key"] = key
    body2, code2 = http_get(f"{base}/esummary.fcgi?" + urllib.parse.urlencode(params2))
    if not body2:
        return [], f"pubmed esummary HTTP {code2}"
    res = json.loads(body2).get("result") or {}
    out = []
    for uid in res.get("uids", []):
        it = res.get(uid) or {}
        doi = pmc = None
        for aid in it.get("articleids", []):
            if aid.get("idtype") == "doi":
                doi = norm_doi(aid.get("value"))
            elif aid.get("idtype") == "pmc":
                pmc = aid.get("value")
        ym = re.match(r"(\d{4})", it.get("pubdate") or "")
        out.append({
            "source": "pubmed",
            "id": f"pmid:{uid}",
            "doi": doi,
            "title": _clean_title(it.get("title") or "").rstrip("."),
            "year": int(ym.group(1)) if ym else None,
            "venue": it.get("fulljournalname") or it.get("source"),
            "citations": None,  # PubMed는 인용수 미제공 — 0(미인용)과 구분해 미상(None)
            "is_oa": True if pmc else None,
            "authors": [a.get("name") for a in (it.get("authors") or [])[:3]],
        })
    return out, None


# ---------------- commands ----------------

REL_W = 1.5  # 소스 relevance 보너스 가중(1위 +1.5 … 10위 +0.15) — 인용수 축을 뒤집지
             # 않으면서 동점·근접 구간에서 적합도 신호를 살린다


OVERLAP_W = 8.0  # 질의-제목 겹침 가중: OVERLAP_W*(overlap-0.5) → 겹침 1.0=+4.0 · 0.5=0 · 0=-4.0


def _query_overlap(query, title):
    """질의 토큰 중 제목에 있는 비율(0~1). 양쪽을 _canon 토큰화 → _stem → TITLE_STOP 제거.
    질의에 내용어가 없으면 None(랭킹 영향 없음).
    round4 실측: 인용 log+최신성+소스 relevance만으로는 무관 고인용 논문(철회된
    암호화폐 리뷰·'Assetization'·Sub-Saharan NPL)이 상위를 차지해 bundle을 6회 반복해야
    했다 — 겹침을 랭킹 1축으로 승격."""
    qt = {_stem(t) for t in _canon(query or "").split()} - TITLE_STOP
    if not qt:
        return None
    tt = {_stem(t) for t in _canon(title or "").split()} - TITLE_STOP
    return len(qt & tt) / len(qt)


def _overlap_term(ov):
    """겹침 → rank_key 가감(선형·단조). 0=-4.0(인용수로 못 올라옴)·0.5 중립·1.0=+4.0.
    log1p(5000)-log1p(50)≈4.6 + 소스 relevance 1위 보너스 1.5가 있어도
    '한 단어만 겹치는 5000인용'(0.2 → -2.4)이 '전부 겹치는 50인용'(1.0 → +4.0)을
    못 이기게 폭을 잡았다. 0.4(5단어 중 2개) 이상 겹치면 인용수가 다시 이긴다."""
    if ov is None:
        return 0.0
    return OVERLAP_W * (min(max(ov, 0.0), 1.0) - 0.5)


def rank_key(p, overlap=None):
    """인용수(log) + 최신성 + 소스 relevance 보너스 (+ 질의-제목 겹침 가감).
    레드팀 R2: 각 API가 반환한 relevance 순위를 통째로 버려 질의와 정확히 일치하는
    논문이 무관한 고인용 논문에 밀리던 허점 — _rank(소스 내 0-based 순위)를 반영.
    round5: overlap(_query_overlap 값)이 주어지면 _overlap_term을 더한다. None이면
    기존 동작과 동일(하위호환)."""
    import math
    cit = math.log1p(p.get("citations") or 0)
    year = p.get("year") or 0
    recency = max(0, (year - 2015)) * 0.25
    rk = p.get("_rank")
    rel = REL_W / (1 + rk) if isinstance(rk, int) else 0.0
    return cit + recency + rel + _overlap_term(overlap)


def _merge_twin(q, p):
    """중복 항목 병합: 인용수 최댓값·OA true 우선·DOI/PDF 보존·소스 표기."""
    if not (q.get("citations") is None and p.get("citations") is None):
        q["citations"] = max(q.get("citations") or 0, p.get("citations") or 0)
    q["is_oa"] = q.get("is_oa") or p.get("is_oa")
    q["doi"] = q.get("doi") or p.get("doi")
    if p.get("citations_s2") is not None:
        q["citations_s2"] = max(q.get("citations_s2") or 0, p["citations_s2"])
    if p.get("pdf") and not q.get("pdf"):
        q["pdf"] = p["pdf"]
    if p.get("is_retracted"):
        q["is_retracted"] = True
    rq, rp = q.get("_rank"), p.get("_rank")
    if isinstance(rp, int) and (not isinstance(rq, int) or rp < rq):
        q["_rank"] = rp  # 어느 한 소스에서라도 상위면 상위로 취급
    src = p.get("source")
    if src and src != q.get("source") and src not in (q.get("also_in") or []):
        q.setdefault("also_in", []).append(src)


def merge_and_rank(pool, limit, oa_only=False, query=None, include_retracted=False):
    """검색 결과 병합·랭킹 (오프라인 테스트 가능하게 cmd_search에서 분리).
    include_retracted=False(기본)면 is_retracted=True 행을 **제거하지 않고 맨 뒤로** 보낸다
    (round4 게이트: 철회 논문이 ⚠️ 단 채 2위에 올라 bundle 상위 초록 슬롯을 소모).
    철회 사실 자체가 답변에 유용할 수 있고 검색 결과 은닉은 규약 위반이라 행은 남긴다.
    include_retracted=True면 정렬 위치 그대로(기존 동작).
    query가 주어지면 각 행에 overlap(=_query_overlap(query, title), 디버그·JSON 출력용)을
    기록하고 rank_key에 겹침 가감을 넣는다. 겹침 0 행은 인용수와 무관하게 겹침>0 행
    아래 티어로 내린다(round4 실측: 무관 고인용 논문이 인용수만으로 상위 점유).
    query=None이면 기존 동작 100% 동일.
    1차: DOI(없으면 정규화 제목) 키로 dedup.
    2차: 정규화 제목+연도(±1)로 키가 갈린 트윈 병합 — arXiv 프리프린트(doi=None)와
        출판본(DOI 보유), DataCite/출판사 DOI 이중 등재가 limit 슬롯을 이중 잠식하는
        것을 방지. 연도가 2년 이상 다르면 동명 이논문일 수 있어 병합하지 않는다.
    절단 시 (a) 인용수 미상(citations=None — arxiv·pubmed. epmc는 citedByCount를
        제공하므로 해당 없음)과 (b) 소스
    relevance 상위(_rank<=2) 항목이 인용 가중 랭킹에 전멸하지 않게 최소 슬롯을
    예약한다((b)는 레드팀 R2 — 질의 정확 일치 논문이 무관 고인용 논문에 밀려
    limit 밖으로 탈락하던 리콜 손실)."""
    seen, merged = {}, []
    # 비논문 레코드는 소스 단에서 걸러지지만, 병합 단계에서도 한 번 더 막는다
    pool = [p for p in pool if (p.get("type") or "") not in NON_PAPER_TYPES]
    for p in pool:
        k = p.get("doi") or _canon(p.get("title") or "")[:80]
        if not k:
            continue
        if k in seen:
            _merge_twin(seen[k], p)
        else:
            seen[k] = p
            merged.append(p)
    by_title, dedup2 = {}, []
    for p in merged:
        tk = _canon(p.get("title") or "")[:80]
        target = None
        for q in by_title.get(tk, []):
            y1, y2 = p.get("year"), q.get("year")
            if y1 is None or y2 is None or abs(int(y1) - int(y2)) <= 1:
                target = q
                break
        if target is not None:
            _merge_twin(target, p)
        else:
            if tk:
                by_title.setdefault(tk, []).append(p)
            dedup2.append(p)
    merged = dedup2
    if oa_only:
        # --oa-only는 전 소스 공통 필터 — openalex만 필터하고 나머지 소스가 그대로
        # 섞이던 허점 봉합. is_oa 미상(crossref 단독 등) 항목도 제외된다.
        merged = [p for p in merged if p.get("is_oa")]
    if query is not None:
        for p in merged:
            p["overlap"] = _query_overlap(query, p.get("title") or "")

    def _key(p):
        # query가 있으면 겹침 0(질의 토큰이 제목에 하나도 없음)은 하위 티어로 고정 —
        # 선형 감점만으로는 인용수(상한 없음)가 결국 뚫는다(log1p(5000)=8.5 > 4.0)
        if query is None:
            return (1, rank_key(p))
        ov = p.get("overlap")
        return (0 if ov == 0 else 1, rank_key(p, ov))

    merged.sort(key=_key, reverse=True)
    if len(merged) > limit:
        head, tail = merged[:limit], merged[limit:]
        cap = max(1, limit // 5)
        reserved, rids = [], set()

        def _reserve(pick, only_if_absent=True):
            # only_if_absent: head에 같은 부류가 하나도 없을 때만 예약(대표성 확보용)
            if only_if_absent and any(pick(p) for p in head):
                return
            added = 0
            for p in tail:
                if added >= cap:
                    break
                if pick(p) and id(p) not in rids:
                    reserved.append(p); rids.add(id(p)); added += 1

        _reserve(lambda p: p.get("citations") is None)
        # relevance 상위는 '대표 하나'로 갈음할 수 없다 — 탈락한 항목 자체를 끌어올린다
        _reserve(lambda p: isinstance(p.get("_rank"), int) and p["_rank"] <= 2,
                 only_if_absent=False)
        if reserved:
            reserved = reserved[: max(1, limit // 2)]
            keep = [p for p in head if id(p) not in rids][: limit - len(reserved)]
            head = keep + reserved
            head.sort(key=_key, reverse=True)
        merged = head
    if not include_retracted:
        # 철회 행은 맨 뒤로(안정 분할 — 비철회끼리·철회끼리 상대 순서 유지). 제거 X, ⚠️ 유지.
        merged = ([p for p in merged if not p.get("is_retracted")]
                  + [p for p in merged if p.get("is_retracted")])
    return merged


# 소스별 per-page 상한 — 초과하면 그 소스가 HTTP 400으로 전멸한다(레드팀 R2)
SOURCE_CAP = {"openalex": 200, "crossref": 1000, "s2": 100,
              "arxiv": 2000, "pubmed": 10000, "epmc": 1000}
KNOWN_SOURCES = tuple(SOURCE_CAP)


def parse_sources(spec):
    """--sources 파싱 → (선택 소스, 미인식 토큰). 공백·대소문자를 정규화한다.
    레드팀 R2: 'openAlex'·' pubmed'(쉼표 뒤 공백)·오타가 조용히 버려져 해당 소스가
    아예 조회되지 않는데도 '소스오류: 없음'으로 출력되던 무성 실패 봉합."""
    picked, unknown = [], []
    for tok in (spec or "").split(","):
        t = tok.strip().lower()
        if not t:
            continue
        if t in SOURCE_CAP:
            if t not in picked:
                picked.append(t)
        else:
            unknown.append(tok.strip())
    return picked, unknown


def run_search(query, limit, sources_spec, year_from=None, oa_only=False, include_retracted=False):
    """검색 코어(cmd_search·cmd_bundle 공용): 소스 병렬 호출 → 병합·랭킹.
    반환 dict: sources(선택 소스) · errors · empty(조회했으나 0건) · per_source(소스별 건수)
    · merged(랭킹된 결과, 각 행에 _rank) · pool(병합 전 총 건수).
    미인식/빈 소스는 exit 2(기존 cmd_search 계약 그대로)."""
    per = max(limit, 5)
    pool, errors, empty = [], [], []
    sources, unknown = parse_sources(sources_spec)
    if unknown:
        print(f"ERROR: 알 수 없는 소스 {unknown} — 사용 가능: {','.join(KNOWN_SOURCES)}",
              file=sys.stderr)
        sys.exit(2)
    if not sources:
        print("ERROR: --sources가 비었다", file=sys.stderr)
        sys.exit(2)

    # 소스별 호출 계획 — 순서는 이전 직렬 호출 순서와 동일(병합 tie-break·errors 순서 보존)
    plan = []
    if "openalex" in sources:
        plan.append(("openalex", search_openalex, (year_from, oa_only)))
    if "crossref" in sources:
        plan.append(("crossref", search_crossref, (year_from,)))
    if "s2" in sources:
        plan.append(("s2", search_s2, (year_from,)))
    if "arxiv" in sources:
        plan.append(("arxiv", search_arxiv, (year_from,)))
    if "pubmed" in sources:
        plan.append(("pubmed", search_pubmed, (year_from,)))
    if "epmc" in sources:
        plan.append(("epmc", search_europepmc, (year_from,)))

    def _n_for(name):
        cap = SOURCE_CAP[name]
        if per > cap:
            print(f"# {name}: --limit {per} > per-page 상한 {cap} → {cap}로 조정",
                  file=sys.stderr)
        return min(per, cap)

    def _call(name, fn, n, args):
        """한 소스 호출. 예외(타임아웃·파싱 오류 등)는 그 소스만의 오류로 격리한다 —
        나머지 소스 결과는 살아야 한다. 429/HTTP 오류는 http_get이 (None, code)로
        돌려주므로 fn이 스스로 err 문자열을 만든다(_quota_warn 경고도 거기서 그대로 뜬다)."""
        try:
            r, e = fn(query, n, *args)
        except Exception as ex:  # 소스 하나의 예외가 검색 전체를 죽이지 않게
            return [], f"{name} 예외: {type(ex).__name__}: {ex}"
        return (r or []), e

    # 상한 조정 메시지는 제출 전에 주스레드에서 순서대로 출력(스레드 출력 뒤섞임 방지)
    ns = [(name, fn, _n_for(name), args) for name, fn, args in plan]
    results = {}
    if len(ns) <= 1:
        for name, fn, n, args in ns:
            results[name] = _call(name, fn, n, args)
    else:
        # 소스별 요청을 동시에 보낸다(표준 라이브러리). 결과 수집은 plan 순서로 하므로
        # pool·errors·empty의 순서는 직렬 호출 때와 동일 → 병합·랭킹·P# 부여 불변.
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(ns)) as ex:
            futs = {name: ex.submit(_call, name, fn, n, args) for name, fn, n, args in ns}
            for name in futs:
                results[name] = futs[name].result()

    per_source = {}
    for name, _fn, _n, _args in ns:
        r, e = results[name]
        if e:
            errors.append(e)
        elif not r:
            empty.append(name)  # '조회했으나 0건'과 '조회 안 함'을 구분
        for i, p in enumerate(r):
            p["_rank"] = i  # 소스 내 relevance 순위 — rank_key 보너스·절단 예약용
        per_source[name] = len(r)
        pool.extend(r)

    merged = merge_and_rank(pool, limit, oa_only, query=query, include_retracted=include_retracted)
    return {"sources": sources, "errors": errors, "empty": empty,
            "per_source": per_source, "merged": merged, "pool": len(pool)}


def _search_exit_if_empty(merged, errors):
    """레드팀 R6: search가 0건(전 소스 오류 포함)이어도 exit 0이라 flowchart 계약
    '0 / 1(0건) / 2'와 어긋났고 bundle(0건 exit 1)과도 달랐다 — 같은 계약으로 맞춘다.
    stderr 문구에 소스오류를 실어 '거짓 부재'(장애를 문헌 없음으로 오독)를 막는다."""
    if merged:
        return
    print("ERROR: 결과 0건" + (f" — 소스오류 {errors} (문헌 부재가 아닐 수 있다 — 재시도)"
                             if errors else ""), file=sys.stderr)
    sys.exit(1)


def cmd_search(a):
    r = run_search(a.query, a.limit, a.sources, a.year_from, a.oa_only,
                   include_retracted=getattr(a, "include_retracted", False))
    sources, errors, empty, merged = r["sources"], r["errors"], r["empty"], r["merged"]
    if a.json:
        print(json.dumps({"query": a.query, "sources": sources, "errors": errors,
                          "empty_sources": empty, "results": merged},
                         ensure_ascii=False, indent=1))
        _search_exit_if_empty(merged, errors)
        return
    print(f"# search: {a.query}  ({len(merged)}건, 소스오류: {errors or '없음'}"
          f"{', 무수확: ' + str(empty) if empty else ''})")
    _search_exit_if_empty(merged, errors)
    for i, p in enumerate(merged, 1):
        oa = "OA" if p.get("is_oa") else "  "
        doi = p.get("doi") or p.get("id") or "-"
        au = ", ".join(x for x in p.get("authors", []) if x) or "?"
        rt = " ⚠️RETRACTED" if p.get("is_retracted") else ""
        rt += " ★recent-slot" if p.get("recent_reserved") else ""
        cit = p.get("citations")
        cit = "?" if cit is None else cit  # ? = 인용수 미상(arxiv·pubmed) — 0(미인용) 아님
        print(f"[P{i}] ({p.get('year')}) {p.get('title')}{rt}")
        print(f"     {au} · {p.get('venue') or '?'} · 인용 {cit} · {oa} · {doi}"
              f" · via {p['source']}{'+' + '+'.join(p.get('also_in', [])) if p.get('also_in') else ''}")



# ---------------- bundle (원샷: 검색 → dedup → 랭킹 → 상위 K편 초록) ----------------

def _arxiv_abs_url(p):
    """항목의 arXiv abs URL(있으면). id가 abs URL이거나 arxiv_id, 10.48550/* DOI."""
    aid = p.get("arxiv_id") or _arxiv_id_from(p.get("id") or "")
    doi = (p.get("doi") or "").lower()
    if not aid and doi.startswith("10.48550/arxiv."):
        aid = doi.split("10.48550/arxiv.", 1)[1]
    return f"https://arxiv.org/abs/{aid}" if aid else None


def _strip_jats(t):
    t = re.sub(r"<[^>]+>", " ", t or "")
    return html.unescape(re.sub(r"\s+", " ", t)).strip()


def _s2_fetch_abstract(doi):
    """S2 단일논문 초록 (abstract, title, err). 무키 가능·표준 라이브러리. 429·404·타임아웃은
    재시도 없이 (None, None, "s2 HTTP <code>") — bundle 병렬 조회에서 _s2_get의 5단 backoff
    (최대 50초)는 너무 느리다. 무키 공유풀 3초 간격은 _s2_get과 같은 _S2_LAST를 락으로 공유."""
    hdr = {}
    key = os.environ.get("S2_API_KEY")
    if key:
        hdr["x-api-key"] = key
    url = ("https://api.semanticscholar.org/graph/v1/paper/DOI:" + urllib.parse.quote(doi, safe="/")
           + "?fields=abstract,title")
    with _S2_LOCK:
        gap = 3.0 - (time.time() - _S2_LAST[0])
        if gap > 0:
            time.sleep(gap)
        body, code = http_get(url, headers=hdr, retries=0)
        _S2_LAST[0] = time.time()
    if not body:
        return None, None, f"s2 HTTP {code}" if code and code > 0 else "s2 연결 실패(타임아웃)"
    try:
        j = json.loads(body) or {}
    except Exception:
        return None, None, "s2 JSON 오류"
    return (j.get("abstract") or "").strip(), j.get("title"), None


def fetch_abstract(p):
    """상위 K편 초록 단건. paper 로직(OpenAlex 단건 = _fetch_doi_meta) → 없으면 Crossref
    `abstract`(JATS 제거) → 그래도 없으면 S2 단일논문(round5). 반환 (abstract, abstract_source, reason, meta).
    S2 폴백은 DOI가 앞 둘 중 어디선가 해석됐을 때만(OpenAlex 레코드 존재 또는 Crossref 200) —
    양쪽 모두 404인 DOI(오타·가짜)는 S2도 404가 대부분이라 3초 간격 공유풀을 낭비하지 않는다.
    round4 실측: 10.1016/j.irfa.2020.101646 은 openalex·crossref 초록 없음, S2에 1,406자.
    S2 제목이 행 제목과 _title_match로 어긋나면 오염 방지로 미채택(사유 "s2 제목 불일치").
    arXiv 항목(DOI 없음·10.48550/*)은 §4 규약대로 조회하지 않고 빈 초록 + abs→pdf URL 안내
    (OpenAlex의 10.48550/* 레코드는 다른 논문의 제목·초록으로 오염된 사례가 있어(레드팀 R5)
    초록을 믿을 수 없고, arXiv API는 3초 간격 강제라 병렬 조회에 맞지 않는다)."""
    doi = p.get("doi")
    if not doi or doi.lower().startswith("10.48550/"):
        url = _arxiv_abs_url(p)
        if url:
            return "", None, (f"arXiv 항목 — paper 초록은 오염 가능·oa 조회 불가. "
                              f"{url.replace('/abs/', '/pdf/')} 를 WebFetch로 직접 읽어라(§4)"), None
        return "", None, "식별자 없음(DOI·arXiv id 모두 부재) — 초록 조회 불가", None
    meta, err = _fetch_doi_meta(doi)
    if meta and meta.get("abstract"):
        return meta["abstract"], "openalex" + _cache_tag(meta.get("cache")), None, meta
    hit = cache_get("cr_abstract", doi)
    if hit and hit.get("abstract"):
        return hit["abstract"], "crossref (cache)", None, meta
    body, code = http_get("https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="/")
                          + "?" + urllib.parse.urlencode({"mailto": EMAIL}))
    if body:
        try:
            ab = _strip_jats((json.loads(body).get("message") or {}).get("abstract"))
        except Exception:
            ab = ""
        if ab:
            cache_put("cr_abstract", doi, {"abstract": ab})  # 초록 있음만 캐시(부재는 재조회)
            return ab, "crossref", None, meta
    if not meta and not body:
        return "", None, f"조회 실패({err}; crossref HTTP {code})", None
    reason = ("초록 미제공(openalex·crossref 모두 없음) — oa로 PDF 위치 확인" if meta
              else f"조회 실패({err}; crossref 초록 없음)")
    # S2 단일논문 폴백 — 초록 있음만 캐시(kind=s2_abstract, 30일). 실패는 사유에 덧붙이고 조용히.
    hit = cache_get("s2_abstract", doi)
    if hit and hit.get("abstract"):
        return hit["abstract"], "s2 (cache)", None, meta
    s2_ab, s2_title, s2_err = _s2_fetch_abstract(doi)
    if s2_err:
        return "", None, f"{reason}; {s2_err}", meta
    if s2_ab:
        if p.get("title") and s2_title and not _title_match(p["title"], s2_title)[0]:
            return "", None, f"{reason}; s2 제목 불일치(오염 방지로 미채택: {s2_title[:60]!r})", meta
        cache_put("s2_abstract", doi, {"abstract": s2_ab})
        return s2_ab, "s2", None, meta
    return "", None, reason, meta


def _safe_fetch_abstract(p):
    try:
        return fetch_abstract(p)
    except Exception as ex:  # 한 편의 예외가 bundle 전체를 죽이지 않게
        return "", None, f"예외: {type(ex).__name__}: {ex}", None


def _bundle_default_out(query):
    slug = re.sub(r"[^A-Za-z0-9가-힣]+", "_", query).strip("_")[:40] or "bundle"
    d = os.path.join(tempfile.gettempdir(), "scholar-bundle")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{slug}_{time.strftime('%Y%m%d-%H%M%S')}.json")


RECENT_YEARS = 5  # bundle --recent-slots의 '최근' 정의: 현재 연도 - 5 이상


def _recent_cut(now_year=None):
    return int(now_year or time.localtime().tm_year) - RECENT_YEARS


def _is_recent(p, cut):
    y = p.get("year")
    try:
        return y is not None and int(y) >= cut
    except (TypeError, ValueError):
        return False


def _reserve_recent(eligible, top, recent_slots, cut):
    """상위 K(head=eligible[:top]) 중 최소 recent_slots편을 최근(year>=cut) 논문으로 예약.
    merge_and_rank의 최소 슬롯 예약과 같은 패턴 — head에 최근 논문이 부족하면 tail의
    최근 논문(랭킹 순)을 끌어올리고, head 뒤쪽의 비최근 행을 그만큼 뺀다. tail에 예약
    대상이 없으면 head 그대로. 반환 (head, 끌어올린 행 id 집합).
    round5: 인용 log 축이 고인용 구논문 쪽으로 기울고 최신성 보너스(연 0.25)가 약해
    2015년 이후 실증을 bundle이 전혀 회수 못 하던 문제."""
    head = list(eligible[:max(0, top)])
    n = min(int(recent_slots or 0), len(head))
    if n <= 0:
        return head, set()
    need = n - sum(1 for p in head if _is_recent(p, cut))
    if need <= 0:
        return head, set()
    cands = [p for p in eligible[len(head):] if _is_recent(p, cut)][:need]
    if not cands:
        return head, set()
    dropped = 0
    for i in range(len(head) - 1, -1, -1):
        if dropped >= len(cands):
            break
        if not _is_recent(head[i], cut):
            head.pop(i)
            dropped += 1
    head = head + cands  # cands는 이미 랭킹 순
    return head, {id(p) for p in cands}


def build_bundle(query, limit=10, top=8, sources_spec="openalex,crossref,s2",
                 year_from=None, oa_only=False, workers=8, include_retracted=False,
                 recent_slots=2, now_year=None):
    """bundle 코어(오프라인 테스트 가능). 검색 → 상위 K편 초록 병렬 조회 → JSON dict.
    include_retracted=False(기본)면 상위 K 초록 head 선정에서 is_retracted 행을 건너뛰고
    다음 행으로 K편을 채운다(철회 행은 결과에 남되 abstract_reason에 제외 사유).
    include_retracted=True면 기존 동작(정렬 위치·슬롯 유지).
    recent_slots(기본 2): 상위 K 중 최소 N편을 최근 5년(현재 연도-5 이상) 논문으로 예약
    (_reserve_recent). 끌어올린 행은 P# 순서상 head 바로 뒤로 승격되고 recent_reserved=True.
    0이면 예약 없음(기존 동작). search에는 적용하지 않는다(출력 순서 계약 유지).
    counts.recent = head(초록 슬롯) 안의 최근 5년 편수."""
    t0 = time.monotonic()
    r = run_search(query, limit, sources_spec, year_from, oa_only, include_retracted=include_retracted)
    t_search = time.monotonic() - t0
    merged = r["merged"]
    if include_retracted:
        eligible = list(merged)
    else:
        eligible = [p for p in merged if not p.get("is_retracted")]
    cut = _recent_cut(now_year)
    head, promoted = _reserve_recent(eligible, top, recent_slots, cut)
    top_n = len(head)
    head_ids = {id(p) for p in head}
    if promoted:
        # 예약 행을 head 뒤로 승격(merge_and_rank 예약 패턴과 동일) — 나머지 상대순서 유지
        merged = [p for p in merged if id(p) in head_ids] + [p for p in merged if id(p) not in head_ids]
    n_recent = sum(1 for p in head if _is_recent(p, cut))
    t1 = time.monotonic()
    fetched = []
    if head:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(workers, len(head)))) as ex:
            futs = [ex.submit(_safe_fetch_abstract, p) for p in head]
            fetched = [f.result() for f in futs]
    t_abs = time.monotonic() - t1
    fetched_by = {id(p): fr for p, fr in zip(head, fetched)}
    papers = []
    for i, p in enumerate(merged, 1):
        row = {"pid": f"P{i}"}
        row.update({k: v for k, v in p.items() if k != "_rank"})
        if id(p) in promoted:
            row["recent_reserved"] = True
        if id(p) in head_ids:
            ab, src, reason, meta = fetched_by[id(p)]
            if meta:
                for k in ("venue", "year", "authors", "oa_url", "type", "arxiv_id"):
                    if meta.get(k) is not None and (row.get(k) in (None, "", []) or k == "authors"):
                        row[k] = meta[k]
                row["is_oa"] = bool(row.get("is_oa") or meta.get("is_oa"))  # OA true 우선(_merge_twin과 동일)
                if meta.get("is_retracted"):
                    row["is_retracted"] = True
                if meta.get("citations") is not None:
                    row["citations"] = max(row.get("citations") or 0, meta["citations"])
            row["abstract"], row["abstract_source"] = ab or "", src
            row["abstract_reason"] = reason
        elif p.get("is_retracted") and not include_retracted:
            row["abstract"], row["abstract_source"] = "", None
            row["abstract_reason"] = "철회 논문 — 상위 초록 슬롯에서 제외(필요하면 paper <doi>)"
        else:
            row["abstract"], row["abstract_source"] = "", None
            row["abstract_reason"] = f"상위 {top_n}편 밖 — 초록 미조회(필요하면 paper <doi>)"
        for k in ("doi", "title", "year", "venue", "citations", "citations_s2", "is_oa", "oa_url"):
            row.setdefault(k, None)  # 스키마 고정 — 소스별로 빠지는 키(venue 등)를 채운다
        row["oa"] = {"is_oa": row.get("is_oa"), "oa_url": row.get("oa_url")}
        papers.append(row)
    n_abs = sum(1 for x in papers if x.get("abstract"))
    return {
        "query": query,
        "sources_used": r["sources"],
        "errors": r["errors"],
        "empty_sources": r["empty"],
        "counts": {"per_source": r["per_source"], "merged": len(merged), "top": top_n,
                   "abstracts": n_abs, "recent": n_recent},
        "papers": papers,
        "elapsed_sec": {"search": round(t_search, 2), "abstracts": round(t_abs, 2),
                        "total": round(time.monotonic() - t0, 2)},
    }


def render_bundle(b):
    c, e = b["counts"], b["elapsed_sec"]
    per = " / ".join(f"{k} {v}" for k, v in c["per_source"].items()) or "-"
    lines = [f"# bundle: {b['query']}",
             f"# 검색 {per} → 병합 {c['merged']} ({e['search']}초) · 초록 {c['abstracts']}/{c['top']} "
             f"({e['abstracts']}초) · 최근5년 {c.get('recent', '?')}/{c['top']} · 총 {e['total']}초",
             f"# 소스오류: {b['errors'] or '없음'}"
             f"{' · 무수확: ' + str(b['empty_sources']) if b.get('empty_sources') else ''}"]
    for p in b["papers"]:
        oa = "OA" if p.get("is_oa") else "  "
        doi = p.get("doi") or p.get("id") or "-"
        au = ", ".join(x for x in (p.get("authors") or [])[:3] if x) or "?"
        rt = " ⚠️RETRACTED" if p.get("is_retracted") else ""
        rt += " ★recent-slot" if p.get("recent_reserved") else ""
        cit = p.get("citations")
        cit = "?" if cit is None else cit
        s2 = f" (S2 {p['citations_s2']})" if p.get("citations_s2") is not None else ""
        via = p.get("source", "?") + ("+" + "+".join(p["also_in"]) if p.get("also_in") else "")
        lines.append(f"[{p['pid']}] ({p.get('year')}) {p.get('title')}{rt}")
        lines.append(f"     {au} · {p.get('venue') or '?'} · 인용 {cit}{s2} · {oa} · {doi} · via {via}")
        if p.get("abstract"):
            lines.append(f"     초록 O ({p['abstract_source']}, {len(p['abstract'])}자)")
        else:
            lines.append(f"     초록 X — {p.get('abstract_reason')}")
    return "\n".join(lines)


def cmd_bundle(a):
    b = build_bundle(a.query, a.limit, a.top, a.sources, a.year_from, a.oa_only,
                     include_retracted=getattr(a, "include_retracted", False),
                     recent_slots=getattr(a, "recent_slots", 2))
    out = a.out or _bundle_default_out(a.query)
    d = os.path.dirname(os.path.abspath(out))
    os.makedirs(d, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(b, fh, ensure_ascii=False, indent=1)
    if a.json:
        print(json.dumps(b, ensure_ascii=False, indent=1))
    else:
        print(render_bundle(b))
        print(f"# JSON → {out}  (project add <slug> --from {out} [--pick P1,P3])")
    if not b["papers"]:
        print("ERROR: 결과 0건" + (f" — 소스오류 {b['errors']}" if b["errors"] else ""), file=sys.stderr)
        sys.exit(1)


def cmd_paper(a):
    ident = a.id
    if not ident.startswith("W") and "openalex" not in ident:
        nd = norm_doi(ident)
        if not nd:
            print("ERROR: 잘못된 DOI", file=sys.stderr)
            sys.exit(1)
        # DOI 경로는 30일 캐시(철회 플래그만 매번 실 조회 — _oa_work_by_doi)
        w, err, cached = _oa_work_by_doi(nd)
        if w is None:
            print(f"ERROR: {err}", file=sys.stderr)
            sys.exit(1)
        out = {k: w.get(k) for k in ("title", "year", "doi", "venue", "citations", "type",
                                     "is_retracted", "is_oa", "oa_url", "authors", "abstract",
                                     "referenced_works_count")}
        out["title"] = w.get("display_name") or w.get("title")
        out["cache"] = bool(cached)
        # 레드팀 R5: §4의 "10.48550/* DOI는 paper가 404"는 거짓이었다 — 실제로는 OpenAlex가
        # 다른 논문의 제목·초록을 단 레코드를 exit 0으로 돌려준다(Wei 2022 CoT → 'BNAI,
        # NO-TOKEN…', 표본 5건 중 3건 오염). graph 전용이던 arXiv API 재확인을 paper에도
        # 건다: 불일치면 초록을 내보내지 않고(ledger 오염 차단) exit 3, 재확인 실패면
        # '미확인'으로 표시하고 exit 5(fail-closed — 성공으로 위장하지 않는다).
        if re.match(r"(?i)^10\.48550/arxiv\.", nd):
            true_title, src = _authoritative_title(nd)
            if not true_title:
                # arXiv API는 3초 간격·429가 잦다 — 등록기관(DataCite) 제목으로 2차 재확인
                dt, dsrc = _datacite_title(nd)
                if dt:
                    true_title, src = dt, f"datacite(arxiv {src})"
                else:
                    src = f"{src}, {dsrc}"
            if true_title:
                ok, _sim, _how = _title_match(out["title"] or "", true_title)
                if not ok:
                    out["title_openalex"] = out["title"]
                    out["title"] = true_title
                    out["abstract"] = None
                    out["warning"] = ("⚠ 제목 오염 의심 — OpenAlex 레코드가 다른 논문의 제목·초록을"
                                      " 달고 있다. 초록 미출력. arXiv abs→pdf를 WebFetch로 직접 읽어라(§4)")
                    out["exit"] = 3
                else:
                    out["title_source"] = src
            else:
                out["warning"] = (f"⚠ arXiv 재확인 실패({src}) — OpenAlex 제목·초록의 오염 여부 미확인."
                                  " 초록을 ledger에 넣기 전 arXiv abs 페이지와 제목을 대조하라")
                out["exit"] = 5
    else:
        # W-id 경로는 캐시하지 않는다(키가 DOI가 아님)
        url = (f"https://api.openalex.org/works/{ident}?"
               + urllib.parse.urlencode({"mailto": EMAIL}))
        body, code = http_get(url)
        if not body:
            print(f"ERROR: OpenAlex HTTP {code}", file=sys.stderr)
            sys.exit(1)
        w = json.loads(body)
        out = {
            "title": w.get("display_name"),
            "year": w.get("publication_year"),
            "doi": norm_doi(w.get("doi")),
            "venue": ((w.get("primary_location") or {}).get("source") or {}).get("display_name"),
            "citations": w.get("cited_by_count"),
            "type": w.get("type"),
            "is_retracted": bool(w.get("is_retracted")),
            "is_oa": (w.get("open_access") or {}).get("is_oa"),
            "oa_url": (w.get("open_access") or {}).get("oa_url"),
            "authors": [x.get("author", {}).get("display_name") for x in w.get("authorships", [])],
            "abstract": uninvert_abstract(w.get("abstract_inverted_index")),
            "referenced_works_count": len(w.get("referenced_works") or []),
            "cache": False,
        }
    rc = out.pop("exit", 0)
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        if rc:
            sys.exit(rc)
        return
    if out["is_retracted"]:
        print("⚠️ RETRACTED — 철회된 논문. 인용 금지(철회 사실 자체를 다루는 경우 제외).")
    elif out["is_retracted"] is None:
        print("⚠️ 철회 여부 미확인 — 메타는 캐시, 철회 실 조회(OpenAlex)가 실패했다. verify로 확인.")
    if out.get("warning"):
        print(out["warning"])
        if out.get("title_openalex"):
            print(f"  OpenAlex 표기: {out['title_openalex']!r}")
    print(f"{out['title']} ({out['year']}){_cache_tag(out['cache'])}")
    print(f"저자: {', '.join(filter(None, out['authors'][:8]))}")
    print(f"게재: {out['venue']} · 인용 {out['citations']} · DOI {out['doi']} · OA={out['is_oa']}")
    if out.get("oa_url"):
        print(f"OA URL: {out['oa_url']}")
    print(f"\n[초록]\n{out['abstract'] or '(초록 미제공)'}")
    if rc:
        sys.exit(rc)


def cmd_oa(a):
    doi = norm_doi(a.doi)
    if not doi:  # paper/snowball과 동일한 가드 — quote(None) TypeError 트레이스백 방지
        print("ERROR: 잘못된 DOI", file=sys.stderr); sys.exit(1)
    body, code = http_get(f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}?email={EMAIL}")
    if not body:
        print(f"NOT_FOUND: Unpaywall HTTP {code}")
        sys.exit(2 if code == 404 else 1)
    j = json.loads(body)
    best = j.get("best_oa_location") or {}
    out = {"doi": doi, "is_oa": j.get("is_oa"), "oa_status": j.get("oa_status"),
           "pdf_url": best.get("url_for_pdf"), "page_url": best.get("url"),
           "version": best.get("version"), "license": best.get("license")}
    print(json.dumps(out, ensure_ascii=False, indent=1))


CSL_STYLES = {"apa": "apa", "mla": "modern-language-association",
              "chicago": "chicago-author-date"}


def cmd_cite(a):
    doi = norm_doi(a.doi)
    if not doi:
        print("ERROR: 잘못된 DOI", file=sys.stderr); sys.exit(1)
    style = CSL_STYLES.get(a.style, a.style)
    body, code = http_get(
        f"https://doi.org/{urllib.parse.quote(doi)}",
        headers={"Accept": f"text/x-bibliography; style={style}; locale=en-US"})
    if not body:
        print(f"ERROR: doi.org HTTP {code}", file=sys.stderr)
        sys.exit(1)
    print(body.strip())


def _canon(s):
    # 레드팀 R4: 제목에 섞여 들어오는 마크업을 먼저 벗긴다. Europe PMC는 HTML
    # 엔티티(&lt;i&gt;)로, OpenAlex·Crossref는 원시 태그(<i>·<sub>)로 흘리는데,
    # 이걸 두면 'lt i gt'·'i' 같은 파편이 편측 잉여로 남아 실재 논문을
    # token-addition MISMATCH(=가짜 인용, exit 3)로 낙인찍는다.
    s = MARKUP_TAG_RE.sub(" ", html.unescape(s or ""))
    # 레드팀 R2: 발음구별부호(Müller/Gómez)가 [^a-z...] 치환으로 토큰이 쪼개져
    # ASCII 표기 인용(Muller)과 대조 실패하던 허점 — NFKD 분해 후 결합문자 제거로
    # ASCII 폴딩. 한글은 NFC 재조합으로 무손상(자모는 결합문자 아님).
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = unicodedata.normalize("NFC", s)
    return re.sub(r"[^a-z0-9가-힣]+", " ", s.lower()).strip()


def _part_marker(s):
    """'Part II' / 'Study 2' 류 시리즈 표지 추출 — 시리즈물 오판 방어."""
    m = re.search(r"\b(?:part|paper|study)\s+([ivxlcdm]+|\d+)\b", _canon(s))
    return m.group(1) if m else None

# 성(姓)의 불변입자 — 판별력이 없어 교집합에서 제외한다(레드팀 R2:
# 'de Silva' vs 'de Souza', 'van Dijk' vs 'van Berg'가 {de}/{van}만으로 통과했음)
NAME_PARTICLES = {"de", "del", "della", "der", "den", "da", "das", "dos", "do",
                  "van", "von", "ter", "ten", "la", "le", "les", "du", "di",
                  "el", "al", "bin", "ibn", "abu", "mac", "mc", "of", "y", "e"}


def _author_match(claimed, actual):
    """1저자 성 대조 — 토큰 단위 교집합.
    부분문자열 오탐 방지('He'⊄'Chen', 'Li'⊄'Oliveira')하면서
    복합성('de la Cruz' vs 'Cruz', 'Smith-Jones' vs 'Smith')은 허용.
    불변입자(de/van/der…)는 교집합에서 빼 '입자만 겹치는' 오탐을 막고,
    양쪽 다 입자뿐이면 원 토큰으로 폴백한다."""
    ca, cf = _canon(claimed), _canon(actual)
    if not ca or not cf:
        return None
    sa, sf = set(ca.split()), set(cf.split())
    ta, tf = sa - NAME_PARTICLES, sf - NAME_PARTICLES
    if ta and tf:
        return bool(ta & tf)
    return bool(sa & sf)  # 성 전체가 입자로만 이루어진 예외 — 원 토큰 폴백


# 극성(부정) 어휘 — 제목 토큰 차집합에 하나라도 있으면 결론이 뒤집힌 인용이다
NEG_TOKENS = {"not", "no", "non", "none", "never", "without", "neither", "nor",
              "cannot", "lack", "lacks", "lacking", "fail", "fails", "failed",
              "failure", "absence", "absent", "unable", "ineffective",
              "insufficient", "negative", "null", "inconclusive", "unchanged"}
# 결론 강도 표지(hedge) — 실제 제목에만 있고 인용에서 사라지면 약한/의심스러운 결과가
# 확정 결과로 승격된다(레드팀 R5: 'Weak Association…'→'Association…'). 부정어와 같은
# 등급으로 막는다. _stem 적용 후 형태로 적는다(myths→myth, overestimates→overestimate).
HEDGE_TOKENS = {"weak", "weakly", "limited", "questionable", "modest", "doubtful",
                "uncertain", "inconsistent", "conflicting", "mixed", "spurious",
                "overestimated", "overestimate", "overestimation", "underestimated",
                "underestimate", "underestimation", "overstated", "exaggerated",
                "myth", "revisited", "reconsidered", "controversial", "unproven",
                "illusory", "marginal", "negligible", "insignificant", "nonsignificant",
                "equivocal", "tentative", "preliminary", "unclear", "elusive"}
# 판별력 없는 기능어 — 차집합 계산에서 제외
TITLE_STOP = {"a", "an", "the", "of", "on", "in", "for", "and", "or", "with", "to",
              "from", "by", "at", "as", "is", "are", "was", "were", "be", "been",
              "its", "their", "this", "that", "these", "those", "via", "into",
              "between", "among", "over", "under", "after", "before", "during",
              "do", "doe", "did", "has", "have", "had", "it", "s"}
# 레드팀 R6: 시점·범위 극성쌍 — TITLE_STOP에 들어 있어 차집합 계산 전에 걸러졌고, 그래서
# 'Depression Before Stroke'(실제 After) · 'Mortality in Children over Five Years'(실제
# under)가 sim 0.9+ 'full' MATCH였다(omitted·substituted도 빈 배열 — 육안 단서 전무).
# TITLE_STOP에서 통째로 빼면 'over time' 류 비극성 용법이 절단/생략 오탐을 내므로,
# **양방향 차집합에 쌍이 서로 맞바뀌어 남을 때만** 'polarity-inversion'으로 막는다.
_POLARITY_STOP_PAIRS = (
    ("before", "after"), ("over", "under"), ("above", "below"), ("within", "beyond"),
    ("during", "after"), ("during", "before"), ("pre", "post"), ("with", "without"),
    ("upstream", "downstream"), ("early", "late"), ("earlier", "later"),
)


# 인용 관행상 제목 뒤에 덧붙는 서지·게재 표지 — 내용 주장을 바꾸지 않으므로
# '내용어 추가'(token-addition)로 보지 않는다(예: '... for Image Recognition CVPR').
CITATION_MARKER_TOKENS = {"cvpr", "iccv", "eccv", "neurips", "nips", "iclr", "icml",
                          "aaai", "ijcai", "acl", "emnlp", "kdd", "sigir", "ieee",
                          "acm", "arxiv", "preprint", "proceeding", "conference",
                          "workshop", "symposium", "journal", "vol", "volume",
                          "issue", "suppl", "supplement", "poster"}


def _stem(w):
    """영국식 -ise/-isation → -ize/-ization 통일 + 단수화. 정당한 표기 변형
    (forest/forests, randomised/randomized)이 '내용어 치환'으로 오판되지 않게."""
    for a, b in (("isations", "izations"), ("isation", "ization"), ("ised", "ized"),
                 ("ising", "izing"), ("ises", "izes"), ("ise", "ize")):
        if w.endswith(a):
            w = w[: -len(a)] + b
            break
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    return w


# 정당한 표기 변형에서 실제로 나타나는 어미 차이 — 이것만 근사매칭 흡수를 허용한다.
# (_stem이 이미 복수형·-ise/-ize를 처리하므로 여기 남는 건 영/미 철자·어형 변화다.)
_SPELLING_SUFFIXES = ("s", "es", "e", "d", "ed", "ing", "al", "ly", "ic")

# 레드팀 R5: 의미가 반전되는 대립 접두쌍 — 어간이 길고 접두만 달라 ratio가 구조적으로
# 0.8~0.92라(hyperglycemia/hypoglycemia 0.88, male/female 0.80, preoperative/
# postoperative 0.80, bipolar/unipolar 0.80) 근사매칭이 '표기 변형'으로 삼켰다. 저혈당
# 논문이 고혈당 근거로, 암컷 실험이 수컷 결과로 인용돼도 MATCH exit 0이었다.
_ANTONYM_PREFIX_PAIRS = (
    ("hyper", "hypo"), ("pre", "post"), ("intra", "inter"), ("intra", "extra"),
    ("inter", "extra"), ("over", "under"), ("bi", "uni"), ("bi", "mono"), ("uni", "mono"),
    ("endo", "exo"), ("supra", "infra"), ("micro", "macro"), ("intra", "trans"),
    ("pro", "anti"), ("ante", "post"), ("sub", "supra"), ("sub", "super"),
    ("ipsi", "contra"), ("homo", "hetero"), ("iso", "aniso"), ("eu", "dys"),
    ("hyper", "normo"), ("hypo", "normo"), ("a", "hyper"), ("a", "hypo"),
    ("de", "re"), ("up", "down"), ("in", "out"), ("max", "min"),
)


def _fold_spelling(w):
    """영/미 철자 차이 중 어간 앞머리에 나타나는 것(paediatric/pediatric,
    oestrogen/estrogen, sulphur/sulfur, modelling/modeling)만 접는다 — 접두 치환 판정의
    오탐 방지용. 겹자음(ll/ss)은 하나로 접는다."""
    w = w.replace("ae", "e").replace("oe", "e").replace("ph", "f").replace("k", "c")
    return re.sub(r"([a-z])\1", r"\1", w)


def _prefix_swap_conflict(x, y):
    """근사매칭 흡수를 막아야 하는 '접두 치환' 쌍인가?(레드팀 R5)
    철자 변형(analysis/analyses·tumour/tumor)은 어간(앞머리)이 같고 꼬리만 다르다.
    반대로 대립 접두쌍·성별(male/female)·이성질체(ketamine/esketamine)는 꼬리(공통
    접미)가 같고 앞머리가 다르다. 이 방향성으로 가른다:
      (a) 명시 대립 접두쌍(_ANTONYM_PREFIX_PAIRS) — 공통 어간 앞에 쌍의 양쪽이 붙은 꼴.
      (b) 공통 접미(4글자+)를 떼고 남는 앞머리가 양쪽 다 비어 있지 않고 서로 다르면
          접두 치환(intracellular/intercellular, overestimation/underestimation).
      (c) 한쪽이 다른 쪽의 **접미**이고 남는 앞머리가 있으면 접두 파생(male/female,
          ketamine/esketamine, citalopram/escitalopram).
    (b)·(c)는 영/미 철자(paediatric/pediatric, oestrogen/estrogen)를 _fold_spelling으로
    접은 뒤 같아지면 치환이 아니다."""
    if _fold_spelling(x) == _fold_spelling(y):
        return False
    for a, b in _ANTONYM_PREFIX_PAIRS:
        for p, q in ((a, b), (b, a)):
            if x.startswith(p) and y.startswith(q) and x[len(p):] == y[len(q):] \
                    and len(x) > len(p) + 2:
                return True
    n = 0
    while n < min(len(x), len(y)) and x[-1 - n] == y[-1 - n]:
        n += 1
    if n < 4:
        return False
    hx, hy = x[:len(x) - n], y[:len(y) - n]
    # 앞머리 비교는 철자 접기 + 묵음 e 제거(judgement/judgment → judg/judg)
    if hx and hy and _fold_spelling(hx).rstrip("e") != _fold_spelling(hy).rstrip("e"):
        return True  # (b)
    if (hx and not hy) or (hy and not hx):
        return True  # (c)
    return False


# 레드팀 R6: 의미를 담는 의학 접미 — 명사형(class를 부여)과 그 형용사형(같은 개념의 어형
# 변화 — class만 맞춘다). _stem이 끝의 s를 깎으므로 itis→iti, osis→osi 꼴로 적는다.
# 어간이 같고 꼬리만 달라 ratio 0.8~0.9인 pneumonia/pneumonitis(0.84)·hepatic/hepatitis
# (0.91)·gastric/gastritis·pancreatic/pancreatitis가 '표기 변형'으로 흡수돼 MATCH였다 —
# 면역관문억제제 폐장염(irAE, 스테로이드) 논문이 감염성 폐렴(항생제) 근거로 둔갑한다.
# 명사형 접미가 한쪽에만 있거나 양쪽 class가 다르면 흡수 금지(→ token-substitution).
# fibrosis/fibrotic·anemia/anemic·arthritis/arthritic 같은 동의 어형쌍은 같은 class라 통과.
_MED_SUFFIX_CLASSES = (
    ("itis", ("iti", "itis"), ("itic",)),
    ("osis", ("osi", "osis"), ("otic", "ostic")),
    ("oma", ("oma", "omata"), ("omatous",)),
    ("emia", ("emia",), ("emic",)),
    ("pathy", ("pathy",), ("pathic",)),
    ("ectomy", ("ectomy",), ()),
    ("algia", ("algia",), ("algic",)),
    ("plasia", ("plasia",), ("plastic",)),
    ("penia", ("penia",), ("penic",)),
    ("megaly", ("megaly",), ()),
)
# 의학 접미가 없는 쪽에서 떼어 볼 '의미 담지' 어미 — pneumon-ia / hepat-ic / gastr-ic.
# 이걸 뗀 어간이 접미 쪽 어간과 같을 때만 '접미 치환'이다. diagnosing/diagnosis처럼
# 어형 변화(-ing)는 어미 목록에 없어 종전대로 흡수된다(오탐 억제).
_MED_ALT_ENDINGS = ("ical", "ia", "ic", "al", "ous", "oid", "y")


def _med_stem(w):
    """(어간, class, 명사형 여부). 명사형 접미만 class를 '부여'하고 형용사형은 짝을 맞출
    때만 쓴다 — politic/political처럼 -itic이 우연히 붙은 비의학어가 오탐을 내지 않게."""
    for name, nouns, adjs in _MED_SUFFIX_CLASSES:
        for sfx in nouns:
            if w.endswith(sfx) and len(w) > len(sfx) + 2:
                return w[:-len(sfx)], name, True
        for sfx in adjs:
            if w.endswith(sfx) and len(w) > len(sfx) + 2:
                return w[:-len(sfx)], name, False
    return w, None, False


def _medical_suffix_conflict(x, y):
    """근사매칭 흡수를 막아야 하는 '의학 접미 치환' 쌍인가?(레드팀 R6 — 접두 치환의 거울)
    한쪽이 명사형 의학 접미(-itis/-osis/-oma/-emia…)를 달고, 다른 쪽이 같은 class(명사형
    또는 그 형용사형)가 아니면서 **어간이 같으면** 의미가 다른 이웃 용어다."""
    fx, fy = _fold_spelling(x), _fold_spelling(y)
    sx, cx, nx = _med_stem(fx)
    sy, cy, ny = _med_stem(fy)
    if not (nx or ny) or cx == cy:
        return False
    if cx and cy:  # 서로 다른 의학 class(hepatoma/hepatitis) — 어간이 같을 때만
        return sx == sy
    s_c, w_u = (sx, fy) if nx else (sy, fx)  # 접미 쪽 어간 vs 무접미 쪽 원형
    for e in _MED_ALT_ENDINGS:
        if w_u.endswith(e) and w_u[:-len(e)] == s_c:
            return True
    return w_u == s_c


def _identifier_conflict(x, y):
    """근사매칭(ratio>=0.8) 흡수를 막아야 하는 '식별자 치환' 쌍인가?
    레드팀 R4: 제목 속 식별자는 어간이 길고 차이가 1글자라 ratio가 구조적으로
    0.87~0.95다 — bnt162b1/bnt162b2=0.875, brca1/brca2=0.8, imagenet/imagenette
    =0.889. 그래서 '표기 변형(analysis/analyses)'으로 흡수돼 전혀 다른 약물·유전자·
    데이터셋의 결과를 인용해도 MATCH(exit 0)가 났다(BNT162b1은 중단된 백신 후보인데
    b2의 3상 유효성 95%가 귀속됨).
      (a) 한쪽이라도 숫자를 포함하면 식별자로 본다(brca1/brca2, gpt3/gpt4,
          cifar10/cifar100, bnt162b1/bnt162b2). covid19 류는 _canon이 'covid 19'로
          쪼개 양쪽에 동일 토큰이 생기므로 차집합에 오지 않는다.
      (b) 숫자가 없어도, 한쪽이 다른 쪽의 접두이면서 남는 꼬리가 정당한 어미가
          아니면 고유명 파생으로 본다(imagenet/imagenette). analysis/analyses처럼
          어간이 갈리는 철자 변형은 접두 관계가 아니라 영향받지 않는다."""
    if re.search(r"\d", x) or re.search(r"\d", y):
        return True
    lo, hi = (x, y) if len(x) <= len(y) else (y, x)
    if hi.startswith(lo):
        tail = hi[len(lo):]
        if tail and tail not in _SPELLING_SUFFIXES:
            return True
    return False


def _merge_split_tokens(ct, rt):
    """한쪽에서 붙여 쓴 토큰(noninvasive)이 다른 쪽에서 하이픈으로 쪼개진(non-invasive
    → 'non','invasive') 표기 차이를 양쪽에서 흡수한다.
    레드팀 R3: _canon이 하이픈을 쪼개는 바람에 차집합에 'non' 파편이 남아,
    'Noninvasive ventilation…'(sim 0.99·저자·연도 일치)이 극성 반전으로 오판돼
    MISMATCH(=가짜 인용, exit 3)가 나던 오탐 봉합."""
    def _pass(a, b):
        sb, out, i = set(b), [], 0
        while i < len(a):
            j2 = a[i] + a[i + 1] if i + 1 < len(a) else None
            j3 = j2 + a[i + 2] if j2 and i + 2 < len(a) else None
            if j3 and j3 in sb:
                out.append(j3); i += 3
            elif j2 and j2 in sb:
                out.append(j2); i += 2
            else:
                out.append(a[i]); i += 1
        return out
    return _pass(ct, rt), _pass(rt, ct)


def _subtitle_tokens(actual):
    """실제 제목의 부제(첫 ':' 뒤)에 있는 어간 토큰 집합 — hedge 부제 절단 면제 판정용
    (레드팀 R6). 부제가 없으면 빈 집합."""
    if not actual or ":" not in actual:
        return set()
    return {_stem(t) for t in _canon(actual.split(":", 1)[1]).split()}


def _title_token_conflict(c, r, subtitle_tail=None):
    """제목 토큰 차집합 분석 → 'negation' / 'token-substitution' / 'token-addition' / None.
    subtitle_tail: 실제 제목 부제(':' 뒤)의 어간 토큰 집합(_subtitle_tokens) — 부제
    절단에서 hedge를 경고 계층으로 내리는 데만 쓴다(레드팀 R6).
    레드팀 R2: 문자 유사도(SequenceMatcher)는 의미 반전에 무감각해
    'does not reduce'→'does reduce'(sim 0.96), 'game of Go'→'game of Chess'
    (sim 0.95)가 그대로 MATCH였다. 유사도 위에 토큰 검사를 얹는다.
      (a) 차집합에 부정어(또는 un-/in-/non- 접두 반전쌍)가 있으면 불일치.
          단 ①하이픈 표기 차이로 생긴 파편(non-invasive)과 ②순수 절단(claimed가
          actual의 접두)일 때 꼬리(부제)에만 있는 부정어는 극성 반전이 아니다(R3 오탐).
      (b) 양방향 차집합에 내용어가 동시에 남으면(= 치환) 불일치.
      (c) claimed에만 내용어가 남으면(= 실제 제목에 없는 모집단·결과지표·연구설계를
          덧붙인 확장 인용) 불일치. 레드팀 R3: `if left and right`가 편측 추가를
          검사조차 안 해 '…High-Risk Surgical Patients in Children'(sim 0.944)이
          MATCH였다. 서지 표지(CVPR 등)만 남는 경우는 제외.
      (d) actual에만 내용어가 남으면(= 범위를 한정하는 단어를 지운 축소 인용)
          'token-omission'. 레드팀 R4: 이 방향이 검사 대상에서 아예 빠져 있어
          '…total cancer incidence and mortality…'→'…total mortality…'(sim 0.899)가
          MATCH였다 — 암 특이 메타분석이 전체사망률 메타분석으로 승격된다. 단
          claimed가 actual에 통째로 들어 있는 진짜 절단(부제 절단·단축형)은 정당한
          축약이므로 면제한다."""
    ct = [_stem(t) for t in c.split()]
    rt = [_stem(t) for t in r.split()]
    ct, rt = _merge_split_tokens(ct, rt)
    dc, dr = set(ct) - set(rt), set(rt) - set(ct)
    if not dc and not dr:
        return None
    # (a) 극성 반전: 부정어 자체 / 접두 반전쌍(effective↔ineffective 등)
    #     순수 절단(r이 c로 시작)이면 actual 쪽 잉여는 전부 꼬리(부제)에서 온 것이므로
    #     극성 반전 판정에서 제외한다 — 부제에 부정어가 있는 논문의 주제목 인용 구제
    tail_only = r.startswith(c) and len(r) > len(c)
    neg_scope = dc if tail_only else (dc | dr)
    if neg_scope & NEG_TOKENS:
        return "negation"
    for x in dc:
        for y in (set() if tail_only else dr):
            if x in TITLE_STOP or y in TITLE_STOP or len(y) < 4 or len(x) < 4:
                continue  # 'into'='in'+'to' 류 기능어 오탐 차단(레드팀 R3)
            for pre in ("un", "in", "im", "non", "dis"):
                if x == pre + y or y == pre + x:
                    return "negation"
    # (f) 시점·범위 극성 반전(before↔after, over↔under…) — 기능어라 TITLE_STOP 필터에
    #     묻히기 전에 검사한다. 양쪽 차집합에 쌍이 서로 맞바뀌어 남을 때만 발화하므로
    #     'over time' 같은 비극성 용법·정당한 절단은 걸리지 않는다(레드팀 R6).
    if not tail_only:
        for p, q in _POLARITY_STOP_PAIRS:
            if (p in dc and q in dr) or (q in dc and p in dr):
                return "polarity-inversion"
    # (e) 결론 강도 표지(hedge) 삭제 — 'Weak Association…'→'Association…',
    #     'Questionable Benefit…'→'Benefit…'. 레드팀 R5: 머리의 한정어를 지운 인용이
    #     연속 절단 면제 + ratio≥0.75로 'full' MATCH였다 — 약한 관련이 '관련 있음'으로
    #     승격된다. 부정어(NEG_TOKENS)와 같은 등급으로 막는다. 부제 절단(tail_only)이어도
    #     면제하지 않는다 — '…: limited evidence'를 잘라내는 것도 같은 왜곡이다.
    #     레드팀 R6: 단 **부제(':' 뒤) 절단**에서는 부정어(NEG)와 같은 등급으로 다룬다 —
    #     ': lack of efficacy'는 tail_only 면제로 MATCH인데 ': limited evidence'만 MISMATCH
    #     (exit 3=가짜 인용)인 것은 더 강한 극성 표지가 더 약한 검사를 받는 모순이었다.
    #     둘 다 '경고 계층'(verify 출력 polarity_omitted)으로 통일하고, 주제목 안의 hedge
    #     삭제(머리·꼬리 불문)는 계속 기계 차단한다.
    hedge = dr & HEDGE_TOKENS
    if hedge and not (tail_only and subtitle_tail and hedge <= subtitle_tail):
        return "hedge-omission"
    # (b)·(c) 내용어 차이 — 표기 변형(analysis/analyses 류)은 근사 매칭으로 흡수
    dc = {t for t in dc if t not in TITLE_STOP}
    dr = {t for t in dr if t not in TITLE_STOP}
    left, right = set(dc), set(dr)
    for x, y in _absorbable_pairs(left, right):
        left.discard(x); right.discard(y)
    if left and right:
        return "token-substitution"
    if left and (left - CITATION_MARKER_TOKENS):
        return "token-addition"
    # (d) 축소 인용 — actual의 한정어를 지운 방향. 진짜 절단(claimed가 actual에
    # 연속 부분문자열로 들어 있음)만 정당한 축약으로 면제한다. 제목 중간의 한정어를
    # 도려내면(cancer incidence and mortality → mortality) 연속성이 깨져 걸린다.
    if right and (right - CITATION_MARKER_TOKENS):
        if not _is_truncation(c, r):
            return "token-omission"
    return None


def _absorbable_pairs(left, right):
    """차집합 양쪽에 남은 토큰 중 '표기 변형'으로 서로 지울 수 있는 (claimed, actual)
    쌍. 조건: 양쪽 4글자+, 식별자 치환·접두 치환(레드팀 R4·R5)이 아니고 ratio≥0.8.
    _title_token_conflict의 흡수 루프와 verify 출력의 substituted 필드가 같은 판정을
    쓰도록 분리했다(흡수된 쌍을 '생략'으로 오표시하지 않기 위해)."""
    pairs, used = [], set()
    for x in sorted(left):
        for y in sorted(right):
            if y in used:
                continue
            if len(x) >= 4 and len(y) >= 4 and \
                    not _identifier_conflict(x, y) and \
                    not _prefix_swap_conflict(x, y) and \
                    not _medical_suffix_conflict(x, y) and \
                    difflib.SequenceMatcher(None, x, y).ratio() >= 0.8:
                pairs.append((x, y)); used.add(y)
                break
    return pairs


def substituted_tokens(claimed, actual):
    """MATCH로 통과한 인용에서 표기 변형으로 흡수된 토큰 쌍('tumour→tumor')을 뽑는다.
    레드팀 R5: 흡수된 쌍이 omitted_tokens에 '생략'으로 찍혀 치환을 절단으로 오표시했다."""
    c, r = _canon(claimed), _canon(actual)
    ct = [_stem(t) for t in c.split()]
    rt = [_stem(t) for t in r.split()]
    ct, rt = _merge_split_tokens(ct, rt)
    dc = {t for t in set(ct) - set(rt) if t not in TITLE_STOP}
    dr = {t for t in set(rt) - set(ct) if t not in TITLE_STOP}
    return [f"{x}→{y}" for x, y in _absorbable_pairs(dc, dr)]


def _is_truncation(c, r):
    """claimed가 actual의 '연속' 부분인가(= 정당한 절단 인용).
    레드팀 R5: 창의 위치를 불문하고 면제해 제목 **머리**의 내용어(Weak/Questionable…)를
    지운 인용도 통과했다 — 창 앞에 잘려 나간 토큰은 기능어(TITLE_STOP)뿐이어야 한다.
    꼬리 절단(부제·한정구)만이 SKILL §6이 승인한 축약이다."""
    if not c or not r:
        return False
    ctoks, rtoks = c.split(), r.split()
    n = len(ctoks)
    for i in range(len(rtoks) - n + 1):
        if rtoks[i:i + n] == ctoks:
            return all(_stem(t) in TITLE_STOP for t in rtoks[:i])
    return False


def omitted_tokens(claimed, actual):
    """정당한 절단으로 면제된 축약 인용에서 '실제 제목에만 있는 내용어'를 뽑는다.
    레드팀 R4: 꼬리 절단은 정당한 축약(…prediction → …prediction with AlphaFold)과
    범위 축소(…cancer incidence → …cancer incidence and mortality: 결과지표 삭제)가
    형태적으로 구분되지 않는다. 전자를 살리려면 후자를 기계적으로 막을 수 없으므로,
    verdict는 MATCH로 두되 무엇이 생략됐는지를 출력에 실어 육안 대조가 실제로
    가능하게 한다(§6이 지시하는 눈 대조의 대상을 만들어 주는 것)."""
    c, r = _canon(claimed), _canon(actual)
    ct = [_stem(t) for t in c.split()]
    rt_raw = r.split()
    rt = [_stem(t) for t in rt_raw]
    ct2, rt2 = _merge_split_tokens(ct, rt)
    have = set(ct2)
    # 표기 변형으로 흡수된 actual 토큰(tumour→tumor의 tumor)은 '생략'이 아니라 '치환'이다 —
    # substituted_tokens로 따로 노출하고 여기서는 뺀다(레드팀 R5)
    dc = {t for t in set(ct2) - set(rt2) if t not in TITLE_STOP}
    dr = {t for t in set(rt2) - set(ct2) if t not in TITLE_STOP}
    have |= {y for _x, y in _absorbable_pairs(dc, dr)}
    # 어간이 아니라 원 표기로 돌려준다(육안 대조용). 연구설계 상용구는 범위 축소
    # 신호가 아니라 부제 절단의 부산물이라 제외해 신호를 흐리지 않는다.
    boiler = {"a", "an", "the", "meta", "analysis", "analyses", "systematic", "review",
              "randomized", "randomised", "controlled", "trial", "trials", "study",
              "studies", "protocol", "cohort", "prospective", "retrospective",
              "multicenter", "multicentre", "double", "blind", "placebo", "pilot"}
    out, seen = [], set()
    for raw, st in zip(rt_raw, rt):
        if st in have or st in TITLE_STOP or st in CITATION_MARKER_TOKENS:
            continue
        if raw in boiler or st in boiler:
            continue
        if raw in seen:
            continue
        seen.add(raw)
        out.append(raw)
    return out


def _title_match(claimed, actual):
    """제목 대조 → (일치여부, 유사도, 판정경로).
    전체 유사도 0.75 실패 시 부제 절단·축약 인용을 구제하되,
    'Part I' vs 'Part II' 같은 시리즈물은 표지 불일치로 강제 MISMATCH.
    구제는 축약 방향(claimed ⊆ actual)만 — 실제 제목에 문구·부제를 덧붙인
    확장 인용은 조작 인용의 주 경로라 구제하지 않는다.
    부정어 삭제·내용어 치환(의미 반전)은 유사도와 무관하게 강제 MISMATCH."""
    c, r = _canon(claimed), _canon(actual)
    ratio = difflib.SequenceMatcher(None, c, r).ratio()
    pm_c, pm_r = _part_marker(claimed), _part_marker(actual)
    if pm_c and pm_r and pm_c != pm_r:
        return False, ratio, "part-conflict"
    # 부제 절단 방향(claimed에 ':' 없음)일 때만 부제 토큰을 넘긴다 — 조작 부제 삽입·교체는 대상 아님
    sub_tail = _subtitle_tokens(actual) if ":" not in (claimed or "") else None
    conflict = _title_token_conflict(c, r, subtitle_tail=sub_tail)
    if conflict:
        return False, ratio, conflict
    # 확장 인용 선검사 — 실제 제목이 claimed에 통째로 들어 있고 덧붙은 양이 크면
    # (단어수 비율 0.8 미만) 유사도와 무관하게 불일치. 레드팀 R3: 이 정책이 아래
    # containment 절에만 있어 ratio>=0.75 조기 반환에 가려진 죽은 코드였다 —
    # '…in mice and humans'(0.901)·'…: a randomized controlled trial'(0.769)이 MATCH였음.
    if r and r in c and len(r.split()) / max(1, len(c.split())) < 0.8:
        return False, ratio, "title-expansion"
    if ratio >= 0.75:
        return True, ratio, "full"
    # 부제(':' 뒤) 절단 구제 — 실제 제목에 부제가 있고 claimed는 주제목만 인용한
    # 방향만. claimed에만 ':'가 있으면(부제 삽입·교체) 구제하지 않는다 — 조작
    # 부제는 full 대조(부제 포함 후보 — _fetch_verify_meta의 titles)로만 통과 가능.
    # 레드팀 R4: 주제목끼리도 토큰 검사를 다시 돌린다 — 이 구제는 전체 유사도
    # 문턱(0.75)을 통째로 우회하므로, 주제목 안에서 결과지표·모집단을 지운 축소
    # 인용(…total cancer incidence and mortality… → …total cancer incidence)이
    # sim 0.627로도 MATCH가 됐다.
    if ":" in (actual or "") and ":" not in (claimed or ""):
        c2, r2 = _canon(claimed.split(":")[0]), _canon(actual.split(":")[0])
        if min(len(c2.split()), len(r2.split())) >= 3:
            ratio2 = difflib.SequenceMatcher(None, c2, r2).ratio()
            conflict2 = _title_token_conflict(c2, r2)
            if conflict2:
                return False, ratio, conflict2
            if ratio2 >= 0.85:
                return True, ratio, "main-title"
    # 축약 인용 구제: claimed(5단어+)가 실제 제목에 통째로 포함되는 방향만.
    # 반대 방향(실제 제목이 claimed에 포함 = 실제 제목에 문구를 덧붙인 확장)은
    # 덧붙은 양이 미미할 때(단어수 비율 0.8+)만 허용 — 그 이상은 조작 경로.
    if len(min(c, r, key=len).split()) >= 5:
        if c in r:
            return True, ratio, "containment"
        if r in c and len(r.split()) / max(1, len(c.split())) >= 0.8:
            return True, ratio, "containment"
    return False, ratio, "none"


_PUBMED_RETRACTION_CACHE = {}


def _pubmed_retraction(doi, hits=None):
    """PubMed 3차 철회 소스. DOI→PMID(esearch `term=<doi>[AID]`) → efetch로
    PublicationType D016441('Retracted Publication')·CommentsCorrections
    RefType="RetractionIn" 확인. 반환 True(철회)/False(색인됐고 철회 아님)/
    None(미색인·조회 실패 — 판정 없음).
    레드팀 R3: OpenAlex(is_retracted:false)·Crossref(update-to 없음)가 둘 다 정상
    응답하면서 철회 반영만 늦어, PubMed가 이미 낙인한 철회 논문이 MATCH+
    'retraction_checked:true'로 통과했다(표본 30건 중 2건). 무키·무료 E-utilities라
    설계 원칙 위반 없이 생의학 도메인 갭을 메운다. 조회 실패는 기존 2소스 판정을
    낮추지 않는다(PubMed 미색인 DOI를 일괄 열화시키면 정상 검증이 깨진다)."""
    if doi in _PUBMED_RETRACTION_CACHE:
        return _PUBMED_RETRACTION_CACHE[doi]
    out = None
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    key = os.environ.get("NCBI_API_KEY")
    # sqlite 캐시는 DOI→PMID 매핑(서지 식별자, 양성만)까지다. 철회를 판정하는 efetch는
    # 히트여도 매번 실 조회한다 — 철회 판정 미캐시 원칙. PMID 부재(미색인)는 캐시하지
    # 않는다(나중에 색인된 철회 논문을 30일간 놓치게 되므로).
    ids = []
    pm = cache_get("pmid", doi)
    if pm and pm.get("pmid"):
        ids = [str(pm["pmid"])]
        if hits is not None:
            hits.append("pmid")
    else:
        p = {"db": "pubmed", "term": f"{doi}[AID]", "retmode": "json", "retmax": "1"}
        if key:
            p["api_key"] = key
        body, _c = http_get(f"{base}/esearch.fcgi?" + urllib.parse.urlencode(p))
        if body:
            try:
                ids = (json.loads(body).get("esearchresult") or {}).get("idlist") or []
            except ValueError:
                ids = []
        if ids:
            cache_put("pmid", doi, {"pmid": str(ids[0])})
            time.sleep(0.34 if not key else 0.11)  # E-utilities 무키 3req/s
    if ids:
        p2 = {"db": "pubmed", "id": ids[0], "retmode": "xml"}
        if key:
            p2["api_key"] = key
        xml, _c2 = http_get(f"{base}/efetch.fcgi?" + urllib.parse.urlencode(p2),
                            headers={"Accept": "application/xml"})
        if xml:
            # 본문(초록)에 'retracted'가 나오는 논문의 오탐을 피하려 태그 단위로만 판정
            out = bool(re.search(r'<PublicationType[^>]*UI="D016441"', xml)
                       or re.search(r'<PublicationType[^>]*>\s*Retracted Publication', xml, re.I)
                       or re.search(r'RefType="RetractionIn"', xml))
    _PUBMED_RETRACTION_CACHE[doi] = out
    return out


def _fetch_verify_meta(doi):
    """검증용 메타데이터: Crossref(서지+부제+update-to+type) + OpenAlex(철회 —
    Retraction Watch 연동) + PubMed(3차 철회 소스) + DataCite(3차 서지 소스).
    철회 판정 때문에 OpenAlex는 항상 조회한다. found=False일 때 codes로
    404(부재 확인)와 전송오류·429·5xx(일시 장애 — 부재 아님)를 구분한다.
    레드팀 R2: OpenAlex만 죽으면 Crossref 성공으로 found=True가 되어 철회 검사가
    조용히 건너뛰어지고 철회 논문이 MATCH로 통과했다 → retraction_checked를 별도로
    추적하고(미수행이면 verdict를 열화), Crossref의 update-to/relation을 2차 철회
    소스로 읽는다.
    레드팀 R3: ①두 소스가 정상 응답해도 철회 반영이 늦으면 통과 → PubMed 추가,
    ②DataCite 등록 DOI(arXiv 10.48550/*)는 Crossref에 구조적으로 없어 실재 논문이
    NOT_FOUND(가짜 인용 신호)로 낙인 → DataCite 폴백(단 철회 플래그가 없어
    retraction_checked는 올리지 않는다)."""
    meta = {"title": None, "titles": [], "year": None, "first_author": None,
            "retracted": False, "found": False, "retraction_checked": False,
            "retraction_sources": [], "type": None, "via_datacite": False,
            "is_correction": False, "is_retraction_notice": False,
            "retracted_evidence": [],  # 원논문 철회 증거(통지문 추정보다 우선 — 레드팀 R5)
            "detail": [], "codes": [], "cache_hits": []}
    qdoi = urllib.parse.quote(doi, safe="/")  # '#'·'?' 포함 DOI가 URL에서 절단되는 것 방지
    # sqlite 캐시(서지 한정): ①DataCite 전용 DOI의 서지(제목·연도·저자 후보) — DOI는
    # 등록기관(RA)이 바뀌지 않으므로 Crossref 404·DataCite 재조회를 건너뛴다.
    # ②PubMed PMID(_pubmed_retraction). Crossref 서지는 캐시해도 이득이 없다 — Crossref는
    # updated-by·relation을 주는 2차 **철회 소스**라 매번 실 조회해야 하고, 철회만 뽑는
    # 경량 probe(/works?filter=doi&select=…)가 실측상 단건 조회보다 느렸다(2026-09-15:
    # probe 0.8~1.2초 vs 단건 0.6~0.9초). OpenAlex(철회 플래그)·PubMed efetch도 항상 실 조회.
    dc_cached = cache_get("verify_datacite", doi)
    if dc_cached:
        meta["cache_hits"].append("datacite")
        body, code = None, 404  # DataCite 전용 DOI — Crossref에는 구조적으로 없다
    else:
        body, code = http_get(f"https://api.crossref.org/works/{qdoi}")
    if body:
        # 레드팀 R6: 2xx + 비JSON 본문(HTML 장애 페이지)이 JSONDecodeError로 verify_one 밖으로
        # 새어 나가 단건은 트레이스백 exit 1, batch는 BAD_ENTRY('refs.json 수정')로 오분류됐다.
        # DataCite·PubMed와 같이 감싸 UNVERIFIED(재시도) 경로로 보낸다.
        try:
            m = json.loads(body).get("message") or {}
        except ValueError:
            m = None
            meta["detail"].append("crossref badjson(비JSON 본문)")
            meta["codes"].append(-1)
    if body and m is not None:
        meta["found"] = True
        meta["retraction_sources"].append("crossref")
        # 심사보고서·그림 component·grant는 논문이 아니다 — [P#] 근거가 될 수 없다
        meta["type"] = (m.get("type") or "").strip().lower() or None
        # Crossref `update-to`는 **방향이 반대**다 — 이 필드를 달고 있는 레코드는
        # "X를 철회/정정한다"고 알리는 **통지문**이지 철회당한 문서가 아니다.
        # 철회 공고문(Lancet 2010 등)을 RETRACTED로 찍던 오판을 여기서 끊는다.
        # 통지문 역시 연구 결과가 없어 [P#] 근거는 될 수 없으므로 NON_ARTICLE로 보낸다
        # (철회 사실을 다루는 문장은 원논문 DOI를 RETRACTED로 인용해 서술한다).
        # 철회당한 원논문이 드는 필드는 `updated-by`다(실측: Wakefield 1998은
        # updated-by, Lancet 2010 공고문은 update-to). 이쪽이 진짜 2차 철회 소스.
        for u in (m.get("updated-by") or []):
            if re.search(r"retract|withdraw", str(u.get("type") or ""), re.I):
                meta["retracted"] = True
                meta["retraction_checked"] = True
                meta["retracted_evidence"].append("crossref updated-by")
        for u in (m.get("update-to") or []):
            ut = str(u.get("type") or "")
            if re.search(r"retract|withdraw", ut, re.I):
                # 레드팀 R5: Elsevier(heliyon·scitotenv·tsc…)는 철회 **원논문** 레코드에
                # update-to={자기 DOI, retraction}을 넣는다. 자기참조는 통지문 신호가
                # 아니라 원논문 자체의 철회 표기다(진짜 통지문은 update-to가 타 DOI를
                # 가리킨다 — Lancet 2010 실측). 통지문으로 오판하면 RETRACTED 대신
                # NON_ARTICLE이 나와 요약줄 '철회 0건'이 거짓이 되고 §6 '원논문 DOI로
                # 교체' 조치가 교착한다.
                if norm_doi(u.get("DOI")) == doi:
                    meta["retracted"] = True
                    meta["retraction_checked"] = True
                    meta["retracted_evidence"].append("crossref update-to(self)")
                else:
                    meta["is_retraction_notice"] = True
            elif CORRECTION_UPDATE_TYPES.search(ut):
                # 이 레코드 자체가 정오표 통지다(원논문을 '고치는' 쪽). 원논문은
                # update-to가 빈 배열이라 오탐되지 않는다(레드팀 R4 실측).
                meta["is_correction"] = True
        # relation 키도 방향이 있다: 'is-retracted-by'(내가 철회당함)만 철회 신호이고
        # 'is-retraction-of'(내가 X를 철회함)는 통지문 쪽이다 — 뒤섞으면 공고문이
        # 철회 논문으로 찍힌다(실사용 발견, 2026-07-28).
        for rel in (m.get("relation") or {}):
            r = str(rel)
            if re.search(r"is-?retraction-?of|retracts", r, re.I):
                meta["is_retraction_notice"] = True
            elif re.search(r"retract", r, re.I):
                meta["retracted"] = True
                meta["retraction_checked"] = True
        meta["title"] = (m.get("title") or [None])[0]
        if meta["title"]:
            meta["titles"].append(meta["title"])
            # Crossref는 부제를 별도 필드로 주므로 '주제목: 부제' 전체도 대조 후보에
            # 추가 — 부제 포함 정당 인용이 주제목뿐인 actual과 어긋나는 오탐 방지
            sub = (m.get("subtitle") or [None])[0]
            if sub and ":" not in meta["title"]:
                meta["titles"].append(f"{meta['title']}: {sub}")
        parts = (m.get("issued") or {}).get("date-parts") or [[None]]
        meta["year"] = parts[0][0] if parts and parts[0] else None
        au = m.get("author") or []
        if au:
            meta["first_author"] = au[0].get("family") or au[0].get("name")
    elif not body:  # badjson은 위에서 이미 detail/codes에 기록됨
        meta["detail"].append(f"crossref {code}")
        meta["codes"].append(code)
    b2, c2 = http_get(f"https://api.openalex.org/works/doi:{qdoi}?mailto={EMAIL}"
                      "&select=display_name,publication_year,is_retracted,authorships,type")
    w = None
    if b2:
        try:
            w = json.loads(b2)
        except ValueError:  # 레드팀 R6: 비JSON 본문 → UNVERIFIED 경로
            meta["detail"].append("openalex badjson(비JSON 본문)")
            meta["codes"].append(-1)
    if w is not None:
        meta["found"] = True
        # OpenAlex는 정오표를 type=erratum으로 정확히 분류하지만 단독 신호로는 못 쓴다 —
        # 제목에 'Correction of a pathogenic gene mutation…'처럼 correction이 들어간
        # 정상 논문(10.1038/nature23305)을 erratum으로 오분류한다(레드팀 R4).
        # Crossref type이 없을 때(=DataCite/OpenAlex 전용)만 보조 판정에 쓴다.
        meta["openalex_type"] = (w.get("type") or "").strip().lower() or None
        meta["retraction_checked"] = True  # 철회 판정 소스가 실제로 응답한 경우만 True
        if "openalex" not in meta["retraction_sources"]:
            meta["retraction_sources"].append("openalex")
        meta["retracted"] = meta["retracted"] or bool(w.get("is_retracted"))
        dn = w.get("display_name")
        if dn:
            meta["titles"].append(dn)  # OpenAlex 표기는 부제 포함이 많아 대조 후보로
        meta["title"] = meta["title"] or dn
        meta["year"] = meta["year"] or w.get("publication_year")
        if not meta["first_author"]:
            aus = w.get("authorships") or []
            name = ((aus[0].get("author") or {}).get("display_name") or "") if aus else ""
            meta["first_author"] = name.split()[-1] if name else None
            # OpenAlex display_name은 성·이름 순서가 뒤집혀 오기도 한다('Zhao Chengshuai' —
            # 실측 2026-09-05, arXiv 2508.01191). 첫 토큰도 후보로 보관해 _author_match가
            # 구조화 소스(DataCite familyName)와 함께 대조하게 한다.
            if name and len(name.split()) > 1:
                meta.setdefault("author_candidates", []).append(name.split()[0])
    elif not b2:  # badjson은 위에서 이미 기록됨
        meta["detail"].append(f"openalex {c2}")
        meta["codes"].append(c2)
    # 3차 서지 소스(DataCite) — Crossref·OpenAlex 둘 다 실패했을 때만. arXiv 전용
    # DOI(10.48550/arXiv.*)·Zenodo 등 DataCite 등록 DOI는 Crossref에 구조적으로 없다.
    # 레드팀 R5: arXiv DOI(10.48550/*)는 OpenAlex 레코드의 제목·초록이 통째로 다른 논문으로
    # 오염된 사례가 있다(Wei 2022 CoT → 'BNAI, NO-TOKEN…', 표본 5건 중 3건). OpenAlex가
    # 응답했더라도 DataCite(arXiv 등록기관 — 정본 제목)를 함께 읽어 대조 후보에 넣는다 —
    # 안 그러면 오염 제목을 인용하면 MATCH, 올바른 제목을 인용하면 MISMATCH로 역작동한다.
    oa_found = meta["found"]
    is_arxiv_doi = bool(re.match(r"^10\.48550/", doi, re.I))
    if not meta["found"] or is_arxiv_doi:
        at = {}
        if dc_cached:
            at = dc_cached
        else:
            b3, c3 = http_get(f"https://api.datacite.org/dois/{qdoi}")
            if b3:
                try:
                    at = (json.loads(b3).get("data") or {}).get("attributes") or {}
                except ValueError:
                    at = {}
                if at:
                    # 서지 3필드만 저장(철회 플래그는 DataCite에 애초에 없다)
                    cache_put("verify_datacite", doi, {
                        "titles": at.get("titles") or [],
                        "publicationYear": at.get("publicationYear"),
                        "creators": (at.get("creators") or [])[:1]})
        if at:
            meta["found"] = True
            meta["via_datacite"] = True
            # DataCite에는 철회 플래그가 없다 — retraction_checked는 올리지 않아
            # RETRACTION_UNCHECKED(exit 5)로 남긴다(철회 fail-open 재발 방지)
            meta["detail"].append("datacite 전용(철회 플래그 미제공)" if not oa_found
                                  else "datacite 제목 대조 병행(arXiv DOI)")
            dc_titles = [(t or {}).get("title") for t in (at.get("titles") or [])]
            dc_titles = [t for t in dc_titles if t]
            for tt in dc_titles:
                if tt not in meta["titles"]:
                    # arXiv DOI면 DataCite 정본 제목을 후보 **앞**에 둔다 — 동률일 때
                    # actual 표시가 오염 제목이 아니라 정본이 되게
                    if is_arxiv_doi and oa_found:
                        meta["titles"].insert(0, tt)
                    else:
                        meta["titles"].append(tt)
            if is_arxiv_doi and oa_found and dc_titles:
                meta["title"] = dc_titles[0]
                # OpenAlex 제목이 DataCite 정본과 다르면 후보에서 뺀다 — 남겨 두면 오염
                # 제목을 그대로 인용한 refs가 MATCH로 통과한다(오염 레코드의 정직한 인용
                # 은 통과·올바른 인용은 차단되던 역작동의 반쪽)
                meta["titles"] = dc_titles + [
                    t for t in meta["titles"]
                    if t not in dc_titles and any(_title_match(t, d)[0] for d in dc_titles)]
            meta["title"] = meta["title"] or (meta["titles"][0] if meta["titles"] else None)
            meta["year"] = meta["year"] or at.get("publicationYear")
            cr = at.get("creators") or []
            if cr:
                nm = cr[0].get("familyName") or cr[0].get("name") or ""
                fam = nm.split(",")[0].strip() or None
                if fam:
                    # 구조화된 familyName은 OpenAlex 표시명 추정보다 신뢰도가 높다 —
                    # 비어 있을 때만 채우지 말고 항상 후보에 넣는다
                    meta.setdefault("author_candidates", []).append(fam)
                    if not meta["first_author"]:
                        meta["first_author"] = fam
        else:
            meta["detail"].append(f"datacite {c3}")
            meta["codes"].append(c3)
    # 3차 철회 소스(PubMed) — 앞 두 소스가 '철회 아님'으로 본 경우에만 확인한다.
    # OpenAlex·Crossref의 철회 반영 지연으로 뚫리던 경로(레드팀 R3).
    if meta["found"] and not meta["retracted"]:
        pr = _pubmed_retraction(doi, hits=meta["cache_hits"])
        if pr is not None:
            meta["retraction_sources"].append("pubmed")
            if pr:
                meta["retracted"] = True
                meta["retraction_checked"] = True
    # Crossref 관행: 철회 논문은 제목에 RETRACTED 접두 (출판사별로 접두를 안 붙이는
    # 곳이 많아 백업일 뿐 — 이것만으로는 retraction_checked를 세우지 않는다)
    # 레드팀 R6: `^\s*retracted`만으로는 'Retracted Publications in the Drug Literature' 같은
    # 철회 연구 메타분석(정상 논문, 세 소스 모두 '철회 아님')까지 RETRACTED(exit 4)로 낙인해
    # 연구윤리 주제 리서치의 게이트 A가 영구 교착했다. 접두어 뒤 **구분자**(:·-·—)를 요구한다
    # — OpenAlex is_retracted:true 표본 198건 중 197건이 'RETRACTED[ ARTICLE|CHAPTER]:'·대시
    # 꼴이고, 'Retracted…'로 시작하는 정상 논문 14건 중 구분자를 가진 것은 0건(2026-09-15 실측).
    if meta["title"] and RETRACTED_PREFIX_RE.match(meta["title"]):
        meta["retracted"] = True
        meta["retraction_checked"] = True
        meta["retracted_evidence"].append("title RETRACTED:")
    # 통지문 제목 관행: 'Retraction—X' / 'Retraction notice: X' / 'Withdrawal of X'.
    # ('RETRACTED: X'는 위에서 이미 원논문으로 잡혔으므로 여기 오지 않는다.)
    for t in ([meta["title"]] + list(meta.get("titles") or [])):
        if t and RETRACTION_NOTICE_TITLE_RE.match(t):
            meta["is_retraction_notice"] = True
            break
    # 통지문은 철회당한 문서가 아니다 — 철회 플래그를 세우지 않고 NON_ARTICLE로 보낸다.
    # 단 원논문 증거(updated-by retraction·자기참조 update-to·'RETRACTED:' 제목)가 있으면
    # 통지문 추정이 아니라 증거가 이긴다(레드팀 R5 — Elsevier 철회 원논문 4/4가 여기서
    # retracted=False로 되돌려져 NON_ARTICLE이 됐다).
    if meta["is_retraction_notice"]:
        if meta["retracted_evidence"]:
            meta["is_retraction_notice"] = False
        else:
            meta["retracted"] = False
    # 정오표 보조 판정(레드팀 R4) — ①제목이 'Author Correction: X' 꼴이거나
    # ②Crossref type이 없는 레코드를 OpenAlex가 erratum 계열로 분류한 경우.
    # ②는 Crossref type이 있을 때(정상 논문의 journal-article 포함) 적용하지 않는다:
    # OpenAlex는 제목에 correction이 든 정상 논문을 erratum으로 오분류하기 때문.
    if not meta["is_correction"]:
        for t in ([meta["title"]] + list(meta.get("titles") or [])):
            if t and CORRECTION_TITLE_RE.match(t):
                meta["is_correction"] = True
                break
    if not meta["is_correction"] and not meta.get("type"):
        if CORRECTION_UPDATE_TYPES.search(meta.get("openalex_type") or ""):
            meta["is_correction"] = True
    return meta


def verify_one(doi, title, author=None, year=None, require_meta=False):
    """단일 인용 검증. verdict: MATCH / MISMATCH(제목) / MISMATCH_META(저자·연도)
    / NON_ARTICLE(심사보고서·component·grant — 논문이 아님) / RETRACTED
    / NOT_FOUND(세 서지 소스 모두 404 — DOI 부재 확인) / UNVERIFIED(전송오류·
    429·5xx — 부재 아님, 재시도 대상) / NO_DOI(doi 누락 — API 조회 불가)
    / RETRACTION_UNCHECKED(철회 판정 소스 미응답 — 재시도 대상)
    / RETRACTION_NA(DataCite 전용 DOI — 철회 플래그 부재로 구조적 미검증, 재시도 무의미)
    / INCOMPLETE_META(require_meta일 때 저자·연도 미기재 — 검사 자체가 생략됨).
    author·year는 준 것만 검사한다. 생략이 '통과'로 보이지 않게 결과에
    meta_missing(인용이 안 준 필드)·meta_unavailable(API에 없어 대조 불가한 필드)을
    항상 실어 보낸다(레드팀 R2)."""
    doi = norm_doi(doi)
    if not doi:
        # doi 없는 항목(DOI 미발급 arXiv 프리프린트 등)에서 배치 전체를 죽이지 않는다
        return {"doi": None, "verdict": "NO_DOI",
                "detail": "doi 누락 — verify 불가. arXiv 등은 search --sources arxiv로 제목·저자 수동 대조"}
    meta = _fetch_verify_meta(doi)
    if not meta["found"]:
        codes = meta.get("codes") or []
        # 404가 아닌 실패(-1 전송오류·429·5xx)가 섞이면 '부재 확인'이 아니라 '미검증'
        verdict = "NOT_FOUND" if codes and all(c == 404 for c in codes) else "UNVERIFIED"
        return {"doi": doi, "verdict": verdict, "detail": ", ".join(meta["detail"])}
    # 제목 후보(주제목 / 부제 포함 전체 / OpenAlex 표기) 전부와 대조해 최선 결과 채택
    candidates = [t for t in dict.fromkeys(meta.get("titles") or []) if t] or [meta["title"] or ""]
    matched, ratio, how, actual = False, 0.0, "none", meta["title"]
    for t in candidates:
        mt, rt, ht = _title_match(title, t)
        if (mt, rt) > (matched, ratio):  # 일치 우선, 그다음 유사도 최대
            matched, ratio, how, actual = mt, rt, ht, t
    author_ok = year_ok = None
    missing, unavailable = [], []
    if author:
        if meta["first_author"]:
            author_ok = _author_match(author, meta["first_author"])
            if author_ok is False:
                # 표시명 순서 오류 대비: 다른 소스의 성(family) 후보와도 대조
                for cand in meta.get("author_candidates") or []:
                    if _author_match(author, cand):
                        author_ok = True
                        meta["first_author"] = cand
                        break
        else:
            unavailable.append("author")
    else:
        missing.append("author")
    if year:
        # 'in press'·'2019a' 같은 실서지 표기에서 int(year)가 트레이스백을 내던 허점
        # (레드팀 R3) — 4자리 연도만 뽑아 쓰고, 없으면 미기재로 취급해 계약 안에서 막는다
        ym = re.search(r"\d{4}", str(year))
        if not ym:
            missing.append("year")
        elif meta["year"]:
            year_ok = abs(int(ym.group()) - int(meta["year"])) <= 1  # online-first 허용
        else:
            unavailable.append("year")
    else:
        missing.append("year")
    if meta["retracted"]:
        verdict = "RETRACTED"
    elif (meta.get("type") in NON_PAPER_TYPES or meta.get("is_correction")
          or meta.get("is_retraction_notice")):
        # 심사보고서·그림 component·grant는 DOI·제목이 실재해도 [P#] 근거가 아니다.
        # 정오표·corrigendum도 마찬가지 — 연구 결과가 0건이라 근거가 될 수 없고,
        # Crossref가 journal-article로 분류해 type 검사만으론 안 걸린다(레드팀 R4).
        # 철회 통지문도 같은 부류 — 철회 사실은 원논문 DOI(RETRACTED)로 인용한다.
        verdict = "NON_ARTICLE"
    elif not matched:
        verdict = "MISMATCH"
    elif author_ok is False or year_ok is False:
        verdict = "MISMATCH_META"
    elif require_meta and missing:
        # 필드를 비우면 검사가 통째로 생략돼 게이트가 느슨해지는 역인센티브 차단
        verdict = "INCOMPLETE_META"
    elif not meta["retraction_checked"]:
        if meta.get("via_datacite"):
            # DataCite 전용 DOI(arXiv 10.48550/*·Zenodo)는 철회 플래그가 구조적으로
            # 없다 — 재시도해도 영원히 retraction_checked=False다. 이를
            # RETRACTION_UNCHECKED(재시도, exit 5)로 두면 정직하게 DOI를 기재한
            # 인용이 영구 교착에 빠지고, refs.json에서 DOI를 지워 NO_DOI(exit 2,
            # 승인된 예외)로 만드는 것이 유일한 탈출구가 된다 — 데이터 삭제가
            # 보상받는 역인센티브(레드팀 R4). '구조적 미검증'으로 분리해 exit 2
            # 계열(수동 확인 후 통과 가능)로 보낸다.
            verdict = "RETRACTION_NA"
        else:
            # 철회 판정 소스(OpenAlex)가 죽은 채 통과시키지 않는다 — fail-open 봉합
            verdict = "RETRACTION_UNCHECKED"
    else:
        verdict = "MATCH"
    # 절단 인용으로 통과했을 때 생략된 한정어를 노출 — 범위 축소 인용의 육안 대조용
    omitted = omitted_tokens(title, actual or "") if verdict == "MATCH" else []
    # 표기 변형으로 흡수된 쌍은 '생략'과 분리해 노출한다(레드팀 R5 — 치환의 오표시 방지)
    substituted = substituted_tokens(title, actual or "") if verdict == "MATCH" else []
    # 레드팀 R6: 부제 절단으로 사라진 결론 극성 표지(lack/no/limited…)는 기계 차단이 아니라
    # '경고 강화' 계층이다 — 별도 필드로 노출해 결론 방향 반전 여부를 반드시 확인하게 한다
    polarity_omitted = [t for t in omitted
                        if _stem(_canon(t)) in NEG_TOKENS or _stem(_canon(t)) in HEDGE_TOKENS]
    ym_c = re.search(r"\d{4}", str(year)) if year else None
    return {"doi": doi, "verdict": verdict, "similarity": round(ratio, 3),
            "matched_on": how, "claimed": title, "actual": actual,
            "omitted_tokens": omitted, "substituted": substituted,
            "polarity_omitted": polarity_omitted,
            "author_ok": author_ok, "year_ok": year_ok,
            "claimed_year": int(ym_c.group()) if ym_c else None,
            "meta_missing": missing, "meta_unavailable": unavailable,
            "retraction_checked": meta["retraction_checked"],
            "retraction_sources": meta.get("retraction_sources") or [],
            "record_type": meta.get("type"),
            "is_correction": bool(meta.get("is_correction")),
            "is_retraction_notice": bool(meta.get("is_retraction_notice")),
            "detail": ", ".join(meta["detail"]) or None,
            "cache": sorted(set(meta.get("cache_hits") or [])) or None,  # 서지 캐시 히트(철회는 항상 실 조회)
            "actual_first_author": meta["first_author"], "actual_year": meta["year"]}


# 레드팀 R6: 단건 NOT_FOUND가 2(=NO_DOI·RETRACTION_NA의 '수동 확인 후 유지' 승인 코드)였다 —
# 가짜 DOI가 예외 승인과 같은 코드를 받는 자기 계약 위반. batch와 같이 3(HARD)으로 통일.
VERIFY_EXIT = {"MATCH": 0, "NOT_FOUND": 3, "NO_DOI": 2, "RETRACTION_NA": 2,
               "MISMATCH": 3, "MISMATCH_META": 3, "NON_ARTICLE": 3, "RETRACTED": 4,
               "UNVERIFIED": 5, "RETRACTION_UNCHECKED": 5,
               "INCOMPLETE_META": 6, "BAD_ENTRY": 6}


def cmd_verify(a):
    """인용 환각 방어: DOI 실재·제목·(선택)저자·연도 일치와 철회 여부를 기계검증."""
    res = verify_one(a.doi, a.title, a.author, a.year)
    print(json.dumps(res, ensure_ascii=False))
    sys.exit(VERIFY_EXIT.get(res["verdict"], 1))


HARD_VERDICTS = ("MISMATCH", "MISMATCH_META", "RETRACTED", "NOT_FOUND", "NON_ARTICLE")
RETRY_VERDICTS = ("UNVERIFIED", "RETRACTION_UNCHECKED")
SOFT_VERDICTS = ("INCOMPLETE_META", "BAD_ENTRY", "NO_DOI", "RETRACTION_NA")
# exit 2(수동 확인 후 인용 유지가 승인되는 예외) 대상 — NO_DOI와 구조적 미검증
EXCEPTION_VERDICTS = ("NO_DOI", "RETRACTION_NA")


def _check_ref_entry(r):
    """refs.json 항목 스키마 검증 → 위반 사유(없으면 None).
    레드팀 R3: 항목이 문자열이거나 year가 'in press'면 트레이스백 + exit 1(§6 계약
    밖 코드)로 배치가 중간에 죽어 요약줄조차 안 나왔다 — BAD_ENTRY(6)로 계약 안에서 막는다."""
    if not isinstance(r, dict):
        return f"항목이 객체가 아니다({type(r).__name__}) — {{'id','doi','title','author','year'}} 형식"
    for f in ("id", "doi", "title", "author"):
        v = r.get(f)
        if v is not None and not isinstance(v, str):
            return f"{f}는 문자열이어야 한다({type(v).__name__})"
    if not (r.get("title") or "").strip():
        return "title 누락 — 제목 대조 불가"
    y = r.get("year")
    if y is not None and not isinstance(y, (int, str)):
        return f"year는 4자리 연도여야 한다({type(y).__name__})"
    if isinstance(y, str) and not re.search(r"\d{4}", y):
        return f"year에 4자리 연도가 없다({y!r}) — in-press면 온라인 공개연도를 쓴다"
    return None


def cmd_verify_batch(a):
    """참고문헌 전수 검증. 입력: JSON 배열
    [{"id":"P1","doi":"10...","title":"...","author":"1저자 성","year":2020}, ...]
    author·year는 **필수** — 빠지면 INCOMPLETE_META로 막는다(필드를 지우면 검사가
    생략돼 게이트가 느슨해지던 역인센티브 차단).
    exit: 전부 MATCH=0 / 가짜·오기재·철회·비논문 존재=3 / 남은 실패가 전부 일시장애
    (UNVERIFIED·RETRACTION_UNCHECKED)=5 / 실패 전원이 NO_DOI(수동 대조 대상)=2 /
    그 외(INCOMPLETE_META·BAD_ENTRY 혼재 포함)=6 / 빈 배열=2(0건은 통과가 아니다).
    NO_DOI가 다른 실패와 섞이면 2가 아니라 6 — exit 2는 SKILL.md의 예외 승인 신호라
    '하나라도 NO_DOI면 2'로 주면 미검증 인용이 예외를 타고 통과한다(레드팀 R3).
    exit 0(또는 SKILL.md §6이 명시한 예외 경로)을 확인하기 전 답변을 내보내지 않는다."""
    # 레드팀 R5: 파일 부재·JSON 문법 오류(후행 쉼표 등)가 트레이스백 + exit 1(§6 계약 밖)로
    # 죽어 요약줄이 없었다 — BAD_ENTRY와 같은 '수정 후 재실행' 계열(exit 6)로 통일.
    try:
        with open(a.file, encoding="utf-8") as f:
            refs = json.load(f)
    except (OSError, ValueError) as e:
        print(f"ERROR: refs.json 읽기/파싱 실패({a.file}): {e} — 경로·JSON 문법 수정 후 재실행",
              file=sys.stderr)
        sys.exit(6)
    if not isinstance(refs, list):
        print("ERROR: refs.json은 JSON 배열이어야 한다", file=sys.stderr)
        sys.exit(2)
    if not refs:
        # 빈 파일이 '0/0 MATCH · exit 0'으로 게이트를 통과하던 허점(레드팀 R2)
        print("ERROR: refs.json이 비어 있다 — 참고문헌 0건은 게이트 통과가 아니다."
              " 답변의 고유 [P#] 개수와 항목 수를 대조하라", file=sys.stderr)
        sys.exit(2)
    results = []
    for i, r in enumerate(refs):
        rid = (r.get("id") if isinstance(r, dict) else None) or f"#{i + 1}"
        bad = _check_ref_entry(r)
        if bad:
            res = {"doi": None, "verdict": "BAD_ENTRY", "detail": bad}
        else:
            try:
                res = verify_one(r.get("doi"), r.get("title") or "", r.get("author"),
                                 r.get("year"), require_meta=True)
            except Exception as e:  # 항목 하나 때문에 배치가 죽지 않게(요약줄 보장)
                res = {"doi": norm_doi(r.get("doi")), "verdict": "BAD_ENTRY",
                       "detail": f"항목 처리 실패: {e!r}"}
        res["id"] = rid
        results.append(res)
        v = res["verdict"]
        mark = "✓" if v == "MATCH" else ("⚠" if v in RETRY_VERDICTS + SOFT_VERDICTS else "✗")
        if v == "MATCH":
            # sim<1.0이면 실제 제목을 반드시 노출 — 의미가 뒤집힌 인용이 조용히
            # '✓ MATCH'로만 찍히던 허점(레드팀 R2) 봉합. 절단하지 않는다: 편측 덧붙임의
            # 차이는 정의상 제목 꼬리라 80자 절단이 육안 대조를 무력화했다(레드팀 R3).
            extra = "" if (res.get("similarity") or 0) >= 0.999 \
                else f" (실제: {res.get('actual')!r})"
            # 축약 인용으로 통과한 경우 무엇이 빠졌는지 명시 — 범위 축소(모집단·
            # 결과지표 삭제)는 형태상 정당 절단과 같아 기계 차단이 불가하다(레드팀 R4)
            if res.get("polarity_omitted"):
                # 레드팀 R6: 부제의 결론 극성 표지(lack/no/limited)가 잘려 나간 절단 —
                # 일반 '범위 축소' 경고보다 구체적으로: '효과 없음' RCT가 '효과 있음' 근거가 된다
                extra += (f" ⚠ 부제의 결론 극성 표지 생략: {', '.join(res['polarity_omitted'])}"
                          f" — 결론 방향 반전 여부 확인 필수(원제목으로 고칠 것)")
            elif res.get("omitted_tokens"):
                extra += f" ⚠ 생략: {', '.join(res['omitted_tokens'])} — 범위 축소인지 확인"
            # 레드팀 R6: ±1년 허용(online-first)으로 통과한 연도 차이를 무표시로 두지 않는다 —
            # 참고문헌·본문 연도 교정 계기를 만든다(허용폭·exit는 그대로)
            cy, ay = res.get("claimed_year"), res.get("actual_year")
            if cy and ay and cy != ay:
                extra += f" (연도: 인용 {cy} / 실제 {ay} — online-first 확인 후 참고문헌 연도 교정)"
            if res.get("substituted"):
                # 흡수된 표기 변형은 '생략'이 아니라 '치환'으로 표시 — 육안 대조 대상이 다르다
                extra += f" ≈ 표기변형: {', '.join(res['substituted'])}"
        elif v == "BAD_ENTRY":
            extra = f" ({res.get('detail')})"
        elif v == "NON_ARTICLE":
            if res.get("is_retraction_notice"):
                extra = (" (철회 **통지문** — 철회당한 논문이 아니다."
                         " 철회 사실은 원논문 DOI(RETRACTED)로 인용할 것:")
            elif res.get("is_correction"):
                extra = " (정오표·corrigendum 통지 — 연구 결과가 없다: 원논문 DOI로 교체:"
            else:
                extra = f" (Crossref type={res.get('record_type')} — 논문이 아니다:"
            extra += f" 실제 {res.get('actual')!r})"
        elif v == "INCOMPLETE_META":
            extra = f" (미기재: {', '.join(res.get('meta_missing') or [])}"\
                    f" — 실제: {res.get('actual_first_author')} {res.get('actual_year')})"
        elif v == "RETRACTION_UNCHECKED":
            extra = f" (철회 판정 소스 미응답: {res.get('detail')} — 재시도)"
        elif v == "RETRACTION_NA":
            # 재시도 대상이 아니다 — 철회 플래그가 구조적으로 존재하지 않는다
            extra = (f" (DataCite 전용 DOI — 철회 플래그 자체가 없어 재시도 무의미."
                     f" 서지는 대조됨: sim={res.get('similarity')}."
                     f" 답변에 '철회 검사 미수행' 명시 후 인용 유지 가능)")
        elif v in ("NOT_FOUND", "UNVERIFIED", "NO_DOI"):
            # 실패 사유(404 vs 전송오류·429)를 반드시 표시 — 부재와 장애를 구분
            extra = f" ({res.get('detail')})"
        else:
            extra = f" (실제: {res.get('actual_first_author')} {res.get('actual_year')}"\
                    f" — {res.get('actual')!r})"
        ctag = f" (cache: {','.join(res['cache'])})" if res.get("cache") else ""
        print(f"{mark} [{res['id']}] {v}"
              f" sim={res.get('similarity', '-')} {res['doi'] or '-'}{extra}{ctag}")
        time.sleep(0.3)  # polite rate limit
    n = len(results)
    cnt = {}
    for r in results:
        cnt[r["verdict"]] = cnt.get(r["verdict"], 0) + 1
    ok = cnt.get("MATCH", 0)
    # 철회 판정에 실제로 응답한 소스를 함께 찍는다 — '철회 0건'의 커버리지 한계를
    # 답변에 남기기 위해(PubMed는 생의학 한정, DataCite 전용 DOI는 철회 플래그 없음)
    rsrc = {}
    for r in results:
        for s in (r.get("retraction_sources") or []):
            rsrc[s] = rsrc.get(s, 0) + 1
    src_txt = "+".join(f"{s} {c}/{n}" for s, c in rsrc.items()) or "없음"
    line = (f"\n인용 검증: {ok}/{n} MATCH · 철회 {cnt.get('RETRACTED', 0)}건"
            f"(판정 소스: {src_txt})")
    # 같은 DOI에 여러 [P#]을 붙여 근거 폭을 부풀리는 경로 — 개수 3중 대조는 못 잡는다
    dgroups = {}
    for r in results:
        if r.get("doi"):
            dgroups.setdefault(r["doi"], []).append(r["id"])
    dups = {d: ids for d, ids in dgroups.items() if len(ids) > 1}
    if dgroups:
        line += f" · 고유 DOI {len(dgroups)}/{n}"
    if dups:
        line += " · ⚠ 중복 DOI: " + "; ".join(
            f"{'·'.join(ids)} → {d}" for d, ids in dups.items())
        line += " — 한 논문에 여러 [P#]이면 통합하거나 게이트 B에서 소명"
    if cnt.get("BAD_ENTRY"):
        line += f" · 스키마 위반 {cnt['BAD_ENTRY']}건 — refs.json 수정 후 재실행"
    if cnt.get("NON_ARTICLE"):
        line += f" · 비논문 레코드(심사보고서·component) {cnt['NON_ARTICLE']}건 — 인용 제거"
    if cnt.get("UNVERIFIED"):
        line += f" · 미검증(네트워크) {cnt['UNVERIFIED']}건 — 인용 제거 말고 재시도"
    if cnt.get("RETRACTION_UNCHECKED"):
        line += f" · 철회검사 미수행 {cnt['RETRACTION_UNCHECKED']}건 — 재시도"
    if cnt.get("RETRACTION_NA"):
        line += (f" · 철회검사 불가(DataCite 전용 DOI) {cnt['RETRACTION_NA']}건"
                 f" — 답변에 명시 후 유지")
    if cnt.get("INCOMPLETE_META"):
        line += f" · 저자·연도 미기재 {cnt['INCOMPLETE_META']}건 — refs.json 보완 후 재실행"
    if cnt.get("NO_DOI"):
        line += f" · NO_DOI {cnt['NO_DOI']}건 — arXiv 수동 대조 후 ledger에 기록"
    print(line)
    if a.json:
        print(json.dumps(results, ensure_ascii=False, indent=1))
    if ok == n:
        sys.exit(0)
    # 실패 유형별 분기 — '가짜 인용 존재'(3)와 재시도(5)·수동대조(2)·보완(6)을 구분
    if any(cnt.get(v) for v in HARD_VERDICTS):
        sys.exit(3)
    if any(cnt.get(v) for v in RETRY_VERDICTS):
        sys.exit(5)
    # exit 2는 SKILL.md §6이 '수동 대조 후 인용 유지'를 승인하는 예외 신호다 —
    # 실패 전원이 NO_DOI일 때만 준다. INCOMPLETE_META·BAD_ENTRY가 섞였는데 2를 주면
    # 저자·연도 대조가 생략된 인용이 예외 조항을 타고 통과한다(레드팀 R3).
    if sum(cnt.get(v, 0) for v in EXCEPTION_VERDICTS) == n - ok:
        sys.exit(2)
    sys.exit(6)


def _snowball_doi(raw):
    """`arXiv:<id>`·순수 arXiv id를 OpenAlex가 아는 `10.48550/arxiv.<id>`로 정규화(레드팀 R6:
    `snowball arXiv:2005.11401`이 norm_doi를 통과해 OpenAlex 404 'ERROR: HTTP 404'로만 끝났다)."""
    d = norm_doi(raw)
    if not d:
        return None
    mm = re.match(r"^arxiv:(.+)$", d)
    if mm:
        return "10.48550/arxiv." + re.sub(r"v\d+$", "", mm.group(1))
    if re.match(r"^\d{4}\.\d{4,5}(v\d+)?$", d):
        return "10.48550/arxiv." + re.sub(r"v\d+$", "", d)
    return d


def cmd_snowball(a):
    doi = _snowball_doi(a.doi)
    if not doi:
        print("ERROR: 잘못된 DOI", file=sys.stderr); sys.exit(1)
    qdoi = urllib.parse.quote(doi, safe="/")  # '#' 포함 DOI 절단 방지
    # 레드팀 R6: 유명 앵커(수천 인용)에서 주제 관련 후속 논문이 --limit 200 밖에 남았다 —
    # OpenAlex title_and_abstract.search를 cites/refs 필터에 결합(무키·무료). '관련 부분집합
    # 우선 회수'이지 전수 회수가 아니다(키워드 없이 다루는 후속 논문은 여전히 못 잡는다).
    topic = (getattr(a, "query", None) or "").strip()
    topic_filt = f",title_and_abstract.search:{topic}" if topic else ""
    sel = "doi,display_name,publication_year,publication_date,cited_by_count"
    yf = getattr(a, "year_from", None)
    # 레드팀 R2: cites가 인용수 내림차순 고정이라 유명 앵커에서는 상위 슬롯을 7~10년 전
    # 논문이 독점해 '후속 연구 회수'라는 목적 자체가 달성 불가였다 → 연도 필터·정렬 노출
    sort = "publication_date:desc" if getattr(a, "sort", "citations") == "date" \
        else "cited_by_count:desc"
    # 레드팀 R5: --limit이 OpenAlex per-page 상한(200)을 넘으면 HTTP 400으로 전멸했다 —
    # search의 _n_for 클램프(레드팀 R2)와 같은 처리. cursor 페이징은 §3 스크리닝 비용
    # 규율상 불필요(200건 초과 회수는 파이프라인 범위 밖).
    cap = SOURCE_CAP["openalex"]
    limit = int(a.limit)
    if limit > cap:
        print(f"# openalex: --limit {limit} > per-page 상한 {cap} → {cap}로 조정", file=sys.stderr)
        limit = cap
    if a.direction == "cites":  # 이 논문을 인용한 후속 논문
        b, c = http_get(f"https://api.openalex.org/works/doi:{qdoi}?mailto={EMAIL}&select=id")
        if not b:
            print(f"ERROR: HTTP {c}" + (f" — OpenAlex에 {doi} 없음(arXiv 논문은 arXiv:<id> 또는"
                                      " 10.48550/arXiv.<id>로, 식별자 형식 확인)" if c == 404 else ""),
                  file=sys.stderr); sys.exit(1)
        wid = json.loads(b)["id"].rsplit("/", 1)[-1]
        filt = f"cites:{wid}" + topic_filt
        if yf:
            filt += f",from_publication_date:{int(yf)}-01-01"
        url = ("https://api.openalex.org/works?"
               + urllib.parse.urlencode({"filter": filt, "per-page": str(limit),
                                         "sort": sort, "mailto": EMAIL,
                                         "select": sel}))
        b2, c2 = http_get(url)
        if not b2:
            # 조용한 빈 출력으로 위장하지 않는다 — '후속 논문 없음'(정상)과 구분
            print(f"ERROR: 인용 목록 조회 실패 HTTP {c2}", file=sys.stderr); sys.exit(1)
        items = json.loads(b2).get("results", [])
    else:  # refs: 이 논문이 인용한 문헌
        b, c = http_get(f"https://api.openalex.org/works/doi:{qdoi}?mailto={EMAIL}"
                        "&select=referenced_works")
        if not b:
            print(f"ERROR: HTTP {c}", file=sys.stderr); sys.exit(1)
        refs = json.loads(b).get("referenced_works") or []
        items = []
        if refs:
            # referenced_works 배열은 OpenAlex ID 오름차순(≈색인순) — 그대로 절단하면
            # 오래된 문헌만 남는 편향. 배치 조회 + 인용수 내림차순으로 핵심부터 회수.
            # 레드팀 R5: OR 필터 상한(100)에 맞춰 refs[:100]만 조회해 101건 이후를 통째로
            # 버렸다 — 앵커 리뷰(refs 100~300건)에서 --year-from 2009 문헌의 62%가 조용히
            # 탈락(Hallmarks of Cancer NG 실측). 100건 단위로 전부 조회한 뒤 클라이언트에서
            # 정렬·limit을 적용한다(refs 300건 = 3회 호출, 무키·무료).
            chunks = [refs[i:i + 100] for i in range(0, len(refs), 100)]
            if len(chunks) > 1:
                print(f"# 참고문헌 {len(refs)}건 → {len(chunks)}회 배치 조회 후 클라이언트 정렬",
                      file=sys.stderr)
            for chunk in chunks:
                wids = "|".join(r.rsplit("/", 1)[-1] for r in chunk)
                filt = f"openalex_id:{wids}" + topic_filt
                if yf:
                    filt += f",from_publication_date:{int(yf)}-01-01"
                url = ("https://api.openalex.org/works?"
                       + urllib.parse.urlencode({"filter": filt,
                                                 "per-page": str(len(chunk)),
                                                 "sort": sort,
                                                 "mailto": EMAIL, "select": sel}))
                b2, c2 = http_get(url)
                if not b2:
                    print(f"ERROR: 참고문헌 배치 조회 실패 HTTP {c2}", file=sys.stderr)
                    sys.exit(1)
                items.extend(json.loads(b2).get("results", []))
            if sort.startswith("publication_date"):
                items.sort(key=lambda it: str(it.get("publication_date") or
                                              it.get("publication_year") or ""), reverse=True)
            else:
                items.sort(key=lambda it: it.get("cited_by_count") or 0, reverse=True)
            items = items[:limit]
    if not items:
        print("# snowball: 결과 없음")
        return
    for it in items:
        print(f"({it.get('publication_year')}) {it.get('display_name')}"
              f" · 인용 {it.get('cited_by_count')} · {norm_doi(it.get('doi')) or '-'}")


def _s2_paper_ident(doi):
    """S2 식별자 — `10.48550/arxiv.<id>`·`arxiv:<id>`·순수 arXiv id는 `arXiv:<id>`(버전 접미
    제거), 그 외는 `DOI:<doi>`. 레드팀 R6: recommend가 무조건 DOI: 접두를 붙여 arXiv 전용
    DOI(CS·ML의 가장 흔한 앵커)에서 항상 S2 404였다 — _fetch_s2_count의 규칙과 통일."""
    d = (doi or "").strip()
    mm = re.match(r"(?i)^(?:10\.48550/arxiv\.|arxiv:)(.+)$", d)
    if mm:
        return "arXiv:" + re.sub(r"v\d+$", "", mm.group(1))
    if re.match(r"^\d{4}\.\d{4,5}(v\d+)?$", d):
        return "arXiv:" + re.sub(r"v\d+$", "", d)
    return "DOI:" + d


def cmd_recommend(a):
    """S2 recommendations — 앵커 논문과 유사한 논문 추천(snowball이 못 잡는 의미 근접)."""
    doi = norm_doi(a.doi)
    if not doi:
        print("ERROR: 잘못된 DOI", file=sys.stderr); sys.exit(1)
    ident = _s2_paper_ident(doi)
    headers = {}
    key = os.environ.get("S2_API_KEY")
    if key:
        headers["x-api-key"] = key
    params = {"fields": "title,year,citationCount,externalIds,venue,authors,isOpenAccess",
              "limit": str(a.limit)}
    url = ("https://api.semanticscholar.org/recommendations/v1/papers/forpaper/"
           f"{urllib.parse.quote(ident, safe=':/')}?" + urllib.parse.urlencode(params))
    body, code = http_get(url, headers=headers, retries=1)
    if not body:
        # S2 무키 공유풀 혼잡(429)은 정상 — 재시도 말고 안내. 404는 식별자 형식 문제일 수 있다
        hint = ("429면 무키 혼잡, snowball로 대체" if code == 429 else
                f"404면 S2가 {ident}를 모르는 것 — arXiv 논문은 arXiv:<id> 또는 10.48550/arXiv.<id>로")
        print(f"# recommend 실패 (S2 HTTP {code} — {hint})", file=sys.stderr)
        sys.exit(1)
    recs = json.loads(body).get("recommendedPapers", [])
    if not recs:
        print("# recommend: 결과 없음")
        return
    print(f"# recommend (앵커 {doi} 유사 {len(recs)}건)")
    for i, p in enumerate(recs, 1):
        d = norm_doi((p.get("externalIds") or {}).get("DOI")) or "-"
        au = ", ".join(x.get("name") for x in (p.get("authors") or [])[:3]) or "?"
        oa = "OA" if p.get("isOpenAccess") else "  "
        print(f"[R{i}] ({p.get('year')}) {p.get('title')}")
        print(f"     {au} · {p.get('venue') or '?'} · 인용 {p.get('citationCount', 0)} · {oa} · {d}")


# ---------------- project (논문 테이블 · 프로젝트 레지스트리) ----------------
# Liner Scholar의 "프로젝트 = 논문 테이블 + AI 열 + 질문 이력"을 파일 하나로 재현한다.
# 핵심 역할은 **P# 전역 레지스트리** — search는 실행마다 [P1]부터 다시 매기지만
# papers.json의 pid는 DOI 기준으로 영구 고정되므로 ledger·[P#] 태그·refs.json이
# 검색 회차와 무관하게 같은 논문을 가리킨다(§3 "P# 전역 재부여"의 파일 구현).
# 파일은 vault 밖(SCHOLAR_HOME)에 두고, vault에는 render 결과(markdown)만 남긴다.

SCHOLAR_HOME = os.path.expanduser(
    os.environ.get("SCHOLAR_HOME", "~/Documents/mybrain_raw/scholar_projects"))

# 기본 AI 열 — Liner 논문 테이블의 열 구성. kind=ai 열은 스크립트가 채우지 않는다
# (Claude가 §4 ledger 발췌를 근거로 `project set`으로 채운다).
DEFAULT_COLS = [
    {"key": "reason", "label": "이 논문이 사용된 이유", "kind": "ai",
     "prompt": "이 프로젝트 질문에 이 논문이 왜 필요한가 — 1~2문장. [해석] 성격(논문 주장이 아님)."},
    {"key": "keywords", "label": "키워드", "kind": "ai",
     "prompt": "핵심 키워드 3~5개, ' · '로 구분. 초록·본문에 실제로 등장하는 용어만."},
    {"key": "focus", "label": "연구 초점", "kind": "ai",
     "prompt": "무엇을 어떤 방법으로 연구했나 — 1문장. 초록 발췌 근거 필수."},
    {"key": "conclusion", "label": "핵심 결론", "kind": "ai",
     "prompt": "논문이 직접 말한 결론·수치 — 1~2문장. ledger 발췌 없는 내용 금지. 초록만 읽었으면 '(초록)' 표기."},
]


def _slug_ok(slug):
    return bool(re.fullmatch(r"[A-Za-z0-9가-힣_\-]{1,60}", slug or ""))


def _pdir(slug):
    if not _slug_ok(slug):
        print(f"ERROR: slug는 영문·숫자·한글·_·-만(60자 이하): {slug!r}", file=sys.stderr)
        sys.exit(2)
    return os.path.join(SCHOLAR_HOME, slug)


def _pload(slug, must=True):
    d = _pdir(slug)
    f = os.path.join(d, "project.json")
    if not os.path.exists(f):
        if must:
            print(f"ERROR: 프로젝트 없음: {f}\n  → project init {slug} --title \"...\"",
                  file=sys.stderr)
            sys.exit(2)
        return None
    with open(f, encoding="utf-8") as fh:
        return json.load(fh)


def _psave(slug, proj):
    d = _pdir(slug)
    os.makedirs(d, exist_ok=True)
    proj["updated"] = time.strftime("%Y-%m-%d")
    tmp = os.path.join(d, "project.json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(proj, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(d, "project.json"))


def _first_family(authors):
    for a in authors or []:
        if a:
            parts = a.strip().split()
            if parts:
                return parts[-1]
    return None


def _arxiv_id_from(s):
    m = re.search(r"(?:arxiv\.org/(?:abs|pdf)/|arXiv:)?(\d{4}\.\d{4,5}(?:v\d+)?)(?:\.pdf)?$", s.strip())
    return m.group(1) if m else None


_ARXIV_LAST = [0.0]


def _fetch_arxiv_meta(aid):
    # arXiv API는 3초 미만 연속 호출에 429를 낸다(실측 2026-09-05) — 호출 간격 강제
    gap = 3.0 - (time.time() - _ARXIV_LAST[0])
    if gap > 0:
        time.sleep(gap)
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode({"id_list": aid})
    body, code = http_get(url)
    _ARXIV_LAST[0] = time.time()
    if not body:
        return None, f"arxiv HTTP {code}"
    rows = _arxiv_parse(body)
    if not rows:
        return None, "arxiv: 항목 없음"
    r = rows[0]
    # 저널 DOI가 붙은 프리프린트는 저널 메타(연도·게재지)를 정본으로 — arXiv 게시연도로
    # 등록하면 verify-batch에서 MISMATCH_META(연도 불일치)가 난다(실측 2026-09-05).
    if r.get("doi"):
        j, _ = _fetch_doi_meta(r["doi"])
        if j:
            j["arxiv_id"] = aid
            if not j.get("abstract"):
                j["abstract"] = _arxiv_summary(body)
            return j, None
    r["arxiv_id"] = aid
    r["abstract"] = _arxiv_summary(body)
    return r, None


def _arxiv_summary(body):
    m = re.search(r"<summary>(.*?)</summary>", body, re.S)
    return html.unescape(re.sub(r"\s+", " ", m.group(1)).strip()) if m else None


_S2_LAST = [0.0]
_S2_LOCK = threading.Lock()  # bundle 병렬 초록 폴백이 무키 공유풀 3초 간격을 지키게


def _s2_get(path_or_query, tries=5):
    hdr = {}
    key = os.environ.get("S2_API_KEY")
    if key:
        hdr["x-api-key"] = key
    url = "https://api.semanticscholar.org/graph/v1/" + path_or_query
    last = None
    for i in range(tries):
        gap = 3.0 - (time.time() - _S2_LAST[0])   # 무키 공유풀 — 실측 1.2s로도 429 다발
        if gap > 0:
            time.sleep(gap)
        body, code = http_get(url, headers=hdr, retries=0)
        _S2_LAST[0] = time.time()
        if body:
            try:
                return json.loads(body), None
            except Exception:
                return None, "s2: JSON 오류"
        last = code
        if code == 429 and i < tries - 1:
            time.sleep(5.0 * (i + 1))
            continue
        break
    return None, f"s2 HTTP {last}"


def _norm_title_key(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def _fetch_s2_count(doi=None, arxiv_id=None, title=None, tries=5):
    """Semantic Scholar 인용수 단건. (count, err).
    OpenAlex는 같은 논문을 arXiv/학회 레코드로 쪼개 세고 arXiv 참고문헌 파싱이 약해
    CS·ML 논문 인용수를 크게 저평가한다(실측 2026-09-05: Turpin 2023 OA 90 vs S2 1,584).
    S2도 가끔 arXiv 레코드와 저널 레코드가 분리돼 있어(실측: Mahowald 2024 arXiv 16 vs DOI 569)
    **arXiv id·DOI 양쪽을 조회해 큰 값**을 쓴다. S2는 `10.48550/arXiv.*`·ACL·NeurIPS(10.52202)
    DOI를 대부분 모르므로(404) arXiv DOI는 arXiv id로 바꾸고, 전부 404면 제목 검색으로 대조."""
    idents = []
    aid = arxiv_id
    if not aid and doi and doi.lower().startswith("10.48550/arxiv."):
        aid = doi.lower().split("10.48550/arxiv.", 1)[1]
    if aid:
        idents.append("arXiv:" + str(aid).split("v")[0])
    if doi and not doi.lower().startswith("10.48550/"):
        idents.append("DOI:" + doi)
    if not idents and not title:
        return None, "식별자 없음"
    best, errs = None, []
    for ident in idents:
        d, e = _s2_get("paper/" + urllib.parse.quote(ident, safe=":/") + "?fields=citationCount", tries)
        if d is None:
            errs.append(f"{ident.split(':')[0]}:{e}")
            continue
        c = d.get("citationCount")
        if isinstance(c, int) and (best is None or c > best):
            best = c
    if best is None and title:
        d, e = _s2_get("paper/search?" + urllib.parse.urlencode(
            {"query": title, "fields": "title,citationCount", "limit": 3}), tries)
        if d:
            want = _norm_title_key(title)
            for it in d.get("data") or []:
                if _norm_title_key(it.get("title")) == want and isinstance(it.get("citationCount"), int):
                    best = it["citationCount"]
                    break
            if best is None:
                errs.append("title:불일치")
        else:
            errs.append(f"title:{e}")
    if best is None:
        return None, " ".join(errs) or "s2: 없음"
    return best, None


def _oa_work_by_doi(doi, full_authors=False):
    """OpenAlex 단건(DOI 키) — paper·bundle·project add 공용, 30일 캐시.
    반환 (work, err, cached). work: title/year/doi/venue/citations/type/is_oa/oa_url/
    authors/abstract/referenced_works_count/is_retracted.
    캐시 히트여도 is_retracted는 저장본을 쓰지 않고 `select=is_retracted`로 **매번 실 조회**
    한다(철회 미캐시 원칙 — 늦게 반영되는 철회를 30일 묵히지 않기 위해). 실 조회가
    실패하면 None(미확인)이다 — False(철회 아님)로 위장하지 않는다."""
    ident = "doi:" + urllib.parse.quote(doi, safe="/")
    url = (f"https://api.openalex.org/works/{ident}?"
           + urllib.parse.urlencode({"mailto": EMAIL}))
    w = cache_get("oa_work", doi)
    if w is not None:
        w = dict(w)
        w["cache"] = True
        w["is_retracted"] = None
        b2, _c2 = http_get(url + "&select=is_retracted")
        if b2:
            try:
                w["is_retracted"] = bool(json.loads(b2).get("is_retracted"))
            except ValueError:
                pass
        return w, None, True
    body, code = http_get(url)
    if not body:
        return None, f"openalex HTTP {code}", False
    j = json.loads(body)
    w = {
        "doi": norm_doi(j.get("doi")) or doi,
        "title": _clean_title(j.get("display_name") or j.get("title")),
        "display_name": j.get("display_name"),
        "year": j.get("publication_year"),
        "venue": ((j.get("primary_location") or {}).get("source") or {}).get("display_name"),
        "citations": j.get("cited_by_count"),
        "is_oa": (j.get("open_access") or {}).get("is_oa"),
        "oa_url": (j.get("open_access") or {}).get("oa_url"),
        "is_retracted": bool(j.get("is_retracted")),
        "type": j.get("type"),
        "authors": [x.get("author", {}).get("display_name") for x in j.get("authorships", [])],
        "abstract": uninvert_abstract(j.get("abstract_inverted_index")),
        "referenced_works_count": len(j.get("referenced_works") or []),
        "cache": False,
    }
    cache_put("oa_work", doi, w)  # is_retracted는 cache_put이 걷어낸다
    return w, None, False


def _fetch_doi_meta(doi):
    """OpenAlex 단건 — cmd_paper와 같은 필드. 실패 시 (None, err). 캐시 히트면 meta["cache"]=True."""
    w, err, _cached = _oa_work_by_doi(doi)
    if w is None:
        return None, err
    meta = {k: w.get(k) for k in ("doi", "title", "year", "venue", "citations", "is_oa", "oa_url",
                                  "is_retracted", "type", "abstract", "cache")}
    meta["authors"] = (w.get("authors") or [])[:8]
    return meta, None


def _paper_key(p):
    return (p.get("doi") or "").lower() or ("arxiv:" + (p.get("arxiv_id") or "")).lower()


def _next_pid(proj):
    used = {int(p["pid"][1:]) for p in proj["papers"] + proj.get("retired", [])
            if re.fullmatch(r"P\d+", p.get("pid", ""))}
    return f"P{max(used) + 1 if used else 1}"


def cmd_project_init(a):
    if _pload(a.slug, must=False):
        print(f"ERROR: 이미 존재: {_pdir(a.slug)} (덮어쓰지 않음)", file=sys.stderr)
        sys.exit(2)
    proj = {
        "slug": a.slug, "title": a.title or a.slug,
        "created": time.strftime("%Y-%m-%d"),
        "columns": [dict(c) for c in DEFAULT_COLS],
        "papers": [], "questions": [],
    }
    _psave(a.slug, proj)
    d = _pdir(a.slug)
    for name in ("ledger.md",):
        p = os.path.join(d, name)
        if not os.path.exists(p):
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(f"# evidence ledger — {proj['title']}\n\n"
                         "| P# | DOI | 발췌문 | 초록/본문 |\n|---|---|---|---|\n")
    print(f"프로젝트 생성: {d}\n  title={proj['title']} · 기본 AI 열 {len(DEFAULT_COLS)}개 "
          f"({', '.join(c['label'] for c in DEFAULT_COLS)})")


def _add_one(proj, meta, source_tag, refresh=False):
    key = _paper_key(meta)
    if not key or key == "arxiv:":
        return None, "식별자 없음(DOI·arXiv id 모두 부재)"
    for p in proj["papers"]:
        if _paper_key(p) == key:
            if refresh:
                for k, v in meta.items():
                    if v is not None and k not in ("pid", "cells", "added", "via"):
                        p[k] = v
                return p, "refreshed"
            return p, "dup"
    pid = _next_pid(proj)
    row = {"pid": pid, "added": time.strftime("%Y-%m-%d"), "via": source_tag,
           "cells": {}}
    for k in ("doi", "arxiv_id", "title", "year", "venue", "citations", "citations_s2", "is_oa",
              "oa_url", "is_retracted", "type", "authors", "abstract", "id"):
        if meta.get(k) is not None:
            row[k] = meta[k]
    proj["papers"].append(row)
    return row, None


def cmd_project_add(a):
    proj = _pload(a.slug)
    items = []
    if a.from_json:
        with open(a.from_json, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            # search --json(results) · bundle(papers) 양쪽 호환
            results = data.get("results") if "results" in data else data.get("papers", data)
        else:
            results = data
        tag_prefix = "bundle:" if isinstance(data, dict) and "papers" in data and "results" not in data else "search:"
        pick = None
        if a.pick:
            pick = {x.strip().upper() for x in a.pick.split(",") if x.strip()}
        for i, r in enumerate(results, 1):
            if pick and f"P{i}" not in pick:
                continue
            items.append((tag_prefix + os.path.basename(a.from_json), r))
    for ident in a.ids:
        items.append(("manual", {"_ident": ident}))
    if not items:
        print("ERROR: 추가할 항목 없음 (DOI/arXiv id 또는 --from search.json)", file=sys.stderr)
        sys.exit(2)
    added, dups, failed = [], [], []
    for tag, r in items:
        ident = r.get("_ident")
        meta, err = None, None
        doi = norm_doi(ident) if ident else r.get("doi")
        aid = _arxiv_id_from(ident) if ident else (r.get("arxiv_id") or _arxiv_id_from(r.get("id") or ""))
        if doi and not a.no_fetch:
            meta, err = _fetch_doi_meta(doi)
        if meta is None and aid and not a.no_fetch:
            meta, err = _fetch_arxiv_meta(aid)
            if meta:
                meta["arxiv_id"] = aid
        if meta is None and not ident:
            # 검색 결과 행을 그대로(초록 없이) 등록 — 네트워크 실패·--no-fetch 경로
            meta = dict(r)
            if aid:
                meta["arxiv_id"] = aid
            if not meta.get("doi") and not meta.get("arxiv_id"):
                err = err or "식별자 없음"
        if meta is None:
            failed.append((ident or r.get("title"), err))
            continue
        if meta.get("is_retracted"):
            failed.append((meta.get("title"), "RETRACTED — 근거로 등록하지 않음"))
            continue
        if not a.no_fetch and not getattr(a, "no_s2", False):
            c2, e2 = _fetch_s2_count(meta.get("doi"), meta.get("arxiv_id") or aid, meta.get("title"))
            if c2 is not None:
                meta["citations_s2"] = c2
            elif e2:
                print(f"  ⚠ S2 인용수 미확보({e2}): {meta.get('title')}", file=sys.stderr)
        row, why = _add_one(proj, meta, tag, refresh=a.refresh)
        if why == "dup":
            dups.append(row["pid"])
        elif why == "refreshed":
            dups.append(row["pid"] + "↻")
        elif why:
            failed.append((meta.get("title"), why))
        else:
            added.append(row)
    _psave(a.slug, proj)
    for row in added:
        print(f"[{row['pid']}] ({row.get('year')}) {row.get('title')} · "
              f"{row.get('doi') or ('arXiv:' + str(row.get('arxiv_id')))}"
              f"{' · 초록O' if row.get('abstract') else ' · 초록X'}")
    print(f"# 추가 {len(added)} · 중복 {len(dups)}{'(' + ','.join(dups) + ')' if dups else ''}"
          f" · 실패 {len(failed)} · 총 {len(proj['papers'])}편")
    for t, e in failed:
        print(f"  ✗ {t}: {e}", file=sys.stderr)
    if failed:
        sys.exit(1)


def cmd_project_recount(a):
    """등록 논문 전부의 인용수만 재조회(OpenAlex + S2). 제목·초록·셀은 건드리지 않는다
    (손으로 교정한 초록·DOI가 --refresh로 덮이는 사고 방지)."""
    proj = _pload(a.slug)
    want = None
    if a.pids:
        want = {x.strip().upper() for x in a.pids.split(",") if x.strip()}
    n_oa = n_s2 = 0
    for p in proj["papers"]:
        if want and p["pid"] not in want:
            continue
        old_oa, old_s2 = p.get("citations"), p.get("citations_s2")
        if not a.s2_only and p.get("doi"):
            m, _ = _fetch_doi_meta(p["doi"])
            if m and m.get("citations") is not None:
                p["citations"] = m["citations"]; n_oa += 1
        c2, e2 = _fetch_s2_count(p.get("doi"), p.get("arxiv_id"), p.get("title"))
        if c2 is not None:
            p["citations_s2"] = c2; n_s2 += 1
        else:
            print(f"  ⚠ [{p['pid']}] S2 미확보({e2})", file=sys.stderr)
        print(f"[{p['pid']}] OA {old_oa}→{p.get('citations')} · S2 {old_s2}→{p.get('citations_s2')}"
              f" · {(p.get('title') or '')[:60]}")
    proj["recounted"] = time.strftime("%Y-%m-%d")
    _psave(a.slug, proj)
    print(f"# 인용수 재조회 {time.strftime('%Y-%m-%d')} · OpenAlex {n_oa}건 · S2 {n_s2}건 / {len(proj['papers'])}편")


def cmd_project_export_db(a):
    """아티팩트 앱(db 캐퍼빌리티)용 문서 1개를 JSON으로 출력. 컬렉션 projects/<slug>.
    --full·--verify-line·--subtitle는 project.json의 view 절에 영구 저장(다음 export·render에 재사용)."""
    proj = _pload(a.slug)
    view = proj.setdefault("view", {})
    if a.full:
        view["full_pids"] = [x.strip().upper() for x in a.full.split(",") if x.strip()]
    if a.verify_line:
        view["verify_line"] = a.verify_line
    if a.subtitle:
        view["subtitle"] = a.subtitle
    _psave(a.slug, proj)
    full = set(view.get("full_pids") or [])
    papers = []
    for p in proj["papers"]:
        papers.append({k: p.get(k) for k in ("pid", "title", "authors", "year", "venue", "doi", "arxiv_id",
                                              "citations", "citations_s2", "is_oa", "abstract", "type")}
                      | {"cells": p.get("cells") or {}, "full": p["pid"] in full})
    doc = {"slug": proj["slug"], "title": proj["title"], "created": proj.get("created"),
           "columns": proj["columns"], "papers": papers, "questions": proj.get("questions") or [],
           "verify_line": view.get("verify_line"), "subtitle": view.get("subtitle"),
           "recounted": proj.get("recounted"), "exported": time.strftime("%Y-%m-%d")}
    out = json.dumps(doc, ensure_ascii=False, indent=1)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(out)
        print(f"export → {a.out} ({len(papers)}편, {len(out.encode()) // 1024} KB)")
    else:
        print(out)


def cmd_project_import_db(a):
    """아티팩트 db에서 내려받은 projects/<slug>.json을 레지스트리에 병합 — 페이지에서 채운
    AI 열(cells)·추가된 열(columns)·질문 이력만 가져온다. 서지 메타는 레지스트리가 정본이라 건드리지 않는다."""
    with open(a.file, encoding="utf-8") as fh:
        doc = json.load(fh)
    doc = doc.get("data", doc) if isinstance(doc, dict) and "data" in doc and "papers" not in doc else doc
    proj = _pload(a.slug, must=False)
    if proj is None:
        # 앱("＋ 새 프로젝트")에서 만든 프로젝트 — 레지스트리를 여기서 생성
        _pdir(a.slug)
        proj = {"slug": a.slug, "title": doc.get("title") or a.slug, "created": doc.get("created") or time.strftime("%Y-%m-%d"),
                "columns": [dict(c) for c in DEFAULT_COLS], "papers": [], "questions": [], "origin": "app"}
        _psave(a.slug, proj)
        d = _pdir(a.slug)
        lp = os.path.join(d, "ledger.md")
        if not os.path.exists(lp):
            with open(lp, "w", encoding="utf-8") as fh:
                fh.write(f"# evidence ledger — {proj['title']}\n\n| P# | DOI | 발췌문 | 초록/본문 |\n|---|---|---|---|\n")
        print(f"레지스트리 신규 생성(앱 프로젝트): {d} · title={proj['title']}")
    if doc.get("title") and doc["title"] != proj.get("title"):
        proj["title"] = doc["title"]   # 앱에서 이름 변경한 경우 반영
    known = {c["key"] for c in proj["columns"]}
    n_col = 0
    for c in doc.get("columns") or []:
        if c.get("key") and c["key"] not in known:
            proj["columns"].append({"key": c["key"], "label": c.get("label") or c["key"],
                                    "kind": c.get("kind", "ai"), "prompt": c.get("prompt", "")})
            known.add(c["key"]); n_col += 1
    by = {p["pid"]: p for p in proj["papers"]}
    n_cell = 0
    for dp in doc.get("papers") or []:
        p = by.get(dp.get("pid"))
        if not p:
            continue
        for k, v in (dp.get("cells") or {}).items():
            if v and p["cells"].get(k) != v:
                p["cells"][k] = v; n_cell += 1
    n_q = 0
    have = {(q.get("date"), q.get("q") or q.get("question")) for q in proj.get("questions") or []}
    for q in doc.get("questions") or []:
        if (q.get("date"), q.get("q") or q.get("question")) not in have:
            proj.setdefault("questions", []).append(q); n_q += 1
    _psave(a.slug, proj)
    print(f"import ← {a.file}: 열 +{n_col} · 셀 갱신 {n_cell} · 질문 +{n_q}")


def cmd_project_rm(a):
    """행 삭제. P#는 재사용되지 않는다(이미 쓰인 [P#] 태그가 다른 논문을 가리키지 않게)."""
    proj = _pload(a.slug)
    p = _find_paper(proj, a.pid)
    proj["papers"] = [x for x in proj["papers"] if x is not p]
    proj.setdefault("retired", []).append({"pid": p["pid"], "doi": p.get("doi"),
                                           "title": p.get("title"),
                                           "removed": time.strftime("%Y-%m-%d")})
    _psave(a.slug, proj)
    print(f"삭제: [{p['pid']}] {p.get('title')} — 번호 {p['pid']}는 재사용되지 않음")


def cmd_project_col(a):
    proj = _pload(a.slug)
    cols = proj["columns"]
    if a.action == "list":
        for c in cols:
            filled = sum(1 for p in proj["papers"] if p["cells"].get(c["key"]))
            print(f"{c['key']:<14} {c['label']} [{c.get('kind','ai')}] "
                  f"{filled}/{len(proj['papers'])} 채움 — {c.get('prompt','')}")
        return
    if a.action == "add":
        key = a.key or re.sub(r"[^a-z0-9]+", "_", (a.label or "").lower()).strip("_") or f"col{len(cols)+1}"
        if any(c["key"] == key for c in cols):
            print(f"ERROR: 열 키 중복: {key}", file=sys.stderr)
            sys.exit(2)
        cols.append({"key": key, "label": a.label or key, "kind": "ai", "prompt": a.prompt or ""})
        _psave(a.slug, proj)
        print(f"열 추가: {key} ({a.label}) — 이제 {len(proj['papers'])}편에 대해 "
              f"`project set {a.slug} <P#> {key} \"...\"` 로 채운다")
        return
    if a.action == "rm":
        n = len(cols)
        proj["columns"] = [c for c in cols if c["key"] != a.key]
        if len(proj["columns"]) == n:
            print(f"ERROR: 열 없음: {a.key}", file=sys.stderr)
            sys.exit(2)
        for p in proj["papers"]:
            p["cells"].pop(a.key, None)
        _psave(a.slug, proj)
        print(f"열 삭제: {a.key}")


def _find_paper(proj, pid):
    pid = pid.upper()
    for p in proj["papers"]:
        if p["pid"] == pid:
            return p
    print(f"ERROR: {pid} 없음 (등록: {', '.join(p['pid'] for p in proj['papers']) or '없음'})",
          file=sys.stderr)
    sys.exit(2)


def cmd_project_set(a):
    proj = _pload(a.slug)
    keys = {c["key"] for c in proj["columns"]}
    cells = []
    if a.from_json:
        with open(a.from_json, encoding="utf-8") as fh:
            cells = json.load(fh)
    elif a.pid and a.key and a.value is not None:
        cells = [{"pid": a.pid, "key": a.key, "value": a.value}]
    else:
        print("ERROR: <P#> <key> <value> 또는 --from cells.json", file=sys.stderr)
        sys.exit(2)
    n = 0
    for c in cells:
        if c.get("key") not in keys:
            print(f"ERROR: 미정의 열 {c.get('key')!r} — 먼저 `project col add`", file=sys.stderr)
            sys.exit(2)
        p = _find_paper(proj, c["pid"])
        p["cells"][c["key"]] = (c.get("value") or "").strip()
        n += 1
    _psave(a.slug, proj)
    print(f"셀 {n}개 기록")


def cmd_project_show(a):
    proj = _pload(a.slug)
    proj, _note = _apply_selection(proj, a)
    if _note:
        print(f"🔎 {_note}")
    print(f"# {proj['title']} ({proj['slug']}) · 논문 {len(proj['papers'])}편 · "
          f"생성 {proj['created']} · 갱신 {proj.get('updated')}")
    print(f"  디렉토리: {_pdir(a.slug)}")
    cols = proj["columns"]
    print("  열: " + ", ".join(f"{c['label']}({c['key']})" for c in cols))
    empty = []
    for p in proj["papers"]:
        miss = [c["key"] for c in cols if not p["cells"].get(c["key"])]
        oa = "OA" if p.get("is_oa") else "  "
        print(f"[{p['pid']}] ({p.get('year')}) {p.get('title')}")
        print(f"     {', '.join(x for x in (p.get('authors') or [])[:3] if x) or '?'} · "
              f"{p.get('venue') or '?'} · 인용 {_cit_label(p)}"
              f" · {oa} · {p.get('doi') or ('arXiv:' + str(p.get('arxiv_id')))}"
              f" · 초록{'O' if p.get('abstract') else 'X'}"
              f"{' · 빈 열: ' + ','.join(miss) if miss else ' · 열 전부 채움'}")
        if miss:
            empty.append((p["pid"], miss))
    if proj["questions"]:
        print("  질문 이력:")
        for q in proj["questions"]:
            print(f"    - [{q['date']}] {q['q']}" + (f" → {q['note']}" if q.get("note") else ""))
    print(f"# 빈 셀 있는 논문 {len(empty)}/{len(proj['papers'])}"
          + (" — render 전에 채우거나 `--allow-empty`" if empty else ""))


def cmd_project_log(a):
    proj = _pload(a.slug)
    proj["questions"].append({"date": time.strftime("%Y-%m-%d"), "q": a.question,
                              "note": a.note})
    _psave(a.slug, proj)
    print(f"질문 기록 ({len(proj['questions'])}건)")


def cmd_project_refs(a):
    """P# 레지스트리 → verify-batch 입력. 철회·식별자 없는 항목은 제외하고 stderr에 알린다."""
    proj = _pload(a.slug)
    refs, skipped = [], []
    # 레드팀 R5: 필터가 없어 레지스트리 전체(이전 회차 포함)만 내보낼 수 있었다 — 2회차
    # 이후 답변(등록 12편 중 5편 인용)은 §6 3중 대조(refs 항목 수 == 답변의 고유 [P#] 수)를
    # 구조적으로 만족시킬 수 없었다. --pids로 이번 답변이 인용한 P#만 뽑는다.
    want = None
    if getattr(a, "pids", None):
        want = {x.strip().upper() for x in a.pids.split(",") if x.strip()}
        known = {p["pid"] for p in proj["papers"]}
        unknown = sorted(want - known)
        if unknown:
            print(f"ERROR: 레지스트리에 없는 P#: {', '.join(unknown)} — 답변이 미등록 논문을 인용한다",
                  file=sys.stderr)
            sys.exit(2)
    for p in proj["papers"]:
        if want is not None and p["pid"] not in want:
            continue
        doi = p.get("doi")
        if not doi and p.get("arxiv_id"):
            doi = f"10.48550/arXiv.{re.sub(r'v\d+$', '', p['arxiv_id'])}"
        if not doi:
            skipped.append((p["pid"], "DOI 없음"))
            continue
        refs.append({"id": p["pid"], "doi": doi, "title": p.get("title"),
                     "author": _first_family(p.get("authors")), "year": p.get("year")})
    out = a.out or os.path.join(_pdir(a.slug), "refs.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(refs, fh, ensure_ascii=False, indent=1)
    scope = f"(--pids {len(want)}편 / 레지스트리 {len(proj['papers'])}편)" if want is not None \
        else f"(레지스트리 전체 {len(proj['papers'])}편)"
    print(f"refs.json {len(refs)}건 {scope} → {out}")
    for pid, why in skipped:
        print(f"  ⚠ {pid} 제외: {why} → §6 NO_DOI 수동 대조", file=sys.stderr)


def _cit_max(p):
    v = [x for x in (p.get("citations"), p.get("citations_s2")) if isinstance(x, (int, float))]
    return max(v) if v else None


_SORT_LABEL = {"n": None, "cit": "인용순", "year": "최신순", "yearasc": "오래된순", "title": "제목순"}


def _select_papers(proj, a):
    """render/show 공용 필터·정렬(Liner 표 툴바 재현, 2026-09-12). (rows, 조건 설명 목록) 반환.
    조건이 하나도 없으면 원본 순서 그대로."""
    rows, conds = list(proj["papers"]), []
    g = lambda k, d=None: getattr(a, k, d)
    if g("pids"):
        want = {x.strip().upper() for x in g("pids").split(",") if x.strip()}
        rows = [p for p in rows if p["pid"] in want]
        conds.append("P# " + ",".join(sorted(want, key=lambda x: int(x[1:]) if x[1:].isdigit() else 0)))
    if g("oa_only"):
        rows = [p for p in rows if p.get("is_oa")]; conds.append("오픈액세스만")
    if g("year_from"):
        rows = [p for p in rows if (p.get("year") or 0) >= int(g("year_from"))]; conds.append(f"{g('year_from')}년 이후")
    if g("year_to"):
        rows = [p for p in rows if p.get("year") and int(p["year"]) <= int(g("year_to"))]; conds.append(f"{g('year_to')}년 이전")
    if g("min_cit") is not None:
        rows = [p for p in rows if (_cit_max(p) or 0) >= int(g("min_cit"))]; conds.append(f"인용 ≥{g('min_cit')}(OpenAlex·S2 중 큰 값)")
    if g("venue"):
        rows = [p for p in rows if g("venue").lower() in (p.get("venue") or "").lower()]; conds.append(f"게재지 '{g('venue')}'")
    if g("grep"):
        t = g("grep").lower()
        def hay(p):
            return " ".join([p.get("title") or "", p.get("abstract") or "", p.get("venue") or "",
                             " ".join(x for x in (p.get("authors") or []) if x)]
                            + [str(v) for v in (p.get("cells") or {}).values()]).lower()
        rows = [p for p in rows if t in hay(p)]; conds.append(f"검색 '{g('grep')}'")
    sort = g("sort") or "n"
    n = lambda p: int(p["pid"][1:]) if p["pid"][1:].isdigit() else 0
    if sort == "cit":
        rows.sort(key=lambda p: (-(_cit_max(p) if _cit_max(p) is not None else -1), n(p)))
    elif sort == "year":
        rows.sort(key=lambda p: (-(p.get("year") or 0), n(p)))
    elif sort == "yearasc":
        rows.sort(key=lambda p: ((p.get("year") or 0), n(p)))
    elif sort == "title":
        rows.sort(key=lambda p: ((p.get("title") or "").lower(), n(p)))
    if _SORT_LABEL.get(sort):
        conds.append(_SORT_LABEL[sort])
    return rows, conds


def _apply_selection(proj, a):
    """필터가 걸리면 papers를 교체한 사본 + filter_note를 돌려준다(레지스트리 원본은 불변)."""
    rows, conds = _select_papers(proj, a)
    if not conds:
        return proj, None
    note = f"전체 {len(proj['papers'])}편 중 {len(rows)}편 — " + " · ".join(conds)
    if not rows:
        print(f"  ⚠ 조건에 맞는 논문 0편: {note}", file=sys.stderr)
    return dict(proj, papers=rows, filter_note=note), note


def _add_filter_args(x):
    x.add_argument("--pids", help="P1,P4 — 일부만")
    x.add_argument("--oa-only", action="store_true", dest="oa_only", help="오픈액세스만")
    x.add_argument("--year-from", type=int, dest="year_from"); x.add_argument("--year-to", type=int, dest="year_to")
    x.add_argument("--min-cit", type=int, dest="min_cit", help="인용수 하한(OpenAlex·S2 중 큰 값)")
    x.add_argument("--venue", help="게재지 부분일치")
    x.add_argument("--grep", help="제목·초록·저자·AI 열 부분일치")
    x.add_argument("--sort", choices=["n", "cit", "year", "yearasc", "title"], default="n",
                   help="n=P# 순(기본) · cit=인용순 · year=최신순 · yearasc=오래된순 · title=제목순")
    return x


def _md_cell(s, maxlen=None):
    s = "" if s is None else str(s)
    s = re.sub(r"\s*\n\s*", " ", s).replace("|", "\\|").strip()
    if maxlen and len(s) > maxlen:
        s = s[:maxlen - 1].rstrip() + "…"
    return s


def _cit_label(p, sep=" · "):
    """인용수 표시: OpenAlex와 S2를 병기. 둘 다 없으면 '?'."""
    oa, s2 = p.get("citations"), p.get("citations_s2")
    parts = []
    if oa is not None:
        parts.append(f"OpenAlex {oa:,}")
    if s2 is not None:
        parts.append(f"S2 {s2:,}")
    return sep.join(parts) if parts else "?"


def render_project_md(proj, cols=None, allow_empty=True, with_abstract=False):
    colsel = proj["columns"]
    if cols:
        want = [c.strip() for c in cols.split(",") if c.strip()]
        colsel = [c for c in proj["columns"] if c["key"] in want]
        missing = set(want) - {c["key"] for c in colsel}
        if missing:
            raise ValueError(f"미정의 열: {sorted(missing)}")
    lines = [f"# {proj['title']} — 논문 테이블",
             "",
             f"> 논문 {len(proj['papers'])}편 · 갱신 {proj.get('updated')} · "
             f"레지스트리 `{proj['slug']}` (P#는 DOI 기준 영구 고정). "
             "AI 열은 초록·본문 발췌 근거로 작성, `(초록)`=초록만 읽음. "
             "'이 논문이 사용된 이유'는 [해석]이며 논문 주장이 아니다.",
             ""]
    if proj.get("filter_note"):
        lines.insert(3, f"> 🔎 필터·정렬: {proj['filter_note']} (부분 표 — 전체 표는 필터 없이 다시 render)")
        lines.insert(4, "")
    head = ["P#", "논문"] + [c["label"] for c in colsel]
    lines.append("| " + " | ".join(head) + " |")
    lines.append("|" + "|".join("---" for _ in head) + "|")
    for p in proj["papers"]:
        au = _first_family(p.get("authors")) or "?"
        if len([x for x in (p.get("authors") or []) if x]) > 1:
            au += " et al."
        ident = p.get("doi")
        link = f"https://doi.org/{ident}" if ident else (
            f"https://arxiv.org/abs/{p['arxiv_id']}" if p.get("arxiv_id") else None)
        title = _md_cell(p.get("title"))
        title = f"[{title}]({link})" if link else title
        meta = (f"**{title}**<br>{au} · {_md_cell(p.get('venue')) or '?'} · {p.get('year')}"
                f" · 인용 {_cit_label(p, '/')}{' · OA' if p.get('is_oa') else ''}")
        row = [p["pid"], meta]
        for c in colsel:
            v = p["cells"].get(c["key"])
            if not v and not allow_empty:
                raise ValueError(f"{p['pid']} 열 {c['key']} 비어 있음")
            row.append(_md_cell(v) if v else "—")
        lines.append("| " + " | ".join(row) + " |")
    if with_abstract:
        lines += ["", "## 초록", ""]
        for p in proj["papers"]:
            lines += [f"### [{p['pid']}] {p.get('title')} ({p.get('year')})",
                      "", (p.get("abstract") or "(초록 미제공)"), ""]
    if proj.get("questions"):
        lines += ["", "## 질문 이력", ""]
        for q in proj["questions"]:
            lines.append(f"- [{q['date']}] {q['q']}" + (f" → [[{q['note']}]]" if q.get("note") else ""))
    return "\n".join(lines) + "\n"


def render_project_csv(proj):
    import csv
    import io as _io
    buf = _io.StringIO()
    w = csv.writer(buf)
    head, rows = _export_rows(proj)
    w.writerow(head)
    for r in rows:
        w.writerow(r)
    return buf.getvalue()

# ---------------- export — RIS · BibTeX · XLSX (Liner "내보내기" 5종 재현, 2026-09-12) ----------------
# CSV/Markdown은 위 render_project_csv/md, 여기는 서지관리기(Zotero·EndNote·Mendeley)와
# 엑셀용. 서지 필드는 레지스트리 정본(OpenAlex/arXiv 메타)에서만 뽑고 AI 열은 keywords(KW)와
# note(P#)로만 실린다 — 내보낸 파일이 [P#] 등급 근거처럼 보이지 않게.

def _split_name(name):
    """'Miles Turpin' → ('Turpin', 'Miles'). 'Turpin, Miles'는 그대로. 단일 토큰은 성만."""
    name = (name or "").strip()
    if not name:
        return None
    if "," in name:
        fam, giv = [x.strip() for x in name.split(",", 1)]
        return fam, giv
    parts = name.split()
    return (parts[-1], " ".join(parts[:-1])) if len(parts) > 1 else (parts[0], "")


def _keywords_of(p):
    kw = (p.get("cells") or {}).get("keywords") or ""
    return [k.strip() for k in re.split(r"[·;,]", kw) if k.strip()]


def _paper_kind(p):
    """article | conference | chapter | book | preprint — RIS TY·BibTeX @type 결정."""
    t = (p.get("type") or "").strip().lower()
    if t == "preprint" or (_arxiv_of(p) and not (p.get("doi") or "").lower().startswith("10.48550/") and not p.get("doi")) \
            or (p.get("doi") or "").lower().startswith("10.48550/arxiv."):
        return "preprint"
    if t in ("book-chapter", "chapter"):
        return "chapter"
    if t in ("book", "monograph", "edited-book"):
        return "book"
    if t in ("proceedings-article", "conference-paper", "paper-conference"):
        return "conference"
    return "article"


def _arxiv_of(p):
    """arxiv_id 필드 또는 arXiv DOI(10.48550/arxiv.<id>)에서 id 복원."""
    if p.get("arxiv_id"):
        return p["arxiv_id"]
    mm = re.match(r"10\.48550/arxiv\.(.+)$", (p.get("doi") or "").lower())
    return mm.group(1) if mm else None


def _venue_out(p):
    """내보내기용 게재지 — 프리프린트 서버명('arXiv (Cornell University)')은 저널이 아니므로 제외."""
    v = (p.get("venue") or "").strip()
    if not v or re.match(r"(?i)^(arxiv|biorxiv|medrxiv|ssrn|research square)\b", v):
        return ""
    return v


def _paper_url(p):
    if p.get("doi"):
        return f"https://doi.org/{p['doi']}"
    if p.get("arxiv_id"):
        return f"https://arxiv.org/abs/{p['arxiv_id']}"
    return p.get("oa_url") or ""


_RIS_TY = {"article": "JOUR", "conference": "CONF", "chapter": "CHAP", "book": "BOOK", "preprint": "UNPB"}


def render_project_ris(proj):
    """RIS(EndNote·Zotero·Mendeley 공통 가져오기 형식). 태그당 한 줄, 레코드는 ER로 닫는다."""
    out = []
    for p in proj["papers"]:
        L = [("TY", _RIS_TY[_paper_kind(p)]), ("ID", p["pid"]), ("TI", p.get("title") or "")]
        for a in p.get("authors") or []:
            sn = _split_name(a)
            if sn:
                L.append(("AU", f"{sn[0]}, {sn[1]}" if sn[1] else sn[0]))
        if p.get("year"):
            L.append(("PY", str(p["year"])))
        if _venue_out(p):
            L.append(("T2" if _paper_kind(p) in ("conference", "chapter") else "JO", _venue_out(p)))
        if p.get("doi"):
            L.append(("DO", p["doi"]))
        url = _paper_url(p)
        if url:
            L.append(("UR", url))
        if p.get("abstract"):
            L.append(("AB", re.sub(r"\s*\n\s*", " ", p["abstract"]).strip()))
        for k in _keywords_of(p):
            L.append(("KW", k))
        note = f"scholar-research {proj['slug']} {p['pid']}"
        if p.get("citations") is not None or p.get("citations_s2") is not None:
            note += f" · 인용 {_cit_label(p, '/')}"
        L.append(("N1", note))
        out.extend(f"{tag}  - {val}" for tag, val in L)
        out.append("ER  - ")
        out.append("")
    return "\n".join(out) + ("\n" if out else "")


_BIB_TYPE = {"article": "article", "conference": "inproceedings", "chapter": "incollection",
             "book": "book", "preprint": "misc"}


def _bib_escape(s):
    s = "" if s is None else str(s)
    s = re.sub(r"\s*\n\s*", " ", s).strip()
    return re.sub(r"([&%$#_])", r"\\\1", s)


def _bib_key(p, used):
    fam = _split_name((p.get("authors") or [None])[0] or "")
    fam = re.sub(r"[^A-Za-z]", "", (fam[0] if fam else "")) or ""
    word = ""
    for w in re.findall(r"[A-Za-z]{3,}", p.get("title") or ""):
        if w.lower() not in ("the", "and", "for", "with", "from", "into", "toward", "towards"):
            word = w.lower()
            break
    key = f"{fam}{p.get('year') or ''}{word}" or p["pid"]
    if not fam and not word:
        key = p["pid"]
    base, i = key, 0
    while key in used:  # 같은 1저자·연도·첫 단어 충돌 → a, b, c…
        key = base + "abcdefghijklmnopqrstuvwxyz"[i % 26] * (i // 26 + 1)
        i += 1
    used.add(key)
    return key


def render_project_bibtex(proj):
    """BibTeX. 제목은 이중 중괄호로 대소문자 보존, 특수문자(&%$#_) 이스케이프, 키 충돌은 접미사."""
    used, out = set(), []
    for p in proj["papers"]:
        kind = _paper_kind(p)
        F = [("title", "{" + _bib_escape(p.get("title")) + "}")]
        au = [a for a in (p.get("authors") or []) if a]
        if au:
            F.append(("author", " and ".join(_bib_escape(a) for a in au)))
        if p.get("year"):
            F.append(("year", str(p["year"])))
        if _venue_out(p):
            F.append(("booktitle" if kind in ("conference", "chapter") else "journal", _bib_escape(_venue_out(p))))
        if p.get("doi"):
            F.append(("doi", p["doi"]))
        aid = _arxiv_of(p)
        if aid:
            F += [("eprint", aid), ("archiveprefix", "arXiv")]
            if kind == "preprint":
                F.append(("howpublished", f"arXiv:{aid}"))
        url = _paper_url(p)
        if url:
            F.append(("url", url))
        if p.get("abstract"):
            F.append(("abstract", _bib_escape(p["abstract"])))
        kws = _keywords_of(p)
        if kws:
            F.append(("keywords", ", ".join(_bib_escape(k) for k in kws)))
        F.append(("note", f"scholar-research {proj['slug']} {p['pid']}"))
        out.append(f"@{_BIB_TYPE[kind]}{{{_bib_key(p, used)},")
        out.append(",\n".join(f"  {k} = {{{v}}}" for k, v in F))
        out.append("}\n")
    return "\n".join(out)


def _export_rows(proj):
    """CSV/XLSX 공용 표 — 서지 열 + AI 열(레지스트리 열 순서)."""
    head = ["pid", "title", "authors", "first_author", "year", "venue", "type", "citations_openalex",
            "citations_s2", "is_oa", "doi", "arxiv_id", "url", "added", "via"] + [c["key"] for c in proj["columns"]]
    rows = []
    for p in proj["papers"]:
        rows.append([p["pid"], p.get("title"), "; ".join(a for a in (p.get("authors") or []) if a),
                     _first_family(p.get("authors")), p.get("year"), p.get("venue"), p.get("type"),
                     p.get("citations"), p.get("citations_s2"), p.get("is_oa"), p.get("doi"), p.get("arxiv_id"),
                     _paper_url(p), p.get("added"), p.get("via")]
                    + [(p.get("cells") or {}).get(c["key"], "") for c in proj["columns"]])
    return head, rows


def render_project_xlsx(proj):
    """엑셀(.xlsx) — 외부 라이브러리 없이 OOXML 최소 구성(zip + inlineStr 셀). 반환은 bytes."""
    import zipfile
    import io as _io
    from xml.sax.saxutils import escape as _x
    head, rows = _export_rows(proj)

    def col(i):  # 0 → A, 26 → AA
        s = ""
        i += 1
        while i:
            i, r = divmod(i - 1, 26)
            s = chr(65 + r) + s
        return s

    def cell(ci, ri, v):
        ref = f"{col(ci)}{ri}"
        if v is None or v == "":
            return ""
        if isinstance(v, bool):
            return f'<c r="{ref}" t="b"><v>{int(v)}</v></c>'
        if isinstance(v, (int, float)):
            return f'<c r="{ref}"><v>{v}</v></c>'
        t = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(v))
        return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{_x(t)}</t></is></c>'

    lines = ['<row r="1">' + "".join(cell(i, 1, h) for i, h in enumerate(head)) + "</row>"]
    for ri, r in enumerate(rows, 2):
        lines.append(f'<row r="{ri}">' + "".join(cell(i, ri, v) for i, v in enumerate(r)) + "</row>")
    widths = "".join(f'<col min="{i+1}" max="{i+1}" width="{14 if i < 15 else 40}" customWidth="1"/>'
                     for i in range(len(head)))
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             f'<cols>{widths}</cols><sheetData>{"".join(lines)}</sheetData></worksheet>')
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
          '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
          '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>')
    wb = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
          '<sheets><sheet name="papers" sheetId="1" r:id="rId1"/></sheets></workbook>')
    wbrels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
              '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
              '</Relationships>')
    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", wbrels)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


EXPORT_FORMATS = {"md": ".md", "csv": ".csv", "html": ".html", "ris": ".ris", "bibtex": ".bib", "bib": ".bib", "xlsx": ".xlsx"}


_HTML_CSS = """
:root{--bg:#F4F6F9;--surface:#FFFFFF;--surface2:#EDF0F4;--ink:#1A2130;--ink2:#4A5468;--muted:#7A8496;--line:#D9DEE6;--accent:#C97C12;--accent-ink:#8A5208;--accent-soft:#FBEFD9;--oa:#2F7D5A;--oa-soft:#DDF0E6;--full:#7A3E9D;--full-soft:#EFE3F6;--shadow:0 1px 2px rgba(20,30,50,.06),0 6px 20px rgba(20,30,50,.06)}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0F141B;--surface:#171D26;--surface2:#1F2733;--ink:#E7EBF1;--ink2:#B4BCC9;--muted:#8792A3;--line:#2B3441;--accent:#F0A93A;--accent-ink:#F6C169;--accent-soft:#33260F;--oa:#5FC38F;--oa-soft:#123324;--full:#C89BE3;--full-soft:#2C1B38;--shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px rgba(0,0,0,.35)}}
:root[data-theme="dark"]{--bg:#0F141B;--surface:#171D26;--surface2:#1F2733;--ink:#E7EBF1;--ink2:#B4BCC9;--muted:#8792A3;--line:#2B3441;--accent:#F0A93A;--accent-ink:#F6C169;--accent-soft:#33260F;--oa:#5FC38F;--oa-soft:#123324;--full:#C89BE3;--full-soft:#2C1B38;--shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px rgba(0,0,0,.35)}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:"IBM Plex Sans",system-ui,-apple-system,"Apple SD Gothic Neo","Noto Sans KR",sans-serif;font-size:14px;line-height:1.5;-webkit-font-smoothing:antialiased}
.wrap{max-width:1480px;margin:0 auto;padding:28px 24px 64px}
header{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:16px 32px;margin-bottom:20px}
.eyebrow{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--accent-ink);font-weight:600;margin:0 0 6px}
h1{font-family:"Newsreader",Georgia,"Apple SD Gothic Neo",serif;font-weight:500;font-size:34px;line-height:1.15;margin:0;text-wrap:balance;letter-spacing:-.01em}
h1 em{font-style:italic;color:var(--accent-ink)}
.sub{color:var(--ink2);margin:8px 0 0;max-width:64ch}
.stats{display:grid;grid-auto-flow:column;border:1px solid var(--line);border-radius:10px;background:var(--surface);overflow:hidden}
.stat{padding:10px 18px;border-left:1px solid var(--line)}.stat:first-child{border-left:0}
.stat b{display:block;font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:20px;font-weight:500;font-variant-numeric:tabular-nums;line-height:1.1}
.stat span{font-size:11px;color:var(--muted);letter-spacing:.04em;text-transform:uppercase}
.verify{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:11.5px;color:var(--ink2);background:var(--surface2);border:1px solid var(--line);border-radius:8px;padding:8px 12px;margin:0 0 18px;overflow-x:auto;white-space:nowrap}
.verify b{color:var(--oa);font-weight:500}
.toolbar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:12px}
.toolbar input[type=search]{flex:1 1 260px;min-width:200px;padding:9px 12px;border:1px solid var(--line);border-radius:8px;background:var(--surface);color:var(--ink);font:inherit}
.toolbar select{padding:9px 10px;border:1px solid var(--line);border-radius:8px;background:var(--surface);color:var(--ink);font:inherit}
.chipbtn{display:inline-flex;align-items:center;gap:6px;padding:7px 12px;border:1px solid var(--line);border-radius:999px;background:var(--surface);color:var(--ink2);font:inherit;font-size:12.5px;cursor:pointer}
.chipbtn[aria-pressed=true]{border-color:var(--accent);background:var(--accent-soft);color:var(--accent-ink)}
.chipbtn:focus-visible,.toolbar :focus-visible,tr:focus-visible,thead th button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.count{margin-left:auto;color:var(--muted);font-variant-numeric:tabular-nums;font-size:12.5px}
.tablewrap{overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:var(--surface);box-shadow:var(--shadow)}
table{border-collapse:separate;border-spacing:0;width:100%;min-width:1240px}
thead th{position:sticky;top:0;z-index:2;background:var(--surface2);color:var(--ink2);font-size:11px;letter-spacing:.08em;text-transform:uppercase;text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);white-space:nowrap}
thead th button{all:unset;cursor:pointer;display:inline-flex;gap:4px;align-items:center}
tbody td{padding:12px;border-bottom:1px solid var(--line);vertical-align:top;font-size:13px;line-height:1.5}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover td{background:color-mix(in srgb,var(--surface2) 55%,transparent)}
td.pid{font-family:"IBM Plex Mono",ui-monospace,monospace;color:var(--muted);font-size:12px;width:44px;padding-right:0}
td.paper{width:300px;min-width:280px}
td.paper a{font-family:"Newsreader",Georgia,serif;font-size:16px;font-weight:500;line-height:1.3;color:var(--ink);text-decoration:none;text-wrap:pretty}
td.paper a:hover{color:var(--accent-ink);text-decoration:underline;text-underline-offset:3px}
.meta{color:var(--ink2);font-size:12px;margin-top:4px}.meta .cit{font-family:"IBM Plex Mono",ui-monospace,monospace;font-variant-numeric:tabular-nums}
.tags{display:flex;flex-wrap:wrap;gap:4px;margin-top:7px}
.tag{font-size:10.5px;letter-spacing:.04em;padding:2px 7px;border-radius:4px;font-weight:600;text-transform:uppercase}
.tag.oa{background:var(--oa-soft);color:var(--oa)}.tag.full{background:var(--full-soft);color:var(--full)}
td.reason{width:230px;color:var(--ink2)}td.kw{width:170px}
.kw span{display:inline-block;background:var(--surface2);border:1px solid var(--line);border-radius:5px;padding:1px 7px;margin:0 4px 4px 0;font-size:12px;color:var(--ink2)}
td.focus{width:230px}td.concl{min-width:300px}.concl .n{color:var(--accent-ink);font-weight:600}
details{margin-top:8px}summary{cursor:pointer;color:var(--accent-ink);font-size:12px;font-weight:500;list-style:none;display:inline-flex;gap:5px;align-items:center}
summary::-webkit-details-marker{display:none}summary::before{content:"\\25B8";font-size:10px}details[open] summary::before{content:"\\25BE"}
.abs{margin-top:8px;color:var(--ink2);font-size:12.5px;max-width:70ch;line-height:1.55}
.na{color:var(--muted);font-style:italic}
footer{margin-top:18px;color:var(--muted);font-size:12px;display:flex;flex-wrap:wrap;gap:6px 18px}
footer code{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:11.5px}
mark{background:var(--accent-soft);color:inherit;padding:0 1px;border-radius:2px}
@media (max-width:720px){h1{font-size:26px}.stats{grid-auto-flow:row;grid-template-columns:repeat(2,1fr)}.stat{border-left:0;border-top:1px solid var(--line)}.stat:nth-child(-n+2){border-top:0}}
"""

_HTML_JS = r"""
const tb=document.getElementById('tb'),q=document.getElementById('q'),sortSel=document.getElementById('sort'),count=document.getElementById('count');
const yfSel=document.getElementById('yf'),minCit=document.getElementById('mincit'),venueSel=document.getElementById('venue');
const filters={oa:false,full:false,recent:false};
const citlbl=r=>{const p=[];if(r.cit!=null)p.push('OpenAlex '+r.cit.toLocaleString());if(r.s2!=null)p.push('S2 '+r.s2.toLocaleString());return p.length?p.join(' / '):'?'};
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function hl(s,term){s=esc(s);if(!term)return s;const re=new RegExp('('+term.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+')','gi');return s.replace(re,'<mark>$1</mark>');}
function numify(s,term){return hl(s,term).replace(/(\d[\d,.~×–-]*\s?(?:%p?|배|×|kW|MW|GW|km|m|억|\$|£)?)/g,'<span class="n">$1</span>');}
function render(){
  const term=q.value.trim();
  let rows=DATA.filter(r=>{
    if(filters.oa&&!r.oa)return false;if(filters.full&&!r.full)return false;if(filters.recent&&!(r.year>=RECENT))return false;
    if(yfSel.value&&!(r.year>=+yfSel.value))return false;if(minCit.value!==''&&!((r.citmax??-1)>=+minCit.value))return false;if(venueSel.value&&r.venue!==venueSel.value)return false;
    if(!term)return true;const hay=[r.title,r.author,r.venue].concat(Object.values(r.c)).concat([r.abstract]).join(' ').toLowerCase();return hay.includes(term.toLowerCase());
  });
  const s=sortSel.value;
  rows.sort((a,b)=>s==='cit'?((b.citmax??-1)-(a.citmax??-1)):s==='year'?(b.year-a.year)||(a.n-b.n):s==='yearasc'?(a.year-b.year)||(a.n-b.n):a.n-b.n);
  count.textContent=rows.length+' / '+DATA.length+'편';
  tb.innerHTML=rows.map(r=>{
    const na=v=>v?numify(v,term):'<span class="na">—</span>';
    const tags=[r.oa?'<span class="tag oa">OA</span>':'',r.full?'<span class="tag full">본문 정독</span>':''].join('');
    const abs=r.abstract?'<details><summary>초록</summary><p class="abs">'+hl(r.abstract,term)+'</p></details>':'';
    let cells='';
    for(const c of COLS){
      if(c.key===KW_KEY){const kws=r.c[c.key]?r.c[c.key].split('·').map(k=>'<span>'+hl(k.trim(),term)+'</span>').join(''):'<span class="na">—</span>';cells+='<td class="kw">'+kws+'</td>';}
      else cells+='<td class="'+(c.key==='reason'?'reason':c.key==='focus'?'focus':'concl')+'">'+na(r.c[c.key])+'</td>';
    }
    return '<tr tabindex="0"><td class="pid">'+r.pid+'</td><td class="paper"><a href="'+esc(r.link)+'" target="_blank" rel="noopener">'+hl(r.title,term)+'</a><div class="meta">'+hl(r.author,term)+' · '+hl(r.venue,term)+' · '+r.year+' · 인용 <span class="cit">'+citlbl(r)+'</span></div><div class="tags">'+tags+'</div>'+abs+'</td>'+cells+'</tr>';
  }).join('');
}
q.addEventListener('input',render);sortSel.addEventListener('change',render);yfSel.addEventListener('change',render);minCit.addEventListener('input',render);venueSel.addEventListener('change',render);
document.querySelectorAll('.chipbtn').forEach(b=>b.addEventListener('click',()=>{const k=b.dataset.f;filters[k]=!filters[k];b.setAttribute('aria-pressed',String(filters[k]));render();}));
try{const saved=localStorage.getItem(STORE_KEY);if(saved)sortSel.value=saved;}catch(e){}
sortSel.addEventListener('change',()=>{try{localStorage.setItem(STORE_KEY,sortSel.value);}catch(e){}});
render();
"""


def render_project_html(proj, cols=None, subtitle=None, verify_line=None, full_pids=(), recent_year=None):
    """자기완결 HTML(정렬·필터·검색·초록 펼침). Artifact/브라우저용 — 마크다운 표의 가독성 보완."""
    import html as _h
    colsel = proj["columns"]
    if cols:
        want = [c.strip() for c in cols.split(",") if c.strip()]
        colsel = [c for c in proj["columns"] if c["key"] in want]
    rows = []
    for r in proj["papers"]:
        au = [a for a in (r.get("authors") or []) if a]
        fam = (_first_family(au) or "?") + (" et al." if len(au) > 1 else "")
        ident = r.get("doi")
        link = f"https://doi.org/{ident}" if ident else f"https://arxiv.org/abs/{r.get('arxiv_id')}"
        rows.append({"pid": r["pid"], "n": int(r["pid"][1:]), "title": r.get("title"), "author": fam,
                     "venue": r.get("venue") or "학회/미상", "year": r.get("year"), "cit": r.get("citations"), "s2": r.get("citations_s2"),
                     "citmax": max([x for x in (r.get("citations"), r.get("citations_s2")) if x is not None] or [None]),
                     "oa": bool(r.get("is_oa")), "link": link, "abstract": r.get("abstract"),
                     "full": r["pid"] in set(full_pids),
                     "c": {c["key"]: (r["cells"].get(c["key"]) or "") for c in colsel}})
    n = len(rows); noa = sum(r["oa"] for r in rows); ncit = sum(r["citmax"] or 0 for r in rows)
    years = [r["year"] for r in rows if r["year"]]
    recent = recent_year or (max(years) - 1 if years else 2024)
    kw_key = next((c["key"] for c in colsel if c["key"] == "keywords"), "")
    ths = "".join(f"<th>{_h.escape(c['label'])}{' <span style=\"font-weight:400;text-transform:none;letter-spacing:0\">[해석]</span>' if c['key']=='reason' else ''}</th>" for c in colsel)
    title = _h.escape(proj["title"])
    sub = _h.escape(subtitle or f"scholar-research 프로젝트 `{proj['slug']}` — 논문 {n}편. AI 열은 초록·본문 발췌를 근거로 작성했고, '이 논문이 사용된 이유' 열만 해석이다.")
    if proj.get("filter_note"):
        sub += f' <b>🔎 {_h.escape(proj["filter_note"])}</b>'
    year_opts = "".join(f'<option value="{y}">{y}년 이후</option>' for y in sorted({r["year"] for r in rows if r["year"]}, reverse=True))
    venue_opts = "".join(f'<option value="{_h.escape(v)}">{_h.escape(v)}</option>' for v in sorted({r["venue"] for r in rows if r["venue"]}))
    vline = _h.escape(verify_line) if verify_line else "인용 검증: (verify-batch 요약줄을 --verify-line으로 전달)"
    vline = vline.replace(_h.escape(f"{n}/{n} MATCH"), f"<b>{n}/{n} MATCH</b>")
    stats = f'<div class="stat"><b>{n}</b><span>논문</span></div><div class="stat"><b>{noa}</b><span>오픈액세스</span></div>' \
            f'<div class="stat"><b>{len(set(full_pids))}</b><span>본문 정독</span></div><div class="stat"><b>{ncit:,}</b><span>누적 인용(OpenAlex·S2 중 큰 값)</span></div>'
    qh = "\n".join(f"- [{x['date']}] {_h.escape(x['q'])}" + (f" → {_h.escape(x['note'])}" if x.get('note') else "") for x in proj.get("questions", []))
    return f"""<title>{title}</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;0,6..72,600;1,6..72,400&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>{_HTML_CSS}</style>
<div class="wrap">
<header><div><p class="eyebrow">scholar-research · 프로젝트 <code>{_h.escape(proj['slug'])}</code> · {proj.get('updated')}</p>
<h1>{title} <em>논문 테이블</em></h1><p class="sub">{sub}</p></div>
<div class="stats">{stats}</div></header>
<p class="verify">{vline}</p>
<div class="toolbar" role="group" aria-label="필터">
<input type="search" id="q" placeholder="제목·키워드·결론·초록 검색" aria-label="검색">
<select id="sort" aria-label="정렬"><option value="n">P# 순</option><option value="cit">인용 많은 순</option><option value="year">최신 순</option><option value="yearasc">오래된 순</option></select>
<button class="chipbtn" data-f="oa" aria-pressed="false">오픈액세스만</button>
<button class="chipbtn" data-f="full" aria-pressed="false">본문 정독만</button>
<button class="chipbtn" data-f="recent" aria-pressed="false">{recent}년 이후</button>
<select id="yf" aria-label="발행 연도"><option value="">연도 전체</option>{year_opts}</select>
<input type="number" id="mincit" min="0" placeholder="인용 ≥" aria-label="인용수 하한" style="width:92px;padding:9px 10px;border:1px solid var(--line);border-radius:8px;background:var(--surface);color:var(--ink);font:inherit">
<select id="venue" aria-label="게재지"><option value="">게재지 전체</option>{venue_opts}</select>
<span class="count" id="count"></span></div>
<div class="tablewrap"><table><thead><tr><th>P#</th><th>논문</th>{ths}</tr></thead><tbody id="tb"></tbody></table></div>
<footer><span>레지스트리 <code>{_h.escape(_pdir(proj['slug']))}/project.json</code></span><span><code>(초록)</code> = 초록만 읽음 · <code>—</code> = 미기재</span></footer>
{('<details style="margin-top:14px"><summary>질문 이력</summary><pre class="abs">' + qh + '</pre></details>') if qh else ''}
</div>
<script>
const DATA={json.dumps(rows, ensure_ascii=False)};
const COLS={json.dumps([{"key": c["key"], "label": c["label"]} for c in colsel], ensure_ascii=False)};
const KW_KEY={json.dumps(kw_key)};const RECENT={recent};const STORE_KEY={json.dumps('scholar-sort-' + proj['slug'])};
{_HTML_JS}
</script>
"""


def cmd_project_render(a):
    proj = _pload(a.slug)
    total = len(proj["papers"])
    proj, _note = _apply_selection(proj, a)
    fmt = "bibtex" if a.format == "bib" else a.format
    try:
        if fmt == "csv":
            text = render_project_csv(proj)
        elif fmt == "ris":
            text = render_project_ris(proj)
        elif fmt == "bibtex":
            text = render_project_bibtex(proj)
        elif fmt == "xlsx":
            text = render_project_xlsx(proj)
        elif fmt == "html":
            text = render_project_html(proj, cols=a.cols, subtitle=a.subtitle, verify_line=a.verify_line,
                                       full_pids=[x.strip().upper() for x in (a.full or "").split(",") if x.strip()])
        else:
            text = render_project_md(proj, cols=a.cols, allow_empty=a.allow_empty,
                                     with_abstract=a.abstract)
    except ValueError as e:
        print(f"ERROR: {e} — 채우거나 --allow-empty", file=sys.stderr)
        sys.exit(3)
    if isinstance(text, bytes) and not a.out:
        print("ERROR: xlsx는 바이너리 — --out <파일.xlsx> 필요", file=sys.stderr)
        sys.exit(2)
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        ext = EXPORT_FORMATS.get(fmt)
        if ext and not a.out.lower().endswith(ext):
            print(f"  ⚠ 확장자 불일치: {a.out} (권장 {ext})", file=sys.stderr)
        if isinstance(text, bytes):
            with open(a.out, "wb") as fh:
                fh.write(text)
        else:
            with open(a.out, "w", encoding="utf-8") as fh:
                fh.write(text)
        print(f"렌더 → {a.out} ({len(proj['papers'])}편{'/전체 ' + str(total) if _note else ''}, {fmt})")
    else:
        sys.stdout.write(text)


def cmd_project_list(a):
    if not os.path.isdir(SCHOLAR_HOME):
        print(f"(프로젝트 없음: {SCHOLAR_HOME})")
        return
    for slug in sorted(os.listdir(SCHOLAR_HOME)):
        f = os.path.join(SCHOLAR_HOME, slug, "project.json")
        if os.path.exists(f):
            with open(f, encoding="utf-8") as fh:
                pj = json.load(fh)
            print(f"{slug:<30} {pj.get('title')} · {len(pj.get('papers', []))}편 · "
                  f"질문 {len(pj.get('questions', []))} · 갱신 {pj.get('updated')}")


_NC_PARENT = argparse.ArgumentParser(add_help=False)
_NC_PARENT.add_argument("--no-cache", action="store_true", dest="no_cache",
                        help="sqlite 메타 캐시를 쓰지 않는다(SCHOLAR_NO_CACHE=1과 동일)")


def add_project_parsers(sub):
    pr = sub.add_parser("project", help="논문 테이블 프로젝트(P# 레지스트리·AI 열·렌더)")
    ps = pr.add_subparsers(dest="pcmd", required=True)

    x = ps.add_parser("init"); x.add_argument("slug"); x.add_argument("--title")
    x.set_defaults(fn=cmd_project_init)

    x = ps.add_parser("list"); x.set_defaults(fn=cmd_project_list)

    x = ps.add_parser("add", parents=[_NC_PARENT]); x.add_argument("slug")
    x.add_argument("ids", nargs="*", help="DOI 또는 arXiv id/URL")
    x.add_argument("--from", dest="from_json", help="search --json 출력 파일")
    x.add_argument("--pick", help="--from과 함께: P1,P3,P7 (검색 출력 번호 기준)")
    x.add_argument("--no-fetch", action="store_true", help="메타 재조회 없이 검색 행 그대로 등록")
    x.add_argument("--refresh", action="store_true", help="이미 있는 논문이면 메타를 새로 덮어씀(셀 유지)")
    x.add_argument("--no-s2", action="store_true", help="Semantic Scholar 인용수 조회 생략")
    x.set_defaults(fn=cmd_project_add)

    x = ps.add_parser("recount", parents=[_NC_PARENT], help="등록 논문 인용수만 재조회(OpenAlex+S2, 셀·초록 보존)")
    x.add_argument("slug"); x.add_argument("--pids", help="P1,P4 — 일부만")
    x.add_argument("--s2-only", action="store_true", help="OpenAlex 재조회 생략")
    x.set_defaults(fn=cmd_project_recount)

    x = ps.add_parser("export-db", help="아티팩트 앱 db용 JSON(projects/<slug> 문서)")
    x.add_argument("slug"); x.add_argument("--out"); x.add_argument("--full", help="본문 정독 P# 목록(영구 저장)")
    x.add_argument("--verify-line"); x.add_argument("--subtitle")
    x.set_defaults(fn=cmd_project_export_db)

    x = ps.add_parser("import-db", help="아티팩트 db 문서(JSON)를 레지스트리에 병합(셀·열·질문만)")
    x.add_argument("slug"); x.add_argument("file")
    x.set_defaults(fn=cmd_project_import_db)

    x = ps.add_parser("rm"); x.add_argument("slug"); x.add_argument("pid")
    x.set_defaults(fn=cmd_project_rm)

    x = ps.add_parser("col"); x.add_argument("slug")
    x.add_argument("action", choices=["list", "add", "rm"])
    x.add_argument("label", nargs="?", help="add: 열 라벨 / rm: 열 키")
    x.add_argument("--key"); x.add_argument("--prompt", help="이 열을 채울 때의 지시문")
    x.set_defaults(fn=lambda a: cmd_project_col(_col_norm(a)))

    x = ps.add_parser("set"); x.add_argument("slug")
    x.add_argument("pid", nargs="?"); x.add_argument("key", nargs="?"); x.add_argument("value", nargs="?")
    x.add_argument("--from", dest="from_json", help='[{"pid","key","value"}] 일괄')
    x.set_defaults(fn=cmd_project_set)

    x = ps.add_parser("show"); x.add_argument("slug"); _add_filter_args(x); x.set_defaults(fn=cmd_project_show)

    x = ps.add_parser("log"); x.add_argument("slug"); x.add_argument("question")
    x.add_argument("--note", help="답변을 파일링한 vault 노트명")
    x.set_defaults(fn=cmd_project_log)

    x = ps.add_parser("refs"); x.add_argument("slug"); x.add_argument("--out")
    x.add_argument("--pids", help="P1,P4 — 이번 답변이 인용한 P#만(§6 3중 대조용). 기본은 레지스트리 전체")
    x.set_defaults(fn=cmd_project_refs)

    x = ps.add_parser("render"); x.add_argument("slug"); _add_filter_args(x)
    x.add_argument("--out"); x.add_argument("--format", choices=["md", "csv", "html", "ris", "bibtex", "bib", "xlsx"], default="md",
                   help="md 표 · csv/xlsx 스프레드시트 · html 자기완결 페이지 · ris/bibtex 서지관리기(Zotero·EndNote·Mendeley)")
    x.add_argument("--subtitle", help="html: 머리말 한 문장"); x.add_argument("--verify-line", dest="verify_line", help="html: verify-batch 요약줄 그대로")
    x.add_argument("--full", help="html: 본문 정독한 P# 목록(쉼표)")
    x.add_argument("--cols", help="렌더할 AI 열 키(쉼표) — 기본 전부")
    x.add_argument("--abstract", action="store_true", help="초록 절 포함")
    x.add_argument("--allow-empty", action="store_true", help="빈 셀 허용(기본: exit 3)")
    x.set_defaults(fn=cmd_project_render)


def _col_norm(a):
    if a.action == "rm":
        a.key = a.key or a.label
    return a




# ---------------- review — 피어 리뷰 5렌즈 "패킷" (Liner peer-review 에이전트 재현, 2026-09-12) ----------------
# 판정은 Claude가 references/peer-review-rubric.md로 한다. 이 명령은 그 전에 초안을 기계적으로 전수
# 검사해 재료를 만든다: 섹션 유무 · [P#] 실재(레지스트리 대조) · DOI 목록 · 근거 없는 수치 문장 ·
# 과장어. "리뷰어가 개수를 채우려고 지어내는 finding"을 막는 장치 — 패킷에 없는 수치·인용 문제는
# 초안 대조 없이는 finding이 될 수 없다.

_REVIEW_SECTIONS = [
    ("abstract", "요약/초록", r"abstract|summary|요약|초록|tl;?dr"),
    ("intro", "질문/배경", r"introduction|background|motivation|research question|배경|서론|질문|문제"),
    ("method", "방법/데이터", r"method|approach|materials|data|procedure|search strateg|방법|데이터|검색전략|절차"),
    ("results", "결과/답", r"result|finding|evidence|결과|발견|답변|답\b|근거"),
    ("discussion", "논의/함의", r"discussion|implication|interpretation|논의|해석|함의|시사점"),
    ("limitations", "한계", r"limitation|threats? to validity|caveat|한계|제약|주의"),
    ("conclusion", "결론", r"conclusion|concluding|takeaway|결론|정리|판정"),
    ("references", "참고문헌", r"reference|bibliograph|citation|참고문헌|출처|근거 논문|인용"),
]
_OVERCLAIM = ["proves", "proven", "clearly", "undoubtedly", "obviously", "definitively", "certainly", "unquestionably",
              "always", "never", "입증", "증명", "명백", "확실", "분명히", "반드시", "항상", "절대", "의심의 여지"]
_CITE_RE = re.compile(r"\[P\d+\]|\b10\.\d{4,9}/[^\s\])>]+|arXiv:\s?\d{4}\.\d{4,5}(?:v\d+)?|\([A-Z][A-Za-z\-]+(?: et al\.)?,? ?\d{4}[a-z]?\)|\[\d{1,3}(?:[,–-]\d{1,3})*\]|\[\[[^\]]+\]\]|https?://\S+")
_NUM_RE = re.compile(r"(?<![\w/.-])(?!(?:19|20)\d{2}(?![.,]?\d))(?!\d{4}\.\d{4,5}\b)\d+(?:[.,]\d+)*\s?(?:%p?|배|x|×|pp|점|편|건|명|억|조|만|k|M|B|GW|MW|kW|TWh|USD|\$|원|bp|σ)?(?![\w/-])")
_PID_RE = re.compile(r"\[(P\d+)\]")
_DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\])>,;]+")


def _review_sentences(text):
    """프론트매터·코드블록·표·제목을 뺀 본문을 문장 단위로. (문장, 줄번호) 목록."""
    lines = text.split("\n")
    body, in_code, i = [], False, 0
    if lines and lines[0].strip() == "---":  # frontmatter
        for j in range(1, len(lines)):
            if lines[j].strip() == "---":
                i = j + 1
                break
    for ln in range(i, len(lines)):
        t = lines[ln]
        if t.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code or not t.strip() or t.lstrip().startswith(("#", "|", ">")) or re.match(r"^\s*[-*]\s*\[[ x]\]", t):
            continue
        body.append((ln + 1, t.strip()))
    out = []
    for ln, t in body:
        for sent in re.split(r"(?<=[.!?。])\s+(?=[A-Z가-힣\[(\"'0-9])", t):
            sent = sent.strip()
            if len(sent) >= 8:
                out.append((sent, ln))
    return out


def review_packet(text, proj=None, title=None):
    """초안 텍스트 → 기계 검사 dict. proj가 있으면 [P#]를 레지스트리와 대조."""
    heads = [(m.group(2).strip(), text[:m.start()].count("\n") + 1)
             for m in re.finditer(r"^(#{1,6})\s+(.+?)\s*$", text, re.M)]
    sec = {}
    for key, label, pat in _REVIEW_SECTIONS:
        hit = next((h for h, _ in heads if re.search(pat, h, re.I)), None)
        sec[key] = {"label": label, "found": hit}
    if not heads:  # 제목 없는 짧은 노트 — 굵은 소제목·라벨로 대체 탐지
        for key, label, pat in _REVIEW_SECTIONS:
            m = re.search(r"(?m)^\s*(?:\*\*|__)?([^\n]{0,30}?(?:" + pat + r")[^\n]{0,30}?)(?:\*\*|__)?\s*[:：]", text, re.I)
            if m:
                sec[key]["found"] = m.group(1).strip()
    sents = _review_sentences(text)
    words = len(re.findall(r"[A-Za-z0-9]+|[가-힣]+", text))
    pids_used = sorted({m for m in _PID_RE.findall(text)}, key=lambda x: int(x[1:]))
    reg = {p["pid"] for p in proj["papers"]} if proj else None
    missing = [p for p in pids_used if reg is not None and p not in reg]
    dois = sorted({d.rstrip(".,;:") for d in _DOI_RE.findall(text)})
    uncited, over = [], []
    for sent, ln in sents:
        nums = [n.strip() for n in _NUM_RE.findall(sent) if n.strip()]
        if nums and not _CITE_RE.search(sent):
            uncited.append({"line": ln, "numbers": nums[:6], "sentence": sent[:160]})
        low = sent.lower()
        hits = [w for w in _OVERCLAIM if w in low]
        if hits:
            over.append({"line": ln, "words": hits, "sentence": sent[:160], "cited": bool(_CITE_RE.search(sent))})
    tag_sents = sum(1 for s_, _ in sents if _CITE_RE.search(s_))
    interp = len(re.findall(r"\[(?:해석|inferred|B)\]", text))
    fact = len(re.findall(r"\[(?:extracted|검증|A)\]", text))
    return {
        "title": title, "words": words, "sentences": len(sents), "cited_sentences": tag_sents,
        "sections": sec, "headings": [h for h, _ in heads],
        "pids_used": pids_used, "pids_missing": missing, "registry": (proj or {}).get("slug"),
        "dois": dois, "uncited_numeric": uncited, "overclaims": over,
        "tags": {"interp": interp, "fact": fact},
    }


def render_review_packet(pk):
    """리뷰 패킷 마크다운 — 렌즈 5개 골격 + 기계 검사 재료 + Claude가 채울 finding 표."""
    sec = pk["sections"]
    missing_sec = [v["label"] for v in sec.values() if not v["found"]]
    found_n = sum(1 for v in sec.values() if v["found"])
    summ = (f"review 패킷: {pk['words']:,}단어 · 문장 {pk['sentences']}(인용 포함 {pk['cited_sentences']}) · "
            f"섹션 {found_n}/{len(sec)}" + (f"(누락: {', '.join(missing_sec)})" if missing_sec else "") +
            f" · [P#] {len(pk['pids_used'])}개" + (f"(미등록 {len(pk['pids_missing'])}: {', '.join(pk['pids_missing'])})" if pk["pids_missing"] else
                                             ("" if pk["registry"] else "(레지스트리 미대조)")) +
            f" · DOI {len(pk['dois'])} · 근거 없는 수치 문장 {len(pk['uncited_numeric'])} · 과장어 {len(pk['overclaims'])}")
    L = [f"# 피어 리뷰 패킷 — {pk.get('title') or '(제목 없음)'}", "", f"> {summ}", "",
         "> 이 파일은 **기계 검사 재료**다. 판정은 `references/peer-review-rubric.md`의 렌즈 5개·finding 자격 요건으로 한다. "
         "아래 항목을 초안 위치와 대조하지 않고 그대로 finding으로 옮기지 않는다.", ""]
    L += ["## 렌즈 1 · Novelty (참신성)", "- 기여 문장(N1)·선행 대비(N2): 패킷은 검사하지 않는다 — 초안에서 '무엇이 새로운가' 문장을 찾아 인용할 것.",
          f"- 프로젝트 표 대조(N3): {'레지스트리 ' + pk['registry'] + '의 핵심 결론 열과 초안 결론을 대조' if pk['registry'] else '--project 미지정 — 대조 불가(Minor 이상 판정 시 반드시 표를 열어 확인)'}",
          "", "| # | 등급 | 규칙 | 위치 | 문제 | 수정 |", "|---|---|---|---|---|---|", ""]
    L += ["## 렌즈 2 · Rigour (엄밀성)"]
    if pk["pids_missing"]:
        L.append(f"- ⚠ R2 **레지스트리에 없는 [P#]**: {', '.join(pk['pids_missing'])} — 환각 인용 경로, 초안 대조 후 Major 후보")
    elif pk["registry"]:
        L.append(f"- R2 [P#] {len(pk['pids_used'])}개 전부 레지스트리 `{pk['registry']}`에 존재 — 내용 정합(핵심 결론 열 ↔ 초안 인용문)은 사람이 대조")
    else:
        L.append("- R2 [P#] 레지스트리 미대조(--project 없음)")
    if pk["dois"]:
        L.append(f"- R2 본문 DOI {len(pk['dois'])}건 → `verify-batch`로 서지 검증 후 인용(미검증 DOI 인용은 Major): " + ", ".join(pk["dois"][:8]) + (" …" if len(pk["dois"]) > 8 else ""))
    if pk["uncited_numeric"]:
        L.append(f"- R1 **근거 표시 없는 수치 문장 {len(pk['uncited_numeric'])}건** (결론을 지탱하면 Major, 보조면 Minor — 문장마다 판단):")
        for u in pk["uncited_numeric"][:25]:
            L.append(f"  - L{u['line']} `{' · '.join(u['numbers'])}` — {u['sentence']}")
        if len(pk["uncited_numeric"]) > 25:
            L.append(f"  - … 외 {len(pk['uncited_numeric']) - 25}건(--json으로 전체)")
    else:
        L.append("- R1 수치 문장은 전부 인용 표시([P#]·DOI·저자연도·URL) 동반 — 매핑 정합은 사람이 대조")
    if pk["overclaims"]:
        L.append(f"- R6 과장어 {len(pk['overclaims'])}건 (근거 등급을 넘는 문장만 finding):")
        for o in pk["overclaims"][:15]:
            L.append(f"  - L{o['line']} [{', '.join(o['words'])}]{' (인용 있음)' if o['cited'] else ' (인용 없음)'} — {o['sentence']}")
    L += ["", "| # | 등급 | 규칙 | 위치 | 문제 | 수정 |", "|---|---|---|---|---|---|", ""]
    L += ["## 렌즈 3 · Clarity (명료성)",
          "- C1 섹션: " + " · ".join(f"{v['label']}={'✓ ' + v['found'] if v['found'] else '✗'}" for v in sec.values()),
          f"- C3 출처태그: [해석]/[inferred] {pk['tags']['interp']}회 · [extracted]/[검증] {pk['tags']['fact']}회 — 0회면 사실·해석 분리가 안 된 것(초안 종류에 따라 C3 판단)",
          "", "| # | 등급 | 규칙 | 위치 | 문제 | 수정 |", "|---|---|---|---|---|---|", ""]
    L += ["## 렌즈 4 · Impact (영향)",
          f"- I1 함의 절: {'✓ ' + sec['discussion']['found'] if sec['discussion']['found'] else '✗ 논의/함의 제목 없음 — 본문에 함의 문장이 있는지 확인 후 판정'}",
          "- I4 반증 조건(counter): 패킷은 검사하지 않는다 — '이 결론이 틀렸다면' 문장을 찾을 것.",
          "", "| # | 등급 | 규칙 | 위치 | 문제 | 수정 |", "|---|---|---|---|---|---|", ""]
    L += ["## 렌즈 5 · Limitation (한계)",
          f"- L1 한계 절: {'✓ ' + sec['limitations']['found'] if sec['limitations']['found'] else '✗ 없음 — Major 후보(본문에 한계 문단이 흩어져 있으면 Minor로 강등 가능)'}",
          "- L2 실질 한계: 리콜 한계·(초록) 비율·색인 지연·연도 불일치 중 해당 항목이 적혔는지 초안에서 확인.",
          "", "| # | 등급 | 규칙 | 위치 | 문제 | 수정 |", "|---|---|---|---|---|---|", ""]
    L += ["## 판정", "- Major __ · Minor __ → **{Accept | Minor revision | Major revision}**", "- 렌즈별 총평 5줄", "- 다음 단계(Major부터)", ""]
    return "\n".join(L), summ


def cmd_review(a):
    with open(a.file, encoding="utf-8") as fh:
        text = fh.read()
    proj = _pload(a.project) if getattr(a, "project", None) else None
    title = None
    m = re.search(r"^#\s+(.+)$", text, re.M)
    if m:
        title = m.group(1).strip()
    else:
        m = re.search(r"^title:\s*[\"']?(.+?)[\"']?\s*$", text, re.M)
        title = m.group(1) if m else os.path.basename(a.file)
    pk = review_packet(text, proj, title)
    if getattr(a, "json", False):
        print(json.dumps(pk, ensure_ascii=False, indent=1))
        return
    md_, summ = render_review_packet(pk)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(md_)
        print(summ + f"\n패킷 → {a.out}")
    else:
        sys.stdout.write(md_)
    if pk["pids_missing"]:
        print(f"⚠ 레지스트리에 없는 [P#] {len(pk['pids_missing'])}개: {', '.join(pk['pids_missing'])}", file=sys.stderr)
        sys.exit(1)


# ---------------- graph — 인용 그래프 (Liner "연구 흐름 탐색"/citation graph 재현, 2026-09-12) ----------------
# 프로젝트 논문끼리 누가 누구를 인용하는지(내부 간선) + 프로젝트 논문 2편 이상이 공통으로 참조하는
# 외부 문헌(허브 = 표에 빠진 핵심 선행연구 후보)을 OpenAlex referenced_works로 만든다.
# 출력은 mermaid(아티팩트·Obsidian이 그대로 렌더) / json / html(자기완결, <pre class="mermaid">).

def _oa_works_by_doi(dois, select="id,doi,display_name,publication_year,cited_by_count,referenced_works"):
    """OpenAlex 배치 조회(doi 필터 | 결합, 50개 단위). {norm_doi: work}"""
    out = {}
    dois = [d for d in dois if d]
    for i in range(0, len(dois), 50):
        chunk = dois[i:i + 50]
        url = ("https://api.openalex.org/works?" + urllib.parse.urlencode(
            {"filter": "doi:" + "|".join(chunk), "per-page": "50", "select": select, "mailto": EMAIL}))
        body, code = http_get(url)
        if not body:
            print(f"  ⚠ OpenAlex 배치 실패 HTTP {code} ({len(chunk)}건)", file=sys.stderr)
            continue
        for w in json.loads(body).get("results", []):
            d = norm_doi(w.get("doi"))
            if d:
                out[d.lower()] = w
    return out


def _oa_works_by_id(wids, select="id,doi,display_name,publication_year,cited_by_count"):
    out = {}
    wids = [w.rsplit("/", 1)[-1] for w in wids if w]
    for i in range(0, len(wids), 50):
        chunk = wids[i:i + 50]
        url = ("https://api.openalex.org/works?" + urllib.parse.urlencode(
            {"filter": "openalex_id:" + "|".join(chunk), "per-page": "50", "select": select, "mailto": EMAIL}))
        body, code = http_get(url)
        if not body:
            print(f"  ⚠ OpenAlex 허브 메타 실패 HTTP {code}", file=sys.stderr)
            continue
        for w in json.loads(body).get("results", []):
            out[w["id"].rsplit("/", 1)[-1]] = w
    return out


def _authoritative_title(doi):
    """OpenAlex 제목은 arXiv DOI 레코드에서 통째로 오염된 사례가 있다(실측 2026-09-12: W4221143046 =
    Wei 2022 CoT 논문인데 display_name이 'BNAI, NO-TOKEN…'). 허브 제목은 arXiv API(arXiv DOI) 또는
    Crossref(그 외)로 재확인한다. → (title|None, source|오류)"""
    mm = re.match(r"(?i)^10\.48550/arxiv\.(.+)$", doi or "")
    if mm:
        gap = 3.0 - (time.time() - _ARXIV_LAST[0])
        if gap > 0:
            time.sleep(gap)
        body, code = http_get("https://export.arxiv.org/api/query?" + urllib.parse.urlencode({"id_list": mm.group(1)}))
        _ARXIV_LAST[0] = time.time()
        rows = _arxiv_parse(body) if body else []
        return (rows[0].get("title"), "arxiv") if rows and rows[0].get("title") else (None, f"arxiv {code if not body else '항목 없음'}")
    body, code = http_get(f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='/')}?" + urllib.parse.urlencode({"mailto": EMAIL}))
    if not body:
        return None, f"crossref {code}"
    t = ((json.loads(body).get("message") or {}).get("title") or [None])[0]
    return (_clean_title(t), "crossref") if t else (None, "crossref 제목 없음")


def _datacite_title(doi):
    """DataCite 정본 제목(arXiv·Zenodo 등록 DOI). → (title|None, source|오류). verify와 같은
    캐시 키(verify_datacite)를 쓴다 — DOI의 등록기관은 바뀌지 않는다."""
    hit = cache_get("verify_datacite", doi)
    if not hit:
        body, code = http_get(f"https://api.datacite.org/dois/{urllib.parse.quote(doi, safe='/')}")
        if not body:
            return None, f"datacite {code}"
        try:
            at = (json.loads(body).get("data") or {}).get("attributes") or {}
        except ValueError:
            return None, "datacite 파싱 실패"
        if not at:
            return None, "datacite 항목 없음"
        hit = {"titles": at.get("titles") or [], "publicationYear": at.get("publicationYear"),
               "creators": (at.get("creators") or [])[:1]}
        cache_put("verify_datacite", doi, hit)
    for t in (hit.get("titles") or []):
        tt = (t or {}).get("title")
        if tt:
            return tt, "datacite"
    return None, "datacite 제목 없음"


def _paper_lookup_key(p):
    """OpenAlex doi 필터용 키 — DOI 없는 arXiv 논문은 arXiv DOI(10.48550/arxiv.<id>)로 조회된다."""
    if p.get("doi"):
        return p["doi"].lower()
    aid = _arxiv_of(p)
    return f"10.48550/arxiv.{aid}".lower() if aid else None


def build_citation_graph(proj, papers=None, min_shared=2, hub_limit=10, check=True):
    """→ {"nodes":[{pid,title,year,cit,wid,doi}], "edges":[(src_pid,dst_pid)], "hubs":[...], "unresolved":[pid],
    "merged":[{"pid","title","cited_by"}]}. check=True면 허브 제목을 arXiv/Crossref로 재확인하고, 표 안 논문의
    다른 OpenAlex 레코드(arXiv판 vs 학회판)인 허브는 그 P#로 병합한다."""
    papers = papers if papers is not None else proj["papers"]
    keys = {p["pid"]: _paper_lookup_key(p) for p in papers}
    by_doi = _oa_works_by_doi([k for k in keys.values() if k])
    nodes, unresolved, wid2pid, refs = [], [], {}, {}
    for p in papers:
        w = by_doi.get(keys[p["pid"]] or "")
        if not w:
            unresolved.append(p["pid"])
            nodes.append({"pid": p["pid"], "title": p.get("title"), "year": p.get("year"), "cit": _cit_max(p),
                          "wid": None, "doi": p.get("doi")})
            continue
        wid = w["id"].rsplit("/", 1)[-1]
        wid2pid[wid] = p["pid"]
        refs[p["pid"]] = [r.rsplit("/", 1)[-1] for r in (w.get("referenced_works") or [])]
        nodes.append({"pid": p["pid"], "title": p.get("title"), "year": p.get("year") or w.get("publication_year"),
                      "cit": _cit_max(p) if _cit_max(p) is not None else w.get("cited_by_count"), "wid": wid, "doi": p.get("doi")})
    edges = []
    for src, rl in refs.items():
        for r in rl:
            if r in wid2pid and wid2pid[r] != src:
                edges.append((src, wid2pid[r]))
    # 공통 참조 허브: 프로젝트 밖 문헌 중 min_shared편 이상이 인용
    cnt = {}
    for src, rl in refs.items():
        for r in set(rl):
            if r not in wid2pid:
                cnt.setdefault(r, set()).add(src)
    hub_ids = sorted([w for w, s_ in cnt.items() if len(s_) >= min_shared], key=lambda w: -len(cnt[w]))[:max(hub_limit * 3, 30)]
    hubs, merged = [], []
    if hub_ids:
        meta = _oa_works_by_id(hub_ids)
        for w in hub_ids:
            mw = meta.get(w, {})
            hubs.append({"wid": w, "title": mw.get("display_name"), "year": mw.get("publication_year"),
                         "cit": mw.get("cited_by_count"), "doi": norm_doi(mw.get("doi")),
                         "cited_by": sorted(cnt[w], key=lambda x: int(x[1:]) if x[1:].isdigit() else 0)})
        hubs.sort(key=lambda h: (-len(h["cited_by"]), -(h["cit"] or 0)))
        hubs = hubs[:hub_limit]
        # 허브 제목 재확인 + 표 안 논문의 별도 레코드 병합
        aid2pid = {(_arxiv_of(p) or "").lower(): p["pid"] for p in papers if _arxiv_of(p)}
        kept = []
        for h in hubs:
            if check and h["doi"]:
                t, src = _authoritative_title(h["doi"])
                if t and h["title"] and not _title_match(t, h["title"])[0]:
                    h["title_oa"], h["title"], h["title_source"] = h["title"], t, src
                    h["flag"] = f"OpenAlex 제목 오염 의심 → {src} 제목으로 교체"
                elif t and not h["title"]:
                    h["title"], h["title_source"] = t, src
                elif not t:
                    h["flag"] = f"DOI 재확인 실패({src}) — OpenAlex 레코드 의심, 등록 전 확인"
            aid = (re.match(r"(?i)^10\.48550/arxiv\.(.+)$", h["doi"] or "") or [None, ""])[1] if h["doi"] else ""
            pid = aid2pid.get(aid.lower()) if aid else None
            if not pid and h.get("title") and not h.get("flag", "").startswith("DOI 재확인 실패"):
                pid = next((p["pid"] for p in papers if p.get("title") and _title_match(h["title"], p["title"])[0]), None)
            if pid:
                for src_ in h["cited_by"]:
                    if src_ != pid and (src_, pid) not in edges:
                        edges.append((src_, pid))
                merged.append({"pid": pid, "wid": h["wid"], "title": h["title"], "cited_by": [x for x in h["cited_by"] if x != pid]})
            else:
                kept.append(h)
        hubs = kept
    deg = {n["pid"]: 0 for n in nodes}
    for a_, b_ in edges:
        deg[a_] += 1; deg[b_] += 1
    return {"slug": proj["slug"], "title": proj["title"], "nodes": nodes, "edges": edges, "hubs": hubs,
            "unresolved": unresolved, "merged": merged,
            "isolated": [n["pid"] for n in nodes if deg[n["pid"]] == 0 and n["wid"]]}


def _short(t, n=48):
    t = re.sub(r"\s+", " ", t or "").strip()
    return (t[:n - 1].rstrip() + "…") if len(t) > n else t


def render_graph_mermaid(g, direction="LR", with_hubs=True):
    q = lambda s_: str(s_).replace('"', "'").replace("[", "(").replace("]", ")")
    L = [f"graph {direction}"]
    years = sorted({n["year"] for n in g["nodes"] if n["year"]})
    for n in g["nodes"]:
        L.append(f'  {n["pid"]}["{n["pid"]} · {n["year"] or "?"}<br/>{q(_short(n["title"]))}"]')
    for a_, b_ in g["edges"]:
        L.append(f"  {a_} --> {b_}")
    if with_hubs:
        for i, h in enumerate(g["hubs"], 1):
            L.append(f'  H{i}(["H{i} · {h["year"] or "?"} · 인용 {h["cit"] or "?"}<br/>{q(_short(h["title"]))}"])')
            for src in h["cited_by"]:
                L.append(f"  {src} -.-> H{i}")
    if years:
        lo, hi = years[0], years[-1]
        for n in g["nodes"]:
            if n["year"] and hi > lo:
                # mermaid style는 쉼표가 속성 구분자라 hsl(…) 불가 — hex로(실측 2026-09-12 Syntax error)
                t = (n["year"] - lo) / (hi - lo)
                r_, g_, b_ = int(224 - 160 * t), int(234 - 140 * t), int(248 - 90 * t)
                L.append(f"  style {n['pid']} fill:#{r_:02x}{g_:02x}{b_:02x},color:{'#111' if t < 0.5 else '#fff'}")
    if with_hubs and g["hubs"]:
        L.append("  classDef hub fill:#fff3d6,stroke:#d08a00,color:#4a3000,stroke-dasharray:4 2")
        L.append("  class " + ",".join(f"H{i}" for i in range(1, len(g["hubs"]) + 1)) + " hub")
    return "\n".join(L) + "\n"


def render_graph_md(g):
    n, e = len(g["nodes"]), len(g["edges"])
    L = [f"# {g['title']} — 인용 그래프", "",
         f"> 논문 {n}편 · 내부 인용 간선 {e} · 공통 참조 허브 {len(g['hubs'])} · 고립(내부 간선 0) {len(g['isolated'])}편"
         + (f" · OpenAlex 미해결 {len(g['unresolved'])}편({', '.join(g['unresolved'])})" if g["unresolved"] else "")
         + f" · 소스 OpenAlex referenced_works · 조회 {time.strftime('%Y-%m-%d')}", "",
         "> 실선 = 표 안 논문끼리의 인용(→ 인용한 쪽에서 인용된 쪽으로). 점선 = 표에 없는 공통 참조 문헌(H#) — 2편 이상이 함께 인용한 선행연구로, "
         "**표에 빠진 핵심 문헌 후보**다(`project add <doi>`로 등록 검토). 색이 진할수록 최신.", "",
         "```mermaid", render_graph_mermaid(g).rstrip(), "```", ""]
    if g["hubs"]:
        L += ["## 공통 참조 허브 (표 밖)", "", "| H# | 문헌 | 연도 | 인용수(OpenAlex) | 인용한 P# | DOI |", "|---|---|---|---|---|---|"]
        for i, h in enumerate(g["hubs"], 1):
            L.append(f"| H{i} | {_md_cell(h['title']) or '(제목 없음 — OpenAlex ' + h['wid'] + ')'}{' ⚠ ' + h['flag'] if h.get('flag') else ''} | {h['year'] or '?'} | {h['cit'] if h['cit'] is not None else '?'} | "
                     f"{', '.join(h['cited_by'])} | {h['doi'] or '—'} |")
        L.append("")
    if g.get("merged"):
        L += ["## 표 안 논문의 별도 OpenAlex 레코드 (허브 아님 — 간선으로 병합)", ""]
        for x in g["merged"]:
            L.append(f"- {x['pid']} ← {', '.join(x['cited_by'])} (OpenAlex {x['wid']}: {x['title']})")
        L.append("")
    if g["isolated"]:
        L += ["## 고립 논문 (표 안에서 인용 관계 없음)", "",
              "인용 관계가 없다고 무관한 건 아니다(다른 분야·최신작·OpenAlex 참고문헌 파싱 누락). 관련성은 표의 '사용 이유' 열로 판단.", ""]
        for pid in g["isolated"]:
            nd = next(x for x in g["nodes"] if x["pid"] == pid)
            L.append(f"- {pid} ({nd['year']}) {nd['title']}")
        L.append("")
    return "\n".join(L)


def render_graph_html(g):
    import html as _h
    md_body = render_graph_md(g)
    # 표·목록은 간단히 <pre>로, mermaid는 네이티브 렌더(<pre class="mermaid">)
    mer = render_graph_mermaid(g)
    hubs = "".join(f"<tr><td>H{i}</td><td>{_h.escape(h['title'] or '(제목 없음 — OpenAlex ' + h['wid'] + ')')}{(' <b>⚠ ' + _h.escape(h['flag']) + '</b>') if h.get('flag') else ''}</td><td>{h['year'] or '?'}</td><td>{h['cit'] if h['cit'] is not None else '?'}</td>"
                   f"<td>{', '.join(h['cited_by'])}</td><td>{('<a href=\"https://doi.org/' + _h.escape(h['doi']) + '\">' + _h.escape(h['doi']) + '</a>') if h['doi'] else '—'}</td></tr>"
                   for i, h in enumerate(g["hubs"], 1))
    iso = "".join(f"<li>{pid} · {_h.escape(str(next(x for x in g['nodes'] if x['pid'] == pid)['title']))}</li>" for pid in g["isolated"])
    return f"""<title>{_h.escape(g['title'])} 인용 그래프</title>
<style>:root{{--ink:#1c1c1c;--ink2:#555;--line:#e3e0d8;--bg:#faf8f3;--surface:#fff;--accent:#2f5fa8}}
@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{--ink:#ececec;--ink2:#b5b5b5;--line:#3a3a3a;--bg:#161616;--surface:#1f1f1f;--accent:#8fb4ff}}}}
:root[data-theme="dark"]{{--ink:#ececec;--ink2:#b5b5b5;--line:#3a3a3a;--bg:#161616;--surface:#1f1f1f;--accent:#8fb4ff}}
body{{background:var(--bg);color:var(--ink);font-family:"IBM Plex Sans","Apple SD Gothic Neo",system-ui,sans-serif;margin:0}}
.wrap{{max-width:1180px;margin:0 auto;padding:28px 22px 60px}}h1{{font-size:26px;margin:0 0 6px}}.sub{{color:var(--ink2);font-size:13.5px;max-width:80ch;line-height:1.55}}
.card{{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:16px;margin-top:16px;overflow-x:auto}}
table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}}th{{color:var(--ink2);font-weight:600;font-size:11.5px;text-transform:uppercase;letter-spacing:.06em}}
a{{color:var(--accent)}}h2{{font-size:16px;margin:0 0 10px}}.leg{{display:flex;gap:16px;flex-wrap:wrap;font-size:12.5px;color:var(--ink2);margin-top:8px}}</style>
<div class="wrap"><h1>{_h.escape(g['title'])} <span style="color:var(--ink2);font-weight:400">인용 그래프</span></h1>
<p class="sub">논문 {len(g['nodes'])}편 · 내부 인용 간선 {len(g['edges'])} · 공통 참조 허브 {len(g['hubs'])} · 고립 {len(g['isolated'])}편{(' · OpenAlex 미해결 ' + ', '.join(g['unresolved'])) if g['unresolved'] else ''} · 소스 OpenAlex referenced_works · 조회 {time.strftime('%Y-%m-%d')}</p>
<div class="leg"><span>실선 → : 표 안 논문끼리의 인용(인용한 쪽 → 인용된 쪽)</span><span>점선 ⇢ H#: 표에 없는 공통 참조 문헌(2편 이상이 함께 인용) = 누락 후보</span><span>진한 파랑 = 최신</span></div>
<div class="card"><pre class="mermaid">{_h.escape(mer)}</pre></div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/mermaid/11.6.0/mermaid.min.js"></script>
<script>(function(){{if(!window.mermaid)return;var dark=document.documentElement.dataset.theme==='dark'||(document.documentElement.dataset.theme!=='light'&&matchMedia('(prefers-color-scheme: dark)').matches);
mermaid.initialize({{startOnLoad:false,theme:dark?'dark':'default',securityLevel:'loose',flowchart:{{htmlLabels:true,useMaxWidth:true}}}});
var el=document.querySelector('pre.mermaid');if(el&&/^\\s*graph/.test(el.textContent))mermaid.run({{nodes:[el]}});}})();</script>
{('<div class="card"><h2>공통 참조 허브 (표 밖 — project add 검토)</h2><table><thead><tr><th>H#</th><th>문헌</th><th>연도</th><th>인용수</th><th>인용한 P#</th><th>DOI</th></tr></thead><tbody>' + hubs + '</tbody></table></div>') if g['hubs'] else ''}
{('<div class="card"><h2>고립 논문 (표 안 인용 관계 없음 — 무관하다는 뜻은 아님)</h2><ul>' + iso + '</ul></div>') if g['isolated'] else ''}
{('<div class="card"><h2>표 안 논문의 별도 OpenAlex 레코드 (간선으로 병합)</h2><ul>' + ''.join(f"<li>{x['pid']} ← {', '.join(x['cited_by'])} (OpenAlex {x['wid']}: {_h.escape(str(x['title']))})</li>" for x in g.get('merged', [])) + '</ul></div>') if g.get('merged') else ''}
</div>
"""


def cmd_graph(a):
    proj = _pload(a.slug)
    proj, _note = _apply_selection(proj, a)
    if not proj["papers"]:
        print("ERROR: 논문 0편", file=sys.stderr); sys.exit(2)
    g = build_citation_graph(proj, min_shared=a.min_shared, hub_limit=a.hubs, check=not getattr(a, "no_check", False))
    if _note:
        g["title"] += f" ({_note})"
    fmt = a.format
    text = (json.dumps(g, ensure_ascii=False, indent=1) if fmt == "json" else
            render_graph_mermaid(g) if fmt == "mermaid" else
            render_graph_html(g) if fmt == "html" else render_graph_md(g))
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"그래프 → {a.out} (논문 {len(g['nodes'])} · 간선 {len(g['edges'])} · 허브 {len(g['hubs'])} · 고립 {len(g['isolated'])}"
              f"{' · 미해결 ' + ','.join(g['unresolved']) if g['unresolved'] else ''}, {fmt})")
    else:
        sys.stdout.write(text)


# ==== data: 1차출처 시계열(FRED · World Bank) + W# 데이터 ledger (round6 data-layer-w) ====
# 설계: 표준 라이브러리·무키 공개 엔드포인트만. 시계열은 sqlite 캐시하지 않는다 — ledger가
# 곧 기록이며, 같은 (source, series_id, country)를 다시 받으면 같은 W#를 재사용하고
# fetched_at·sha256·obs_end만 갱신한다(P# 규약과 동일: 번호 재사용 금지, 삭제 번호는 비움).
# 계산(수익률·MDD·샤프)은 여기 넣지 않는다 — Claude가 세션에서 CSV를 읽어 계산하고 모든
# 수치를 [W#]에 귀속한다. `data show --stats`는 검산용 기술통계(n·min·max·first/last·결측)만.
#
# exit 계약:
#   data fred/worldbank : 성공 0 / 네트워크 실패·HTTP≠200·빈 CSV·헤더 불일치·인자 오류 2(BAD_SOURCE)
#   data verify         : 전부 OK 0 / stale(원격 obs_end가 ledger보다 뒤 — 새 관측 있음) 또는
#                         원격 재조회 실패(미검증) 5 / sha256 불일치·csv 파일 부재·미등록 W# 3 /
#                         검증 대상 0건 2(0건은 통과가 아니다 — verify-batch와 동일 원칙).
#                         3이 5보다 우선(하나라도 무결성 실패면 3).
#   data show/list/vintage : 0 / 미등록 W#·ledger 없음 2
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
FRED_SERIES_URL = "https://fred.stlouisfed.org/series/{sid}"
WB_URL = "https://api.worldbank.org/v2/country/{iso}/indicator/{ind}?format=json&per_page=20000"


class DataError(Exception):
    """BAD_SOURCE 계열 — stderr 사유 + exit 2."""


def _data_dir():
    return os.path.join(SCHOLAR_HOME, "data")


def _ledger_path():
    return os.path.join(_data_dir(), "ledger.json")


def _ledger_load():
    f = _ledger_path()
    if not os.path.exists(f):
        return {"next_wid": 1, "entries": []}
    with open(f, encoding="utf-8") as fh:
        led = json.load(fh)
    led.setdefault("next_wid", 1)
    led.setdefault("entries", [])
    return led


def _ledger_save(led):
    os.makedirs(_data_dir(), exist_ok=True)
    tmp = _ledger_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(led, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, _ledger_path())


def _next_wid(led):
    """단조 증가 — 삭제된 번호는 다시 쓰지 않는다(next_wid 카운터 + 기존 최대값의 max)."""
    used = [int(e["wid"][1:]) for e in led["entries"] if re.fullmatch(r"W\d+", e.get("wid", ""))]
    n = max(int(led.get("next_wid", 1)), (max(used) + 1) if used else 1)
    led["next_wid"] = n + 1
    return f"W{n}"


def _ledger_find(led, source, series_id, country):
    for e in led["entries"]:
        if (e.get("source"), e.get("series_id"), e.get("country") or None) == (source, series_id, country or None):
            return e
    return None


def _ledger_get(led, wid):
    for e in led["entries"]:
        if e.get("wid") == wid:
            return e
    return None


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _date_ok(s, kind="date"):
    """--start/--end 형식 검사. kind=date: YYYY-MM-DD, kind=year: YYYY."""
    if s is None:
        return None
    pat = r"\d{4}-\d{2}-\d{2}" if kind == "date" else r"\d{4}"
    if not re.fullmatch(pat, str(s)):
        raise DataError(f"날짜 형식 오류({s!r}) — {'YYYY-MM-DD' if kind == 'date' else 'YYYY'}")
    return str(s)


def _filter_rows(rows, start, end):
    """문자열 비교 — ISO 날짜(YYYY-MM-DD)·연도(YYYY) 모두 사전순=시간순."""
    return [(d, v) for d, v in rows if (not start or d >= start) and (not end or d[:len(end)] <= end)]


def _parse_fred_csv(body, sid):
    """FRED fredgraph.csv → [(date, float|None)]. 헤더 'observation_date,<ID>' 강제, 결측 '.'→None."""
    lines = [ln.strip() for ln in (body or "").splitlines() if ln.strip()]
    if not lines:
        raise DataError(f"FRED {sid}: 빈 CSV")
    hdr = [c.strip() for c in lines[0].split(",")]
    if len(hdr) < 2 or hdr[0].lower() != "observation_date" or hdr[1].upper() != sid.upper():
        raise DataError(f"FRED {sid}: CSV 헤더 불일치 — {lines[0][:80]!r} (기대 'observation_date,{sid}')")
    rows = []
    for ln in lines[1:]:
        parts = ln.split(",")
        if len(parts) < 2 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[0]):
            continue
        raw = parts[1].strip()
        if raw in (".", ""):
            rows.append((parts[0], None))
        else:
            try:
                rows.append((parts[0], float(raw)))
            except ValueError:
                rows.append((parts[0], None))
    if not rows:
        raise DataError(f"FRED {sid}: 관측 행 0건")
    return rows


def fetch_fred_csv(sid, start=None, end=None):
    """→ (rows, url). 실패는 DataError."""
    url = FRED_CSV_URL.format(sid=urllib.parse.quote(sid))
    body, code = http_get(url, headers={"Accept": "text/csv,*/*"})
    if code != 200 or body is None:
        raise DataError(f"FRED {sid}: HTTP {code} ({url}) — 시리즈 ID 오탈자 또는 네트워크")
    rows = _parse_fred_csv(body, sid)
    rows = _filter_rows(rows, start, end)
    if not rows:
        raise DataError(f"FRED {sid}: 기간 필터({start}~{end}) 후 관측 0건")
    return rows, url


def _fred_title_from_html(html_body):
    mt = re.search(r"<title>(.*?)</title>", html_body or "", re.S | re.I)
    if not mt:
        return None
    t = html.unescape(mt.group(1)).strip()
    t = re.split(r"\s*\|\s*FRED\b", t)[0].strip()
    return t or None


def fetch_fred_meta(sid):
    """제목은 /series/<ID> 페이지 <title>(' | FRED' 앞부분). 옛 /data/<ID>.txt는 폐지됨
    (2026-09-15 실측 301→HTML). 단위·계절조정은 HTML에서 확실히 못 얻으면 None.
    실패해도 예외를 내지 않는다 — CSV만으로 진행."""
    meta = {"title": None, "units": None, "seasonal_adjustment": None}
    try:
        body, code = http_get(FRED_SERIES_URL.format(sid=urllib.parse.quote(sid)),
                              headers={"Accept": "text/html,*/*"})
        if code == 200 and body:
            meta["title"] = _fred_title_from_html(body)
    except Exception:
        pass
    return meta


def infer_frequency(dates):
    """관측일 간격의 최빈값 → D/W/M/Q/A (1일·7일·1개월·3개월·1년). 판정 불가면 None."""
    import datetime as _dt
    ds = []
    for d in dates:
        try:
            ds.append(_dt.date.fromisoformat(d) if len(d) == 10 else _dt.date(int(d[:4]), 1, 1))
        except ValueError:
            continue
    if len(ds) < 2:
        return None
    gaps = [(b - a).days for a, b in zip(ds, ds[1:]) if (b - a).days > 0]
    if not gaps:
        return None
    mode = max(set(gaps), key=gaps.count)
    if mode <= 3:      # 영업일 시계열은 주말 건너뜀(1이 최빈, 3도 허용)
        return "D"
    if 6 <= mode <= 8:
        return "W"
    if 28 <= mode <= 31:
        return "M"
    if 89 <= mode <= 92:
        return "Q"
    if 365 <= mode <= 366:
        return "A"
    return None


def fetch_worldbank(ind, iso, start=None, end=None):
    """World Bank v2 (무키). 응답 [meta, rows]; rows[].{date,value,indicator.value,country.value}.
    → (rows[(year,val|None)] 오름차순, title, country_name, url)."""
    url = WB_URL.format(iso=urllib.parse.quote(iso), ind=urllib.parse.quote(ind))
    body, code = http_get(url)
    if code != 200 or body is None:
        raise DataError(f"World Bank {ind}/{iso}: HTTP {code} ({url})")
    try:
        data = json.loads(body)
    except ValueError:
        raise DataError(f"World Bank {ind}/{iso}: JSON 파싱 실패")
    if not isinstance(data, list) or len(data) < 2 or not isinstance(data[1], list):
        msg = ""
        if isinstance(data, list) and data and isinstance(data[0], dict):
            msg = str((data[0].get("message") or [{}])[0].get("value", ""))[:120]
        raise DataError(f"World Bank {ind}/{iso}: 응답 형식 불일치(지표·국가 코드 확인){' — ' + msg if msg else ''}")
    rows, title, cname = [], None, None
    for r in data[1]:
        if not isinstance(r, dict) or not r.get("date"):
            continue
        v = r.get("value")
        try:
            v = float(v) if v is not None else None
        except (TypeError, ValueError):
            v = None
        rows.append((str(r["date"]), v))
        title = title or (r.get("indicator") or {}).get("value")
        cname = cname or (r.get("country") or {}).get("value")
    rows.sort(key=lambda x: x[0])
    rows = _filter_rows(rows, start, end)
    if not rows:
        raise DataError(f"World Bank {ind}/{iso}: 관측 0건(기간 {start}~{end})")
    return rows, title, cname, url


def _write_csv(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write("date,value\n")
        for d, v in rows:
            fh.write(f"{d},{'' if v is None else (repr(v) if v != int(v) else str(int(v)))}\n")
    os.replace(tmp, path)


def _read_csv(path):
    rows = []
    with open(path, encoding="utf-8") as fh:
        hdr = fh.readline()
        if not hdr.startswith("date,value"):
            raise DataError(f"{path}: CSV 헤더 불일치 {hdr.strip()!r}")
        for ln in fh:
            ln = ln.rstrip("\n")
            if not ln:
                continue
            d, _, v = ln.partition(",")
            rows.append((d, float(v) if v.strip() else None))
    return rows


def _obs_bounds(rows):
    """(obs_start, obs_end, n_obs, n_missing). obs_end = 마지막 **비결측** 관측일(데이터 기준시점)."""
    non = [d for d, v in rows if v is not None]
    return (non[0] if non else rows[0][0], non[-1] if non else rows[-1][0],
            len(non), len(rows) - len(non))


def _ledger_upsert(led, source, series_id, country, rows, title, units, frequency, url,
                   start=None, end=None, seasonal=None):
    e = _ledger_find(led, source, series_id, country)
    new = e is None
    if new:
        e = {"wid": _next_wid(led), "source": source, "series_id": series_id, "country": country}
        led["entries"].append(e)
    fname = f"{e['wid']}_{re.sub(r'[^A-Za-z0-9._-]', '_', series_id)}" + (f"_{country}" if country else "") + ".csv"
    csv_path = os.path.join(_data_dir(), fname)
    _write_csv(csv_path, rows)
    s, en, n, nm = _obs_bounds(rows)
    e.update({"title": title if title else e.get("title"), "units": units if units else e.get("units"),
              "frequency": frequency or e.get("frequency"), "seasonal_adjustment": seasonal,
              "obs_start": s, "obs_end": en, "n_obs": n, "n_missing": nm,
              "filter_start": start, "filter_end": end,
              "fetched_at": _utc_now(), "url": url, "csv_path": csv_path,
              "sha256": _sha256_file(csv_path)})
    return e, new


def _copy_out(csv_path, out):
    if out:
        import shutil
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        shutil.copyfile(csv_path, out)


def _print_entry(e, new):
    print(f"[{e['wid']}] {'신규' if new else '재사용(갱신)'} · {e['source']} {e['series_id']}"
          f"{' ' + e['country'] if e.get('country') else ''} · {e.get('title') or '(제목 미확보)'}\n"
          f"  기간 {e['obs_start']}~{e['obs_end']} · n={e['n_obs']}(결측 {e['n_missing']}) · 주기 {e.get('frequency') or '?'}"
          f" · 단위 {e.get('units') or 'null'}\n  수집 {e['fetched_at']} · {e['csv_path']}\n  sha256 {e['sha256'][:16]}… · {e['url']}")


def cmd_data_fred(a):
    try:
        start, end = _date_ok(a.start), _date_ok(a.end)
        sid = a.series_id.strip()
        if not re.fullmatch(r"[A-Za-z0-9_.\-]{1,64}", sid):
            raise DataError(f"FRED 시리즈 ID 형식 오류: {sid!r}")
        rows, url = fetch_fred_csv(sid, start, end)
    except DataError as e:
        print(f"ERROR(BAD_SOURCE): {e}", file=sys.stderr)
        sys.exit(2)
    meta = fetch_fred_meta(sid)
    led = _ledger_load()
    e, new = _ledger_upsert(led, "fred", sid, None, rows, meta.get("title"), meta.get("units"),
                            infer_frequency([d for d, _ in rows]), url, start, end,
                            meta.get("seasonal_adjustment"))
    _ledger_save(led)
    _copy_out(e["csv_path"], a.out)
    _print_entry(e, new)


def cmd_data_worldbank(a):
    try:
        start, end = _date_ok(a.start, "year"), _date_ok(a.end, "year")
        ind = a.indicator.strip()
        if not re.fullmatch(r"[A-Za-z0-9_.\-]{1,64}", ind):
            raise DataError(f"World Bank 지표 코드 형식 오류: {ind!r}")
        countries = [c.strip().upper() for c in (a.country or "").split(",") if c.strip()]
        if not countries or not all(re.fullmatch(r"[A-Z0-9]{2,3}", c) for c in countries):
            raise DataError("--country는 ISO 코드(US 또는 US,KR)")
        fetched = [(c,) + fetch_worldbank(ind, c, start, end) for c in countries]
    except DataError as e:
        print(f"ERROR(BAD_SOURCE): {e}", file=sys.stderr)
        sys.exit(2)
    led = _ledger_load()
    outs = []
    for c, rows, title, cname, url in fetched:
        e, new = _ledger_upsert(led, "worldbank", ind, c, rows,
                                f"{title} — {cname}" if title and cname else title, None,
                                infer_frequency([d for d, _ in rows]), url, start, end)
        outs.append((e, new))
    _ledger_save(led)
    if a.out:
        if len(outs) == 1:
            _copy_out(outs[0][0]["csv_path"], a.out)
        else:
            base, ext = os.path.splitext(a.out)
            for e, _ in outs:
                _copy_out(e["csv_path"], f"{base}_{e['country']}{ext or '.csv'}")
    for e, new in outs:
        _print_entry(e, new)


def _select_entries(led, wids):
    if not led["entries"]:
        raise DataError(f"ledger 비어 있음: {_ledger_path()} — data fred/worldbank로 먼저 수집")
    if not wids:
        return list(led["entries"])
    sel = []
    for w in wids:
        e = _ledger_get(led, w)
        if not e:
            raise DataError(f"미등록 W#: {w}")
        sel.append(e)
    return sel


def cmd_data_list(a):
    led = _ledger_load()
    if not led["entries"]:
        print(f"(데이터 없음: {_ledger_path()})")
        return
    print(f"{'W#':<5} {'출처':<10} {'시리즈':<22} {'국가':<4} {'기간':<23} {'obs_end':<10} {'수집(UTC)':<20} {'n':>6}  제목")
    for e in led["entries"]:
        period = f"{e['obs_start']}~{e['obs_end']}"
        print(f"{e['wid']:<5} {e['source']:<10} {e['series_id'][:22]:<22} {(e.get('country') or '-'):<4} "
              f"{period:<23} {e['obs_end']:<10} {e['fetched_at']:<20} {e['n_obs']:>6}  {(e.get('title') or '')[:60]}")


def _series_stats(rows):
    vals = [(d, v) for d, v in rows if v is not None]
    if not vals:
        return {"n": 0, "n_missing": len(rows)}
    mn = min(vals, key=lambda x: x[1]); mx = max(vals, key=lambda x: x[1])
    return {"n": len(vals), "n_missing": len(rows) - len(vals),
            "min": {"date": mn[0], "value": mn[1]}, "max": {"date": mx[0], "value": mx[1]},
            "first": {"date": vals[0][0], "value": vals[0][1]},
            "last": {"date": vals[-1][0], "value": vals[-1][1]}}


def cmd_data_show(a):
    led = _ledger_load()
    try:
        e = _ledger_get(led, a.wid)
        if not e:
            raise DataError(f"미등록 W#: {a.wid}")
        if not os.path.exists(e["csv_path"]):
            raise DataError(f"{a.wid}: csv 파일 부재 {e['csv_path']} — data verify 후 재수집")
        rows = _read_csv(e["csv_path"])
    except DataError as x:
        print(f"ERROR: {x}", file=sys.stderr)
        sys.exit(2)
    print(f"[{e['wid']}] {e['source']} {e['series_id']}{' ' + e['country'] if e.get('country') else ''}"
          f" · {e.get('title') or ''} · 주기 {e.get('frequency') or '?'} · obs_end {e['obs_end']} · 수집 {e['fetched_at']}")
    if a.stats:
        st = _series_stats(rows)
        print("stats: " + json.dumps(st, ensure_ascii=False))
    n = a.tail if a.tail and a.tail > 0 else 10
    print("date,value")
    for d, v in rows[-n:]:
        print(f"{d},{'' if v is None else v}")


def _remote_obs_end(e):
    """원격 재조회 → obs_end(마지막 비결측 관측일). 실패면 None."""
    try:
        if e["source"] == "fred":
            rows, _ = fetch_fred_csv(e["series_id"], e.get("filter_start"), e.get("filter_end"))
        elif e["source"] == "worldbank":
            rows, _, _, _ = fetch_worldbank(e["series_id"], e["country"], e.get("filter_start"), e.get("filter_end"))
        else:
            return None
    except DataError:
        return None
    return _obs_bounds(rows)[1]


def cmd_data_verify(a):
    """①csv_path 존재·sha256 일치 ②원격 재조회 ③원격 obs_end > ledger obs_end → ⚠ stale.
    요약줄 '데이터 검증: k/N OK · stale m건' + exit 0(전부 OK)/5(stale 또는 재조회 실패 — 미검증)/
    3(해시 불일치·파일 부재·미등록 W#)/2(대상 0건). 3 > 5 > 0 우선."""
    led = _ledger_load()
    try:
        entries = _select_entries(led, a.wids)
    except DataError as x:
        print(f"ERROR: {x}", file=sys.stderr)
        print("데이터 검증: 0/0 OK · stale 0건")
        sys.exit(3 if "미등록" in str(x) else 2)
    ok = stale = hard = unverified = 0
    results = []
    for e in entries:
        wid = e["wid"]
        if not os.path.exists(e["csv_path"]):
            hard += 1; results.append((wid, "MISSING_FILE", e["csv_path"])); continue
        sha = _sha256_file(e["csv_path"])
        if sha != e.get("sha256"):
            hard += 1; results.append((wid, "SHA_MISMATCH", f"ledger {e.get('sha256', '')[:12]} ≠ file {sha[:12]}")); continue
        remote_end = _remote_obs_end(e)
        if remote_end is None:
            unverified += 1; results.append((wid, "UNVERIFIED", "원격 재조회 실패 — 미검증(부재 아님)")); continue
        if remote_end > e["obs_end"]:
            stale += 1; results.append((wid, "⚠ stale", f"ledger obs_end {e['obs_end']} < 원격 {remote_end} — 새 관측 있음, 재수집 권장"))
        else:
            ok += 1; results.append((wid, "OK", f"sha256 일치 · obs_end {e['obs_end']} 최신"))
    for wid, verdict, detail in results:
        print(f"{wid:<5} {verdict:<13} {detail}")
    print(f"데이터 검증: {ok}/{len(entries)} OK · stale {stale}건"
          + (f" · 미검증 {unverified}건" if unverified else "")
          + (f" · 무결성 실패 {hard}건" if hard else ""))
    if a.json:
        print(json.dumps([{"wid": w, "verdict": v, "detail": d} for w, v, d in results], ensure_ascii=False))
    sys.exit(3 if hard else (5 if (stale or unverified) else 0))


def render_vintage_md(entries):
    lines = ["| W# | 시리즈 | 관측 기준시점(obs_end) | 수집일(fetched_at) | 주기 | 출처 URL |",
             "|---|---|---|---|---|---|"]
    for e in entries:
        name = f"{e['series_id']}{' ' + e['country'] if e.get('country') else ''}"
        if e.get("title"):
            name += f" — {_md_cell(e['title'], 60)}"
        lines.append(f"| {e['wid']} | {name} | {e['obs_end']} | {e['fetched_at'][:10]} | {e.get('frequency') or '?'} | {e['url']} |")
    return "\n".join(lines)


def cmd_data_vintage(a):
    """답변에 붙일 신선도 표 — '발간일 ≠ 데이터 기준시점'을 표로 강제(사용자 CLAUDE.md 신선도 규칙)."""
    led = _ledger_load()
    try:
        entries = _select_entries(led, a.wids)
    except DataError as x:
        print(f"ERROR: {x}", file=sys.stderr)
        sys.exit(2)
    md = render_vintage_md(entries)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(md + "\n")
        print(f"저장: {a.out}")
    else:
        print(md)


def add_data_parsers(sub):
    dp = sub.add_parser("data", help="1차출처 시계열(FRED·World Bank) 수집 + W# 데이터 ledger·검증·신선도 표")
    ds = dp.add_subparsers(dest="data_cmd", required=True)
    x = ds.add_parser("fred", help="FRED 시리즈 CSV(무키) → $SCHOLAR_HOME/data/<W#>_<ID>.csv + ledger")
    x.add_argument("series_id"); x.add_argument("--start"); x.add_argument("--end"); x.add_argument("--out")
    x.set_defaults(fn=cmd_data_fred)
    x = ds.add_parser("worldbank", help="World Bank v2 지표(무키) → 국가별 W# (--country US,KR)")
    x.add_argument("indicator"); x.add_argument("--country", required=True)
    x.add_argument("--start", help="YYYY"); x.add_argument("--end", help="YYYY"); x.add_argument("--out")
    x.set_defaults(fn=cmd_data_worldbank)
    x = ds.add_parser("list", help="ledger 표"); x.set_defaults(fn=cmd_data_list)
    x = ds.add_parser("show", help="마지막 N행(+--stats 검산용 기술통계)")
    x.add_argument("wid"); x.add_argument("--tail", type=int, default=10); x.add_argument("--stats", action="store_true")
    x.set_defaults(fn=cmd_data_show)
    x = ds.add_parser("verify", help="csv 무결성(sha256)+원격 재조회 신선도. exit 0/5(stale·미검증)/3(무결성)/2(0건)")
    x.add_argument("wids", nargs="*"); x.add_argument("--json", action="store_true")
    x.set_defaults(fn=cmd_data_verify)
    x = ds.add_parser("vintage", help="신선도 표(마크다운): W#|시리즈|obs_end|fetched_at|주기|URL")
    x.add_argument("wids", nargs="*"); x.add_argument("--out")
    x.set_defaults(fn=cmd_data_vintage)


def main():
    ap = argparse.ArgumentParser(prog="scholar.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    # --no-cache: sqlite 메타·초록 캐시 비활성(SCHOLAR_NO_CACHE=1과 동일). 철회 판정은
    # 캐시와 무관하게 항상 실 조회된다
    nc = argparse.ArgumentParser(add_help=False)
    nc.add_argument("--no-cache", action="store_true", dest="no_cache",
                    help="sqlite 메타·초록 캐시를 쓰지 않는다(SCHOLAR_NO_CACHE=1과 동일)")

    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=10)
    s.add_argument("--sources", default="openalex,crossref,s2",
                   help="openalex,crossref,s2,arxiv,pubmed,epmc 조합 "
                        "(기본: openalex,crossref,s2 · epmc=Europe PMC 전문검색). "
                        "미인식 소스명은 exit 2로 거부한다")
    s.add_argument("--year-from", type=int, dest="year_from")
    s.add_argument("--oa-only", action="store_true")
    s.add_argument("--include-retracted", action="store_true", dest="include_retracted",
                   help="철회(⚠️RETRACTED) 행을 맨 뒤로 보내지 않고 랭킹 위치 그대로 둔다(기본: 맨 뒤)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_search)

    b = sub.add_parser("bundle", parents=[nc], help="원샷: 검색→DOI dedup→랭킹→상위 K편 초록 병렬 조회→JSON 1개+요약 표")
    b.add_argument("query")
    b.add_argument("--limit", type=int, default=10, help="병합 후 보존할 검색 결과 수(기본 10)")
    b.add_argument("--top", type=int, default=8, help="초록을 조회할 상위 편수(기본 8)")
    b.add_argument("--sources", default="openalex,crossref,s2",
                   help="search와 동일(기본 openalex,crossref,s2). 미인식 소스명은 exit 2")
    b.add_argument("--year-from", type=int, dest="year_from")
    b.add_argument("--oa-only", action="store_true")
    b.add_argument("--include-retracted", action="store_true", dest="include_retracted",
                   help="철회 행을 랭킹 위치·상위 K 초록 슬롯에 그대로 둔다(기본: 맨 뒤로 보내고 초록 슬롯에서 제외)")
    b.add_argument("--recent-slots", type=int, default=2, dest="recent_slots",
                   help="상위 K 초록 슬롯 중 최소 N편을 최근 5년(현재연도-5 이상) 논문으로 예약(기본 2, 0=예약 없음). "
                        "예약 대상이 없으면 그대로. search에는 적용 안 됨")
    b.add_argument("--out", help="JSON 저장 경로(기본 $TMPDIR/scholar-bundle/<query>_<시각>.json)")
    b.add_argument("--json", action="store_true", help="요약 표 대신 JSON을 stdout에도 출력")
    b.set_defaults(fn=cmd_bundle)

    p = sub.add_parser("paper", parents=[nc])
    p.add_argument("id", help="DOI 또는 OpenAlex W-id")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_paper)

    o = sub.add_parser("oa")
    o.add_argument("doi")
    o.set_defaults(fn=cmd_oa)

    c = sub.add_parser("cite")
    c.add_argument("doi")
    c.add_argument("--style", default="apa", help="apa|mla|chicago 또는 CSL 스타일명")
    c.set_defaults(fn=cmd_cite)

    v = sub.add_parser("verify", parents=[nc])
    v.add_argument("doi")
    v.add_argument("title", help="답변에 인용한 제목 (실제 제목과 대조)")
    v.add_argument("--author", help="인용한 1저자 성(family name) — 대조용")
    v.add_argument("--year", type=int, help="인용한 발행연도 — ±1년 허용")
    v.set_defaults(fn=cmd_verify)

    vb = sub.add_parser("verify-batch", parents=[nc])
    vb.add_argument("file",
                    help='JSON 배열 파일: [{"id","doi","title","author","year"}]'
                         " — author(1저자 성)·year는 필수(누락 시 INCOMPLETE_META)")
    vb.add_argument("--json", action="store_true")
    vb.set_defaults(fn=cmd_verify_batch)

    n = sub.add_parser("snowball")
    n.add_argument("doi")
    n.add_argument("--direction", choices=["cites", "refs"], default="cites")
    n.add_argument("--limit", type=int, default=10)
    n.add_argument("--year-from", type=int, dest="year_from",
                   help="이 연도 이후만 (cites 기본 정렬이 인용수순이라 최신 후속연구가 "
                        "상위에 안 올라온다 — 최신성이 필요하면 함께 걸어라)")
    n.add_argument("--sort", choices=["citations", "date"], default="citations")
    n.add_argument("--query", help="주제 키워드 — OpenAlex title_and_abstract.search를 cites/refs 필터에 "
                                   "결합(유명 앵커에서 관련 부분집합 우선 회수). 전수 회수가 아니다")
    n.set_defaults(fn=cmd_snowball)

    rc = sub.add_parser("recommend")
    rc.add_argument("doi")
    rc.add_argument("--limit", type=int, default=10)
    rc.set_defaults(fn=cmd_recommend)

    add_project_parsers(sub)

    rv = sub.add_parser("review", help="피어 리뷰 패킷 — 초안의 섹션·[P#]·DOI·근거 없는 수치·과장어 전수 검사(판정은 references/peer-review-rubric.md)")
    rv.add_argument("file", help="초안 .md/.txt")
    rv.add_argument("--project", help="[P#]를 대조할 프로젝트 slug")
    rv.add_argument("--out", help="패킷 저장 경로(기본 stdout)")
    rv.add_argument("--json", action="store_true")
    rv.set_defaults(fn=cmd_review)

    gr = sub.add_parser("graph", help="프로젝트 인용 그래프 — 내부 인용 간선 + 공통 참조 허브(표에 빠진 선행연구 후보), mermaid/md/html/json")
    gr.add_argument("slug"); _add_filter_args(gr)
    gr.add_argument("--format", choices=["md", "mermaid", "html", "json"], default="md")
    gr.add_argument("--out")
    gr.add_argument("--min-shared", type=int, default=2, dest="min_shared", help="허브 판정: 이 편수 이상이 공통 인용(기본 2)")
    gr.add_argument("--hubs", type=int, default=10, help="허브 상한(기본 10)")
    gr.add_argument("--no-check", action="store_true", dest="no_check", help="허브 제목 arXiv/Crossref 재확인 생략(빠름·OpenAlex 제목 오염 미탐지)")
    gr.set_defaults(fn=cmd_graph)

    add_data_parsers(sub)

    a = ap.parse_args()
    if getattr(a, "no_cache", False):
        global _CACHE_OFF
        _CACHE_OFF = True
    a.fn(a)


if __name__ == "__main__":
    main()
