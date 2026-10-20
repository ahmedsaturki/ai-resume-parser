#!/usr/bin/env python3
"""
AI Resume Parser — Core Engine
Parses resumes (PDF) into structured JSON via OpenRouter (free tier).
Python + Supabase. NO n8n (owner decision).

Usage:
  python main.py --file resume.pdf --user <user_id>   # Parse one file
  python main.py --once                              # Process upload queue (cron mode)
"""
import os
import sys
import json
import argparse
import logging
import tempfile

import requests

# Optional PDF text extraction
try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
OPENROUTER_KEY = os.getenv("OPENROUTER_KEY", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("resume-parser")

EXTRACT_SCHEMA = {
    "name": "string",
    "email": "string",
    "phone": "string",
    "location": "string",
    "summary": "string (2-3 sentences)",
    "skills": ["array of strings"],
    "experience": [{"title": "", "company": "", "start_date": "", "end_date": "", "highlights": ["array"]}],
    "education": [{"degree": "", "institution": "", "year": ""}],
    "certifications": ["array of strings"],
    "languages": ["array of strings"],
    "years_of_experience": "number",
}


def extract_pdf_text(file_path: str) -> str:
    """Extract text from PDF using pdfplumber (pure Python, no paid deps)."""
    if not HAS_PDFPLUMBER:
        raise RuntimeError("pdfplumber not installed — run: pip install pdfplumber")
    text_parts = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            text_parts.append(page.extract_text() or "")
    return "\n".join(text_parts)


def parse_with_ai(text: str) -> dict:
    """Extract structured data via OpenRouter GPT-4o-mini (free tier)."""
    prompt = {
        "model": "openai/gpt-4o-mini",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a resume parsing engine. Extract structured data from the resume text. "
                    f"Respond ONLY with JSON matching this schema: {json.dumps(EXTRACT_SCHEMA)}. "
                    "Use empty string/array/0 for missing fields. Dates in YYYY-MM format where possible."
                ),
            },
            {"role": "user", "content": text[:10000]},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 2000,
    }
    r = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"},
        json=prompt,
        timeout=120,
    )
    r.raise_for_status()
    return json.loads(r.json()["choices"][0]["message"]["content"])


def supabase_headers():
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def fetch_pending_uploads():
    """Fetch uploaded resumes awaiting parsing."""
    url = f"{SUPABASE_URL}/rest/v1/resumes?status=eq.uploaded&order=created_at.asc&limit=20"
    r = requests.get(url, headers=supabase_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


def download_file(storage_path: str) -> str:
    """Download file from Supabase Storage to a temp file. Returns local path."""
    url = f"{SUPABASE_URL}/storage/v1/object/{storage_path}"
    r = requests.get(url, headers={"apikey": SUPABASE_SERVICE_KEY,
                                   "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}, timeout=60)
    r.raise_for_status()
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.write(r.content)
    tmp.close()
    return tmp.name


def store_parsed(resume_id: str, parsed: dict):
    url = f"{SUPABASE_URL}/rest/v1/resumes?id=eq.{resume_id}"
    payload = {
        "status": "parsed",
        "parsed_data": parsed,
        "parse_confidence": 0.95,  # heuristic; refine with validation
        "parsed_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    }
    r = requests.patch(url, headers=supabase_headers(), json=payload, timeout=30)
    r.raise_for_status()


def process_queue():
    """Cron mode: process all pending uploads."""
    processed = 0
    for row in fetch_pending_uploads():
        try:
            local = download_file(row["storage_path"])
            text = extract_pdf_text(local)
            os.unlink(local)
            if len(text.strip()) < 50:
                raise ValueError("PDF has no extractable text (likely scanned image)")
            parsed = parse_with_ai(text)
            store_parsed(row["id"], parsed)
            processed += 1
            log.info(f"resume {row['id']}: parsed for user {row['user_id']}")
        except Exception as e:
            log.error(f"resume {row['id']} failed: {e}")
            try:
                requests.patch(
                    f"{SUPABASE_URL}/rest/v1/resumes?id=eq.{row['id']}",
                    headers=supabase_headers(),
                    json={"status": "failed", "error_message": str(e)[:500]},
                    timeout=30,
                )
            except Exception:
                pass
    return processed


def parse_single(file_path: str, user_id: str):
    """CLI mode: parse one file and store."""
    text = extract_pdf_text(file_path)
    parsed = parse_with_ai(text)
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/resumes",
        headers=supabase_headers(),
        json={"user_id": user_id, "status": "parsed", "parsed_data": parsed, "file_name": os.path.basename(file_path)},
        timeout=30,
    )
    r.raise_for_status()
    return parsed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", help="Path to a PDF resume")
    parser.add_argument("--user", help="User ID (with --file)")
    parser.add_argument("--once", action="store_true", help="Process upload queue once (cron)")
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not OPENROUTER_KEY:
        log.error("SUPABASE_URL, SUPABASE_SERVICE_KEY, OPENROUTER_KEY must be set")
        sys.exit(1)

    if args.file:
        if not args.user:
            log.error("--user required with --file")
            sys.exit(1)
        parsed = parse_single(args.file, args.user)
        print(json.dumps(parsed, ensure_ascii=False, indent=2))
        return

    n = process_queue()
    log.info(f"Processed {n} resume(s)")


if __name__ == "__main__":
    main()
