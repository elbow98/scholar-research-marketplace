# scholar-research

Source-grounded academic answers — a from-scratch reproduction of a Scholar-mode
research assistant using **only free scholarly APIs and your own Claude
subscription**. No server, no inference bill, no database licence.

Every factual claim in an answer carries a `[P#]` citation tag, and **every
citation is machine-verified against the DOI registry** before the answer is
emitted — so hallucinated references are structurally impossible (the answer ends
with `인용 검증: N/N MATCH`).

## What it does

Ask a research question and the skill runs a fixed 6-stage pipeline:

1. **Decompose** the question into English academic keyword combinations
2. **Multi-source search** — one-shot `bundle`: OpenAlex + Crossref + Semantic
   Scholar in parallel → DOI dedup → relevance-first ranking → top-K abstracts
   (OpenAlex → Crossref → S2 fallback). Auto-adds PubMed for medicine, arXiv for
   CS/physics, Europe PMC for full-text-only terms. Retracted papers are pushed to
   the tail and kept out of abstract slots; 2 slots are reserved for recent (5y) work
3. **Screen** candidates for relevance (bulk screening delegated to a cheap
   sub-agent to save credits)
4. **Close-read** 3–8 selected papers (abstract, or legal OA full text via
   Unpaywall — no paywall circumvention)
5. **Synthesize** with a strict source-tag rubric ([P#] per claim; interpretation
   physically separated)
6. **Double verification gate** — (A) every DOI/title/first-author/year checked
   against Crossref/OpenAlex/DataCite plus retraction status (Crossref update-to,
   OpenAlex, PubMed); antonym-prefix swaps (hyper/hypo…), dropped hedge words
   (Weak/Limited…) and truncated titles are caught. (B) every `[P#]` sentence is
   matched to an evidence-ledger excerpt. The answer is not emitted until both pass

## Data sources (all free)

| Source | Coverage | Key |
|---|---|---|
| OpenAlex | ~250M works, abstracts, citation graph | none (email polite pool) |
| Crossref | DOI registry, bibliography | none |
| Semantic Scholar | recommendations, TLDRs | none (shared pool; 429 auto-skipped) |
| arXiv | STEM preprints | none |
| PubMed (E-utilities) | medicine / life sciences | none (`NCBI_API_KEY` optional) |
| Unpaywall | legal open-access PDF locations | none (email) |
| Europe PMC | full-text indexed search, preprints | none |
| DataCite | arXiv/dataset DOI metadata (title re-check) | none |
| FRED (public CSV) | US macro/financial time series for `[W#]` data layer | none |
| World Bank API v2 | country indicators for `[W#]` data layer | none |

## Requirements

- **Claude Code** (the skill runs on your own subscription — that is the entire
  inference cost model)
- **Python 3** (standard library only — no `pip install` required; `certifi` is
  used if present but not needed)
- Internet access to the public API hosts above

## Install

```
/plugin marketplace add elbow98/scholar-research-marketplace
/plugin install scholar-research
```

(Replace `elbow98/...` with wherever you host this repo.)

## Optional environment variables

None are required. Setting them just improves rate limits / etiquette:

```
export SCHOLAR_EMAIL="you@example.com"   # OpenAlex/Crossref/Unpaywall polite pool
export NCBI_API_KEY="..."                # PubMed 3→10 req/s
export OPENALEX_API_KEY="..."            # optional
export S2_API_KEY="..."                  # optional; lifts Semantic Scholar 429s
```

## Usage

```
/scholar-research uranium supply-demand balance and price outlook
```

or just ask in natural language: *"answer with academic sources: …"*,
*"논문 근거로 답해줘: …"*, *"find papers on …"*.

The CLI can also be driven directly:

```
S="${CLAUDE_PLUGIN_ROOT}/skills/scholar-research/scripts/scholar.py"
python3 $S search "in-situ recovery uranium extraction efficiency" --limit 10
python3 $S paper 10.1080/14786451.2025.2457376
python3 $S oa 10.1080/14786451.2025.2457376
python3 $S cite 10.1080/14786451.2025.2457376 --style apa
python3 $S verify 10.1080/14786451.2025.2457376 "Global uranium market dynamics"
python3 $S snowball 10.1080/14786451.2025.2457376 --direction cites
python3 $S recommend 10.1080/14786451.2025.2457376
python3 $S bundle "regime based asset allocation commodities" --top 8 --out bundle.json
python3 $S verify-batch refs.json          # exit 0 MATCH / 2 / 3 / 4 RETRACTED / 5 / 6
python3 $S project init my-topic --title "My topic" && python3 $S project add my-topic --from bundle.json --pick P1,P3
python3 $S project render my-topic --format md|html|ris|bibtex|xlsx
python3 $S graph my-topic --format mermaid   # citation graph
python3 $S review my-topic P1                # peer-review packet (5 lenses)
python3 $S data fred CPIAUCSL --start 2015-01-01   # [W1] with vintage ledger
python3 $S data worldbank FP.CPI.TOTL.ZG --country US,KR
python3 $S data verify && python3 $S data vintage
```

### Local cache

Paper metadata/abstracts and bibliographic lookups are cached in sqlite for 30
days (`~/.cache/scholar-research/cache.db`, override with `SCHOLAR_CACHE`).
Retraction status is **never** cached — it is re-queried live on every hit.
Disable with `--no-cache` or `SCHOLAR_NO_CACHE=1`.

### Project home

Project registries and `[W#]` data ledgers live under `SCHOLAR_HOME`
(default `~/Documents/mybrain_raw/scholar_projects`). Set it to wherever you
like; nothing else in the skill depends on that path.

## Regression tests

```
python3 ${CLAUDE_PLUGIN_ROOT}/skills/scholar-research/scripts/selftest.py          # offline, ~620 checks
python3 ${CLAUDE_PLUGIN_ROOT}/skills/scholar-research/scripts/selftest.py --live   # adds live-API checks
```

## Changelog

- **0.2.0 (2026-09-15)** — parallel source search (−54% latency), `bundle`,
  sqlite cache, relevance-first ranking, recent-work slots, retracted-slot
  exclusion, S2 abstract fallback, `[W#]` data layer (FRED / World Bank +
  vintage ledger), project mode (tables, AI columns, RIS/BibTeX/XLSX, citation
  graph, peer-review packet), Europe PMC + DataCite sources, and ~90 red-team
  fixes to the verification gate (retraction detection, antonym-prefix and
  hedge-word title checks, DOI normalisation, arXiv-DOI title re-check,
  snowball full-reference paging). selftest 217 → 622 checks.
- **0.1.0 (2026-07-27)** — initial release.

## What it will not do

- No Google Scholar scraping, no Sci-Hub, no paywall circumvention
- No citation is written without passing DOI machine-verification
- Papers without legal OA access are used at abstract level only, tagged `(초록)`

## License

MIT
