---
name: scholar-research
description: >
  This skill should be used when the user asks for answers grounded in academic
  literature (학술 논문 근거 답변·문헌 리서치). Liner AI의 Scholar 모드를 무료 학술
  API(OpenAlex·Crossref·Unpaywall·arXiv·S2)와 구독제 Claude만으로 재현한다 — 질문을
  검색전략으로 분해해 멀티소스 검색·스크리닝·정독 후, 모든 주장에 [P#] 출처 태그를
  달고 이중 게이트(서지 기계검증: DOI·제목·저자·연도·철회 + 주장-근거 발췌 대조)를
  전수 통과한 답변만 산출한다(가짜 인용·내용 왜곡 방어).
  Korean triggers — "논문 근거로 답해줘", "학술 검색해줘", "문헌 리서치", "이 주제
  논문 찾아줘", "스칼라 모드", "출처 있는 답변으로", "인용 달아서 정리해줘",
  "선행연구 조사", "논문 테이블 만들어줘", "이 프로젝트에 논문 추가해줘/열 추가해줘",
  "지난 리서치 이어서", "논문 피어 리뷰해줘", "인용 그래프 그려줘", "BibTeX/RIS로
  내보내줘", "/scholar-research". English triggers — "answer with academic
  sources", "literature search", "find papers on", "scholar mode", "cite sources".
  DO NOT trigger for 일반 웹 리서치(→ WebSearch/insane-research), 투자 종목·산업
  분석(→ equity-deep-dive/industry-analysis), 이미 가진 PDF의 번역·요약(→
  pdf-translate), 단순 사실 확인.
---

# scholar-research — 출처 있는 학술 답변 파이프라인

모든 데이터 접근은 `scripts/scholar.py`(표준 라이브러리만, 무료 API만) 하나로 한다.
`S=~/.claude/skills/scholar-research/scripts/scholar.py`

```
python3 $S search "query" [--limit N] [--sources openalex,crossref,s2,arxiv,pubmed,epmc] [--year-from Y] [--oa-only] [--include-retracted] [--json]
                                  # 소스 병렬 호출(2026-09-15, 3소스 6.3s→2.9s). 랭킹은 질의-제목 겹침(overlap) 우선·인용수 보조
python3 $S bundle "query" [--limit N=10] [--top K=8] [--sources ...] [--year-from Y] [--oa-only] [--recent-slots N=2] [--include-retracted] [--out f.json] [--json]
                                  # 원샷(2026-09-15): 검색(병렬)→DOI dedup→랭킹→상위 K편 초록 병렬 조회→JSON+요약표.
                                  # 초록 체인 openalex→crossref→s2 단일논문(제목 대조 후 채택, 편당 최대 3초 — 무키 공유풀 간격).
                                  # 상위 K 중 최소 --recent-slots 편은 최근 5년 논문으로 예약(★recent-slot 표시, counts.recent).
                                  # 철회 논문은 결과 맨 뒤 + 상위 초록 슬롯에서 제외(--include-retracted면 기존 위치).
                                  # 머리줄이 §5 답변 과정 블록 그대로. arXiv 항목은 초록 미조회+pdf URL 안내(§4).
                                  # citations_s2는 S2 검색행 값(429면 null) — 정확값은 project add/recount.
                                  # `project add <slug> --from bundle.json --pick P1,P3 --no-fetch` 호환. exit 0/1(0건 — 소스오류 유무는 머리줄·JSON errors로)/2(--sources 이름 오류). search도 동일 계약
python3 $S paper <doi>            # 상세+초록(inverted index 복원)
python3 $S oa <doi>               # 합법 OA PDF 위치 (Unpaywall)
python3 $S cite <doi> --style apa|mla|chicago
python3 $S verify <doi> "인용한 제목" [--author 1저자성] [--year YYYY]
                                  # MATCH(0)/NO_DOI·RETRACTION_NA(2 — 수동 확인 후 유지 가능한 예외)/
                                  # NOT_FOUND·MISMATCH·MISMATCH_META·NON_ARTICLE(3)/RETRACTED(4)/
                                  # --year는 ±1년 허용(online-first) — batch 행에 실제 연도가 찍히면 교정
                                  # UNVERIFIED·RETRACTION_UNCHECKED(5 — 미검증)/BAD_ENTRY(6)
python3 $S verify-batch refs.json # 참고문헌 전수 검증 — exit 계약은 §6 표 참조
python3 $S snowball <doi> --direction cites|refs [--year-from Y] [--sort citations|date] [--limit N] [--query 키워드]  # N>200은 200으로 클램프(OpenAlex per-page 상한). refs는 100건 단위 전수 배치 조회. --query=주제 부분집합 우선 회수(§3)
python3 $S recommend <doi>       # S2 유사논문 추천(의미 근접, snowball 보완). arXiv 앵커는 10.48550/arXiv.<id>·arXiv:<id> 둘 다 자동으로 S2 arXiv: 형식으로 조회
python3 $S project init|list|add|recount|rm|col|set|show|log|refs|render|export-db|import-db ...   # §7 프로젝트 모드(export/import-db는 §7.5)
python3 $S data fred <ID>|worldbank <IND> --country US,KR [--start --end] [--out f.csv]   # §10.1 [W#] 시계열 수집(무키)
python3 $S data list|show W1 [--tail N] [--stats]|verify [W1 ...]|vintage [W1 ...]    # ledger·검산·신선도 표. verify exit 0/2/3/5
```
**로컬 캐시**(2026-09-15): DOI별 메타·초록을 sqlite(`~/.cache/scholar-research/cache.db`, env `SCHOLAR_CACHE`로
경로 변경)에 30일 캐시한다. 대상은 `paper`·`bundle` 초록·`verify` 서지(PMID 매핑·DataCite)·`project add/recount`이며
`search` 결과는 캐시하지 않는다(신선도). 히트는 출력에 `(cache)`로 표시. 끄기: `--no-cache` 또는 `SCHOLAR_NO_CACHE=1`.
**철회 판정은 캐시하지 않는다** — 히트여도 OpenAlex `is_retracted`·PubMed efetch·Crossref는 매번 실 조회(철회는 늦게
반영되므로). `citations`는 최대 30일 stale일 수 있으니 정확값은 `project recount`. 초기화는 DB 파일 삭제.

미인식 `--sources` 이름(오타·대문자·쉼표 뒤 공백)은 **exit 2로 거부**된다 — 조용히
빠진 소스 때문에 "문헌 없음"으로 오판하지 않게 한 것이므로, 에러가 나면 이름을 고쳐
재실행한다. `--limit`은 소스별 per-page 상한(openalex 200·s2 100 등)으로 자동 클램프.

env(선택): `SCHOLAR_EMAIL`(polite pool), `OPENALEX_API_KEY`, `S2_API_KEY`.
**OpenAlex는 2026-02부터 사용량 과금제**(단건 조회 무료 · 필터 $0.10/1,000 · 검색 $1/1,000).
익명 $0.10/일(검색 ~100회), 무료 키 $1/일. **결제수단 미등록이면 돈은 안 나가고 요청이 거부될
뿐**이다. 한도·속도 초과는 둘 다 429 → 스크립트가 stderr에 `⚠️ OpenAlex HTTP 429 — 일일 한도…`
를 1회 크게 찍는다. **이 경고 뒤의 openalex 0건은 '문헌 부재'가 아니다** — 다른 소스 결과로만
판단하고, 키를 넣거나 다음 날 재실행한다.

## 파이프라인 (6단계 — 순서 고정)
분기점 지도: `references/pipeline-flowchart.md`(전체 흐름·게이트 exit 분기·캐시/exit 계약 표). 정본은 이 문서.

### 1. 질문 분해 → 검색전략
- 질문을 **영문 학술 키워드 2~3개 조합**으로 변환한다(동의어·학술용어 치환:
  "우라늄 ISR 회수율" → "in-situ recovery uranium extraction efficiency").
  **범용 토큰('large language model'·'deep learning'·'machine learning' 등)을 질의에
  덧붙이지 않는다** — 핵심 구문 + 한정어 1~2개로 짧게. 랭킹의 질의-제목 겹침은 토큰
  동일 가중이라 범용 토큰 3개만 맞은 서베이가 overlap 0.5(중립)로 인용수만으로 초록
  슬롯을 차지한다(실측: 'speculative decoding large language model inference'에서
  서베이 3~4편이 슬롯 점유, 원논문 Leviathan 2022는 슬롯 밖). **overlap 0.5 동점 행이
  초록 슬롯을 차지하면 질의를 좁혀 재실행**한다.
- 시효성 주제면 `--year-from`을 걸어라.
- **분야 라우팅** — 기본 소스는 openalex,crossref,s2. 질문 분야에 따라 추가:
  - 의학·생명과학·임상 → `--sources`에 **pubmed** 추가 (PubMed·arXiv는 인용수
    미상 `인용 ?`로 나오므로 랭킹은 openalex 쪽 수치로 판단. PMC 있으면 OA 표시됨)
  - CS·물리·수학 등 프리프린트 문화권 → **arxiv** 추가
  - 경제·사회과학·에너지·정책 → 기본 조합 유지 (openalex가 최광역)
  - **기법·장비·시약·데이터셋·지역명처럼 제목·초록이 아니라 본문(Methods/Results)에만
    등장할 조건 → `epmc` 추가**. 다른 소스는 전부 제목·초록 색인 검색이라 이런 질의는
    리콜이 무너진다(Europe PMC는 전문 색인 + 프리프린트(PPR) 포함, PubMed 상위집합).
    섹션 한정 구문도 그대로 쓸 수 있다: `search 'METHODS:"Sysmex XN-1000" AND anemia' --sources epmc`.
    단 epmc 결과에는 DOI 없는 학회초록이 섞인다 — **단발 모드**에서는 §6 NO_DOI 경로로
  처리하고, **프로젝트 모드(§7)에서는 등록 자체가 불가**(`project add`가 DOI·arXiv id 없는
  항목을 거부)하므로 `search --sources crossref,openalex "제목"`으로 DOI를 먼저 확보하거나,
  못 찾으면 근거에서 제외한다(§7.2 4단계).

### 2. 멀티소스 검색
- **기본은 `bundle`**(2026-09-15) — 검색·dedup·상위 K편 초록까지 한 번에 받아 §3·§4의
  모델 턴을 줄인다. `--out`으로 저장한 JSON이 §3 전역 P# 표·§5 답변 과정 블록의 원본이다.
  키워드 조합이 여럿이면 `bundle`을 조합별로 돌리고 §3에서 DOI 기준으로 합친다.
  `search`는 단일 소스 확인(arxiv·pubmed 단독)이나 `--json` 원본이 필요할 때만.
- `search`를 쓰는 경우(위 예외 경로)에도 키워드 조합별로 실행한다(보통 2~3회). 기본 소스는
  openalex+crossref+s2. `search --json`에는 `counts`·`elapsed_sec`가 없으므로 §5 답변 과정
  블록의 숫자는 `bundle` JSON에서만 나온다 — bundle을 돌린 뒤 search를 다시 돌리지 않는다.
- **랭킹은 관련도 우선·인용수 보조**(2026-09-15 Liner 비교 round4 교훈): 질의 토큰이 제목에
  하나도 없는 행(`overlap` 0)은 인용수와 무관하게 하위 티어로 내려간다 — 인용수 가중이 무관
  고인용 논문('food regime genealogy'·'Assetization')을 상위에 올려 bundle을 6회 반복하게 했던
  사고. `search --json`·bundle 행의 `overlap`(0~1)로 확인. 겹침 0.4 이상부터는 인용수가 다시
  주도한다. 제목에만 없고 초록에 질의어가 있는 논문은 내려갈 수 있으니 결과가 빈약하면
  키워드 조합을 바꿔 재검색한다(§1).
- **철회 논문은 결과 맨 뒤로 보내고 상위 초록 슬롯에서 제외**한다(은닉하지 않는다 — ⚠️RETRACTED
  표시 유지). 철회 사실 자체를 다뤄야 하면 `--include-retracted` 또는 `paper <doi>`.
- OpenAlex가 book-review·paratext·erratum·retraction·supplementary-materials로 분류한 레코드는
  검색단에서 제외된다(2026-09-15 — 도서관용 서평이 책의 인용수를 흡수한 채 [P6]으로 랭크됐던 사고).
- **S2 429는 정상 동작**(무키 공유풀 혼잡) — 스크립트가 자동 스킵하므로 재시도로
  시간 낭비하지 말 것.
- 결과가 빈약하면 키워드를 넓히고, 폭발하면 `--year-from`·구체어로 좁힌다.
- 랭킹은 **인용수 가중 + 소스 relevance 보너스** — 그래도 질의 적합도보다 인용수가
  세다. 인용수 미상(`인용 ?`: arxiv·pubmed)·미인용 최신 논문은 하위로 밀린다.
  **epmc는 citedByCount를 제공하므로**(실측 표본 100% 커버리지, 프리프린트 포함)
  랭킹·스크리닝 판단에 그대로 쓸 수 있다 — 단 Europe PMC 자체 집계라 openalex
  수치와 다를 수 있다.
  스크립트가 (a) 인용수 미상, (b) 각 소스 relevance 상위 항목에 절단 슬롯을
  예약하지만, **랭킹 하위 = 관련성 하위가 아니다** — 상위만 보고 판단하지 말고
  최신성이 핵심인 질문이면 `--sources arxiv`(또는 pubmed) 단독 검색으로 별도로 훑어라.
- `--year-from`은 **arxiv 포함 전 소스**에 적용된다(arXiv는 submittedDate 범위 필터,
  실패 시 클라이언트측 재필터). arXiv에서 `--year-from` 적용 후 0건이면 stderr에
  '날짜 절 미적용 가능' 경고가 뜬다 — 이 경우의 0건은 **'문헌 부재'가 아니다**.
- **arXiv 질의는 토큰별 AND로 조인해 보낸다** — arXiv API는 공백을 암묵 OR로
  파싱해서(전 소스 중 arXiv만 OR) 다중 키워드 질의의 정밀도가 붕괴했다. 스크립트가
  자동 처리하므로 키워드는 평소대로 넣으면 되고, 직접 `AND`/`OR`/따옴표를 쓴
  질의는 그대로 전달된다.
- `--oa-only`는 병합 후 **전 소스**에 is_oa 필터를 건다 — OA 정보가 없는
  crossref 단독 결과는 제외되므로, 리콜이 중요하면 켜지 말고 §4에서 `oa`로 판별.

### 3. 스크리닝 (관련성 판정)
- 후보 ~10건 이하: 제목·연도·인용수·게재지를 보고 직접 선별.
- 후보 수십 건 이상: **haiku 서브에이전트**에 후보 목록을 넘겨 관련성 O/X 판정만
  시킨다(크레딧 규율 — 상위 모델로 대량 스크리닝 금지).
- 선별 기준: 질문 적합성 > 인용수 > 최신성 > OA 여부. 리뷰 논문 1편을 앵커로.
- **최근 실증 확보**: bundle은 상위 K 초록 중 최소 2편(`--recent-slots`, 기본 2)을 최근 5년
  논문으로 예약한다(round4에서 2015년 이후 실증을 전혀 회수 못 한 교훈). 예약된 행은 요약표에
  `★recent-slot`, JSON에 `recent_reserved: true`. 시효성 주제면 `--year-from`과 함께 쓴다.
- **커버리지 보강**(초기 검색이 빈약하거나 앵커 리뷰가 잡혔을 때): 앵커 DOI로
  `snowball --direction cites`(후속 연구)·`recommend`(의미 근접 논문)를 돌려
  키워드 검색이 놓친 문헌을 회수한다. recommend는 S2라 429면 snowball로 대체.
  **유명 앵커(수천 인용)면 `--query "키워드"`로 좁혀라** — cites는 --limit 200으로
  절단되는데 인용수·날짜 정렬은 주제 축과 직교라, RAG 원논문(인용 3,060) 후속 중
  환각 주제 301건이 상위 200에 22~23건만 들어왔다. `--query`는 OpenAlex
  `title_and_abstract.search`를 cites/refs 필터에 결합해 **관련 부분집합을 우선 회수**한다
  (전수 회수가 아니다 — 키워드 없이 다루는 후속 논문은 여전히 못 잡는다).
  `snowball --direction refs`는 **기본**이 인용수 내림차순이다(참고문헌 100건
  초과 시 100건 단위로 **전부** 배치 조회한 뒤 클라이언트에서 정렬·`--limit` 적용 —
  stderr에 배치 수 표시. 2026-09-15 전까지는 앞 100건만 조회해 앵커 리뷰의 최신
  선행연구 62%가 조용히 탈락했다). `--sort date`·`--year-from`은 refs에도
  cites와 **동일하게 적용**되므로, 앵커가 딛고 선 최신 선행연구를 찾을 땐
  `--direction refs --sort date`를 걸어라(기본 정렬로는 5~20년 전 고전만 나온다).
- **cites는 기본이 인용수순이라 최근 후속연구가 안 나온다** — 유명 앵커면 상위
  10건이 전부 5~10년 전 논문이 된다. 최신 후속연구를 회수하려면
  `snowball <doi> --direction cites --year-from 2025`(고신호 최근분) 또는
  `--sort date`(최신순)를 함께 걸어라.
- **P# 전역 재부여(필수)**: `search`는 실행마다 [P1]부터 다시 매기므로 검색을
  2회 이상 하면 서로 다른 논문이 같은 번호를 단다. 스크리닝 확정 직후 선별
  논문에 **DOI 기준 전역 P#를 재부여한 표**를 만들고, 이후 모든 단계(§4 ledger·
  §5 태그·§6 refs.json)는 검색 출력이 아니라 **그 표만** 참조한다.

### 4. 정독 + 근거 발췌 (evidence ledger)
- **기본은 초록 전용**(2026-09-15 — 속도 규약). 선별 3~8편은 `paper`(또는 `bundle` JSON의
  `abstract`)로 초록만 확보하고 `(초록)` 태그로 인용한다. bundle 초록은 openalex→crossref→
  **s2 단일논문** 순으로 채운다(`abstract_source`에 표시. S2는 제목이 행과 일치할 때만 채택 —
  round5 실측 상위 5편 채움 2/5→4/5). 셋 다 없으면 `abstract_reason`대로 `oa`로 PDF 위치 확인. **본문 정독은 2~3편 상한**, 다음
  중 하나에 해당하는 논문만: ①앵커 리뷰 ②답변의 핵심 수치·결론이 나오는 논문 ③논문 간
  상충의 당사자. 정독은 `oa`로 PDF URL을 얻어 WebFetch/다운로드로 읽는다. 정독 대상과
  이유를 ledger 머리에 한 줄씩 적는다(답변 과정 블록의 "정독 m건"이 여기서 나온다).
  Liner 실측(`references/liner-observed-20260915.md`)도 80건 중 정독 5건이었다 — 정독 수가
  아니라 근거 발췌의 정확도가 품질을 만든다.
- **arXiv 항목은 `paper`·`oa`로 읽지 않는다** — `paper`는 OpenAlex 단일 소스인데
  `10.48550/*` DOI에 **404가 아니라 오염된 레코드**(다른 논문의 제목·초록, 실측 5건 중
  3건 — Wei 2022 CoT가 'BNAI, NO-TOKEN…'으로)를 exit 0으로 돌려주던 것을 2026-09-15에
  막았다: 스크립트가 arXiv API로 제목을 재확인해 불일치면 `⚠ 제목 오염 의심`+초록
  미출력+exit 3, 재확인 실패면 `⚠ arXiv 재확인 실패`+exit 5(미확인 — 초록을 ledger에
  넣지 말 것). `oa`는 Unpaywall이라 DOI 없는 항목은 조회 자체가 불가다. arXiv 검색
  결과(doi=None이 다수, 식별자는 abs URL)는 **abs URL을 `/abs/`→`/pdf/`로 바꿔
  WebFetch로 직접 읽는다**(합법 OA — 페이월 우회 아님). `verify`는 arXiv DOI에 대해
  DataCite 정본 제목을 함께 대조하므로 오염 제목을 인용하면 MISMATCH, 정본 제목이면
  MATCH다.
- **OA가 없는 논문은 초록+메타데이터만으로 다루고 본문 주장 인용 금지** —
  페이월 우회는 하지 않는다.
- `paper`가 `⚠️ RETRACTED`를 표시하면 그 논문은 근거로 쓰지 않는다(철회 사실
  자체를 다루는 경우만 예외, 반드시 "철회됨" 명기).
- **읽으면서 evidence ledger를 만든다**: 인용할 주장마다 그 주장을 지지하는
  원문 문장(초록/본문)을 **그대로 발췌**해 작업파일에 기록
  (`P# | DOI | 발췌문 | 초록/본문 여부` — DOI 열 필수: P#만 있으면 검색 실행 간
  번호 충돌 시 발췌가 다른 논문에 귀속될 수 있다). 발췌를 못 뜨는 주장은 그
  논문의 주장이 아니라는 뜻 — [P#] 태그를 달 수 없다.

### 5. 합성 — 출처태그 루브릭 (강제)
- 답변의 **모든 사실 주장 문장 끝에 `[P#]` 태그**. 논문이 직접 말한 것만 태그를
  달 수 있다 — 판정 기준은 4단계 ledger에 대응 발췌가 있는가. 없으면 태그를
  떼고 `[해석]` 절로 강등한다.
- 논문에서 추출한 사실(`[P#]`)과 내 해석·종합(`[해석]` 절 분리)을 물리적으로
  나눈다. 논문 간 상충은 숨기지 말고 병기한다.
- 초록만 읽은 논문은 태그 옆에 `(초록)` 표기. 답변 말미에 참고문헌 목록
  (`cite --style apa` 산출물 + DOI)을 붙인다.
- **답변 과정 블록(필수, 답변 최상단)** — Liner의 "답변 과정" 재현(2026-09-15). 숫자는
  `bundle` JSON의 `counts`·`elapsed_sec`와 §3 전역 P# 표·§4 ledger에서 그대로 옮긴다
  (기억으로 적지 않는다). 형식:
  ```
  답변 과정 · 검색 openalex 14 / crossref 15 / s2 12 → 병합 31 (2.4초)
  · 스크리닝 8편 채택(P1~P8) · 정독 2편(P1 앵커 리뷰, P4 핵심 수치) · 나머지 초록
  · 게이트: [§6 결과줄 그대로 — 검증 전 초안이면 "⚠️ 검증 전"]
  ```
- **관련 질문 3개(답변 말미)** — 답변에서 근거가 **부족했던 지점**(발췌를 못 뜬 주장,
  `[해석]`으로 강등된 문장, 상충이 남은 쟁점)에서 뽑는다. 형식은 Desk 앱과 동일하게
  `===추천질문===` 아래 3줄. 답변이 이미 다룬 내용을 되묻는 질문은 금지.
- **시각물 규칙**(표·차트·mermaid) — 수치는 **ledger에 발췌가 있는 것만** 그린다. 보간·
  추정·외삽한 점은 점선/`(추정)` 표시 + 캡션에 출처 `[P#]`와 "보간" 명기. 본문이
  "데이터 없음"이라 말하면서 차트는 수치를 그리는 **본문-시각물 불일치는 금지**.
  반례: Liner가 World Bank "2026년 16% 상승 전망" 한 줄을 분기 선형보간해 "예측 지수
  100→116"으로 그린 것(`references/liner-observed-20260915.md` §2).

### 6. 이중 검증 게이트 (통과 전 출력 금지)
**지연 실행 예외(2026-09-15)** — 사용자가 기다리는 시간을 줄이기 위해 **"검증 전 초안"을
먼저 보일 수 있다.** 조건: ①초안 첫 줄과 답변 과정 블록에 `⚠️ 검증 전 초안 — 인용은 아직
기계검증·대조 전` 표시 ②초안의 `[P#]` 문장은 **12개 상한**(대조 비용을 예측 가능하게)
③초안을 낸 직후 같은 턴 또는 바로 다음 턴에서 게이트 A·B를 돌리고, 결과줄을 덧붙이며
표시를 제거한다 ④게이트에서 탈락한 인용은 초안에서도 지우고 "초안 대비 변경: [P3] 제거,
[P5] 약화" 를 결과줄 옆에 적는다. **표시를 뗀 답변만 vault 파일링·프로젝트 log 대상**이다.

**게이트 A — 서지 기계검증** (가짜 인용·오기재·철회 방어):
- 먼저 **개수 3중 대조**: §3 전역 P# 표의 행 수 == `refs.json` 항목 수 ==
  답변에 등장한 **고유 [P#] 개수**. 하나라도 어긋나면 출력 금지 — 일부만 등재하면
  "2/2 MATCH"가 전수 검증처럼 보인다(빈 배열은 exit 2로 거부된다).
  대조는 **고유 DOI 수 기준**이다 — 같은 논문에 여러 [P#]을 붙이면 행 수는 맞아도
  독립 출처는 1편이라 근거 폭이 부풀려진다(`verify-batch` 요약줄이 `고유 DOI n/N`과
  `⚠ 중복 DOI`로 표시한다). 중복이면 [P#]을 하나로 통합하거나 게이트 B에서 소명한다.
  DOI는 `doi:` 접두·후행 마침표를 벗겨 정규화하므로 표기만 다른 같은 DOI도 중복으로 잡힌다.
- **프로젝트 모드(§7)의 3중 대조**: refs.json은 `project refs <slug> --pids <답변의 고유
  P# 목록>`으로 만든다 — 레지스트리 전체가 아니라 **이번 답변이 인용한 P#만** 내보내야
  항목 수 == 답변의 고유 [P#] 수가 성립한다(2회차 이후 등록 논문 일부만 인용하는 것이
  정상). 레지스트리에 없는 P#를 넘기면 exit 2 = 미등록 인용(환각 경로). 결과줄 뒤에
  `· 레지스트리 N편 중 답변 인용 k편 검증`을 병기한다.
- 참고문헌 전수를 `refs.json`(`[{"id","doi","title","author","year"}]` —
  **author=1저자 성, year 필수**. 비우면 그 항목의 저자·연도 대조가 통째로
  생략되므로 스크립트가 `INCOMPLETE_META`로 막는다. 필드를 지워 게이트를
  통과시키는 것은 규약 위반)으로 만들어 `verify-batch refs.json` 실행.

  | verdict | exit | 조치 |
  |---|---|---|
  | MATCH | 0 | 통과 |
  | MISMATCH / NOT_FOUND | 3(단건 verify도 3) | 제목 왜곡·가짜 DOI — 인용 제거 또는 올바른 DOI 재검색 후 교정 |
  | MISMATCH_META | 3 | 저자·연도 오기재 교정. **연도는 ±1년 허용(online-first)** — MATCH라도 행에 `(연도: 인용 2016 / 실제 2015 …)`가 찍히면 refs.json·본문 산문의 연도를 실제값으로 교정한다(참고문헌은 `cite` 산출물이라 이미 실제 연도) |
  | NON_ARTICLE | 3 | 심사보고서(peer-review)·그림 component·grant DOI, **정오표·corrigendum·erratum 통지**, **철회 공고문(retraction notice)** — 논문이 아니다(연구 결과가 0건). 인용 제거하고 **원논문 DOI로 교체**. 철회 공고문이면 아래 "철회 논문 서술" 절차로 |
  | RETRACTED | 3(단건 4) | 인용 제거. **철회 자체를 논하려면 refs.json에서 빼고** 아래 "철회 논문 서술" 절차를 따른다 — refs에 남긴 채로는 게이트를 통과할 수 없다 |
  | UNVERIFIED | 5 | 네트워크 장애 — **인용 제거 금지**, 재시도 |
  | RETRACTION_UNCHECKED | 5 | 철회 판정 소스 미응답 — 철회 검사가 **안 된 것**이므로 재시도 |
  | RETRACTION_NA | 2 | DataCite 전용 DOI(`10.48550/arXiv.*`·Zenodo) — 철회 플래그가 **구조적으로 없어** 재시도가 무의미. 아래 예외 경로 |
  | INCOMPLETE_META | 6 | refs.json에 author·year 채워 재실행 |
  | BAD_ENTRY | 6 | refs.json 스키마 위반(항목이 객체가 아님·title 누락·year에 4자리 연도 없음) — 항목 수정 후 재실행 |
  | NO_DOI | 2 | 아래 예외 경로 |
  | (빈 refs.json) | 2 | 참고문헌 누락 — 개수 3중 대조부터 다시 |
  | (refs.json 부재·JSON 문법 오류) | 6 | 요약줄 없이 `ERROR: refs.json 읽기/파싱 실패`만 — 경로·후행 쉼표 등 수정 후 재실행 |

  실패 유형이 섞이면 **더 엄한 코드가 우선**한다(3 > 5 > 2 > 6). 특히 exit 2는
  **실패 전원이 예외 유형(NO_DOI·RETRACTION_NA)일 때만** 나온다 — 여기에
  INCOMPLETE_META·BAD_ENTRY가 섞이면 6이다(예외 경로를 타고 미검증 인용이
  통과하지 않게).
- **통과 조건**: `exit 0`. 단 **실패가 전부 예외 유형(NO_DOI·RETRACTION_NA)일 때
  (exit 2)만** 예외 — 아래 둘 중 해당 절차를 밟고 그 근거를 ledger에 기록한 뒤
  인용을 유지할 수 있다(출력 목록에서 다른 실패 유형이 0건임을 눈으로 확인할 것).
  **이 예외 외에는 실패 인용이 남은 답변을 절대 내보내지 않는다.**
  - `NO_DOI` → **단발 모드에서만 유효한 경로다**(프로젝트 모드는 DOI 없는 항목을 등록 자체가
    못 하므로 §7.2 4단계). arXiv 프리프린트면 `search --sources arxiv "제목"`, epmc 학회초록이면
    `search --sources epmc,pubmed "제목"`으로 제목·저자·연도를 수동 대조.
  - `RETRACTION_NA` → 서지(제목·저자·연도)는 이미 DataCite로 대조됐다. 답변에
    "철회 검사 미수행(DataCite 전용 DOI)"을 명시하고 확신도를 낮춘다.
- **철회 논문 서술 절차** (백신-자폐, 줄기세포 STAP 등 "왜 이 논쟁이 시작됐나"를
  다룰 때 필요. 실사용 발견 2026-07-28 — 이 절차가 없어서 게이트 A가 원리적으로
  통과 불가능했다):
  1. 철회 논문을 `refs.json`에서 **뺀다**. refs.json은 "근거로 쓴 문헌" 목록이고
     철회 논문은 근거가 아니다 — 남겨두면 exit 3으로 영구 교착에 빠진다.
  2. 본문에서는 `[P#]`가 아니라 **`[철회됨]` 태그**로 인용하고, 철회 사실·철회
     연도·철회 사유를 함께 적는다. 그 논문의 **주장 내용을 근거로 쓰지 않는다**
     (쓸 수 있는 것은 "그런 주장이 제기됐고 무효화됐다"는 사실 자체뿐).
  3. 철회 사실의 출처는 **원논문 DOI를 단건 `verify`로 조회한 결과**(RETRACTED
     판정 + 철회 소스)를 쓴다. 그 출력을 ledger에 붙인다. Elsevier식 원논문(update-to가
     자기 DOI를 가리킴)도 RETRACTED로 판정된다(2026-09-15 전에는 통지문으로 오판해
     NON_ARTICLE이 나와 이 절차가 교착했다).
  4. **철회 공고문(retraction notice) DOI를 refs.json에 넣지 말 것** — 공고문은
     연구 결과가 없어 `NON_ARTICLE`(exit 3)로 막힌다. 공고문의 철회 사유 문구를
     인용하고 싶으면 본문에 `[철회공고]`로 직접 인용하고 refs에서는 뺀다.
  5. 요약줄에 `· 철회 논문 k건(근거 제외, [철회됨] 서술)`을 덧붙여 은폐가 아님을 밝힌다.
- ⚠️ **DOI를 지워 NO_DOI로 만들어 예외를 타는 것은 규약 위반이다** — arXiv 전용
  DOI는 정직하게 기재해야 `RETRACTION_NA`로 서지 대조까지 받는다. 필드 삭제는
  검증을 늘리는 게 아니라 없앤다(author·year 삭제 금지와 같은 취지).
- UNVERIFIED·RETRACTION_UNCHECKED가 반복 실패하면 해당 인용에 "기계검증
  미완(네트워크)" 또는 "철회 검사 미수행"을 답변에 명시하고 확신도를 낮춘다.
  arXiv 전용 DOI(`10.48550/arXiv.*`)는 Crossref에 없어 DataCite로만 확인되므로
  `RETRACTION_NA`가 정상이다 — **가짜 인용이 아니다**. NOT_FOUND는 세 서지
  소스(Crossref·OpenAlex·DataCite)가 모두 404일 때만 나온다 — 이때만 '가짜 인용' 신호다.
- **철회 판정 소스는 Crossref(update-to)+OpenAlex(Retraction Watch)+PubMed
  (Publication Type)** 셋이다. 요약줄의 `철회 0건(판정 소스: crossref n/N+…)` 표기를
  그대로 옮겨 커버리지 한계를 남긴다 — 소스별 `n/N`은 그 소스가 실제로 판정에 응답한
  인용 수다. PubMed는 생의학 한정이라 타 분야 인용은 두 소스 판정뿐이다.
- `✓ MATCH`라도 `sim`이 1.0 미만이면 출력에 **실제 제목이 잘리지 않고 전부** 찍힌다.
  스크립트가 토큰 검사로 잡는 것: 부정어 삭제·핵심어 치환·제목 확장(모집단·연구설계
  덧붙이기)·**식별자 치환**(BNT162b1↔b2, BRCA1↔2 등 숫자 붙은 토큰)·**대립 접두
  치환**(hyper↔hypo, pre↔post, male↔female, bi↔uni, intra↔inter, over↔under,
  ketamine↔esketamine — `token-substitution`)·**의학 접미 치환**(pneumonia↔pneumonitis,
  hepatic↔hepatitis, gastric↔gastritis — -itis/-osis/-oma/-emia가 한쪽에만 붙은 이웃어는
  철자 변형으로 흡수하지 않는다, `token-substitution`)·**시점·범위 극성 반전**(before↔after,
  over↔under, during↔after, above↔below, within↔beyond — 기능어라도 양쪽이 맞바뀌면
  `polarity-inversion`)·**결론 강도 표지 삭제**(weak·limited·
  questionable·modest·overestimated·myth·revisited… — `hedge-omission`)·**머리·중간의
  한정어 삭제**(`token-omission` — 제목 앞의 내용어를 지운 인용도 걸린다).
  **부제(':' 뒤)의 부정어·hedge 절단은 기계 차단이 아니라 경고다** — 정당한 주제목 인용
  (`…: why deterrence does not work`)과 결론 삭제(`…: lack of efficacy`·`…: limited evidence`)가
  형태상 같아 MATCH로 두되, 행에 `⚠ 부제의 결론 극성 표지 생략: lack …`(JSON `polarity_omitted`)
  이 찍힌다. **이 경고가 있으면 결론 방향이 뒤집혔는지 원제목으로 확인하고 부제까지 인용한다.**
  **기계검증이 못 잡는 잔여 경로는 꼬리 절단형 범위 축소뿐이다** — 정당한 축약
  인용(`…prediction` ⊂ `…prediction with AlphaFold`)과 형태가 같아 구분이 불가능하다.
  그래서 절단으로 통과한 행에는 `⚠ 생략: <토큰들>`이 함께 찍힌다. **생략 목록에
  모집단·결과지표·조건·결론 강도 표지가 들어 있으면**(예: `생략: cancer, incidence`)
  특정 조건 연구를 일반 결론으로 승격시킨 것이므로 인용을 원제목으로 고친다.
  `≈ 표기변형: tumour→tumor`는 생략이 아니라 흡수된 철자 변형이다 — 육안 대조에서
  의미가 같은지만 본다.
  표시된 실제 제목은 **눈으로 끝까지 대조**하되, 차이가 꼬리에만 있다고 가정하지
  말 것 — 식별자 치환은 문자열 중간 1글자다.

**게이트 B — 주장-근거 대조** (내용 왜곡 방어 — 실재하는 논문에 그 논문이 안 한
말을 붙이는 것은 게이트 A가 못 잡는다):
- 답변의 `[P#]` 문장 각각을 4단계 evidence ledger의 발췌와 1:1 대조한다.
  발췌가 주장을 지지하지 않으면(범위 과장·조건 누락·수치 반올림 왜곡 포함)
  문장을 발췌 수준으로 약화하거나 태그를 뗀다.
- **M의 정의: 태그가 붙은 문장 수이지 논문 수가 아니다**(실사용 발견 2026-07-28 —
  논문 7편을 근거로 20문장을 쓰고 `7/7 지지`라 적으면 13문장이 대조를 안 받은
  채 통과한다). 한 문장에 `[P3][P5]`처럼 태그가 둘이면 **쌍 단위로 2건**을 센다.
  세는 법: 답변에서 `[P#]`가 등장하는 문장을 전부 나열 → 각 문장×태그 쌍마다
  ledger 발췌를 붙임 → 분모 M = 그 쌍의 총수, 분자 = 지지된 쌍의 수.
  M이 refs.json 항목 수와 같아지면 논문 수를 센 것이 아닌지 의심하라.
- [P#] 문장이 15개를 넘으면 대조를 **haiku 서브에이전트**에 위임한다
  (문장+발췌 쌍 목록을 넘겨 지지/불지지 판정만 받기).

- 답변 말미에 두 게이트 결과를 한 줄로 명시 — `verify-batch` 요약줄을 **한 글자도
  고치지 말고 그대로 붙인 뒤** 게이트 B 결과만 잇는다. 요약줄의 소스별 `n/N`,
  꼬리 항목(NO_DOI·미검증·철회검사 불가·중복 DOI 등)을 **요약하거나 지우지 말 것** —
  `pubmed 3/10`을 `pubmed`로 줄이면 커버리지가 과대표시된다. 형태:
  `인용 검증: N/N MATCH · 철회 0건(판정 소스: crossref n/N+openalex n/N+pubmed n/N)
  · 고유 DOI N/N [· 요약줄의 꼬리 항목 전부] · 주장-근거 대조 M/M 지지`.

## 7. 프로젝트 모드 — 논문 테이블 + 질문 이력 (Liner "프로젝트" 재현, 2026-09-05 신설)

한 주제로 질문이 **두 번 이상** 이어지거나 논문이 **5편 이상** 쌓이면 프로젝트로 전환한다.
일회성 질문 하나는 프로젝트를 만들지 않는다(§1~6만으로 끝낸다).

### 7.1 구조 (파일은 vault 밖, 표는 vault 안)
- 레지스트리: `$SCHOLAR_HOME/<slug>/`(기본 `~/Documents/mybrain_raw/scholar_projects/`)
  - `project.json` — **P# 전역 레지스트리**(DOI 기준 영구 고정) + 열 정의 + 셀 + 질문 이력
  - `ledger.md` — §4 evidence ledger(프로젝트 공용, 회차마다 append)
  - `refs.json` — `project refs`가 레지스트리에서 생성 → `verify-batch` 입력
  - `bundle_*.json` — 회차별 검색 원본(`bundle --out …` 기본; `search --json`은 단일 소스 확인 때만)
- vault(mybrain 사용자): `40_Resources/03_리서치/학술프로젝트/<주제>/`
  - `<주제>_프로젝트.md` — 허브 노트(type: index). 질문 이력·답변 노트 링크·미해결 질문
  - `<주제>_논문표.md` — `project render --out`의 출력. **손으로 고치지 않는다**(다음 render가 덮어씀)
  - 답변 노트 — 회차별 `<주제>_<소주제>_YYYY-MM.md`(type: research, 기존 규약)

### 7.2 절차
1. `project init <slug> --title "<주제>"` (slug: 영문·숫자·한글·_·-). `project list`로 기존 확인 —
   **같은 주제 프로젝트가 있으면 새로 만들지 말고 이어간다**(P# 연속성이 프로젝트의 존재 이유).
2. §2 검색은 `bundle --out bundle.json`으로 저장한 뒤 `project add <slug> --from bundle.json --pick P1,P4`
   (`search --json` 파일도 같은 방식으로 호환되지만 §5 답변 과정 블록의 `counts`·`elapsed_sec`가 없다).
   `--pick`의 번호는 **그 검색 출력의 번호**이고, 등록 후 부여되는 P#는 레지스트리 번호다 —
   등록 직후 출력되는 `[P#]`만 이후 단계에서 쓴다. DOI·arXiv id/URL 직접 등록도 가능.
   철회 논문은 자동 거부되고, 같은 DOI는 중복으로 걸러진다(`--refresh`면 메타만 갱신·셀 유지).
   arXiv 프리프린트에 저널 DOI가 붙어 있으면 저널 메타(연도·게재지)가 정본으로 들어간다.
   **인용수는 OpenAlex(`citations`)와 Semantic Scholar(`citations_s2`)를 함께 저장·병기한다**
   (2026-09-05 신설). OpenAlex는 같은 논문을 arXiv/학회 레코드로 쪼개 세고 arXiv 참고문헌
   파싱이 약해 CS·ML 논문을 10배 넘게 저평가한다(실측: Turpin 2023 OA 90 vs S2 1,584 vs
   Liner 841). 표에는 `OA n / S2 m`으로 둘 다 찍히므로 **CS·ML 논문의 인용 규모는 S2 값으로
   말한다**. S2 무키 풀은 429가 잦아 등록 시 빠질 수 있다 → `project recount <slug>
   [--s2-only] [--pids P2,P9]`로 인용수만 재조회(제목·초록·셀은 보존 — `--refresh`와 달리
   손으로 교정한 초록을 덮지 않는다). 인용수 비교는 색인마다 다르므로 타 도구(Liner 등)와
   수치가 달라도 오류가 아니다 — 소스와 조회일을 함께 말한다.
3. §4 정독 후 ledger 발췌를 근거로 **AI 열을 채운다**: `project set <slug> P3 conclusion "..."`
   또는 여러 셀은 `[{"pid","key","value"}]` JSON으로 `project set <slug> --from cells.json`.
   기본 열 4개(사용 이유·키워드·연구 초점·핵심 결론)의 규칙은 `project col <slug> list`의
   prompt 그대로 — **`핵심 결론`·`연구 초점`은 [P#] 문장과 같은 등급**(ledger 발췌 없는 내용
   금지, 초록만 읽었으면 `(초록)`), **`사용 이유`는 [해석]**(논문 주장이 아님을 표 머리말이 밝힌다).
   사용자가 "OO 열 추가해줘"라 하면 `project col <slug> add "<라벨>" --key <k> --prompt "<기준>"`
   후 등록 논문 전부에 대해 그 열을 채운다(Liner의 "AI로 열 추가하기").
4. 게이트 A는 `project refs <slug> --pids <이번 답변의 고유 P#>` → `verify-batch <dir>/refs.json` —
   refs.json을 손으로 만들지 않는다(레지스트리가 유일한 출처, `--pids`가 회차 범위를 정한다).
   2회차 이후 등록 논문 일부만 인용하는 것이 정상이므로 `--pids` 없이 전체를 내보내면 §6 3중
   대조(refs 항목 수 == 답변의 고유 [P#] 수)가 어긋난다. **DOI·arXiv id 없는 논문은 프로젝트
   모드에 애초에 등록되지 않는다**(`project add`가 `✗ 식별자 없음`으로 거부, exit 1) — epmc
   학회초록 등은 `search --sources crossref,openalex "제목"`으로 DOI를 확보한 뒤 등록하고, 못
   찾으면 근거에서 제외한다. §6 NO_DOI 경로는 단발 모드 전용이며, refs.json을 손으로 만들어
   우회하지 않는다.
5. `project log <slug> "<질문>" --note "<답변 노트명>"`으로 회차를 기록하고
   `project render <slug> --out "<vault>/.../<주제>_논문표.md"`로 표를 갱신한다.
   기본은 **빈 셀이 있으면 exit 3**이다 — 채우지 않은 열은 `--cols`로 빼거나, 미완임을 알고
   `--allow-empty`로 낸다(표에 `—`로 남아 다음 회차 할 일이 보인다). `--abstract`는 초록 절 포함,
   **내보내기 5종(2026-09-12, Liner 내보내기 재현)**: `--format csv|xlsx`(스프레드시트, xlsx는 `--out` 필수·외부 라이브러리 없음)
   · `--format ris|bibtex`(Zotero·EndNote·Mendeley 가져오기 — 서지는 레지스트리 정본만, AI 열은 KW·note에만 실려 [P#] 등급 근거처럼
   보이지 않는다; arXiv DOI는 `@misc`+`eprint`, 프리프린트 서버명은 journal로 안 나감). **필터·정렬**(render·show·graph 공용):
   `--pids P1,P4` · `--oa-only` · `--year-from/--year-to` · `--min-cit N`(OpenAlex·S2 중 큰 값) · `--venue 부분일치` · `--grep 제목·초록·AI열`
   · `--sort n|cit|year|yearasc|title`. 필터가 걸리면 md 머리말·html 부제에 `🔎 전체 N편 중 k편 — 조건`이 찍혀 부분 표임이 드러난다
   (레지스트리는 불변). **`--format html`**은 정렬·필터(연도·인용 하한·게재지)·검색·초록 펼침이 붙은 자기완결
   페이지 — 터미널·마크다운 표의 가독성 한계를 넘기 위한 것(2026-09-05). `--full P4,P10`(본문 정독 표시)·
   `--verify-line "<verify-batch 요약줄 그대로>"`·`--subtitle`을 함께 주고, 산출 파일을 Artifact 도구로
   발행해 링크를 사용자에게 준다(mybrain 사용자는 답변에 링크 병기).
6. 허브 노트를 갱신하고(질문 이력·답변 노트 링크·다음 질문), index·리서치 허브 MOC에 반영.

### 7.3 후속 질문("이어서")
허브 노트 → `project show <slug>`(등록 논문·빈 열·질문 이력) 순으로 읽고 시작한다. 기존 P#는
그대로 인용하고 새 논문만 `project add`로 붙인다. 새 검색의 `[P1]`을 답변에 그대로 쓰면
레지스트리의 P1과 충돌한다 — **답변의 [P#]는 항상 레지스트리 번호**.

### 7.4 허브 노트 골격
```markdown
---
type: index
tags: [학술프로젝트, <주제태그>]
summary: <주제> 학술 프로젝트 — 논문 N편, 질문 K회차. 핵심 수렴점 1줄.
confidence: medium
source: 레지스트리 ~/Documents/mybrain_raw/scholar_projects/<slug>/ (인용 검증 N/N MATCH)
---
# <주제> — 학술 프로젝트
- 논문 테이블: [[<주제>_논문표]]
## 질문 이력
- [YYYY-MM-DD] <질문> → [[<답변 노트>]]
## 열린 질문
- ...
```

### 7.5 Scholar Desk — 아티팩트 앱(Liner식 3패널, 2026-09-06 신설)
URL: https://claude.ai/code/artifact/8154a715-9ea6-4dd3-b9dc-c5e4cbdb833f (db + sample 캐퍼빌리티).
좌 프로젝트 목록 · 중앙 논문표(정렬·필터·AI 열) · 우 근거 채팅([P#] 태그, 논문 목록만 근거) · 하단 검색 요청함.
페이지 소스: 세션 scratchpad `scholar_desk.html`(재발행 시 같은 URL로 `url` 지정).
- **레지스트리 → 앱**: `project export-db <slug> --out x.json [--full P#,.. --verify-line .. --subtitle ..]`
  → Artifact `write_db set projects/<slug> file_path=x.json`. 인용수 recount·논문 add 후 **반드시 다시 export**(앱은 db가 정본이 아니다).
- **앱 → 레지스트리**: 페이지에서 "AI로 열 추가"로 채운 셀은 db에만 있다 → `read_db get projects/<slug> --out_dir d`
  → `project import-db <slug> d/projects/<slug>.json`(셀·열·질문만 병합, 서지 메타는 레지스트리 정본 유지).
- **앱에서 만든 새 프로젝트**("＋ 새 프로젝트"): db에 `projects/<slug>`(papers 빈 배열, origin=app)와 첫 검색어의 `requests`가 생긴다.
  레지스트리에 없는 slug면 `read_db get projects/<slug> --out_dir d` → `project import-db <slug> d/projects/<slug>.json`이
  **레지스트리를 자동 생성**한다(제목·앱 추가 열·질문 병합). 앱에서 이름을 바꾸면 import-db가 제목을 따라간다.
- **채팅 → 리서치 요청**: 채팅에 "찾아줘·검색해·논문 추천·리서치하고 싶어" 류가 오거나 프로젝트가 빈 상태면 페이지가 `requests`에
  `type:"research"`(query=질문 전문)를 넣고 안내 메시지를 채팅에 남긴다(논문이 있으면 현재 표로 먼저 답도 함). 처리 시 **답변을
  `chats/<slug>`의 messages에 assistant로 append**(read 후 set, 기존 유지; 끝에 `===추천질문===` 3개)해야 Liner처럼 "찾아줌+답변"이 된다.
- **검색 요청 처리**("검색 요청 처리해줘"): `read_db query requests where status==pending` → 요청마다 §1~6 파이프라인
  (search → screening → project add → ledger → verify-batch) → 셀 채우기 → export-db로 projects/<slug> 갱신 →
  `write_db update requests/<id> {status:"done", result:"P24~P26 추가, 3/3 MATCH"}`. 페이지는 fetch가 막혀 있어 검색 자체는 못 한다.
  자동 폴링(CronCreate)은 auto 모드 분류기가 거부했다(2026-09-06) — 사용자가 `/loop 4m 검색 요청 처리해줘`를 직접 걸거나 말로 요청한다.
- 채팅 컨텍스트: 논문 전부가 64KB에 들어가면 한 번에 넣고, 넘치면 **제목·한 줄 결론 색인 + `get_papers` 도구**(Claude가 필요한
  논문만 초록·AI 열을 읽어 감, 도구 라운드마다 과금·30~90초). 답변 끝의 `===추천질문===` 3개는 후속 질문 버튼이 된다.
- **툴바(2026-09-12)**: 검색·정렬 4종·정독만·OA만 + **연도 이후·인용 ≥·게재지** 필터(옵션은 표에서 자동 생성) + **내보내기**(현재 필터·정렬 적용분을
  CSV·Markdown·RIS·BibTeX로 **클립보드 복사** — 샌드박스는 다운로드가 막혀 있어 복사 방식; xlsx는 터미널 render). 버튼 라벨에 `(k/N)`이 붙으면 부분 표.
- **채팅 `/`스킬 7종(2026-09-12, Liner 채팅 스킬 재현)** — 입력창에 `/`를 치면 목록. `find-related-papers`(→ requests에 `skill` 필드 붙은 리서치 요청,
  §9) · `add-citations`(붙여넣은 문단에 [P#]·[근거 없음]·[상충] 표시) · `brainstorm-ideas`(gap 기반 연구질문 5) · `write-draft`(한 절 뼈대,
  [해석] 분리) · `review-document`(5렌즈 간이 리뷰 — 정식은 §8) · `visualize-topic`(mermaid mindmap 코드) · `check-grammar`(뜻·태그 불변 교정).
  chat 모드 스킬은 표의 논문만 근거로 페이지 안에서 답하고, 사용자 메시지는 원문(`/…`)으로 남고 확장 프롬프트만 모델에 간다.
  `analyze-data`·`generate-figures`는 데이터 파일 입력이 없어 제공하지 않는다. 시작 칩 4개(관련 논문 찾기·아이디어·초안·인용 찾기)는 `/스킬`을 채워 넣는다.
- 아티팩트 샌드박스 함정 4종(2026-09-06 실측): 폼 submit 이벤트 없음(버튼 click 직결) · `confirm/alert` 항상 무효(페이지 내 다이얼로그) ·
  sample 프롬프트 64KB(`sample.limits()`로 읽어 바이트 예산) · db `data()`는 동결 객체(`structuredClone` 후 변경). 브라우저 확장은 iframe
  내부를 조작·읽기 못하므로 앱 검증은 `read_db` + 사용자 실측으로 한다.
- 앱의 AI 열·채팅은 **사용자 구독으로 과금되는 sample 호출**이다. 첫 호출 때 동의 프롬프트가 뜨고 화면이 다시 열릴 수 있어
  질문은 보내기 전에 db에 저장된다(다시 보내기 버튼). 앱이 채운 셀은 ledger 발췌가 없으므로 **[P#] 등급이 아니다** — 답변 노트에
  옮길 땐 §4 ledger를 채우고 나서 쓴다.

### 7.6 인용 그래프 — `graph` (Liner "연구 흐름 탐색" 재현, 2026-09-12)
`graph <slug> [필터·정렬 옵션] [--format md|mermaid|html|json] [--out f] [--min-shared 2] [--hubs 10] [--no-check]`
- OpenAlex `referenced_works`로 **표 안 논문끼리의 인용 간선**(실선, 인용한 쪽 → 인용된 쪽)과 **공통 참조 허브**(점선 H# — 표에 없는데
  2편 이상이 함께 인용한 문헌 = **표에 빠진 핵심 선행연구 후보**, `project add <doi>` 검토 대상)를 만든다. 색이 진할수록 최신.
- **OpenAlex 제목 오염 방어**(실측 2026-09-12): W4221143046(=Wei 2022 CoT, arXiv DOI)의 display_name이 전혀 다른 논문명으로 돼 있었다.
  허브 제목은 arXiv DOI면 arXiv API, 그 외는 Crossref로 재확인해 다르면 교체하고 `⚠ 제목 오염 의심`을 붙인다. Crossref 404면
  `⚠ DOI 재확인 실패 — 레코드 의심`(등록 전 확인). `--no-check`는 이 단계를 생략(빠르지만 오염 미탐지).
- **별도 레코드 병합**: 표 안 논문의 arXiv판/학회판이 OpenAlex에 따로 있으면(arXiv id·제목 일치) 허브가 아니라 그 P#로 간선을 넘긴다
  (실측: cot_reasoning에서 H1이 P1 자신이었고 4편이 인용). DOI 없는 arXiv 논문은 `10.48550/arxiv.<id>`로 조회한다.
- md(mermaid 코드블록 — Obsidian 렌더)·html(자기완결, cdnjs mermaid 11.6 로드 — 아티팩트 네이티브 렌더는 `<pre class="mermaid">`에 적용되지
  않았음, 실측 2026-09-12; mermaid `style`에 쉼표 있는 `hsl()`은 문법오류라 hex 사용)·json. html은 Artifact로 발행해 링크를 준다.
- 고립 논문(내부 간선 0)은 무관하다는 뜻이 아니다 — 다른 분야·최신작·OpenAlex 참고문헌 파싱 누락. 관련성은 '사용 이유' 열로 판단.
- 한계: OpenAlex 참고문헌 파싱이 CS·ML에서 약해 간선이 실제보다 적다(S2 대체 미구현). 허브 인용수는 OpenAlex 값(S2와 다름).

## 8. 피어 리뷰 — 5렌즈 (Liner "피어 리뷰" 에이전트 재현, 2026-09-12)
트리거: "리뷰해줘·피어 리뷰·심사해줘·초안 검토". 대상은 논문 초안·리서치 보고서·§7 답변 노트. 투자 아티클은 이 절이 아니라 `/article-redteam`.
1. `scholar.py review <초안.md> [--project <slug>] [--out 패킷.md] [--json]` — **리뷰 패킷**(기계 검사 재료): 섹션 8종 유무 · `[P#]`
   레지스트리 대조(미등록이면 exit 1 = 환각 인용 경로) · 본문 DOI 목록(→ `verify-batch`) · **근거 표시 없는 수치 문장**(연도만 있는 문장·표·
   코드·arXiv id는 제외) · 과장어(proves·명백히·입증…). 패킷은 판정이 아니라 렌즈별 finding의 출발 재료다.
2. `references/peer-review-rubric.md`를 읽고 렌즈 5개를 **순서대로**: Novelty(N1~N4) · Rigour(R1~R6) · Clarity(C1~C5) · Impact(I1~I4) ·
   Limitation(L1~L4). 등급은 규칙에 적힌 **Major/Minor** 그대로(임의 승격·강등 금지). finding은 위치·규칙·등급·근거 4칸을 다 채운 것만.
   패킷 항목을 초안 대조 없이 옮기지 않는다. 같은 원인의 파생 finding은 하나로 묶는다(개수 인플레이션 방지).
3. 판정: Major 0 → **Accept** / Minor만 → **Minor revision** / Major 1+ → **Major revision**(Major 3+ 또는 렌즈 2·5 양쪽이면 결론 재작성 권고).
   출력 골격은 루브릭 §출력 골격. 패킷 요약줄을 리포트 머리에 그대로 싣는다.
4. §7 답변 노트를 리뷰하면 Rigour R2는 레지스트리 `핵심 결론` 열과 초안 인용문을 대조하는 것이고, Limitation L2는 §알려진 한계(SLR 대체 아님·
   제목/초록 색인·철회 검사 불완전)가 해당되는 만큼 옮겨졌는지 본다.
- 리뷰 결과는 초안 옆에 `<초안>_리뷰_YYYY-MM-DD.md`로 두고 초안은 고치지 않는다(수정은 사용자). Desk 앱 `/review-document`는 루브릭 없는
  간이판이라 판정을 내리지 않는 것이 원칙 — 정식 판정은 이 절.

## 9. 채팅 `/`스킬 체계 — Liner 9종 ↔ 이 스킬의 대응 (2026-09-12)
사용자가 Liner식으로 부르면 아래로 라우팅한다. 터미널 열 = Claude Code에서, Desk 열 = 아티팩트 앱 채팅에서.
| Liner `/`스킬 | 터미널(scholar-research) | Desk 앱 |
|---|---|---|
| find-related-papers | §1~6 파이프라인(search→screening→project add→verify) · 앵커 있으면 `snowball`/`recommend`/`graph` 허브 | `/find-related-papers` → requests(`skill` 필드) → "검색 요청 처리해줘" |
| add-citations | 문단을 받아 §4 ledger 근거로 [P#] 부착, 근거 없는 문장은 `[근거 없음 — 검색어]`, 상충은 `[상충: P#]` — **ledger 발췌 없는 [P#] 금지** | `/add-citations` (표 근거, ledger 없음 → 초안용) |
| write-draft | §7.2 뼈대(주장 3~5문장 [P#] → [해석] → 한계). 완성문 X ([[feedback_draft_skeleton_not_finished]]) | `/write-draft` |
| brainstorm-ideas | 표의 gap·`graph` 고립/허브 기반 연구질문 5개, 각 [P#]·검증 방법 | `/brainstorm-ideas` |
| visualize-topic | `graph` (인용 그래프) 또는 mermaid mindmap(주제→[P#]). 수치 차트는 §5 시각물 규칙(ledger 수치만·보간 표시) | `/visualize-topic` (mindmap 코드) |
| review-document | **§8** `review` + 루브릭 → Major/Minor 판정 | `/review-document` (간이, 판정 보류) |
| check-grammar | 뜻·수치·[P#] 불변 교정 + 변경 목록 | `/check-grammar` |
| analyze-data · generate-figures | 제공 안 함 — 데이터 파일 분석은 이 스킬 범위 밖(별도 도구) | 제공 안 함 |
| (Liner 에이전트) hypothesis-generator/evaluator · survey-generator/simulator | 제공 안 함 — 설문 시뮬레이션은 환각 위험이 커 의도적으로 제외. 가설 생성은 brainstorm-ideas로 대체 | — |
Liner의 웹+논문 동시검색은 **의도적으로 미제공**(환각 방어 — 학술 DB만 근거). 필요하면 사용자가 웹 검색을 따로 요청한다.

## 10. 데이터 계산 요청 규칙 — 백테스트·수치 산출 (2026-09-15)
질문이 "백테스팅해줘·수익률 계산해줘·시뮬레이션해줘"처럼 **수치 산출**인데 확보한 근거가
논문(초록·본문)뿐이면, 결과 수치를 만들지 않는다. 논문의 표본·기간과 사용자의 질문이
같은 데이터가 아니므로, 논문 수치를 조립해 "백테스트 결과"로 내는 것은 날조다.
순서를 강제한다:
1. **부재 선언(첫 줄)** — "가격·수익률 시계열이 없어 누적수익률·MDD·샤프는 산출 불가".
2. **실행 가능한 설계** — 국면 정의·판별 규칙·전략 비중·평가지표를 `[P#]` 근거로 제시.
   비중·임계값 표에는 "제안 초기값, 관측된 최적값 아님" 주석을 단다.
3. **필요 데이터 목록** — 시계열 이름·주기·출처 후보(FRED 시리즈 id 등)를 표로.
4. 문헌이 확인하는 **방향성 결과**(어느 국면에서 어떤 자산이 유효했나)는 `[P#]`로 별도 절.
Liner의 같은 질문 답변이 이 순서를 정확히 따랐다(`references/liner-observed-20260915.md`
§2) — 그러나 시각물에서는 어겼다(§5 시각물 규칙 반례).

### 10.1 1차출처 데이터 계층 `[W#]` (2026-09-15 신설)
실제 계산은 **시계열을 `data` 명령으로 수집해 W#를 부여한 뒤에만** 한다. 논문 = `[P#]`,
데이터 = `[W#]` — 답변의 모든 수치는 둘 중 하나에 귀속돼야 하며, 어느 쪽도 아닌 수치는
`[해석]`이 아니라 **삭제**한다(계산 결과에 [해석]은 없다).
- 수집: `data fred <ID> [--start --end]`(FRED 공개 CSV, 무키) · `data worldbank <IND> --country US,KR
  [--start --end]`(World Bank v2, 무키). 같은 (출처·시리즈·국가)를 다시 받으면 같은 W#를 재사용하고
  수집일·해시·obs_end만 갱신한다(번호 재사용 금지 — P# 규약과 동일). ledger:
  `$SCHOLAR_HOME/data/ledger.json`, CSV `$SCHOLAR_HOME/data/<W#>_<시리즈>[_<국가>].csv`.
- 계산은 Claude가 세션에서 CSV를 읽어 한다(스크립트는 계산하지 않는다). 검산은 `data show W1
  --stats`(n·결측·min/max·first/last)로 원본 값과 대조한다.
- **신선도 표 필수**: 계산 결과를 내는 답변은 `data vintage`가 만든 표(W# · 시리즈 · 관측
  기준시점 obs_end · 수집일 · 주기 · URL)를 붙인다. **obs_end가 데이터 기준시점**이고 수집일이
  아니다(사용자 vault 규칙 "발간일 ≠ 데이터 기준시점"). 연도가 다른 시리즈를 나누거나 비교하면
  불일치를 명시하고 `[ambiguous]` 처리.
- 게이트: `data verify` 요약줄 `데이터 검증: k/N OK · stale m건`을 §6 결과줄 옆에 병기한다.
  exit 0 통과 / 5 stale·재조회 실패(미검증 — 재수집 후 재계산) / 3 해시 불일치·파일 부재(계산
  무효) / 2 ledger 비어 있음. stale이면 수치를 내지 않는다.
- 시각물은 §5 규칙 그대로: W# 원본 관측점만 그리고, 파생 지표(누적수익률 등)는 캡션에 계산식과
  `[W#]`를 적는다. 보간·외삽 점은 점선 + `(추정)`.
- 한계: FRED 단위·계절조정 메타는 얻지 못한다(null) — 단위는 FRED 시리즈 페이지에서 사람이 확인해
  캡션에 적는다. 주기는 관측 간격에서 추정(D/W/M/Q/A).

## vault 파일링 (mybrain 사용자 한정, 선택)
재사용 가치가 있으면(종합 비교·데이터 갭 해소) CLAUDE.md 규약대로 파일링:
`10_Inbox` 착지 → 프론트매터 5필드 + **데이터 신선도 표**(논문 연도≠데이터 연도
주의) + 위키링크 2개 이상 + index/관련 MOC 갱신. 일회성 답변은 파일링하지 않는다.

## 하지 말 것
- Google Scholar 스크레이핑(차단·불안정), Sci-Hub 등 페이월 우회.
- verify 없이 DOI·제목을 답변에 쓰는 것(환각 인용의 주 발생 경로).
- ledger 발췌 없이 `[P#]` 태그를 다는 것(내용 왜곡의 주 발생 경로 — verify는
  서지만 검증하지 "그 논문이 그 말을 했는가"는 게이트 B만 잡는다).
- 철회(RETRACTED) 논문을 근거로 인용하는 것.
- 대량 후보를 상위 모델 컨텍스트로 전부 읽는 것 — 스크리닝은 haiku, 정독은 선별분만.
- 중간 산출물을 안 남기고 긴 파이프라인을 이어가는 것 — 검색 결과·선별 목록·
  evidence ledger·refs.json을 파일로 저장 후 진행(크레딧 소진 대비).

## 알려진 한계 (사용자에게 과대 약속 금지)
- 이 파이프라인은 **체계적 문헌고찰(SLR)의 대체가 아니다** — 키워드 검색 리콜
  한계, S2 공유풀 유실, OpenAlex 인덱싱 지연(최신 수 주)으로 누락 문헌이 있을
  수 있다. 망라성이 필요한 질문엔 이 한계를 답변에 명시한다.
- **crossref·s2·pubmed·arxiv는 제목·초록만 색인한다** — 본문에만 나오는 기법·장비·
  데이터셋·지역명 조건은 무경고로 리콜이 붕괴한다. openalex의 `search` 파라미터는
  전문 일부를 포함하지만(내부적으로 `fulltext.search`로 매핑 — 실측: `"Sysmex
  XN-1000"` 2,319건 vs `title_and_abstract.search` 351건) 색인 범위가 공개되지
  않아 망라적이지 않다. 그런 질의는 `--sources epmc`(전문 색인 + 섹션 한정 구문 +
  프리프린트)를 반드시 함께 돌린다.
- **철회 검사는 완전하지 않다** — Crossref·OpenAlex는 철회 반영이 늦고(실측: 이미
  철회된 논문이 `is_retracted:false`로 남아 있는 사례), PubMed 보강은 생의학
  한정이며, DataCite에만 있는 DOI는 철회 플래그 자체가 없다. `철회 0건`은
  '세 소스가 아는 한 없음'이지 '철회되지 않았음'의 증명이 아니다.
- **`graph`의 간선은 OpenAlex 참고문헌 파싱에 의존**해 CS·ML에서 실제보다 적고, 허브 제목은 OpenAlex 오염 사례가 있어 재확인 없이는
  믿지 않는다(§7.6). **`review` 패킷은 기계 검사**라 근거 없는 수치 문장 판정에 오탐(검증 요약줄 등)·미탐(단어형 수치)이 있다 — 판정은 사람.
- 초록만 읽은 논문의 결론은 본문 대비 과장 경향이 있다 — `(초록)` 표기가 그
  경고다. 핵심 근거가 전부 `(초록)`이면 확신도를 낮춰 서술한다.
