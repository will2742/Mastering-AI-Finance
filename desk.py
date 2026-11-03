"""SEC-backed equity-research desk for FIN 67110 Checkpoint 1."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import os


SEC_FILES = "https://www.sec.gov/files/company_tickers.json"
SEC_DATA = "https://data.sec.gov"
SEC_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"


def sec_session() -> requests.Session:
    load_dotenv()
    user_agent = os.getenv("SEC_USER_AGENT", "").strip()
    if not user_agent or "your-purdue-email" in user_agent:
        raise SystemExit("Set SEC_USER_AGENT in .env before using EDGAR.")
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
    return session


def get_json(session: requests.Session, url: str) -> dict:
    response = session.get(url, timeout=60)
    response.raise_for_status()
    return response.json()


def get_text(session: requests.Session, url: str) -> str:
    response = session.get(url, timeout=60)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    return "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())


def resolve_ticker(session: requests.Session, ticker: str) -> tuple[str, str]:
    print("Step 1: resolve ticker to CIK")
    companies = get_json(session, SEC_FILES).values()
    match = next((item for item in companies if item["ticker"].upper() == ticker.upper()), None)
    if not match:
        raise SystemExit(f"Ticker not found: {ticker}")
    return f'{int(match["cik_str"]):010d}', match["title"]


def recent_filings(submissions: dict) -> list[dict]:
    recent = submissions["filings"]["recent"]
    return [
        {key: recent[key][index] for key in recent}
        for index in range(len(recent["form"]))
    ]


def choose_filings(filings: list[dict]) -> dict[str, dict]:
    print("Step 2: select latest 10-K, 10-Q, and earnings 8-K")
    selected = {
        "10-K": next(item for item in filings if item["form"] == "10-K"),
        "10-Q": next(item for item in filings if item["form"] == "10-Q"),
        "8-K": next(
            item
            for item in filings
            if item["form"] == "8-K" and "2.02" in (item.get("items") or "")
        ),
    }
    for form, item in selected.items():
        print(f"  {form}: {item['accessionNumber']} filed {item['filingDate']}")
    return selected


def accession_path(accession: str) -> str:
    return accession.replace("-", "")


def filing_base(cik: str, accession: str) -> str:
    return f"{SEC_ARCHIVES}/{int(cik)}/{accession_path(accession)}"


def find_release_document(session: requests.Session, cik: str, filing: dict) -> str:
    base = filing_base(cik, filing["accessionNumber"])
    index = get_json(session, f"{base}/index.json")
    names = [item["name"] for item in index["directory"]["item"]]
    candidates = [name for name in names if re.search(r"ex(?:hibit)?[-_]?99[_\-.]?1", name, re.I)]
    if not candidates:
        candidates = [name for name in names if "99" in name and name.endswith((".htm", ".html"))]
    if not candidates:
        raise RuntimeError("Exhibit 99.1 was not found in the earnings 8-K package.")
    return candidates[0]


def save_numbered_text(path: Path, text: str) -> list[str]:
    lines = text.splitlines()
    path.write_text("\n".join(f"L{number}: {line}" for number, line in enumerate(lines, 1)), encoding="utf-8")
    return lines


def download_documents(session: requests.Session, cik: str, selected: dict, run_dir: Path) -> dict:
    print("Step 3: download filings and convert them to numbered plain text")
    documents = {}
    for form in ("10-K", "10-Q"):
        filing = selected[form]
        url = f"{filing_base(cik, filing['accessionNumber'])}/{filing['primaryDocument']}"
        text = get_text(session, url)
        lines = save_numbered_text(run_dir / f"{form}.txt", text)
        documents[form] = {"url": url, "lines": lines}
    release_name = find_release_document(session, cik, selected["8-K"])
    release_url = f"{filing_base(cik, selected['8-K']['accessionNumber'])}/{release_name}"
    release_text = get_text(session, release_url)
    documents["release"] = {
        "url": release_url,
        "name": release_name,
        "lines": save_numbered_text(run_dir / "earnings-release.txt", release_text),
    }
    return documents


FACT_TAGS = {
    "revenue": ["Revenues", "SalesRevenueNet", "RevenueFromContractWithCustomerExcludingAssessedTax"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "diluted_eps": ["EarningsPerShareDiluted"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "long_term_debt": ["LongTermDebtNoncurrent", "LongTermDebtCurrent", "LongTermDebt"],
    "cash_from_operations": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
}


def select_fact(companyfacts: dict, candidates: list[str], forms=("10-K",)) -> dict | None:
    us_gaap = companyfacts.get("facts", {}).get("us-gaap", {})
    best = None
    for tag in candidates:
        fact = us_gaap.get(tag)
        if not fact:
            continue
        for unit, observations in fact.get("units", {}).items():
            for obs in observations:
                if obs.get("form") not in forms:
                    continue
                candidate = {**obs, "tag": tag, "unit": unit}
                key = (candidate.get("end", ""), candidate.get("filed", ""), candidate.get("fy", 0))
                if best is None or key > best[0]:
                    best = (key, candidate)
    return best[1] if best else None


def annual_facts(session: requests.Session, cik: str) -> dict:
    print("Step 4: fetch XBRL facts for the latest fiscal year")
    companyfacts = get_json(session, f"{SEC_DATA}/api/xbrl/companyfacts/CIK{cik}.json")
    facts = {name: select_fact(companyfacts, tags) for name, tags in FACT_TAGS.items()}
    for name, fact in facts.items():
        if fact:
            print(f"  {name}: {fact['val']} {fact['unit']} | {fact['tag']} | {fact['end']} | {fact['accn']}")
    return facts


def ratio(numerator: float | None, denominator: float | None) -> float | None:
    return numerator / denominator if numerator is not None and denominator not in (None, 0) else None


def calculate_ratios(facts: dict, price: float | None) -> dict:
    print("Step 5: calculate ratios in code")
    value = lambda name: facts.get(name, {}).get("val") if facts.get(name) else None
    revenue = value("revenue")
    ratios = {
        "operating_margin": ratio(value("operating_income"), revenue),
        "debt_to_equity": ratio(value("long_term_debt"), value("equity")),
        "free_cash_flow": (
            value("cash_from_operations") - value("capex")
            if value("cash_from_operations") is not None and value("capex") is not None
            else None
        ),
        "pe": ratio(price, value("diluted_eps")) if price else None,
    }
    for name, value_ in ratios.items():
        print(f"  {name}: {value_ if value_ is not None else 'not available'}")
    return ratios


def matching_lines(lines: list[str], patterns: list[str], limit=16) -> list[dict]:
    matches = []
    for number, line in enumerate(lines, 1):
        if any(re.search(pattern, line, re.I) for pattern in patterns):
            matches.append({"line": number, "text": line})
    return matches[:limit]


def release_facts(documents: dict) -> list[dict]:
    print("Step 6: locate the quarter's revenue and diluted EPS in Exhibit 99.1")
    matches = matching_lines(
        documents["release"]["lines"],
        [r"total.*revenue", r"collaboration revenue", r"diluted.*(?:loss|earnings).*share", r"net loss"],
    )
    for item in matches:
        print(f"  L{item['line']}: {item['text'][:180]}")
    return matches


def peer_facts(session: requests.Session, peer_tickers: list[str]) -> dict:
    print("Step 7: read comparable peer facts")
    output = {}
    for ticker in peer_tickers:
        cik, name = resolve_ticker(session, ticker)
        facts = annual_facts(session, cik)
        output[ticker] = {
            "name": name,
            "revenue": facts.get("revenue"),
            "operating_income": facts.get("operating_income"),
            "operating_margin": ratio(
                facts.get("operating_income", {}).get("val") if facts.get("operating_income") else None,
                facts.get("revenue", {}).get("val") if facts.get("revenue") else None,
            ),
        }
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("ticker")
    parser.add_argument("--peers", default="")
    parser.add_argument("--price", type=float)
    args = parser.parse_args()

    ticker = args.ticker.upper()
    session = sec_session()
    cik, company = resolve_ticker(session, ticker)
    submissions = get_json(session, f"{SEC_DATA}/submissions/CIK{cik}.json")
    selected = choose_filings(recent_filings(submissions))
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = Path("runs") / f"{ticker}-{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=False)
    documents = download_documents(session, cik, selected, run_dir)
    facts = annual_facts(session, cik)
    ratios = calculate_ratios(facts, args.price)
    quarter = release_facts(documents)
    peers = peer_facts(session, [item.strip().upper() for item in args.peers.split(",") if item.strip()])
    payload = {
        "ticker": ticker,
        "company": company,
        "cik": cik,
        "run_time_utc": timestamp,
        "filings": selected,
        "documents": {name: {key: value for key, value in doc.items() if key != "lines"} for name, doc in documents.items()},
        "annual_facts": facts,
        "ratios": ratios,
        "quarter_release_matches": quarter,
        "peers": peers,
    }
    (run_dir / "facts.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Saved run: {run_dir}")


if __name__ == "__main__":
    main()
