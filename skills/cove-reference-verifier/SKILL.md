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

1. **Factored execution**: 각 verification question(Q1–Q7)은 **독립된 도구 호출**로 답한다. 이전 답변이나 사용자 원본 reference 텍스트가 verification prompt context에 같이 들어가지 않게 하라. 함께 들어가면 LLM이 hallucination을 그대로 복제한다 (논문 Section 3.3, Wikidata Joint Prec 0.29 vs Factored 0.32). (단 Phase 4 cross-check은 원문 대조가 목적인 별도 단계이므로 이 규칙의 예외다 — Phase 4 참조.)
2. **Tool-grounded only**: PMID·저자·제목 등은 **PubMed MCP 도구가 직접 반환한 값만** ground truth로 사용한다. 도구가 반환하지 않은 정보는 LLM이 "기억해서" 채워 넣지 말 것. 도구 실패 시 `unverifiable`로 표시한다.
3. **Open question**: yes/no verification 사용 금지. "Is the author X?" 대신 "Who are the authors?" (논문 Table 4: open 0.22 > yes/no 0.19).
4. **Atomic 분해**: 한 reference에 대해 최대 6개 atomic question으로 분해. Shortform이 longform보다 정확 (논문 Section 4.3: 17% → 70%).

이 원칙을 어기면 본 skill의 검증 결과는 신뢰할 수 없다.

---

## Workflow (Factor+Revise 4 phases)

본 skill은 CoVe 논문의 4단계를 reference 검증 도메인에 맞게 구체화한 것이다.

### Phase 1 — Baseline Parsing (논문 Step 1: Generate Baseline)

사용자의 입력에서 reference 항목을 추출하고 atomic field로 분해한다.

> **경로 주의**: `scripts/`·`references/`는 이 SKILL.md가 있는 `skills/cove-reference-verifier/`가 아니라 **플러그인 루트**에 있다. 스킬 실행 시 작업 디렉터리가 플러그인 루트라는 보장이 없으므로, 아래 예시처럼 플러그인 루트를 가리키는 `${CLAUDE_PLUGIN_ROOT}`로 절대경로화해 호출한다. (해당 환경변수가 없는 수동 실행 시에는 플러그인 루트 경로로 치환.)

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/parse_references.py" <input_path> -o /tmp/refs.json
# 또는 stdin:
cat manuscript.md | python "${CLAUDE_PLUGIN_ROOT}/scripts/parse_references.py"
```

지원 입력:
- `.docx` (References / 참고문헌 섹션 자동 추출, python-docx 사용)
- `.md` / `.txt` / `.tex`
- raw text via stdin
- 사용자가 chat에 붙여넣은 reference list

산출물 (JSON):
```json
[{"idx":1, "raw":"...", "pmid":"...", "doi":"...", "authors":[...], "title":"...",
  "journal":"...", "year":"...", "volume":"...", "pages":"...",
  "in_text_claim": null, "in_text_context": null}, ...]
```

**In-text claim + context 추출 (선택)**: 사용자가 manuscript 본문도 제공한 경우 아래 스크립트를 실행한다.

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/extract_claims.py" <manuscript_path> --refs /tmp/refs.json -o /tmp/refs.json
```

- `in_text_claim`: citation이 등장하는 문장 (citation 마크업 제거). Q6에서 사용.
- `in_text_context`: 해당 문장이 속한 단락 전체. **Q7 Citation Appropriateness 검토에 사용.**

**reference list만 제공된 경우**: 두 필드 모두 null로 유지하고 Q1–Q6만 수행한다. Q7은 건너뛴다.

### Phase 2 — Plan Verifications (논문 Step 2)

각 reference `R_i`에 대해 **최대 7종 atomic question**을 자동 생성한다 (`references/verification_prompts.md` 참조):

| # | Question | Tool | 조건 |
|---|----------|------|------|
| Q1 | `R_i.pmid`/`R_i.doi`가 PubMed에 존재하는가? | `get_article_metadata` (DOI는 먼저 `convert_article_ids` 변환 후 재확인) | 필수 |
| Q2 | Title+1st author로 역검색 시 PMID가 일치하는가? | `search_articles` | 필수 |
| Q3 | (확정 PMID의) 저자는? | `get_article_metadata` | 필수 |
| Q4 | (확정 PMID의) 저널·연도는? | `get_article_metadata` | 필수 |
| Q5 | (확정 PMID의) 권·페이지는? | `get_article_metadata` | 선택 |
| Q6 | abstract가 in-text claim을 지지하는가? | `get_article_metadata` (abstract) + LLM | in_text_claim 있을 때 |
| Q7 | 단락 맥락에서 이 인용이 적절하고 충실한가? | `get_article_metadata` (abstract) + LLM | in_text_context 있을 때 |

**Q7 (Citation Appropriateness)**: 논문이 존재하는지와는 별개로, 저자가 그 논문을 올바른 맥락에서, 충실하게, 적절한 목적으로 인용했는지를 4개 sub-dimension(faithfulness / direction / scope / purpose)으로 판정한다. 원고 본문 없이 reference list만 들어온 경우 자동으로 건너뛴다.

### Phase 3 — Execute Verifications (논문 Step 3, **Factored**)

**중요**: 각 question은 **독립 호출**이다. 한 reference에 대해 Q1→Q2→Q3...를 처리할 때 Q3 prompt에 사용자 원본 reference 텍스트를 함께 넣지 말라. 오직 atomic 단위 입력(예: `pmid="38123456"`)과 도구 출력만 사용한다.

병렬화 권장: 여러 reference의 Q1을 한 번에 (가능하면 batch) 호출한 뒤, 다음으로 모든 Q2를 호출하는 방식. 본 skill에서는 reference별로 순차 처리해도 무방하지만 30개 이상이면 병렬화 고려.

**Q7 실행**: `in_text_context`가 있는 reference에 대해, Q3-Q6와 동일한 도구 호출(abstract 포함)로 얻은 abstract + in_text_context를 입력으로 `references/verification_prompts.md`의 appropriateness_check_prompt를 실행한다. 원본 reference 문자열이나 parsed metadata는 이 prompt에 포함하지 않는다.

**실행 후 저장**: 각 reference의 Q1–Q7 결과와 cross-check 결과를 다음 구조로 메모리에 누적하고, 전체가 끝난 뒤 `/tmp/verifications.json`에 저장한다. Claude가 직접 이 JSON을 생성해야 하며, 도구 호출 결과를 빠짐없이 반영해야 한다.

```json
[
  {
    "idx": 1,
    "raw": "...",
    "verified_pmid": "12345678",
    "verdict": "verified|partial_mismatch|hallucinated|unverifiable",
    "field_diffs": { "pmid": {"user":"...", "ground_truth":"...", "match": true}, ... },
    "claim_support": null,
    "citation_appropriateness": {
      "faithfulness": {"verdict": "accurate", "explanation": "..."},
      "direction":    {"verdict": "correct",  "explanation": "..."},
      "scope":        {"verdict": "appropriate", "explanation": "..."},
      "purpose":      {"verdict": "appropriate", "explanation": "..."},
      "overall": "appropriate",
      "severity": "none",
      "summary": "..."
    },
    "corrected_citation_vancouver": "...",
    "notes": "..."
  }
]
```

`citation_appropriateness`는 `in_text_context`가 null인 경우 필드 자체를 null로 둔다.

**Q1/Q2 결합 전략**:
- Case A: `pmid` 또는 `doi`가 있으면 Q1 우선 → 매핑 성공 시 그 PMID를 verified_pmid로 채택.
- Case B: `pmid`/`doi` 둘 다 없으면 Q2 (title+author 역검색)로 verified_pmid 결정.
- Case C: Q1 실패 + Q2 결과 0건 → 잠재적 hallucination. Q3–Q6 스킵하고 verdict=`hallucinated`로 직행.
- Case D: Q1과 Q2가 서로 다른 PMID를 가리킴 → DOI/PMID가 잘못 적혔거나 hallucination. Q2 결과를 우선하되 `notes`에 표시.

**Q6 Claim verification**: `references/verification_prompts.md`의 claim_check_prompt를 그대로 사용한다. 결과는 4단계: `supported` / `partially_supported` / `not_in_abstract` / `contradicted`.

### Phase 4 — Cross-check & Final Verified Response (논문 Step 4, Factor+**Revise**)

Reference별로 별도 cross-check prompt를 실행하여 verdict와 field-level diff를 확정 (`references/verification_prompts.md`의 cross-check 섹션).

**중요 — 두 층위의 verdict를 분리한다**: ① reference의 **존재·메타데이터** 정확성(verdict)과 ② 본문 주장에 대한 **claim-support**(claim_support 필드)는 별개다. 특히 `claim_support=not_in_abstract`는 "거짓"이 아니라 "abstract만으로는 확인 불가(full text 필요)"라는 뜻이므로, **메타데이터가 모두 일치하는 정상 reference를 `partial_mismatch`로 강등시키지 않는다.** 이 경우 verdict는 `verified`로 두되 `claim_support`에 `not_in_abstract`를 그대로 보존해 리포트에서 "abstract로 확인 불가" 배지로 표기한다.

Verdict 규칙:

```
hallucinated     ← (Q1 실패 AND Q2 미발견) OR (title 유사도 < 0.4)
unverifiable     ← Q1·Q2 모두 도구 오류/미호출
partial_mismatch ← Q1 성공이지만 author/title/year/journal 중 ≥1개 mismatch
                   OR claim_support ∈ {partially_supported, contradicted}
                   (※ not_in_abstract는 강등 사유에서 제외 — 아래 verified 참조)
verified         ← Q1 성공 AND 모든 field match=True
                   AND claim_support ∈ {supported, not_in_abstract, null}
                   (※ not_in_abstract면 verdict는 verified이되 claim_support 필드에
                      값을 보존하여 "full text 필요" 배지로 표기)
verified_no_pmid ← 사용자 reference에 PMID/DOI가 없었지만 Q2(title+author 역검색)로
                   PMID를 확인했고 모든 field가 일치하는 경우.
                   verdict="verified"로 기록하되 notes에
                   "PMID not in original; found via title search → {pmid}" 표시.
                   corrected_citation_vancouver에 PMID를 추가 제안.
```

각 reference의 cross-check 결과를 모아 verifications JSON 배열로 저장한 뒤 리포트를 렌더링:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/render_report.py" /tmp/verifications.json \
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

**필수**: 아래 두 경로 중 **하나**. 세션 시작 시 어느 쪽이 쓸 수 있는지 먼저 확인하고, 리포트에 어느 경로를 썼는지 남긴다.

*경로 1 — PubMed MCP (권장)*

Cowork 전용이 아니다. claude.ai 커넥터에 PubMed를 연결하면 Claude Code(CLI·VS Code 확장·데스크톱 앱)에서도 동일하게 로드된다. 도구 이름은 환경에 따라 접두사가 다르므로(`mcp__claude_ai_PubMed__*` 등) **이름을 하드코딩하지 말고 사용 가능한 도구 목록에서 PubMed 계열을 찾아 쓴다.** 필요한 기능:
  - `search_articles`, `get_article_metadata`, `convert_article_ids`,
    `lookup_article_by_citation`, `find_related_articles`

*경로 2 — E-utilities 폴백 (커넥터 없을 때)*

MCP가 없으면 번들 스크립트로 NCBI 공개 API를 직접 호출한다. 키·로그인 불필요, 반환 필드는 MCP와 동일한 모양이라 이후 검증 로직은 그대로 쓴다.

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/pubmed_lookup.py" --pmids 21150449 34575667
python "${CLAUDE_PLUGIN_ROOT}/scripts/pubmed_lookup.py" --doi 10.1097/FJC.0b013e318207a35f
python "${CLAUDE_PLUGIN_ROOT}/scripts/pubmed_lookup.py" --citation "Nature|2020|580|123|Smith"
python "${CLAUDE_PLUGIN_ROOT}/scripts/pubmed_lookup.py" --search "atrial fibrillation" --max 5
```

- **존재 검증**: `--pmids` 응답의 `articles[]`에 실제로 들어있는 PMID만 존재하는 것이다. 요청했는데 안 돌아온 PMID는 `not_found[]`에 담긴다 — 이것이 `hallucinated` 판정 근거다. 스크립트는 입력 PMID를 절대 echo하지 않는다.
- **조회 실패 ≠ 존재하지 않음**: 네트워크·rate limit 오류는 stderr에 `{"status": "lookup_failed"}`로 나오고 exit code 2다. 이 경우 `hallucinated`가 아니라 `unverifiable`로 처리한다.
- 무인증 호출은 초당 약 3회로 제한된다(스크립트가 자동 간격 조절). `NCBI_API_KEY`가 있으면 초당 10회.

- Python 3.10+ 표준 라이브러리 (폴백 스크립트는 외부 의존성 없음)

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
│   └── verification_prompts.md           # Q1–Q7 + cross-check prompt 템플릿
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
