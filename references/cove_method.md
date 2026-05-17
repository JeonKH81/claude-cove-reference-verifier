# Chain-of-Verification (CoVe) — 본 skill의 이론적 근거

## 출처
Dhuliawala S, Komeili M, Xu J, Raileanu R, Li X, Celikyilmaz A, Weston J. **Chain-of-Verification Reduces Hallucination in Large Language Models.** Findings of the Association for Computational Linguistics: ACL 2024, pages 3563–3578. (paper id: 2024.findings-acl.212)

## 4단계 절차 (논문 Section 3)

| 단계 | 이름 | 입력 | 출력 |
|------|------|------|------|
| 1 | Generate Baseline Response | query | draft response (hallucination 가능) |
| 2 | Plan Verifications | query + draft | verification questions list |
| 3 | Execute Verifications | (variant마다 다름) | 각 question에 대한 답 |
| 4 | Generate Final Verified Response | 위 모든 정보 | 수정된 final response |

## Execute 단계의 4가지 변형

논문 Table 1, Table 2, Table 3의 실험 결과:

| 변형 | 핵심 특성 | 장점 | 단점 |
|------|----------|------|------|
| Joint | 단일 prompt로 plan+execute | 호출 1회 | draft를 보면서 답 → hallucination 복제 (Wikidata Prec 0.29) |
| 2-Step | plan 1회 + execute 1회 (draft 미노출) | 단순, 빠름 | 한 context에서 모든 q를 답함 (Wikidata Prec 0.36) |
| **Factored** | 각 question마다 **독립** prompt, draft 미노출 | question 간 간섭 제거, 병렬화 가능 (Wikidata Prec 0.32, FACTSCORE 63.7) | 호출 수 많음 |
| **Factor+Revise** | Factored + 별도 cross-check prompt | longform 최고 성능 (FACTSCORE 71.4) | 호출 수 가장 많음 |

본 skill은 **Factor+Revise를 기본**으로 채택. 이유:
- Reference verification은 본질적으로 longform fact-checking → factor+revise가 +28% FACTSCORE 우위
- 각 reference는 독립 사실이므로 question들이 서로 conditioning할 필요 없음
- Cross-check 단계가 명시적으로 inconsistency를 표시 → 사용자에게 투명한 감사 트레일

## 본 skill 적용에서 반드시 지킬 핵심 원칙

### 1. Verification step에서 원본 reference 텍스트를 노출하지 말 것
논문 Section 3.3 Factored: *"those prompts do not contain the original baseline response and are hence not prone to simply copying or repeating it."*
→ PubMed 검색에 보낼 query는 저자명/제목/연도 등 **개별 atomic fact**만 보내고, 원래 사용자가 적어둔 reference 문자열 전체를 prompt context로 함께 넣지 않는다. 이렇게 해야 LLM이 hallucination된 원문을 그대로 복제하지 않는다.

### 2. Open question을 사용할 것
논문 Section 6 (Table 4): yes/no 질문은 모델이 "동의 편향"을 보여 정확도가 더 낮다 (Wiki-Category Prec 0.19 vs open 0.22). 본 skill은 open-ended verification questions를 사용한다.
- ✗ "Is the author 'Smith J' correct for PMID 12345?" → yes/no 편향
- ✓ "Who are the authors of PMID 12345?" → open

### 3. Shortform > Longform 정확도
논문 Section 4.3: *"shortform verification questions are more accurately answered than longform queries"* (Wikidata: 17% → 70%).
→ 한 reference에 대해 가능한 한 작은 단위로 분해해 검증한다 (existence, authors, title, journal/year, claim별로).

### 4. Tool-grounded verification
논문 Limitations: *"an obvious extension to our work is to equip CoVe with tool-use, e.g., to use retrieval augmentation in the verification execution step which would likely bring further gains."*
→ 본 skill은 LLM 자체 지식 대신 **PubMed MCP 도구의 직접 반환값**만을 ground truth로 사용한다. 도구가 반환하지 않은 PMID/저자/제목은 LLM이 "기억해서" 메우지 않는다.

## CoVe의 한계 (논문 Limitations)

본 skill 사용자에게도 동일하게 알릴 것:
1. CoVe는 hallucination을 완전히 제거하지 않는다 — 감소시킬 뿐.
2. 원래 모델이 모르는 분야의 fact는 여전히 verify되지 못할 수 있다 (PubMed 외부 conference proceedings 등).
3. 검증으로 인한 **추가 토큰 비용**과 LLM 호출 수 증가가 있다 (논문 Table 5: Few-shot 1회 vs CoVe(factor+revise) 1+s+2f회).
