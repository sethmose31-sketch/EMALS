"""
gemini_processor.py
--------------------
Sends raw, messy CV text to Gemini and gets back a clean, structured JSON
object following a fixed schema. Gemini is used here to:
  - fix OCR/typo issues from extraction
  - normalize dates, phone numbers, job titles
  - infer a sensible summary and total years of experience
  - split unstructured blobs of text into structured fields
"""

import json
import time
import google.generativeai as genai

# Change this if you want a different Gemini model.
MODEL_NAME = "gemini-2.5-flash"

SCHEMA_INSTRUCTIONS = """
You are an expert HR data analyst. You will be given the raw extracted text
of a single candidate's CV/resume. The text may contain extraction glitches,
inconsistent spacing, OCR typos, or jumbled ordering. Clean it up and return
ONLY a single valid JSON object (no markdown fences, no commentary) with
EXACTLY this structure:

{
  "full_name": string or null,
  "contact": {
    "email": string or null,
    "phone": string or null,
    "location": string or null,
    "linkedin": string or null,
    "portfolio_or_github": string or null
  },
  "summary": string,               // 2-3 sentence professional summary you write based on the CV
  "current_title": string or null,
  "total_experience_years": number or null,  // your best estimate, based on work history dates
  "seniority_level": string,       // one of: "Entry", "Junior", "Mid", "Senior", "Lead", "Executive"
  "work_experience": [
    {
      "company": string,
      "role": string,
      "start_date": string or null,   // format YYYY-MM if possible, else raw text
      "end_date": string or null,     // "Present" if current
      "duration": string or null,     // e.g. "2 yrs 3 mos"
      "responsibilities": [string, ...],
      "key_achievements": [string, ...]
    }
  ],
  "education": [
    {
      "institution": string,
      "degree": string or null,
      "field_of_study": string or null,
      "graduation_year": string or null
    }
  ],
  "skills": {
    "technical": [string, ...],
    "soft": [string, ...],
    "tools_and_platforms": [string, ...]
  },
  "languages": [string, ...],
  "certifications": [string, ...],
  "notable_projects": [string, ...],
  "red_flags_or_gaps": [string, ...],   // e.g. unexplained employment gaps, very short tenures
  "data_quality_notes": [string, ...]   // note any fields you had to infer/guess vs. found explicitly
}

Rules:
- If information is genuinely missing, use null or an empty array. Do NOT invent facts like companies or degrees that aren't in the text.
- You MAY infer/compute things like total_experience_years, seniority_level, and the summary — that is expected.
- Correct obvious typos and normalize formatting (e.g. phone numbers, capitalization of names).
- Output must be valid JSON parseable by json.loads(). Nothing else.
"""


def _clean_json_response(raw: str) -> str:
    """Strip markdown code fences if the model adds them despite instructions."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0]
    return text.strip()


def process_cv(raw_text: str, api_key: str, max_retries: int = 3) -> dict:
    """
    Send raw CV text to Gemini and return a structured dict following the
    schema above. Retries on transient errors / bad JSON.
    """
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name=MODEL_NAME,
        system_instruction=SCHEMA_INSTRUCTIONS,
    )

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            response = model.generate_content(
                f"Here is the raw CV text:\n\n---\n{raw_text}\n---",
                generation_config={
                    "temperature": 0.2,
                    "response_mime_type": "application/json",
                },
            )
            cleaned = _clean_json_response(response.text)
            data = json.loads(cleaned)
            return data
        except json.JSONDecodeError as e:
            last_error = f"JSON parse error: {e}"
        except Exception as e:
            last_error = str(e)

        time.sleep(1.5 * attempt)  # backoff before retry

    # If every attempt failed, return a placeholder so the pipeline can
    # continue processing the rest of the folder instead of crashing.
    return {
        "full_name": None,
        "contact": {"email": None, "phone": None, "location": None,
                    "linkedin": None, "portfolio_or_github": None},
        "summary": "Could not be processed automatically.",
        "current_title": None,
        "total_experience_years": None,
        "seniority_level": "Unknown",
        "work_experience": [],
        "education": [],
        "skills": {"technical": [], "soft": [], "tools_and_platforms": []},
        "languages": [],
        "certifications": [],
        "notable_projects": [],
        "red_flags_or_gaps": [],
        "data_quality_notes": [f"PROCESSING FAILED: {last_error}"],
    }
