import os
import re
import json
import time
import pickle
import hashlib
import logging
from dotenv import load_dotenv
from typing import Any, Dict, List, Optional, Iterable
from urllib.parse import urlparse

import requests
import pandas as pd
from bs4 import BeautifulSoup

try:
    import trafilatura
except ImportError:
    trafilatura = None


# -----------------------------
# CONFIG
# -----------------------------

load_dotenv()

ADZUNA_APP_ID = os.getenv("ADZUNA_APP_ID")
ADZUNA_APP_KEY = os.getenv("ADZUNA_APP_KEY")

COUNTRY = "gb"
TARGET_UNIQUE_JOBS = 5000
RESULTS_PER_PAGE = 50  # если API ограничит, уменьшите до 20
MAX_PAGES_PER_QUERY = 80
REQUEST_SLEEP_SEC = 0.35
FULLTEXT_SLEEP_SEC = 0.6

OUTPUT_PKL = "adzuna_uk_data_science_jobs.pkl"
CHECKPOINT_PKL = "adzuna_checkpoint.pkl"

BASE_URL = f"https://api.adzuna.com/v1/api/jobs/{COUNTRY}/search"

SEARCH_QUERIES = [
    "data scientist",
    "senior data scientist",
    "machine learning engineer",
    "ml engineer",
    "machine learning scientist",
    "ai engineer",
    "data engineer",
    "analytics engineer",
    "data analyst",
    "business intelligence analyst",
    "BI analyst",
    "NLP engineer",
    "natural language processing",
    "computer vision engineer",
    "research scientist machine learning",
    "applied scientist",
    "MLOps engineer",
    "deep learning engineer",
    "statistician",
    "quantitative analyst",
    "quant researcher",
    "decision scientist",
    "product analyst",
    "marketing analyst",
    "risk analyst python",
    "python data science",
    "SQL python analyst",
    "pytorch",
    "tensorflow",
    "scikit-learn",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; research-job-collector/1.0; "
        "+https://example.com/contact)"
    ),
    "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)


# -----------------------------
# HELPERS
# -----------------------------

def require_credentials() -> None:
    if not ADZUNA_APP_ID or not ADZUNA_APP_KEY:
        raise RuntimeError(
            "Set environment variables ADZUNA_APP_ID and ADZUNA_APP_KEY first."
        )


def clean_html_text(text: str) -> str:
    if text is None:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.I)
    text = BeautifulSoup(text, "html.parser").get_text("\n")
    text = re.sub(r"\r", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def stable_hash(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def flatten_location(job: Dict[str, Any]) -> str:
    loc = job.get("location") or {}
    if isinstance(loc, dict):
        return loc.get("display_name") or ", ".join(loc.get("area", []) or [])
    return ""


def normalize_company(job: Dict[str, Any]) -> str:
    company = job.get("company") or {}
    if isinstance(company, dict):
        return company.get("display_name") or ""
    return ""


def extract_jsonld_objects(html: str) -> List[Any]:
    soup = BeautifulSoup(html, "html.parser")
    objects = []

    for script in soup.find_all("script", type=lambda t: t and "ld+json" in t):
        raw = script.string or script.get_text()
        if not raw:
            continue

        raw = raw.strip()
        try:
            parsed = json.loads(raw)
            objects.append(parsed)
        except Exception:
            # Some pages contain multiple JSON objects or invalid escapes.
            continue

    return objects


def iter_json_objects(obj: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from iter_json_objects(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from iter_json_objects(item)


def extract_jobposting_description_from_jsonld(html: str) -> Optional[str]:
    for root in extract_jsonld_objects(html):
        for obj in iter_json_objects(root):
            obj_type = obj.get("@type") or obj.get("type")
            if isinstance(obj_type, list):
                is_jobposting = any(str(t).lower() == "jobposting" for t in obj_type)
            else:
                is_jobposting = str(obj_type).lower() == "jobposting"

            if is_jobposting and obj.get("description"):
                return clean_html_text(str(obj["description"]))

    return None


def extract_visible_text_fallback(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
        tag.decompose()

    # Prefer likely content containers.
    candidates = []
    selectors = [
        "[class*=description]",
        "[id*=description]",
        "[class*=job]",
        "[id*=job]",
        "main",
        "article",
        "body",
    ]

    for selector in selectors:
        for node in soup.select(selector):
            text = node.get_text("\n", strip=True)
            if len(text) > 500:
                candidates.append(text)

    if candidates:
        text = max(candidates, key=len)
    else:
        text = soup.get_text("\n", strip=True)

    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def fetch_full_description(url: str, timeout: int = 25) -> Dict[str, Any]:
    """
    Best-effort extraction from the destination page behind Adzuna redirect_url.
    This cannot guarantee exact full description for every job board.
    """
    if not url:
        return {
            "final_url": "",
            "full_description": "",
            "full_description_source": "missing_url",
            "fetch_status": None,
            "fetch_error": "missing_url",
        }

    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=timeout,
            allow_redirects=True,
        )

        status = response.status_code
        final_url = response.url

        if status >= 400:
            return {
                "final_url": final_url,
                "full_description": "",
                "full_description_source": "http_error",
                "fetch_status": status,
                "fetch_error": f"HTTP {status}",
            }

        html = response.text or ""

        # 1. Best case: structured JobPosting.description.
        jsonld_desc = extract_jobposting_description_from_jsonld(html)
        if jsonld_desc and len(jsonld_desc) > 200:
            return {
                "final_url": final_url,
                "full_description": jsonld_desc,
                "full_description_source": "jsonld_jobposting",
                "fetch_status": status,
                "fetch_error": "",
            }

        # 2. Strong fallback: trafilatura.
        if trafilatura is not None:
            extracted = trafilatura.extract(
                html,
                include_comments=False,
                include_tables=False,
                favor_recall=True,
            )
            if extracted and len(extracted) > 300:
                return {
                    "final_url": final_url,
                    "full_description": extracted.strip(),
                    "full_description_source": "trafilatura",
                    "fetch_status": status,
                    "fetch_error": "",
                }

        # 3. Weak fallback: visible text.
        visible = extract_visible_text_fallback(html)
        return {
            "final_url": final_url,
            "full_description": visible,
            "full_description_source": "visible_text_fallback",
            "fetch_status": status,
            "fetch_error": "",
        }

    except Exception as exc:
        return {
            "final_url": "",
            "full_description": "",
            "full_description_source": "exception",
            "fetch_status": None,
            "fetch_error": repr(exc),
        }


# -----------------------------
# ADZUNA API
# -----------------------------

def search_adzuna_page(
    query: str,
    page: int,
    results_per_page: int = RESULTS_PER_PAGE,
    sort_by: str = "date",
) -> Dict[str, Any]:
    url = f"{BASE_URL}/{page}"

    params = {
        "app_id": ADZUNA_APP_ID,
        "app_key": ADZUNA_APP_KEY,
        "results_per_page": results_per_page,
        "what": query,
        "sort_by": sort_by,
        "content-type": "application/json",
    }

    response = requests.get(url, params=params, headers=HEADERS, timeout=30)

    if response.status_code == 429:
        raise RuntimeError("Rate limited by Adzuna API: HTTP 429")

    if response.status_code >= 400:
        raise RuntimeError(
            f"Adzuna API error HTTP {response.status_code}: {response.text[:500]}"
        )

    return response.json()


def normalize_adzuna_job(job: Dict[str, Any], search_query: str) -> Dict[str, Any]:
    api_description = clean_html_text(job.get("description") or "")

    return {
        "adzuna_id": str(job.get("id", "")),
        "search_query": search_query,
        "title": job.get("title", ""),
        "company": normalize_company(job),
        "location": flatten_location(job),
        "category_label": (job.get("category") or {}).get("label", ""),
        "category_tag": (job.get("category") or {}).get("tag", ""),
        "created": job.get("created", ""),
        "contract_type": job.get("contract_type", ""),
        "contract_time": job.get("contract_time", ""),
        "salary_min": job.get("salary_min"),
        "salary_max": job.get("salary_max"),
        "salary_is_predicted": job.get("salary_is_predicted"),
        "latitude": job.get("latitude"),
        "longitude": job.get("longitude"),
        "redirect_url": job.get("redirect_url", ""),
        "api_description_snippet": api_description,
        "api_raw": job,
    }


def collect_adzuna_jobs() -> List[Dict[str, Any]]:
    require_credentials()

    seen_ids = set()
    seen_fingerprints = set()
    rows: List[Dict[str, Any]] = []

    for query in SEARCH_QUERIES:
        logging.info("Query: %s", query)

        for page in range(1, MAX_PAGES_PER_QUERY + 1):
            if len(rows) >= TARGET_UNIQUE_JOBS:
                logging.info("Target reached: %d jobs", len(rows))
                return rows

            try:
                payload = search_adzuna_page(query=query, page=page)
            except Exception as exc:
                logging.warning("Failed query=%s page=%s: %r", query, page, exc)
                break

            results = payload.get("results") or []
            if not results:
                logging.info("No more results for query=%s at page=%s", query, page)
                break

            new_on_page = 0

            for job in results:
                row = normalize_adzuna_job(job, query)

                adzuna_id = row["adzuna_id"]
                fingerprint = stable_hash(
                    "|".join([
                        row["title"].lower().strip(),
                        row["company"].lower().strip(),
                        row["location"].lower().strip(),
                        row["redirect_url"].split("?")[0].lower().strip(),
                    ])
                )

                if adzuna_id and adzuna_id in seen_ids:
                    continue
                if fingerprint in seen_fingerprints:
                    continue

                if adzuna_id:
                    seen_ids.add(adzuna_id)
                seen_fingerprints.add(fingerprint)

                rows.append(row)
                new_on_page += 1

            logging.info(
                "query=%s page=%s got=%d new=%d total=%d",
                query,
                page,
                len(results),
                new_on_page,
                len(rows),
            )

            # checkpoint
            if len(rows) % 200 < RESULTS_PER_PAGE:
                pd.DataFrame(rows).to_pickle(CHECKPOINT_PKL)

            time.sleep(REQUEST_SLEEP_SEC)

    return rows


def enrich_with_full_descriptions(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    enriched = []

    for i, row in enumerate(rows, start=1):
        url = row.get("redirect_url", "")
        full = fetch_full_description(url)

        row.update(full)

        row["api_description_len"] = len(row.get("api_description_snippet") or "")
        row["full_description_len"] = len(row.get("full_description") or "")
        row["full_description_hash"] = stable_hash(row.get("full_description") or "")
        row["final_domain"] = urlparse(row.get("final_url") or "").netloc

        enriched.append(row)

        if i % 25 == 0:
            logging.info(
                "Fulltext enriched %d/%d; last source=%s len=%s",
                i,
                len(rows),
                row.get("full_description_source"),
                row.get("full_description_len"),
            )
            pd.DataFrame(enriched).to_pickle(CHECKPOINT_PKL)

        time.sleep(FULLTEXT_SLEEP_SEC)

    return enriched


def main() -> None:
    rows = collect_adzuna_jobs()

    logging.info("Collected unique Adzuna rows: %d", len(rows))

    rows = rows[:TARGET_UNIQUE_JOBS]
    enriched = enrich_with_full_descriptions(rows)

    df = pd.DataFrame(enriched)

    # Useful quality flags
    df["has_probable_full_description"] = (
        df["full_description_len"].fillna(0) >
        df["api_description_len"].fillna(0) * 2
    )

    df["description_for_nlp"] = df["full_description"].where(
        df["full_description_len"].fillna(0) > 300,
        df["api_description_snippet"],
    )

    df.to_pickle(OUTPUT_PKL)

    # Optional: also save a compact audit CSV
    audit_cols = [
        "adzuna_id",
        "title",
        "company",
        "location",
        "created",
        "search_query",
        "redirect_url",
        "final_url",
        "full_description_source",
        "api_description_len",
        "full_description_len",
        "has_probable_full_description",
        "fetch_status",
        "fetch_error",
    ]
    df[audit_cols].to_csv("adzuna_uk_data_science_jobs_audit.csv", index=False)

    logging.info("Saved: %s", OUTPUT_PKL)
    logging.info("Rows: %d", len(df))
    logging.info(
        "Probable full descriptions: %d / %d",
        int(df["has_probable_full_description"].sum()),
        len(df),
    )


if __name__ == "__main__":
    main()
