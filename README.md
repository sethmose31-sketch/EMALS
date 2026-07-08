# Candidate Dossier — CV Scraper + Gemini Cleanup + Streamlit Dashboard

Turns a folder of messy CVs (PDF/Word) into a clean, structured JSON database,
then browses it in a rich Streamlit dashboard.

## How it works

```
cvs_input/            <- drop your CV files here (.pdf / .docx)
      |
      v
extract_text.py       <- pulls raw text out of each file
      |
      v
gemini_processor.py   <- sends raw text to Gemini, gets back clean structured JSON
      |
      v
build_database.py     <- runs the above over the whole folder, saves data/candidates.json
      |
      v
app.py                <- Streamlit dashboard reads data/candidates.json
```

## 1. Setup

```bash
pip install -r requirements.txt
```

Set your Gemini API key one of two ways:

```bash
export GEMINI_API_KEY="your-key-here"
```

or copy `.env.example` to `.env` and fill it in:

```bash
cp .env.example .env
# then edit .env
```

## 2. Add your CVs

Drop all your PDF and DOCX CV files into the `cvs_input/` folder.

> Note: legacy `.doc` files and scanned/image-only PDFs aren't supported —
> convert `.doc` to `.docx`, and run OCR first on scanned PDFs if needed.

## 3. Build the database

```bash
python build_database.py --input cvs_input --output data/candidates.json
```

This processes every CV, one at a time, and saves progress after each one —
so if it's interrupted, just re-run it and it'll pick up where it left off
(use `--force` to reprocess everything from scratch).

Each candidate becomes a JSON object with: full name, contact details,
professional summary, estimated years of experience, seniority level, full
work history with responsibilities/achievements, education, skills (technical
/ soft / tools), languages, certifications, notable projects, and any
flagged gaps or inconsistencies Gemini noticed.

## 4. Launch the dashboard

```bash
streamlit run app.py
```

You get:
- **Filters** — search by name/title/skill, filter by seniority and years of experience
- **Analytics** — experience distribution, seniority mix, top technical skills
- **Roster view** — a card per candidate with a quick summary and skill chips
- **Full dossier** — expand any candidate for their complete breakdown
- **Compare mode** — pick up to 3 candidates to view side by side
- **CSV export** — download the currently filtered list

## Notes on cost & accuracy

- Each CV = 1 Gemini API call. Check current Gemini pricing/rate limits for
  your volume before running this over hundreds of CVs at once.
- Gemini is instructed not to invent facts (companies, degrees, etc.) but it
  *does* infer things like total years of experience and seniority level —
  treat those as estimates, not ground truth, especially for edge cases.
- Re-running `build_database.py` without `--force` skips CVs already in the
  database, so it's safe to add new CVs to the folder incrementally.
# EMALS
