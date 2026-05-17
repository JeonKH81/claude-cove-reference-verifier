#!/usr/bin/env python3
"""
parse_references.py
====================
Reference parser for the cove-reference-verifier skill.

입력으로 다음 중 하나를 받는다:
  - .docx 파일 경로 (References / 참고문헌 섹션 자동 추출)
  - .md / .txt / .tex 파일 경로
  - stdin으로 들어온 raw text

출력은 stdout으로 JSON list:
  [
    {
      "idx": 1,
      "raw": "원본 reference 한 줄 또는 한 항목",
      "pmid": "12345" or null,
      "doi": "10.xxx/xxx" or null,
      "authors": ["Smith J", "Doe A", ...],
      "title": "..." or null,
      "journal": "..." or null,
      "year": "2024" or null,
      "volume": "..." or null,
      "pages": "..." or null,
      "in_text_claim": null   # 별도 추출 단계에서 채움
    },
    ...
  ]

CoVe Phase 1 (Baseline parsing) 단계에 해당. 원본을 손실 없이 보존하면서
구조화된 atomic field들을 분리한다. 이후 Phase 3 (Factored execute)에서
이 atomic field만 PubMed MCP에 전달된다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Regex patterns                                                              #
# --------------------------------------------------------------------------- #

# PMID: "PMID: 12345678" 또는 단순 "PMID 12345678" 또는 "PubMed PMID: 12345678"
RE_PMID = re.compile(r"PMID[\s:]*?(\d{6,9})", re.IGNORECASE)

# DOI: 10.XXXX/anything (RFC: doi.org 또는 https 접두 허용)
RE_DOI = re.compile(
    r"(?:doi[\s:]*|https?://(?:dx\.)?doi\.org/)?(10\.\d{4,9}/[^\s,;\)\]]+)",
    re.IGNORECASE,
)

# Year: 4 digits in 1800-2099 within parentheses or after period
RE_YEAR = re.compile(r"(?<!\d)(18\d{2}|19\d{2}|20\d{2})(?!\d)")

# Vancouver-style: "Authors. Title. Journal. Year;Vol(Issue):Pages."
RE_VANCOUVER_VOL_PAGES = re.compile(
    r";\s*(?P<vol>\d+)\s*(?:\((?P<issue>[^)]+)\))?\s*:\s*(?P<pages>[\dA-Za-z\-–\s,]+?)\."
)

# Reference 섹션 헤더 패턴 (한국어/영어/번호식)
RE_REF_HEADER = re.compile(
    r"^\s*(?:references|참고\s*문헌|bibliography|문헌\s*인용|works\s+cited)\s*:?\s*$",
    re.IGNORECASE,
)

# 번호 매김 패턴: "1.", "1)", "[1]", "(1)" 등
RE_NUMBERED_ITEM = re.compile(r"^\s*(?:\[(\d+)\]|\((\d+)\)|(\d+)[\.\)])\s+(.*)$")


# --------------------------------------------------------------------------- #
# Loaders                                                                     #
# --------------------------------------------------------------------------- #


def load_text(path: Path) -> str:
    """파일 확장자에 따라 text를 읽어 반환."""
    suffix = path.suffix.lower()
    if suffix == ".docx":
        try:
            import docx  # python-docx
        except ImportError as e:
            raise SystemExit(
                "python-docx가 필요합니다. "
                "`pip install --break-system-packages python-docx` 후 재시도하세요."
            ) from e
        doc = docx.Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs)
    return path.read_text(encoding="utf-8", errors="replace")


def extract_reference_section(text: str) -> str:
    """References/참고문헌 헤더 이후의 텍스트만 잘라낸다. 헤더 없으면 전체 반환."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if RE_REF_HEADER.match(line):
            return "\n".join(lines[i + 1 :])
    return text


def split_into_items(ref_text: str) -> list[str]:
    """Reference 섹션 텍스트를 항목 단위로 분리.

    번호식 ("1. ...", "[1] ...") 우선, 없으면 빈 줄 기준으로 split.
    """
    items: list[str] = []
    current: list[str] = []
    saw_numbered = False

    for line in ref_text.splitlines():
        m = RE_NUMBERED_ITEM.match(line)
        if m:
            saw_numbered = True
            if current:
                items.append(" ".join(current).strip())
                current = []
            current.append(m.group(4))
        else:
            stripped = line.strip()
            if not stripped:
                if current and not saw_numbered:
                    items.append(" ".join(current).strip())
                    current = []
                continue
            current.append(stripped)

    if current:
        items.append(" ".join(current).strip())

    # 비어있는 항목 제거
    return [it for it in items if it]


# --------------------------------------------------------------------------- #
# Field extractors                                                            #
# --------------------------------------------------------------------------- #


def extract_pmid(s: str) -> str | None:
    m = RE_PMID.search(s)
    return m.group(1) if m else None


def extract_doi(s: str) -> str | None:
    m = RE_DOI.search(s)
    if not m:
        return None
    doi = m.group(1).rstrip(".,;)")
    return doi if doi.startswith("10.") else None


def extract_year(s: str) -> str | None:
    # 마지막 4자리 연도가 보통 publication year
    years = RE_YEAR.findall(s)
    return years[-1] if years else None


def extract_authors_title_journal(item: str) -> dict[str, Any]:
    """Vancouver/AMA 스타일을 가정하고 best-effort로 author/title/journal을 분리.

    전형 패턴:
      "Smith J, Doe A, Roe B. The title of the paper. J Med Sci. 2024;12(3):45-60."
    """
    # Vancouver에서 첫 번째 마침표 + 공백 = author 끝
    parts = re.split(r"(?<=[\w\)])\.\s+", item, maxsplit=3)
    authors_str = parts[0].strip() if parts else ""
    title = parts[1].strip().rstrip(".") if len(parts) > 1 else None
    journal = parts[2].strip().rstrip(".") if len(parts) > 2 else None

    # author 분해
    authors = []
    if authors_str:
        # "Smith J, Doe A" 또는 "Smith J., Doe A." 또는 "Smith J and Doe A"
        # 마지막 author는 "et al." 처리
        cleaned = re.sub(r"\bet\s+al\.?", "", authors_str, flags=re.IGNORECASE).strip(", ")
        authors = [a.strip().rstrip(".") for a in re.split(r",\s*|\sand\s", cleaned) if a.strip()]

    return {
        "authors": authors,
        "title": title,
        "journal": journal,
    }


def extract_volume_pages(item: str) -> tuple[str | None, str | None]:
    m = RE_VANCOUVER_VOL_PAGES.search(item)
    if not m:
        return None, None
    return m.group("vol"), m.group("pages").strip()


# --------------------------------------------------------------------------- #
# Main parser                                                                 #
# --------------------------------------------------------------------------- #


def parse_one(idx: int, raw: str) -> dict[str, Any]:
    pmid = extract_pmid(raw)
    doi = extract_doi(raw)
    year = extract_year(raw)
    vol, pages = extract_volume_pages(raw)
    parts = extract_authors_title_journal(raw)

    return {
        "idx": idx,
        "raw": raw,
        "pmid": pmid,
        "doi": doi,
        "authors": parts["authors"],
        "title": parts["title"],
        "journal": parts["journal"],
        "year": year,
        "volume": vol,
        "pages": pages,
        "in_text_claim": None,
    }


def parse_text(text: str) -> list[dict[str, Any]]:
    section = extract_reference_section(text)
    items = split_into_items(section)
    return [parse_one(i + 1, raw) for i, raw in enumerate(items)]


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse references for CoVe verification.")
    parser.add_argument(
        "input",
        nargs="?",
        help="Path to .docx/.md/.txt/.tex file. Omit to read from stdin.",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output JSON path. Default: stdout.",
    )
    args = parser.parse_args()

    if args.input:
        text = load_text(Path(args.input))
    else:
        text = sys.stdin.read()

    parsed = parse_text(text)
    payload = json.dumps(parsed, ensure_ascii=False, indent=2)

    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
        print(f"Wrote {len(parsed)} references to {args.output}", file=sys.stderr)
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
