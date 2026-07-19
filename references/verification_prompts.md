# Verification Prompt 템플릿

본 문서는 SKILL.md의 Phase 2 (Plan Verifications)와 Phase 3 (Execute Verifications, Factored)에서 호출하는 표준 prompt들을 정의한다. 각 verification prompt(Q1–Q7)는 **단일 atomic fact**만을 묻고, **원본 reference 문자열을 포함하지 않는다** (Factored 원칙 — 원문을 같이 넣으면 LLM이 hallucination을 복제한다).

> **범위 구분**: 이 "원문 미포함" 규칙은 **Q1–Q7 verification prompt에만** 적용된다. Phase 4의 **cross-check(Revise) prompt는 예외**로, user reference 원문과 PubMed ground truth를 **대조**하는 것이 그 단계의 목적이므로 `raw_text`(원본 문자열)를 의도적으로 입력받는다(아래 Phase 4 참조). 즉 "절대 금지"는 Q1–Q7 한정이고, cross-check은 허용이다.

---

## Phase 2 — 한 개의 reference에 대해 자동으로 던질 질문 7종

각 reference 항목 `R_i = {pmid?, doi?, authors, title, journal, year, volume?, pages?, in_text_claim?, in_text_context?}`에 대해 다음 7가지를 자동 생성한다.

| # | Question type | 묻는 atomic fact | 도구 | 조건 |
|---|---------------|------------------|------|------|
| Q1 | **Existence** | `R_i.pmid` 또는 `R_i.doi`가 PubMed에 실제로 존재하는가? | `get_article_metadata` | 필수 |
| Q2 | **Title-Author retrieval** | "Title=`R_i.title`, First author=`R_i.authors[0]`"로 검색 시 PMID가 일치하는가? | `search_articles` | 필수 |
| Q3 | **Authorship** | (Q1/Q2에서 얻은 정식 PMID의) authors는? | `get_article_metadata` | 필수 |
| Q4 | **Journal/Year** | (정식 PMID의) journal과 publication year는? | `get_article_metadata` | 필수 |
| Q5 | **Volume/Pages** | (정식 PMID의) volume과 pages는? | `get_article_metadata` | 선택 |
| Q6 | **Claim support** | in_text_claim이 abstract에 명시적으로 등장하는가? | `get_article_metadata` (abstract) + LLM | in_text_claim 있을 때 |
| Q7 | **Citation appropriateness** | 단락 맥락에서 이 인용이 적절하고 충실한가? | `get_article_metadata` (abstract) + LLM | in_text_context 있을 때 |

**핵심 원칙**: Q3–Q7의 prompt는 사용자가 적은 원본 author/title/journal/year를 **포함하지 않는다**. 오직 정식 PMID만 전달한다.

**Q7 활성화 조건**: 원고 본문이 함께 제공된 경우에만 실행. reference list만 있으면 Q1-Q6까지만 수행.

---

## Phase 3 — Execute Verifications (Factored, 독립 prompt)

> **도구 표기 규약**: 아래 템플릿의 `mcp__pubmed__*`는 **기능 이름을 가리키는 표기**이지 실제 도구 ID가 아니다. 실제 접두사는 환경마다 다르므로(`mcp__claude_ai_PubMed__*` 등) 사용 가능한 도구 목록에서 PubMed 계열을 찾아 대응시킨다.
>
> MCP가 아예 없는 환경에서는 번들 폴백으로 대체한다 — 반환 필드 모양이 같으므로 아래 해석 규칙은 그대로 적용된다:
>
> | 템플릿의 호출 | 폴백 대체 |
> |---|---|
> | `get_article_metadata(pmids=[...])` | `pubmed_lookup.py --pmids ...` |
> | `convert_article_ids(ids=[doi])` | `pubmed_lookup.py --doi ...` |
> | `search_articles(...)` | `pubmed_lookup.py --search ...` |
> | `lookup_article_by_citation(...)` | `pubmed_lookup.py --citation "저널\|연도\|권\|시작쪽\|저자"` |
>
> 폴백은 존재하지 않는 PMID를 `not_found[]`로 분리해 돌려준다(echo 안 함). 조회 자체가 실패하면 exit code 2 + `{"status": "lookup_failed"}` — 이때는 `hallucinated`가 아니라 `unverifiable`이다.

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
  mcp__pubmed__get_article_metadata(pmids=["{verified_pmid}"])

LLM 판단:
  Question: "What are the listed authors for this PubMed article?"
  주의: 원래 사용자 reference의 author 문자열을 prompt에 넣지 말 것.
  도구가 반환한 authors 배열만 ground truth로 사용한다.
```

### Q4 prompt template (Journal/Year)

```
도구 호출:
  mcp__pubmed__get_article_metadata(pmids=["{verified_pmid}"])

LLM 판단:
  Question: "What journal published this article and in what year?"
  주의: 사용자 입력의 journal/year를 prompt에 넣지 말 것.
```

### Q5 prompt template (Volume/Pages, optional)

Q4와 동일한 도구 호출 결과에서 volume/pages 필드만 추출.

### Q6 prompt template (Claim verification, optional)

```
도구 호출:
  mcp__pubmed__get_article_metadata(pmids=["{verified_pmid}"])  → abstract 필드

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

### Q7 prompt template (Citation Appropriateness, in_text_context 있을 때만)

Q7은 "이 논문이 존재하는가"가 아니라 "이 논문이 이 맥락에서 적절하게 인용됐는가"를 판단한다. 4개 sub-dimension을 독립적으로 평가한다.

**중요**: 이 prompt에는 abstract(도구 반환값)와 in_text_context(단락 원문)만 들어간다. 원본 reference 문자열이나 파싱된 metadata는 포함하지 않는다.

```
도구 호출:
  mcp__pubmed__get_article_metadata(pmids=["{verified_pmid}"])  → abstract, title 필드

LLM 판단 (appropriateness_check_prompt):
  System:
  "당신은 의학 논문 인용 적절성 검토자입니다.
  아래 [Abstract]와 [Manuscript paragraph]만을 근거로 판단하세요.
  abstract 또는 단락에 없는 정보는 추론하거나 기억으로 채우지 마세요."

  [Abstract of cited paper]
  {abstract_text}

  [Manuscript paragraph where citation appears]
  {in_text_context}

  다음 4가지 항목을 각각 독립적으로 평가하세요:

  1. FAITHFULNESS — 저자가 이 논문의 내용을 충실하게 표현했는가?
     - 'accurate': 단락의 표현이 abstract 내용과 일치함
     - 'exaggerated': 논문보다 강한 결론으로 표현됨 (예: "경향" → "증명")
     - 'understated': 논문보다 약하게 표현됨
     - 'misrepresented': 핵심 내용이 왜곡됨

  2. DIRECTION — 발견의 방향(효과 방향, 연관성 방향)이 맞는가?
     - 'correct': 방향이 일치함
     - 'reversed': 반대 방향으로 인용됨 (예: 감소 → 증가)
     - 'not_applicable': 방향성이 없는 논문 (methods, review 등)

  3. SCOPE — 인용된 증거의 범위/확실성 수준이 적절한가?
     - 'appropriate': 연구 규모·설계가 맥락에 맞게 인용됨
     - 'overstated': 파일럿/소규모 연구를 확립된 근거처럼 인용
     - 'understated': 강한 근거를 약하게 표현

  4. PURPOSE — 이 논문이 단락의 목적에 맞게 인용됐는가?
     - 'appropriate': 방법론, 근거, 배경 등 인용 목적이 논문 성격과 일치
     - 'misaligned': 예를 들어 방법론 논문을 임상 근거로, 동물실험을 인체 근거로 인용

  각 항목에 대해 verdict와 1문장 explanation을 반환하세요.
  Overall: 4항목 중 하나라도 문제가 있으면 'concern', 모두 적절하면 'appropriate'.
  Overall이 'concern'이면 severity도 판정: 'minor'(표현 차이) 또는 'major'(방향 오류/왜곡).

  JSON으로 반환:
  {
    "faithfulness": {"verdict": "...", "explanation": "..."},
    "direction":    {"verdict": "...", "explanation": "..."},
    "scope":        {"verdict": "...", "explanation": "..."},
    "purpose":      {"verdict": "...", "explanation": "..."},
    "overall":      "appropriate|concern",
    "severity":     "none|minor|major",
    "summary":      "1-2문장 종합 의견"
  }
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

**존재·메타데이터 verdict와 claim-support는 분리한다.** `not_in_abstract`는 "거짓"이 아니라 "abstract만으로는 확인 불가(full text 필요)"이므로 메타데이터가 일치하는 정상 reference를 강등시키지 않는다.

```
hallucinated   ← (Q1=False AND Q2=not_found) OR (title 유사도 < 0.4)
unverifiable   ← (Q1=null AND Q2=null)  # 도구 미호출/오류
partial_mismatch ← Q1=True 이지만 author/title/year 중 ≥1개 mismatch
                 OR claim_support ∈ {partially_supported, contradicted}
                 # not_in_abstract는 강등 사유 아님
verified       ← Q1=True AND 모든 field match=True
                 AND claim_support ∈ {supported, not_in_abstract, null}
                 # not_in_abstract면 verdict=verified, 단 claim_support 값은 보존해 "full text 필요" 배지로 표기
```

---

## 절대 금지 사항 (Citation Grounding 정책)

- LLM이 PMID를 "기억"해서 채우지 말 것 — 반드시 도구가 반환한 값만 사용.
- 도구 호출이 실패한 경우 추정 금지. `unverifiable`로 표시.
- 사용자 원본 reference 텍스트를 **Q1–Q7 verification prompt**에 넣지 말 것 (atomic field만 전달). 단 Phase 4 **cross-check prompt는 예외** — 원문 대조가 목적이므로 `raw_text`를 허용한다.
- Yes/No verification 사용 금지 — open question 전용.
