#!/usr/bin/env python3
"""PubMed lookup fallback for environments without the PubMed MCP connector.

Talks to the public NCBI E-utilities endpoints and emits the same field shape
the MCP tools return, so the verification prompts in references/ can consume
either source without branching.

Ground-truth rule (see references/cove_method.md): only values that actually
came back from the API are reported. A PMID that E-utilities does not know is
returned in `not_found`, never echoed back as if it existed -- the same trap
that makes convert_article_ids unsafe for existence checks.

Usage:
    python pubmed_lookup.py --pmids 21150449 34575667
    python pubmed_lookup.py --doi 10.1097/FJC.0b013e318207a35f
    python pubmed_lookup.py --citation "Nature|2020|580|123|Smith"
    python pubmed_lookup.py --search "atrial fibrillation anticoagulation" --max 5

Optional environment:
    NCBI_EMAIL    contact address included with each request (NCBI courtesy)
    NCBI_API_KEY  raises the rate limit from ~3/sec to ~10/sec
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TOOL_NAME = "cove-reference-verifier"

_API_KEY = os.environ.get("NCBI_API_KEY", "").strip()
_EMAIL = os.environ.get("NCBI_EMAIL", "").strip()

# NCBI allows ~3 req/sec unauthenticated, ~10 with a key.
_MIN_INTERVAL = 0.11 if _API_KEY else 0.34
_last_call = 0.0


def _throttle() -> None:
    global _last_call
    wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def _get(endpoint: str, params: dict, retries: int = 3) -> bytes:
    params = {k: v for k, v in params.items() if v not in (None, "")}
    params["tool"] = TOOL_NAME
    if _EMAIL:
        params["email"] = _EMAIL
    if _API_KEY:
        params["api_key"] = _API_KEY
    url = f"{BASE}/{endpoint}?{urllib.parse.urlencode(params)}"

    last_err: Exception | None = None
    for attempt in range(retries):
        _throttle()
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            # 429/5xx are transient; back off and retry.
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                last_err = exc
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
        except urllib.error.URLError as exc:
            if attempt < retries - 1:
                last_err = exc
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
    raise RuntimeError(f"E-utilities request failed: {last_err}")


def _text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return "".join(node.itertext()).strip()


def _parse_article(art: ET.Element) -> dict:
    medline = art.find("MedlineCitation")
    if medline is None:
        return {}
    article = medline.find("Article")
    if article is None:
        return {}

    pmid = _text(medline.find("PMID"))

    ids = {"pmid": pmid}
    for aid in art.findall(".//ArticleIdList/ArticleId"):
        kind = aid.get("IdType", "")
        if kind in ("doi", "pmc"):
            ids[kind] = _text(aid)

    # Abstract may be split into labelled sections.
    parts = []
    for seg in article.findall(".//Abstract/AbstractText"):
        label = seg.get("Label")
        body = _text(seg)
        parts.append(f"{label}: {body}" if label else body)

    authors = []
    for au in article.findall(".//AuthorList/Author"):
        last = _text(au.find("LastName"))
        if not last:
            continue
        authors.append(
            {
                "last_name": last,
                "fore_name": _text(au.find("ForeName")),
                "initials": _text(au.find("Initials")),
            }
        )

    journal = article.find("Journal")
    issue = journal.find("JournalIssue") if journal is not None else None
    pubdate = issue.find("PubDate") if issue is not None else None
    year = _text(pubdate.find("Year")) if pubdate is not None else ""
    if not year and pubdate is not None:
        # Some records only carry MedlineDate, e.g. "2011 Jun-Jul".
        year = _text(pubdate.find("MedlineDate"))[:4]

    return {
        "identifiers": ids,
        "title": _text(article.find("ArticleTitle")),
        "abstract": " ".join(p for p in parts if p),
        "doi": ids.get("doi", ""),
        "journal": {
            "title": _text(journal.find("Title")) if journal is not None else "",
            "iso_abbreviation": (
                _text(journal.find("ISOAbbreviation")) if journal is not None else ""
            ),
        },
        "authors": authors,
        "publication_date": {
            "year": year,
            "month": _text(pubdate.find("Month")) if pubdate is not None else "",
        },
        "citation": {
            "volume": _text(issue.find("Volume")) if issue is not None else "",
            "issue": _text(issue.find("Issue")) if issue is not None else "",
            "pages": _text(article.find(".//Pagination/MedlinePgn")),
        },
        "article_types": [
            _text(pt) for pt in article.findall(".//PublicationTypeList/PublicationType")
        ],
    }


def fetch_metadata(pmids: list[str]) -> dict:
    """Return metadata for PMIDs that genuinely exist in PubMed."""
    pmids = [p.strip() for p in pmids if p.strip()]
    if not pmids:
        return {"articles": [], "count": 0, "not_found": []}

    raw = _get("efetch.fcgi", {"db": "pubmed", "id": ",".join(pmids), "retmode": "xml"})
    root = ET.fromstring(raw)

    articles = [a for a in (_parse_article(x) for x in root.findall(".//PubmedArticle")) if a]
    found = {a["identifiers"]["pmid"] for a in articles}
    # Anything we asked for but did not get back does not exist -- report it as
    # such rather than echoing the input.
    missing = [p for p in pmids if p not in found]

    return {"articles": articles, "count": len(articles), "not_found": missing}


def search(query: str, max_results: int = 20) -> dict:
    raw = _get(
        "esearch.fcgi",
        {"db": "pubmed", "term": query, "retmax": max_results, "retmode": "json"},
    )
    data = json.loads(raw).get("esearchresult", {})
    return {
        "pmids": data.get("idlist", []),
        "total_count": int(data.get("count", 0)),
        "query": query,
        "query_translation": data.get("querytranslation", ""),
    }


def lookup_doi(doi: str) -> dict:
    """Resolve a DOI to a PMID, then confirm the PMID really resolves back."""
    hit = search(f'"{doi}"[AID] OR "{doi}"[DOI]', max_results=5)
    if not hit["pmids"]:
        return {"doi": doi, "pmid": None, "articles": [], "count": 0}
    meta = fetch_metadata(hit["pmids"][:1])
    return {
        "doi": doi,
        "pmid": hit["pmids"][0] if meta["articles"] else None,
        "articles": meta["articles"],
        "count": meta["count"],
    }


def lookup_citation(journal: str, year: str, volume: str, page: str, author: str) -> dict:
    terms = []
    if journal:
        terms.append(f'"{journal}"[Journal]')
    if year:
        terms.append(f"{year}[DP]")
    if volume:
        terms.append(f"{volume}[VI]")
    if page:
        terms.append(f"{page}[PG]")
    if author:
        terms.append(f"{author}[AU]")
    if not terms:
        return {"pmids": [], "articles": [], "count": 0}

    hit = search(" AND ".join(terms), max_results=5)
    meta = fetch_metadata(hit["pmids"][:3]) if hit["pmids"] else {"articles": [], "count": 0}
    return {"query": " AND ".join(terms), "pmids": hit["pmids"], **meta}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--pmids", nargs="+", help="One or more PMIDs to verify.")
    g.add_argument("--doi", help="Resolve a DOI to its PubMed record.")
    g.add_argument("--search", help="Free-text or field-tagged PubMed query.")
    g.add_argument(
        "--citation",
        help='Pipe-separated "journal|year|volume|first_page|author" (blanks allowed).',
    )
    ap.add_argument("--max", type=int, default=20, help="Max results for --search.")
    ap.add_argument("-o", "--out", help="Write JSON here instead of stdout.")
    args = ap.parse_args()

    try:
        if args.pmids:
            result = fetch_metadata(args.pmids)
        elif args.doi:
            result = lookup_doi(args.doi)
        elif args.search:
            result = search(args.search, args.max)
        else:
            fields = (args.citation.split("|") + [""] * 5)[:5]
            result = lookup_citation(*[f.strip() for f in fields])
    except Exception as exc:  # noqa: BLE001 - surface the failure to the caller
        # A failed lookup must not be mistaken for "reference does not exist".
        err = {"error": str(exc), "source": "eutils", "status": "lookup_failed"}
        print(json.dumps(err, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2

    result["source"] = "eutils"
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"Wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
