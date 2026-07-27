---
name: scholar-research
description: >
  This skill should be used when the user asks for answers grounded in academic
  literature (학술 논문 근거 답변·문헌 리서치). Liner AI의 Scholar 모드를 무료 학술
  API(OpenAlex·Crossref·Unpaywall·arXiv·S2)와 구독제 Claude만으로 재현한다 — 질문을
  검색전략으로 분해해 멀티소스 검색·스크리닝·정독 후, 모든 주장에 [P#] 출처 태그를
  달고 인용을 DOI 기계검증으로 전수 확인한 답변을 산출한다(환각 인용 0 보장).
  Korean triggers — "논문 근거로 답해줘", "학술 검색해줘", "문헌 리서치", "이 주제
  논문 찾아줘", "스칼라 모드", "출처 있는 답변으로", "인용 달아서 정리해줘",
  "선행연구 조사", "/scholar-research". English triggers — "answer with academic
  sources", "literature search", "find papers on", "scholar mode", "cite sources".
  DO NOT trigger for 일반 웹 리서치(→ WebSearch/insane-research), 투자 종목·산업
  분석(→ equity-deep-dive/industry-analysis), 이미 가진 PDF의 번역·요약(→
  pdf-translate), 단순 사실 확인.
---

# scholar-research — 출처 있는 학술 답변 파이프라인

모든 데이터 접근은 `scripts/scholar.py`(표준 라이브러리만, 무료 API만) 하나로 한다.
`S="${CLAUDE_PLUGIN_ROOT}/skills/scholar-research/scripts/scholar.py"`  (플러그인 설치 시 자동 해석. 로컬 개발이면 실제 경로로 대체)

```
python3 $S search "query" [--limit N] [--sources openalex,crossref,s2,arxiv,pubmed] [--year-from Y] [--oa-only] [--json]
python3 $S paper <doi>            # 상세+초록(inverted index 복원)
python3 $S oa <doi>               # 합법 OA PDF 위치 (Unpaywall)
python3 $S cite <doi> --style apa|mla|chicago
python3 $S verify <doi> "인용한 제목"   # MATCH/MISMATCH/NOT_FOUND (exit 0/3/2)
python3 $S snowball <doi> --direction cites|refs
python3 $S recommend <doi>       # S2 유사논문 추천(의미 근접, snowball 보완)
```

env(선택): `SCHOLAR_EMAIL`(polite pool 권장 — 미설정 시 스크립트 기본 이메일 사용), `OPENALEX_API_KEY`, `S2_API_KEY`, `NCBI_API_KEY`(PubMed 10req/s).

## 파이프라인 (6단계 — 순서 고정)

### 1. 질문 분해 → 검색전략
- 질문을 **영문 학술 키워드 2~3개 조합**으로 변환한다(동의어·학술용어 치환:
  "우라늄 ISR 회수율" → "in-situ recovery uranium extraction efficiency").
- 시효성 주제면 `--year-from`을 걸어라.
- **분야 라우팅** — 기본 소스는 openalex,crossref,s2. 질문 분야에 따라 추가:
  - 의학·생명과학·임상 → `--sources`에 **pubmed** 추가 (PubMed는 인용수 0으로
    나오므로 랭킹은 openalex 쪽 수치로 판단. PMC 있으면 OA 표시됨)
  - CS·물리·수학 등 프리프린트 문화권 → **arxiv** 추가
  - 경제·사회과학·에너지·정책 → 기본 조합 유지 (openalex가 최광역)

### 2. 멀티소스 검색
- `search`를 키워드 조합별로 실행(보통 2~3회). 기본 소스는 openalex+crossref+s2.
- **S2 429는 정상 동작**(무키 공유풀 혼잡) — 스크립트가 자동 스킵하므로 재시도로
  시간 낭비하지 말 것.
- 결과가 빈약하면 키워드를 넓히고, 폭발하면 `--year-from`·구체어로 좁힌다.

### 3. 스크리닝 (관련성 판정)
- 후보 ~10건 이하: 제목·연도·인용수·게재지를 보고 직접 선별.
- 후보 수십 건 이상: **haiku 서브에이전트**에 후보 목록을 넘겨 관련성 O/X 판정만
  시킨다(크레딧 규율 — 상위 모델로 대량 스크리닝 금지).
- 선별 기준: 질문 적합성 > 인용수 > 최신성 > OA 여부. 리뷰 논문 1편을 앵커로.
- **커버리지 보강**(초기 검색이 빈약하거나 앵커 리뷰가 잡혔을 때): 앵커 DOI로
  `snowball --direction cites`(후속 연구)·`recommend`(의미 근접 논문)를 돌려
  키워드 검색이 놓친 문헌을 회수한다. recommend는 S2라 429면 snowball로 대체.

### 4. 정독
- 선별 3~8편에 `paper`로 초록 확보. 초록으로 부족하면 `oa`로 PDF URL을 얻어
  WebFetch/다운로드로 본문을 읽는다.
- **OA가 없는 논문은 초록+메타데이터만으로 다루고 본문 주장 인용 금지** —
  페이월 우회는 하지 않는다.

### 5. 합성 — 출처태그 루브릭 (강제)
- 답변의 **모든 사실 주장 문장 끝에 `[P#]` 태그**. 논문이 직접 말한 것만 태그를
  달 수 있다.
- 논문에서 추출한 사실(`[P#]`)과 내 해석·종합(`[해석]` 절 분리)을 물리적으로
  나눈다. 논문 간 상충은 숨기지 말고 병기한다.
- 초록만 읽은 논문은 태그 옆에 `(초록)` 표기. 답변 말미에 참고문헌 목록
  (`cite --style apa` 산출물 + DOI)을 붙인다.

### 6. 인용 기계검증 게이트 (통과 전 출력 금지)
- 참고문헌 **전수**에 대해 `verify <doi> "<내가 쓴 제목>"` 실행.
- MISMATCH·NOT_FOUND가 하나라도 있으면: 해당 인용을 제거하거나 올바른 DOI를
  재검색해 교정한 뒤 재검증. **검증 실패 인용이 남은 답변은 절대 내보내지 않는다.**
- 답변 말미에 검증 결과를 한 줄로 명시: `인용 검증: N/N MATCH`.

## vault 파일링 (mybrain 사용자 한정, 선택)
재사용 가치가 있으면(종합 비교·데이터 갭 해소) CLAUDE.md 규약대로 파일링:
`10_Inbox` 착지 → 프론트매터 5필드 + **데이터 신선도 표**(논문 연도≠데이터 연도
주의) + 위키링크 2개 이상 + index/관련 MOC 갱신. 일회성 답변은 파일링하지 않는다.

## 하지 말 것
- Google Scholar 스크레이핑(차단·불안정), Sci-Hub 등 페이월 우회.
- verify 없이 DOI·제목을 답변에 쓰는 것(환각 인용의 주 발생 경로).
- 대량 후보를 상위 모델 컨텍스트로 전부 읽는 것 — 스크리닝은 haiku, 정독은 선별분만.
- 중간 산출물을 안 남기고 긴 파이프라인을 이어가는 것 — 검색 결과·선별 목록을
  파일로 저장 후 진행(크레딧 소진 대비).
