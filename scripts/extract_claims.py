#!/usr/bin/env python3
"""
extract_claims.py
=================
원고 본문에서 citation을 문장 단위로 추출하고, 각 reference 번호 또는
(저자, 연도) 키와 매핑하여 in_text_claim과 in_text_context를 보강한다.

CoVe Phase 1의 선택적 in-text claim 추출 단계에 해당.
결과는 parse_references.py가 만든 refs.json의 두 필드를 채운다:
  - in_text_claim:   citation이 등장하는 문장 (citation 마크업 제거)
  - in_text_context: 해당 문장이 속한 단락 전체 (Q7 appropriateness 검토용)

Q7 (Citation Appropriateness) 검토는 in_text_context가 있을 때만 활성화된다.
reference list만 제공된 경우 두 필드 모두 null이며 Q1-Q6만 수행된다.

지원 citation 스타일:
  - Vancouver 번호식: [1], [1,2,3], [1-3], (1), (1,2)
  - APA 저자-연도식: (Smith, 2024), (Smith & Doe, 2024), (Smith et al., 2024)
  - JAMA/AMA 상첨자 번호: ¹²³ (Unicode superscript)

사용법:
  python extract_claims.py manuscript.docx --refs refs.json -o refs_with_claims.json
  python extract_claims.py manuscript.md   --refs refs.json --style vancouver
  cat body.txt | python extract_claims.py  --refs refs.json --style apa

출력: refs.json과 동일한 구조이지만 해당 reference의 in_text_claim,
      in_text_context가 채워짐.
      한 reference에 인용 문장이 여러 개이면 첫 번째 문장만 사용한다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Citation pattern definitions                                                #
# --------------------------------------------------------------------------- #

# Vancouver: [1], [1,2], [1-3], [1, 2, 3], (1), (1,2)
RE_VANCOUVER = re.compile(r"[\[\(](\d[\d,\s\-–]*)[\]\)]")

# APA: (Smith, 2024), (Smith & Doe, 2024), (Smith et al., 2024)
# 저자 성이 대문자로 시작하고 연도 4자리로 끝남
RE_APA = re.compile(
    r"\(([A-Z][A-Za-zÀ-ɏ]+(?:\s+(?:et\s+al\.|&\s+[A-Z][A-Za-z]+|\w+))*,\s+\d{4}(?:;\s*[A-Z][A-Za-z]+[^)]*,\s+\d{4})*)\)"
)

# Unicode superscript digits (¹²³⁴⁵⁶⁷⁸⁹⁰)
SUPERSCRIPT_MAP = str.maketrans("¹²³⁴⁵⁶⁷⁸⁹⁰", "1234567890")
RE_SUPERSCRIPT = re.compile(r"[¹²³⁴⁵⁶⁷⁸⁹⁰,]+")

# Known abbreviations that end with a period but are NOT sentence boundaries
_ABBREVS = {
    "et al", "fig", "figs", "dr", "mr", "mrs", "prof", "vs",
    "approx", "dept", "univ", "no", "vol", "pp", "ed", "eds",
    "ibid", "cf", "e.g", "i.e", "ca", "jan", "feb", "mar",
    "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
}

def _is_abbrev_boundary(text: str, pos: int) -> bool:
    """True if the period at text[pos] follows a known abbreviation."""
    # Find last word before "."
    i = pos - 1
    while i >= 0 and text[i] not in " \t\n":
        i -= 1
    word1 = text[i + 1 : pos].lower().rstrip(".")
    if word1 in _ABBREVS:
        return True
    # Check two-word abbreviation (e.g. "et al")
    j = i - 1
    while j >= 0 and text[j] not in " \t\n":
        j -= 1
    word2 = text[j + 1 : i].lower().rstrip(".")
    two_word = f"{word2} {word1}"
    return two_word in _ABBREVS


def split_sentences(text: str) -> list[str]:
    """Split text into sentences, skipping abbreviation-period boundaries."""
    # Find all candidate split positions
    pattern = re.compile(r"(?<=[.!?])\s+(?=[A-Z\[\(])")
    result: list[str] = []
    last = 0
    for m in pattern.finditer(text):
        boundary_pos = m.start()
        # Find where the period is (just before whitespace)
        period_pos = boundary_pos - 1
        if _is_abbrev_boundary(text, period_pos):
            continue
        segment = text[last:boundary_pos].strip()
        if segment:
            result.append(segment)
        last = m.end()
    tail = text[last:].strip()
    if tail:
        result.append(tail)
    return result

# --------------------------------------------------------------------------- #
# Citation extraction helpers                                                 #
# --------------------------------------------------------------------------- #


def _expand_range(s: str) -> list[int]:
    """'1-3' 또는 '1–3' → [1, 2, 3], '1,2' → [1, 2]."""
    nums: list[int] = []
    for part in re.split(r",\s*", s):
        m = re.match(r"(\d+)\s*[-–]\s*(\d+)", part.strip())
        if m:
            nums.extend(range(int(m.group(1)), int(m.group(2)) + 1))
        elif part.strip().isdigit():
            nums.append(int(part.strip()))
    return nums


def _extract_vancouver_indices(sentence: str) -> list[int]:
    indices: list[int] = []
    for m in RE_VANCOUVER.finditer(sentence):
        indices.extend(_expand_range(m.group(1)))
    # Superscript fallback
    sup_sentence = sentence.translate(SUPERSCRIPT_MAP)
    for m in RE_SUPERSCRIPT.finditer(sentence):
        converted = m.group(0).translate(SUPERSCRIPT_MAP)
        indices.extend(_expand_range(converted))
    return sorted(set(indices))


def _extract_apa_keys(sentence: str) -> list[str]:
    """APA 인용 → '(저자, 연도)' 문자열 리스트."""
    keys: list[str] = []
    for m in RE_APA.finditer(sentence):
        keys.append(m.group(0))
    return keys


# --------------------------------------------------------------------------- #
# Text loading                                                                #
# --------------------------------------------------------------------------- #


def load_body_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        try:
            import docx
        except ImportError as e:
            raise SystemExit(
                "python-docx가 필요합니다. `pip install --break-system-packages python-docx`"
            ) from e
        doc = docx.Document(str(path))
        paragraphs = []
        in_refs = False
        ref_header = re.compile(
            r"^\s*(?:references|참고\s*문헌|bibliography)\s*:?\s*$", re.IGNORECASE
        )
        for p in doc.paragraphs:
            if ref_header.match(p.text):
                in_refs = True
            if not in_refs:
                paragraphs.append(p.text)
        return "\n".join(paragraphs)
    elif suffix in {".md", ".txt", ".tex"}:
        text = path.read_text(encoding="utf-8", errors="replace")
        # References 섹션 이전만 반환
        ref_header = re.compile(
            r"^(?:#{1,3}\s*)?(?:references|참고\s*문헌|bibliography)\s*:?\s*$",
            re.IGNORECASE | re.MULTILINE,
        )
        m = ref_header.search(text)
        return text[: m.start()] if m else text
    return path.read_text(encoding="utf-8", errors="replace")




# --------------------------------------------------------------------------- #
# APA key → ref index matching                                               #
# --------------------------------------------------------------------------- #


def _build_apa_index(refs: list[dict[str, Any]]) -> dict[str, int]:
    """각 reference의 first_author_lastname + year → idx 매핑."""
    mapping: dict[str, int] = {}
    for ref in refs:
        authors = ref.get("authors") or []
        year = ref.get("year") or ""
        if not authors or not year:
            continue
        first = authors[0]
        # "Smith J" → "Smith", "Smith, J." → "Smith"
        lastname = re.split(r"[,\s]", first)[0].strip()
        if lastname:
            mapping[f"{lastname}_{year}"] = ref["idx"]
    return mapping


def _match_apa_key(apa_key: str, apa_index: dict[str, int]) -> list[int]:
    """'(Smith et al., 2024)' → [idx] 또는 [] """
    results: list[int] = []
    # 여러 인용이 세미콜론으로 이어진 경우 분리
    for segment in re.split(r";\s*", apa_key.strip("()")):
        m = re.match(r"([A-Z][A-Za-zÀ-ɏ]+).*?,\s*(\d{4})", segment)
        if m:
            key = f"{m.group(1)}_{m.group(2)}"
            if key in apa_index:
                results.append(apa_index[key])
    return results


# --------------------------------------------------------------------------- #
# Core: sentence → claim mapping                                             #
# --------------------------------------------------------------------------- #


def _split_paragraphs(text: str) -> list[str]:
    """빈 줄(또는 연속 줄바꿈) 기준으로 단락 분리."""
    return [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]


def map_claims(
    sentences: list[str],
    refs: list[dict[str, Any]],
    style: str,
    paragraphs: list[str] | None = None,
) -> dict[int, tuple[str, str | None]]:
    """ref idx → (in_text_claim, in_text_context) 매핑.

    in_text_claim:   citation이 있는 문장 (마크업 제거)
    in_text_context: 해당 문장이 속한 단락 전체 (Q7용). paragraphs 없으면 None.
    """
    idx_to_result: dict[int, tuple[str, str | None]] = {}
    apa_index = _build_apa_index(refs) if style in {"apa", "auto"} else {}

    for sentence in sentences:
        matched_indices: list[int] = []

        if style in {"vancouver", "auto"}:
            matched_indices.extend(_extract_vancouver_indices(sentence))

        if style in {"apa", "auto"} and apa_index:
            for key in _extract_apa_keys(sentence):
                matched_indices.extend(_match_apa_key(key, apa_index))

        if not matched_indices:
            continue

        # citation 마크업 제거한 clean 문장
        clean = re.sub(RE_VANCOUVER, "", sentence)
        clean = re.sub(RE_APA, "", clean)
        clean = re.sub(r"\s{2,}", " ", clean).strip()

        # 단락 찾기: sentence를 포함하는 단락
        context: str | None = None
        if paragraphs:
            for para in paragraphs:
                if sentence[:40] in para or clean[:40] in para:
                    context = para
                    break

        for idx in matched_indices:
            if idx not in idx_to_result:
                idx_to_result[idx] = (clean, context)

    return idx_to_result


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract in-text claims from manuscript body and inject into refs.json."
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="Manuscript path (.docx/.md/.txt). Omit to read from stdin.",
    )
    parser.add_argument(
        "--refs",
        required=True,
        help="Path to refs.json produced by parse_references.py.",
    )
    parser.add_argument(
        "--style",
        choices=["vancouver", "apa", "auto"],
        default="auto",
        help="Citation style. Default: auto (tries both).",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output JSON path. Default: stdout.",
    )
    args = parser.parse_args()

    refs: list[dict[str, Any]] = json.loads(
        Path(args.refs).read_text(encoding="utf-8")
    )

    if args.input:
        text = load_body_text(Path(args.input))
    else:
        text = sys.stdin.read()

    sentences = split_sentences(text)
    paragraphs = _split_paragraphs(text)
    claims = map_claims(sentences, refs, args.style, paragraphs)

    filled = 0
    for ref in refs:
        idx = ref["idx"]
        if idx in claims:
            claim, context = claims[idx]
            ref["in_text_claim"] = claim
            ref["in_text_context"] = context
            filled += 1
        else:
            ref.setdefault("in_text_context", None)

    payload = json.dumps(refs, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
        print(
            f"Wrote {len(refs)} refs ({filled} with claims) to {args.output}",
            file=sys.stderr,
        )
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
