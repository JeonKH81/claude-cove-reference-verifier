# CoVe Reference Verifier

A Claude Code skill that detects and corrects **reference hallucinations** in academic manuscripts using [Chain-of-Verification (CoVe)](https://aclanthology.org/2024.findings-acl.212/) — Dhuliawala et al., ACL Findings 2024.

## What it does

LLMs frequently hallucinate academic references: fabricated PMIDs, wrong authors, misattributed journals, or claims that don't appear in the cited paper's abstract. This skill verifies each reference against PubMed ground truth and produces a structured report.

**Verdict categories:**

| Verdict | Meaning |
|---|---|
| `verified` | All fields match PubMed |
| `partial_mismatch` | Paper found but ≥1 field differs |
| `hallucinated` | Not found by PMID/DOI or title+author search |
| `unverifiable` | Tool error or insufficient identifiers |

## How it works

The skill applies the **Factor+Revise** variant of CoVe:

1. **Phase 1 — Baseline Parsing**: Extract references from `.docx`/`.md`/`.txt` into atomic JSON fields (PMID, DOI, authors, title, journal, year, volume, pages)
2. **Phase 2 — Plan Verifications**: Generate 6 atomic questions per reference (existence, authorship, journal/year, volume/pages, claim support)
3. **Phase 3 — Factored Execution**: Each question is answered by an **independent** PubMed tool call — no cross-contamination from the original hallucinated text
4. **Phase 4 — Cross-check & Report**: Verdict + field-level diff + corrected Vancouver citation

The key CoVe principle: verification questions are answered with **only the atomic fact** as input (e.g., just the PMID), never the full original reference string. This prevents the LLM from copying the hallucination.

## Triggering the skill

Use this skill when you say things like:

- "이 reference list 검증해줘"
- "이 인용 맞아? PMID 진짜 있어?"
- "ChatGPT가 만든 reference 확인해줘"
- "citation check", "CoVe", "PubMed로 검증"

Attach a manuscript draft or reference list (`.docx`, `.md`, `.txt`) or paste references directly into chat.

## Output

- **Chat summary**: verdict counts + top 3–5 most problematic references
- **Markdown report** (`.md`)
- **Word report** (`.docx`) with per-reference field comparison table and corrected Vancouver citations

## File structure

```
cove-reference-verifier/
├── SKILL.md                       # Skill definition (Claude Code loads this)
├── README.md                      # This file
├── references/
│   ├── cove_method.md             # CoVe paper summary and 4 core principles
│   └── verification_prompts.md    # Q1–Q6 + cross-check prompt templates
├── scripts/
│   ├── parse_references.py        # Phase 1: docx/md/txt → atomic JSON
│   └── render_report.py           # Phase 4: verifications.json → md/docx report
└── examples/
    ├── sample_references.txt      # Example reference list input
    ├── mock_verifications.json    # Example verifications.json
    └── sample_report.md           # Example output report
```

## Installation

### Option 1 — GitHub 마켓플레이스 (recommended)

Claude Code에서 아래 명령어를 실행하세요:

```
/plugin marketplace add JeonKH81/claude-cove-reference-verifier
/plugin install cove-reference-verifier@cove-reference-verifier
```

설치 후 Claude Code를 재시작하면 바로 사용할 수 있습니다.

### Option 1b — zip 단독 (오프라인 / GitHub 불필요)

릴리스의 `cove-reference-verifier-vX.Y.Z.zip` 하나만 있으면 됩니다. 압축을 풀면 그 폴더가 곧 마켓플레이스입니다(`.claude-plugin/marketplace.json`, `source: "."`).

```bash
unzip cove-reference-verifier-v1.0.1.zip
/plugin marketplace add ./cove-reference-verifier
/plugin install cove-reference-verifier@cove-reference-verifier
```

### Option 2 — Manual clone

```bash
git clone https://github.com/JeonKH81/claude-cove-reference-verifier.git
```

클론 후 `CLAUDE.md`에 경로를 등록하세요:

```markdown
## Skills

- /path/to/claude-cove-reference-verifier/skills/cove-reference-verifier/SKILL.md
```

### Enable PubMed (권장)

이 플러그인은 PubMed 조회가 필요합니다. 두 가지 경로가 있고, **둘 중 하나만 있으면 됩니다.**

**경로 1 — PubMed MCP 커넥터 (권장)**

claude.ai의 커넥터 설정에서 PubMed를 활성화하세요. 한 번 연결하면 **Claude Code(CLI·VS Code 확장·데스크톱 앱)와 Cowork 모두에서** 동일하게 동작합니다. Cowork 전용이 아닙니다.

**경로 2 — 폴백: PubMed E-utilities (커넥터 불필요)**

커넥터가 없으면 번들 스크립트가 NCBI E-utilities 공개 API를 직접 호출합니다. API 키·로그인 없이 동작합니다.

```bash
python scripts/pubmed_lookup.py --pmids 21150449
```

인터넷 연결이 필요하며, 무인증 호출은 초당 약 3회로 제한됩니다(스크립트가 자동으로 간격을 둡니다). NCBI 권장에 따라 연락처를 남기려면:

```bash
export NCBI_EMAIL="you@example.com"   # 선택
export NCBI_API_KEY="..."             # 선택, 있으면 초당 10회
```

### Install python-docx (optional)

`.docx` 파일 입력 또는 Word 리포트 출력이 필요한 경우:

```bash
pip install python-docx
```

## Requirements

- Claude Code (CLI, VS Code extension, or desktop app) — Cowork도 지원
- PubMed 조회 경로 **하나**: MCP 커넥터(권장) 또는 번들된 E-utilities 폴백 스크립트
- Python 3.10+
- `python-docx` — `.docx` 입출력 시에만 필요

## Limitations

Per CoVe paper (Dhuliawala et al., 2024 Limitations):

1. CoVe **reduces** hallucination — it does not eliminate it. All `partial_mismatch` / `hallucinated` items require human review.
2. References not indexed in PubMed (conference proceedings, preprints, books) are marked `unverifiable`.
3. Claim support is abstract-based only. `not_in_abstract` means "full text needed," not "false."
4. Each reference requires up to 6 PubMed tool calls + 1 cross-check LLM call. ~200 calls for 30 references.

## Citation

If you use this skill in your work, please cite:

> Dhuliawala S, Komeili M, Xu J, Raileanu R, Li X, Celikyilmaz A, Weston J. **Chain-of-Verification Reduces Hallucination in Large Language Models.** Findings of the Association for Computational Linguistics: ACL 2024, pages 3563–3578.

## License

MIT
