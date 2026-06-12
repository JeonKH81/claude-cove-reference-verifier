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
      "in_text_claim": null,   # 별도 추출 단계(extract_claims.py)에서 채움
      "in_text_context": null  # 별도 추출 단계(extract_claims.py)에서 채움
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
    r";\s*(?P<vol>\d+)\s*(?:\((?P<issue>[^)]+)\))?\s*:\s*(?P<pages>[\dA-Za-z\-–\s,]+?)[.\s]"
)

# Article number (e.g. ":e12345", "Article e12345", ":20193456")
RE_ARTICLE_NUMBER = re.compile(r"(?::\s*e|article\s+e?)(\d{4,})\b", re.IGNORECASE)

# APA-style year in parens after authors: "Author, A. (2024). Title..."
RE_APA_YEAR = re.compile(r"\((\d{4})\)\.")

# APA-style volume/pages: "Journal, 12(3), 45–60."
RE_APA_VOL_PAGES = re.compile(
    r",\s*(?P<vol>\d+)\s*(?:\((?P<issue>[^)]+)\))?\s*,\s*(?P<pages>[\dA-Za-z\-–]+)\."
)

# Reference 섹션 헤더 패턴 (한국어/영어/번호식)
RE_REF_HEADER = re.compile(
    r"^\s*(?:references|참고\s*문헌|bibliography|문헌\s*인용|works\s+cited)\s*:?\s*$",
    re.IGNORECASE,
)

# 번호 매김 패턴: "1.", "1)", "[1]", "(1)" 등
RE_NUMBERED_ITEM = re.compile(r"^\s*(?:\[(\d+)\]|\((\d+)\)|(\d+)[\.\)])\s+(.*)$")

# APA author unit: "Lastname, F. M." (성, 이니셜). ", &" / ", " 로 연결된 다수 저자 분리에 사용.
RE_APA_AUTHOR = re.compile(
    r"([A-Z][A-Za-z'’\-]+(?:\s+[A-Z][A-Za-z'’\-]+)*),\s*((?:[A-Z]\.\s*){1,4})"
)


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


def _looks_like_reference(line: str) -> bool:
    """한 줄이 그 자체로 완결된 reference처럼 보이는지 판정.

    줄 단위로 나뉜 reference 리스트(번호·빈 줄 없이 "한 줄에 한 reference")를
    감지하기 위한 휴리스틱. wrapping된 한 reference의 중간 줄(저자/제목 일부)은
    보통 연도나 PMID/DOI로 끝나지 않으므로 False가 된다.
    """
    if RE_PMID.search(line) or RE_DOI.search(line):
        return True
    # 연도를 포함하고 마침표로 끝나면 완결된 인용으로 간주
    return bool(RE_YEAR.search(line) and line.rstrip().endswith("."))


def split_into_items(ref_text: str) -> list[str]:
    """Reference 섹션 텍스트를 항목 단위로 분리.

    우선순위:
      1) 번호식 ("1. ...", "[1] ...")이 하나라도 있으면 번호 기준 분리.
      2) 번호가 없으면 빈 줄 기준으로 블록을 나눈 뒤,
         한 블록 안의 모든 줄이 각각 완결된 reference처럼 보이면
         (= "한 줄에 한 reference" 입력) 줄 단위로 분리한다.
         그렇지 않으면(=여러 줄에 걸쳐 wrapping된 한 reference) 블록을 합친다.
    """
    lines = ref_text.splitlines()

    # 1) 번호식 우선
    if any(RE_NUMBERED_ITEM.match(ln) for ln in lines):
        items: list[str] = []
        current: list[str] = []
        for line in lines:
            m = RE_NUMBERED_ITEM.match(line)
            if m:
                if current:
                    items.append(" ".join(current).strip())
                    current = []
                current.append(m.group(4))
            else:
                stripped = line.strip()
                if stripped:
                    current.append(stripped)
        if current:
            items.append(" ".join(current).strip())
        return [it for it in items if it]

    # 2) 번호 없음 → 빈 줄 기준 블록 분리
    blocks: list[list[str]] = []
    current = []
    for line in lines:
        stripped = line.strip()
        if stripped:
            current.append(stripped)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)

    # 3) 블록별로 "한 줄에 한 reference"인지 판정
    items = []
    for block in blocks:
        if len(block) > 1 and all(_looks_like_reference(ln) for ln in block):
            items.extend(block)
        else:
            items.append(" ".join(block).strip())

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
    # APA: year in parentheses "(2024)." takes priority
    m = RE_APA_YEAR.search(s)
    if m:
        return m.group(1)
    # Vancouver/other: last 4-digit year
    years = RE_YEAR.findall(s)
    return years[-1] if years else None


def _split_authors(authors_str: str) -> list[str]:
    """Vancouver/AMA author 문자열을 개별 저자 리스트로 분리 (et al. 제거).

    Vancouver/AMA는 저자 내부에 콤마가 없으므로 ("Smith J, Doe A")
    콤마/and/& 기준 split이 안전하다.
    """
    cleaned = re.sub(r"\bet\s+al\.?", "", authors_str, flags=re.IGNORECASE).strip(", ")
    return [a.strip().rstrip(".") for a in re.split(r",\s*|\s+and\s+|&\s*", cleaned) if a.strip()]


def _split_authors_apa(authors_str: str) -> list[str]:
    """APA author 문자열을 개별 저자 리스트로 분리.

    APA는 "Smith, J., & Doe, A." 처럼 **저자 내부에 콤마**(성, 이니셜)가 있어
    단순 콤마 split이 성/이니셜을 쪼갠다. "Lastname, F. M." 단위를 정규식으로
    묶어 "Lastname F M" 형태(Vancouver 표기)로 정규화한다.
    """
    cleaned = re.sub(r"\bet\s+al\.?", "", authors_str, flags=re.IGNORECASE)
    authors: list[str] = []
    for m in RE_APA_AUTHOR.finditer(cleaned):
        last = m.group(1).strip()
        initials = re.sub(r"[.\s]+", " ", m.group(2)).strip()
        name = f"{last} {initials}".strip()
        authors.append(re.sub(r"\s+", " ", name))
    # 패턴이 하나도 안 맞으면 generic splitter로 fallback
    return authors if authors else _split_authors(authors_str)


def _is_apa(item: str) -> bool:
    """APA 스타일 휴리스틱: 저자 뒤에 "(YYYY)." 패턴이 있는지."""
    return bool(RE_APA_YEAR.search(item))


def extract_authors_title_journal(item: str) -> dict[str, Any]:
    """Vancouver/AMA/APA 스타일 best-effort 분리.

    Vancouver/AMA:
      "Smith J, Doe A. The title. J Med Sci. 2024;12(3):45-60."
    APA:
      "Smith, J., & Doe, A. (2024). The title. J Med Sci, 12(3), 45-60."
    """
    if _is_apa(item):
        # APA: 저자는 "(YYYY)." 이전, 제목은 "(YYYY). " 이후 첫 문장, 저널은 그다음
        year_match = RE_APA_YEAR.search(item)
        authors_str = item[: year_match.start()].strip().rstrip(",")
        after_year = item[year_match.end():].strip()
        apa_parts = re.split(r"\.\s+", after_year, maxsplit=2)
        title = apa_parts[0].strip() if apa_parts else None
        # 저널은 콤마 기준 첫 토큰 (APA: "J Med Sci, 12(3), 45-60.")
        journal_raw = apa_parts[1].strip() if len(apa_parts) > 1 else None
        journal = journal_raw.split(",")[0].strip() if journal_raw else None
        return {"authors": _split_authors_apa(authors_str), "title": title, "journal": journal}

    # Vancouver/AMA: 첫 마침표+공백 = author 끝
    parts = re.split(r"(?<=[\w\)])\.\s+", item, maxsplit=3)
    authors_str = parts[0].strip() if parts else ""
    title = parts[1].strip().rstrip(".") if len(parts) > 1 else None
    journal_raw = parts[2].strip() if len(parts) > 2 else None
    # 저널에서 연도/권호 잘라내기 ("J Med Sci. 2024;12" → "J Med Sci")
    journal = re.split(r"\.\s+\d{4}|;\s*\d", journal_raw)[0].strip() if journal_raw else None

    return {"authors": _split_authors(authors_str), "title": title, "journal": journal}


def extract_volume_pages(item: str) -> tuple[str | None, str | None]:
    # Vancouver: "2024;12(3):45-60"
    m = RE_VANCOUVER_VOL_PAGES.search(item)
    if m:
        pages = m.group("pages").strip()
        # Treat article numbers (e.g. "e12345", purely numeric 5+) as pages
        if re.fullmatch(r"e?\d{5,}", pages.replace(" ", "")):
            pages = pages.strip()
        return m.group("vol"), pages
    # APA: "Journal, 12(3), 45-60."
    m = RE_APA_VOL_PAGES.search(item)
    if m:
        return m.group("vol"), m.group("pages").strip()
    # Article number only (":e12345" or "Article e12345")
    m_art = RE_ARTICLE_NUMBER.search(item)
    if m_art:
        return None, m_art.group(1)
    return None, None


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
        "in_text_context": None,
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
