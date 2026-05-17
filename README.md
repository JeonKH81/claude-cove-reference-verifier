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

### Option 1 — Claude Code plugin install (recommended)

Claude Code에서 아래 명령어를 실행하세요:

```
/plugin install github:JeonKH81/claude-cove-reference-verifier
```

설치 후 Claude Code를 재시작하면 바로 사용할 수 있습니다.

### Option 2 — Manual clone

```bash
git clone https://github.com/JeonKH81/claude-cove-reference-verifier.git
```

클론 후 `CLAUDE.md`에 경로를 등록하세요:

```markdown
## Skills

- /path/to/claude-cove-reference-verifier/skills/cove-reference-verifier/SKILL.md
```

### Enable PubMed MCP

이 플러그인은 PubMed MCP 도구가 필요합니다. [Claude Cowork](https://claude.ai/code) 환경에서 PubMed connector를 활성화하세요.

### Install python-docx (optional)

`.docx` 파일 입력 또는 Word 리포트 출력이 필요한 경우:

```bash
pip install python-docx
```

## Requirements

- Claude Code (CLI or desktop app)
- PubMed MCP connector (Cowork mode)
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
