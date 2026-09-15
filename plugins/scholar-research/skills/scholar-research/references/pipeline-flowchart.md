# scholar-research 파이프라인 플로우차트 (2026-09-15)

SKILL.md 본문의 6단계·프로젝트 모드·게이트·데이터 계층을 한 장으로 그린 것.
규약의 정본은 SKILL.md이며 이 문서는 **분기점을 빠르게 찾는 지도**다. 절 번호는 SKILL.md와 같다.

## 1. 전체 흐름 (질문 → 답변)

```mermaid
flowchart TD
    Q[사용자 질문] --> D{§10 수치 산출 요청?<br/>백테스트·수익률·시뮬레이션}
    D -- 예 --> W[§10 데이터 계산 규칙<br/>1 부재 선언 → 2 실행 가능한 설계<br/>→ 3 필요 데이터 표 → 4 문헌 방향성]
    W --> WD{1차출처 시계열 확보 가능?<br/>data fred / data worldbank}
    WD -- 예 --> WL[[W#] 데이터 ledger<br/>data verify 결과줄 + data vintage 신선도 표 필수<br/>모든 수치는 [P#] 또는 [W#]에 귀속]
    WD -- 아니오 --> S1
    WL --> S1
    D -- 아니오 --> S1

    S1[§1 질문 분해<br/>영문 키워드 2~3조합 · 분야 라우팅<br/>pubmed / arxiv / epmc 추가 여부] --> S2
    S2[§2 검색 — bundle 기본<br/>openalex+crossref+s2 병렬 → DOI dedup<br/>→ 관련도·인용 랭킹 → 상위 K 초록<br/>openalex→crossref→s2 폴백] --> PM{프로젝트 모드?<br/>§7 같은 주제 이어가기}
    PM -- 예 --> PA[project add --from bundle.json --pick<br/>P#는 레지스트리 번호]
    PM -- 아니오 --> S3
    PA --> S3
    S3[§3 스크리닝<br/>≤10건 직접 · 수십 건 haiku 서브에이전트<br/>앵커 리뷰 1편 지정] --> CV{커버리지 빈약?}
    CV -- 예 --> SB[snowball cites/refs<br/>refs는 100건 단위 전량 배치<br/>recommend는 S2 429면 snowball 대체]
    SB --> S3
    CV -- 아니오 --> S4
    S4[§4 정독 — 기본 초록 전용<br/>본문 정독 2~3편 상한<br/>앵커 리뷰·핵심 수치·상충 논문<br/>evidence ledger 발췌] --> S5
    S5[§5 합성<br/>답변 과정 블록 · 모든 사실 문장 [P#]<br/>[해석] 절 분리 · 관련 질문 3개<br/>시각물은 ledger 수치만] --> DEF{지연 실행?<br/>사용자 대기 단축}
    DEF -- 예 --> DR[⚠️ 검증 전 초안 표시<br/>[P#] 문장 12개 상한]
    DR --> G
    DEF -- 아니오 --> G
    G[§6 이중 게이트] --> OUT[답변 출력<br/>표시 제거 · 결과줄 부착]
    OUT --> F{vault 파일링?<br/>mybrain 사용자}
    F -- 예 --> V[40_Resources/03_리서치 · index/MOC 갱신]
```

## 2. §6 이중 게이트 상세

```mermaid
flowchart TD
    A0[게이트 A — 서지 기계검증] --> A1{개수 3중 대조<br/>P# 표 행 수 == refs.json 항목 수<br/>== 답변 고유 [P#] 수}
    A1 -- 불일치 --> STOP1[출력 금지 — 등재 누락]
    A1 -- 일치 --> A2[refs.json 작성<br/>id·doi·title·author 1저자 성·year 필수<br/>프로젝트 모드: project refs --pids]
    A2 --> A3[verify-batch refs.json]
    A3 --> EX{exit}
    EX -- 0 MATCH --> B0
    EX -- 2 NO_DOI / RETRACTION_NA --> FIX2[수동 대조 후 유지 가능한 예외<br/>단발 모드 전용]
    EX -- 3 MISMATCH / NOT_FOUND / NON_ARTICLE --> FIX3[가짜·오기재 인용 — 제거<br/>접두·의학접미 치환·극성 반전·한정어 삭제도 여기]
    EX -- 4 RETRACTED --> FIX4[철회 논문 — 인용 금지<br/>철회 사실 서술만 허용]
    EX -- 5 UNVERIFIED / RETRACTION_UNCHECKED --> FIX5[미검증 — 재실행 또는 제거]
    EX -- 6 BAD_ENTRY / 파일 오류 --> FIX6[refs.json 수정 후 재실행]
    FIX2 --> A1
    FIX3 --> A1
    FIX4 --> A1
    FIX5 --> A3
    FIX6 --> A2

    B0[게이트 B — 주장-근거 대조] --> B1{각 [P#] 문장마다<br/>ledger 발췌가 지지하는가}
    B1 -- 지지 --> B2[통과 — 결과줄:<br/>인용 검증 n/n MATCH · 철회 0건<br/>· 고유 DOI n/n · 대조 m/m 지지]
    B1 -- 불지지 --> B3[태그 제거 → [해석] 강등<br/>또는 문장 삭제]
    B3 --> A1
```

## 3. 캐시·exit 계약 요약

| 명령 | 캐시 | 주요 exit |
|---|---|---|
| `search` | 캐시 안 함(신선도) | 0 / 1(0건 — 전 소스 오류 포함, stderr에 소스오류) / 2(미인식 소스명) |
| `bundle` | 초록·메타만 30일 | 0 / 1(0건) / 2(미인식 소스명) |
| `paper` | 메타·초록 30일, 철회 판정은 매번 실 조회 | 0 / 1 / 3(arXiv 제목 오염 의심) / 5(재확인 실패) |
| `verify` / `verify-batch` | PMID 매핑·DataCite 서지만, Crossref·철회는 매번 | 0 / 2(NO_DOI·RETRACTION_NA만) / 3(NOT_FOUND 포함, 단건·batch 동일) / 4 / 5 / 6 |
| `data fred` / `data worldbank` | 캐시 안 함(ledger가 기록) | 0 / 2(BAD_SOURCE) |
| `data verify` | – | 0 / 2(ledger 비어 있음) / 3(해시 불일치·파일 부재·미등록 W#) / 5(stale·재조회 실패) |

끄기: `--no-cache` 또는 `SCHOLAR_NO_CACHE=1`. 경로: `SCHOLAR_CACHE`(기본 `~/.cache/scholar-research/cache.db`).

## 4. 자주 틀리는 분기 (레드팀에서 실제로 잡힌 것)

- **철회 판정은 캐시 히트여도 실 조회** — 늦게 반영되므로. 자기참조 update-to(Elsevier)도 RETRACTED.
- **arXiv DOI(10.48550/*)의 `paper` 초록은 믿지 않는다** — OpenAlex 레코드 오염 사례. arXiv API·DataCite로 제목 재확인.
- **프로젝트 모드 3중 대조는 `--pids`로** — 레지스트리 전체를 refs.json으로 내보내면 개수가 어긋난다.
- **DOI 정규화** — `doi:` 접두·후행 마침표는 벗긴다. 괄호는 보호(Wiley 구형).
- **수치 산출 요청에 논문 수치를 조립해 "백테스트 결과"로 내지 않는다** — [W#] 시계열이 있을 때만 계산.
