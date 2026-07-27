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
2. **Multi-source search** — OpenAlex + Crossref + Semantic Scholar (auto-adds
   PubMed for medicine, arXiv for CS/physics)
3. **Screen** candidates for relevance (bulk screening delegated to a cheap
   sub-agent to save credits)
4. **Close-read** 3–8 selected papers (abstract, or legal OA full text via
   Unpaywall — no paywall circumvention)
5. **Synthesize** with a strict source-tag rubric ([P#] per claim; interpretation
   physically separated)
6. **Citation verification gate** — every DOI/title checked; the answer is not
   emitted until all citations pass

## Data sources (all free)

| Source | Coverage | Key |
|---|---|---|
| OpenAlex | ~250M works, abstracts, citation graph | none (email polite pool) |
| Crossref | DOI registry, bibliography | none |
| Semantic Scholar | recommendations, TLDRs | none (shared pool; 429 auto-skipped) |
| arXiv | STEM preprints | none |
| PubMed (E-utilities) | medicine / life sciences | none (`NCBI_API_KEY` optional) |
| Unpaywall | legal open-access PDF locations | none (email) |

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
```

## What it will not do

- No Google Scholar scraping, no Sci-Hub, no paywall circumvention
- No citation is written without passing DOI machine-verification
- Papers without legal OA access are used at abstract level only, tagged `(초록)`

## License

MIT
