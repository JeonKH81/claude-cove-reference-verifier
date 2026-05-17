# Reference Verification Report

본 리포트는 Chain-of-Verification (CoVe; Dhuliawala et al., ACL Findings 2024) 기법의 
Factor+Revise 변형을 적용해 작성되었습니다. 각 reference의 PMID/DOI/저자/제목/저널/연도/권/페이지 
atomic field들이 PubMed 도구 호출의 직접 반환값과 비교 검증되었습니다.

## Summary

- 총 검증 항목: **4**
- Verified: **1**
- Partial mismatch: **1**
- Hallucinated: **1**
- Unverifiable: **1**

## Verdict 정의

- **[VERIFIED]** — PubMed에서 일치하는 문헌이 확인되었고 모든 필드가 일치합니다.
- **[PARTIAL MISMATCH]** — PubMed에서 문헌은 확인되었으나 일부 필드(저자/제목/연도/저널/권/페이지)가 다릅니다.
- **[HALLUCINATED]** — 해당 PMID/DOI 또는 제목+저자 조합으로 PubMed에서 문헌을 찾을 수 없습니다. Hallucination 가능성이 높습니다.
- **[UNVERIFIABLE]** — 도구 호출이 실패했거나 식별 정보가 부족하여 검증할 수 없었습니다.

---

## Per-reference details

### 1. [VERIFIED]

**Original (as written):**

> Neumann FJ, Sousa-Uva M, Ahlsson A, et al. 2018 ESC/EACTS Guidelines on myocardial revascularization. Eur Heart J. 2019;40(2):87-165. doi:10.1093/eurheartj/ehy394. PMID: 30165437.

**Field-level comparison:**

| Field | User | PubMed (ground truth) | Match |
|---|---|---|---|
| pmid | 30165437 | 30165437 | OK |
| authors | Neumann FJ, Sousa-Uva M, Ahlsson A, et al. | Neumann FJ, Sousa-Uva M, Ahlsson A, ... | OK |
| title | 2018 ESC/EACTS Guidelines on myocardial revascularization | 2018 ESC/EACTS Guidelines on myocardial revascularization | OK |
| journal | Eur Heart J | European Heart Journal | OK |
| year | 2019 | 2019 | OK |
| volume | 40 | 40 | OK |
| pages | 87-165 | 87-165 | OK |

**Suggested corrected citation (Vancouver):**

> Neumann FJ, Sousa-Uva M, Ahlsson A, et al. 2018 ESC/EACTS Guidelines on myocardial revascularization. Eur Heart J. 2019;40(2):87-165.

**Notes:** All atomic fields verified against PubMed (PMID 30165437).

---

### 2. [HALLUCINATED]

**Original (as written):**

> Smith JR, Patel KK, Garcia M. Deep learning derived FFR predicts MACE in NSTEMI patients: a multicenter cohort. J Am Coll Cardiol. 2024;83(11):1102-1115. PMID: 99999999.

**Field-level comparison:**

| Field | User | PubMed (ground truth) | Match |
|---|---|---|---|
| pmid | 99999999 | — | DIFF |
| authors | Smith JR, Patel KK, Garcia M | — | DIFF |
| title | Deep learning derived FFR predicts MACE in NSTEMI patients: a multicenter cohort | — | DIFF |
| journal | J Am Coll Cardiol | — | DIFF |
| year | 2024 | — | DIFF |
| volume | 83 | — | DIFF |
| pages | 1102-1115 | — | DIFF |

**Notes:** PMID 99999999 not found via convert_article_ids. Title+author back-search returned 0 plausible matches (no titles with Jaro-Winkler >= 0.6). Likely hallucinated reference; recommend removing or replacing with a verified citation.

---

### 3. [PARTIAL MISMATCH]

**Original (as written):**

> Levine GN, Bates ER, Bittl JA, et al. 2016 ACC/AHA Guideline Focused Update on Duration of Dual Antiplatelet Therapy in Patients With Coronary Artery Disease. Circulation. 2016;134(10):e123-e155. doi:10.1161/CIR.0000000000000404.

**Field-level comparison:**

| Field | User | PubMed (ground truth) | Match |
|---|---|---|---|
| pmid | — | 27026020 | DIFF |
| authors | Levine GN, Bates ER, Bittl JA, et al. | Levine GN, Bates ER, Bittl JA, ... | OK |
| title | 2016 ACC/AHA Guideline Focused Update on Duration of Dual Antiplatelet Therapy in Patients With Coronary Artery Disease | 2016 ACC/AHA Guideline Focused Update on Duration of Dual Antiplatelet Therapy in Patients With Coronary Artery Disease | OK |
| journal | Circulation | Circulation | OK |
| year | 2016 | 2016 | OK |
| volume | 134 | 134 | OK |
| pages | e123-e155 | e123-e155 | OK |

**Suggested corrected citation (Vancouver):**

> Levine GN, Bates ER, Bittl JA, et al. 2016 ACC/AHA Guideline Focused Update on Duration of Dual Antiplatelet Therapy in Patients With Coronary Artery Disease. Circulation. 2016;134(10):e123-e155. PMID: 27026020.

**Notes:** All metadata fields match. PMID is missing in user citation; suggested addition: 27026020.

---

### 4. [UNVERIFIABLE]

**Original (as written):**

> Park J, Kim DY, Lee S, Choi WJ. AI-augmented angiography for instantaneous wave-free ratio: validation in 2,500 lesions. Lancet Digital Health. 2025;7(2):e45-e58. PMID: 12345678.

**Field-level comparison:**

| Field | User | PubMed (ground truth) | Match |
|---|---|---|---|
| pmid | 12345678 | — | DIFF |
| authors | Park J, Kim DY, Lee S, Choi WJ | — | DIFF |
| title | AI-augmented angiography for instantaneous wave-free ratio: validation in 2,500 lesions | — | DIFF |
| journal | Lancet Digital Health | — | DIFF |
| year | 2025 | — | DIFF |

**Notes:** PMID 12345678 maps to an unrelated 1990s article; title-author back-search returned no matches. Could be a future/preprint not yet indexed. Manual verification required.

---

## Methodology note

본 검증의 한계 (CoVe 논문 Limitations 그대로):
1. CoVe는 hallucination을 완전히 제거하지 않습니다 — 감소시킬 뿐입니다.
2. PubMed에 색인되지 않은 문헌(일부 conference proceedings, preprint, 단행본 등)은 검증되지 않습니다.
3. Claim support 평가는 abstract만을 근거로 하며, full-text가 필요한 세부 주장은 'not_in_abstract'로 표시됩니다.

이 리포트는 **사람의 최종 검토를 대체하지 않습니다**. 모든 'partial_mismatch' / 'hallucinated' 항목은 사용자가 직접 원본 논문을 확인하시기 바랍니다.