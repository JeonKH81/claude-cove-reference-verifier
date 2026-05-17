---
name: cove-reference-verifier
description: Chain-of-Verification (Dhuliawala et al., ACL Findings 2024)을 적용해 학술 원고/리뷰의 reference hallucination을 감지·수정하는 skill. 사용자가 작성한 draft, 참고문헌 리스트(.docx/.md/.txt) 또는 in-text citation을 받아 각 reference의 PMID·DOI·저자·제목·저널·연도·권/페이지·in-text claim을 PubMed 도구 호출의 직접 반환값과 비교해 verified / partial_mismatch / hallucinated / unverifiable로 판정하고 수정 제안을 생성한다. 사용자가 "reference 검증", "참고문헌 hallucination", "이 인용 맞아?", "citation check", "CoVe", "이 reference list 확인해줘", "Vancouver 검증", "PubMed로 검증" 같은 표현을 쓰거나 manuscript draft·reference list 파일을 첨부하면서 검증/사실확인을 요청할 때 반드시 이 skill을 사용하라. 새 문헌을 검색하는 것이 아니라 이미 있는 인용을 검증하는 것이 핵심이다.
---

# CoVe Reference Verifier

본 skill은 LLM이 학술 원고에서 흔히 만들어내는 **reference hallucination**(존재하지 않는 PMID·잘못된 저자·헷갈린 저널·실제 abstract에 없는 주장)을 감지하고 수정하기 위한 도구다. 이론적 근거는 `references/cove_method.md`에 정리된 Chain-of-Verification (Dhuliawala et al., ACL Findings 2024) **Factor+Revise** 변형이다.

## 언제 이 skill이 트리거되어야 하나

- 사용자가 작성한 manuscript draft, review 원고, 학회 발표 자료 등의 **reference 리스트 검증 요청**.
- "이 인용 맞아?", "PMID 진짜 있어?", "이 논문이 실제로 그런 주장을 했어?" 같은 in-text citation 사실확인.
- 다른 LLM(혹은 본인의 이전 draft)이 만들어낸 reference list의 일괄 검증.
- 한국어/영어 모두 지원. Vancouver, AMA, APA 어떤 스타일이든 입력 가능.

이 skill은 **새 문헌을 검색**하지 않는다. 그 작업은 `clinical-research-harness:lit-search` 등 별도 skill의 영역이다.

---

## 핵심 원칙 (반드시 준수)

CoVe 논문 Section 3과 본 skill의 `references/cove_method.md`에서 도출된 4가지 비협상(non-negotiable) 원칙:

1. **Factored execution**: 각 verification question은 **독립된 도구 호출**로 답한다. 이전 답변이나 사용자 원본 reference 텍스트가 prompt context에 같이 들어가지 않게 하라. 함께 들어가면 LLM이 hallucination을 그대로 복제한다 (논문 Section 3.3, Wikidata Joint Prec 0.29 vs Factored 0.32).
2. **Tool-grounded only**: PMID·저자·제목 등은 **PubMed MCP 도구가 직접 반환한 값만** ground truth로 사용한다. 도구가 반환하지 않은 정보는 LLM이 "기억해서" 채워 넣지 말 것. 도구 실패 시 `unverifiable`로 표시한다.
3. **Open question**: yes/no verification 사용 금지. "Is the author X?" 대신 "Who are the authors?" (논문 Table 4: open 0.22 > yes/no 0.19).
4. **Atomic 분해**: 한 reference에 대해 최대 6개 atomic question으로 분해. Shortform이 longform보다 정확 (논문 Section 4.3: 17% → 70%).

이 원칙을 어기면 본 skill의 검증 결과는 신뢰할 수 없다.

---

## Workflow (Factor+Revise 4 phases)

본 skill은 CoVe 논문의 4단계를 reference 검증 도메인에 맞게 구체화한 것이다.

### Phase 1 — Baseline Parsing (논문 Step 1: Generate Baseline)

사용자의 입력에서 reference 항목을 추출하고 atomic field로 분해한다.

```bash
python scripts/parse_references.py <input_path> -o /tmp/refs.json
# 또는 stdin:
cat manuscript.md | python scripts/parse_references.py
```

지원 입력:
- `.docx` (References / 참고문헌 섹션 자동 추출, python-docx 사용)
- `.md` / `.txt` / `.tex`
- raw text via stdin
- 사용자가 chat에 붙여넣은 reference list

산출물 (JSON):
```json
[{"idx":1, "raw":"...", "pmid":"...", "doi":"...", "authors":[...], "title":"...",
  "journal":"...", "year":"...", "volume":"...", "pages":"...", "in_text_claim": null}, ...]
```

**In-text claim 추출 (선택)**: 사용자가 manuscript 본문도 제공한 경우, 각 reference 번호가 인용된 문장을 함께 추출하여 `in_text_claim` 필드에 채운다 (Phase 3 Q6에서 사용).

### Phase 2 — Plan Verifications (논문 Step 2)

각 reference `R_i`에 대해 **6종 atomic question**을 자동 생성한다 (`references/verification_prompts.md` 참조):

| # | Question | Tool | 필수성 |
|---|----------|------|--------|
| Q1 | `R_i.pmid`/`R_i.doi`가 PubMed에 존재하는가? | **반드시** `get_article_metadata` (DOI는 먼저 `convert_article_ids`로 PMID 변환 후) — `convert_article_ids`는 존재하지 않는 ID도 echo해서 돌려주므로 단독으로 쓰면 안 됨 | 필수 |
| Q2 | Title+1st author로 역검색 시 PMID가 일치하는가? | `mcp__d2d22bd4-...__search_articles` | 필수 (PMID 없을 때 핵심) |
| Q3 | (확정 PMID의) 저자는? | `get_article_metadata` | 필수 |
| Q4 | (확정 PMID의) 저널·연도는? | `get_article_metadata` | 필수 |
| Q5 | (확정 PMID의) 권·페이지는? | `get_article_metadata` | optional |
| Q6 | abstract가 사용자의 in-text claim을 지지하는가? | `get_article_metadata` (abstract) + LLM 판정 | claim 있으면 필수 |

### Phase 3 — Execute Verifications (논문 Step 3, **Factored**)

**중요**: 각 question은 **독립 호출**이다. 한 reference에 대해 Q1→Q2→Q3...를 처리할 때 Q3 prompt에 사용자 원본 reference 텍스트를 함께 넣지 말라. 오직 atomic 단위 입력(예: `pmid="38123456"`)과 도구 출력만 사용한다.

병렬화 권장: 여러 reference의 Q1을 한 번에 (가능하면 batch) 호출한 뒤, 다음으로 모든 Q2를 호출하는 방식. 본 skill에서는 reference별로 순차 처리해도 무방하지만 30개 이상이면 병렬화 고려.

**Q1/Q2 결합 전략**:
- Case A: `pmid` 또는 `doi`가 있으면 Q1 우선 → 매핑 성공 시 그 PMID를 verified_pmid로 채택.
- Case B: `pmid`/`doi` 둘 다 없으면 Q2 (title+author 역검색)로 verified_pmid 결정.
- Case C: Q1 실패 + Q2 결과 0건 → 잠재적 hallucination. Q3–Q6 스킵하고 verdict=`hallucinated`로 직행.
- Case D: Q1과 Q2가 서로 다른 PMID를 가리킴 → DOI/PMID가 잘못 적혔거나 hallucination. Q2 결과를 우선하되 `notes`에 표시.

**Q6 Claim verification**: `references/verification_prompts.md`의 claim_check_prompt를 그대로 사용한다. 결과는 4단계: `supported` / `partially_supported` / `not_in_abstract` / `contradicted`.

### Phase 4 — Cross-check & Final Verified Response (논문 Step 4, Factor+**Revise**)

Reference별로 별도 cross-check prompt를 실행하여 verdict와 field-level diff를 확정 (`references/verification_prompts.md`의 cross-check 섹션). Verdict 규칙:

```
hallucinated     ← (Q1 실패 AND Q2 미발견) OR (title 유사도 < 0.4)
unverifiable     ← Q1·Q2 모두 도구 오류/미호출
partial_mismatch ← Q1 성공이지만 author/title/year/journal 중 ≥1개 mismatch
                   OR claim_support ∈ {partially_supported, not_in_abstract}
verified         ← Q1 성공 AND 모든 field match=True
                   AND claim_support ∈ {supported, null}
```

각 reference의 cross-check 결과를 모아 verifications JSON 배열로 저장한 뒤 리포트를 렌더링:

```bash
python scripts/render_report.py /tmp/verifications.json \
  --md /tmp/report.md \
  --docx "/Users/kh_jeon/Documents/Claude/Projects/FASTCAMPUS II/reference_verification_report.docx"
```

리포트는 다음을 포함:
- Summary 통계 (verified / partial / hallucinated / unverifiable 수)
- Reference별 field-level 비교 표 (User vs PubMed)
- Claim support 결과 (해당 시)
- **Vancouver 스타일로 재구성된 corrected citation**
- Methodology note 및 한계

---

## 사용 예시 (사용자 → assistant 대화 흐름)

### 예시 1 — Manuscript draft에서 reference 검증

> 사용자: "이 review 원고 reference 한번 봐줘. ChatGPT한테 시킨 거라 의심돼."

1. 첨부된 `.docx`를 `parse_references.py`로 파싱 → atomic field JSON.
2. 각 reference에 대해 Phase 2 question 자동 생성 (필요시 사용자에게 in-text claim까지 검증할지 물음).
3. Phase 3 Factored execution: PubMed MCP 도구를 reference 수만큼 호출.
4. Phase 4 cross-check 후 `render_report.py`로 .docx 리포트 생성.
5. 최종 응답: summary 수치 + 가장 위험한 항목 3-5개 미리보기 + .docx 링크.

### 예시 2 — In-chat reference list 검증

> 사용자가 chat에 5개 reference를 붙여넣고 "이거 진짜 있는 논문이야?"

1. 텍스트를 `parse_references.py`에 stdin으로 흘려 atomic field JSON.
2. Phase 3에서 Q1+Q2만 (claim 없으므로 Q6 생략).
3. Phase 4 verdict와 corrected_citation_vancouver를 chat에 표 형태로 직접 출력.

### 예시 3 — DOI만 잔뜩 있는 list

DOI는 `convert_article_ids`로 PMID 변환 → 이후 동일 워크플로우.

---

## 도구 의존성

**필수**:
- PubMed MCP (Cowork mode에서 자동 로드되는 `mcp__d2d22bd4-...` 도구군):
  - `search_articles`, `get_article_metadata`, `convert_article_ids`,
    `lookup_article_by_citation`, `find_related_articles`
- Python 3.10+ 표준 라이브러리

**조건부**:
- `python-docx` (입력이 .docx거나 .docx 리포트가 필요할 때)
  - 설치: `pip install --break-system-packages python-docx`
- `WebSearch` (선택, PubMed에 색인되지 않은 conference proceedings/preprint 검증 fallback)
  - PubMed에서 못 찾으면 verdict=`unverifiable`로 두는 것이 기본. WebSearch fallback은 사용자가 명시적으로 요청할 때만.

---

## 한계와 주의 (사용자에게 반드시 고지)

CoVe 논문 Limitations 섹션을 그대로 적용:

1. **CoVe는 hallucination을 완전히 제거하지 않는다** — 감소시킬 뿐. 모든 `partial_mismatch` / `hallucinated` 항목은 사용자가 직접 원본을 확인해야 한다.
2. **PubMed 외 문헌은 검증되지 않는다**: Late-breaking conference proceedings, preprint(arXiv·medRxiv 일부), 단행본, gray literature는 보통 PubMed에 없다 → `unverifiable`로 표시.
3. **Claim support는 abstract 기반**이다. Full-text가 필요한 세밀한 주장은 `not_in_abstract`로 표시되며, 이는 "거짓"이 아니라 "이 도구로는 abstract만 봤다"는 뜻이다.
4. **Title 유사도 기반 매칭의 false positive**: 매우 유사한 제목의 다른 논문이 매칭될 수 있다. cross-check에서 author/year까지 일치하는지 반드시 확인.
5. **추가 비용**: CoVe(factor+revise)는 reference 1개당 최대 6번의 도구 호출 + 1회의 cross-check LLM 호출이 필요하다 (논문 Table 5: 1+s+2f). 30개 reference는 약 200회 호출.

---

## 파일 구조

```
claude-cove-reference-verifier/
├── .claude-plugin/
│   └── plugin.json                       # 플러그인 메타데이터
├── skills/
│   └── cove-reference-verifier/
│       └── SKILL.md                      # 본 파일
├── references/
│   ├── cove_method.md                    # CoVe 논문 핵심 + 본 skill의 4 원칙
│   └── verification_prompts.md           # Q1–Q6 + cross-check prompt 템플릿
├── scripts/
│   ├── parse_references.py               # Phase 1: docx/md/txt → atomic JSON
│   └── render_report.py                  # Phase 4: verifications.json → md/docx 리포트
└── examples/
    └── (검증 예시 파일들)
```

---

## 참고

본 skill의 모든 검증 로직은 다음 논문에서 도출되었으며, 결과 리포트에서도 명시적으로 인용된다:

> Dhuliawala S, Komeili M, Xu J, Raileanu R, Li X, Celikyilmaz A, Weston J. **Chain-of-Verification Reduces Hallucination in Large Language Models.** Findings of the Association for Computational Linguistics: ACL 2024, pages 3563–3578.
