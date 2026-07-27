#!/usr/bin/env python3
"""scholar.py — 무료 학술 API 통합 CLI (표준 라이브러리만 사용)

scholar-research 스킬의 데이터층. 외부 유료 API 없음 — OpenAlex·Crossref·
arXiv·Unpaywall(전부 무키/이메일)·Semantic Scholar(무키 공유풀, 429 시 스킵).

subcommands:
  search  "query"        멀티소스 검색 → DOI dedup → 랭킹
  paper   <doi|W-id>     단일 논문 상세 (초록 복원 포함)
  oa      <doi>          Unpaywall 합법 OA PDF 위치
  cite    <doi>          서지 인용 문자열 (apa|mla|chicago — doi.org CSL)
  verify  <doi> "title"  인용 실재 기계검증 (MATCH/MISMATCH/NOT_FOUND)
  snowball <doi>         인용 눈덩이 (--direction cites|refs)
  recommend <doi>        S2 유사논문 추천 (의미 근접 — snowball 보완)

env: SCHOLAR_EMAIL(권장), OPENALEX_API_KEY(선택), S2_API_KEY(선택), NCBI_API_KEY(선택)
"""
import argparse
import difflib
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

EMAIL = os.environ.get("SCHOLAR_EMAIL", "scholar-research@example.com")
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
    return doi or None


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
                  "primary_location,authorships,open_access,type",
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
            "title": w.get("display_name") or w.get("title"),
            "year": w.get("publication_year"),
            "venue": src,
            "citations": w.get("cited_by_count", 0),
            "is_oa": (w.get("open_access") or {}).get("is_oa", False),
            "authors": [a.get("author", {}).get("display_name")
                        for a in (w.get("authorships") or [])[:3]],
        })
    return out, None


def search_crossref(q, limit, year_from=None):
    params = {"query": q, "rows": str(limit), "mailto": EMAIL,
              "select": "DOI,title,author,issued,container-title,is-referenced-by-count,type"}
    if year_from:
        params["filter"] = f"from-pub-date:{year_from}-01-01"
    body, code = http_get("https://api.crossref.org/works?" + urllib.parse.urlencode(params))
    if not body:
        return [], f"crossref HTTP {code}"
    out = []
    for it in json.loads(body).get("message", {}).get("items", []):
        year = None
        parts = (it.get("issued") or {}).get("date-parts") or [[None]]
        if parts and parts[0]:
            year = parts[0][0]
        out.append({
            "source": "crossref",
            "doi": norm_doi(it.get("DOI")),
            "title": (it.get("title") or [None])[0],
            "year": year,
            "venue": (it.get("container-title") or [None])[0],
            "citations": it.get("is-referenced-by-count", 0),
            "is_oa": None,
            "authors": [" ".join(filter(None, [a.get("given"), a.get("family")]))
                        for a in (it.get("author") or [])[:3]],
        })
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
            "is_oa": p.get("isOpenAccess"),
            "authors": [a.get("name") for a in (p.get("authors") or [])[:3]],
        })
    return out, None


def search_arxiv(q, limit):
    params = {"search_query": f"all:{q}", "max_results": str(limit),
              "sortBy": "relevance"}
    body, code = http_get("https://export.arxiv.org/api/query?" + urllib.parse.urlencode(params))
    if not body:
        return [], f"arxiv HTTP {code}"
    out = []
    for m in re.finditer(r"<entry>(.*?)</entry>", body, re.S):
        e = m.group(1)
        def tag(t):
            mm = re.search(rf"<{t}[^>]*>(.*?)</{t}>", e, re.S)
            return re.sub(r"\s+", " ", mm.group(1)).strip() if mm else None
        aid = tag("id") or ""
        doi_m = re.search(r'<arxiv:doi[^>]*>(.*?)</arxiv:doi>', e)
        out.append({
            "source": "arxiv",
            "id": aid,
            "doi": norm_doi(doi_m.group(1)) if doi_m else None,
            "title": tag("title"),
            "year": int(tag("published")[:4]) if tag("published") else None,
            "venue": "arXiv",
            "citations": 0,
            "is_oa": True,
            "pdf": aid.replace("/abs/", "/pdf/") if "/abs/" in aid else None,
            "authors": re.findall(r"<name>(.*?)</name>", e)[:3],
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
            "title": re.sub(r"</?[bi]>", "", it.get("title") or "").rstrip("."),
            "year": int(ym.group(1)) if ym else None,
            "venue": it.get("fulljournalname") or it.get("source"),
            "citations": 0,
            "is_oa": True if pmc else None,
            "authors": [a.get("name") for a in (it.get("authors") or [])[:3]],
        })
    return out, None


# ---------------- commands ----------------

def rank_key(p):
    import math
    cit = math.log1p(p.get("citations") or 0)
    year = p.get("year") or 0
    recency = max(0, (year - 2015)) * 0.25
    return cit + recency


def cmd_search(a):
    per = max(a.limit, 5)
    pool, errors = [], []
    sources = a.sources.split(",")
    if "openalex" in sources:
        r, e = search_openalex(a.query, per, a.year_from, a.oa_only)
        pool += r; e and errors.append(e)
    if "crossref" in sources:
        r, e = search_crossref(a.query, per, a.year_from)
        pool += r; e and errors.append(e)
    if "s2" in sources:
        r, e = search_s2(a.query, per, a.year_from)
        pool += r; e and errors.append(e)
    if "arxiv" in sources:
        r, e = search_arxiv(a.query, per)
        pool += r; e and errors.append(e)
    if "pubmed" in sources:
        r, e = search_pubmed(a.query, per, a.year_from)
        pool += r; e and errors.append(e)

    seen, merged = {}, []
    for p in pool:
        k = p.get("doi") or (p.get("title") or "").lower()[:80]
        if not k:
            continue
        if k in seen:  # 소스 병합: 인용수 최댓값·OA true 우선
            q = seen[k]
            q["citations"] = max(q.get("citations") or 0, p.get("citations") or 0)
            q["is_oa"] = q.get("is_oa") or p.get("is_oa")
            q.setdefault("also_in", []).append(p["source"])
        else:
            seen[k] = p
            merged.append(p)
    merged.sort(key=rank_key, reverse=True)
    merged = merged[: a.limit]
    if a.json:
        print(json.dumps({"query": a.query, "errors": errors, "results": merged},
                         ensure_ascii=False, indent=1))
        return
    print(f"# search: {a.query}  ({len(merged)}건, 소스오류: {errors or '없음'})")
    for i, p in enumerate(merged, 1):
        oa = "OA" if p.get("is_oa") else "  "
        doi = p.get("doi") or p.get("id") or "-"
        au = ", ".join(x for x in p.get("authors", []) if x) or "?"
        print(f"[P{i}] ({p.get('year')}) {p.get('title')}")
        print(f"     {au} · {p.get('venue') or '?'} · 인용 {p.get('citations')} · {oa} · {doi}"
              f" · via {p['source']}{'+' + '+'.join(p.get('also_in', [])) if p.get('also_in') else ''}")


def cmd_paper(a):
    ident = a.id
    if not ident.startswith("W") and "openalex" not in ident:
        ident = f"doi:{norm_doi(ident)}"
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
        "is_oa": (w.get("open_access") or {}).get("is_oa"),
        "oa_url": (w.get("open_access") or {}).get("oa_url"),
        "authors": [x.get("author", {}).get("display_name") for x in w.get("authorships", [])],
        "abstract": uninvert_abstract(w.get("abstract_inverted_index")),
        "referenced_works_count": len(w.get("referenced_works") or []),
    }
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    print(f"{out['title']} ({out['year']})")
    print(f"저자: {', '.join(filter(None, out['authors'][:8]))}")
    print(f"게재: {out['venue']} · 인용 {out['citations']} · DOI {out['doi']} · OA={out['is_oa']}")
    if out.get("oa_url"):
        print(f"OA URL: {out['oa_url']}")
    print(f"\n[초록]\n{out['abstract'] or '(초록 미제공)'}")


def cmd_oa(a):
    doi = norm_doi(a.doi)
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
    style = CSL_STYLES.get(a.style, a.style)
    body, code = http_get(
        f"https://doi.org/{urllib.parse.quote(doi)}",
        headers={"Accept": f"text/x-bibliography; style={style}; locale=en-US"})
    if not body:
        print(f"ERROR: doi.org HTTP {code}", file=sys.stderr)
        sys.exit(1)
    print(body.strip())


def cmd_verify(a):
    """인용 환각 방어: DOI가 실재하고 제목이 주장과 일치하는지 기계검증."""
    doi = norm_doi(a.doi)
    body, code = http_get(f"https://api.crossref.org/works/{urllib.parse.quote(doi)}")
    real_title = None
    if body:
        real_title = ((json.loads(body).get("message") or {}).get("title") or [None])[0]
    else:
        # Crossref 미등록 DOI(예: DataCite)는 OpenAlex로 2차 확인
        b2, c2 = http_get(f"https://api.openalex.org/works/doi:{doi}?mailto={EMAIL}")
        if b2:
            real_title = json.loads(b2).get("display_name")
        else:
            print(json.dumps({"doi": doi, "verdict": "NOT_FOUND",
                              "detail": f"crossref {code}, openalex {c2}"}, ensure_ascii=False))
            sys.exit(2)
    def canon(s):
        return re.sub(r"[^a-z0-9가-힣]+", " ", (s or "").lower()).strip()
    ratio = difflib.SequenceMatcher(None, canon(a.title), canon(real_title)).ratio()
    verdict = "MATCH" if ratio >= 0.75 else "MISMATCH"
    print(json.dumps({"doi": doi, "verdict": verdict, "similarity": round(ratio, 3),
                      "claimed": a.title, "actual": real_title}, ensure_ascii=False))
    sys.exit(0 if verdict == "MATCH" else 3)


def cmd_snowball(a):
    doi = norm_doi(a.doi)
    if a.direction == "cites":  # 이 논문을 인용한 후속 논문
        b, c = http_get(f"https://api.openalex.org/works/doi:{doi}?mailto={EMAIL}&select=id")
        if not b:
            print(f"ERROR: HTTP {c}", file=sys.stderr); sys.exit(1)
        wid = json.loads(b)["id"].rsplit("/", 1)[-1]
        url = ("https://api.openalex.org/works?"
               + urllib.parse.urlencode({"filter": f"cites:{wid}", "per-page": str(a.limit),
                                         "sort": "cited_by_count:desc", "mailto": EMAIL,
                                         "select": "doi,display_name,publication_year,cited_by_count"}))
        b2, c2 = http_get(url)
        items = json.loads(b2).get("results", []) if b2 else []
    else:  # refs: 이 논문이 인용한 문헌
        b, c = http_get(f"https://api.openalex.org/works/doi:{doi}?mailto={EMAIL}"
                        "&select=referenced_works")
        if not b:
            print(f"ERROR: HTTP {c}", file=sys.stderr); sys.exit(1)
        refs = (json.loads(b).get("referenced_works") or [])[: a.limit]
        items = []
        for rid in refs:
            wid = rid.rsplit("/", 1)[-1]
            b2, _ = http_get(f"https://api.openalex.org/works/{wid}?mailto={EMAIL}"
                             "&select=doi,display_name,publication_year,cited_by_count")
            if b2:
                items.append(json.loads(b2))
            time.sleep(0.15)
    for it in items:
        print(f"({it.get('publication_year')}) {it.get('display_name')}"
              f" · 인용 {it.get('cited_by_count')} · {norm_doi(it.get('doi')) or '-'}")


def cmd_recommend(a):
    """S2 recommendations — 앵커 논문과 유사한 논문 추천(snowball이 못 잡는 의미 근접)."""
    doi = norm_doi(a.doi)
    headers = {}
    key = os.environ.get("S2_API_KEY")
    if key:
        headers["x-api-key"] = key
    params = {"fields": "title,year,citationCount,externalIds,venue,authors,isOpenAccess",
              "limit": str(a.limit)}
    url = ("https://api.semanticscholar.org/recommendations/v1/papers/forpaper/"
           f"DOI:{urllib.parse.quote(doi)}?" + urllib.parse.urlencode(params))
    body, code = http_get(url, headers=headers, retries=1)
    if not body:
        # S2 무키 공유풀 혼잡(429)은 정상 — 재시도 말고 안내
        print(f"# recommend 실패 (S2 HTTP {code} — 429면 무키 혼잡, snowball로 대체)",
              file=sys.stderr)
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


def main():
    ap = argparse.ArgumentParser(prog="scholar.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=10)
    s.add_argument("--sources", default="openalex,crossref,s2",
                   help="openalex,crossref,s2,arxiv,pubmed 조합 (기본: openalex,crossref,s2)")
    s.add_argument("--year-from", type=int, dest="year_from")
    s.add_argument("--oa-only", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_search)

    p = sub.add_parser("paper")
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

    v = sub.add_parser("verify")
    v.add_argument("doi")
    v.add_argument("title", help="답변에 인용한 제목 (실제 제목과 대조)")
    v.set_defaults(fn=cmd_verify)

    n = sub.add_parser("snowball")
    n.add_argument("doi")
    n.add_argument("--direction", choices=["cites", "refs"], default="cites")
    n.add_argument("--limit", type=int, default=10)
    n.set_defaults(fn=cmd_snowball)

    rc = sub.add_parser("recommend")
    rc.add_argument("doi")
    rc.add_argument("--limit", type=int, default=10)
    rc.set_defaults(fn=cmd_recommend)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
