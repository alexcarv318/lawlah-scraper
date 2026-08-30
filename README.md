# Lawlah scraper

Pipeline for Lawlah, an AI system for Singapore lawyers.

It scrapes legislation from [sso.gov.sg](https://sso.gov.sg) and case law from [LawNet](https://www.lawnet.com), checks that each document is complete, then loads a knowledge base the product can search and classify.

Scrapes never write straight into the knowledge base. Raw fetches land in `raw_source`. Only documents that pass parse checks are promoted to `knowledge_base`. Classification is a later step.

Two Postgres databases on one instance:

| Database | Role |
|---|---|
| `raw_source` | Landing zone. Source-shaped: URL, HTML, LawNet JSON, parse status. |
| `knowledge_base` | Serving graph. Cases, legislation, provisions, paragraphs, taxonomy, aliases, references. |

LawNet is the only case source. Acts and subsidiary legislation have versions. Cases do not. The knowledge base still stores one current SL row until we promote SL versions.

---

## Contents

- [Case scrape entry points](#case-scrape-entry-points)
- [Case parse entry points](#case-parse-entry-points)
- [Case promote entry points](#case-promote-entry-points)
- [Legislation scrape entry points](#legislation-scrape-entry-points)
- [Raw source](#raw-source)
  - [Cases](#raw-source-cases)
    - [raw_cases](#raw_cases)
    - [raw_case_documents](#raw_case_documents)
  - [Acts](#raw-source-acts)
    - [raw_acts](#raw_acts)
    - [raw_act_versions](#raw_act_versions)
  - [Subsidiary legislation](#raw-source-subsidiary-legislation)
    - [raw_subsidiary_legislations](#raw_subsidiary_legislations)
    - [raw_subsidiary_legislation_versions](#raw_subsidiary_legislation_versions)
- [Knowledge base](#knowledge-base)
  - [Cases](#cases)
    - [courts](#courts)
    - [cases](#cases-1)
    - [judges](#judges)
    - [case_judges](#case_judges)
    - [parties](#parties)
    - [case_parties](#case_parties)
    - [counsels](#counsels)
    - [case_counsels](#case_counsels)
  - [Legislation](#legislation)
    - [acts](#acts)
    - [act_versions](#act_versions)
    - [subsidiary_legislations](#subsidiary_legislations)
    - [legislative_definitions](#legislative_definitions)
  - [Provisions](#provisions)
  - [Paragraphs](#paragraphs)
  - [Taxonomy](#taxonomy)
    - [topics](#topics)
    - [concepts](#concepts)
    - [functional_roles](#functional_roles)
    - [Junctions](#taxonomy-junctions)
  - [Aliases](#aliases)
  - [References](#references)

---

## Case scrape entry points

These write to `raw_source` only. They do not parse layouts or promote to the knowledge base.

`CaseRawScraper.run` is the usual entry. It opens a session, discovers cases, then fetches pending documents. Use `CaseSearchScraper.discover` or `CaseDocumentScraper.fetch_pending` when you want one step on its own.

### `CaseRawScraper.run`

Discover LawNet search hits, then fetch documents that are still pending.

| Argument | Type | Description |
|---|---|---|
| `max_search_pages` | `int \| None` | How many search pages to walk. `None` means until LawNet returns an empty page. |
| `max_documents` | `int \| None` | How many pending documents to fetch. `None` means all pending. |
| `until_latest_stored_date` | `bool` | Default `True`. Search is `date-desc`. Stop after a hit older than the latest `raw_cases.date`. Same-day cases are still stored. `False` walks the whole catalog (first load or a manual backfill). |

```python
from src.cases.scrape import CaseRawScraper

# Incremental update (cron)
CaseRawScraper().run(max_search_pages=None, max_documents=None)

# First load
CaseRawScraper().run(
    max_search_pages=None,
    max_documents=None,
    until_latest_stored_date=False,
)

# Smoke test
CaseRawScraper().run(max_search_pages=2, max_documents=16, until_latest_stored_date=False)
```

### `CaseSearchScraper.discover`

Walk LawNet search and insert missing `raw_cases`. Returns how many rows were added.

| Argument | Type | Description |
|---|---|---|
| `max_pages` | `int \| None` | Page cap. `None` means until search is empty. |
| `until_latest_stored_date` | `bool` | Same stop rule as `run`. |

Needs a `RawCaseRepository` and a `LawNetClient`.

```python
added = CaseSearchScraper(repository, client).discover(
    max_pages=None,
    until_latest_stored_date=True,
)
```

### `CaseDocumentScraper.fetch_pending`

Fetch LawNet documents for `raw_cases` that have no successful `raw_case_documents` row. Returns how many documents were attempted.

| Argument | Type | Description |
|---|---|---|
| `max_documents` | `int \| None` | How many pending citations to fetch. `None` means all pending. |

Needs a `RawCaseRepository` and `ScrapeLimits`.

```python
fetched = CaseDocumentScraper(repository, limits).fetch_pending(max_documents=80)
```

---

## Case parse entry points

These write to `raw_source` only. They do not store paragraph text or promote to the knowledge base.

`CaseRawParser.run` is the usual entry. It opens a session and parses fetched documents that are still `not_parsed`. Use `CaseDocumentParser.parse_pending` when you already have a session.

Detected layouts: `modern_judg1`, `numbered_plain_p`, `unnumbered_br`. Anything else is `unknown_layout` and `needs_review`.

### `CaseRawParser.run`

Parse successful `raw_case_documents` that are still `not_parsed`. Writes `layout`, counts, `parse_status`, and `needs_review` on the same row.

| Argument | Type | Description |
|---|---|---|
| `max_documents` | `int \| None` | How many unparsed documents to parse. `None` means all pending. |

```python
from src.cases.parse import CaseRawParser

# All fetched, unparsed
CaseRawParser().run(max_documents=None)

# Smoke test
CaseRawParser().run(max_documents=16)
```

### `CaseDocumentParser.parse_pending`

Same work as `run`, on an existing repository. Returns how many documents were parsed.

| Argument | Type | Description |
|---|---|---|
| `max_documents` | `int \| None` | How many unparsed documents to parse. `None` means all pending. |

Needs a `RawCaseRepository`.

```python
parsed = CaseDocumentParser(repository).parse_pending(max_documents=80)
```

---

## Case promote entry points

These read `raw_source` and write `knowledge_base`. Only `parse_status=complete` and not yet `promoted` documents are promoted. Paragraphs are re-extracted from HTML. A successful promote sets `promoted=true`.

`CaseRawPromoter.run` is the usual entry. Use `CasePromoter.promote_pending` when you already have sessions.

### `CaseRawPromoter.run`

Promote complete raw cases into `courts`, `cases`, `paragraphs`, `judges`, `parties`, and `counsels`.

| Argument | Type | Description |
|---|---|---|
| `max_cases` | `int \| None` | How many complete cases to promote. `None` means all pending. |

```python
from src.cases.promote import CaseRawPromoter

# All complete, not yet in the knowledge base
CaseRawPromoter().run(max_cases=None)

# Smoke test
CaseRawPromoter().run(max_cases=16)
```

### `CasePromoter.promote_pending`

Same work as `run`, on existing repositories. Returns how many cases were inserted.

| Argument | Type | Description |
|---|---|---|
| `max_cases` | `int \| None` | How many complete cases to promote. `None` means all pending. |

Needs a `RawCaseRepository`, a `KnowledgeCaseRepository`, and a `CaseDocumentParser`.

```python
promoted = CasePromoter(raw_repository, knowledge_repository, parser).promote_pending(
    max_cases=80
)
```

---

## Legislation scrape entry points

These write to `raw_source` only. They do not parse provisions or promote to the knowledge base.

Needs Chromium: `uv run playwright install chromium`.

`LegislationRawScraper.run` is the usual entry. It discovers current acts, walks each timeline, then fetches pending versions. It does **not** scrape subsidiary legislation.

`LegislationRawScraper.scrape_subsidiary_legislation` is for later. It only reads the SL tab of **acts already in `raw_acts`**. It does not browse the global SL catalog.

### `LegislationRawScraper.run`

Browse current acts, insert missing `raw_acts` / `raw_act_versions`, then fetch HTML that is still pending.

| Argument | Type | Description |
|---|---|---|
| `max_acts` | `int \| None` | How many browse rows / acts to process. `None` means the full current list. |
| `max_versions` | `int \| None` | How many pending act versions to fetch. `None` means all pending. |

```python
from src.legislation.scrape import LegislationRawScraper

# Full current acts and every timeline version
LegislationRawScraper().run(max_acts=None, max_versions=None)

# Smoke test
LegislationRawScraper().run(max_acts=1, max_versions=3)
```

### `LegislationRawScraper.scrape_subsidiary_legislation`

For each stored act, list that act's subsidiary legislation, walk those timelines, then fetch pending SL versions.

| Argument | Type | Description |
|---|---|---|
| `max_acts` | `int \| None` | How many stored acts to take SL from. `None` means every stored act. |
| `max_versions` | `int \| None` | How many pending SL versions to fetch. `None` means all pending for those acts. |

```python
from src.legislation.scrape import LegislationRawScraper

LegislationRawScraper().scrape_subsidiary_legislation(
    max_acts=None,
    max_versions=None,
)
```

`?WholeDoc=1` tells SSO to load the whole document via `/Details/GetLazyLoadContent`. Fetch uses Playwright, scrolls to trigger those requests, and is done when every TOC `#pr*` / `#Sc*` id is on the page. If content stops growing, the current HTML is saved and marked `needs_review`. Each version is committed as soon as it is fetched.

SSO Playwright uses `PROXY_DNS`, `PROXY_PORT`, `PROXY_USERNAME`, and `PROXY_PASSWORD` from `.env` when they are set. LawNet does not.

---

## Raw source

Work items and fetched HTML. Not a second copy of the knowledge-base graph.

Search creates a `raw_cases` row. The document API fills `raw_case_documents`. Browse creates a `raw_acts` row. Each timeline date fills `raw_act_versions`. Each act's SL list fills `raw_subsidiary_legislations`, and each SL timeline date fills `raw_subsidiary_legislation_versions`. Promote only when `parse_status` is `complete`.

### Raw source cases

### raw_cases

One row per citation we know about. The work item. `date` is a scrape cursor, taken from the LawNet search hit. Everything else from search stays in `search_result`.

| Column | Type | Description |
|---|---|---|
| `id` | integer | Primary key. |
| `neutral_citation` | string, unique | Identity, e.g. `[2026] SGHC 164`. |
| `date` | date, nullable | Decision date. Used to scrape incrementally. |
| `status` | string | `discovered` / `fetch_failed` / `parse_incomplete` / `complete` / `needs_review`. |
| `search_result` | jsonb, nullable | LawNet search item as-is (`titles`, `ncitation`, `dates`, `courts`, `casenumber`, `corams`, `catchword`, …). |

### raw_case_documents

One LawNet snapshot per case. Re-fetch overwrites this row.

| Column | Type | Description |
|---|---|---|
| `id` | integer | Primary key. |
| `raw_case_id` | integer, unique | FK → `raw_cases`. |
| `source_url` | text | LawNet document URL. |
| `http_status` | integer, nullable | HTTP status of the fetch. |
| `fetch_status` | string | `success` / `not_found` / `error`. |
| `fetch_error` | text, nullable | Error message when the fetch fails. |
| `html` | text, nullable | Judgment HTML from the LawNet document API. |
| `source_metadata` | jsonb, nullable | LawNet document `metadata` as-is (`CaseTitle`, `CaseNumber`, `Parties`, `Counsels`, `Corams`, …). |
| `layout` | string, nullable | Detected HTML layout: `modern_judg1` / `numbered_plain_p` / `unnumbered_br` / `single_block` / `unknown`. |
| `parse_status` | string | `not_parsed` / `complete` / `incomplete` / `unknown_layout` / `failed`. |
| `expected_paragraph_count` | integer, nullable | Numbered layouts only: the max paragraph number. `NULL` when the layout has no numbers. |
| `extracted_paragraph_count` | integer, nullable | Paragraphs the parser produced. |
| `needs_review` | boolean | Unknown layout or incomplete parse. |
| `promoted` | boolean | `true` after the document is written to the knowledge base. |

### Raw source acts

### raw_acts

One row per current act from the SSO browse list. The work item. Slug is the path id (`AA2004`).

| Column | Type | Description |
|---|---|---|
| `id` | integer | Primary key. |
| `slug` | string, unique | Act id from the URL, e.g. `AA2004`. |
| `title` | string | Title from the browse list. |
| `source_url` | text | Current act URL, e.g. `https://sso.agc.gov.sg/Act/AA2004`. |
| `status` | string | `discovered` / `fetch_failed` / `parse_incomplete` / `complete` / `needs_review`. |

### raw_act_versions

One snapshot per timeline date. Unique on `(raw_act_id, valid_from)`. `html` is `#legisContent` after `WholeDoc=1` lazy-load finishes. An act is complete when every timeline date has a complete version.

| Column | Type | Description |
|---|---|---|
| `id` | integer | Primary key. |
| `raw_act_id` | integer | FK → `raw_acts`. |
| `valid_from` | date | Version date from the timeline (`ValidDate`). |
| `is_current` | boolean | The version in force now. |
| `source_url` | text | Current URL or `/Act/AA2004/Historical/20241209?…`. |
| `http_status` | integer, nullable | HTTP status of the fetch. |
| `fetch_status` | string | `not_fetched` / `success` / `not_found` / `error`. |
| `fetch_error` | text, nullable | Error message when the fetch fails. |
| `html` | text, nullable | Full act HTML after lazy-load. |
| `source_metadata` | jsonb, nullable | TOC vs assembled section/schedule counts. |
| `parse_status` | string | `not_parsed` / `complete` / `incomplete` / `unknown_layout` / `failed`. |
| `expected_provision_count` | integer, nullable | TOC `#pr*` plus `#Sc*` count. |
| `extracted_provision_count` | integer, nullable | Assembled `div.prov1` plus `div.schedule`. |
| `needs_review` | boolean | Stub HTML, count mismatch, or later incomplete parse. |

### Raw source subsidiary legislation

### raw_subsidiary_legislations

One row per current SL instrument listed under a stored act. The work item. Slug is the path id (`AA2004-R1`). Always discovered from that act's SL tab, never from the global SL catalog.

| Column | Type | Description |
|---|---|---|
| `id` | integer | Primary key. |
| `raw_act_id` | integer | FK → `raw_acts`. The authorising act whose SL tab listed this instrument. |
| `slug` | string, unique | Current path id, e.g. `AA2004-R1`. Historical URLs can use an older slug; that lives on the version row. |
| `title` | string | Title from the SL list. |
| `number` | string | SL number, e.g. `Cap. 2, R 1` or `S 946/2024`. |
| `source_url` | text | Current instrument URL. |
| `status` | string | `discovered` / `fetch_failed` / `parse_incomplete` / `complete` / `needs_review`. |

### raw_subsidiary_legislation_versions

One snapshot per SL timeline date. Unique on `(raw_subsidiary_legislation_id, valid_from)`. Same fetch rules as act versions. Not populated until `scrape_subsidiary_legislation`.

| Column | Type | Description |
|---|---|---|
| `id` | integer | Primary key. |
| `raw_subsidiary_legislation_id` | integer | FK → `raw_subsidiary_legislations`. |
| `valid_from` | date | Version date from the timeline (`ValidDate`). |
| `is_current` | boolean | The version in force now. |
| `source_url` | text | Current URL or `/SL/…/Historical/…`. |
| `http_status` | integer, nullable | HTTP status of the fetch. |
| `fetch_status` | string | `not_fetched` / `success` / `not_found` / `error`. |
| `fetch_error` | text, nullable | Error message when the fetch fails. |
| `html` | text, nullable | Full SL HTML after lazy-load. |
| `source_metadata` | jsonb, nullable | TOC vs assembled section/schedule counts. |
| `parse_status` | string | `not_parsed` / `complete` / `incomplete` / `unknown_layout` / `failed`. |
| `expected_provision_count` | integer, nullable | TOC `#pr*` plus `#Sc*` count. |
| `extracted_provision_count` | integer, nullable | Assembled `div.prov1` plus `div.schedule`. |
| `needs_review` | boolean | Stub HTML, count mismatch, or later incomplete parse. |

---

## Knowledge base

Only promoted, complete documents. This is what the product queries.

### Cases

#### courts

Singapore court, keyed by the code in the neutral citation (`SGHC`, `SGCA`, `SGHC(I)`, …).

| Column | Type | Description |
|---|---|---|
| `id` | integer | Primary key. |
| `code` | string, unique | Citation code, e.g. `SGHC`. |
| `name` | string | Display name, e.g. `General Division of the High Court`. |

#### cases

One judgment. `case_number` is the court file number (`Originating Application Nos 1149 of 2025 and 256 of 2026`), not the number inside the neutral citation. Year is not stored; `date` is enough.

| Column | Type | Description |
|---|---|---|
| `id` | integer | Primary key. |
| `court_id` | integer | FK → `courts`. |
| `date` | date | Decision date. |
| `uri` | string, unique | Stable document URI. |
| `title` | string | Case title, e.g. `DVV v DVW and another matter`. |
| `neutral_citation` | string, unique | `[2026] SGHC 164`. |
| `case_number` | string, nullable | Court file number from LawNet `CaseNumber`. |

#### judges

People who sat on the case. LawNet calls this **coram**, not parties.

| Column | Type | Description |
|---|---|---|
| `id` | integer | Primary key. |
| `full_name` | string | Name as given, e.g. `Dedar Singh Gill`. |
| `title` | string, nullable | Judicial title, e.g. `J`. |

#### case_judges

Which judges sat on which case.

| Column | Type | Description |
|---|---|---|
| `case_id` | integer | FK → `cases`. Part of primary key. |
| `judge_id` | integer | FK → `judges`. Part of primary key. |

#### parties

Litigants. Unique on `name` — we only ever have the string. The same company in many cases is one row. Anonymized names such as `DVV` can collide; we cannot disambiguate them.

| Column | Type | Description |
|---|---|---|
| `id` | integer | Primary key. |
| `name` | string, unique | Party name from LawNet `Parties.Party`. |

#### case_parties

| Column | Type | Description |
|---|---|---|
| `case_id` | integer | FK → `cases`. Part of primary key. |
| `party_id` | integer | FK → `parties`. Part of primary key. |
| `role` | string, nullable | Role when LawNet sends it (`applicant`, `respondent`, …). |

#### counsels

Counsel as people, unique on `name`. LawNet sends appearance lines (`Cavinder Bull SC, … (Drew & Napier LLC) for the applicant`), not a list of people. A later parser splits those lines into rows here.

| Column | Type | Description |
|---|---|---|
| `id` | integer | Primary key. |
| `name` | string, unique | Normalized person name. |

#### case_counsels

| Column | Type | Description |
|---|---|---|
| `case_id` | integer | FK → `cases`. Part of primary key. |
| `counsel_id` | integer | FK → `counsels`. Part of primary key. |
| `represents` | string, nullable | Side they appeared for (`applicant`, `respondent`, `appellant`, …). Text, not a closed enum. |

---

### Legislation

Versions exist on SSO for acts and for SL. The knowledge base still has one current SL document. Raw already stores SL versions for when we promote them.

#### acts

The work: the statute as a named thing over time.

| Column | Type | Description |
|---|---|---|
| `id` | integer | Primary key. |
| `uri` | string, unique | Stable act URI. |
| `title` | string | Act title. |

#### act_versions

One row per version of an act. Unique on `(act_id, valid_from)`. At most one `is_current` row per act.

| Column | Type | Description |
|---|---|---|
| `id` | integer | Primary key. |
| `act_id` | integer | FK → `acts`. |
| `uri` | string, unique | URI of this version on SSO. |
| `valid_from` | date | Date this version took effect. |
| `is_current` | boolean | The version in force now. |

#### subsidiary_legislations

Rules, regulations, orders. No version history.

| Column | Type | Description |
|---|---|---|
| `id` | integer | Primary key. |
| `act_id` | integer, nullable | Parent act when known. |
| `uri` | string, unique | Stable SL URI. |
| `title` | string | Title. |
| `number` | string | SL number, e.g. `S 123/2020`. |
| `date` | date | Date of the instrument. |

#### legislative_definitions

Current-act glossary only. Rewritten when the current version is promoted. One row per term on an act.

| Column | Type | Description |
|---|---|---|
| `id` | integer | Primary key. |
| `act_id` | integer | FK → `acts`. |
| `term` | string | Unquoted term, e.g. `accounting corporation`. |
| `definition` | text | The predicate, including `means` / `includes` / `has the meaning given by`. |

Unique on `(act_id, term)`.

---

### Provisions

One tree of provisions per act version or per subsidiary legislation. Exactly one parent document: `act_version_id` or `subsidiary_legislation_id`, not both.

`ordinal` and `descendant_count` are a nested set for outline queries (section plus its children) without walking the tree.

| Column | Type | Description |
|---|---|---|
| `id` | integer | Primary key. |
| `act_version_id` | integer, nullable | FK → `act_versions`. Set for act trees. |
| `subsidiary_legislation_id` | integer, nullable | FK → `subsidiary_legislations`. Set for SL trees. |
| `parent_id` | integer, nullable | FK → `provisions`. Parent node in the same tree. |
| `functional_role_id` | integer, nullable | FK → `functional_roles`. Classification. |
| `uri` | string, unique | Stable provision URI. |
| `kind` | string | `part` / `division` / `subdivision` / `section` / `subsection` / `proviso` / `point` / `opening` / `schedule`. |
| `ordinal` | integer | Nested-set left position in the document. |
| `level` | integer | Depth in the tree. |
| `citation` | string, nullable | Pinpoint, e.g. `s 12(1)`. |
| `heading` | string, nullable | Heading text. |
| `content` | text, nullable | Body text, without amendment chrome. |
| `amendment_note` | text, nullable | SSO `amendNote` text, e.g. `[Act 24 of 2025 wef 06/05/2026]`. |
| `descendant_count` | integer | Size of the subtree, for outline slices. |
| `embedding` | vector(1536), nullable | Embedding of the provision text. |

---

### Paragraphs

Ordered units of a judgment. Classification and references attach here.

| Column | Type | Description |
|---|---|---|
| `id` | integer | Primary key. |
| `case_id` | integer | FK → `cases`. |
| `functional_role_id` | integer, nullable | FK → `functional_roles`. Classification. |
| `uri` | string, unique | Stable paragraph URI. |
| `ordinal` | integer | Order in the judgment. |
| `content` | text | Paragraph text. |
| `embedding` | vector(1536), nullable | Embedding of the paragraph text. |

---

### Taxonomy

Topics contain concepts. Functional roles describe what a paragraph or provision *does* (holding, issue, definition, …), not what it is about.

#### topics

| Column | Type | Description |
|---|---|---|
| `id` | integer | Primary key. |
| `name` | string, unique | Topic name. |
| `description` | text | What this topic covers. |

#### concepts

| Column | Type | Description |
|---|---|---|
| `id` | integer | Primary key. |
| `topic_id` | integer | FK → `topics`. |
| `name` | string, unique | Concept name. |
| `description` | text | What this concept covers. |

#### functional_roles

| Column | Type | Description |
|---|---|---|
| `id` | integer | Primary key. |
| `name` | string, unique | Role name. |
| `description` | text | What this role means. |
| `applies_to` | string | `case` or `legislation`. |

#### Taxonomy junctions

Same shape on all six: two foreign keys, composite primary key.

| Table | Links |
|---|---|
| `case_topics` | `cases` ↔ `topics` |
| `case_concepts` | `cases` ↔ `concepts` |
| `paragraph_topics` | `paragraphs` ↔ `topics` |
| `paragraph_concepts` | `paragraphs` ↔ `concepts` |
| `provision_topics` | `provisions` ↔ `topics` |
| `provision_concepts` | `provisions` ↔ `concepts` |

| Column | Type | Description |
|---|---|---|
| `{left}_id` | integer | FK to the document or node. Part of primary key. |
| `{right}_id` | integer | FK to `topics` or `concepts`. Part of primary key. |

---

### Aliases

Short names used inside one case (`the Act`, `the 2012 Regulations`). Scoped to the case. Unique on `(case_id, short_name)`.

An alias points at a document. A [reference](#references) can then point at this alias and add a pincite.

| Column | Type | Description |
|---|---|---|
| `id` | integer | Primary key. |
| `case_id` | integer | The case that uses this short name. |
| `short_name` | string | The short form as written. |
| `expanded_text` | string | What it expands to. |
| `target_act_id` | integer, nullable | Resolved act, when known. |
| `target_case_id` | integer, nullable | Resolved case, when known. |

---

### References

A citation from a paragraph to an act, provision, case, or paragraph. At most one of the four targets. Zero targets means the cite is still unresolved.

`alias_id` is set when the paragraph used a short name from `aliases`.

| Column | Type | Description |
|---|---|---|
| `id` | integer | Primary key. |
| `source_paragraph_id` | integer | Paragraph that contains the citation. |
| `alias_id` | integer, nullable | Short name used in that paragraph, if any. |
| `target_act_id` | integer, nullable | Cited act. |
| `target_provision_id` | integer, nullable | Cited provision. |
| `target_case_id` | integer, nullable | Cited case. |
| `target_paragraph_id` | integer, nullable | Cited paragraph (pincite). |
| `quoted_text` | text | The citation text as written. |
