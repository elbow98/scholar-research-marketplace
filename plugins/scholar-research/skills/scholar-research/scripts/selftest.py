#!/usr/bin/env python3
"""selftest.py — scholar.py 회귀 테스트.

기본은 오프라인(네트워크 불필요 — API 호출은 http_get 모킹), `--live`면 실 API 검증 추가.
새 허점을 고칠 때마다 여기에 회귀 케이스를 추가한다 — 수정의 증명은 exit 0.
"""
import argparse
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import urllib.parse as urllib_parse

BASE = os.path.dirname(os.path.abspath(__file__))
# 기존 회귀 케이스는 전부 캐시 비활성으로 돈다(모킹 URL 계약 유지). 캐시 케이스는
# _cache_tests()에서만 임시 SCHOLAR_CACHE 경로로 켠다.
os.environ["SCHOLAR_NO_CACHE"] = "1"
spec = importlib.util.spec_from_file_location("scholar", os.path.join(BASE, "scholar.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

fails = []
count = 0


def check(name, cond, detail=""):
    global count
    count += 1
    print(("OK  " if cond else "FAIL") + f" {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(name)


# ---- _title_match ----
TITLE_CASES = [
    ("Deep learning", "Deep learning", True, "title: 동일"),
    ("Attention is all you need", "Attention Is All You Need", True, "title: 대소문자"),
    ("Highly accurate protein structure prediction",
     "Highly accurate protein structure prediction with AlphaFold", True, "title: 축약"),
    ("Safe handling of nanotechnology",
     "Safe handling of nanotechnology: subtitle about risk governance in labs", True, "title: 부제 절단"),
    ("Nanotoxicology studies Part I", "Nanotoxicology studies Part II", False, "title: 시리즈 오판 방어"),
    ("Completely different paper title here",
     "Deep residual learning for image recognition", False, "title: 무관 제목"),
    # 레드팀 R1: containment 구제가 실제 제목에 덧붙인 조작 문구를 통과시키던 허점
    ("Deep residual learning for image recognition proves that vaccines cause "
     "autism in longitudinal studies",
     "Deep residual learning for image recognition", False, "title: 조작 문구 뒤붙임 차단"),
    ("Longitudinal evidence shows that deep residual learning for image recognition",
     "Deep residual learning for image recognition", False, "title: 조작 문구 앞붙임 차단"),
    ("A comprehensive survey of deep learning methods",
     "A comprehensive survey of deep learning methods and their applications in "
     "medical imaging analysis and clinical diagnosis workflows",
     True, "title: 정당한 축약 인용(claimed ⊆ actual) 구제 유지"),
    ("Deep residual learning for image recognition CVPR",
     "Deep residual learning for image recognition", True, "title: 사소한 덧붙임(단어비 0.8+) 허용"),
    # 레드팀 R1: main-title 구제가 조작 부제 삽입·교체를 통과시키던 허점
    ("Deep residual learning for image recognition: a fabricated subtitle that says anything",
     "Deep residual learning for image recognition", False, "title: 조작 부제 삽입 차단"),
    ("Machine learning in healthcare: completely invented subtitle claims",
     "Machine learning in healthcare: a systematic review of clinical applications",
     False, "title: 조작 부제 교체 차단"),
    # 레드팀 R2: 문자 유사도가 의미 반전에 무감각해 부정어 삭제·핵심어 치환이 MATCH였다
    ("Neuroblastoma screening does reduce mortality",
     "Neuroblastoma screening does not reduce mortality", False, "title: 부정어 삭제 차단"),
    ("Screening for type 2 diabetes reduces mortality",
     "Screening for type 2 diabetes does not reduce mortality", False,
     "title: 부정어 삭제(어형 변화 동반) 차단"),
    ("Mastering the game of Chess with deep neural networks and tree search",
     "Mastering the game of Go with deep neural networks and tree search", False,
     "title: 연구대상 치환 차단"),
    ("Mastering the game of Go with shallow neural networks and random search",
     "Mastering the game of Go with deep neural networks and tree search", False,
     "title: 2단어 치환 차단"),
    ("An ineffective intervention for chronic pain in older adults",
     "An effective intervention for chronic pain in older adults", False,
     "title: in- 접두 극성 반전 차단"),
    # 정당한 표기 변형은 계속 통과해야 한다(오탐 회귀 방어)
    ("Random forest classifiers for remote sensing image analysis",
     "Random forests classifiers for remote sensing image analysis", True,
     "title: 단복수 변형 허용"),
    ("A randomised controlled trial of vitamin D supplementation",
     "A randomized controlled trial of vitamin D supplementation", True,
     "title: 영/미 철자 변형 허용"),
    # 레드팀 R3: 편측 추가(claimed에만 내용어)가 토큰 검사를 아예 거치지 않고
    # ratio>=0.75 경로로 MATCH되던 허점 — 모집단·연구설계 날조가 통과했다
    ("Metformin improves healthspan and lifespan in mice and humans",
     "Metformin improves healthspan and lifespan in mice", False,
     "title: 모집단 확장(in mice and humans) 차단"),
    ("Metformin improves healthspan and lifespan in mice: a randomized controlled trial",
     "Metformin improves healthspan and lifespan in mice", False,
     "title: 연구설계 날조 부제 차단"),
    ("A Randomized, Controlled Trial of the Use of Pulmonary-Artery Catheters in "
     "High-Risk Surgical Patients in Children",
     "A Randomized, Controlled Trial of the Use of Pulmonary-Artery Catheters in "
     "High-Risk Surgical Patients", False, "title: 모집단 덧붙임(in Children) 차단"),
    ("A Randomized, Controlled Trial of the Use of Pulmonary-Artery Catheters in "
     "High-Risk Surgical Patients and Mortality Reduction",
     "A Randomized, Controlled Trial of the Use of Pulmonary-Artery Catheters in "
     "High-Risk Surgical Patients", False, "title: 결과지표 덧붙임 차단"),
    # 축약 방향(실제 제목의 한정어 삭제)은 설계된 허용 — 회귀 방어
    ("Metformin improves healthspan and lifespan",
     "Metformin improves healthspan and lifespan in mice", True,
     "title: 축약 인용은 계속 허용(설계된 구제)"),
    # 레드팀 R3: 부정어 검사가 표기 차이·정당한 부제 절단을 가짜 인용으로 오판
    ("Noninvasive ventilation in acute respiratory failure",
     "Non-invasive ventilation in acute respiratory failure", True,
     "title: non-/non 하이픈 표기 변형 허용(negation 오탐 차단)"),
    ("Nonlinear dynamics of coupled oscillator networks",
     "Non-linear dynamics of coupled oscillator networks", True,
     "title: nonlinear/non-linear 표기 변형 허용"),
    ("Collaborative tax evasion and social norms",
     "Collaborative tax evasion and social norms: why deterrence does not work", True,
     "title: 부제에 부정어가 있어도 정당한 주제목 절단 허용"),
    ("Translating evidence to practice in medicine",
     "Translating evidence into practice in medicine", True,
     "title: 'into'='in'+'to' 접두쌍 오탐 차단"),
    # 진짜 극성 반전은 계속 잡아야 한다(위 완화의 회귀 방어)
    ("Noninvasive ventilation is effective in acute respiratory failure",
     "Non-invasive ventilation is ineffective in acute respiratory failure", False,
     "title: 표기 변형 완화 후에도 in- 접두 반전은 차단"),
    ("Collaborative tax evasion and social norms do not matter",
     "Collaborative tax evasion and social norms", False,
     "title: claimed 쪽에 부정어를 덧붙인 확장은 차단"),
    # 레드팀 R4: 제목 속 식별자를 한 글자 바꿔도 근사매칭(ratio>=0.8)이 '표기 변형'으로
    # 흡수해 MATCH였다 — BNT162b1(중단된 후보)에 b2의 3상 유효성 95%가 귀속된다.
    # 식별자는 어간이 길고 차이가 1글자라 ratio가 구조적으로 0.87~0.95다.
    ("Safety and Efficacy of the BNT162b1 mRNA Covid-19 Vaccine",
     "Safety and Efficacy of the BNT162b2 mRNA Covid-19 Vaccine", False,
     "title: 백신 후보 식별자 치환(BNT162b1↔b2) 차단"),
    ("BRCA1 mutation carriers and breast cancer risk in cohorts",
     "BRCA2 mutation carriers and breast cancer risk in cohorts", False,
     "title: 유전자 식별자 치환(BRCA1↔2) 차단"),
    ("GPT3 few shot learners evaluation across benchmark tasks",
     "GPT4 few shot learners evaluation across benchmark tasks", False,
     "title: 모델 버전 치환(GPT3↔4) 차단"),
    ("CIFAR10 classification with residual convolutional networks",
     "CIFAR100 classification with residual convolutional networks", False,
     "title: 데이터셋 식별자 치환(CIFAR10↔100) 차단"),
    ("Training deep networks on the ImageNette benchmark dataset",
     "Training deep networks on the ImageNet benchmark dataset", False,
     "title: 숫자 없는 데이터셋 파생명 치환(ImageNette↔ImageNet) 차단"),
    # 식별자 규칙이 정당한 표기 변형을 깨지 않아야 한다(오탐 회귀 방어)
    ("Meta analysis of tumour response in paediatric patients",
     "Meta analyses of tumor response in pediatric patients", True,
     "title: 영/미 철자+단복수 동시 변형 허용(식별자 규칙 오탐 방어)"),
    ("Efficacy of the BNT162b2 mRNA Covid-19 Vaccine in adults",
     "Efficacy of the BNT162b2 mRNA Covid19 Vaccine in adults", True,
     "title: covid-19/covid19 표기 변형 허용(숫자 포함이지만 동일 토큰)"),
    # 레드팀 R4: 실제 제목 중간의 한정어만 지우면(cancer incidence 삭제) 검사가
    # 아예 없어 MATCH였다 — 암 특이 메타분석이 전체사망률 메타분석으로 승격된다
    ("Vitamin D supplementation and total mortality: a meta-analysis of randomized "
     "controlled trials",
     "Vitamin D supplementation and total cancer incidence and mortality: "
     "a meta-analysis of randomized controlled trials", False,
     "title: 제목 중간 한정어 삭제(cancer incidence) 차단"),
    ("Effect of metformin on mortality in patients with heart failure",
     "Effect of metformin on mortality in diabetic patients with heart failure", False,
     "title: 모집단 한정어 삭제(diabetic) 차단"),
    ("Prevalence of dementia in the elderly cohort study population",
     "Prevalence of vascular dementia in the elderly cohort study population", False,
     "title: 질환 한정어 삭제(vascular) 차단"),
    ("Screening for depression in postpartum women using validated scales",
     "Screening for self-reported depression in postpartum women using validated scales",
     False, "title: 측정방식 한정어 삭제(self-reported) 차단"),
    # 레드팀 R5: 대립 접두 치환(hyper↔hypo 등)이 ratio≥0.8 흡수로 '표기 변형' 취급돼
    # MATCH였다 — 저혈당 논문이 고혈당 근거로, 암컷 실험이 수컷 결과로 인용됐다
    ("Hyperglycemia in Type 2 Diabetes", "Hypoglycemia in Type 2 Diabetes", False,
     "title: hyper↔hypo 접두 치환 차단"),
    ("Male mice behave well in open field tests",
     "Female mice behave well in open field tests", False, "title: male↔female 치환 차단"),
    ("Preoperative Delirium in elderly surgical patients",
     "Postoperative Delirium in elderly surgical patients", False, "title: pre↔post 치환 차단"),
    ("Bipolar Depression treatment outcomes review",
     "Unipolar Depression treatment outcomes review", False, "title: bi↔uni 치환 차단"),
    ("Chronic Hypotension and Pregnancy Outcomes",
     "Chronic Hypertension and Pregnancy Outcomes", False, "title: hypo↔hyper(tension) 치환 차단"),
    ("Intracellular signaling pathways in neurons",
     "Intercellular signaling pathways in neurons", False, "title: intra↔inter 치환 차단"),
    ("Ketamine for treatment resistant depression",
     "Esketamine for treatment resistant depression", False,
     "title: 이성질체 접두 파생(ketamine↔esketamine) 치환 차단"),
    ("Citalopram versus placebo in major depression",
     "Escitalopram versus placebo in major depression", False,
     "title: citalopram↔escitalopram 치환 차단"),
    # 접두 치환 규칙이 정당한 영/미 철자 변형(앞머리·겹자음·c/k)을 깨지 않아야 한다
    ("Modeling behaviour of oestrogen in haemoglobin transport",
     "Modelling behavior of estrogen in hemoglobin transport", True,
     "title: 겹자음·oe/ae 앞머리 철자 변형 허용(접두 치환 규칙 오탐 방어)"),
    ("Clinical judgement and leucocyte counts in workers",
     "Clinical judgment and leukocyte counts in workers", True,
     "title: judgement/judgment·leucocyte/leukocyte 변형 허용"),
    ("Enrolment and fulfilment in randomised trials of anaesthesia",
     "Enrollment and fulfillment in randomized trials of anesthesia", True,
     "title: enrolment/enrollment·anaesthesia/anesthesia 변형 허용"),
    # 레드팀 R5: 제목 머리의 결론 강도 표지(Weak/Questionable/Limited)를 지운 인용이
    # 연속 절단 면제 + ratio≥0.75로 'full' MATCH였다 — 약한 관련이 '관련 있음'으로 승격
    ("Association Between Retinopathy of Prematurity and Neurological Disorders in Childhood",
     "Weak Association Between Retinopathy of Prematurity and Neurological Disorders in Childhood",
     False, "title: 머리 hedge(Weak) 삭제 차단"),
    ("Benefit of Short Course Radiotherapy for Rectal Cancer",
     "Questionable Benefit of Short Course Radiotherapy for Rectal Cancer", False,
     "title: 머리 hedge(Questionable) 삭제 차단"),
    ("Effect of aspirin on cardiovascular events in elderly",
     "Limited Effect of aspirin on cardiovascular events in elderly", False,
     "title: 머리 hedge(Limited) 삭제 차단"),
    ("Radiation Risk from computed tomography scans in children",
     "Radiation Risk from computed tomography scans in children Overestimated", False,
     "title: 꼬리 hedge(Overestimated) 삭제도 차단"),
    # 머리 절단은 내용어를 지우면 차단, 관사만 지우면 허용
    ("Prediction of protein structure with deep learning networks",
     "Accurate Prediction of protein structure with deep learning networks", False,
     "title: 머리 내용어(Accurate) 절단 차단"),
    ("Comprehensive survey of deep learning methods",
     "A comprehensive survey of deep learning methods", True,
     "title: 머리 관사(A)만 지운 절단은 허용"),
]
for claimed, actual, expect, label in TITLE_CASES:
    got, ratio, how = m._title_match(claimed, actual)
    check(label, got == expect, f"match={got} ratio={ratio:.2f} via={how}")
# 레드팀 R5: 흡수된 표기 변형이 omitted_tokens에 '생략'으로 찍혀 치환을 절단으로 오표시했다
_sub = m.substituted_tokens("Meta analysis of tumour response in paediatric patients",
                            "Meta analyses of tumor response in pediatric patients")
check("substituted: 흡수된 철자 변형을 'x→y'로 노출",
      "tumour→tumor" in _sub and "paediatric→pediatric" in _sub, str(_sub))
_om = m.omitted_tokens("Meta analysis of tumour response in paediatric patients",
                       "Meta analyses of tumor response in pediatric patients")
check("omitted: 흡수된 변형(tumor·pediatric)은 생략 목록에서 제외", _om == [], str(_om))
check("omitted: 진짜 꼬리 절단은 계속 노출",
      m.omitted_tokens("Highly accurate protein structure prediction",
                       "Highly accurate protein structure prediction with AlphaFold") == ["alphafold"])

# ---- _author_match (레드팀 R1: 부분문자열 오탐 — 'He'⊂'Chen' 류) ----
AUTHOR_CASES = [
    ("He", "He", True, "author: 동일"),
    ("he", "He", True, "author: 대소문자"),
    ("Che", "He", False, "author: 'Che'≠'He' 부분문자열 오탐 차단"),
    ("Hen", "He", False, "author: 'Hen'≠'He' 부분문자열 오탐 차단"),
    ("He", "Chen", False, "author: 'He'⊄'Chen' 차단"),
    ("Li", "Oliveira", False, "author: 'Li'⊄'Oliveira' 차단"),
    ("Cruz", "de la Cruz", True, "author: 복합성 토큰 일치 허용"),
    ("Smith-Jones", "Smith", True, "author: 하이픈 복합성 허용"),
    # 레드팀 R2: 불변입자만 겹쳐도 통과하던 허점 ('de Silva' vs 'de Souza')
    ("de Silva", "de Souza", False, "author: 불변입자 'de'만 겹침 차단"),
    ("van Dijk", "van Berg", False, "author: 'van'만 겹침 차단"),
    ("van der Berg", "van den Broek", False, "author: 'van'+관사만 겹침 차단"),
    ("de Jong", "de Vries", False, "author: 'de'만 겹침 차단"),
    ("de Souza", "de Souza", True, "author: 입자 포함 동일 성 허용"),
    ("de la Cruz", "Cruz", True, "author: 입자 제거 후 본체 일치 허용"),
]
for c_au, a_au, expect, label in AUTHOR_CASES:
    check(label, m._author_match(c_au, a_au) == expect)
check("author: 빈 입력은 미판정(None)", m._author_match("", "He") is None)

# ---- norm_doi ----
check("norm_doi: url+대문자", m.norm_doi("https://doi.org/10.1038/NATURE14539") == "10.1038/nature14539")
check("norm_doi: dx.doi.org", m.norm_doi("http://dx.doi.org/10.1/x") == "10.1/x")
check("norm_doi: None", m.norm_doi(None) is None)
check("norm_doi: 빈 문자열", m.norm_doi("  ") is None)
# 레드팀 R5: 'doi:' 접두 미제거(중복 DOI 경보 우회·캐시 키 분열)·후행 마침표(NOT_FOUND 낙인)
check("norm_doi: 'doi:' 접두 제거", m.norm_doi("doi:10.1038/nature14539") == "10.1038/nature14539")
check("norm_doi: 'DOI: ' 공백 포함 접두 제거", m.norm_doi("DOI: 10.1038/nature14539") == "10.1038/nature14539")
check("norm_doi: 후행 마침표 제거", m.norm_doi("10.1038/nature14539.") == "10.1038/nature14539")
check("norm_doi: 후행 세미콜론·쉼표 제거", m.norm_doi("https://doi.org/10.1/x;,") == "10.1/x")
check("norm_doi: 괄호로 끝나는 구형 DOI 보호",
      m.norm_doi("10.1002/(SICI)1097-0258(19980430)17:8<857::AID-SIM777>3.0.CO;2-E")
      == "10.1002/(sici)1097-0258(19980430)17:8<857::aid-sim777>3.0.co;2-e")

# ---- uninvert_abstract ----
check("uninvert: 기본", m.uninvert_abstract({"deep": [0], "learning": [1]}) == "deep learning")
check("uninvert: None", m.uninvert_abstract(None) is None)

# ---- verify exit code 계약 (레드팀 R1: NO_DOI=2·UNVERIFIED=5 추가) ----
check("VERIFY_EXIT 계약",
      (m.VERIFY_EXIT.get("MATCH"), m.VERIFY_EXIT.get("NOT_FOUND"),
       m.VERIFY_EXIT.get("NO_DOI"), m.VERIFY_EXIT.get("MISMATCH"),
       m.VERIFY_EXIT.get("MISMATCH_META"), m.VERIFY_EXIT.get("RETRACTED"),
       m.VERIFY_EXIT.get("UNVERIFIED")) == (0, 3, 2, 3, 3, 4, 5))
# 레드팀 R6: 단건 NOT_FOUND(가짜 DOI)가 예외 승인 코드(2)와 같았다 — batch와 같은 3으로 통일
check("VERIFY_EXIT 계약: NOT_FOUND=3(단건·batch 통일, exit 2는 NO_DOI·RETRACTION_NA만)",
      m.VERIFY_EXIT.get("NOT_FOUND") == 3 and m.VERIFY_EXIT.get("NO_DOI") == 2
      and m.VERIFY_EXIT.get("RETRACTION_NA") == 2)
# 레드팀 R3: 새 verdict도 exit 계약에 등재돼야 한다(exit 1 = 계약 밖 코드 금지)
check("VERIFY_EXIT 계약: NON_ARTICLE=3 · BAD_ENTRY=6",
      (m.VERIFY_EXIT.get("NON_ARTICLE"), m.VERIFY_EXIT.get("BAD_ENTRY")) == (3, 6))

# ---- verify_one 오프라인 (http_get 모킹) ----
ORIG_HTTP_GET = m.http_get

# 레드팀 R1: doi 누락 항목이 TypeError로 배치를 죽이던 허점 — 네트워크 없이 NO_DOI
r = m.verify_one(None, "Attention Is All You Need", "Vaswani", 2017)
check("verify: doi 누락 → NO_DOI(크래시 없음)", r.get("verdict") == "NO_DOI", str(r))

# 레드팀 R1: 네트워크 전면 장애가 NOT_FOUND(가짜 인용 신호)로 오판되던 허점
m.http_get = lambda url, **kw: (None, -1)
r = m.verify_one("10.1038/nature14539", "Deep learning")
check("verify: 전송오류 → UNVERIFIED(NOT_FOUND 아님)", r.get("verdict") == "UNVERIFIED", str(r))
m.http_get = lambda url, **kw: (None, 429)
r = m.verify_one("10.1038/nature14539", "Deep learning")
check("verify: 429 → UNVERIFIED", r.get("verdict") == "UNVERIFIED", str(r))
m.http_get = lambda url, **kw: ((None, 404) if "crossref" in url else (None, -1))
r = m.verify_one("10.1038/nature14539", "Deep learning")
check("verify: 404+전송오류 혼합 → UNVERIFIED", r.get("verdict") == "UNVERIFIED", str(r))
m.http_get = lambda url, **kw: (None, 404)
r = m.verify_one("10.9999/nonexistent", "x")
check("verify: 두 API 모두 404 → NOT_FOUND", r.get("verdict") == "NOT_FOUND", str(r))

# 레드팀 R1: '#' 포함 DOI가 URL 프래그먼트로 절단되던 허점 — 인코딩 확인
_urls = []
def _cap404(url, **kw):
    _urls.append(url)
    return (None, 404)
m.http_get = _cap404
m.verify_one("10.1038/nature14539#frag", "x")
check("verify: '#' DOI URL 인코딩(%23)",
      _urls and all("#" not in u for u in _urls) and any("%23" in u for u in _urls), str(_urls))

# 레드팀 R1: Crossref subtitle 필드 활용 — 정당한 부제 포함 인용은 구제, 조작 부제는 차단
_CR_SUB = json.dumps({"message": {
    "title": ["Safe handling of nanotechnology"],
    "subtitle": ["Risk governance in laboratory settings"],
    "issued": {"date-parts": [[2020]]},
    "author": [{"family": "Kim"}]}})
_OA_SUB = json.dumps({"display_name": "Safe handling of nanotechnology",
                      "publication_year": 2020, "is_retracted": False,
                      "authorships": [{"author": {"display_name": "Jisoo Kim"}}]})
m.http_get = lambda url, **kw: ((_CR_SUB, 200) if "crossref" in url else (_OA_SUB, 200))
r = m.verify_one("10.1/sub", "Safe handling of nanotechnology: Risk governance in laboratory settings")
check("verify: 부제 포함 정당 인용 → MATCH(subtitle 후보 대조)", r.get("verdict") == "MATCH", str(r))
r = m.verify_one("10.1/sub", "Safe handling of nanotechnology: totally fabricated claims about outcomes")
check("verify: 조작 부제 → MISMATCH", r.get("verdict") == "MISMATCH", str(r))

# ---- 레드팀 R2: 철회 검사 fail-open 봉합 ----
# Crossref만 성공하고 OpenAlex(철회 판정 소스)가 죽으면 MATCH가 아니라 재시도 대상
_CR_PLAIN = json.dumps({"message": {"title": ["A perfectly ordinary paper title"],
                                    "issued": {"date-parts": [[2012]]},
                                    "author": [{"family": "Kumar"}]}})
m.http_get = lambda url, **kw: ((_CR_PLAIN, 200) if "crossref" in url else (None, 401))
r = m.verify_one("10.1/x", "A perfectly ordinary paper title", "Kumar", 2012)
check("verify: OpenAlex 장애 → RETRACTION_UNCHECKED(MATCH 아님)",
      r.get("verdict") == "RETRACTION_UNCHECKED", str(r))
check("verify: retraction_checked 플래그 노출", r.get("retraction_checked") is False, str(r))
m.http_get = lambda url, **kw: ((_CR_PLAIN, 200) if "crossref" in url else (None, 429))
r = m.verify_one("10.1/x", "A perfectly ordinary paper title", "Kumar", 2012)
check("verify: OpenAlex 429 → RETRACTION_UNCHECKED", r.get("verdict") == "RETRACTION_UNCHECKED", str(r))
# Crossref 2차 철회 소스는 `updated-by`다 — OpenAlex 없이도 판정.
# (`update-to`는 방향이 반대라 통지문 신호다. 실측: Wakefield 1998=updated-by,
#  Lancet 2010 공고문=update-to. 2026-07-28 실사용 발견으로 방향 정정.)
_CR_RETRACTED = json.dumps({"message": {
    "title": ["A perfectly ordinary paper title"],
    "issued": {"date-parts": [[2012]]}, "author": [{"family": "Kumar"}],
    "updated-by": [{"type": "retraction", "DOI": "10.1/notice"}]}})
m.http_get = lambda url, **kw: ((_CR_RETRACTED, 200) if "crossref" in url else (None, 401))
r = m.verify_one("10.1/x", "A perfectly ordinary paper title", "Kumar", 2012)
check("verify: Crossref updated-by=retraction → RETRACTED(OpenAlex 없이)",
      r.get("verdict") == "RETRACTED", str(r))
# 정상 경로: 두 API 다 응답하면 MATCH + retraction_checked=True
_OA_PLAIN = json.dumps({"display_name": "A perfectly ordinary paper title",
                        "publication_year": 2012, "is_retracted": False,
                        "authorships": [{"author": {"display_name": "Anil Kumar"}}]})
m.http_get = lambda url, **kw: ((_CR_PLAIN, 200) if "crossref" in url else (_OA_PLAIN, 200))
r = m.verify_one("10.1/x", "A perfectly ordinary paper title", "Kumar", 2012)
check("verify: 두 소스 정상 → MATCH", r.get("verdict") == "MATCH", str(r))
check("verify: retraction_checked=True", r.get("retraction_checked") is True, str(r))

# ---- 레드팀 R3: PubMed 3차 철회 소스 / DataCite 3차 서지 소스 / 비논문 레코드 ----
_slp0 = m.time.sleep
m.time.sleep = lambda *a: None  # E-utilities polite sleep 생략(테스트 가속)

_PM_ESEARCH = json.dumps({"esearchresult": {"idlist": ["42371203"]}})
_PM_RETRACTED = ('<PubmedArticleSet><PubmedArticle><PublicationTypeList>'
                 '<PublicationType UI="D016441">Retracted Publication</PublicationType>'
                 '</PublicationTypeList></PubmedArticle></PubmedArticleSet>')
_PM_CLEAN = ('<PubmedArticleSet><PubmedArticle><PublicationTypeList>'
             '<PublicationType UI="D016428">Journal Article</PublicationType>'
             '</PublicationTypeList></PubmedArticle></PubmedArticleSet>')
_DC_ARXIV = json.dumps({"data": {"attributes": {
    "titles": [{"title": "DeepSeek-R1: Incentivizing Reasoning Capability in LLMs"}],
    "publicationYear": 2025, "creators": [{"name": "DeepSeek-AI"}]}}})
_CR_PEERREVIEW = json.dumps({"message": {
    "title": ['Review for "Recovery of uranium using epoxy-modified sorbents"'],
    "type": "peer-review", "issued": {"date-parts": [[2024]]},
    "author": [{"family": "Anonymous"}]}})


def _mock_sources(cr=None, oa=None, pm=None, dc=None):
    """crossref/openalex/pubmed(esearch·efetch)/datacite 응답을 URL로 분기하는 모킹."""
    def f(url, **kw):
        if "crossref" in url:
            return (cr, 200) if cr else (None, 404)
        if "openalex" in url:
            return (oa, 200) if oa else (None, 404)
        if "esearch" in url:
            return (_PM_ESEARCH, 200) if pm else (None, 404)
        if "efetch" in url:
            return (pm, 200) if pm else (None, 404)
        if "datacite" in url:
            return (dc, 200) if dc else (None, 404)
        return (None, 404)
    return f


# 두 소스가 '철회 아님'으로 정상 응답해도 PubMed가 낙인했으면 RETRACTED
m._PUBMED_RETRACTION_CACHE.clear()
m.http_get = _mock_sources(_CR_PLAIN, _OA_PLAIN, _PM_RETRACTED)
r = m.verify_one("10.1/pm-retracted", "A perfectly ordinary paper title", "Kumar", 2012)
check("verify: PubMed만 철회 낙인 → RETRACTED(2소스 정상 응답이어도)",
      r.get("verdict") == "RETRACTED", str(r))
check("verify: 철회 판정 소스에 pubmed 등재",
      "pubmed" in (r.get("retraction_sources") or []), str(r))
# PubMed가 '철회 아님'을 확인하면 MATCH 유지 + 소스 목록에 등재
m._PUBMED_RETRACTION_CACHE.clear()
m.http_get = _mock_sources(_CR_PLAIN, _OA_PLAIN, _PM_CLEAN)
r = m.verify_one("10.1/pm-clean", "A perfectly ordinary paper title", "Kumar", 2012)
check("verify: PubMed 정상(철회 아님) → MATCH 유지", r.get("verdict") == "MATCH", str(r))
check("verify: 철회 판정 소스 3종 노출",
      (r.get("retraction_sources") or []) == ["crossref", "openalex", "pubmed"], str(r))
# PubMed 미색인(arXiv·인문학 DOI)은 기존 2소스 판정을 낮추지 않는다
m._PUBMED_RETRACTION_CACHE.clear()
m.http_get = _mock_sources(_CR_PLAIN, _OA_PLAIN, None)
r = m.verify_one("10.1/pm-absent", "A perfectly ordinary paper title", "Kumar", 2012)
check("verify: PubMed 미색인 → MATCH 유지(열화 금지)", r.get("verdict") == "MATCH", str(r))
check("verify: PubMed 미색인 시 소스 목록에서 제외",
      "pubmed" not in (r.get("retraction_sources") or []), str(r))

# DataCite 폴백 — arXiv 전용 DOI가 NOT_FOUND(가짜 인용 신호)로 낙인되던 허점
m._PUBMED_RETRACTION_CACHE.clear()
m.http_get = _mock_sources(None, None, None, _DC_ARXIV)
r = m.verify_one("10.48550/arxiv.2501.12948",
                 "DeepSeek-R1: Incentivizing Reasoning Capability in LLMs",
                 "DeepSeek-AI", 2025)
check("verify: DataCite 폴백 → NOT_FOUND 아님", r.get("verdict") != "NOT_FOUND", str(r))
# 레드팀 R4: DataCite 전용 DOI는 철회 플래그가 구조적으로 없어 재시도해도 영원히
# retraction_checked=False다. RETRACTION_UNCHECKED(exit 5, 재시도)로 두면 정직한
# DOI 기재가 영구 교착에 빠지고 DOI 삭제(NO_DOI, exit 2)가 유일한 탈출구가 된다 —
# 데이터 삭제가 보상받는 역인센티브. RETRACTION_NA(exit 2)로 분리한다.
check("verify: DataCite 전용은 RETRACTION_NA(재시도 아닌 구조적 미검증)",
      r.get("verdict") == "RETRACTION_NA", str(r))
check("verify: RETRACTION_NA는 exit 2(예외 경로 — 교착 아님)",
      m.VERIFY_EXIT.get("RETRACTION_NA") == 2, str(m.VERIFY_EXIT))
check("verify: DataCite 서지로 제목 대조 수행", (r.get("similarity") or 0) > 0.99, str(r))
m._PUBMED_RETRACTION_CACHE.clear()
m.http_get = _mock_sources(None, None, None, None)
r = m.verify_one("10.9999/none", "x", "Y", 2020)
check("verify: 세 소스 모두 404 → NOT_FOUND 유지", r.get("verdict") == "NOT_FOUND", str(r))

# ---- 레드팀 R4: 정오표·Author Correction 통지가 원논문으로 위장하던 허점 ----
# Crossref는 정오표를 journal-article로 분류하므로 type 검사로는 안 걸린다.
# 정밀 신호는 update-to의 type(정오표=correction, 원논문=빈 배열)이다.
_CR_ERRATUM = json.dumps({"message": {
    "title": ["Author Correction: VAMPnets for deep learning of molecular kinetics"],
    "type": "journal-article", "issued": {"date-parts": [[2018]]},
    "author": [{"family": "Mardt"}],
    "update-to": [{"type": "correction", "DOI": "10.1038/s41467-018-06081-9"}]}})
_OA_ERRATUM = json.dumps({
    "display_name": "Author Correction: VAMPnets for deep learning of molecular kinetics",
    "publication_year": 2018, "is_retracted": False, "type": "erratum",
    "authorships": [{"author": {"display_name": "Andreas Mardt"}}]})
m._PUBMED_RETRACTION_CACHE.clear()
m.http_get = _mock_sources(_CR_ERRATUM, _OA_ERRATUM, _PM_CLEAN)
r = m.verify_one("10.1038/s41467-018-06999-0",
                 "VAMPnets for deep learning of molecular kinetics", "Mardt", 2018)
check("verify: 정오표 DOI를 원논문 제목으로 인용 → NON_ARTICLE(MATCH 아님)",
      r.get("verdict") == "NON_ARTICLE", str(r))
check("verify: 정오표 판정 플래그 노출", r.get("is_correction") is True, str(r))
# update-to가 없어도 제목 접두('Author Correction: ')만으로 잡아야 한다
_CR_ERRATUM_NOUP = json.dumps({"message": {
    "title": ["Author Correction: VAMPnets for deep learning of molecular kinetics"],
    "type": "journal-article", "issued": {"date-parts": [[2018]]},
    "author": [{"family": "Mardt"}]}})
m._PUBMED_RETRACTION_CACHE.clear()
m.http_get = _mock_sources(_CR_ERRATUM_NOUP, None, _PM_CLEAN)
r = m.verify_one("10.1038/x-corr",
                 "VAMPnets for deep learning of molecular kinetics", "Mardt", 2018)
check("verify: update-to 없어도 'Author Correction:' 제목 접두로 NON_ARTICLE",
      r.get("verdict") == "NON_ARTICLE", str(r))
# Corrigendum/Erratum 접두 변형도 동일 처리
for _pfx in ("Corrigendum to: ", "Erratum: ", "Publisher Correction: "):
    _cr = json.dumps({"message": {"title": [_pfx + "A perfectly ordinary paper title"],
                                  "type": "journal-article",
                                  "issued": {"date-parts": [[2012]]},
                                  "author": [{"family": "Kumar"}]}})
    m._PUBMED_RETRACTION_CACHE.clear()
    m.http_get = _mock_sources(_cr, None, _PM_CLEAN)
    r = m.verify_one("10.1/c", "A perfectly ordinary paper title", "Kumar", 2012)
    check(f"verify: '{_pfx.strip()}' 접두 → NON_ARTICLE", r.get("verdict") == "NON_ARTICLE", str(r))
# 오탐 방어: 제목에 correction이 들어간 정상 논문(10.1038/nature23305)은 통과해야 한다.
# OpenAlex는 이 논문을 erratum으로 오분류하므로 OpenAlex type을 단독 신호로 쓰면 안 된다.
_CR_REAL_CORR = json.dumps({"message": {
    "title": ["Correction of a pathogenic gene mutation in human embryos"],
    "type": "journal-article", "issued": {"date-parts": [[2017]]},
    "author": [{"family": "Ma"}],
    "update-to": [{"type": "expression_of_concern", "DOI": "10.1038/x"}]}})
_OA_REAL_CORR = json.dumps({
    "display_name": "Correction of a pathogenic gene mutation in human embryos",
    "publication_year": 2017, "is_retracted": False, "type": "erratum",
    "authorships": [{"author": {"display_name": "Hong Ma"}}]})
m._PUBMED_RETRACTION_CACHE.clear()
m.http_get = _mock_sources(_CR_REAL_CORR, _OA_REAL_CORR, _PM_CLEAN)
r = m.verify_one("10.1038/nature23305",
                 "Correction of a pathogenic gene mutation in human embryos", "Ma", 2017)
check("verify: 제목에 'Correction of'가 든 정상 논문은 MATCH(정오표 오탐 방어)",
      r.get("verdict") == "MATCH", str(r))

# ---- 실사용 발견(2026-07-28): 철회 **통지문**이 철회당한 논문으로 오판되던 허점 ----
# update-to/relation은 방향이 있다 — 이 필드를 든 쪽은 "X를 철회한다"는 통지문이다.
# Lancet 2010 철회 공고문이 RETRACTED(exit 4)로 찍혀, 철회를 정당하게 서술하는
# 답변이 게이트 A를 통과할 수 없었다.
_CR_RETNOTICE = json.dumps({"message": {
    "title": ["Retraction—Ileal-lymphoid-nodular hyperplasia, non-specific colitis, "
              "and pervasive developmental disorder in children"],
    "type": "journal-article", "issued": {"date-parts": [[2010]]},
    "author": [{"family": "The Editors of The Lancet"}],
    "update-to": [{"type": "retraction", "DOI": "10.1016/s0140-6736(97)11096-0"}]}})
_OA_RETNOTICE = json.dumps({
    "display_name": "Retraction—Ileal-lymphoid-nodular hyperplasia, non-specific colitis, "
                    "and pervasive developmental disorder in children",
    "publication_year": 2010, "is_retracted": False, "type": "article",
    "authorships": [{"author": {"display_name": "The Editors of The Lancet"}}]})
m._PUBMED_RETRACTION_CACHE.clear()
m.http_get = _mock_sources(_CR_RETNOTICE, _OA_RETNOTICE, _PM_CLEAN)
r = m.verify_one("10.1016/s0140-6736(10)60175-4",
                 "Retraction—Ileal-lymphoid-nodular hyperplasia, non-specific colitis, "
                 "and pervasive developmental disorder in children",
                 "The Editors of The Lancet", 2010)
check("verify: 철회 통지문 → NON_ARTICLE(RETRACTED 아님)",
      r.get("verdict") == "NON_ARTICLE", str(r))
check("verify: 철회 통지문 플래그 노출", r.get("is_retraction_notice") is True, str(r))
check("verify: 철회 통지문은 retracted로 표시되지 않는다",
      r.get("verdict") != "RETRACTED", str(r))
# 원논문(RETRACTED: 접두 + is_retracted)은 여전히 RETRACTED여야 한다 — 방향 구분 확인
_CR_RETRACTED_ORIG = json.dumps({"message": {
    "title": ["RETRACTED: Ileal-lymphoid-nodular hyperplasia, non-specific colitis, "
              "and pervasive developmental disorder in children"],
    "type": "journal-article", "issued": {"date-parts": [[1998]]},
    "author": [{"family": "Wakefield"}]}})
_OA_RETRACTED_ORIG = json.dumps({
    "display_name": "RETRACTED: Ileal-lymphoid-nodular hyperplasia, non-specific colitis, "
                    "and pervasive developmental disorder in children",
    "publication_year": 1998, "is_retracted": True, "type": "article",
    "authorships": [{"author": {"display_name": "Andrew J Wakefield"}}]})
m._PUBMED_RETRACTION_CACHE.clear()
m.http_get = _mock_sources(_CR_RETRACTED_ORIG, _OA_RETRACTED_ORIG, _PM_CLEAN)
r = m.verify_one("10.1016/s0140-6736(97)11096-0",
                 "Ileal-lymphoid-nodular hyperplasia, non-specific colitis, "
                 "and pervasive developmental disorder in children", "Wakefield", 1998)
check("verify: 철회 원논문은 여전히 RETRACTED(방향 구분 유지)",
      r.get("verdict") == "RETRACTED", str(r))
# 통지문 제목 변형 — 'Retraction notice: X' / 'Withdrawal of X' / 'Notice of Retraction: X'
for _t in ("Retraction notice: Some paper about X",
           "Withdrawal: Some paper about X",
           "Notice of Retraction: Some paper about X"):
    _cr = json.dumps({"message": {"title": [_t], "type": "journal-article",
                                  "issued": {"date-parts": [[2015]]},
                                  "author": [{"family": "Editors"}]}})
    m._PUBMED_RETRACTION_CACHE.clear()
    m.http_get = _mock_sources(_cr, None, _PM_CLEAN)
    r = m.verify_one("10.1/rn", _t, "Editors", 2015)
    check(f"verify: '{_t[:22]}…' 통지문 제목 → NON_ARTICLE",
          r.get("verdict") == "NON_ARTICLE", str(r))
# 오탐 방어: 제목이 'Retraction'으로 시작하지만 구분자가 없는 정상 연구논문
_CR_RET_STUDY = json.dumps({"message": {
    "title": ["Retraction rates in biomedical journals: a longitudinal analysis"],
    "type": "journal-article", "issued": {"date-parts": [[2021]]},
    "author": [{"family": "Nakamura"}]}})
_OA_RET_STUDY = json.dumps({
    "display_name": "Retraction rates in biomedical journals: a longitudinal analysis",
    "publication_year": 2021, "is_retracted": False, "type": "article",
    "authorships": [{"author": {"display_name": "Kei Nakamura"}}]})
m._PUBMED_RETRACTION_CACHE.clear()
m.http_get = _mock_sources(_CR_RET_STUDY, _OA_RET_STUDY, _PM_CLEAN)
r = m.verify_one("10.1/retstudy",
                 "Retraction rates in biomedical journals: a longitudinal analysis",
                 "Nakamura", 2021)
check("verify: '철회'를 연구주제로 다루는 정상 논문은 MATCH(통지문 오탐 방어)",
      r.get("verdict") == "MATCH", str(r))

# 비논문 레코드(심사보고서) — DOI·제목이 실재해도 [P#] 근거가 아니다
m._PUBMED_RETRACTION_CACHE.clear()
m.http_get = _mock_sources(_CR_PEERREVIEW, None, None)
r = m.verify_one("10.1039/d4ra04688a/v1/review2",
                 'Review for "Recovery of uranium using epoxy-modified sorbents"',
                 "Anonymous", 2024)
check("verify: peer-review 레코드 → NON_ARTICLE(MATCH 아님)",
      r.get("verdict") == "NON_ARTICLE", str(r))

# year 관용 파싱 — 'in press'·'2019a'가 int()에서 트레이스백을 내던 허점
m._PUBMED_RETRACTION_CACHE.clear()
m.http_get = _mock_sources(_CR_PLAIN, _OA_PLAIN, _PM_CLEAN)
r = m.verify_one("10.1/y1", "A perfectly ordinary paper title", "Kumar", "2012a")
check("verify: year='2012a' → 4자리 추출 후 대조(MATCH)",
      r.get("verdict") == "MATCH" and r.get("year_ok") is True, str(r))
r = m.verify_one("10.1/y2", "A perfectly ordinary paper title", "Kumar", "in press",
                 require_meta=True)
check("verify: year='in press' → 크래시 없이 INCOMPLETE_META",
      r.get("verdict") == "INCOMPLETE_META" and "year" in (r.get("meta_missing") or []), str(r))
m.time.sleep = _slp0

# 레드팀 R2: 의미 반전 인용(부정어 삭제)이 sim 0.95+로 MATCH되던 허점 — 실측 재현
_CR_NEG = json.dumps({"message": {
    "title": ["Neuroblastoma screening does not reduce mortality"],
    "issued": {"date-parts": [[2002]]}, "author": [{"family": "Tuffs"}]}})
_OA_NEG = json.dumps({"display_name": "Neuroblastoma screening does not reduce mortality",
                      "publication_year": 2002, "is_retracted": False,
                      "authorships": [{"author": {"display_name": "Annette Tuffs"}}]})
m.http_get = lambda url, **kw: ((_CR_NEG, 200) if "crossref" in url else (_OA_NEG, 200))
r = m.verify_one("10.1/neg", "Neuroblastoma screening does reduce mortality", "Tuffs", 2002)
check("verify: 부정어 삭제 인용 → MISMATCH(sim 0.95+여도)",
      r.get("verdict") == "MISMATCH", str(r))

# 레드팀 R2: author·year 미기재를 batch가 조용히 MATCH로 통과시키던 허점
m.http_get = lambda url, **kw: ((_CR_PLAIN, 200) if "crossref" in url else (_OA_PLAIN, 200))
r = m.verify_one("10.1/x", "A perfectly ordinary paper title", require_meta=True)
check("verify: require_meta + 저자·연도 미기재 → INCOMPLETE_META",
      r.get("verdict") == "INCOMPLETE_META", str(r))
check("verify: meta_missing에 미기재 필드 노출",
      set(r.get("meta_missing") or []) == {"author", "year"}, str(r))
r = m.verify_one("10.1/x", "A perfectly ordinary paper title")
check("verify: 단건 verify는 author·year 선택 유지(MATCH)", r.get("verdict") == "MATCH", str(r))

# ---- cmd_verify_batch 오프라인 ----
def _run_batch(refs):
    path = os.path.join(tempfile.gettempdir(), "scholar_selftest_refs.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(refs, f)
    out, code = io.StringIO(), None
    try:
        with contextlib.redirect_stdout(out):
            m.cmd_verify_batch(argparse.Namespace(file=path, json=False))
    except SystemExit as e:
        code = e.code
    return out.getvalue(), code

_slp = m.time.sleep
m.time.sleep = lambda *a: None  # 배치 polite sleep 생략(테스트 가속)

# 레드팀 R1: doi 없는 항목에서 TypeError로 배치 전체가 죽던 허점
# 레드팀 R2: 실패가 전부 NO_DOI면 exit 3(가짜 인용)이 아니라 exit 2(수동 대조 경로) —
# SKILL.md가 'NO_DOI는 수동 대조 후 인용 유지'라면서 exit 0을 요구하던 모순 해소
m.http_get = lambda url, **kw: (None, -1)
out, code = _run_batch([{"id": "P1", "title": "Attention Is All You Need",
                         "author": "Vaswani", "year": 2017}])
check("verify-batch: doi 누락 항목 → 크래시 없이 exit 2(NO_DOI 전용)", code == 2, f"exit={code}")
check("verify-batch: NO_DOI 행에 사유 출력", "doi 누락" in out, out)
check("verify-batch: 요약줄에 NO_DOI 건수 명시", "NO_DOI 1건" in out, out)
# 레드팀 R1: 실패가 전부 일시 장애면 exit 5(재시도) — '가짜 인용'(3)과 구분
out, code = _run_batch([{"id": "P1", "doi": "10.1038/nature14539", "title": "Deep learning",
                         "author": "LeCun", "year": 2015}])
check("verify-batch: 전부 UNVERIFIED → exit 5", code == 5, f"exit={code}\n{out}")
check("verify-batch: UNVERIFIED 행에 실패 사유(detail) 출력", "crossref -1" in out, out)

# 레드팀 R2: 빈 refs.json이 '0/0 MATCH' exit 0으로 게이트를 통과하던 허점
out, code = _run_batch([])
check("verify-batch: 빈 배열 → exit 2(통과 아님)", code == 2, f"exit={code}\n{out}")
check("verify-batch: 빈 배열에 '0/0 MATCH' 출력 금지", "0/0 MATCH" not in out, out)

# 레드팀 R2: author·year를 지우면 검사가 생략된 채 ✓ MATCH로 집계되던 역인센티브
m.http_get = lambda url, **kw: ((_CR_PLAIN, 200) if "crossref" in url else (_OA_PLAIN, 200))
out, code = _run_batch([{"id": "P1", "doi": "10.1/x",
                         "title": "A perfectly ordinary paper title"}])
check("verify-batch: author·year 미기재 → exit 6(무성 통과 금지)", code == 6, f"exit={code}\n{out}")
check("verify-batch: 요약줄에 미기재 건수 명시", "저자·연도 미기재 1건" in out, out)
out, code = _run_batch([{"id": "P1", "doi": "10.1/x", "author": "Kumar", "year": 2012,
                         "title": "A perfectly ordinary paper title"}])
check("verify-batch: author·year 기재 시 exit 0", code == 0, f"exit={code}\n{out}")

# 레드팀 R2: MATCH 행이 sim<1.0인데도 실제 제목을 안 보여줘 왜곡 인용이 은폐되던 허점
out, code = _run_batch([{"id": "P1", "doi": "10.1/x", "author": "Kumar", "year": 2012,
                         "title": "A perfectly ordinary paper title CVPR"}])
check("verify-batch: sim<1.0 MATCH 행에 실제 제목 노출",
      "실제:" in out and "A perfectly ordinary paper title" in out, out)

# 레드팀 R2: 하드 실패가 섞이면 재시도·수동대조 코드에 묻히지 않고 exit 3
m.http_get = lambda url, **kw: ((_CR_PLAIN, 200) if "crossref" in url else (_OA_PLAIN, 200))
out, code = _run_batch([{"id": "P1", "doi": "10.1/x", "author": "Kumar", "year": 2012,
                         "title": "A completely different fabricated title"},
                        {"id": "P2", "title": "no doi item", "author": "X", "year": 2020}])
check("verify-batch: 하드 실패 우선 → exit 3", code == 3, f"exit={code}\n{out}")

# ---- 레드팀 R3: 중복 DOI(근거 폭 부풀리기) ----
m._PUBMED_RETRACTION_CACHE.clear()
m.http_get = _mock_sources(_CR_PLAIN, _OA_PLAIN, _PM_CLEAN)
out, code = _run_batch([
    {"id": "P1", "doi": "10.1/dup", "author": "Kumar", "year": 2012,
     "title": "A perfectly ordinary paper title"},
    {"id": "P2", "doi": "https://doi.org/10.1/DUP", "author": "Kumar", "year": 2012,
     "title": "A perfectly ordinary paper title"}])
check("verify-batch: 중복 DOI 요약줄에 ⚠ 표기(표기 변형 포함)",
      "중복 DOI" in out and "P1·P2" in out, out)
check("verify-batch: 고유 DOI 수 명시", "고유 DOI 1/2" in out, out)
check("verify-batch: 중복이어도 검증 자체는 통과(exit 0 유지)", code == 0, f"exit={code}\n{out}")
check("verify-batch: 요약줄에 철회 판정 소스·커버리지 표기",
      "판정 소스: crossref 2/2+openalex 2/2+pubmed 2/2" in out, out)

# ---- 레드팀 R3: 비정형 refs.json 항목이 트레이스백+exit 1로 배치를 죽이던 허점 ----
crashed = None
try:
    out, code = _run_batch(["10.1093/oep/gpf045"])
except Exception as e:
    crashed, out, code = repr(e), "", None
check("verify-batch: 문자열 항목 → 크래시 없이 exit 6(BAD_ENTRY)",
      crashed is None and code == 6, f"exit={code} crash={crashed}\n{out}")
check("verify-batch: BAD_ENTRY여도 요약줄 출력", "인용 검증:" in out, out)
crashed = None
try:
    out, code = _run_batch([
        {"id": "P1", "doi": "10.1/x", "author": "Kumar", "year": 2012,
         "title": "A perfectly ordinary paper title"},
        {"id": "P2", "doi": "10.1/z", "author": "Kumar", "year": "in press",
         "title": "A perfectly ordinary paper title"}])
except Exception as e:
    crashed, out, code = repr(e), "", None
check("verify-batch: year='in press' → 크래시 없이 배치 완주",
      crashed is None and "인용 검증:" in out and code in (6,),
      f"exit={code} crash={crashed}\n{out}")
check("verify-batch: 앞 항목 검증 결과는 보존", "[P1] MATCH" in out, out)

# ---- 레드팀 R3: exit 2(수동 대조 예외 승인)는 실패 전원이 NO_DOI일 때만 ----
out, code = _run_batch([
    {"id": "P1", "title": "arXiv preprint without doi", "author": "Kim", "year": 2025},
    {"id": "P2", "doi": "10.1/x", "title": "A perfectly ordinary paper title"}])
check("verify-batch: NO_DOI+INCOMPLETE_META 혼재 → exit 6(2 아님)", code == 6,
      f"exit={code}\n{out}")
out, code = _run_batch([
    {"id": "P1", "title": "arXiv preprint without doi", "author": "Kim", "year": 2025},
    {"id": "P2", "title": "another arXiv preprint", "author": "Lee", "year": 2026}])
check("verify-batch: 실패 전원 NO_DOI → exit 2 유지", code == 2, f"exit={code}\n{out}")

# ---- 레드팀 R4: arXiv 전용 DOI를 정직하게 기재하면 영구 교착(exit 5)이고
# DOI를 지우면 예외 경로(exit 2)로 통과하던 역인센티브 ----
m._PUBMED_RETRACTION_CACHE.clear()
m.http_get = _mock_sources(None, None, None, _DC_ARXIV)
out, code = _run_batch([
    {"id": "P1", "doi": "10.48550/arxiv.2501.12948", "author": "DeepSeek-AI", "year": 2025,
     "title": "DeepSeek-R1: Incentivizing Reasoning Capability in LLMs"}])
check("verify-batch: DataCite 전용 DOI 정직 기재 → exit 2(교착 아님)", code == 2,
      f"exit={code}\n{out}")
check("verify-batch: RETRACTION_NA 행에 '재시도 무의미' 명시",
      "재시도 무의미" in out, out)
check("verify-batch: 요약줄에 철회검사 불가 건수 명시", "철회검사 불가" in out, out)
# DOI를 지운 쪽(NO_DOI)보다 불리하지 않아야 한다 — 두 경로 모두 exit 2
m.http_get = lambda url, **kw: (None, -1)
out2, code2 = _run_batch([
    {"id": "P1", "author": "DeepSeek-AI", "year": 2025,
     "title": "DeepSeek-R1: Incentivizing Reasoning Capability in LLMs"}])
check("verify-batch: DOI 삭제(NO_DOI)가 정직 기재보다 유리하지 않다(둘 다 exit 2)",
      code2 == 2 and code == 2, f"strip={code2} honest={code}")
# 예외 유형에 하드 실패가 섞이면 여전히 3
m._PUBMED_RETRACTION_CACHE.clear()
def _mix(url, **kw):
    if "10.48550" in url or "2501.12948" in url:
        return (None, 404)
    return _mock_sources(_CR_PLAIN, _OA_PLAIN, _PM_CLEAN)(url, **kw)
m.http_get = _mix
out, code = _run_batch([
    {"id": "P1", "doi": "10.1/x", "author": "Kumar", "year": 2012,
     "title": "A completely different fabricated title"},
    {"id": "P2", "doi": "10.48550/arxiv.2501.12948", "author": "DeepSeek-AI", "year": 2025,
     "title": "DeepSeek-R1: Incentivizing Reasoning Capability in LLMs"}])
check("verify-batch: RETRACTION_NA에 하드 실패가 섞이면 exit 3", code == 3, f"exit={code}\n{out}")

# ---- 레드팀 R4: 절단 인용으로 통과한 행에 생략 토큰 노출 ----
# 꼬리 절단형 범위 축소는 정당 축약과 형태가 같아 기계 차단이 불가 —
# 무엇이 생략됐는지를 출력해 육안 대조의 대상을 만든다
_VITD = ("Vitamin D supplementation and total cancer incidence and mortality: "
         "a meta-analysis of randomized controlled trials")
_CR_VITD = json.dumps({"message": {"title": [_VITD],
                                   "issued": {"date-parts": [[2019]]},
                                   "author": [{"family": "Keum"}]}})
_OA_VITD = json.dumps({"display_name": _VITD, "publication_year": 2019,
                       "is_retracted": False,
                       "authorships": [{"author": {"display_name": "N. Keum"}}]})
m._PUBMED_RETRACTION_CACHE.clear()
m.http_get = _mock_sources(_CR_VITD, _OA_VITD, _PM_CLEAN)
out, code = _run_batch([{"id": "P1", "doi": "10.1093/annonc/mdz059", "author": "Keum",
                         "year": 2019,
                         "title": "Vitamin D supplementation and total cancer incidence"}])
check("verify-batch: 절단 통과 행에 '⚠ 생략' + 생략 토큰 노출",
      "생략:" in out and "mortality" in out, out)
# 연구설계 상용구(meta-analysis·randomized controlled trial)는 범위 축소 신호가
# 아니라 부제 절단의 부산물 — 생략 목록에 섞이면 진짜 신호가 묻힌다
check("verify-batch: 생략 목록에서 연구설계 상용구 제외(신호 희석 방지)",
      "randomized" not in out.split("생략:")[1].split("—")[0], out)
check("omitted_tokens: 어간이 아니라 원 표기로 반환(육안 대조용)",
      m.omitted_tokens("Highly accurate protein structure prediction",
                       "Highly accurate protein structure prediction with AlphaFold")
      == ["alphafold"],
      str(m.omitted_tokens("Highly accurate protein structure prediction",
                           "Highly accurate protein structure prediction with AlphaFold")))

# ---- 레드팀 R3: MATCH 행의 실제 제목 80자 절단(육안 대조 무력화) ----
_LONG = ("A Randomized, Controlled Trial of the Use of Pulmonary-Artery Catheters "
         "in High-Risk Surgical Patients")
_CR_LONG = json.dumps({"message": {"title": [_LONG],
                                   "issued": {"date-parts": [[2003]]},
                                   "author": [{"family": "Sandham"}]}})
_OA_LONG = json.dumps({"display_name": _LONG, "publication_year": 2003,
                       "is_retracted": False,
                       "authorships": [{"author": {"display_name": "J. Sandham"}}]})
m._PUBMED_RETRACTION_CACHE.clear()
m.http_get = _mock_sources(_CR_LONG, _OA_LONG, _PM_CLEAN)
out, code = _run_batch([{"id": "P1", "doi": "10.1056/nejmoa021108", "author": "Sandham",
                         "year": 2003, "title": _LONG + " CVPR"}])
check("verify-batch: 긴 실제 제목을 80자에서 자르지 않는다(꼬리 노출)",
      "High-Risk Surgical Patients'" in out, out)
# 편측 덧붙임(모집단 날조)은 애초에 MATCH가 아니어야 한다
out, code = _run_batch([{"id": "P1", "doi": "10.1056/nejmoa021108", "author": "Sandham",
                         "year": 2003, "title": _LONG + " in Children"}])
check("verify-batch: 모집단 덧붙임 인용 → exit 3(MATCH 아님)", code == 3, f"exit={code}\n{out}")

# ---- 레드팀 R3: 비논문 레코드(심사보고서)는 배치에서도 하드 실패 ----
m._PUBMED_RETRACTION_CACHE.clear()
m.http_get = _mock_sources(_CR_PEERREVIEW, None, None)
out, code = _run_batch([{"id": "P1", "doi": "10.1039/d4ra04688a/v1/review2",
                         "author": "Anonymous", "year": 2024,
                         "title": 'Review for "Recovery of uranium using epoxy-modified sorbents"'}])
check("verify-batch: 심사보고서 레코드 → exit 3", code == 3, f"exit={code}\n{out}")
check("verify-batch: 요약줄에 비논문 건수 명시", "비논문 레코드" in out, out)
m.time.sleep = _slp

# ---- cmd_paper: '#' DOI 인코딩 ----
_urls = []
m.http_get = _cap404
code = None
try:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        m.cmd_paper(argparse.Namespace(id="10.1000/abc#frag", json=False))
except SystemExit as e:
    code = e.code
check("paper: '#' DOI 인코딩", _urls and "#" not in _urls[0] and "%23" in _urls[0], str(_urls))

# ---- cmd_snowball ----
# 레드팀 R1: cites 2차 호출 실패가 조용한 빈 출력+exit 0으로 위장하던 허점
_seq = {"n": 0}
def _sb_cites_fail(url, **kw):
    _seq["n"] += 1
    if _seq["n"] == 1:
        return (json.dumps({"id": "https://openalex.org/W123"}), 200)
    return (None, 429)
m.http_get = _sb_cites_fail
code = None
try:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        m.cmd_snowball(argparse.Namespace(doi="10.1/x", direction="cites", limit=5))
except SystemExit as e:
    code = e.code
check("snowball cites: 목록 조회 실패 → exit 1(조용한 빈 출력 금지)", code == 1, f"exit={code}")

# 레드팀 R1: refs가 ID 오름차순(색인순) 절단으로 핵심 참고문헌을 놓치던 허점
_urls = []
def _sb_refs(url, **kw):
    _urls.append(url)
    if "referenced_works" in url:
        return (json.dumps({"referenced_works":
                            ["https://openalex.org/W9", "https://openalex.org/W2"]}), 200)
    return (json.dumps({"results": [{"doi": None, "display_name": "t",
                                     "publication_year": 2020, "cited_by_count": 1}]}), 200)
m.http_get = _sb_refs
with contextlib.redirect_stdout(io.StringIO()):
    m.cmd_snowball(argparse.Namespace(doi="10.1/x", direction="refs", limit=5))
check("snowball refs: 인용수 내림차순 배치 조회",
      any("openalex_id%3A" in u and "cited_by_count%3Adesc" in u for u in _urls), str(_urls))

def _sb_refs_fail(url, **kw):
    if "referenced_works" in url:
        return (json.dumps({"referenced_works": ["https://openalex.org/W9"]}), 200)
    return (None, 429)
m.http_get = _sb_refs_fail
code = None
try:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        m.cmd_snowball(argparse.Namespace(doi="10.1/x", direction="refs", limit=5))
except SystemExit as e:
    code = e.code
check("snowball refs: 배치 조회 실패 → exit 1", code == 1, f"exit={code}")

# ---- 레드팀 R2: snowball 최신성(cites 인용수순 고정) ----
_urls = []
def _sb_ok(url, **kw):
    _urls.append(url)
    if "select=id" in url:
        return (json.dumps({"id": "https://openalex.org/W123"}), 200)
    return (json.dumps({"results": []}), 200)
m.http_get = _sb_ok
with contextlib.redirect_stdout(io.StringIO()):
    m.cmd_snowball(argparse.Namespace(doi="10.1/x", direction="cites", limit=5,
                                      year_from=2025, sort="citations"))
check("snowball cites: --year-from → from_publication_date 필터",
      any("from_publication_date%3A2025-01-01" in u for u in _urls), str(_urls))
_urls.clear()
with contextlib.redirect_stdout(io.StringIO()):
    m.cmd_snowball(argparse.Namespace(doi="10.1/x", direction="cites", limit=5,
                                      year_from=None, sort="date"))
check("snowball cites: --sort date → publication_date:desc",
      any("publication_date%3Adesc" in u for u in _urls), str(_urls))
_urls.clear()
with contextlib.redirect_stdout(io.StringIO()):
    m.cmd_snowball(argparse.Namespace(doi="10.1/x", direction="cites", limit=5,
                                      year_from=None, sort="citations"))
check("snowball cites: 기본은 인용수순 유지",
      any("cited_by_count%3Adesc" in u for u in _urls), str(_urls))

# ---- 레드팀 R2: 빈 DOI에서 quote(None) TypeError로 죽던 oa/cite/recommend ----
for name, fn, ns in (
        ("oa", m.cmd_oa, argparse.Namespace(doi="https://doi.org/")),
        ("cite", m.cmd_cite, argparse.Namespace(doi="  ", style="apa")),
        ("recommend", m.cmd_recommend, argparse.Namespace(doi="https://doi.org/", limit=5))):
    code, crashed = None, None
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            fn(ns)
    except SystemExit as e:
        code = e.code
    except Exception as e:  # TypeError 트레이스백 = 회귀
        crashed = repr(e)
    check(f"{name}: 빈 DOI → 정돈된 에러 exit 1(크래시 없음)",
          crashed is None and code == 1, f"exit={code} crash={crashed}")

# ---- 레드팀 R2: arXiv XML 엔티티 복원 + --year-from ----
_ATOM = ("<feed><entry><id>http://arxiv.org/abs/2001.00001v1</id>"
         "<published>2020-01-01T00:00:00Z</published>"
         "<title>Deep Learning &amp; Graphs: A &lt;Survey&gt;</title>"
         "<name>Jos&#233; Garc&#237;a</name></entry></feed>")
m.http_get = lambda url, **kw: (_ATOM, 200)
ar, _e = m.search_arxiv("x", 5)
check("arxiv: XML 엔티티(&amp;·&lt;) 복원",
      ar and ar[0]["title"] == "Deep Learning & Graphs: A <Survey>", str(ar))
check("arxiv: 저자 숫자 엔티티 복원", ar and ar[0]["authors"] == ["José García"], str(ar))
pool = [{"source": "openalex", "doi": "10.1/a", "title": "Deep Learning & Graphs: A Survey",
         "year": 2020, "citations": 5, "is_oa": True}, ar[0]]
check("dedup: 엔티티 복원 후 arXiv-출판본 트윈 병합", len(m.merge_and_rank(pool, 10)) == 1,
      str(m.merge_and_rank(pool, 10)))

_urls = []
def _arxiv_dated(url, **kw):
    _urls.append(url)
    if "submittedDate" in url:
        return ("<feed></feed>", 200)  # 날짜 절 미지원 시뮬 → 재조회 경로
    return (_ATOM, 200)
m.http_get = _arxiv_dated
ar2, _e = m.search_arxiv("x", 5, year_from=2024)
check("arxiv: --year-from → submittedDate 범위 필터 전달",
      any("submittedDate" in u for u in _urls), str(_urls))
check("arxiv: 날짜절 무효 시 재조회 후 클라이언트측 연도 필터", ar2 == [], str(ar2))
m.http_get = lambda url, **kw: (_ATOM, 200)
ar3, _e = m.search_arxiv("x", 5, year_from=2019)
check("arxiv: year_from 이후 항목은 유지", len(ar3) == 1, str(ar3))

# ---- 레드팀 R4: arXiv가 다중 토큰 질의를 OR로 파싱해 정밀도가 붕괴하던 허점 ----
check("arxiv: 다중 키워드는 토큰별 AND로 조인(암묵 OR 방어)",
      m._arxiv_query("retrieval augmented generation hallucination")
      == "all:retrieval AND all:augmented AND all:generation AND all:hallucination",
      m._arxiv_query("retrieval augmented generation hallucination"))
check("arxiv: 단일 토큰은 그대로", m._arxiv_query("uranium") == "all:uranium")
check("arxiv: 사용자가 넣은 불리언 질의는 패스스루",
      m._arxiv_query("uranium OR thorium") == "all:uranium OR thorium")
check("arxiv: 따옴표 구문 질의는 패스스루",
      m._arxiv_query('"in situ recovery"') == 'all:"in situ recovery"')
_urls = []
def _cap_atom(url, **kw):
    _urls.append(url)
    return (_ATOM, 200)
m.http_get = _cap_atom
m.search_arxiv("retrieval augmented generation", 5)
check("arxiv: 실제 요청 URL에 AND 결합 반영",
      any("all%3Aretrieval+AND+all%3Aaugmented" in u for u in _urls), str(_urls))

# 레드팀 R4: 날짜 절이 무시되면 결과가 '비는' 게 아니라 '연도 무관 노이즈'로 채워진다 —
# 재조회 가드가 클라이언트 필터 이전을 보고 있어 절대 발동하지 않았고, 필터가
# 노이즈를 전멸시켜 '무수확'(=문헌 부재)이 거짓으로 전파됐다
_OLD_ATOM = ("<feed><entry><id>http://arxiv.org/abs/0301.00001v1</id>"
             "<published>2003-01-01T00:00:00Z</published>"
             "<title>An old paper from 2003</title><name>A B</name></entry></feed>")
_NEW_ATOM = ("<feed><entry><id>http://arxiv.org/abs/2501.00001v1</id>"
             "<published>2025-06-01T00:00:00Z</published>"
             "<title>A fresh 2025 preprint</title><name>C D</name></entry></feed>")
_calls = []
def _arxiv_noise(url, **kw):
    _calls.append(url)
    # 날짜 절이 붙은 1차 요청: arXiv가 절을 무시하고 옛 논문(노이즈)을 반환
    if "submittedDate" in url:
        return (_OLD_ATOM, 200)
    return (_NEW_ATOM, 200)  # 절 없는 재조회: 실제 최신 논문
m.http_get = _arxiv_noise
ar4, _e4 = m.search_arxiv("quantum error correction", 5, year_from=2025)
check("arxiv: 날짜 절 무시로 노이즈만 오면 재조회가 실제로 발동(거짓 무수확 방어)",
      len(_calls) == 2 and any("submittedDate" not in u for u in _calls), str(_calls))
check("arxiv: 재조회로 회수한 최신 논문이 결과에 남는다",
      len(ar4) == 1 and ar4[0]["year"] == 2025, str(ar4))

# ---- 레드팀 R2: Europe PMC(전문 색인·프리프린트) 소스 ----
_EPMC = json.dumps({"resultList": {"result": [
    {"id": "PPR123", "source": "PPR", "doi": "10.1101/2020.09.27.20202168",
     "title": "Reticulocyte hemoglobin equivalent in anemia screening.",
     "authorString": "Kim J, Lee S.", "pubYear": "2020", "isOpenAccess": "Y",
     "citedByCount": 4},
    {"id": "ABS1", "source": "MED", "doi": None, "title": "Conference abstract",
     "authorString": "Park H.", "pubYear": "2021", "isOpenAccess": "N"}]}})
_urls = []
def _cap_epmc(url, **kw):
    _urls.append(url)
    return (_EPMC, 200)
m.http_get = _cap_epmc
er, e_err = m.search_europepmc('METHODS:"Sysmex XN-1000"', 5, year_from=2019)
check("epmc: 결과 매핑(doi·연도·프리프린트)",
      e_err is None and len(er) == 2 and er[0]["doi"] == "10.1101/2020.09.27.20202168"
      and er[0]["year"] == 2020 and er[0]["source"] == "epmc", str(er))
check("epmc: DOI 없는 항목도 회수(NO_DOI 경로 대상)", er[1]["doi"] is None, str(er))
check("epmc: --year-from → FIRST_PDATE 필터",
      any("FIRST_PDATE" in u for u in _urls), str(_urls))

# ---- 레드팀 R4: epmc 제목의 HTML 엔티티가 실재 논문을 MISMATCH(가짜 인용)로 낙인 ----
_EPMC_ENT = json.dumps({"resultList": {"result": [
    {"id": "1", "source": "MED", "doi": "10.2174/0115734056389602250826081355",
     "title": "Smartphone-Based Anemia Screening &lt;i&gt;via&lt;/i&gt; Conjunctival "
              "Imaging with 3D-Printed Spacer.",
     "authorString": "Kumar A.", "pubYear": "2025", "isOpenAccess": "Y",
     "citedByCount": 0}]}})
m.http_get = lambda url, **kw: (_EPMC_ENT, 200)
er2, _e2 = m.search_europepmc("anemia screening", 5)
check("epmc: 제목 HTML 엔티티(&lt;i&gt;) 복원·태그 제거",
      er2 and er2[0]["title"] ==
      "Smartphone-Based Anemia Screening via Conjunctival Imaging with 3D-Printed Spacer",
      str(er2))
# 그 제목을 그대로 인용해도(=SKILL.md §3이 지시하는 동작) MISMATCH가 나면 안 된다
_CLEAN_T = "Smartphone-Based Anemia Screening via Conjunctival Imaging with 3D-Printed Spacer"
check("epmc: 엔티티 제목 인용이 MISMATCH로 낙인되지 않는다",
      m._title_match("Smartphone-Based Anemia Screening &lt;i&gt;via&lt;/i&gt; "
                     "Conjunctival Imaging with 3D-Printed Spacer", _CLEAN_T)[0] is True,
      str(m._title_match("Smartphone-Based Anemia Screening &lt;i&gt;via&lt;/i&gt; "
                         "Conjunctival Imaging with 3D-Printed Spacer", _CLEAN_T)))
# 원시 마크업 태그(OpenAlex·Crossref가 흘리는 <i>)도 같은 부류
check("epmc/openalex: 원시 <i> 태그 제목도 태그 없는 실제 제목과 일치",
      m._title_match("<i>Akkermansia muciniphila</i> and improved metabolic health",
                     "Akkermansia muciniphila and improved metabolic health")[0] is True)
# 마크업 제거가 꺾쇠 안의 '내용어'까지 지우면 안 된다(제목 훼손 방어)
check("마크업 제거: 'A <Survey>'의 내용어는 보존",
      m._clean_title("Deep Learning &amp; Graphs: A <Survey>")
      == "Deep Learning & Graphs: A <Survey>", m._clean_title("A <Survey>"))
# DOI 없는 항목의 dedup도 마크업 때문에 깨지면 안 된다(근거 폭 부풀리기 방어)
_pool_mk = [{"source": "epmc", "doi": None, "title": _CLEAN_T, "year": 2025,
             "citations": 0, "is_oa": True},
            {"source": "crossref", "doi": None, "title": _CLEAN_T, "year": 2025,
             "citations": 0, "is_oa": None}]
check("dedup: 마크업 정리 후 동일 논문 1건으로 병합", len(m.merge_and_rank(_pool_mk, 10)) == 1,
      str(m.merge_and_rank(_pool_mk, 10)))

# ---- 레드팀 R2: --sources 검증·정규화, per-page 상한 클램프 ----
def _run_search(**kw):
    ns = argparse.Namespace(query="q", limit=5, sources="openalex", year_from=None,
                            oa_only=False, json=False)
    for k, v in kw.items():
        setattr(ns, k, v)
    o, er_, code = io.StringIO(), io.StringIO(), 0
    try:
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(er_):
            m.cmd_search(ns)
    except SystemExit as e:
        code = e.code
    return o.getvalue(), er_.getvalue(), code

_urls = []
def _cap_empty(url, **kw):
    _urls.append(url)
    return (json.dumps({"results": [], "message": {"items": []}, "data": [],
                        "resultList": {"result": []}}), 200)
m.http_get = _cap_empty
o, er_, code = _run_search(sources="bogus")
check("search: 미인식 소스 → exit 2(무성 0건 금지)",
      code == 2 and "알 수 없는 소스" in er_, f"exit={code} {er_}")
o, er_, code = _run_search(sources="openalex,pubmedd")
check("search: 오타 소스 혼합 → exit 2", code == 2, f"exit={code} {er_}")
_urls.clear()
o, er_, code = _run_search(sources="openAlex, S2")
# 레드팀 R6: 0건이면 exit 1(flowchart 계약 '0 / 1(0건) / 2', bundle과 동일) — 조회 자체는 실행됨
check("search: 대소문자·공백 정규화 후 실제 조회(0건 → exit 1)",
      code == 1 and any("openalex" in u for u in _urls)
      and any("semanticscholar" in u for u in _urls), f"exit={code} {_urls}")
check("search: 조회했으나 0건인 소스는 '무수확'으로 구분 표시", "무수확" in o, o)
_urls.clear()
o, er_, code = _run_search(sources="openalex", limit=250)
check("search: per-page 상한 클램프(openalex 200 — HTTP 400 전멸 방지)",
      any("per-page=200" in u for u in _urls) and "상한" in er_, f"{_urls} {er_}")

m.http_get = ORIG_HTTP_GET  # 모킹 원복 (live 전 필수)

# ---- parallel-sources: 소스 병렬 호출 — 1개 예외 격리·기본 조합·병합 불변 ----
def _parallel_tests():
    import threading
    import time as _t
    orig = (m.search_openalex, m.search_crossref, m.search_s2, m.search_arxiv)
    _mk = lambda src, doi, cit: {"source": src, "doi": doi, "title": "T " + doi,
                                 "year": 2020, "citations": cit, "is_oa": False, "authors": ["A"]}
    seen = []
    def _oa(q, n, yf=None, oa=False):
        seen.append("openalex"); return [_mk("openalex", "10.1/a", 50), _mk("openalex", "10.1/b", 5)], None
    def _cr_boom(q, n, yf=None):
        seen.append("crossref"); raise RuntimeError("timed out")
    def _s2(q, n, yf=None):
        seen.append("s2"); return [_mk("s2", "10.1/c", 9)], None
    m.search_openalex, m.search_crossref, m.search_s2 = _oa, _cr_boom, _s2
    o, er_, code = _run_search(sources="openalex,crossref,s2", json=True)
    j = json.loads(o) if code == 0 and o.strip().startswith("{") else {}
    dois = [r.get("doi") for r in j.get("results", [])]
    check("parallel: 3소스 중 1개 예외 → 나머지 2소스 결과 정상 병합(exit 0)",
          code == 0 and sorted(dois) == ["10.1/a", "10.1/b", "10.1/c"], f"exit={code} {dois} {er_}")
    check("parallel: 예외 소스는 errors에 이름과 함께 격리(무성 탈락 X)",
          len(j.get("errors", [])) == 1 and "crossref" in j["errors"][0] and "timed out" in j["errors"][0],
          str(j.get("errors")))
    check("parallel: 예외 소스가 '무수확'으로 위장되지 않음", "crossref" not in j.get("empty_sources", []),
          str(j.get("empty_sources")))
    check("parallel: 세 소스 모두 실제 호출됨", sorted(seen) == ["crossref", "openalex", "s2"], str(seen))
    check("search --json: 행별 overlap 필드(0~1 또는 None) 추가 — 스키마 추가만",
          j.get("results") and all("overlap" in r and (r["overlap"] is None or 0.0 <= r["overlap"] <= 1.0)
                                   for r in j["results"]), str(j.get("results")))

    # 병렬성 자체: 소스마다 0.25초 블로킹 → 직렬이면 0.75초+, 병렬이면 0.5초 미만
    def _slow(src):
        def f(q, n, *a):
            _t.sleep(0.25); return [_mk(src, "10.2/" + src, 1)], None
        return f
    m.search_openalex, m.search_crossref, m.search_s2 = _slow("openalex"), _slow("crossref"), _slow("s2")
    t0 = _t.monotonic()
    o, er_, code = _run_search(sources="openalex,crossref,s2")
    dt = _t.monotonic() - t0
    check("parallel: 3소스 동시 호출(0.25s×3이 0.5s 미만에 끝남)", code == 0 and dt < 0.5, f"{dt:.2f}s")

    # 병합·랭킹·P# 불변: 직렬 순서(openalex→crossref→s2)로 pool이 쌓여야 동률 tie-break가 같다
    def _tie(src, delay):
        def f(q, n, *a):
            _t.sleep(delay); return [_mk(src, "10.3/" + src, 7)], None
        return f
    m.search_openalex, m.search_crossref, m.search_s2 = _tie("openalex", 0.15), _tie("crossref", 0.05), _tie("s2", 0.0)
    o1, _, _ = _run_search(sources="openalex,crossref,s2")
    m.search_openalex, m.search_crossref, m.search_s2 = _tie("openalex", 0.0), _tie("crossref", 0.0), _tie("s2", 0.1)
    o2, _, _ = _run_search(sources="openalex,crossref,s2")
    check("parallel: 완료 순서가 달라도 P# 부여·출력이 동일(pool 순서=소스 고정 순서)", o1 == o2 and "[P3]" in o1,
          o1 + "\n---\n" + o2)

    # HTTP 429(http_get 층) → 그 소스만 오류, _quota_warn 경고 유지, 나머지 생존
    m.search_openalex, m.search_crossref, m.search_s2 = orig[:3]
    m._QUOTA_WARNED.clear()
    class _E(Exception):
        def read(self): return b"quota"
    def _http(url, **kw):
        if "openalex.org" in url:
            m._quota_warn(url, 429, _E()); return None, 429
        return (json.dumps({"results": [], "message": {"items": [
                    {"DOI": "10.4/x", "title": ["Crossref Paper"], "author": [{"family": "K"}],
                     "issued": {"date-parts": [[2021]]}, "container-title": ["J"], "is-referenced-by-count": 3,
                     "type": "journal-article"}]},
                "data": [{"title": "S2 Paper", "year": 2022, "citationCount": 4, "externalIds": {"DOI": "10.4/y"},
                          "venue": "V", "authors": [{"name": "L"}], "isOpenAccess": False}],
                "resultList": {"result": []}}), 200)
    m.http_get = _http
    o, er_, code = _run_search(sources="openalex,crossref,s2", json=True)
    m.http_get = ORIG_HTTP_GET
    j = json.loads(o) if o.strip().startswith("{") else {}
    dois = sorted(r.get("doi") for r in j.get("results", []))
    check("parallel: openalex 429 → 그 소스만 오류·나머지 2소스 병합·_quota_warn 경고 유지",
          code == 0 and dois == ["10.4/x", "10.4/y"] and any("openalex" in e.lower() for e in j.get("errors", []))
          and "OpenAlex HTTP 429" in er_, f"exit={code} {dois} errors={j.get('errors')} stderr={er_[:120]}")
    m._QUOTA_WARNED.clear()

    # 기본 소스 조합에 arxiv 없음(단독 18초 실측) · 명시적 --sources arxiv는 그대로 동작
    captured = {}
    def _cap_search(a):
        captured["sources"] = a.sources
    m.cmd_search, _orig_cs, _orig_argv = _cap_search, m.cmd_search, sys.argv
    try:
        sys.argv = ["scholar.py", "search", "q"]; m.main()
        default_sources = captured.get("sources")
        sys.argv = ["scholar.py", "search", "q", "--sources", "arxiv"]; m.main()
        explicit = captured.get("sources")
    finally:
        m.cmd_search, sys.argv = _orig_cs, _orig_argv
    picked, unk = m.parse_sources(default_sources)
    check("parallel: 기본 소스 조합에 arxiv 없음", "arxiv" not in picked and not unk and len(picked) >= 2,
          str(default_sources))
    check("parallel: 명시적 --sources arxiv는 그대로 통과", m.parse_sources(explicit) == (["arxiv"], []), str(explicit))
    calls = []
    def _ax(q, n, yf=None):
        calls.append(n); return [_mk("arxiv", "", None) | {"doi": None, "id": "2301.00001"}], None
    m.search_arxiv = _ax
    o, er_, code = _run_search(sources="arxiv")
    check("parallel: 단일 소스(arxiv)는 스레드풀 없이 직접 호출·정상 출력", code == 0 and calls == [5] and "[P1]" in o,
          f"exit={code} {calls} {o}")
    m.search_openalex, m.search_crossref, m.search_s2, m.search_arxiv = orig
_parallel_tests()

# ---- merge_and_rank ----
# 레드팀 R1: arxiv 프리프린트(doi=None)와 출판본(DOI)이 dedup되지 않던 허점
pool = [
    {"source": "openalex", "doi": "10.1/a", "title": "Attention Is All You Need",
     "year": 2017, "citations": 100, "is_oa": True},
    {"source": "arxiv", "doi": None, "title": "Attention Is All You Need",
     "year": 2017, "citations": None, "is_oa": True},
]
out = m.merge_and_rank(pool, 10)
check("dedup: 프리프린트-출판본 트윈 병합(제목+연도)",
      len(out) == 1 and out[0]["doi"] == "10.1/a" and "arxiv" in out[0].get("also_in", []),
      str(out))
pool = [
    {"source": "openalex", "doi": "10.1/a", "title": "A survey", "year": 2017,
     "citations": 10, "is_oa": None},
    {"source": "arxiv", "doi": None, "title": "A survey", "year": 2022,
     "citations": None, "is_oa": True},
]
out = m.merge_and_rank(pool, 10)
check("dedup: 연도 상이(2년+)한 동명 제목은 미병합", len(out) == 2, str(out))

# 레드팀 R1: 인용수 미상(arxiv·pubmed) 소스가 인용 가중 랭킹 절단으로 전멸하던 허점
pool = [{"source": "openalex", "doi": f"10.1/{i}", "title": f"Cited paper number {i} unique",
         "year": 2020, "citations": 50 + i, "is_oa": None} for i in range(12)]
pool += [{"source": "arxiv", "doi": None, "title": f"Fresh preprint {i} on brand new topic",
          "year": 2026, "citations": None, "is_oa": True} for i in range(3)]
out = m.merge_and_rank(pool, 10)
check("rank: 인용수 미상 항목 최소 슬롯 예약",
      len(out) == 10 and any(p.get("citations") is None for p in out),
      f"nocite={sum(1 for p in out if p.get('citations') is None)}")

# 레드팀 R1: --oa-only가 openalex에만 적용되고 타 소스는 그대로 섞이던 허점
pool = [
    {"source": "crossref", "doi": "10.1/x", "title": "Paywalled paper about topic",
     "year": 2020, "citations": 10, "is_oa": None},
    {"source": "openalex", "doi": "10.1/y", "title": "Open access paper about topic",
     "year": 2020, "citations": 5, "is_oa": True},
]
out = m.merge_and_rank(pool, 10, oa_only=True)
check("--oa-only: 병합 후 전 소스 is_oa 필터", len(out) == 1 and out[0]["doi"] == "10.1/y", str(out))

# 레드팀 R2: 소스 relevance 순위를 통째로 버려 질의 정확 일치 논문이 절단으로
# 탈락하던 허점 — relevance 1위(_rank=0)는 저인용이어도 슬롯을 예약받는다
pool = [{"source": "openalex", "doi": f"10.2/{i}", "title": f"Unrelated high cite paper {i}",
         "year": 2020, "citations": 5000 + i, "is_oa": True, "_rank": i + 1}
        for i in range(10)]
pool.append({"source": "crossref", "doi": "10.2/exact", "title": "Exact query match paper",
             "year": 2026, "citations": 0, "is_oa": True, "_rank": 0})
out = m.merge_and_rank(pool, 5)
check("rank: 소스 relevance 1위는 절단에서 보존",
      any(p["doi"] == "10.2/exact" for p in out), str([p["doi"] for p in out]))
check("rank: relevance 보너스가 인용수 축을 뒤집지는 않음",
      out[0]["doi"] != "10.2/exact", str([p["doi"] for p in out]))
# _rank 없는 풀(기존 호출부·테스트)은 동작 불변
pool_nr = [{"source": "openalex", "doi": f"10.3/{i}", "title": f"paper {i}", "year": 2020,
            "citations": 10 * i, "is_oa": True} for i in range(5)]
check("rank: _rank 없는 풀은 인용수순 유지",
      [p["doi"] for p in m.merge_and_rank(pool_nr, 3)] == ["10.3/4", "10.3/3", "10.3/2"],
      str(m.merge_and_rank(pool_nr, 3)))

# round5: 질의-제목 겹침(overlap)을 랭킹 1축으로 — 무관 고인용 논문이 인용수만으로
# 상위를 차지해 bundle을 6회 반복하던 허점(철회 암호화폐 리뷰·Assetization·Sub-Saharan NPL)
_q_ov = "regime based asset allocation commodities"
_pool_ov = [
    {"source": "openalex", "doi": "10.5/food", "title": "A food regime genealogy",
     "year": 2009, "citations": 5000, "is_oa": True, "_rank": 0},
    {"source": "openalex", "doi": "10.5/rba",
     "title": "Regime-based asset allocation for commodities: a Markov-switching approach",
     "year": 2009, "citations": 50, "is_oa": True, "_rank": 1},
    {"source": "crossref", "doi": "10.5/npl",
     "title": "Determinants of non-performing loans in Sub-Saharan Africa",
     "year": 2009, "citations": 800, "is_oa": True, "_rank": 0},
]
check("overlap: 토큰화(불용어·복수형 제거) 후 질의 토큰 중 제목에 있는 비율",
      m._query_overlap(_q_ov, "Regime-based asset allocation for commodities") == 1.0
      and m._query_overlap(_q_ov, "A food regime genealogy") == 0.2
      and m._query_overlap(_q_ov, "Determinants of non-performing loans") == 0.0
      and m._query_overlap("the of and", "Anything") is None,
      str([m._query_overlap(_q_ov, t) for t in ("Regime-based asset allocation for commodities",
                                                  "A food regime genealogy")]))
_out_ov = m.merge_and_rank([dict(p) for p in _pool_ov], 10, query=_q_ov)
check("overlap (a): 겹침 1.0·인용 50이 겹침 0.2·인용 5000(소스 relevance 1위)보다 위",
      [p["doi"] for p in _out_ov] == ["10.5/rba", "10.5/food", "10.5/npl"],
      str([(p["doi"], p.get("overlap")) for p in _out_ov]))
check("overlap: query 전달 시 각 행에 overlap 필드 기록(0~1)",
      [p.get("overlap") for p in _out_ov] == [1.0, 0.2, 0.0], str(_out_ov))
_out_nq = m.merge_and_rank([dict(p) for p in _pool_ov], 10)
check("overlap (b): query=None → 기존 인용수·relevance 순서 그대로·overlap 필드 없음",
      [p["doi"] for p in _out_nq] == ["10.5/food", "10.5/npl", "10.5/rba"]
      and all("overlap" not in p for p in _out_nq), str(_out_nq))
_p0 = {"citations": 5000, "year": 2020, "_rank": 0}
check("overlap (c): 겹침 0은 -3.0 이상 감점·겹침 0.5 중립·겹침 1.0 보너스 ≥ +1.0",
      m.rank_key(_p0, 0.0) - m.rank_key(_p0) <= -3.0
      and m.rank_key(_p0, 0.5) == m.rank_key(_p0)
      and m.rank_key(_p0, 1.0) - m.rank_key(_p0) >= 1.0
      and m.rank_key(_p0, None) == m.rank_key(_p0),
      str([m.rank_key(_p0, x) - m.rank_key(_p0) for x in (0.0, 0.5, 1.0)]))
# 겹침 0 항목은 인용수 5000이라도 겹침 1.0·인용 0 항목 아래 — 인용수로 못 올라온다
_pool_z = [{"source": "openalex", "doi": "10.5/z", "title": "Totally unrelated famous survey",
            "year": 2020, "citations": 5000, "is_oa": True},
           {"source": "openalex", "doi": "10.5/m", "title": "Regime based asset allocation commodities",
            "year": 2020, "citations": 0, "is_oa": True}]
check("overlap (c): 겹침 0·인용 5000 < 겹침 1.0·인용 0",
      [p["doi"] for p in m.merge_and_rank(_pool_z, 10, query=_q_ov)] == ["10.5/m", "10.5/z"],
      str(m.merge_and_rank(_pool_z, 10, query=_q_ov)))
# 절단 예약(head 재정렬)도 겹침을 반영 — 예약으로 끌어올린 항목이 인용수순으로 되밀리지 않음
_pool_cut = [{"source": "openalex", "doi": f"10.5/u{i}", "title": f"Unrelated high cite paper {i}",
              "year": 2020, "citations": 5000 + i, "is_oa": True, "_rank": i + 1} for i in range(10)]
_pool_cut.append({"source": "crossref", "doi": "10.5/exact", "title": "Regime-based asset allocation for commodities",
                  "year": 2020, "citations": 0, "is_oa": True, "_rank": 0})
_out_cut = m.merge_and_rank(_pool_cut, 5, query=_q_ov)
check("overlap: limit 절단 후에도 겹침 1.0 항목이 1위(query 있음)",
      len(_out_cut) == 5 and _out_cut[0]["doi"] == "10.5/exact", str([p["doi"] for p in _out_cut]))

# ---- 레드팀 R6: 극성쌍·의학 접미·부제 극성 경고·RETRACTED 접두·arXiv 간격·exit 계약·S2 식별자 ----
def _r6_tests():
    orig_http, orig_sleep, orig_last = m.http_get, m.time.sleep, m._ARXIV_LAST[0]
    try:
        # (1) 시점·범위 극성쌍(before↔after, over↔under)이 TITLE_STOP에 묻혀 MATCH였다
        for c, r, exp, label in (
                ("Frequency of Depression Before Stroke", "Frequency of Depression After Stroke",
                 False, "title R6: before↔after 극성 반전 차단(Hackett 2005 재현)"),
                ("Mortality in Children over Five Years: A Pooled Analysis",
                 "Mortality in Children under Five Years: A Pooled Analysis",
                 False, "title R6: over↔under 극성 반전 차단(Olofin 2013 재현)"),
                ("Depression During Pregnancy", "Depression After Pregnancy",
                 False, "title R6: during↔after 극성 반전 차단"),
                ("Changes in mortality over time", "Changes in mortality over time and space",
                 True, "title R6: 비극성 'over time' 절단은 계속 허용(오탐 방지)"),
                ("Depression before and after stroke", "Depression after stroke",
                 True, "title R6: 한쪽에만 남는 극성어(맞바뀜 아님)는 발화하지 않는다"),
                # (2) 의미가 다른 의학 접미 이웃어가 ratio≥0.8 '표기 변형'으로 흡수됐다
                ("Pneumonia in Patients Treated With Anti-Programmed Death-1/Programmed Death Ligand 1 Therapy",
                 "Pneumonitis in Patients Treated With Anti-Programmed Death-1/Programmed Death Ligand 1 Therapy",
                 False, "title R6: pneumonia↔pneumonitis 흡수 금지(Naidoo 2017 재현)"),
                ("Hepatic toxicity of immune checkpoint inhibitors",
                 "Hepatitis toxicity of immune checkpoint inhibitors", False,
                 "title R6: hepatic↔hepatitis 흡수 금지"),
                ("Gastric lesions in long-term aspirin users", "Gastritis lesions in long-term aspirin users",
                 False, "title R6: gastric↔gastritis 흡수 금지"),
                ("Idiopathic pulmonary fibrosis in adults", "Idiopathic pulmonary fibrotic in adults",
                 True, "title R6: fibrosis↔fibrotic 동의 어형쌍은 계속 흡수"),
                ("Iron deficiency anaemia in pregnancy", "Iron deficiency anemia in pregnancy",
                 True, "title R6: anaemia↔anemia 철자 변형은 계속 흡수"),
                ("Meta analysis of leukaemia risk", "Meta analyses of leukemia risk", True,
                 "title R6: leukaemia↔leukemia 계속 흡수"),
                # (3) 부제(':' 뒤) hedge 절단은 NEG와 같은 '경고 계층' — 주제목 hedge는 계속 차단
                ("Effect of aspirin on cardiovascular events in elderly",
                 "Effect of aspirin on cardiovascular events in elderly: limited evidence", True,
                 "title R6: 부제의 hedge 절단은 NEG 부제 절단과 같은 등급(경고, MATCH)"),
                ("Effect of aspirin on cardiovascular events in elderly",
                 "Limited Effect of aspirin on cardiovascular events in elderly", False,
                 "title R6: 주제목 머리 hedge 삭제는 계속 차단"),
                ("Radiation Risk from computed tomography scans in children",
                 "Radiation Risk from computed tomography scans in children Overestimated", False,
                 "title R6: 주제목 꼬리 hedge(콜론 없음) 삭제도 계속 차단"),
                ("Effect of aspirin on cardiovascular events: limited evidence",
                 "Effect of aspirin on cardiovascular events: limited evidence", True,
                 "title R6: 부제까지 그대로 인용하면 당연히 MATCH")):
            got, ratio, how = m._title_match(c, r)
            check(label, got == exp, f"match={got} ratio={ratio:.2f} via={how}")
        check("R6: _medical_suffix_conflict 정탐(pneumonia/pneumoniti·hepatic/hepatiti·hepatoma/hepatiti)",
              m._medical_suffix_conflict("pneumonia", "pneumoniti") and
              m._medical_suffix_conflict("hepatic", "hepatiti") and
              m._medical_suffix_conflict("hepatoma", "hepatiti"))
        check("R6: _medical_suffix_conflict 오탐 방지(politic/political·diagnosing/diagnosi·coma/comma)",
              not m._medical_suffix_conflict("politic", "political") and
              not m._medical_suffix_conflict("diagnosing", "diagnosi") and
              not m._medical_suffix_conflict("coma", "comma"))
        m.time.sleep = lambda *a: None

        # (3') verify 출력: 부제의 결론 극성 표지 생략 → polarity_omitted + 강화 경고 행
        _cr = json.dumps({"message": {"title": ["Long-term oral administration of amrinone for "
                                                 "congestive heart failure: lack of efficacy"],
                                      "issued": {"date-parts": [[1985]]}, "author": [{"family": "Massie"}]}})
        _oa = json.dumps({"display_name": "Long-term oral administration of amrinone for congestive "
                                          "heart failure: lack of efficacy",
                          "publication_year": 1985, "is_retracted": False,
                          "authorships": [{"author": {"display_name": "Barry Massie"}}]})
        m.http_get = _mock_sources(_cr, _oa, None)
        r = m.verify_one("10.1/amrinone", "Long-term oral administration of amrinone for congestive heart failure",
                         "Massie", 1985)
        check("verify R6: 부제 NEG 절단은 MATCH 유지(R3 구제 회귀 없음)", r.get("verdict") == "MATCH", str(r))
        check("verify R6: polarity_omitted에 부제의 부정어 노출", r.get("polarity_omitted") == ["lack"], str(r))
        out, code = _run_batch([{"id": "P1", "doi": "10.1/amrinone", "author": "Massie", "year": 1985,
                                 "title": "Long-term oral administration of amrinone for congestive heart failure"}])
        check("verify-batch R6: 극성 표지 생략 행에 '결론 방향 반전' 강화 경고",
              code == 0 and "결론 극성 표지 생략: lack" in out and "결론 방향 반전" in out, f"exit={code}\n{out}")
        _oa2 = json.loads(_oa); _oa2["display_name"] = _oa2["display_name"].replace("lack of efficacy", "limited evidence")
        _cr2 = json.loads(_cr); _cr2["message"]["title"] = [_oa2["display_name"]]
        m.http_get = _mock_sources(json.dumps(_cr2), json.dumps(_oa2), None)
        r = m.verify_one("10.1/amrinone2", "Long-term oral administration of amrinone for congestive heart failure",
                         "Massie", 1985)
        check("verify R6: 부제 hedge 절단도 같은 경고 계층(MATCH + polarity_omitted)",
              r.get("verdict") == "MATCH" and r.get("polarity_omitted") == ["limited"], str(r))

        # (4) 'Retracted …'로 시작하는 정상 논문(철회 연구 메타분석)이 RETRACTED로 오판됐다
        def _pair(title, retracted=False, year=2012, fam="Samp"):
            cr = json.dumps({"message": {"title": [title], "type": "journal-article",
                                         "issued": {"date-parts": [[year]]}, "author": [{"family": fam}]}})
            oa = json.dumps({"display_name": title, "publication_year": year, "is_retracted": retracted,
                             "type": "article", "authorships": [{"author": {"display_name": "J " + fam}}]})
            return cr, oa
        m._PUBMED_RETRACTION_CACHE.clear()
        m.http_get = _mock_sources(*_pair("Retracted Publications in the Drug Literature"), _PM_CLEAN)
        r = m.verify_one("10.1002/j.1875-9114.2012.01100.x", "Retracted Publications in the Drug Literature", "Samp", 2012)
        check("verify R6: 'Retracted Publications in …' 정상 논문 → MATCH(세 소스 철회 아님)",
              r.get("verdict") == "MATCH", str(r))
        for t in ("RETRACTED: Some paper about X", "RETRACTED ARTICLE: Some paper about X",
                  "RETRACTED CHAPTER: Some paper about X", "Retracted and replaced: Some paper about X",
                  "RETRACTED — Some paper about X"):
            m._PUBMED_RETRACTION_CACHE.clear()
            m.http_get = _mock_sources(*_pair(t), _PM_CLEAN)  # 세 소스 플래그 없이 제목 접두만(백업 규칙)
            r = m.verify_one("10.1/ret", t, "Samp", 2012)
            check(f"verify R6: 구분자 동반 접두 '{t.split(' Some')[0]}' → RETRACTED 유지", r.get("verdict") == "RETRACTED", str(r))
        for t in ("Retracted Science and the Retraction Index", "Retracted papers originating from paper mills",
                  "Retracted COVID-19 articles: a side-effect of the pandemic"):
            check(f"R6: RETRACTED_PREFIX_RE가 정상 제목 '{t[:30]}…'을 잡지 않는다",
                  not m.RETRACTED_PREFIX_RE.match(t))

        # (5) Crossref/OpenAlex 2xx + 비JSON 본문 → UNVERIFIED(트레이스백·BAD_ENTRY 아님)
        m.http_get = lambda url, **kw: ("<html>Service Unavailable</html>", 200) \
            if ("crossref" in url or "openalex" in url) else (None, 404)
        try:
            r = m.verify_one("10.1038/nature14539", "Deep learning", "LeCun", 2015)
            crashed = None
        except Exception as e:
            r, crashed = {}, repr(e)
        check("verify R6: 비JSON 2xx 본문 → UNVERIFIED(예외 전파 없음)",
              crashed is None and r.get("verdict") == "UNVERIFIED" and "badjson" in (r.get("detail") or ""),
              f"crash={crashed} {r}")
        out, code = _run_batch([{"id": "P1", "doi": "10.1038/nature14539", "title": "Deep learning",
                                 "author": "LeCun", "year": 2015}])
        check("verify-batch R6: 비JSON 본문 → exit 5(재시도), BAD_ENTRY(6) 아님",
              code == 5 and "BAD_ENTRY" not in out and "미검증(네트워크)" in out, f"exit={code}\n{out}")

        # (6) ±1년 허용은 유지하되 batch MATCH 행에 실제 연도를 노출
        m.http_get = _mock_sources(_CR_PLAIN, _OA_PLAIN, None)
        out, code = _run_batch([{"id": "P1", "doi": "10.1/x", "title": "A perfectly ordinary paper title",
                                 "author": "Kumar", "year": 2013}])
        check("verify-batch R6: ±1년 → MATCH exit 0 유지", code == 0 and "MATCH" in out, f"exit={code}\n{out}")
        check("verify-batch R6: MATCH 행에 '연도: 인용 2013 / 실제 2012' 노출",
              "연도: 인용 2013 / 실제 2012" in out, out)
        out, code = _run_batch([{"id": "P1", "doi": "10.1/x", "title": "A perfectly ordinary paper title",
                                 "author": "Kumar", "year": 2012}])
        check("verify-batch R6: 연도 일치면 연도 경고 없음", "연도: 인용" not in out, out)
        r = m.verify_one("10.1/x", "A perfectly ordinary paper title", "Kumar", 2013)
        check("verify R6: claimed_year 필드", r.get("claimed_year") == 2013 and r.get("actual_year") == 2012, str(r))

        # (7) 단건 NOT_FOUND exit 3(예외 승인 코드 2와 분리)
        m.http_get = lambda url, **kw: (None, 404)
        code = None
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                m.cmd_verify(argparse.Namespace(doi="10.1/nope", title="x", author=None, year=None))
        except SystemExit as e:
            code = e.code
        check("verify R6: 단건 NOT_FOUND → exit 3", code == 3, f"exit={code}")

        # (8) search 0건(전 소스 오류) → exit 1, --json도 동일(errors 필드 유지)
        m.http_get = lambda url, **kw: (None, -1)
        o, er_, code = _run_search(sources="openalex,crossref,s2")
        check("search R6: 전 소스 오류 0건 → exit 1 + stderr 소스오류", code == 1 and "결과 0건" in er_ and "소스오류" in er_,
              f"exit={code} {er_}")
        o, er_, code = _run_search(sources="openalex,crossref,s2", json=True)
        j = json.loads(o) if o.strip().startswith("{") else {}
        check("search R6: --json 0건도 exit 1, JSON errors 필드 유지",
              code == 1 and j.get("results") == [] and len(j.get("errors", [])) == 3, f"exit={code} {o[:200]}")

        # (9) search_arxiv: 재조회에 3초 간격 게이트 + 재조회 실패는 소스오류
        slept = []
        m.time.sleep = lambda sec: slept.append(sec)
        m._ARXIV_LAST[0] = 0.0
        calls = []
        def _noise(url, **kw):
            calls.append(url)
            return (_OLD_ATOM, 200) if "submittedDate" in url else (_NEW_ATOM, 200)
        m.http_get = _noise
        ar, err = m.search_arxiv("quantum error correction", 5, year_from=2025)
        check("arxiv R6: year_from 재조회 전 3초 간격 sleep(_ARXIV_LAST 게이트 공유)",
              len(calls) == 2 and any(2.5 <= x <= 3.0 for x in slept) and err is None and len(ar) == 1,
              f"calls={len(calls)} slept={slept} err={err}")
        check("arxiv R6: search 호출 시각이 _ARXIV_LAST에 기록된다(후속 meta 조회가 간격을 안다)",
              m._ARXIV_LAST[0] > 0)
        m._ARXIV_LAST[0] = 0.0
        def _noise429(url, **kw):
            return (_OLD_ATOM, 200) if "submittedDate" in url else (None, 429)
        m.http_get = _noise429
        ar, err = m.search_arxiv("quantum error correction", 5, year_from=2025)
        check("arxiv R6: 재조회 429 → 소스오류 반환(empty로 위장하지 않음)",
              ar == [] and err and "429" in err, f"{ar} {err}")

        # (10) recommend: arXiv 전용 DOI·arXiv:<id> 앵커를 S2 형식으로
        check("R6: _s2_paper_ident 규칙", (m._s2_paper_ident("10.48550/arxiv.2005.11401"),
                                          m._s2_paper_ident("arxiv:2005.11401v2"),
                                          m._s2_paper_ident("2005.11401"),
                                          m._s2_paper_ident("10.1186/s40537-021-00492-0"))
              == ("arXiv:2005.11401", "arXiv:2005.11401", "arXiv:2005.11401", "DOI:10.1186/s40537-021-00492-0"))
        urls = []
        def _s2rec(url, **kw):
            urls.append(url)
            return (json.dumps({"recommendedPapers": [{"title": "T", "year": 2021, "citationCount": 1,
                                                       "externalIds": {"DOI": "10.1/r"}, "authors": []}]}), 200)
        m.http_get = _s2rec
        for anchor in ("10.48550/arXiv.2005.11401", "arXiv:2005.11401"):
            urls.clear()
            with contextlib.redirect_stdout(io.StringIO()):
                m.cmd_recommend(argparse.Namespace(doi=anchor, limit=5))
            check(f"recommend R6: 앵커 {anchor} → forpaper/arXiv:2005.11401로 조회",
                  any("forpaper/arXiv:2005.11401?" in u for u in urls), str(urls))
        m.http_get = lambda url, **kw: (None, 404)
        er_ = io.StringIO(); code = None
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(er_):
                m.cmd_recommend(argparse.Namespace(doi="10.1/unknown", limit=5))
        except SystemExit as e:
            code = e.code
        check("recommend R6: 404 메시지에 식별자 형식 힌트(429 안내로 오진 유도 X)",
              code == 1 and ("식별자" in er_.getvalue() or "arXiv:<id>" in er_.getvalue()), er_.getvalue())

        # (11) snowball: arXiv:<id> 정규화 + --query 주제 필터 결합
        check("R6: _snowball_doi 규칙", (m._snowball_doi("arXiv:2005.11401v2"), m._snowball_doi("2005.11401"),
                                        m._snowball_doi("10.1/x")) == ("10.48550/arxiv.2005.11401", "10.48550/arxiv.2005.11401", "10.1/x"))
        urls = []
        def _oa_sb(url, **kw):
            urls.append(url)
            if "/works/doi:" in url:
                return (json.dumps({"id": "https://openalex.org/W1", "referenced_works": ["https://openalex.org/W2"]}), 200)
            return (json.dumps({"results": []}), 200)
        m.http_get = _oa_sb
        with contextlib.redirect_stdout(io.StringIO()):
            m.cmd_snowball(argparse.Namespace(doi="arXiv:2005.11401", direction="cites", limit=5,
                                              year_from=None, sort="citations", query="hallucination"))
        check("snowball R6: arXiv:<id> → 10.48550/arxiv.<id>로 OpenAlex 조회",
              any("doi:10.48550/arxiv.2005.11401" in u for u in urls), str(urls))
        check("snowball R6: --query가 cites 필터에 title_and_abstract.search로 결합",
              any("cites%3AW1%2Ctitle_and_abstract.search%3Ahallucination" in u for u in urls), str(urls))
        urls.clear()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            m.cmd_snowball(argparse.Namespace(doi="10.1/x", direction="refs", limit=5,
                                              year_from=None, sort="citations", query="hallucination"))
        check("snowball R6: --query가 refs 배치 필터에도 결합",
              any("title_and_abstract.search%3Ahallucination" in u and "openalex_id" in u for u in urls), str(urls))
        urls.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            m.cmd_snowball(argparse.Namespace(doi="10.1/x", direction="cites", limit=5,
                                              year_from=None, sort="citations"))
        check("snowball R6: --query 없으면 기존 필터 그대로(회귀 없음)",
              any("filter=cites%3AW1&" in u for u in urls) and not any("title_and_abstract" in u for u in urls), str(urls))
        m.http_get = lambda url, **kw: (None, 404)
        er_ = io.StringIO(); code = None
        try:
            with contextlib.redirect_stderr(er_):
                m.cmd_snowball(argparse.Namespace(doi="10.1/x", direction="cites", limit=5,
                                                  year_from=None, sort="citations"))
        except SystemExit as e:
            code = e.code
        check("snowball R6: OpenAlex 404에 식별자 형식 안내", code == 1 and "식별자 형식" in er_.getvalue(), er_.getvalue())
        # 파서: snowball --query 수용(기본 None)
        captured = {}
        _osb, _argv = m.cmd_snowball, sys.argv
        m.cmd_snowball = lambda a: captured.update(vars(a))
        try:
            sys.argv = ["scholar.py", "snowball", "10.1/x"]; m.main(); q1 = captured.get("query"); captured.clear()
            sys.argv = ["scholar.py", "snowball", "10.1/x", "--query", "hallucination"]; m.main(); q2 = captured.get("query")
        finally:
            m.cmd_snowball, sys.argv = _osb, _argv
        check("parser R6: snowball --query 기본 None·지정 시 전달", (q1, q2) == (None, "hallucination"), str((q1, q2)))
    finally:
        m.http_get, m.time.sleep, m._ARXIV_LAST[0] = orig_http, orig_sleep, orig_last
_r6_tests()

# ---- SKILL.md 동기화 (지시문 수정이 본체인 허점들) ----
with open(os.path.join(BASE, "..", "SKILL.md"), encoding="utf-8") as f:
    skill = f.read()
check("SKILL.md: P# 전역 재부여 규칙 명시", "전역 P#" in skill)
check("SKILL.md: ledger에 DOI 열 명시", "P# | DOI | 발췌문" in skill)
check("SKILL.md: UNVERIFIED 처리 지침(재시도, 인용 제거 금지)", "UNVERIFIED" in skill)
check("SKILL.md: NO_DOI 처리 지침", "NO_DOI" in skill)
check("SKILL.md: --oa-only 범위 명시", "`--oa-only`는 병합 후" in skill)
# 레드팀 R2 — 코드와 지시문이 어긋나면 게이트가 실무에서 무력화된다
check("SKILL.md: verify-batch exit 계약 표(RETRACTION_UNCHECKED)", "RETRACTION_UNCHECKED" in skill)
check("SKILL.md: INCOMPLETE_META(author·year 필수 강제) 명시", "INCOMPLETE_META" in skill)
check("SKILL.md: 예외 유형만 남은 경우의 통과 예외 명시",
      "실패가 전부 예외 유형(NO_DOI·RETRACTION_NA)일 때" in skill)
check("SKILL.md: [P#] 개수 3중 대조 절차", "개수 3중 대조" in skill)
check("SKILL.md: 빈 refs.json 거부 명시", "빈 배열은 exit 2로 거부" in skill)
check("SKILL.md: sim<1.0 MATCH 실제 제목 육안 대조 지시", "눈으로 끝까지 대조" in skill)
check("SKILL.md: epmc 전문검색 라우팅", "epmc" in skill and "본문(Methods/Results)" in skill)
check("SKILL.md: 미인식 소스 exit 2 안내", "exit 2로 거부**된다" in skill)
check("SKILL.md: snowball cites 최신성 맹점 안내", "cites는 기본이 인용수순" in skill)
check("SKILL.md: --year-from arxiv 적용 명시", "arxiv 포함 전 소스" in skill)
# 레드팀 R3
check("SKILL.md: refs도 --sort date 적용됨을 명시(문서-CLI 불일치 해소)",
      "--direction refs --sort date" in skill and "cites와 **동일하게 적용**" in skill)
check("SKILL.md: 고유 DOI 기준 대조(중복 등재 방어)", "고유 DOI 수 기준" in skill)
check("SKILL.md: NON_ARTICLE 조치 명시", "NON_ARTICLE" in skill)
check("SKILL.md: BAD_ENTRY 조치 명시", "BAD_ENTRY" in skill)
check("SKILL.md: exit 혼재 시 우선순위 명시", "더 엄한 코드가 우선" in skill)
check("SKILL.md: NOT_FOUND는 세 소스 404일 때만",
      "소스(Crossref·OpenAlex·DataCite)가 모두 404" in skill)
check("SKILL.md: 철회 판정 소스 3종 명시", "철회 판정 소스는 Crossref" in skill)
check("SKILL.md: 실제 제목 미절단 육안 대조 지시", "잘리지 않고 전부" in skill)
check("SKILL.md: 철회 검사 커버리지 한계 명시", "철회 검사는 완전하지 않다" in skill)
# 레드팀 R4 — 코드 동작 변경분과 문서-사실 오기 정정
check("SKILL.md: NON_ARTICLE에 정오표·corrigendum 명시",
      "정오표·corrigendum·erratum 통지" in skill and "원논문 DOI로 교체" in skill)
check("SKILL.md: RETRACTION_NA 예외 경로(exit 2) 명시",
      "RETRACTION_NA" in skill and "구조적으로 없어" in skill)
check("SKILL.md: DOI 삭제로 예외를 타는 것이 규약 위반임을 명시",
      "DOI를 지워 NO_DOI로 만들어 예외를 타는 것은 규약 위반" in skill)
check("SKILL.md: 식별자 치환·축약 방향 기계검증 범위 갱신",
      "식별자 치환" in skill and "token-omission" in skill)
# 실사용 발견(2026-07-28) — 철회 서술 경로와 게이트 B 분모 정의
check("SKILL.md: 철회 논문 서술 절차 존재(게이트 A 교착 해소)",
      "철회 논문 서술 절차" in skill and "`refs.json`에서 **뺀다**" in skill)
check("SKILL.md: 철회 논문은 [철회됨] 태그로 서술", "`[철회됨]` 태그" in skill)
check("SKILL.md: 철회 공고문을 refs에 넣지 말 것 명시",
      "철회 공고문(retraction notice) DOI를 refs.json에 넣지 말 것" in skill)
check("SKILL.md: NON_ARTICLE에 철회 공고문 포함",
      "철회 공고문(retraction notice)**" in skill)
check("SKILL.md: RETRACTED 행이 'refs에 남긴 채 통과' 오해를 차단",
      "refs에 남긴 채로는 게이트를 통과할 수 없다" in skill)
check("SKILL.md: 게이트 B의 M을 문장 수로 정의(논문 수 오산 방어)",
      "태그가 붙은 문장 수이지 논문 수가 아니다" in skill)
check("SKILL.md: 꼬리 절단형 범위축소는 기계검증 불가 + 생략 토큰 확인 지시",
      "생략:" in skill and "기계검증이 못 잡는 잔여 경로는 꼬리 절단형 범위 축소뿐이다" in skill
      and "기계검증이 못 잡는 유일한 잔여 경로" not in skill)
check("SKILL.md: 대립 접두 치환·hedge 삭제·머리 절단 차단 명시(레드팀 R5)",
      "hedge-omission" in skill and "hyper↔hypo" in skill and "머리·중간의" in skill
      and "≈ 표기변형" in skill)
check("SKILL.md: 프로젝트 모드 3중 대조는 project refs --pids(레드팀 R5)",
      "project refs <slug> --pids" in skill and "레지스트리 N편 중 답변 인용 k편" in skill)
check("SKILL.md: verify-batch 파일 오류 exit 6 계약 명시", "refs.json 부재·JSON 문법 오류) | 6" in skill)
# 레드팀 R6 — 코드 동작 변경분과 문서 계약 정합
check("SKILL.md R6: 부제 극성 표지 절단은 경고 계층(polarity_omitted) 명시",
      "polarity_omitted" in skill and "부제의 결론 극성 표지 생략" in skill)
check("SKILL.md R6: 의학 접미 치환·극성 반전 기계검증 범위 갱신",
      "pneumonia↔pneumonitis" in skill and "polarity-inversion" in skill)
check("SKILL.md R6: 연도 ±1년 허용 + batch 행 실제 연도 교정 지시",
      "±1년 허용(online-first)" in skill and "연도: 인용 2016 / 실제 2015" in skill)
check("SKILL.md R6: NOT_FOUND 단건 exit 3(예외 승인 코드 2와 분리)",
      "NOT_FOUND | 3(단건 verify도 3)" in skill and "NOT_FOUND·MISMATCH·MISMATCH_META·NON_ARTICLE(3)" in skill)
check("SKILL.md R6: bundle/search exit 2는 --sources 이름 오류(HTTP 소스오류 아님)",
      "2(--sources 이름 오류)" in skill)
check("SKILL.md R6: project 요약에 export-db|import-db 등재", "render|export-db|import-db" in skill)
check("SKILL.md R6: 프로젝트 모드 DOI 없는 항목 등록 불가 + NO_DOI는 단발 모드 전용",
      "모드에 애초에 등록되지 않는다" in skill and "단발 모드에서만 유효한 경로" in skill
      and "--sources epmc,pubmed" in skill)
check("SKILL.md R6: snowball --query(유명 앵커 주제 부분집합 우선 회수) 안내",
      "--query" in skill and "관련 부분집합을 우선 회수" in skill and "전수 회수가 아니다" in skill)
check("SKILL.md R6: 범용 토큰 질의 금지 규약(overlap 동점 서베이 슬롯 점유)",
      "범용 토큰" in skill and "overlap 0.5 동점" in skill)
check("SKILL.md R6: recommend arXiv 앵커 자동 변환 안내", "S2 arXiv: 형식" in skill)
with open(os.path.join(BASE, "..", "references", "pipeline-flowchart.md"), encoding="utf-8") as f:
    _fc = f.read()
check("flowchart R6: NOT_FOUND는 exit 3 가지, exit 2는 NO_DOI·RETRACTION_NA만",
      "3 MISMATCH / NOT_FOUND / NON_ARTICLE" in _fc and "2 NO_DOI / RETRACTION_NA" in _fc)
check("SKILL.md: §2 search 2~3회 잔존 문장 조건화(bundle 기본과 충돌 제거)",
      "`search`를 키워드 조합별로 실행(보통 2~3회)" not in skill
      and "project add <slug> --from bundle.json --pick" in skill)
check("SKILL.md: snowball refs 100건 초과 전수 배치 조회 명시",
      "100건 단위로 **전부** 배치 조회" in skill and "앞 100건 한정" not in skill)
check("SKILL.md: 요약줄 템플릿에 소스별 n/N 포함(커버리지 소실 방지)",
      "crossref n/N+openalex n/N+pubmed n/N" in skill)
check("SKILL.md: 요약줄을 고치지 말고 그대로 붙이라는 지시",
      "한 글자도\n  고치지 말고" in skill or "한 글자도 고치지 말고" in skill)
check("SKILL.md: openalex는 전문 일부 색인(제목·초록만 아님) 정정",
      "crossref·s2·pubmed·arxiv는 제목·초록만 색인한다" in skill
      and "fulltext.search" in skill)
check("SKILL.md: epmc는 인용수 제공(미상 목록에서 제외) 정정",
      "인용 ?`: arxiv·pubmed)" in skill and "epmc는 citedByCount를 제공" in skill)
check("SKILL.md: arXiv 정독 경로(abs→pdf WebFetch) 명시",
      "arXiv 항목은 `paper`·`oa`로 읽지 않는다" in skill and "/pdf/" in skill)
check("SKILL.md: paper의 10.48550 DOI는 404가 아니라 오염 레코드(레드팀 R5)",
      "404가 아니라 오염된 레코드" in skill and "⚠ 제목 오염 의심" in skill
      and "10.48550/*` DOI 모두 404(exit 1)" not in skill)
check("SKILL.md: arXiv 질의 AND 조인 명시",
      "arXiv 질의는 토큰별 AND로 조인" in skill)
check("SKILL.md: arXiv year-from 0건이 '문헌 부재'가 아님을 명시",
      "'문헌 부재'가 아니다" in skill)
# scholar.py 내부 문서도 같은 오기를 반복하면 안 된다(코드-문서 동기화)
_src = open(os.path.join(BASE, "scholar.py"), encoding="utf-8").read()
check("scholar.py: merge_and_rank 독스트링의 '인용수 미상 epmc' 오기 정정",
      "citations=None — arxiv·pubmed·epmc" not in _src)

# ---- live (--live) ----
if "--live" in sys.argv:
    r = m.verify_one("10.1038/nature14539", "Deep learning", "LeCun", 2015)
    check("live: MATCH+저자+연도", r.get("verdict") == "MATCH", str(r))
    r2 = m.verify_one("10.1016/s0140-6736(97)11096-0",
                      "Ileal-lymphoid-nodular hyperplasia, non-specific colitis, "
                      "and pervasive developmental disorder in children")
    check("live: RETRACTED 감지", r2.get("verdict") == "RETRACTED", str(r2))
    r3 = m.verify_one("10.1038/nature14539", "Deep learning", year=2010)
    check("live: 연도 오기재", r3.get("verdict") == "MISMATCH_META", str(r3))
    # 레드팀 R1: 실제 제목+조작 문구 인용이 containment로 MATCH되던 공격 재현 차단
    r4 = m.verify_one("10.1109/cvpr.2016.90",
                      "Deep Residual Learning for Image Recognition proves that "
                      "vaccines cause autism in longitudinal studies")
    check("live: 조작 확장 인용 차단", r4.get("verdict") == "MISMATCH", str(r4))
    # 레드팀 R2 실측 재현 케이스 — 문자 유사도 0.95+로 MATCH였던 의미 반전 인용
    r5 = m.verify_one("10.1136/bmj.324.7342.867/a",
                      "Neuroblastoma screening does reduce mortality", "Tuffs", 2002)
    check("live: 부정어 삭제(결론 반전) 인용 차단",
          r5.get("verdict") == "MISMATCH" and r5.get("similarity", 0) > 0.9, str(r5))
    r6 = m.verify_one("10.1038/nature16961",
                      "Mastering the game of Chess with deep neural networks and tree search",
                      "Silver", 2016)
    check("live: 연구대상 치환 인용 차단", r6.get("verdict") == "MISMATCH", str(r6))
    # 불변입자만 겹치는 저자 오기재(실제 1저자 de Souza)
    r7 = m.verify_one("10.4103/aian.aian_225_17",
                      "Parkinsonism and tremor complicating long-term cinitapride use",
                      "de Silva", 2017)
    check("live: 'de Silva' vs 'de Souza' 저자 오기재 차단",
          r7.get("verdict") == "MISMATCH_META", str(r7))
    # 철회 논문(Crossref 제목에 RETRACTED 접두 없음 — OpenAlex 소스가 필수인 케이스)
    r8 = m.verify_one("10.1021/am300292v",
                      "Flexible and Microporous Chitosan Hydrogel/Nano ZnO Composite "
                      "Bandages for Wound Dressing: In Vitro and In Vivo Evaluation",
                      "Sudheesh Kumar", 2012)
    check("live: 철회 논문 감지(접두어 없는 출판사)", r8.get("verdict") == "RETRACTED", str(r8))
    check("live: 정상 경로에서 retraction_checked=True", r8.get("retraction_checked") is True, str(r8))
    # Europe PMC 전문 색인 — 본문에만 나오는 조건 회수
    er, ee = m.search_europepmc('"reticulocyte hemoglobin equivalent"', 3)
    check("live: epmc 검색 동작", ee is None and len(er) >= 1, f"{ee} {len(er)}")
    # ---- 레드팀 R3 실측 재현 케이스 ----
    # PubMed만 철회로 낙인한 논문(OpenAlex is_retracted:false·Crossref update-to 없음)
    r9 = m.verify_one("10.1007/s11259-026-11380-4",
                      "A case of suspected vertical hepatozoonosis manifesting as severe "
                      "anemia and gastroenteritis in a three-month-old puppy following "
                      "vaccination", "Bahrami", 2026)
    # 2026-07-28: 이 DOI에 Crossref `updated-by: retraction`이 등록되면서(7/27,
    # source=publisher) 이제 Crossref 단계에서 철회가 확정된다 — PubMed 폴백까지
    # 가지 않는다. 원래 의도(어느 소스든 철회를 놓치지 않는가)를 유지하되 특정
    # 소스를 못박지 않는다. 소스별 커버리지는 아래 오프라인 모킹 케이스가 지킨다.
    check("live: 실제 철회 논문 감지(소스 무관)",
          r9.get("verdict") == "RETRACTED"
          and bool(r9.get("retraction_sources")), str(r9))
    # arXiv 전용 DOI(DataCite) — 실재 논문이 NOT_FOUND(가짜 인용)로 낙인되면 안 된다
    r10 = m.verify_one("10.48550/arXiv.2407.21783", "The Llama 3 Herd of Models",
                       "Grattafiori", 2024)
    # 레드팀 R4: 재시도해도 안 풀리는 구조적 미검증이므로 RETRACTION_NA(exit 2) —
    # 정직한 DOI 기재가 DOI 삭제(NO_DOI)보다 불리해지던 역인센티브 제거
    check("live: arXiv DataCite DOI → RETRACTION_NA(NOT_FOUND·교착 아님)",
          r10.get("verdict") == "RETRACTION_NA"
          and (r10.get("similarity") or 0) > 0.99, str(r10))
    # 제목 확장(모집단·연구설계 날조)은 ratio 0.75+여도 차단
    r11 = m.verify_one("10.1038/ncomms3192",
                       "Metformin improves healthspan and lifespan in mice and humans",
                       "Martin-Montalvo", 2013)
    check("live: 모집단 확장 인용 차단(sim 0.9+)",
          r11.get("verdict") == "MISMATCH" and r11.get("matched_on") == "token-addition",
          str(r11))
    r12 = m.verify_one("10.1038/ncomms3192", "Metformin improves healthspan and lifespan",
                       "Martin-Montalvo", 2013)
    check("live: 정당한 축약 인용은 계속 MATCH", r12.get("verdict") == "MATCH", str(r12))
    # 하이픈 표기 변형·부제 부정어가 '가짜 인용'(exit 3)으로 오판되던 오탐
    r13 = m.verify_one("10.1016/s0140-6736(09)60496-7",
                       "Noninvasive ventilation in acute respiratory failure", "Nava", 2009)
    check("live: 'Noninvasive' vs 'Non-invasive' 오탐 해소(MATCH)",
          r13.get("verdict") == "MATCH", str(r13))
    r14 = m.verify_one("10.1093/oep/gpf045", "Collaborative tax evasion and social norms",
                       "Chang", 2004)
    check("live: 부제에 부정어가 있는 논문의 주제목 인용 구제(MATCH)",
          r14.get("verdict") == "MATCH" and r14.get("matched_on") == "main-title", str(r14))
    # 심사보고서 DOI — 제목·저자·연도가 맞아도 논문이 아니다
    r15 = m.verify_one("10.1039/d4ra04688a/v1/review2",
                       'Review for "Recovery of uranium using epoxy-modified sorbents"',
                       "Anonymous", 2024)
    check("live: 심사보고서 레코드 → NON_ARTICLE", r15.get("verdict") == "NON_ARTICLE", str(r15))
    # ---- 레드팀 R4 실측 재현 케이스 ----
    # 정오표 DOI + 원논문 제목·저자·연도 — 실재 조합이라 어떤 대조에도 안 걸리던 완전 위장
    r16 = m.verify_one("10.1038/s41467-018-06999-0",
                       "VAMPnets for deep learning of molecular kinetics", "Mardt", 2018)
    check("live: 정오표 DOI를 원논문으로 인용 → NON_ARTICLE(MATCH 아님)",
          r16.get("verdict") == "NON_ARTICLE", str(r16))
    # 제목 속 식별자 1글자 치환(중단된 백신 후보 b1에 b2의 3상 결과 귀속)
    r17 = m.verify_one("10.1056/nejmoa2034577",
                       "Safety and Efficacy of the BNT162b1 mRNA Covid-19 Vaccine",
                       "Polack", 2020)
    check("live: 백신 후보 식별자 치환(BNT162b1↔b2) 차단",
          r17.get("verdict") == "MISMATCH", str(r17))
    r18 = m.verify_one("10.1056/nejmoa2034577",
                       "Safety and Efficacy of the BNT162b2 mRNA Covid-19 Vaccine",
                       "Polack", 2020)
    check("live: 올바른 식별자는 계속 MATCH(식별자 규칙 오탐 방어)",
          r18.get("verdict") == "MATCH", str(r18))
    # 제목 중간 한정어 삭제 — 암 특이 메타분석을 전체사망률 메타분석으로 승격
    r19 = m.verify_one("10.1093/annonc/mdz059",
                       "Vitamin D supplementation and total mortality: a meta-analysis "
                       "of randomized controlled trials", "Keum", 2019)
    check("live: 제목 중간 한정어 삭제(cancer incidence) 차단",
          r19.get("verdict") == "MISMATCH", str(r19))

# ---- 저자 표시명 순서 오류(OpenAlex 'Zhao Chengshuai') — DataCite familyName 후보로 구제 ----
def _author_order_tests():
    # 1) 후보 대조 로직: first_author가 틀려도 candidates에 실제 성이 있으면 통과해야 한다
    meta={"first_author":"Chengshuai","author_candidates":["Zhao"]}
    ok=m._author_match("Zhao",meta["first_author"])
    if ok is False:
        ok=any(m._author_match("Zhao",c) for c in meta["author_candidates"])
    check("author: 표시명 역순도 familyName 후보로 MATCH", ok is True)
    # 2) 후보가 없으면 여전히 MISMATCH여야 한다(오탐 방어)
    check("author: 후보 없는 진짜 불일치는 여전히 False", m._author_match("Zhao","Chengshuai") is False)
    # 3) 엔드투엔드: DataCite 응답 모킹 — OpenAlex는 역순 표시명, DataCite는 구조화 familyName
    import json as _j
    def fake(url, **kw):
        if "crossref" in url: return None, 404
        if "openalex" in url:
            return _j.dumps({"display_name":"Is CoT a Mirage","publication_year":2025,"is_retracted":False,
                             "authorships":[{"author":{"display_name":"Zhao Chengshuai"}}]}), 200
        if "datacite" in url:
            return _j.dumps({"data":{"attributes":{"titles":[{"title":"Is CoT a Mirage"}],"publicationYear":2025,
                             "creators":[{"name":"Zhao, Chengshuai","givenName":"Chengshuai","familyName":"Zhao"}]}}}), 200
        return None, 404
    orig=m.http_get; m.http_get=fake
    try:
        r=m.verify_one("10.48550/arxiv.2508.01191","Is CoT a Mirage","Zhao",2025)
    finally:
        m.http_get=orig
    check("author: e2e 역순 표시명 → MATCH(DataCite familyName)", r.get("verdict") in ("MATCH","RETRACTION_NA") and r.get("author_ok") is True, str(r))
_author_order_tests()

def _s2_count_tests():
    import json as _j
    calls=[]
    def fake(url, **kw):
        calls.append(url)
        if "semanticscholar" in url:
            if len(calls)==1: return None, 429
            if "arXiv:" in url: return _j.dumps({"citationCount":16}), 200
            if "DOI:" in url: return _j.dumps({"citationCount":569}), 200
            if "paper/search" in url:
                return _j.dumps({"data":[{"title":"Other Paper","citationCount":9},
                                         {"title":"Faith and Fate: Limits of Transformers","citationCount":77}]}), 200
        return None, 404
    orig=m.http_get; m.http_get=fake
    orig_sleep=m.time.sleep; m.time.sleep=lambda *_: None
    try:
        c,e=m._fetch_s2_count(doi="10.1016/j.tics.2024.01.011", arxiv_id="2301.06627v2")
        check("s2: 429 재시도 + arXiv·DOI 양쪽 조회 후 큰 값", c==569 and e is None, str((c,e)))
        check("s2: arXiv 버전접미 제거", any("arXiv:2301.06627?" in u for u in calls), str(calls))
        calls.clear()
        c,e=m._fetch_s2_count(doi="10.48550/arXiv.2205.11916")
        check("s2: arXiv DOI는 arXiv id로 변환(DOI 조회 안 함)", c==16 and all("DOI:" not in u for u in calls), str(calls))
        c,e=m._fetch_s2_count()
        check("s2: 식별자 없으면 None+err", c is None and e)
        calls.clear()
        def fake404(url, **kw):
            calls.append(url)
            if "paper/search" in url:
                return _j.dumps({"data":[{"title":"Faith and fate: limits of transformers","citationCount":77}]}), 200
            return None, 404
        m.http_get=fake404
        c,e=m._fetch_s2_count(doi="10.18653/v1/x", title="Faith and Fate: Limits of Transformers")
        check("s2: DOI 404면 제목 검색으로 대조(정규화 일치)", c==77, str((c,e,calls)))
        calls.clear()
        c,e=m._fetch_s2_count(doi="10.18653/v1/x", title="Completely Different Title")
        check("s2: 제목 불일치는 None(오매칭 방지)", c is None and "불일치" in e, str((c,e)))
    finally:
        m.http_get=orig; m.time.sleep=orig_sleep
    check("s2: 라벨 OA/S2 병기", m._cit_label({"citations":90,"citations_s2":1584},"/")=="OpenAlex 90/S2 1,584")
    check("s2: 라벨 둘 다 없으면 ?", m._cit_label({})=="?")
    check("s2: 라벨 S2만", m._cit_label({"citations_s2":7})=="S2 7")
_s2_count_tests()

def _quota_tests():
    import urllib.error, io as _io
    m._QUOTA_WARNED.clear()
    def mk(code, url):
        return urllib.error.HTTPError(url, code, "x", {}, _io.BytesIO(b'{"error":"daily budget exceeded"}'))
    orig=m.urllib.request.urlopen; orig_sleep=m.time.sleep; m.time.sleep=lambda *_: None
    def boom(req, **kw): raise mk(429, req.full_url)
    m.urllib.request.urlopen=boom
    try:
        err=io.StringIO()
        with contextlib.redirect_stderr(err):
            b,c=m.http_get("https://api.openalex.org/works?search=x", retries=0)
            b2,c2=m.http_get("https://api.openalex.org/works/W1", retries=0)
        check("quota: openalex 429 → (None,429) 계약 유지", b is None and c==429)
        check("quota: 한도 경고 stderr + 응답 본문 포함", "한도" in err.getvalue() and "daily budget" in err.getvalue(), err.getvalue())
        check("quota: 같은 호스트 경고는 1회만", err.getvalue().count("⚠️")==1, err.getvalue())
        err=io.StringIO()
        with contextlib.redirect_stderr(err):
            m.http_get("https://api.semanticscholar.org/graph/v1/paper/x", retries=0)
        check("quota: S2 429는 한도 경고 안 냄(공유풀 정상동작)", "한도" not in err.getvalue())
    finally:
        m.urllib.request.urlopen=orig; m.time.sleep=orig_sleep; m._QUOTA_WARNED.clear()
_quota_tests()


# ---- project (논문 테이블 레지스트리) — 오프라인, SCHOLAR_HOME을 임시폴더로 ----
def _proj_tests():
    import types
    with tempfile.TemporaryDirectory() as td:
        m.SCHOLAR_HOME = td
        A = lambda **k: types.SimpleNamespace(**k)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            m.cmd_project_init(A(slug="t1", title="테스트"))
        check("project: init 생성", os.path.exists(os.path.join(td, "t1", "project.json")))
        check("project: init ledger 골격", os.path.exists(os.path.join(td, "t1", "ledger.md")))
        # 중복 init 거부
        try:
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                m.cmd_project_init(A(slug="t1", title="x"))
            check("project: 중복 init exit 2", False)
        except SystemExit as e:
            check("project: 중복 init exit 2", e.code == 2)
        # slug 검증
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                m._pdir("bad/slug")
            check("project: slug 경로문자 거부", False)
        except SystemExit as e:
            check("project: slug 경로문자 거부", e.code == 2)
        # search --json 파일에서 --no-fetch 등록 (+ 식별자 없는 행은 실패)
        sj = os.path.join(td, "s.json")
        json.dump({"results": [
            {"doi": "10.1000/a", "title": "Alpha paper", "year": 2020, "authors": ["Ann Lee", "Bob Kim"],
             "venue": "J", "citations": 5, "is_oa": True},
            {"doi": None, "id": "http://arxiv.org/abs/2401.00001v1", "title": "Beta preprint", "year": 2024,
             "authors": ["Cy Park"], "venue": "arXiv", "citations": None, "is_oa": True},
            {"doi": None, "id": None, "title": "No ident", "year": 2000, "authors": []},
        ]}, open(sj, "w"))
        out = io.StringIO(); err = io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                m.cmd_project_add(A(slug="t1", ids=[], from_json=sj, pick=None, no_fetch=True, refresh=False))
            code = 0
        except SystemExit as e:
            code = e.code
        pj = m._pload("t1")
        check("project: add --no-fetch 2건 등록·식별자없는 행 실패 exit 1", code == 1 and len(pj["papers"]) == 2, out.getvalue()+err.getvalue())
        check("project: arXiv id 추출", pj["papers"][1].get("arxiv_id") == "2401.00001v1", str(pj["papers"][1]))
        check("project: P# 순차 부여", [p["pid"] for p in pj["papers"]] == ["P1", "P2"])
        # 중복 = 같은 DOI
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            m.cmd_project_add(A(slug="t1", ids=[], from_json=sj, pick="P1", no_fetch=True, refresh=False))
        check("project: DOI 중복은 재등록 안 함", "중복 1(P1)" in out.getvalue() and len(m._pload("t1")["papers"]) == 2, out.getvalue())
        # --pick 범위
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            try:
                m.cmd_project_add(A(slug="t1", ids=[], from_json=sj, pick="P2", no_fetch=True, refresh=False))
            except SystemExit:
                pass
        check("project: --pick은 선택 행만", "추가 0 · 중복 1(P2)" in out.getvalue(), out.getvalue())
        # refresh: 셀 유지 + 메타 갱신
        with contextlib.redirect_stdout(io.StringIO()):
            m.cmd_project_set(A(slug="t1", pid="P1", key="conclusion", value="결론 A", from_json=None))
        json.dump({"results": [{"doi": "10.1000/a", "title": "Alpha paper (v2)", "year": 2021, "authors": ["Ann Lee"]}]},
                  open(sj, "w"))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            m.cmd_project_add(A(slug="t1", ids=[], from_json=sj, pick=None, no_fetch=True, refresh=True))
        p1 = m._find_paper(m._pload("t1"), "P1")
        check("project: --refresh 메타 갱신·셀 유지", p1["year"] == 2021 and p1["cells"]["conclusion"] == "결론 A", str(p1))
        # col add/list/rm
        with contextlib.redirect_stdout(io.StringIO()):
            m.cmd_project_col(A(slug="t1", action="add", label="투자 시사점", key="invest", prompt="p"))
        check("project: col add", any(c["key"] == "invest" for c in m._pload("t1")["columns"]))
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                m.cmd_project_col(A(slug="t1", action="add", label="x", key="invest", prompt=""))
            check("project: col 키 중복 거부", False)
        except SystemExit as e:
            check("project: col 키 중복 거부", e.code == 2)
        # set: 미정의 열·없는 P# 거부
        for kw, name in ((dict(pid="P1", key="nokey", value="v"), "set 미정의 열 exit 2"),
                         (dict(pid="P9", key="invest", value="v"), "set 없는 P# exit 2")):
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    m.cmd_project_set(A(slug="t1", from_json=None, **kw))
                check("project: " + name, False)
            except SystemExit as e:
                check("project: " + name, e.code == 2)
        # set --from 일괄
        cj = os.path.join(td, "c.json")
        json.dump([{"pid": "P1", "key": "invest", "value": "a | b"}, {"pid": "P2", "key": "reason", "value": "줄1\n줄2"}], open(cj, "w"))
        with contextlib.redirect_stdout(io.StringIO()):
            m.cmd_project_set(A(slug="t1", pid=None, key=None, value=None, from_json=cj))
        pj = m._pload("t1")
        check("project: set --from 일괄", pj["papers"][0]["cells"]["invest"] == "a | b" and pj["papers"][1]["cells"]["reason"] == "줄1\n줄2")
        # render: 빈 셀 strict → ValueError, allow → 파이프 이스케이프·줄바꿈 평탄화·DOI 링크·arXiv 링크
        try:
            m.render_project_md(pj, allow_empty=False)
            check("project: render strict 빈 셀 거부", False)
        except ValueError:
            check("project: render strict 빈 셀 거부", True)
        md = m.render_project_md(pj, allow_empty=True)
        check("project: render 파이프 이스케이프", "a \\| b" in md, md)
        check("project: render 줄바꿈 평탄화", "줄1 줄2" in md and "줄1\n줄2" not in md)
        check("project: render DOI 링크", "https://doi.org/10.1000/a" in md)
        check("project: render arXiv 링크", "https://arxiv.org/abs/2401.00001v1" in md)
        check("project: render 결측 셀 —", "| — |" in md)
        check("project: render 1저자 et al.", "Lee et al." not in md and "Lee ·" in md, md)  # refresh 후 저자 1명
        try:
            m.render_project_md(pj, cols="invest,zzz")
            check("project: render 미정의 --cols 거부", False)
        except ValueError:
            check("project: render 미정의 --cols 거부", True)
        md2 = m.render_project_md(pj, cols="invest")
        check("project: render --cols 선택", "투자 시사점" in md2 and "핵심 결론" not in md2)
        csv_ = m.render_project_csv(pj)
        check("project: render csv 헤더", csv_.splitlines()[0].startswith("pid,title,authors,first_author") and csv_.splitlines()[0].endswith(",invest"))
        # log → render 질문 이력
        with contextlib.redirect_stdout(io.StringIO()):
            m.cmd_project_log(A(slug="t1", question="Q1", note="노트A"))
        check("project: 질문 이력 위키링크", "[[노트A]]" in m.render_project_md(m._pload("t1")))
        # refs: DOI 없는 arXiv → 10.48550 DOI 합성(v 접미 제거), 1저자 성
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            m.cmd_project_refs(A(slug="t1", out=None))
        refs = json.load(open(os.path.join(td, "t1", "refs.json")))
        check("project: refs P#=id·1저자 성·year", refs[0] == {"id": "P1", "doi": "10.1000/a", "title": "Alpha paper (v2)", "author": "Lee", "year": 2021}, str(refs[0]))
        check("project: refs arXiv DOI 합성", refs[1]["doi"] == "10.48550/arXiv.2401.00001", str(refs[1]))
        # 레드팀 R5: --pids로 이번 답변이 인용한 P#만 내보내기(§6 3중 대조 성립)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            m.cmd_project_refs(A(slug="t1", out=None, pids="p1"))
        refs = json.load(open(os.path.join(td, "t1", "refs.json")))
        check("project: refs --pids → 지정 P#만(대소문자 무관)", [r["id"] for r in refs] == ["P1"], str(refs))
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                m.cmd_project_refs(A(slug="t1", out=None, pids="P1,P9"))
            check("project: refs --pids 미등록 P# → exit 2", False)
        except SystemExit as e:
            check("project: refs --pids 미등록 P# → exit 2", e.code == 2, f"exit={e.code}")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            m.cmd_project_refs(A(slug="t1", out=None))  # 이후 테스트가 전체 refs.json을 기대
        # rm: 삭제 후 P# 재사용 금지
        with contextlib.redirect_stdout(io.StringIO()):
            m.cmd_project_rm(A(slug="t1", pid="P2"))
        json.dump({"results": [{"doi": "10.1000/c", "title": "Gamma", "year": 2022, "authors": ["D E"]}]}, open(sj, "w"))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            m.cmd_project_add(A(slug="t1", ids=[], from_json=sj, pick=None, no_fetch=True, refresh=False))
        pids = [p["pid"] for p in m._pload("t1")["papers"]]
        check("project: rm 후 P# 재사용 금지", pids == ["P1", "P3"], str(pids))
        # 철회 논문 등록 거부 (no_fetch 행에 is_retracted)
        json.dump({"results": [{"doi": "10.1000/r", "title": "Retracted", "year": 2022, "authors": ["R R"], "is_retracted": True}]}, open(sj, "w"))
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                m.cmd_project_add(A(slug="t1", ids=[], from_json=sj, pick=None, no_fetch=True, refresh=False))
            code = 0
        except SystemExit as e:
            code = e.code
        check("project: 철회 논문 등록 거부", code == 1 and "10.1000/r" not in json.dumps(m._pload("t1")["papers"]))


_proj_tests()

def _import_db_tests():
    import types, json as _j
    with tempfile.TemporaryDirectory() as td:
        m.SCHOLAR_HOME = td
        A = lambda **k: types.SimpleNamespace(**k)
        f = os.path.join(td, "app.json")
        _j.dump({"id":"smr_x1","data":{"slug":"smr_x1","title":"SMR 경제성","created":"2026-09-06","columns":[
            {"key":"reason","label":"이 논문이 사용된 이유","kind":"ai","prompt":"x"},{"key":"limits_ab12","label":"한계점","kind":"ai","prompt":"한계"}],
            "papers":[],"questions":[{"date":"2026-09-06","q":"첫 질문"}]}}, open(f,"w"))
        out=io.StringIO()
        with contextlib.redirect_stdout(out):
            m.cmd_project_import_db(A(slug="smr_x1", file=f))
        pj=os.path.join(td,"smr_x1","project.json")
        check("import-db: 앱 프로젝트 → 레지스트리 신규 생성", os.path.exists(pj) and os.path.exists(os.path.join(td,"smr_x1","ledger.md")), out.getvalue())
        p=_j.load(open(pj))
        check("import-db: 제목·앱 추가 열·질문 병합", p["title"]=="SMR 경제성" and any(c["key"]=="limits_ab12" for c in p["columns"]) and len(p["questions"])==1, str(p["columns"]))
        check("import-db: 기본 열 4개 유지", [c["key"] for c in p["columns"][:4]]==["reason","keywords","focus","conclusion"])
        # 두 번째 import: 제목 변경 반영, 질문 중복 안 됨
        _j.dump({"slug":"smr_x1","title":"SMR 경제성 v2","columns":[],"papers":[],"questions":[{"date":"2026-09-06","q":"첫 질문"}]}, open(f,"w"))
        with contextlib.redirect_stdout(io.StringIO()):
            m.cmd_project_import_db(A(slug="smr_x1", file=f))
        p=_j.load(open(pj))
        check("import-db: 재실행 시 제목 갱신·질문 중복 없음", p["title"]=="SMR 경제성 v2" and len(p["questions"])==1)
_import_db_tests()

# ---- export: RIS · BibTeX · XLSX (2026-09-12 Liner 내보내기 재현) ----
def _export_tests():
    import zipfile, io as _io, types
    proj = {"slug": "exp", "title": "내보내기", "updated": "2026-09-12",
            "columns": [{"key": "keywords", "label": "키워드", "kind": "ai"}, {"key": "conclusion", "label": "핵심 결론", "kind": "ai"}],
            "papers": [
                {"pid": "P1", "doi": "10.1000/a&b", "title": "Language Models Don't Always Say What They Think: 50% & more",
                 "year": 2023, "venue": "NeurIPS", "type": "article", "citations": 90, "citations_s2": 1584, "is_oa": True,
                 "authors": ["Miles Turpin", "Julian Michael", "Ethan Perez"], "abstract": "line1\nline2",
                 "cells": {"keywords": "CoT · faithfulness · bias", "conclusion": "x"}},
                {"pid": "P2", "doi": "10.1000/c", "title": "Language of the mind", "year": 2023, "type": "article",
                 "authors": ["Miles Turpin"], "cells": {}},
                {"pid": "P3", "arxiv_id": "2401.00001v1", "title": "A preprint", "year": 2024, "type": "preprint",
                 "venue": "arXiv (Cornell University)", "authors": ["Kim, Jae-hyun"], "cells": {}},
                {"pid": "P4", "doi": "10.1000/d", "title": "Conf paper", "year": 2022, "type": "proceedings-article",
                 "venue": "ICML", "authors": [], "cells": {}},
            ]}
    ris = m.render_project_ris(proj)
    recs = [r for r in ris.split("ER  - ") if r.strip()]
    check("ris: 레코드 4개·ER 종결", len(recs) == 4 and ris.rstrip().endswith("ER  -"), ris[-40:])
    check("ris: TY 매핑 JOUR/UNPB/CONF", "TY  - JOUR" in recs[0] and "TY  - UNPB" in recs[2] and "TY  - CONF" in recs[3])
    check("ris: AU 'Family, Given' 변환·기존 쉼표형 보존", "AU  - Turpin, Miles" in recs[0] and "AU  - Kim, Jae-hyun" in recs[2], recs[2])
    check("ris: KW 키워드 3개 분리", recs[0].count("KW  - ") == 3)
    check("ris: AB 줄바꿈 제거·DO·UR", "AB  - line1 line2" in recs[0] and "DO  - 10.1000/a&b" in recs[0] and "UR  - https://arxiv.org/abs/2401.00001v1" in recs[2])
    check("ris: N1에 P#·인용수", "N1  - scholar-research exp P1 · 인용 OpenAlex 90/S2 1,584" in recs[0], recs[0])
    check("ris: 학회는 T2", "T2  - ICML" in recs[3] and "JO  - NeurIPS" in recs[0])
    bib = m.render_project_bibtex(proj)
    keys = __import__("re").findall(r"@(\w+)\{([^,]+),", bib)
    check("bibtex: @type 매핑", [k[0] for k in keys] == ["article", "article", "misc", "inproceedings"], str(keys))
    check("bibtex: 키 충돌 접미사(Turpin2023language ×2)", keys[0][1] == "Turpin2023language" and keys[1][1] == "Turpin2023languagea", str(keys))
    check("bibtex: 저자 없는 논문 키는 연도+단어", keys[3][1] == "2022conf", str(keys))
    check("bibtex: 특수문자 이스케이프·제목 이중중괄호", "title = {{Language Models Don't Always Say What They Think: 50\\% \\& more}}" in bib, bib[:300])
    check("bibtex: author ' and ' 결합", "author = {Miles Turpin and Julian Michael and Ethan Perez}" in bib)
    check("bibtex: arXiv eprint·howpublished", "eprint = {2401.00001v1}" in bib and "howpublished = {arXiv:2401.00001v1}" in bib)
    check("bibtex: 학회는 booktitle", "booktitle = {ICML}" in bib and "journal = {NeurIPS}" in bib)
    check("bibtex: keywords 쉼표 결합", "keywords = {CoT, faithfulness, bias}" in bib)
    pp = {"slug": "e2", "columns": [], "papers": [{"pid": "P1", "doi": "10.48550/arxiv.2205.11916", "title": "Zero-shot reasoners",
          "year": 2022, "type": "article", "venue": "arXiv (Cornell University)", "authors": ["Takeshi Kojima"], "cells": {}}]}
    b2, r2 = m.render_project_bibtex(pp), m.render_project_ris(pp)
    check("bibtex: arXiv DOI → @misc·eprint 복원·venue 제외", b2.startswith("@misc{") and "eprint = {2205.11916}" in b2 and "journal" not in b2, b2)
    check("ris: arXiv DOI → UNPB·JO 없음", "TY  - UNPB" in r2 and "JO  - " not in r2, r2)
    xb = m.render_project_xlsx(proj)
    z = zipfile.ZipFile(_io.BytesIO(xb))
    sheet = z.read("xl/worksheets/sheet1.xml").decode()
    check("xlsx: zip 구성 5파트", sorted(z.namelist()) == sorted(["[Content_Types].xml", "_rels/.rels", "xl/workbook.xml", "xl/_rels/workbook.xml.rels", "xl/worksheets/sheet1.xml"]), str(z.namelist()))
    check("xlsx: 행 5(헤더+4)·제목 XML 이스케이프", sheet.count("<row ") == 5 and "50% &amp; more" in sheet, sheet[:200])
    check("xlsx: 숫자 셀은 v, 불리언은 t=b", '<c r="H2"><v>90</v></c>' in sheet and 't="b"><v>1</v>' in sheet, sheet[:600])
    csv_ = m.render_project_csv(proj)
    check("csv: 공용 헤더(authors·url 포함)+AI 열", csv_.splitlines()[0].startswith("pid,title,authors,first_author,year,venue,type,citations_openalex") and csv_.splitlines()[0].endswith("keywords,conclusion"), csv_.splitlines()[0])
    check("csv: 빈 프로젝트도 헤더만", m.render_project_csv(dict(proj, papers=[])).count("\n") == 1)
    check("ris/bibtex: 빈 프로젝트는 빈 문자열", m.render_project_ris(dict(proj, papers=[])) == "" and m.render_project_bibtex(dict(proj, papers=[])) == "")
    # cmd_project_render 경로: xlsx는 --out 필수, 확장자 경고, bib 별칭
    with tempfile.TemporaryDirectory() as td:
        m.SCHOLAR_HOME = td
        os.makedirs(os.path.join(td, "exp")); json.dump(proj, open(os.path.join(td, "exp", "project.json"), "w"))
        A = lambda **k: types.SimpleNamespace(**dict(dict(slug="exp", out=None, cols=None, subtitle=None, verify_line=None, full=None, abstract=False, allow_empty=True), **k))
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                m.cmd_project_render(A(format="xlsx"))
            check("render: xlsx --out 없으면 exit 2", False)
        except SystemExit as e:
            check("render: xlsx --out 없으면 exit 2", e.code == 2, err.getvalue())
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            m.cmd_project_render(A(format="bib", out=os.path.join(td, "x.txt")))
        check("render: bib 별칭→bibtex·확장자 경고", "bibtex" in out.getvalue() and "권장 .bib" in err.getvalue(), out.getvalue() + err.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            m.cmd_project_render(A(format="xlsx", out=os.path.join(td, "x.xlsx")))
        check("render: xlsx 바이너리 파일 생성", zipfile.is_zipfile(os.path.join(td, "x.xlsx")))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            m.cmd_project_render(A(format="ris"))
        check("render: ris stdout", out.getvalue().startswith("TY  - JOUR"))
_export_tests()

# ---- 필터·정렬 (2026-09-12 Liner 표 툴바 재현) ----
def _filter_tests():
    import types
    proj = {"slug": "flt", "title": "필터", "created": "2026-09-12", "updated": "2026-09-12", "questions": [], "columns": [{"key": "conclusion", "label": "핵심 결론", "kind": "ai"}],
            "papers": [
                {"pid": "P1", "doi": "10.1/a", "title": "Alpha study", "year": 2021, "venue": "Nature", "citations": 10, "citations_s2": 500, "is_oa": True, "authors": ["A One"], "cells": {"conclusion": "gamma"}},
                {"pid": "P2", "doi": "10.1/b", "title": "Beta study", "year": 2024, "venue": "NeurIPS", "citations": 300, "is_oa": False, "authors": ["B Two"], "cells": {}},
                {"pid": "P3", "doi": "10.1/c", "title": "Charlie study", "year": 2023, "venue": "Nature Energy", "citations": None, "is_oa": True, "authors": ["C Three"], "abstract": "about GAMMA rays", "cells": {}},
            ]}
    A = lambda **k: types.SimpleNamespace(**dict(dict(pids=None, oa_only=False, year_from=None, year_to=None, min_cit=None, venue=None, grep=None, sort="n"), **k))
    ids = lambda rows: [r["pid"] for r in rows]
    r, c = m._select_papers(proj, A())
    check("filter: 조건 없음 → 원본 순서·조건 목록 비움", ids(r) == ["P1", "P2", "P3"] and c == [])
    check("filter: --sort cit (OA·S2 중 큰 값, 없으면 맨 뒤)", ids(m._select_papers(proj, A(sort="cit"))[0]) == ["P1", "P2", "P3"] and m._cit_max(proj["papers"][0]) == 500)
    check("filter: --sort year / yearasc / title", ids(m._select_papers(proj, A(sort="year"))[0]) == ["P2", "P3", "P1"] and ids(m._select_papers(proj, A(sort="yearasc"))[0]) == ["P1", "P3", "P2"] and ids(m._select_papers(proj, A(sort="title"))[0]) == ["P1", "P2", "P3"])
    check("filter: --oa-only", ids(m._select_papers(proj, A(oa_only=True))[0]) == ["P1", "P3"])
    check("filter: --year-from/--year-to", ids(m._select_papers(proj, A(year_from=2023))[0]) == ["P2", "P3"] and ids(m._select_papers(proj, A(year_to=2023))[0]) == ["P1", "P3"])
    check("filter: --min-cit (인용 없음은 0 취급)", ids(m._select_papers(proj, A(min_cit=300))[0]) == ["P1", "P2"] and ids(m._select_papers(proj, A(min_cit=0))[0]) == ["P1", "P2", "P3"])
    check("filter: --venue 부분일치·대소문자 무시", ids(m._select_papers(proj, A(venue="nature"))[0]) == ["P1", "P3"])
    check("filter: --grep 제목·초록·AI 열", ids(m._select_papers(proj, A(grep="gamma"))[0]) == ["P1", "P3"])
    check("filter: --pids", ids(m._select_papers(proj, A(pids="p3,P1"))[0]) == ["P1", "P3"])
    r, c = m._select_papers(proj, A(oa_only=True, sort="year", min_cit=1))
    check("filter: 복합 조건 AND + 조건 설명", ids(r) == ["P1"] and c == ["오픈액세스만", "인용 ≥1(OpenAlex·S2 중 큰 값)", "최신순"], str(c))
    p2, note = m._apply_selection(proj, A(year_from=2023))
    check("filter: _apply_selection은 사본(원본 불변)+note", len(proj["papers"]) == 3 and len(p2["papers"]) == 2 and note.startswith("전체 3편 중 2편 — 2023년 이후"), note)
    md_ = m.render_project_md(p2, allow_empty=True)
    check("filter: md 머리말에 필터 줄", "> 🔎 필터·정렬: 전체 3편 중 2편" in md_ and "| P2 |" in md_ and "| P1 |" not in md_, md_[:400])
    html_ = m.render_project_html(p2)
    check("filter: html 부제에 필터·연도/게재지 옵션", "🔎 전체 3편 중 2편" in html_ and '<option value="2024">2024년 이후</option>' in html_ and '<option value="NeurIPS">' in html_ and "id=\"mincit\"" in html_)
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        p3, note3 = m._apply_selection(proj, A(venue="zzz"))
    check("filter: 0편이면 stderr 경고(렌더는 계속)", p3["papers"] == [] and "0편" in err.getvalue())
    with tempfile.TemporaryDirectory() as td:
        m.SCHOLAR_HOME = td
        os.makedirs(os.path.join(td, "flt")); json.dump(proj, open(os.path.join(td, "flt", "project.json"), "w"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            m.cmd_project_render(A(slug="flt", format="ris", out=None, cols=None, subtitle=None, verify_line=None, full=None, abstract=False, allow_empty=True, sort="cit", min_cit=300))
        check("filter: render ris에도 필터·정렬 적용", out.getvalue().count("TY  - ") == 2 and out.getvalue().index("ID  - P1") < out.getvalue().index("ID  - P2"), out.getvalue()[:200])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            m.cmd_project_show(A(slug="flt", oa_only=True))
        check("filter: show에 필터 적용·🔎 줄", out.getvalue().startswith("🔎 전체 3편 중 2편") and "논문 2편" in out.getvalue(), out.getvalue()[:200])
        check("filter: 레지스트리 파일은 불변", len(json.load(open(os.path.join(td, "flt", "project.json")))["papers"]) == 3)
_filter_tests()

# ---- review 패킷 (2026-09-12 피어 리뷰 5렌즈) ----
def _review_tests():
    import types
    draft = """---
title: "CoT는 추론인가"
---
# CoT는 실제 추론인가
## 배경
사고연쇄는 2022년 제안됐다 [P1]. 이 글은 답변 신뢰성을 묻는다.
## 결과
CoT 설명의 충실도는 GSM8K에서 40.7%로 떨어졌다 [P2]. 편향 실험에서 정확도가 36% 하락했다.
이 결과는 CoT가 추론이 아님을 명백히 입증한다.
Turpin et al. reported 1,584 citations (Turpin, 2023).
See also 10.48550/arxiv.2205.11916 and [P9].
| 표 | 100% |
```
code 99%
```
## 한계
초록만 읽은 논문이 3편이다 [P3].
"""
    proj = {"slug": "rv", "papers": [{"pid": "P1"}, {"pid": "P2"}, {"pid": "P3"}]}
    pk = m.review_packet(draft, proj, "t")
    check("review: 섹션 탐지(배경·결과·한계 ✓, 방법·결론 ✗)", pk["sections"]["intro"]["found"] == "배경" and pk["sections"]["results"]["found"] and pk["sections"]["limitations"]["found"] and not pk["sections"]["method"]["found"] and not pk["sections"]["conclusion"]["found"], str(pk["sections"]))
    check("review: [P#] 사용·미등록", pk["pids_used"] == ["P1", "P2", "P3", "P9"] and pk["pids_missing"] == ["P9"])
    check("review: DOI 추출", pk["dois"] == ["10.48550/arxiv.2205.11916"], str(pk["dois"]))
    un = pk["uncited_numeric"]
    check("review: 근거 없는 수치 문장 1건(36% 하락)·연도만 있는 문장 제외·표/코드 제외", len(un) == 1 and "36%" in un[0]["numbers"][0] and "하락" in un[0]["sentence"], str(un))
    check("review: 인용 있는 수치 문장은 제외(40.7% [P2]·1,584 (Turpin, 2023))", all("40.7" not in u["sentence"] and "1,584" not in u["sentence"] for u in un))
    ov = pk["overclaims"]
    check("review: 과장어(명백히·입증) 문장·인용 없음 표시", len(ov) == 1 and set(ov[0]["words"]) >= {"명백", "입증"} and ov[0]["cited"] is False, str(ov))
    check("review: 단어·문장 수 양수", pk["words"] > 30 and pk["sentences"] >= 6)
    md_, summ = m.render_review_packet(pk)
    check("review: 요약줄", "섹션 3/8" in summ and "[P#] 4개(미등록 1: P9)" in summ and "근거 없는 수치 문장 1" in summ and "과장어 1" in summ, summ)
    check("review: 렌즈 5개 골격·finding 표 5개", md_.count("## 렌즈") == 5 and md_.count("| # | 등급 | 규칙 |") == 5 and "⚠ R2 **레지스트리에 없는 [P#]**: P9" in md_)
    pk2 = m.review_packet(draft, None, "t")
    check("review: --project 없으면 미대조", pk2["pids_missing"] == [] and pk2["registry"] is None and "레지스트리 미대조" in m.render_review_packet(pk2)[1])
    with tempfile.TemporaryDirectory() as td:
        m.SCHOLAR_HOME = td
        os.makedirs(os.path.join(td, "rv")); json.dump(dict(proj, title="x", columns=[], questions=[], created="2026-09-12"), open(os.path.join(td, "rv", "project.json"), "w"))
        f = os.path.join(td, "d.md"); open(f, "w").write(draft)
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                m.cmd_review(types.SimpleNamespace(file=f, project="rv", out=os.path.join(td, "pk.md"), json=False))
            check("review: 미등록 P#면 exit 1", False)
        except SystemExit as e:
            check("review: 미등록 P#면 exit 1", e.code == 1 and "P9" in err.getvalue() and os.path.exists(os.path.join(td, "pk.md")), err.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            m.cmd_review(types.SimpleNamespace(file=f, project=None, out=None, json=True))
        check("review: --json", json.loads(out.getvalue())["pids_used"] == ["P1", "P2", "P3", "P9"])
        check("review: 제목은 첫 # 헤딩", json.loads(out.getvalue())["title"] == "CoT는 실제 추론인가")
_review_tests()

# ---- graph 인용 그래프 (2026-09-12, OpenAlex 모킹) ----
def _graph_tests():
    import types, urllib.parse as up
    W = lambda n: f"https://openalex.org/W{n}"
    works = {
        "10.1/a": {"id": W(1), "doi": "https://doi.org/10.1/a", "display_name": "Alpha", "publication_year": 2020, "cited_by_count": 100, "referenced_works": [W(9), W(8)]},
        "10.1/b": {"id": W(2), "doi": "https://doi.org/10.1/b", "display_name": "Beta", "publication_year": 2022, "cited_by_count": 50, "referenced_works": [W(1), W(9), W(7)]},
        "10.1/c": {"id": W(3), "doi": "https://doi.org/10.1/c", "display_name": "Gamma", "publication_year": 2024, "cited_by_count": 5, "referenced_works": [W(1), W(2), W(9)]},
    }
    hubmeta = {"W9": {"id": W(9), "doi": "https://doi.org/10.9/hub", "display_name": "Hub paper [x]", "publication_year": 2015, "cited_by_count": 9000}}
    calls = []
    def fake(url, **kw):
        calls.append(url)
        q = dict(up.parse_qsl(up.urlparse(url).query))
        f = q.get("filter", "")
        if f.startswith("doi:"):
            want = f[4:].split("|")
            return json.dumps({"results": [works[d] for d in want if d in works]}), 200
        if f.startswith("openalex_id:"):
            want = f[len("openalex_id:"):].split("|")
            return json.dumps({"results": [hubmeta[w] for w in want if w in hubmeta]}), 200
        return None, 500
    m.http_get = fake
    proj = {"slug": "g", "title": "그래프", "papers": [
        {"pid": "P1", "doi": "10.1/a", "title": "Alpha", "year": 2020, "citations": 100},
        {"pid": "P2", "doi": "10.1/b", "title": "Beta", "year": 2022, "citations": 50},
        {"pid": "P3", "doi": "10.1/c", "title": "Gamma", "year": 2024, "citations": 5},
        {"pid": "P4", "doi": "10.1/none", "title": "Missing in OA", "year": 2023},
        {"pid": "P5", "arxiv_id": "2401.1", "title": "arXiv only", "year": 2024},
    ]}
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        g = m.build_citation_graph(proj, check=False)
    check("graph: 내부 간선(P2→P1, P3→P1, P3→P2)", sorted(g["edges"]) == [("P2", "P1"), ("P3", "P1"), ("P3", "P2")], str(g["edges"]))
    check("graph: 허브 = 2편 이상 공통 참조(W9만; W8·W7은 1편)", len(g["hubs"]) == 1 and g["hubs"][0]["wid"] == "W9" and g["hubs"][0]["cited_by"] == ["P1", "P2", "P3"] and g["hubs"][0]["doi"] == "10.9/hub", str(g["hubs"]))
    check("graph: 미해결(P4 OpenAlex에 없음·P5 arXiv DOI로 조회 시도)·고립 없음", g["unresolved"] == ["P4", "P5"] and g["isolated"] == [], str(g["unresolved"]))
    check("graph: arXiv-only 논문은 10.48550/arxiv.<id>로 배치 조회", any("10.48550" in c for c in calls), str(calls[:1]))
    kinds = [dict(up.parse_qsl(up.urlparse(c).query)).get("filter", "").split(":")[0] for c in calls]
    check("graph: DOI 배치 1회 + 허브 메타 1회", kinds == ["doi", "openalex_id"], str(kinds))
    mer = m.render_graph_mermaid(g)
    check("graph: mermaid 노드·간선·허브 점선·대괄호 치환", mer.startswith("graph LR") and "  P3 --> P1" in mer and "  P1 -.-> H1" in mer and 'Hub paper (x)' in mer and "class H1 hub" in mer, mer)
    check("graph: mermaid 연도 음영(최신 진함, hex — 쉼표 있는 hsl은 mermaid 문법오류)", "style P3 fill:#40５e9e".replace("５","5") in mer and "style P1 fill:#e0eaf8,color:#111" in mer and "hsl(" not in mer, mer)
    md_ = m.render_graph_md(g)
    check("graph: md 허브 표·미해결 표기", "| H1 | Hub paper [x] | 2015 | 9000 | P1, P2, P3 | 10.9/hub |" in md_ and "미해결 2편(P4, P5)" in md_ and "```mermaid" in md_, md_[:600])
    html_ = m.render_graph_html(g)
    check("graph: html <pre class=\"mermaid\">·허브 표", '<pre class="mermaid">graph LR' in html_ and "https://doi.org/10.9/hub" in html_)
    g2 = m.build_citation_graph(proj, min_shared=3, check=False)
    check("graph: --min-shared 3 → 허브 W9(3편) 유지", len(g2["hubs"]) == 1)
    g3 = m.build_citation_graph(proj, min_shared=4, check=False)
    check("graph: --min-shared 4 → 허브 0·메타 조회 생략", g3["hubs"] == [])
    # 고립: P3만 넣으면 간선 0
    g4 = m.build_citation_graph(dict(proj, papers=[proj["papers"][2], proj["papers"][3]]), check=False)
    check("graph: 단독 논문은 고립 목록(미해결은 고립 아님)", g4["isolated"] == ["P3"] and g4["edges"] == [])
    with tempfile.TemporaryDirectory() as td:
        m.SCHOLAR_HOME = td
        os.makedirs(os.path.join(td, "g")); json.dump(dict(proj, columns=[], questions=[], created="2026-09-12"), open(os.path.join(td, "g", "project.json"), "w"))
        A = lambda **k: types.SimpleNamespace(**dict(dict(slug="g", pids=None, oa_only=False, year_from=None, year_to=None, min_cit=None, venue=None, grep=None, sort="n", format="md", out=None, min_shared=2, hubs=10), **k))
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            m.cmd_graph(A(format="mermaid", year_from=2022))
        check("graph: cmd 필터 적용(2022+ → P2,P3,P5) mermaid stdout", out.getvalue().startswith("graph LR") and "P1[" not in out.getvalue() and "P2 -->" not in out.getvalue() and "P3 --> P2" in out.getvalue(), out.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            m.cmd_graph(A(format="json", out=os.path.join(td, "g.json")))
        check("graph: --out json", "그래프 →" in out.getvalue() and json.load(open(os.path.join(td, "g.json")))["edges"])
    # check=True: 허브 제목 오염 교체 + 표 안 논문 별도 레코드 병합
    works2 = dict(works); works2["10.1/c"] = dict(works["10.1/c"], referenced_works=[W(1), W(2), W(9), W(11)]); works2["10.1/b"] = dict(works["10.1/b"], referenced_works=[W(1), W(9), W(11)])
    hub2 = dict(hubmeta); hub2["W11"] = {"id": W(11), "doi": "https://doi.org/10.48550/arxiv.2401.1", "display_name": "CORRUPTED TITLE", "publication_year": 2024, "cited_by_count": 7}
    hub2["W9"] = dict(hub2["W9"], display_name="Wrong Name Entirely")
    def fake2(url, **kw):
        calls.append(url)
        if "api.crossref.org/works/10.9/hub" in url:
            return json.dumps({"message": {"title": ["Real Hub Title"]}}), 200
        if "export.arxiv.org" in url:
            return "<feed><entry><id>http://arxiv.org/abs/2401.1v1</id><title>arXiv only</title></entry></feed>", 200
        q = dict(up.parse_qsl(up.urlparse(url).query)); f = q.get("filter", "")
        if f.startswith("doi:"):
            return json.dumps({"results": [works2[d] for d in f[4:].split("|") if d in works2]}), 200
        if f.startswith("openalex_id:"):
            return json.dumps({"results": [hub2[w] for w in f[len("openalex_id:"):].split("|") if w in hub2]}), 200
        return None, 500
    m.http_get = fake2; m._ARXIV_LAST[0] = 0
    _orig_parse = m._arxiv_parse
    m._arxiv_parse = lambda body: [{"title": "arXiv only", "arxiv_id": "2401.1"}] if "2401.1" in body else []
    with contextlib.redirect_stderr(io.StringIO()):
        g6 = m.build_citation_graph(proj)
    h9 = next((h for h in g6["hubs"] if h["wid"] == "W9"), None)
    check("graph: check — 오염된 OpenAlex 제목을 Crossref 제목으로 교체+flag", h9 and h9["title"] == "Real Hub Title" and h9["title_oa"] == "Wrong Name Entirely" and "오염" in h9["flag"], str(h9))
    check("graph: check — 표 안 arXiv 논문(P5)의 별도 레코드 W11은 허브가 아니라 간선(P2→P5, P3→P5)", all(h["wid"] != "W11" for h in g6["hubs"]) and ("P2", "P5") in g6["edges"] and ("P3", "P5") in g6["edges"] and g6["merged"][0]["pid"] == "P5", str(g6["merged"]) + str(g6["edges"]))
    md6 = m.render_graph_md(g6)
    check("graph: md에 ⚠ flag·병합 절", "⚠ OpenAlex 제목 오염 의심" in md6 and "## 표 안 논문의 별도 OpenAlex 레코드" in md6 and "P5 ← P2, P3" in md6, md6[-600:])
    t, src = m._authoritative_title("10.9/hub"); check("graph: _authoritative_title crossref", (t, src) == ("Real Hub Title", "crossref"))
    t, src = m._authoritative_title("10.48550/arxiv.2401.1"); check("graph: _authoritative_title arXiv", (t, src) == ("arXiv only", "arxiv"))
    m.http_get = lambda url, **kw: (None, 404)
    t, src = m._authoritative_title("10.9/none"); check("graph: _authoritative_title 실패는 (None, 'crossref 404')", t is None and src == "crossref 404")
    m._arxiv_parse = _orig_parse
    m.http_get = lambda url, **kw: (None, 503)
    with contextlib.redirect_stderr(io.StringIO()):
        g5 = m.build_citation_graph(proj)
    check("graph: OpenAlex 전부 실패 → 전원 미해결·간선 0(조용히 성공 위장 X)", len(g5["unresolved"]) == 5 and g5["edges"] == [])
    m.http_get = ORIG_HTTP_GET
_graph_tests()


# ---- bundle: 원샷 검색→dedup→랭킹→상위 K편 초록 병렬 조회→JSON(--out)·project add 호환 ----
def _bundle_tests():
    import threading
    import time as _t
    import types
    orig_fns = (m.search_openalex, m.search_crossref, m.search_s2, m.search_arxiv)
    _mk = lambda src, doi, cit, **kw: dict({"source": src, "doi": doi, "title": "T " + (doi or kw.get("id", "")),
                                            "year": 2021, "citations": cit, "is_oa": False, "authors": ["A"]}, **kw)
    def _oa(q, n, yf=None, oa=False):
        return [_mk("openalex", "10.1/a", 50, id="https://openalex.org/W1"), _mk("openalex", "10.1/b", 5),
                _mk("openalex", "10.1/c", 3)], None
    def _cr(q, n, yf=None):
        return [_mk("crossref", "10.1/a", 40), _mk("crossref", "10.1/z", 1)], None
    def _s2(q, n, yf=None):
        return [_mk("s2", "10.1/a", 60, citations_s2=60), _mk("s2", "10.48550/arxiv.2402.00002", 2, citations_s2=2)], None
    def _ax(q, n, yf=None):
        return [_mk("arxiv", None, None, id="https://arxiv.org/abs/2401.00001v1", venue="arXiv")], None
    m.search_openalex, m.search_crossref, m.search_s2, m.search_arxiv = _oa, _cr, _s2, _ax

    calls = []
    lock = threading.Lock()
    def _http(url, **kw):
        with lock:
            calls.append(url)
        _t.sleep(0.2)
        if "openalex.org/works/doi:10.1/a" in url:
            return json.dumps({"doi": "https://doi.org/10.1/a", "display_name": "T 10.1/a", "publication_year": 2021,
                               "cited_by_count": 55, "primary_location": {"source": {"display_name": "J-A"}},
                               "open_access": {"is_oa": True, "oa_url": "https://x/a.pdf"},
                               "authorships": [{"author": {"display_name": "Ann Lee"}}, {"author": {"display_name": "Bo Kim"}}],
                               "abstract_inverted_index": {"Alpha": [0], "abstract": [1], "text": [2]}}), 200
        if "openalex.org/works/doi:10.1/b" in url:
            return json.dumps({"doi": "https://doi.org/10.1/b", "display_name": "T 10.1/b", "publication_year": 2021,
                               "cited_by_count": 5, "authorships": [], "abstract_inverted_index": None}), 200
        if "crossref.org/works/10.1/b" in url:
            return json.dumps({"message": {"abstract": "<jats:p>Beta &amp; abstract</jats:p>"}}), 200
        if "10.1/z" in url:
            raise RuntimeError("boom")
        return None, 404
    m.http_get = _http

    t0 = _t.monotonic()
    with contextlib.redirect_stderr(io.StringIO()):
        b = m.build_bundle("q", limit=10, top=10, sources_spec="openalex,crossref,s2,arxiv")
    dt = _t.monotonic() - t0
    by = {p.get("doi") or p.get("id"): p for p in b["papers"]}
    check("bundle: 3소스 중복 DOI(10.1/a ×3)가 한 편으로 병합", sum(1 for p in b["papers"] if p.get("doi") == "10.1/a") == 1
          and b["counts"]["merged"] == 6, str([p.get("doi") for p in b["papers"]]))
    a_ = by.get("10.1/a", {})
    check("bundle: 병합 행에 인용수 max·citations_s2·also_in 보존", a_.get("citations") == 60 and a_.get("citations_s2") == 60
          and sorted(a_.get("also_in", [])) == ["crossref", "s2"], str(a_))
    check("bundle: counts.per_source = 소스별 원건수", b["counts"]["per_source"] == {"openalex": 3, "crossref": 2, "s2": 2, "arxiv": 1}
          and b["counts"]["top"] == 6, str(b["counts"]))
    check("bundle: OpenAlex 초록 채움(abstract_source=openalex)·메타 보강(OA·저자·venue)",
          a_.get("abstract") == "Alpha abstract text" and a_.get("abstract_source") == "openalex" and a_.get("is_oa") is True
          and a_.get("oa", {}).get("oa_url") == "https://x/a.pdf" and a_.get("venue") == "J-A" and a_.get("authors") == ["Ann Lee", "Bo Kim"], str(a_))
    b_ = by.get("10.1/b", {})
    check("bundle: OpenAlex 초록 없음 → Crossref abstract 폴백(JATS 제거·엔티티 복원)",
          b_.get("abstract") == "Beta & abstract" and b_.get("abstract_source") == "crossref", str(b_))
    c_ = by.get("10.1/c", {})
    check("bundle: 양쪽 404 → 빈 초록 + 사유(조회 실패)", c_.get("abstract") == "" and c_.get("abstract_source") is None
          and "조회 실패" in (c_.get("abstract_reason") or ""), str(c_))
    z_ = by.get("10.1/z", {})
    check("bundle: 한 편의 예외는 그 행의 사유로 격리(전체 생존)", z_.get("abstract") == "" and "예외" in (z_.get("abstract_reason") or "")
          and "boom" in z_["abstract_reason"], str(z_))
    ax = by.get("https://arxiv.org/abs/2401.00001v1", {})
    check("bundle: arXiv 항목(doi=None)은 조회 없이 빈 초록 + pdf URL 안내(§4)", ax.get("abstract") == ""
          and "https://arxiv.org/pdf/2401.00001v1" in (ax.get("abstract_reason") or "")
          and not any("2401.00001" in u for u in calls), str(ax) + str(calls))
    ax2 = by.get("10.48550/arxiv.2402.00002", {})
    check("bundle: 10.48550/* DOI도 arXiv 취급(paper 404 회피)", ax2.get("abstract") == ""
          and "https://arxiv.org/pdf/2402.00002" in (ax2.get("abstract_reason") or "") and not any("2402.00002" in u for u in calls), str(ax2))
    check("bundle: 초록 조회 병렬(0.2s×4편 DOI 조회가 0.6s 미만)", dt < 0.6 and b["counts"]["abstracts"] == 2, f"{dt:.2f}s {b['counts']}")
    check("bundle: pid P1..Pn 연번·elapsed_sec 3키·스키마 필수 키", [p["pid"] for p in b["papers"]] == [f"P{i}" for i in range(1, 7)]
          and set(b["elapsed_sec"]) == {"search", "abstracts", "total"}
          and {"query", "sources_used", "counts", "papers", "elapsed_sec"} <= set(b)
          and all({"pid", "doi", "title", "year", "venue", "citations", "citations_s2", "abstract", "abstract_source", "oa"} <= set(p) for p in b["papers"])
          and all("_rank" not in p for p in b["papers"]), str(b.keys()) + str(b["papers"][0].keys()))

    # --top K: K편만 조회, 나머지는 '상위 밖' 사유
    calls.clear()
    with contextlib.redirect_stderr(io.StringIO()):
        b2 = m.build_bundle("q", limit=10, top=2, sources_spec="openalex,crossref,s2,arxiv")
    n_fetched = sum(1 for p in b2["papers"] if p.get("abstract_reason") is None or "상위" not in (p.get("abstract_reason") or ""))
    check("bundle: --top 2 → 2편만 초록 조회·나머지는 '상위 2편 밖' 사유", b2["counts"]["top"] == 2 and n_fetched == 2
          and sum(1 for p in b2["papers"] if "상위 2편 밖" in (p.get("abstract_reason") or "")) == 4, str([(p["pid"], p.get("abstract_reason")) for p in b2["papers"]]))
    check("bundle: 상위 2편은 랭킹 순(P1=최다 인용 10.1/a)", b2["papers"][0]["doi"] == "10.1/a" and b2["papers"][0]["abstract"], str(b2["papers"][0]))

    # cmd_bundle --out 저장 + 요약 표 + project add --from 호환
    with tempfile.TemporaryDirectory() as td:
        outp = os.path.join(td, "sub", "b.json")
        o = io.StringIO()
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(io.StringIO()):
            m.cmd_bundle(types.SimpleNamespace(query="q", limit=10, top=8, sources="openalex,crossref,s2,arxiv",
                                               year_from=None, oa_only=False, out=outp, json=False))
        txt = o.getvalue()
        check("bundle: --out 저장(하위 폴더 생성)·JSON 로드", os.path.exists(outp) and json.load(open(outp))["counts"]["merged"] == 6, txt)
        check("bundle: 요약 표(검색/병합/초록/초 · [P#] 행 · 초록 O/X · JSON 경로)", "# 검색 openalex 3 / crossref 2" in txt
              and "병합 6" in txt and "초록 2/6" in txt and "[P1]" in txt and "초록 O (openalex" in txt and "초록 X — arXiv" in txt
              and f"# JSON → {outp}" in txt and "(S2 60)" in txt, txt)
        m.SCHOLAR_HOME = td
        with contextlib.redirect_stdout(io.StringIO()):
            m.cmd_project_init(types.SimpleNamespace(slug="bt", title="b"))
        o = io.StringIO(); e = io.StringIO(); code = 0
        try:
            with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
                m.cmd_project_add(types.SimpleNamespace(slug="bt", ids=[], from_json=outp, pick="P1,P2,P3", no_fetch=True, refresh=False))
        except SystemExit as ex:
            code = ex.code
        pj = m._pload("bt")
        check("bundle: project add --from bundle.json --pick P1,P2,P3 --no-fetch → 3편 등록(초록·citations_s2 그대로)", code == 0 and len(pj["papers"]) == 3
              and pj["papers"][0]["doi"] == "10.1/a" and pj["papers"][0].get("abstract") == "Alpha abstract text"
              and pj["papers"][0].get("citations_s2") == 60 and pj["papers"][0]["via"].startswith("bundle:"), o.getvalue() + e.getvalue() + str(pj["papers"][:1]))
        # --json 출력 경로
        o = io.StringIO()
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(io.StringIO()):
            m.cmd_bundle(types.SimpleNamespace(query="q", limit=10, top=1, sources="openalex", year_from=None, oa_only=False,
                                               out=os.path.join(td, "j.json"), json=True))
        jj = json.loads(o.getvalue())
        check("bundle: --json은 stdout에 JSON(표 대신)", jj["counts"]["top"] == 1 and jj["sources_used"] == ["openalex"], o.getvalue()[:200])

    # 결과 0건 → exit 1(조용한 성공 위장 X) · 미인식 소스 → exit 2(search 계약 그대로)
    m.search_openalex = lambda q, n, yf=None, oa=False: ([], "openalex HTTP 503")
    with tempfile.TemporaryDirectory() as td:
        code = 0
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                m.cmd_bundle(types.SimpleNamespace(query="q", limit=5, top=8, sources="openalex", year_from=None, oa_only=False,
                                                   out=os.path.join(td, "e.json"), json=False))
        except SystemExit as ex:
            code = ex.code
        check("bundle: 결과 0건(소스 전멸) → exit 1 + JSON은 저장(errors 기록)", code == 1 and json.load(open(os.path.join(td, "e.json")))["errors"] == ["openalex HTTP 503"])
        code = 0
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                m.cmd_bundle(types.SimpleNamespace(query="q", limit=5, top=8, sources="bogus", year_from=None, oa_only=False,
                                                   out=os.path.join(td, "e2.json"), json=False))
        except SystemExit as ex:
            code = ex.code
        check("bundle: 미인식 소스 → exit 2", code == 2)

    # 파서 기본값: top 8 · limit 10 · sources = search 기본과 동일
    captured = {}
    _orig_cb, _orig_argv = m.cmd_bundle, sys.argv
    m.cmd_bundle = lambda a: captured.update(vars(a))
    try:
        sys.argv = ["scholar.py", "bundle", "q"]; m.main()
    finally:
        m.cmd_bundle, sys.argv = _orig_cb, _orig_argv
    check("bundle: 파서 기본값 top=8·limit=10·sources=openalex,crossref,s2·out=None",
          captured.get("top") == 8 and captured.get("limit") == 10 and captured.get("sources") == "openalex,crossref,s2"
          and captured.get("out") is None, str(captured))

    m.search_openalex, m.search_crossref, m.search_s2, m.search_arxiv = orig_fns
    m.http_get = ORIG_HTTP_GET
_bundle_tests()

# ---- retracted-slot-exclusion: 철회 행은 맨 뒤 + bundle 상위 K 초록 슬롯 제외(--include-retracted면 기존 동작) ----
def _retracted_slot_tests():
    import threading
    import types
    # (1) merge_and_rank: 철회 행(최다 인용)이 맨 뒤로 — 제거 X·is_retracted 유지·비철회 상대순서 유지
    _p = lambda doi, cit, **kw: dict({"source": "openalex", "doi": doi, "title": "Paper " + doi, "year": 2020,
                                      "citations": cit, "is_oa": True}, **kw)
    pool = [_p("10.9/ok1", 50), _p("10.9/bad", 900, is_retracted=True), _p("10.9/ok2", 30), _p("10.9/ok3", 10)]
    out = m.merge_and_rank([dict(x) for x in pool], 10)
    check("retracted: 철회 행은 랭킹 무관 맨 뒤(제거 X·⚠️ 플래그 유지)",
          [x["doi"] for x in out] == ["10.9/ok1", "10.9/ok2", "10.9/ok3", "10.9/bad"] and out[-1].get("is_retracted") is True,
          str([(x["doi"], x.get("is_retracted")) for x in out]))
    out_i = m.merge_and_rank([dict(x) for x in pool], 10, include_retracted=True)
    check("retracted: include_retracted=True → 정렬 위치 그대로(기존 동작)",
          [x["doi"] for x in out_i] == ["10.9/bad", "10.9/ok1", "10.9/ok2", "10.9/ok3"], str([x["doi"] for x in out_i]))
    # 철회 2건이면 철회끼리도 랭킹 순서 유지(안정 분할)
    pool2 = pool + [_p("10.9/bad2", 5, is_retracted=True)]
    out2 = m.merge_and_rank([dict(x) for x in pool2], 10)
    check("retracted: 철회 2건 → 뒤쪽에서 철회끼리 랭킹 순서 유지(안정 분할)",
          [x["doi"] for x in out2][-2:] == ["10.9/bad", "10.9/bad2"], str([x["doi"] for x in out2]))
    # limit 절단은 철회 여부와 무관(슬롯은 소모 — 은닉 아님)
    out3 = m.merge_and_rank([dict(x) for x in pool], 2)
    check("retracted: limit 절단 후 분할(철회 행도 limit 슬롯은 정상 소모, 결과에 남음)",
          [x["doi"] for x in out3] == ["10.9/ok1", "10.9/bad"], str([x["doi"] for x in out3]))
    # query 경로에서도 동일
    out4 = m.merge_and_rank([dict(x) for x in pool], 10, query="paper")
    check("retracted: query 랭킹 경로에서도 철회 행 맨 뒤", out4[-1]["doi"] == "10.9/bad", str([x["doi"] for x in out4]))

    # (2) bundle: 철회 행이 2위여도 head는 건너뛰고 K편 채움
    orig_fns = (m.search_openalex, m.search_crossref, m.search_s2, m.search_arxiv)
    def _oa(q, n, yf=None, oa=False):
        return [_p("10.9/ok1", 50, authors=["A"]), _p("10.9/bad", 900, is_retracted=True, authors=["B"]),
                _p("10.9/ok2", 30, authors=["C"]), _p("10.9/ok3", 10, authors=["D"])], None
    m.search_openalex = _oa
    calls = []
    lock = threading.Lock()
    def _http(url, **kw):
        with lock:
            calls.append(url)
        for d in ("ok1", "bad", "ok2", "ok3"):
            if f"openalex.org/works/doi:10.9/{d}" in url and "select=" not in url:
                return json.dumps({"doi": f"https://doi.org/10.9/{d}", "display_name": f"Paper 10.9/{d}",
                                   "publication_year": 2020, "cited_by_count": 1, "is_retracted": d == "bad",
                                   "authorships": [], "abstract_inverted_index": {"Abs": [0], d: [1]}}), 200
        if "select=" in url:
            return json.dumps({"is_retracted": "bad" in url}), 200
        return None, 404
    orig_http = m.http_get
    m.http_get = _http
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            b = m.build_bundle("q", limit=10, top=2, sources_spec="openalex")
        by = {x["doi"]: x for x in b["papers"]}
        check("bundle retracted: 철회 행 맨 뒤(P4)·⚠️ 플래그 유지",
              [x["doi"] for x in b["papers"]] == ["10.9/ok1", "10.9/ok2", "10.9/ok3", "10.9/bad"]
              and by["10.9/bad"]["pid"] == "P4" and by["10.9/bad"].get("is_retracted") is True,
              str([(x["pid"], x["doi"]) for x in b["papers"]]))
        check("bundle retracted: head는 철회 행을 건너뛰고 K=2편(ok1·ok2) 초록 채움",
              b["counts"]["top"] == 2 and b["counts"]["abstracts"] == 2
              and by["10.9/ok1"]["abstract"] == "Abs ok1" and by["10.9/ok2"]["abstract"] == "Abs ok2",
              str(b["counts"]) + str([(x["doi"], x.get("abstract")) for x in b["papers"]]))
        check("bundle retracted: 철회 행 abstract_reason=슬롯 제외 사유·초록 미조회(HTTP 호출 없음)",
              by["10.9/bad"]["abstract"] == "" and by["10.9/bad"]["abstract_reason"] == "철회 논문 — 상위 초록 슬롯에서 제외(필요하면 paper <doi>)"
              and not any("10.9/bad" in u for u in calls), str(by["10.9/bad"].get("abstract_reason")) + str(calls))
        check("bundle retracted: 비철회 상위 밖 행은 기존 '상위 2편 밖' 사유 그대로",
              "상위 2편 밖" in (by["10.9/ok3"].get("abstract_reason") or ""), str(by["10.9/ok3"].get("abstract_reason")))
        txt = m.render_bundle(b)
        check("bundle retracted: 요약 표에 ⚠️RETRACTED·제외 사유 표시",
              "[P4] (2020) Paper 10.9/bad ⚠️RETRACTED" in txt and "초록 X — 철회 논문 — 상위 초록 슬롯에서 제외" in txt, txt)
        # top이 비철회 편수보다 크면 비철회만큼만(철회 행으로 채우지 않음)
        calls.clear()
        with contextlib.redirect_stderr(io.StringIO()):
            b5 = m.build_bundle("q", limit=10, top=8, sources_spec="openalex")
        check("bundle retracted: top 8 > 비철회 3편 → counts.top=3(철회 행으로 슬롯 안 채움)",
              b5["counts"]["top"] == 3 and b5["counts"]["abstracts"] == 3 and not any("10.9/bad" in u for u in calls),
              str(b5["counts"]))

        # (3) --include-retracted: 기존 동작(정렬 위치 P1·head 슬롯 소모·초록 조회)
        calls.clear()
        with contextlib.redirect_stderr(io.StringIO()):
            bi = m.build_bundle("q", limit=10, top=2, sources_spec="openalex", include_retracted=True)
        byi = {x["doi"]: x for x in bi["papers"]}
        check("bundle --include-retracted: 철회 행 랭킹 위치(P1)·head 슬롯 유지·초록 조회(기존 동작)",
              [x["doi"] for x in bi["papers"]] == ["10.9/bad", "10.9/ok1", "10.9/ok2", "10.9/ok3"]
              and byi["10.9/bad"]["pid"] == "P1" and byi["10.9/bad"]["abstract"] == "Abs bad"
              and byi["10.9/bad"].get("is_retracted") is True and bi["counts"]["top"] == 2
              and "상위 2편 밖" in (byi["10.9/ok2"].get("abstract_reason") or ""),
              str([(x["pid"], x["doi"], x.get("abstract"), x.get("abstract_reason")) for x in bi["papers"]]))

        # (4) cmd_search 렌더: 철회 행이 마지막 P#로 ⚠️ 표시 / --include-retracted면 P1
        o = io.StringIO()
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(io.StringIO()):
            m.cmd_search(types.SimpleNamespace(query="q", limit=10, sources="openalex", year_from=None, oa_only=False,
                                               json=False, include_retracted=False))
        t = o.getvalue()
        check("search retracted: 철회 행이 [P4]로 맨 뒤·⚠️RETRACTED 표시 유지",
              "[P4] (2020) Paper 10.9/bad ⚠️RETRACTED" in t and "[P1] (2020) Paper 10.9/ok1" in t, t)
        o = io.StringIO()
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(io.StringIO()):
            m.cmd_search(types.SimpleNamespace(query="q", limit=10, sources="openalex", year_from=None, oa_only=False,
                                               json=False, include_retracted=True))
        check("search --include-retracted: 철회 행 [P1] 유지(기존 동작)", "[P1] (2020) Paper 10.9/bad ⚠️RETRACTED" in o.getvalue(), o.getvalue())
        # SimpleNamespace에 include_retracted가 없어도(구 호출자) 기본 False로 동작
        o = io.StringIO()
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(io.StringIO()):
            m.cmd_search(types.SimpleNamespace(query="q", limit=10, sources="openalex", year_from=None, oa_only=False, json=False))
        check("search retracted: 인자 객체에 include_retracted 없으면 기본(맨 뒤)", "[P4] (2020) Paper 10.9/bad" in o.getvalue(), o.getvalue())

        # (5) 파서: search·bundle 둘 다 --include-retracted 수용, 기본 False
        captured = {}
        _os, _ob, _argv = m.cmd_search, m.cmd_bundle, sys.argv
        m.cmd_search = m.cmd_bundle = lambda a: captured.update(vars(a))
        try:
            sys.argv = ["scholar.py", "search", "q"]; m.main(); d1 = captured.get("include_retracted"); captured.clear()
            sys.argv = ["scholar.py", "search", "q", "--include-retracted"]; m.main(); d2 = captured.get("include_retracted"); captured.clear()
            sys.argv = ["scholar.py", "bundle", "q"]; m.main(); d3 = captured.get("include_retracted"); captured.clear()
            sys.argv = ["scholar.py", "bundle", "q", "--include-retracted"]; m.main(); d4 = captured.get("include_retracted")
        finally:
            m.cmd_search, m.cmd_bundle, sys.argv = _os, _ob, _argv
        check("parser: --include-retracted (search·bundle) 기본 False·지정 시 True",
              (d1, d2, d3, d4) == (False, True, False, True), str((d1, d2, d3, d4)))
    finally:
        m.search_openalex, m.search_crossref, m.search_s2, m.search_arxiv = orig_fns
        m.http_get = orig_http
_retracted_slot_tests()


# ---- recent-evidence-recall: bundle --recent-slots N — 상위 K 초록 슬롯에 최근 5년 논문 최소 N편 예약(round5) ----
def _recent_slot_tests():
    import re
    import threading
    import types
    NOW = 2026
    CUT = NOW - 5  # 2021
    _p = lambda doi, cit, year, **kw: dict({"source": "openalex", "doi": doi, "title": "Paper " + doi, "year": year,
                                            "citations": cit, "is_oa": True, "authors": ["A"]}, **kw)
    # 랭킹(rank_key 실측): old1(5000) > old2(3000) > old3(2000) > old4(1500) > new1(2025, 30)=K+1 > new2(2024, 2)=K+2 > old5
    # (new2는 인용 2편이지만 최신성 보너스 2.25로 old5(2010, 10편)를 넘는다)
    # 인용 log 축이 구논문 쪽으로 기울어 최근 실증 2편이 전부 K=4 밖으로 밀리는 round4 재현
    POOL = [_p("10.7/old1", 5000, 2005), _p("10.7/old2", 3000, 2008), _p("10.7/old3", 2000, 2012),
            _p("10.7/old4", 1500, 2014), _p("10.7/new1", 30, 2025), _p("10.7/old5", 10, 2010), _p("10.7/new2", 2, 2024)]
    orig_fns = (m.search_openalex, m.search_crossref, m.search_s2, m.search_arxiv)
    pool_holder = [POOL]
    def _oa(q, n, yf=None, oa=False):
        return [dict(x) for x in pool_holder[0]], None
    m.search_openalex = _oa
    calls = []
    lock = threading.Lock()
    def _http(url, **kw):
        with lock:
            calls.append(url)
        mm = re.search(r"openalex\.org/works/doi:10\.7/(\w+)", url)
        if mm and "select=" not in url:
            d = mm.group(1)
            return json.dumps({"doi": f"https://doi.org/10.7/{d}", "display_name": f"Paper 10.7/{d}",
                               "publication_year": 2020, "cited_by_count": 1, "authorships": [],
                               "abstract_inverted_index": {"Abs": [0], d: [1]}}), 200
        if "select=" in url:
            return json.dumps({"is_retracted": False}), 200
        return None, 404
    orig_http = m.http_get
    m.http_get = _http
    try:
        # (0) 순수 예약 함수
        cut = m._recent_cut(NOW)
        check("recent-slot: _recent_cut(2026)=2021·_is_recent 경계(2021 O·2020 X·None X·'abc' X)",
              cut == 2021 and m._is_recent({"year": 2021}, cut) and not m._is_recent({"year": 2020}, cut)
              and not m._is_recent({"year": None}, cut) and not m._is_recent({"year": "abc"}, cut), str(cut))
        elig = [dict(x) for x in POOL]
        h, prom = m._reserve_recent(elig, 4, 2, cut)
        check("recent-slot: _reserve_recent K=4·N=2 → head=old1·old2·new1·new2(뒤쪽 비최근 old3·old4 탈락)·승격 2",
              [x["doi"] for x in h] == ["10.7/old1", "10.7/old2", "10.7/new1", "10.7/new2"] and len(prom) == 2,
              str([x["doi"] for x in h]))
        h0, prom0 = m._reserve_recent(elig, 4, 0, cut)
        check("recent-slot: _reserve_recent N=0 → 기존 head 그대로(old1..old4)·승격 0",
              [x["doi"] for x in h0] == ["10.7/old1", "10.7/old2", "10.7/old3", "10.7/old4"] and not prom0, str([x["doi"] for x in h0]))
        h9, prom9 = m._reserve_recent(elig, 4, 9, cut)
        check("recent-slot: N > K여도 K로 상한(최근 2편뿐이라 승격 2·head 4)",
              len(h9) == 4 and len(prom9) == 2, str([x["doi"] for x in h9]))
        h_t0, _ = m._reserve_recent(elig, 0, 2, cut)
        check("recent-slot: top=0 → head 빈 리스트(예약 무시)", h_t0 == [], str(h_t0))

        # (1) 최근 논문이 K+2위(new1)·K+4위(new2) → --recent-slots 2(기본)로 head에 들어와 초록 조회
        with contextlib.redirect_stderr(io.StringIO()):
            b = m.build_bundle("q", limit=10, top=4, sources_spec="openalex", now_year=NOW)
        by = {x["doi"]: x for x in b["papers"]}
        check("recent-slot: 기본(recent_slots=2) → new1·new2가 head에 들어와 초록 채움·old3·old4 탈락",
              by["10.7/new1"]["abstract"] == "Abs new1" and by["10.7/new2"]["abstract"] == "Abs new2"
              and by["10.7/old1"]["abstract"] == "Abs old1" and by["10.7/old2"]["abstract"] == "Abs old2"
              and by["10.7/old3"]["abstract"] == "" and by["10.7/old4"]["abstract"] == "",
              str([(x["pid"], x["doi"], bool(x.get("abstract"))) for x in b["papers"]]))
        check("recent-slot: counts.top=4·abstracts=4·recent=2(스키마 추가)",
              b["counts"]["top"] == 4 and b["counts"]["abstracts"] == 4 and b["counts"]["recent"] == 2, str(b["counts"]))
        check("recent-slot: 승격 행은 P# 순서상 head 뒤(P3·P4)로 올라오고 recent_reserved=True·나머지 상대순서 유지",
              [x["doi"] for x in b["papers"]] == ["10.7/old1", "10.7/old2", "10.7/new1", "10.7/new2",
                                                  "10.7/old3", "10.7/old4", "10.7/old5"]
              and by["10.7/new1"].get("recent_reserved") is True and by["10.7/new2"].get("recent_reserved") is True
              and "recent_reserved" not in by["10.7/old1"] and [x["pid"] for x in b["papers"]] == [f"P{i}" for i in range(1, 8)],
              str([(x["pid"], x["doi"], x.get("recent_reserved")) for x in b["papers"]]))
        check("recent-slot: 탈락 행(old3)은 초록 미조회(HTTP 호출 없음)·'상위 4편 밖' 사유",
              not any("10.7/old3" in u for u in calls) and "상위 4편 밖" in (by["10.7/old3"].get("abstract_reason") or ""),
              str(by["10.7/old3"].get("abstract_reason")))
        txt = m.render_bundle(b)
        check("recent-slot: 요약 표에 '최근5년 2/4'·★recent-slot 표시",
              "최근5년 2/4" in txt and "[P3] (2025) Paper 10.7/new1 ★recent-slot" in txt, txt)

        # (2) --recent-slots 0 → 기존 동작(old1..old4 head·순서 불변·recent_reserved 없음)
        calls.clear()
        with contextlib.redirect_stderr(io.StringIO()):
            b0 = m.build_bundle("q", limit=10, top=4, sources_spec="openalex", recent_slots=0, now_year=NOW)
        check("recent-slot: recent_slots=0 → 기존 동작(head=old1..old4·P# 랭킹 순 그대로·counts.recent=0)",
              [x["doi"] for x in b0["papers"]] == ["10.7/old1", "10.7/old2", "10.7/old3", "10.7/old4",
                                                   "10.7/new1", "10.7/new2", "10.7/old5"]
              and all(bool(x.get("abstract")) == (x["doi"] in ("10.7/old1", "10.7/old2", "10.7/old3", "10.7/old4")) for x in b0["papers"])
              and b0["counts"]["recent"] == 0 and not any("recent_reserved" in x for x in b0["papers"]),
              str([(x["pid"], x["doi"], bool(x.get("abstract"))) for x in b0["papers"]]) + str(b0["counts"]))

        # (3) 최근 논문 0편 → 기존 head 그대로(예약 대상 없음)
        pool_holder[0] = [x for x in POOL if not x["doi"].startswith("10.7/new")]
        calls.clear()
        with contextlib.redirect_stderr(io.StringIO()):
            b3 = m.build_bundle("q", limit=10, top=4, sources_spec="openalex", now_year=NOW)
        check("recent-slot: 최근 논문 0편 → head=old1..old4 그대로·counts.recent=0·승격 없음",
              [x["doi"] for x in b3["papers"]] == ["10.7/old1", "10.7/old2", "10.7/old3", "10.7/old4", "10.7/old5"]
              and b3["counts"]["top"] == 4 and b3["counts"]["abstracts"] == 4 and b3["counts"]["recent"] == 0
              and not any("recent_reserved" in x for x in b3["papers"]),
              str([(x["pid"], x["doi"], bool(x.get("abstract"))) for x in b3["papers"]]) + str(b3["counts"]))

        # (4) head에 이미 최근 논문이 N편 이상이면 예약 없음(순서 불변)
        pool_holder[0] = [_p("10.7/r1", 900, 2024), _p("10.7/r2", 800, 2023), _p("10.7/o1", 700, 2001),
                          _p("10.7/o2", 600, 2002), _p("10.7/r3", 5, 2025)]
        with contextlib.redirect_stderr(io.StringIO()):
            b4 = m.build_bundle("q", limit=10, top=4, sources_spec="openalex", now_year=NOW)
        check("recent-slot: head에 이미 최근 2편(r1·r2) → r3 승격 없음·순서 불변·counts.recent=2",
              [x["doi"] for x in b4["papers"]] == ["10.7/r1", "10.7/r2", "10.7/o1", "10.7/o2", "10.7/r3"]
              and b4["counts"]["recent"] == 2 and not any("recent_reserved" in x for x in b4["papers"]),
              str([x["doi"] for x in b4["papers"]]) + str(b4["counts"]))

        # (5) 철회 행은 최근이어도 예약 대상 아님(기본) — 비철회 최근 행만 끌어올림
        pool_holder[0] = [_p("10.7/o1", 900, 2001), _p("10.7/o2", 800, 2002), _p("10.7/o3", 700, 2003),
                          _p("10.7/o4", 600, 2004), _p("10.7/rbad", 500, 2025, is_retracted=True), _p("10.7/rok", 5, 2024)]
        with contextlib.redirect_stderr(io.StringIO()):
            b5 = m.build_bundle("q", limit=10, top=4, sources_spec="openalex", now_year=NOW)
        by5 = {x["doi"]: x for x in b5["papers"]}
        check("recent-slot: 철회된 최근 행(rbad)은 예약 안 됨·비철회 최근 행(rok)만 승격·철회 행은 여전히 맨 뒤",
              by5["10.7/rok"].get("recent_reserved") is True and by5["10.7/rok"]["abstract"] == "Abs rok"
              and by5["10.7/rbad"]["abstract"] == "" and b5["papers"][-1]["doi"] == "10.7/rbad"
              and b5["counts"]["recent"] == 1 and b5["counts"]["top"] == 4,
              str([(x["doi"], x.get("recent_reserved"), bool(x.get("abstract"))) for x in b5["papers"]]))

        # (6) 실제 시계 기준(now_year 미지정)도 동작: 올해-1 논문은 최근
        this_year = m.time.localtime().tm_year
        pool_holder[0] = [_p("10.7/a1", 900, 1999), _p("10.7/a2", 800, 1998), _p("10.7/a3", 1, this_year - 1)]
        with contextlib.redirect_stderr(io.StringIO()):
            b6 = m.build_bundle("q", limit=10, top=2, sources_spec="openalex")
        check("recent-slot: now_year 미지정 → 시스템 연도 기준(올해-1 논문 승격)",
              b6["papers"][1]["doi"] == "10.7/a3" and b6["papers"][1].get("recent_reserved") is True and b6["counts"]["recent"] == 1,
              str([x["doi"] for x in b6["papers"]]))

        # (7) 파서: --recent-slots 기본 2·지정값 전달
        captured = {}
        _orig_cb, _orig_argv = m.cmd_bundle, sys.argv
        m.cmd_bundle = lambda a: captured.update(vars(a))
        try:
            sys.argv = ["scholar.py", "bundle", "q"]; m.main()
            d0 = captured.get("recent_slots")
            captured.clear()
            sys.argv = ["scholar.py", "bundle", "q", "--recent-slots", "0"]; m.main()
            d1 = captured.get("recent_slots")
        finally:
            m.cmd_bundle, sys.argv = _orig_cb, _orig_argv
        check("recent-slot: 파서 --recent-slots 기본 2·--recent-slots 0 전달", d0 == 2 and d1 == 0, f"{d0} {d1}")
        # (8) cmd_bundle이 recent_slots를 build_bundle에 넘김
        seen = {}
        _orig_bb = m.build_bundle
        m.build_bundle = lambda *a, **k: seen.update(k) or {"papers": [{"pid": "P1", "doi": "x", "title": "t"}], "counts": {"per_source": {}, "merged": 1, "top": 0, "abstracts": 0, "recent": 0},
                                                            "elapsed_sec": {"search": 0, "abstracts": 0, "total": 0}, "errors": [], "empty_sources": [], "sources_used": [], "query": "q"}
        try:
            with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()):
                m.cmd_bundle(types.SimpleNamespace(query="q", limit=10, top=8, sources="openalex", year_from=None, oa_only=False,
                                                   include_retracted=False, recent_slots=3, out=os.path.join(td, "r.json"), json=False))
        finally:
            m.build_bundle = _orig_bb
        check("recent-slot: cmd_bundle → build_bundle(recent_slots=3) 전달", seen.get("recent_slots") == 3, str(seen))
    finally:
        m.http_get = orig_http
        m.search_openalex, m.search_crossref, m.search_s2, m.search_arxiv = orig_fns
_recent_slot_tests()

# ---- cache: sqlite 로컬 캐시 — 히트/미스/TTL 만료/철회 미캐시/비활성 ----
def _cache_tests():
    import sqlite3
    orig_http, orig_off = m.http_get, m._CACHE_OFF
    calls = []
    state = {"retracted": False}
    def _http(url, **kw):
        calls.append(url)
        if "openalex.org/works/doi:10.1/cache" in url:
            if "select=is_retracted" in url:
                return json.dumps({"is_retracted": state["retracted"]}), 200
            return json.dumps({"doi": "https://doi.org/10.1/cache", "display_name": "Cached Paper",
                               "publication_year": 2020, "cited_by_count": 3, "is_retracted": state["retracted"],
                               "authorships": [{"author": {"display_name": "Ann Lee"}}],
                               "abstract_inverted_index": {"Hello": [0], "world": [1]},
                               "referenced_works": ["W1", "W2"]}), 200
        if "openalex.org/works/doi:10.1/crab" in url:
            if "select=" in url:
                return json.dumps({"is_retracted": False}), 200
            return json.dumps({"doi": "https://doi.org/10.1/crab", "display_name": "CR", "publication_year": 2020,
                               "authorships": [], "abstract_inverted_index": None}), 200
        if "crossref.org/works/10.1/crab" in url:
            return json.dumps({"message": {"abstract": "<jats:p>CR abs</jats:p>"}}), 200
        return None, 404
    def _rows(db, kind=None):
        con = sqlite3.connect(db)
        q = "SELECT kind, key, value FROM entries" + (f" WHERE kind='{kind}'" if kind else "")
        rows = con.execute(q).fetchall(); con.close()
        return rows
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "sub", "c.db")  # 하위 폴더 자동 생성 확인
        os.environ["SCHOLAR_CACHE"] = db
        os.environ.pop("SCHOLAR_NO_CACHE", None)
        m._CACHE_OFF = False
        m.http_get = _http
        try:
            check("cache: 경로 env·기본값", m._cache_path() == db and m.cache_enabled())
            # 1) 미스
            meta, err = m._fetch_doi_meta("10.1/cache")
            n_full = sum(1 for u in calls if "10.1/cache" in u and "select=" not in u)
            check("cache: 미스 → 단건 전체 조회 1회 + DB 행 생성(oa_work)",
                  bool(meta) and meta.get("cache") is False and n_full == 1 and len(_rows(db, "oa_work")) == 1,
                  f"{meta} {calls}")
            # 2) 히트 — 전체 조회 없이 select=is_retracted 실 조회만
            calls.clear()
            meta2, _ = m._fetch_doi_meta("10.1/cache")
            check("cache: 히트 → 전체 조회 0회·select=is_retracted 실 조회 1회·cache=True·필드 동일",
                  meta2.get("cache") is True and len(calls) == 1 and "select=is_retracted" in calls[0]
                  and meta2["title"] == meta["title"] and meta2["abstract"] == "Hello world"
                  and meta2["authors"] == ["Ann Lee"] and meta2["is_retracted"] is False, f"{meta2} {calls}")
            # 3) 철회 미캐시 — 실 조회가 철회로 바뀌면 히트에서도 즉시 반영, DB에는 철회 키 없음
            state["retracted"] = True; calls.clear()
            meta3, _ = m._fetch_doi_meta("10.1/cache")
            vals = [v for _k, _kk, v in _rows(db)]
            check("cache: 철회 미캐시 — 히트여도 실 조회값(철회) 반영 + DB 값에 is_retracted 부재",
                  meta3["is_retracted"] is True and meta3.get("cache") is True
                  and all("retracted" not in v for v in vals), f"{meta3} {vals}")
            m.http_get = lambda url, **kw: (None, 503)
            meta4, _ = m._fetch_doi_meta("10.1/cache")
            check("cache: 히트인데 철회 실 조회 실패 → is_retracted None(False로 위장 X)",
                  meta4 is not None and meta4["is_retracted"] is None and meta4.get("cache") is True, str(meta4))
            m.http_get = _http; state["retracted"] = False
            # cache_put 자체가 철회 키를 걷어낸다(구조적 보장)
            m.cache_put("oa_work", "10.1/forbidden", {"title": "t", "is_retracted": True, "retracted": True,
                                                       "retraction_checked": True, "cache": True})
            got = m.cache_get("oa_work", "10.1/forbidden")
            check("cache: cache_put이 철회 키(is_retracted·retracted·retraction_checked)·cache 키를 저장하지 않음",
                  got == {"title": "t"}, str(got))
            # 4) TTL 만료(30일) → 재조회·ts 갱신
            con = sqlite3.connect(db); con.execute("UPDATE entries SET ts = ts - ?", (31 * 86400,)); con.commit(); con.close()
            calls.clear()
            meta5, _ = m._fetch_doi_meta("10.1/cache")
            ts = [r for r in sqlite3.connect(db).execute("SELECT ts FROM entries WHERE key='10.1/cache'")][0][0]
            check("cache: 30일 경과 → 만료 → 전체 재조회(cache=False)·ts 갱신",
                  meta5.get("cache") is False and any("select=" not in u for u in calls) and m.time.time() - ts < 60,
                  f"{meta5} {calls}")
            con = sqlite3.connect(db); con.execute("UPDATE entries SET ts = ts - ?", (29 * 86400,)); con.commit(); con.close()
            calls.clear(); meta5b, _ = m._fetch_doi_meta("10.1/cache")
            check("cache: 29일 경과 → 아직 히트", meta5b.get("cache") is True and len(calls) == 1, str(calls))
            # paper 출력 "(cache)"
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                m.cmd_paper(argparse.Namespace(id="10.1/cache", json=False))
            check("paper: 캐시 히트 시 '(cache)' 표시 + 제목·초록 출력",
                  "(cache)" in buf.getvalue() and "Cached Paper (2020)" in buf.getvalue() and "Hello world" in buf.getvalue(), buf.getvalue())
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                m.cmd_paper(argparse.Namespace(id="10.1/cache", json=True))
            j = json.loads(buf.getvalue())
            check("paper --json: cache=true·referenced_works_count 보존·is_retracted 실 조회값",
                  j.get("cache") is True and j.get("referenced_works_count") == 2 and j.get("is_retracted") is False, str(j))
            # 5) bundle 초록 — abstract_source에 (cache) 표기, Crossref 폴백 초록도 캐시
            ab, src, reason, mt = m.fetch_abstract({"doi": "10.1/cache"})
            check("bundle 초록: 캐시 히트 → abstract_source='openalex (cache)'", ab == "Hello world" and src == "openalex (cache)", f"{ab} {src}")
            calls.clear()
            r1 = m.fetch_abstract({"doi": "10.1/crab"}); r2 = m.fetch_abstract({"doi": "10.1/crab"})
            check("bundle 초록: Crossref 폴백 초록 캐시(1회차 crossref, 2회차 'crossref (cache)'·crossref 호출 1회)",
                  r1[:2] == ("CR abs", "crossref") and r2[:2] == ("CR abs", "crossref (cache)")
                  and sum(1 for u in calls if "crossref" in u) == 1, f"{r1[:2]} {r2[:2]} {calls}")
            # 6) verify — PMID만 캐시, efetch(철회 판정)는 매번 실 조회
            m._PUBMED_RETRACTION_CACHE.clear()
            m.http_get = _mock_sources(_CR_PLAIN, _OA_PLAIN, _PM_CLEAN)
            v1 = m.verify_one("10.1/pm-cache", "A perfectly ordinary paper title", "Kumar", 2012)
            m._PUBMED_RETRACTION_CACHE.clear(); calls.clear()
            def _pm_trace(url, **kw):
                calls.append(url)
                return _mock_sources(_CR_PLAIN, _OA_PLAIN, _PM_CLEAN)(url)
            m.http_get = _pm_trace
            v2 = m.verify_one("10.1/pm-cache", "A perfectly ordinary paper title", "Kumar", 2012)
            check("verify: 2회차 PMID 캐시 히트 → esearch 0회·efetch 1회·crossref/openalex 실 조회·cache=['pmid']·verdict 동일",
                  v1["verdict"] == "MATCH" and v2["verdict"] == "MATCH" and v1.get("cache") is None and v2.get("cache") == ["pmid"]
                  and not any("esearch" in u for u in calls) and sum(1 for u in calls if "efetch" in u) == 1
                  and any("crossref" in u for u in calls) and any("openalex" in u for u in calls)
                  and v2.get("retraction_sources") == ["crossref", "openalex", "pubmed"], f"{v2} {calls}")
            m._PUBMED_RETRACTION_CACHE.clear()
            m.http_get = _mock_sources(_CR_PLAIN, _OA_PLAIN, _PM_RETRACTED)
            v3 = m.verify_one("10.1/pm-cache", "A perfectly ordinary paper title", "Kumar", 2012)
            check("verify: PMID 캐시 히트 상태에서 PubMed가 철회로 바뀌면 즉시 RETRACTED(철회 판정 미캐시)",
                  v3["verdict"] == "RETRACTED" and v3.get("cache") == ["pmid"], str(v3))
            # PubMed 미색인은 캐시하지 않는다(나중에 색인된 철회를 놓치지 않게)
            m._PUBMED_RETRACTION_CACHE.clear()
            m.http_get = _mock_sources(_CR_PLAIN, _OA_PLAIN, None)
            m.verify_one("10.1/pm-none", "A perfectly ordinary paper title", "Kumar", 2012)
            check("verify: PubMed 미색인 DOI는 pmid 캐시 행 없음", not [r for r in _rows(db, "pmid") if r[1] == "10.1/pm-none"])
            # 7) verify — DataCite 전용 DOI 서지 캐시(Crossref 404·DataCite 재조회 생략, OpenAlex는 실 조회)
            m._PUBMED_RETRACTION_CACHE.clear()
            m.http_get = _mock_sources(None, None, None, _DC_ARXIV)
            d1 = m.verify_one("10.48550/arxiv.2501.12948", "DeepSeek-R1: Incentivizing Reasoning Capability in LLMs", "DeepSeek-AI", 2025)
            m._PUBMED_RETRACTION_CACHE.clear(); calls.clear()
            def _dc_trace(url, **kw):
                calls.append(url)
                return _mock_sources(None, None, None, _DC_ARXIV)(url)
            m.http_get = _dc_trace
            d2 = m.verify_one("10.48550/arxiv.2501.12948", "DeepSeek-R1: Incentivizing Reasoning Capability in LLMs", "DeepSeek-AI", 2025)
            check("verify: DataCite 전용 DOI 2회차 → crossref·datacite 호출 0회·openalex 실 조회·verdict 동일·cache=['datacite']",
                  d1["verdict"] == d2["verdict"] == "RETRACTION_NA" and d2.get("cache") == ["datacite"]
                  and not any("crossref" in u or "datacite" in u for u in calls) and any("openalex" in u for u in calls)
                  and d2.get("actual") == d1.get("actual") and d2.get("author_ok") is True, f"{d2} {calls}")
            vals = [v for _k, _kk, v in _rows(db)]
            check("cache: 전체 DB 어느 행에도 철회 관련 값 없음(retract 문자열 0건)",
                  bool(vals) and all("retract" not in v.lower() for v in vals), str(vals)[:300])
            # verify-batch 요약줄에 (cache: …) 표기
            m._PUBMED_RETRACTION_CACHE.clear()
            m.http_get = _mock_sources(_CR_PLAIN, _OA_PLAIN, _PM_CLEAN)
            out, code = _run_batch([{"id": "P1", "doi": "10.1/pm-cache", "title": "A perfectly ordinary paper title",
                                     "author": "Kumar", "year": 2012}])
            check("verify-batch: 캐시 히트 행에 '(cache: pmid)' 표기 + exit 0 유지", code == 0 and "(cache: pmid)" in out, out)
            # 8) 비활성 — SCHOLAR_NO_CACHE=1 / --no-cache 플래그
            m.http_get = _http
            os.environ["SCHOLAR_NO_CACHE"] = "1"; calls.clear()
            meta6, _ = m._fetch_doi_meta("10.1/cache")
            check("cache: SCHOLAR_NO_CACHE=1 → 히트 없이 전체 조회(cache=False)",
                  not m.cache_enabled() and meta6.get("cache") is False and any("select=" not in u for u in calls), str(calls))
            os.environ.pop("SCHOLAR_NO_CACHE", None)
            captured = {}
            _orig_fns = (m.cmd_paper, m.cmd_verify, m.cmd_verify_batch, m.cmd_bundle, m.cmd_project_add, m.cmd_project_recount)
            m.cmd_paper = m.cmd_verify = m.cmd_verify_batch = m.cmd_bundle = m.cmd_project_add = m.cmd_project_recount = \
                (lambda a: captured.update(vars(a)))
            _orig_argv = sys.argv
            try:
                ok_flag = True
                for argv in (["paper", "--no-cache", "10.1/x"], ["verify", "--no-cache", "10.1/x", "t"],
                             ["verify-batch", "--no-cache", "r.json"], ["bundle", "--no-cache", "q"],
                             ["project", "add", "--no-cache", "slug"], ["project", "recount", "--no-cache", "slug"]):
                    m._CACHE_OFF = False; captured.clear()
                    sys.argv = ["scholar.py"] + argv; m.main()
                    ok_flag = ok_flag and captured.get("no_cache") is True and m._CACHE_OFF is True and not m.cache_enabled()
                check("cache: --no-cache 플래그(paper·verify·verify-batch·bundle·project add/recount) → 캐시 비활성", ok_flag, str(captured))
                m._CACHE_OFF = False; captured.clear()
                sys.argv = ["scholar.py", "paper", "10.1/x"]; m.main()
                check("cache: 플래그 없으면 기본 활성", captured.get("no_cache") is False and m.cache_enabled())
            finally:
                sys.argv = _orig_argv
                (m.cmd_paper, m.cmd_verify, m.cmd_verify_batch, m.cmd_bundle, m.cmd_project_add, m.cmd_project_recount) = _orig_fns
            m._CACHE_OFF = True; calls.clear()
            meta7, _ = m._fetch_doi_meta("10.1/cache")
            check("cache: _CACHE_OFF(--no-cache) → 전체 조회(cache=False)", meta7.get("cache") is False and any("select=" not in u for u in calls))
            # 9) DB 손상·경로 불가는 조용히 미스로 처리(기능 저하 없이 실 조회)
            m._CACHE_OFF = False
            os.environ["SCHOLAR_CACHE"] = os.path.join(td, "nodir", "x.db")
            open(os.path.join(td, "nodir"), "w").close()  # 파일이라 폴더 생성 불가
            calls.clear(); meta8, _ = m._fetch_doi_meta("10.1/cache")
            check("cache: DB 열기 실패 → 예외 없이 실 조회(cache=False)", meta8 is not None and meta8.get("cache") is False, str(meta8))
        finally:
            m.http_get = orig_http
            m._CACHE_OFF = orig_off
            m._PUBMED_RETRACTION_CACHE.clear()
            os.environ["SCHOLAR_NO_CACHE"] = "1"
            os.environ.pop("SCHOLAR_CACHE", None)
def _r5_tests():
    """레드팀 R5 회귀: Elsevier 자기참조 update-to · arXiv DOI 제목 오염 · verify-batch 파일 오류 ·
    snowball 클램프/청크 · openalex type 필터 · paper arXiv 재확인."""
    orig_http, orig_sleep = m.http_get, m.time.sleep
    m.time.sleep = lambda *a: None
    try:
        # --- Elsevier식 철회 원논문: update-to가 자기 DOI → 통지문이 아니라 RETRACTED ---
        _CR_ELS = json.dumps({"message": {
            "title": ["RETRACTED: Using CRITIC-TOPSIS for supplier selection"],
            "type": "journal-article", "issued": {"date-parts": [[2024]]},
            "author": [{"family": "Zhang"}],
            "update-to": [{"DOI": "10.1016/j.heliyon.2024.e31484", "type": "retraction"}],
            "updated-by": [{"DOI": "10.1016/j.heliyon.2024.e99999", "type": "retraction"}]}})
        _OA_ELS = json.dumps({"display_name": "RETRACTED: Using CRITIC-TOPSIS for supplier selection",
                              "publication_year": 2024, "is_retracted": False,
                              "authorships": [{"author": {"display_name": "Wei Zhang"}}]})
        m._PUBMED_RETRACTION_CACHE.clear()
        m.http_get = _mock_sources(_CR_ELS, _OA_ELS, _PM_CLEAN)
        r = m.verify_one("10.1016/j.heliyon.2024.e31484",
                         "Using CRITIC-TOPSIS for supplier selection", "Zhang", 2024)
        check("verify: Elsevier 자기참조 update-to → RETRACTED(NON_ARTICLE 아님)",
              r.get("verdict") == "RETRACTED", str(r))
        check("verify: 자기참조 update-to는 통지문 플래그를 세우지 않는다",
              r.get("is_retraction_notice") is False, str(r))
        # 자기참조만 있고 updated-by·제목 접두가 없어도 원논문 철회로 본다
        _CR_ELS2 = json.dumps({"message": {
            "title": ["Using CRITIC-TOPSIS for supplier selection"],
            "type": "journal-article", "issued": {"date-parts": [[2024]]},
            "author": [{"family": "Zhang"}],
            "update-to": [{"DOI": "https://doi.org/10.1016/J.HELIYON.2024.E31484", "type": "Retraction"}]}})
        m._PUBMED_RETRACTION_CACHE.clear()
        m.http_get = _mock_sources(_CR_ELS2, _OA_ELS, _PM_CLEAN)
        r = m.verify_one("10.1016/j.heliyon.2024.e31484",
                         "Using CRITIC-TOPSIS for supplier selection", "Zhang", 2024)
        check("verify: 자기참조 update-to(URL·대문자 표기)만으로도 RETRACTED",
              r.get("verdict") == "RETRACTED", str(r))
        # 대조군: 타 DOI를 가리키는 update-to = 진짜 통지문 → NON_ARTICLE 유지
        _CR_NOTICE = json.dumps({"message": {
            "title": ["Retraction notice to: Using CRITIC-TOPSIS for supplier selection"],
            "type": "journal-article", "issued": {"date-parts": [[2024]]},
            "author": [{"family": "Zhang"}],
            "update-to": [{"DOI": "10.1016/j.heliyon.2024.e31484", "type": "retraction"}]}})
        m._PUBMED_RETRACTION_CACHE.clear()
        m.http_get = _mock_sources(_CR_NOTICE, None, _PM_CLEAN)
        r = m.verify_one("10.1016/j.heliyon.2024.e99999",
                         "Using CRITIC-TOPSIS for supplier selection", "Zhang", 2024)
        check("verify: 타 DOI를 가리키는 update-to는 여전히 통지문(NON_ARTICLE)",
              r.get("verdict") == "NON_ARTICLE" and r.get("is_retraction_notice") is True, str(r))
        # 제목 'RETRACTED:' 원논문이 통지문 제목 패턴과 겹쳐도 증거가 이긴다
        _CR_TITLE = json.dumps({"message": {
            "title": ["RETRACTED: Something"], "subtitle": ["Withdrawal of the claims"],
            "type": "journal-article", "issued": {"date-parts": [[2020]]},
            "author": [{"family": "Kim"}],
            "update-to": [{"DOI": "10.1/other", "type": "retraction"}]}})
        m._PUBMED_RETRACTION_CACHE.clear()
        m.http_get = _mock_sources(_CR_TITLE, None, _PM_CLEAN)
        r = m.verify_one("10.1/self", "Something", "Kim", 2020)
        check("verify: 'RETRACTED:' 제목 증거는 통지문 추정보다 우선(RETRACTED)",
              r.get("verdict") == "RETRACTED", str(r))

        # --- arXiv DOI: OpenAlex 제목 오염 → DataCite 정본 제목으로 대조 ---
        _OA_POLL = json.dumps({"display_name": "BNAI, NO-TOKEN, and MIND-UNITY: a failure mode",
                               "publication_year": 2022, "is_retracted": False, "type": "preprint",
                               "authorships": [{"author": {"display_name": "Jason Wei"}}]})
        _DC_COT = json.dumps({"data": {"attributes": {
            "titles": [{"title": "Chain-of-Thought Prompting Elicits Reasoning in Large Language Models"}],
            "publicationYear": 2022, "creators": [{"familyName": "Wei"}]}}})
        m._PUBMED_RETRACTION_CACHE.clear()
        m.http_get = _mock_sources(None, _OA_POLL, None, _DC_COT)
        r = m.verify_one("10.48550/arxiv.2201.11903",
                         "Chain-of-Thought Prompting Elicits Reasoning in Large Language Models", "Wei", 2022)
        check("verify: arXiv DOI 정본 제목 인용 → MATCH(OpenAlex 오염 제목에 밀리지 않음)",
              r.get("verdict") == "MATCH", str(r))
        check("verify: arXiv DOI actual은 DataCite 정본 제목",
              (r.get("actual") or "").startswith("Chain-of-Thought"), str(r))
        m._PUBMED_RETRACTION_CACHE.clear()
        m.http_get = _mock_sources(None, _OA_POLL, None, _DC_COT)
        r = m.verify_one("10.48550/arxiv.2201.11903",
                         "BNAI, NO-TOKEN, and MIND-UNITY: a failure mode", "Wei", 2022)
        check("verify: arXiv DOI 오염 제목 인용 → MISMATCH(오염 레코드의 정직한 인용도 차단)",
              r.get("verdict") == "MISMATCH", str(r))
        # DataCite 실패 시 기존 동작(OpenAlex 제목)으로 폴백 — 열화 없음
        m._PUBMED_RETRACTION_CACHE.clear()
        m.http_get = _mock_sources(None, _OA_POLL, None, None)
        r = m.verify_one("10.48550/arxiv.2201.11903",
                         "BNAI, NO-TOKEN, and MIND-UNITY: a failure mode", "Wei", 2022)
        check("verify: arXiv DOI DataCite 실패 → OpenAlex 제목 폴백(MATCH)", r.get("verdict") == "MATCH", str(r))

        # --- verify-batch: 파일 부재·JSON 문법 오류 → exit 6(계약 안) ---
        for label, path in (("부재", os.path.join(tempfile.gettempdir(), "scholar_selftest_nonexistent.json")),
                            ("문법 오류", os.path.join(tempfile.gettempdir(), "scholar_selftest_bad.json"))):
            if label == "문법 오류":
                open(path, "w", encoding="utf-8").write('[{"id": "P1",}]')
            elif os.path.exists(path):
                os.remove(path)
            code, crashed, err = None, None, io.StringIO()
            try:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                    m.cmd_verify_batch(argparse.Namespace(file=path, json=False))
            except SystemExit as e:
                code = e.code
            except Exception as e:
                crashed = repr(e)
            check(f"verify-batch: refs.json {label} → 트레이스백 없이 exit 6",
                  crashed is None and code == 6, f"exit={code} crash={crashed}")
            check(f"verify-batch: refs.json {label} ERROR 사유 출력", "읽기/파싱 실패" in err.getvalue(), err.getvalue())

        # --- snowball: --limit > 200 클램프 + refs 100건 초과 청크 전수 조회 ---
        urls, errs = [], io.StringIO()
        def _sb_big(url, **kw):
            urls.append(url)
            if "select=id" in url:
                return (json.dumps({"id": "https://openalex.org/W123"}), 200)
            return (json.dumps({"results": []}), 200)
        m.http_get = _sb_big
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errs):
            m.cmd_snowball(argparse.Namespace(doi="10.1/x", direction="cites", limit=250,
                                              year_from=None, sort="citations"))
        check("snowball cites: --limit 250 → per-page 200 클램프(HTTP 400 전멸 방지)",
              any("per-page=200" in u for u in urls) and not any("per-page=250" in u for u in urls), str(urls))
        check("snowball: 클램프 시 stderr 안내", "상한 200" in errs.getvalue(), errs.getvalue())
        urls.clear()
        def _sb_refs_big(url, **kw):
            urls.append(url)
            if "referenced_works" in url:
                return (json.dumps({"referenced_works": [f"https://openalex.org/W{i}" for i in range(250)]}), 200)
            q = urllib_parse.parse_qs(urllib_parse.urlparse(url).query)
            ids = q["filter"][0].split("openalex_id:")[1].split(",")[0].split("|")
            # 뒤 청크일수록 인용수가 높은 문헌 — ID순 절단이면 놓치는 구조
            return (json.dumps({"results": [{"doi": None, "display_name": i, "publication_year": 2020,
                                              "cited_by_count": int(i[1:])} for i in ids]}), 200)
        m.http_get = _sb_refs_big
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            m.cmd_snowball(argparse.Namespace(doi="10.1/x", direction="refs", limit=5,
                                              year_from=None, sort="citations"))
        batch = [u for u in urls if "openalex_id" in u]
        lines = [l for l in out.getvalue().splitlines() if l.startswith("(")]
        check("snowball refs: 250건 → 3회 배치 조회(101건 이후 탈락 없음)", len(batch) == 3, str(len(batch)))
        check("snowball refs: 클라이언트 정렬 후 limit 적용(상위 5건, 최고 인용은 3번째 청크)",
              len(lines) == 5 and "인용 249" in lines[0], "\n".join(lines))
        check("snowball refs: 배치 per-page는 청크 크기(≤100)",
              all("per-page=100" in u or "per-page=50" in u for u in batch), str(batch))

        # --- openalex type 보존 + 비논문 필터 ---
        _OA_SEARCH = json.dumps({"results": [
            {"id": "W1", "doi": "https://doi.org/10.5860/choice.46-6301", "display_name": "Animal spirits",
             "publication_year": 2009, "cited_by_count": 1485, "type": "book-review", "authorships": []},
            {"id": "W2", "doi": "https://doi.org/10.1/real", "display_name": "Prospect theory revisited",
             "publication_year": 1996, "cited_by_count": 1019, "type": "article",
             "authorships": [{"author": {"display_name": "George Wu"}}]}]})
        m.http_get = lambda url, **kw: (_OA_SEARCH, 200)
        rows, err = m.search_openalex("prospect theory", 10)
        check("openalex: book-review 레코드는 검색단에서 제외", [r["doi"] for r in rows] == ["10.1/real"], str(rows))
        check("openalex: 행에 type 보존(병합단 필터가 작동하게)", rows and rows[0].get("type") == "article", str(rows))
        pool = [{"source": "openalex", "doi": "10.1/br", "title": "Some book review", "year": 2009,
                 "citations": 1485, "is_oa": False, "type": "paratext"},
                {"source": "openalex", "doi": "10.1/ok", "title": "A real paper", "year": 2010,
                 "citations": 10, "is_oa": False, "type": "article"}]
        merged = m.merge_and_rank(pool, 10)
        check("merge: openalex paratext 행은 병합단에서도 제외", [p["doi"] for p in merged] == ["10.1/ok"], str(merged))

        # --- paper: arXiv DOI 레코드 오염 → arXiv API 재확인 ---
        _OA_WORK_POLL = json.dumps({"doi": "https://doi.org/10.48550/arxiv.2201.11903",
                                    "display_name": "BNAI, NO-TOKEN, and MIND-UNITY: a failure mode",
                                    "publication_year": 2022, "is_retracted": False, "type": "preprint",
                                    "authorships": [], "abstract_inverted_index": {"There": [0], "is": [1]},
                                    "referenced_works": []})
        _ATOM_COT = ("<feed><entry><id>http://arxiv.org/abs/2201.11903v6</id>"
                     "<published>2022-01-28T00:00:00Z</published>"
                     "<title>Chain-of-Thought Prompting Elicits Reasoning in Large Language Models</title>"
                     "<name>Jason Wei</name></entry></feed>")
        def _paper_http(url, **kw):
            if "arxiv.org" in url:
                return (_ATOM_COT, 200)
            return (_OA_WORK_POLL, 200)
        m.http_get = _paper_http
        out, code = io.StringIO(), None
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                m.cmd_paper(argparse.Namespace(id="10.48550/arXiv.2201.11903", json=True))
        except SystemExit as e:
            code = e.code
        j = json.loads(out.getvalue() or "{}")
        check("paper: arXiv DOI 제목 오염 → exit 3(성공 위장 금지)", code == 3, f"exit={code}")
        check("paper: 오염 시 초록 미출력 + 정본 제목·경고", j.get("abstract") is None
              and (j.get("title") or "").startswith("Chain-of-Thought") and "제목 오염 의심" in (j.get("warning") or ""), str(j))
        # arXiv 실패 + DataCite 정본 제목 → DataCite로 재확인해 오염 판정(exit 3)
        m.http_get = lambda url, **kw: ((None, 429) if "arxiv.org" in url else
                                        ((_DC_COT, 200) if "datacite" in url else (_OA_WORK_POLL, 200)))
        out, code = io.StringIO(), None
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                m.cmd_paper(argparse.Namespace(id="10.48550/arXiv.2201.11903", json=True))
        except SystemExit as e:
            code = e.code
        j = json.loads(out.getvalue() or "{}")
        check("paper: arXiv 429여도 DataCite 정본 제목으로 오염 판정(exit 3·초록 미출력)",
              code == 3 and j.get("abstract") is None, f"exit={code} {j.get('warning')}")
        # 재확인 실패(arXiv·DataCite 모두) → 미확인 경고 + exit 5(fail-closed)
        m.http_get = lambda url, **kw: ((None, -1) if ("arxiv.org" in url or "datacite" in url) else (_OA_WORK_POLL, 200))
        out, code = io.StringIO(), None
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                m.cmd_paper(argparse.Namespace(id="10.48550/arXiv.2201.11903", json=True))
        except SystemExit as e:
            code = e.code
        j = json.loads(out.getvalue() or "{}")
        check("paper: arXiv 재확인 실패 → exit 5 + 미확인 경고", code == 5 and "재확인 실패" in (j.get("warning") or ""), f"exit={code} {j.get('warning')}")
        # 정상(제목 일치) → exit 0, 초록 유지
        _OA_WORK_OK = json.loads(_OA_WORK_POLL); _OA_WORK_OK["display_name"] = "Chain-of-Thought Prompting Elicits Reasoning in Large Language Models"
        m.http_get = lambda url, **kw: ((_ATOM_COT, 200) if "arxiv.org" in url else (json.dumps(_OA_WORK_OK), 200))
        out, code = io.StringIO(), None
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                m.cmd_paper(argparse.Namespace(id="10.48550/arXiv.2201.11903", json=True))
        except SystemExit as e:
            code = e.code
        j = json.loads(out.getvalue() or "{}")
        check("paper: arXiv DOI 제목 일치 → exit 0·초록 유지", code is None and j.get("abstract") == "There is", str(j))
        # 비-arXiv DOI는 재확인 없이 기존 동작
        m.http_get = lambda url, **kw: (json.dumps(_OA_WORK_OK), 200)
        out, code = io.StringIO(), None
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                m.cmd_paper(argparse.Namespace(id="10.1/plain", json=True))
        except SystemExit as e:
            code = e.code
        check("paper: 비-arXiv DOI는 재확인 없이 exit 0", code is None and "warning" not in json.loads(out.getvalue()), out.getvalue()[:200])
    finally:
        m.http_get = orig_http
        m.time.sleep = orig_sleep
        m._PUBMED_RETRACTION_CACHE.clear()
_r5_tests()

_cache_tests()


# ---- round5 s2-abstract-fallback: bundle 초록 체인 openalex → crossref → S2 단일논문 ----
def _s2_abstract_tests():
    import sqlite3
    orig_http, orig_sleep, orig_off = m.http_get, m.time.sleep, m._CACHE_OFF
    calls = []
    S2 = "api.semanticscholar.org/graph/v1/paper/DOI:"
    _OA = lambda doi, title: (json.dumps({"doi": "https://doi.org/" + doi, "display_name": title, "publication_year": 2020,
                                          "authorships": [], "abstract_inverted_index": None}), 200)
    _CR_EMPTY = (json.dumps({"message": {"title": ["x"]}}), 200)
    def _http(url, **kw):
        calls.append(url)
        if "select=is_retracted" in url:
            return json.dumps({"is_retracted": False}), 200
        if "openalex.org/works/doi:10.1016/j.irfa.2020.101646" in url:
            return _OA("10.1016/j.irfa.2020.101646", "Bitcoin and market efficiency")
        if "crossref.org/works/10.1016/j.irfa.2020.101646" in url:
            return _CR_EMPTY
        if S2 + "10.1016/j.irfa.2020.101646" in url:
            return json.dumps({"title": "Bitcoin and market efficiency",
                               "abstract": "  S2 abstract text of 1406 chars stand-in.  "}), 200
        if "openalex.org/works/doi:10.1/wrong" in url:
            return _OA("10.1/wrong", "Nanotoxicology studies Part I")
        if "crossref.org/works/10.1/wrong" in url:
            return _CR_EMPTY
        if S2 + "10.1/wrong" in url:
            return json.dumps({"title": "Nanotoxicology studies Part II", "abstract": "Polluted abstract"}), 200
        if "openalex.org/works/doi:10.1/rl" in url:
            return _OA("10.1/rl", "Rate limited paper")
        if "crossref.org/works/10.1/rl" in url:
            return _CR_EMPTY
        if S2 + "10.1/rl" in url:
            return None, 429
        if "openalex.org/works/doi:10.1016/j.econmod.2021.105588" in url:
            return _OA("10.1016/j.econmod.2021.105588", "Econ model paper")
        if "crossref.org/works/10.1016/j.econmod.2021.105588" in url:
            return _CR_EMPTY
        if S2 + "10.1016/j.econmod.2021.105588" in url:
            return json.dumps({"title": "Econ model paper", "abstract": None}), 200
        if "crossref.org/works/10.1/cr-only" in url:  # OpenAlex 404·Crossref 200(초록 없음) → DOI 해석됨 → S2 호출
            return _CR_EMPTY
        if S2 + "10.1/cr-only" in url:
            return json.dumps({"title": "Whatever", "abstract": "From S2 only"}), 200
        return None, 404
    def _rows(db, kind):
        con = sqlite3.connect(db)
        rows = con.execute("SELECT key, value FROM entries WHERE kind=?", (kind,)).fetchall(); con.close()
        return rows
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "s2.db")
        os.environ["SCHOLAR_CACHE"] = db
        os.environ.pop("SCHOLAR_NO_CACHE", None)
        m._CACHE_OFF = False
        m.http_get = _http
        m.time.sleep = lambda s_: None  # 공유풀 3초 간격 무효화(오프라인)
        m._S2_LAST[0] = 0.0
        try:
            # 1) 채택 — openalex·crossref 초록 없음 → S2 초록(제목 일치) 채택, abstract_source="s2", 캐시 저장
            ab, src, reason, meta = m.fetch_abstract({"doi": "10.1016/j.irfa.2020.101646", "title": "Bitcoin and market efficiency"})
            check("s2 초록 폴백: openalex·crossref 초록 없음 → S2 채택(abstract_source='s2'·strip·reason None·meta 유지)",
                  ab == "S2 abstract text of 1406 chars stand-in." and src == "s2" and reason is None
                  and (meta or {}).get("title") == "Bitcoin and market efficiency", f"{ab!r} {src} {reason}")
            check("s2 초록 폴백: S2 URL = graph/v1/paper/DOI:<doi>?fields=abstract,title · 앞 둘 실패 후에만(순서)",
                  any(S2 + "10.1016/j.irfa.2020.101646?fields=abstract,title" in u for u in calls)
                  and [("openalex" in u, "crossref" in u, "semanticscholar" in u) for u in calls
                       if "10.1016/j.irfa.2020.101646" in u and "select=" not in u]
                  == [(True, False, False), (False, True, False), (False, False, True)], str(calls))
            rows = _rows(db, "s2_abstract")
            check("s2 초록 폴백: 캐시 kind=s2_abstract에 초록 저장(키=DOI)", len(rows) == 1 and rows[0][0] == "10.1016/j.irfa.2020.101646"
                  and json.loads(rows[0][1])["abstract"].startswith("S2 abstract"), str(rows))
            # 2) 캐시 히트 — 2회차는 S2 호출 없이 'S2 (cache)'
            calls.clear()
            ab2, src2, _, _ = m.fetch_abstract({"doi": "10.1016/j.irfa.2020.101646", "title": "Bitcoin and market efficiency"})
            check("s2 초록 폴백: 캐시 히트 → abstract_source='s2 (cache)'·S2 재호출 0회", ab2 == ab and src2 == "s2 (cache)"
                  and not any("semanticscholar" in u for u in calls), f"{src2} {calls}")
            # 3) 제목 불일치 거부 — S2가 다른 논문을 돌려주면 초록 미채택 + 사유 + 캐시 미저장
            calls.clear()
            ab3, src3, reason3, _ = m.fetch_abstract({"doi": "10.1/wrong", "title": "Nanotoxicology studies Part I"})
            check("s2 초록 폴백: S2 제목이 행 제목과 불일치(_title_match) → 미채택·사유 's2 제목 불일치'·기존 사유 유지",
                  ab3 == "" and src3 is None and "s2 제목 불일치" in (reason3 or "") and "초록 미제공" in (reason3 or ""), f"{ab3!r} {reason3}")
            check("s2 초록 폴백: 제목 불일치 초록은 캐시하지 않음", not any(k == "10.1/wrong" for k, _ in _rows(db, "s2_abstract")))
            # 4) 429 — 조용히 빈 초록 + 기존 사유에 's2 HTTP 429' 병기, 재시도 없음(1회 호출), 캐시 미저장
            calls.clear()
            ab4, src4, reason4, meta4 = m.fetch_abstract({"doi": "10.1/rl", "title": "Rate limited paper"})
            check("s2 초록 폴백: S2 429 → 빈 초록 + 기존 사유 + '; s2 HTTP 429'·meta 유지·재시도 없음",
                  ab4 == "" and src4 is None and (reason4 or "").endswith("; s2 HTTP 429") and "초록 미제공" in reason4
                  and meta4 is not None and sum(1 for u in calls if "semanticscholar" in u) == 1, f"{reason4} {calls}")
            check("s2 초록 폴백: 429는 캐시하지 않음", not any(k == "10.1/rl" for k, _ in _rows(db, "s2_abstract")))
            # 5) S2에도 초록 없음(econmod) → 사유 그대로(덧붙임 없음)
            ab5, src5, reason5, _ = m.fetch_abstract({"doi": "10.1016/j.econmod.2021.105588", "title": "Econ model paper"})
            check("s2 초록 폴백: S2도 초록 없음 → 사유 그대로(s2 언급 없음)", ab5 == "" and src5 is None
                  and reason5 == "초록 미제공(openalex·crossref 모두 없음) — oa로 PDF 위치 확인", reason5)
            # 6) 양쪽 404(DOI 미해석) → S2 미호출·기존 '조회 실패' 사유 그대로(공유풀 낭비 방지)
            calls.clear()
            ab6, src6, reason6, meta6 = m.fetch_abstract({"doi": "10.1/nope", "title": "Nope"})
            check("s2 초록 폴백: openalex·crossref 모두 404 → S2 미호출·'조회 실패' 사유·meta None 유지",
                  ab6 == "" and src6 is None and meta6 is None and reason6.startswith("조회 실패(")
                  and not any("semanticscholar" in u for u in calls), f"{reason6} {calls}")
            # 7) OpenAlex 404지만 Crossref 200(초록 없음) → DOI 해석됨 → S2 호출·채택(행 제목 없으면 대조 생략)
            calls.clear()
            ab7, src7, reason7, meta7 = m.fetch_abstract({"doi": "10.1/cr-only"})
            check("s2 초록 폴백: OpenAlex 404·Crossref 200(초록 없음) → S2 채택(meta None·행 제목 없으면 대조 생략)",
                  ab7 == "From S2 only" and src7 == "s2" and meta7 is None and reason7 is None, f"{ab7!r} {src7} {reason7}")
            # 8) 타임아웃(-1) → 's2 연결 실패' 병기
            m.http_get = lambda url, **kw: ((_OA("10.1/to", "T")) if "openalex.org/works/doi:10.1/to" in url and "select=" not in url
                                            else (json.dumps({"is_retracted": False}), 200) if "select=" in url
                                            else _CR_EMPTY if "crossref" in url else (None, -1))
            _, _, reason8, _ = m.fetch_abstract({"doi": "10.1/to", "title": "T"})
            check("s2 초록 폴백: 타임아웃(-1) → 사유에 's2 연결 실패' 병기", "s2 연결 실패" in (reason8 or ""), reason8)
        finally:
            m.http_get, m.time.sleep, m._CACHE_OFF = orig_http, orig_sleep, orig_off
            os.environ["SCHOLAR_NO_CACHE"] = "1"
            os.environ.pop("SCHOLAR_CACHE", None)
            m._S2_LAST[0] = 0.0
_s2_abstract_tests()


# ---- round6 data-layer-w: FRED·World Bank 시계열 + W# ledger(fred/worldbank/list/show/verify/vintage) ----
def _data_tests():
    import types
    A = lambda **k: types.SimpleNamespace(**k)
    FRED_CSV = ("observation_date,DGS10\n2024-01-01,.\n2024-01-02,3.95\n2024-01-03,3.9\n"
                "2024-01-04,.\n2024-01-05,4.05\n2024-01-08,4.02\n")
    FRED_CSV_NEW = FRED_CSV + "2024-01-09,4.1\n"
    FRED_HTML = "<html><head><title>10-Year Treasury &amp; Constant Maturity (DGS10) | FRED | St. Louis Fed</title></head></html>"
    def _wb(iso, name, vals):
        return json.dumps([{"page": 1, "pages": 1, "per_page": 20000, "total": len(vals)},
                           [{"indicator": {"id": "NY.GDP.MKTP.CD", "value": "GDP (current US$)"},
                             "country": {"id": iso, "value": name}, "countryiso3code": iso,
                             "date": str(y), "value": v} for y, v in vals]])
    WB_US = _wb("US", "United States", [(2022, 2.5e13), (2021, 2.3e13), (2020, None)])
    WB_KR = _wb("KR", "Korea, Rep.", [(2022, 1.7e12), (2021, 1.8e12)])
    state = {"fred_csv": FRED_CSV, "fred_fail": False, "wb_fail": False, "html_fail": False, "calls": []}

    def fake_get(url, headers=None, **kw):
        state["calls"].append(url)
        if "fredgraph.csv" in url:
            if state["fred_fail"]:
                return None, state["fred_fail"] if isinstance(state["fred_fail"], int) else 500
            sid = urllib_parse.parse_qs(urllib_parse.urlsplit(url).query).get("id", [""])[0]
            # 헤더 id는 요청 시리즈를 따른다(헤더 강제 검사 자체는 별도 케이스에서 OTHER로 검증)
            body = state["fred_csv"]
            return (body.replace("observation_date,DGS10", f"observation_date,{sid}", 1) if body.startswith("observation_date,DGS10") else body), 200
        if "fred.stlouisfed.org/series/" in url:
            return (None, 404) if state["html_fail"] else (FRED_HTML, 200)
        if "api.worldbank.org" in url:
            if state["wb_fail"]:
                return None, -1
            return (WB_US if "/country/US/" in url else WB_KR), 200
        return None, 404

    def run(fn, a):
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                fn(a)
            code = 0
        except SystemExit as e:
            code = e.code
        return code, out.getvalue(), err.getvalue()

    orig_http, orig_home = m.http_get, m.SCHOLAR_HOME
    with tempfile.TemporaryDirectory() as td:
        m.SCHOLAR_HOME = td
        m.http_get = fake_get
        try:
            # 1) FRED CSV 파싱 — 결측 '.'→None, 헤더 강제, 날짜 필터
            rows = m._parse_fred_csv(FRED_CSV, "DGS10")
            check("data: FRED CSV 파싱 결측 '.'→None·6행", len(rows) == 6 and rows[0] == ("2024-01-01", None) and rows[1] == ("2024-01-02", 3.95), str(rows))
            filt = m._filter_rows(rows, "2024-01-03", "2024-01-05")
            check("data: 날짜 필터 --start/--end", [d for d, _ in filt] == ["2024-01-03", "2024-01-04", "2024-01-05"], str(filt))
            try:
                m._parse_fred_csv("observation_date,OTHER\n2024-01-01,1\n", "DGS10"); check("data: 헤더 불일치 DataError", False)
            except m.DataError:
                check("data: 헤더 불일치 DataError", True)
            try:
                m._parse_fred_csv("", "DGS10"); check("data: 빈 CSV DataError", False)
            except m.DataError:
                check("data: 빈 CSV DataError", True)
            # 2) 메타: <title> 엔티티 해제·' | FRED' 앞부분 / 주기 추정
            check("data: FRED 제목 파싱(엔티티·| FRED 절단)", m._fred_title_from_html(FRED_HTML) == "10-Year Treasury & Constant Maturity (DGS10)", m._fred_title_from_html(FRED_HTML))
            check("data: 주기 추정 D/W/M/Q/A",
                  m.infer_frequency([d for d, _ in rows]) == "D"
                  and m.infer_frequency(["2024-01-01", "2024-01-08", "2024-01-15"]) == "W"
                  and m.infer_frequency(["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01"]) == "M"
                  and m.infer_frequency(["2024-01-01", "2024-04-01", "2024-07-01"]) == "Q"
                  and m.infer_frequency(["2020", "2021", "2022"]) == "A")
            # 3) fred 수집 → W1·csv·ledger
            code, out, err = run(m.cmd_data_fred, A(series_id="DGS10", start=None, end=None, out=None))
            led = m._ledger_load()
            e1 = led["entries"][0] if led["entries"] else {}
            check("data: fred 수집 → W1 신규·exit 0", code == 0 and e1.get("wid") == "W1" and "[W1] 신규" in out, out + err)
            check("data: ledger 스키마(title·frequency·obs_end=마지막 비결측·n_obs·sha256·url)",
                  e1.get("title") == "10-Year Treasury & Constant Maturity (DGS10)" and e1.get("frequency") == "D"
                  and e1.get("obs_end") == "2024-01-08" and e1.get("obs_start") == "2024-01-02" and e1.get("n_obs") == 4
                  and e1.get("n_missing") == 2 and e1.get("units") is None and len(e1.get("sha256", "")) == 64
                  and "fredgraph.csv?id=DGS10" in e1.get("url", ""), json.dumps(e1, ensure_ascii=False))
            check("data: csv 파일 경로·헤더 date,value", os.path.exists(e1["csv_path"]) and os.path.basename(e1["csv_path"]) == "W1_DGS10.csv"
                  and open(e1["csv_path"]).readline() == "date,value\n", e1.get("csv_path"))
            # 메타 실패해도 CSV만으로 진행
            state["html_fail"] = True
            code, out, err = run(m.cmd_data_fred, A(series_id="DGS2", start=None, end=None, out=os.path.join(td, "o.csv")))
            state["html_fail"] = False
            e2 = m._ledger_get(m._ledger_load(), "W2") or {}
            check("data: 메타 실패해도 CSV만으로 W2 등록·--out 복사", code == 0 and e2.get("title") is None and os.path.exists(os.path.join(td, "o.csv")), out + err)
            # 4) World Bank 국가 2개 → W3·W4, 오름차순, null→None
            code, out, err = run(m.cmd_data_worldbank, A(indicator="NY.GDP.MKTP.CD", country="us,KR", start=None, end=None, out=None))
            led = m._ledger_load()
            eu, ek = m._ledger_find(led, "worldbank", "NY.GDP.MKTP.CD", "US"), m._ledger_find(led, "worldbank", "NY.GDP.MKTP.CD", "KR")
            check("data: worldbank 국가 2개 → W3(US)·W4(KR) exit 0", code == 0 and eu and ek and {eu["wid"], ek["wid"]} == {"W3", "W4"}, out + err)
            us_rows = m._read_csv(eu["csv_path"]) if eu else []
            check("data: worldbank 파싱 — 연도 오름차순·null→None·제목=indicator.value — country.value·주기 A",
                  [d for d, _ in us_rows] == ["2020", "2021", "2022"] and us_rows[0][1] is None
                  and eu.get("title") == "GDP (current US$) — United States" and eu.get("obs_end") == "2022" and eu.get("frequency") == "A",
                  json.dumps(eu, ensure_ascii=False))
            check("data: worldbank csv 파일명에 국가 접미", os.path.basename(eu["csv_path"]) == f"{eu['wid']}_NY.GDP.MKTP.CD_US.csv", eu["csv_path"])
            # 5) 재조회 → 같은 W# 재사용·fetched_at·sha256·obs_end 갱신 / 삭제 번호 미재사용
            state["fred_csv"] = FRED_CSV_NEW
            m.time.sleep(0) ; old_sha, old_fetch = e1["sha256"], e1["fetched_at"]
            code, out, err = run(m.cmd_data_fred, A(series_id="DGS10", start=None, end=None, out=None))
            led = m._ledger_load(); e1b = m._ledger_get(led, "W1")
            check("data: 같은 시리즈 재조회 → W1 재사용·obs_end/sha256 갱신·항목 수 불변",
                  code == 0 and "[W1] 재사용(갱신)" in out and e1b["obs_end"] == "2024-01-09" and e1b["sha256"] != old_sha
                  and len(led["entries"]) == 4 and led["next_wid"] == 5, out + str(e1b))
            led["entries"] = [x for x in led["entries"] if x["wid"] != "W2"]; m._ledger_save(led)
            code, out, err = run(m.cmd_data_fred, A(series_id="UNRATE", start=None, end=None, out=None))
            led = m._ledger_load()
            check("data: 삭제된 W2 번호 미재사용 → 신규는 W5", code == 0 and m._ledger_find(led, "fred", "UNRATE", None)["wid"] == "W5"
                  and m._ledger_get(led, "W2") is None, str([x["wid"] for x in led["entries"]]))
            # 6) verify: 전부 OK → exit 0 + 요약줄
            code, out, err = run(m.cmd_data_verify, A(wids=[], json=False))
            check("data: verify 전부 OK exit 0·요약줄", code == 0 and "데이터 검증: 4/4 OK · stale 0건" in out, out + err)
            # 7) verify: 원격 obs_end 앞당김 → ⚠ stale + exit 5
            state["fred_csv"] = FRED_CSV_NEW + "2024-01-10,4.2\n"
            code, out, err = run(m.cmd_data_verify, A(wids=["W1"], json=True))
            check("data: verify 원격 새 관측 → '⚠ stale' + exit 5", code == 5 and "⚠ stale" in out and "데이터 검증: 0/1 OK · stale 1건" in out
                  and "2024-01-10" in out, out + err)
            # 원격 재조회 실패 → 미검증 exit 5 (stale 아님)
            state["wb_fail"] = True
            code, out, err = run(m.cmd_data_verify, A(wids=["W3"], json=False))
            state["wb_fail"] = False
            check("data: verify 원격 재조회 실패 → UNVERIFIED exit 5(부재 아님)", code == 5 and "UNVERIFIED" in out and "미검증 1건" in out, out + err)
            # 8) verify: sha256 불일치 → exit 3 / 파일 부재 → exit 3 / 3이 5보다 우선
            with open(eu["csv_path"], "a") as fh:
                fh.write("2023,9.9\n")
            code, out, err = run(m.cmd_data_verify, A(wids=["W3"], json=False))
            check("data: verify sha256 불일치 → SHA_MISMATCH exit 3", code == 3 and "SHA_MISMATCH" in out and "무결성 실패 1건" in out, out + err)
            os.remove(ek["csv_path"])
            code, out, err = run(m.cmd_data_verify, A(wids=["W1", "W4"], json=False))
            check("data: verify 파일 부재+stale 혼재 → 3 우선", code == 3 and "MISSING_FILE" in out and "⚠ stale" in out, out + err)
            code, out, err = run(m.cmd_data_verify, A(wids=["W99"], json=False))
            check("data: verify 미등록 W# → exit 3", code == 3 and "미등록" in err, out + err)
            # 9) vintage 표 형식
            code, out, err = run(m.cmd_data_vintage, A(wids=["W1", "W3"], out=None))
            lines = out.strip().splitlines()
            check("data: vintage 마크다운 표(헤더·구분선·W1/W3 행·obs_end·fetched_at·주기·URL)",
                  code == 0 and lines[0].startswith("| W# | 시리즈 | 관측 기준시점(obs_end) | 수집일(fetched_at) | 주기 | 출처 URL |")
                  and lines[1].startswith("|---|") and len(lines) == 4
                  and lines[2].startswith("| W1 | DGS10 — 10-Year") and "| 2024-01-09 |" in lines[2] and "| D |" in lines[2]
                  and "fredgraph.csv?id=DGS10" in lines[2]
                  and lines[3].startswith("| W3 | NY.GDP.MKTP.CD US") and "| 2022 |" in lines[3] and "| A |" in lines[3]
                  and e1b["fetched_at"][:10] in lines[2], out + err)
            # 10) show --tail --stats
            code, out, err = run(m.cmd_data_show, A(wid="W1", tail=2, stats=True))
            st = json.loads([ln for ln in out.splitlines() if ln.startswith("stats: ")][0][7:])
            check("data: show --tail 2 --stats(n·min·max·first/last·결측)", code == 0 and out.strip().endswith("2024-01-08,4.02\n2024-01-09,4.1")
                  and st["n"] == 5 and st["n_missing"] == 2 and st["min"] == {"date": "2024-01-03", "value": 3.9}
                  and st["max"] == {"date": "2024-01-09", "value": 4.1} and st["first"]["date"] == "2024-01-02" and st["last"]["date"] == "2024-01-09", out + err)
            # 11) HTTP 실패·헤더 불일치·빈 CSV → exit 2(BAD_SOURCE), ledger 불변
            n_before = len(m._ledger_load()["entries"])
            state["fred_fail"] = 404
            code, out, err = run(m.cmd_data_fred, A(series_id="NOPE", start=None, end=None, out=None))
            check("data: FRED HTTP 404 → exit 2 BAD_SOURCE", code == 2 and "BAD_SOURCE" in err and "HTTP 404" in err, out + err)
            state["fred_fail"] = False; state["fred_csv"] = "observation_date,OTHER\n2024-01-01,1\n"
            code, out, err = run(m.cmd_data_fred, A(series_id="DGS30", start=None, end=None, out=None))
            check("data: FRED 헤더 불일치 → exit 2", code == 2 and "헤더 불일치" in err, out + err)
            state["fred_csv"] = ""
            code, out, err = run(m.cmd_data_fred, A(series_id="DGS30", start=None, end=None, out=None))
            check("data: FRED 빈 CSV → exit 2", code == 2 and "빈 CSV" in err, out + err)
            state["fred_csv"] = FRED_CSV_NEW
            code, out, err = run(m.cmd_data_fred, A(series_id="DGS10", start="2030-01-01", end=None, out=None))
            check("data: 기간 필터 후 0건 → exit 2", code == 2 and "관측 0건" in err, out + err)
            code, out, err = run(m.cmd_data_fred, A(series_id="DGS10", start="2024/01/01", end=None, out=None))
            check("data: --start 형식 오류 → exit 2", code == 2 and "날짜 형식" in err, out + err)
            state["wb_fail"] = True
            code, out, err = run(m.cmd_data_worldbank, A(indicator="NY.GDP.MKTP.CD", country="US", start=None, end=None, out=None))
            state["wb_fail"] = False
            check("data: World Bank 네트워크 실패(-1) → exit 2", code == 2 and "HTTP -1" in err, out + err)
            check("data: 실패 시 ledger 불변", len(m._ledger_load()["entries"]) == n_before)
            # 12) 시계열은 sqlite 캐시 안 함 — 재조회마다 원격 호출
            state["calls"].clear()
            run(m.cmd_data_fred, A(series_id="DGS10", start=None, end=None, out=None))
            run(m.cmd_data_fred, A(series_id="DGS10", start=None, end=None, out=None))
            check("data: 시계열 미캐시(재조회마다 fredgraph 호출)", sum(1 for u in state["calls"] if "fredgraph" in u) == 2, str(state["calls"]))
            # 13) CLI 배선: main()에서 data 서브커맨드 파싱
            _argv = sys.argv
            try:
                sys.argv = ["scholar.py", "data", "list"]
                o = io.StringIO()
                with contextlib.redirect_stdout(o):
                    m.main()
                check("data: CLI 'data list' 배선·표 출력", "W1" in o.getvalue() and "DGS10" in o.getvalue(), o.getvalue())
            finally:
                sys.argv = _argv
        finally:
            m.http_get, m.SCHOLAR_HOME = orig_http, orig_home
_data_tests()

print(f"\n{'FAIL ' + str(len(fails)) + '건: ' + ', '.join(fails) if fails else 'ALL PASS'}"
      f" ({count}건{' (live 포함)' if '--live' in sys.argv else ' 오프라인'})")
sys.exit(1 if fails else 0)
