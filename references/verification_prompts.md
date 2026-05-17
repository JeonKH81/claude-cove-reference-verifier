# Verification Prompt 템플릿

본 문서는 SKILL.md의 Phase 2 (Plan Verifications)와 Phase 3 (Execute Verifications, Factored)에서 호출하는 표준 prompt들을 정의한다. 각 prompt는 **단일 atomic fact**만을 묻고, **원본 reference 문자열은 절대 prompt에 포함하지 않는다** (Factored 원칙).

---

## Phase 2 — 한 개의 reference에 대해 자동으로 던질 질문 6종

각 reference 항목 `R_i = {pmid?, doi?, authors, title, journal, year, volume?, pages?, in-text claim?}`에 대해 다음 6가지를 자동 생성한다.

| # | Question type | 묻는 atomic fact | 도구 |
|---|---------------|------------------|------|
| Q1 | **Existence** | `R_i.pmid` 또는 `R_i.doi`가 PubMed에 실제로 존재하는가? | `mcp__pubmed__convert_article_ids` 또는 `get_article_metadata` |
| Q2 | **Title-Author retrieval** | "Title=`R_i.title`, First author=`R_i.authors[0]`"로 검색 시 PMID가 일치하는가? | `mcp__pubmed__search_articles` |
| Q3 | **Authorship** | (Q1/Q2에서 얻은 정식 PMID의) authors는? | `get_article_metadata` |
| Q4 | **Journal/Year** | (정식 PMID의) journal과 publication year는? | `get_article_metadata` |
| Q5 | **Volume/Pages** (선택) | (정식 PMID의) volume과 pages는? | `get_article_metadata` |
| Q6 | **Claim verification** (선택) | "사용자가 인용한 in-text claim이 abstract에 명시적으로 등장하는가?" | `get_article_metadata` (abstract) → LLM 일치성 판단 |

**중요**: Q3–Q6의 prompt는 사용자가 적은 원본 author/title/journal/year를 **포함하지 않는다**. 오직 정식 PMID만 전달한다. 이것이 Factored의 핵심.

---

## Phase 3 — Execute Verifications (Factored, 독립 prompt)

### Q1 prompt template (Existence)

**중요 도구 동작**: PubMed MCP의 `convert_article_ids`는 입력 PMID가 존재하지 않아도 같은 PMID를 echo해서 돌려준다. 따라서 단독으로는 존재 검증에 부적합하다. 반드시 `get_article_metadata`의 응답에 해당 PMID가 실제로 들어있는지 (`count` 필드 또는 articles 배열에 매핑이 있는지) 확인해야 한다.

```
입력 PMID인 경우:
  도구 호출:
    mcp__pubmed__get_article_metadata(pmids=["{pmid}"])
  해석:
    - articles 배열에 해당 pmid가 있으면 → exists=True
    - count=0 또는 articles가 비어있으면 → exists=False (잠재적 hallucination)

입력 DOI인 경우:
  Step A: mcp__pubmed__convert_article_ids(ids=["{doi}"], id_type="doi")
          → 매핑된 pmid를 추출
  Step B: 위에서 얻은 pmid로 get_article_metadata 재호출하여 실제 존재 확인
```

### Q2 prompt template (Title+Author 역검색)

```
도구 호출:
  mcp__pubmed__search_articles(
    query='"{title}" AND {first_author_lastname}[au]',
    max_results=5
  )

해석:
  - 첫 결과의 title 유사도 ≥ 0.85 (Jaro-Winkler) → matched_pmid
  - 결과 0건 또는 유사도 < 0.6 → not_found (잠재적 hallucination)
```

### Q3 prompt template (Authors)

```
도구 호출:
  mcp__pubmed__get_article_metadata(pmid="{verified_pmid}")

LLM 판단:
  Question: "What are the listed authors for this PubMed article?"
  주의: 원래 사용자 reference의 author 문자열을 prompt에 넣지 말 것.
  도구가 반환한 authors 배열만 ground truth로 사용한다.
```

### Q4 prompt template (Journal/Year)

```
도구 호출:
  mcp__pubmed__get_article_metadata(pmid="{verified_pmid}")

LLM 판단:
  Question: "What journal published this article and in what year?"
  주의: 사용자 입력의 journal/year를 prompt에 넣지 말 것.
```

### Q5 prompt template (Volume/Pages, optional)

Q4와 동일한 도구 호출 결과에서 volume/pages 필드만 추출.

### Q6 prompt template (Claim verification, optional)

```
도구 호출:
  mcp__pubmed__get_article_metadata(pmid="{verified_pmid}")  → abstract 필드

LLM 판단 (claim_check_prompt):
  System: "당신은 의학 논문 fact-checker입니다. 다음 abstract만을 근거로 판단하세요. abstract에 없는 사실은 모두 'not in abstract'로 응답하세요."
  
  Abstract:
  {abstract_text}
  
  Claim to verify (사용자가 본문에서 이 reference를 인용하면서 한 주장):
  {in_text_claim}
  
  Question: "Does this abstract explicitly support the claim above?
  Answer with one of:
    - 'supported' (claim is directly stated or strongly implied)
    - 'partially_supported' (related but not the exact claim)
    - 'not_in_abstract' (no support in abstract; full text needed)
    - 'contradicted' (abstract states the opposite)"
```

---

## Phase 4 — Cross-check (Revise) prompt

Factor+Revise의 **별도 cross-check 단계** (논문 Section 3.3 Factor+Revise). 각 reference 단위로 다음을 수행한다.

```
System:
"당신은 학술 인용 검증자입니다. 사용자가 작성한 reference와 PubMed가 반환한 ground truth를 비교하여 mismatch를 표시하세요. PubMed 결과가 없으면 'no_pubmed_evidence'로 표시하세요."

Input:
  user_reference = {
    "raw_text": "{원본 reference 문자열}",
    "parsed": {pmid, doi, authors, title, journal, year, volume, pages}
  }
  pubmed_ground_truth = {
    "pmid": "{verified_pmid or null}",
    "authors": [...],
    "title": "...",
    "journal": "...",
    "year": "...",
    "volume": "...",
    "pages": "...",
    "abstract": "..."
  }
  claim_check_result = "supported|partially_supported|not_in_abstract|contradicted|null"

Output (strict JSON):
{
  "verdict": "verified|partial_mismatch|hallucinated|unverifiable",
  "field_diffs": {
    "pmid":   {"user": "...", "ground_truth": "...", "match": true|false},
    "authors":{"user": "...", "ground_truth": "...", "match": true|false},
    "title":  {"user": "...", "ground_truth": "...", "match": true|false},
    "journal":{"user": "...", "ground_truth": "...", "match": true|false},
    "year":   {"user": "...", "ground_truth": "...", "match": true|false},
    "volume": {"user": "...", "ground_truth": "...", "match": true|false},
    "pages":  {"user": "...", "ground_truth": "...", "match": true|false}
  },
  "claim_support": "supported|partially_supported|not_in_abstract|contradicted|null",
  "corrected_citation_vancouver": "{ground_truth로부터 재구성한 Vancouver style 인용}",
  "notes": "{검증자 코멘트, 1-2문장}"
}
```

### Verdict 결정 규칙

```
hallucinated   ← (Q1=False AND Q2=not_found) OR (title 유사도 < 0.4)
unverifiable   ← (Q1=null AND Q2=null)  # 도구 미호출/오류
partial_mismatch ← Q1=True 이지만 author/title/year 중 ≥1개 mismatch
                 OR claim_support ∈ {partially_supported, not_in_abstract}
verified       ← Q1=True AND 모든 field match=True
                 AND claim_support ∈ {supported, null}
```

---

## 절대 금지 사항 (Citation Grounding 정책)

- LLM이 PMID를 "기억"해서 채우지 말 것 — 반드시 도구가 반환한 값만 사용.
- 도구 호출이 실패한 경우 추정 금지. `unverifiable`로 표시.
- 사용자 원본 reference 텍스트를 verification prompt에 넣지 말 것 (atomic field만 전달).
- Yes/No verification 사용 금지 — open question 전용.
