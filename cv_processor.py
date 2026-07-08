"""
cv_processor.py
----------------
Multi-provider CV processing system with Ollama (local Llama) as the primary,
free option, and optional cloud fallbacks (OpenAI, Anthropic) if the local
model is unavailable or fails.

Handles a real-world mixed input folder: PDFs, Word docs (.doc/.docx), and
scanned images (.png/.jpg/.tiff/etc.), including files that are actually
certificates rather than CVs.

Key additions vs. the original single-provider version:
  - Abstract `CVProcessorBase` interface so any provider can be swapped in.
  - `OpenAIProcessor` and `AnthropicProcessor` as optional fallback providers.
  - `ProviderChain` that tries providers in priority order and automatically
    falls back to the next one if a provider errors out or is unavailable.
  - A file-ingestion layer (`extract_text_from_file`) that pulls text out of
    PDFs, Word docs, and images — with automatic OCR fallback for scanned
    PDFs and image files.
  - Lightweight document classification (`classify_document_type`) that
    tells CVs apart from certificates/diplomas so certificates don't get
    force-fit into the CV schema.
  - Batch processing (`process_cv_batch`) for a whole folder of mixed files:
    it groups certificates with the CV they likely belong to (by filename),
    processes each CV, and folds in a note about the candidate's matched
    certificates.
  - Provider availability checks (e.g. is Ollama actually running?) so the
    chain can skip a provider quickly instead of retrying a dead endpoint.

System dependencies used for extraction (all pre-installed in this
environment): `pdftotext` / `pdftoppm` (poppler-utils), `pandoc`, `tesseract`.
Python deps: `pytesseract`, `Pillow`, `python-docx` (fallback only).
"""

import json
import time
import os
import re
import glob
import shutil
import subprocess
import tempfile
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List, Tuple
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================================
# Schema Definition
# ============================================================================

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

REQUIRED_TOP_LEVEL_FIELDS = [
    "full_name", "contact", "summary", "current_title", "total_experience_years",
    "seniority_level", "work_experience", "education", "skills", "languages",
    "certifications", "notable_projects", "red_flags_or_gaps", "data_quality_notes",
]


def _clean_response(raw: str) -> str:
    """Strip markdown code fences if present."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0]
    return text.strip()


def _validate_and_backfill(result: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure all expected top-level fields exist, even if the model dropped some."""
    defaults = _get_placeholder()
    for field in REQUIRED_TOP_LEVEL_FIELDS:
        if field not in result or result[field] is None and field not in ("full_name", "current_title", "total_experience_years"):
            if field not in result:
                result[field] = defaults[field]
    return result


# ============================================================================
# File Ingestion Layer — pulls raw text out of PDFs, Word docs, and images
# ============================================================================

OCR_MIN_CHARS = 100  # below this, a "text" extraction is treated as empty/scanned
SUPPORTED_EXTENSIONS = (".pdf", ".docx", ".doc", ".txt",
                        ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp")


def _run(cmd: List[str]) -> str:
    """Run a subprocess command and return stdout, raising on failure."""
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"Command {' '.join(cmd)} failed: {result.stderr.strip()}")
    return result.stdout


def _ocr_image_file(image_path: str) -> str:
    """OCR a single image file using pytesseract."""
    from PIL import Image
    import pytesseract
    return pytesseract.image_to_string(Image.open(image_path))


def _ocr_pdf(pdf_path: str) -> str:
    """Rasterize every page of a (likely scanned) PDF and OCR each one."""
    with tempfile.TemporaryDirectory() as tmp:
        prefix = os.path.join(tmp, "page")
        _run(["pdftoppm", "-jpeg", "-r", "200", pdf_path, prefix])
        pages = sorted(glob.glob(f"{prefix}*.jpg"))
        if not pages:
            raise RuntimeError("pdftoppm produced no page images")
        texts = [_ocr_image_file(p) for p in pages]
        return "\n\n".join(texts)


def extract_text_from_pdf(pdf_path: str) -> Tuple[str, str]:
    """
    Extract text from a PDF. Tries the embedded text layer first (fast, cheap);
    falls back to OCR if the PDF is scanned / has little to no text layer.

    Returns (text, method) where method is "text_layer" or "ocr".
    """
    try:
        text = _run(["pdftotext", "-layout", pdf_path, "-"])
    except Exception as e:
        logger.warning(f"pdftotext failed on {pdf_path}: {e}")
        text = ""

    if len(text.strip()) >= OCR_MIN_CHARS:
        return text, "text_layer"

    logger.info(f"🔎 {os.path.basename(pdf_path)} looks scanned (little/no text layer) — running OCR")
    try:
        return _ocr_pdf(pdf_path), "ocr"
    except Exception as e:
        logger.error(f"OCR fallback failed for {pdf_path}: {e}")
        return text, "text_layer"  # return whatever little we had


def extract_text_from_docx(path: str) -> str:
    """Extract text from a .docx (or .dotx) file. Pandoc first, python-docx as fallback."""
    try:
        return _run(["pandoc", "-t", "plain", path])
    except Exception as e:
        logger.warning(f"pandoc failed on {path}, falling back to python-docx: {e}")

    try:
        import docx
        document = docx.Document(path)
        return "\n".join(p.text for p in document.paragraphs)
    except Exception as e:
        logger.error(f"python-docx fallback also failed for {path}: {e}")
        return ""


def extract_text_from_doc(path: str) -> str:
    """Extract text from a legacy .doc (binary Word) file."""
    try:
        return _run(["pandoc", "-t", "plain", path])
    except Exception as e:
        logger.warning(f"pandoc failed on legacy .doc {path}: {e}")

    if shutil.which("antiword"):
        try:
            return _run(["antiword", path])
        except Exception as e:
            logger.error(f"antiword also failed for {path}: {e}")

    logger.error(f"Could not extract text from legacy .doc file: {path}")
    return ""


def extract_text_from_file(path: str) -> Tuple[str, str]:
    """
    Universal extractor: dispatches by extension.

    Returns (text, method) where method is one of:
    "text_layer", "ocr", "pandoc", "docx_lib", "image_ocr", "plain_text", "unsupported"
    """
    ext = os.path.splitext(path)[1].lower()

    if ext == ".pdf":
        return extract_text_from_pdf(path)
    elif ext == ".docx":
        return extract_text_from_docx(path), "pandoc"
    elif ext == ".doc":
        return extract_text_from_doc(path), "pandoc"
    elif ext == ".txt":
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read(), "plain_text"
        except Exception as e:
            logger.error(f"Could not read text file {path}: {e}")
            return "", "plain_text"
    elif ext in (".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"):
        try:
            return _ocr_image_file(path), "image_ocr"
        except Exception as e:
            logger.error(f"OCR failed for image {path}: {e}")
            return "", "image_ocr"
    else:
        logger.warning(f"Unsupported file extension for {path}")
        return "", "unsupported"


# ============================================================================
# Document Classification — tell CVs apart from certificates/diplomas/other
# ============================================================================

CV_KEYWORDS = [
    "work experience", "professional experience", "employment history",
    "curriculum vitae", "resume", "résumé", "career objective", "education",
    "skills", "references available", "references", "objective", "profile summary",
    "linkedin.com/in", "responsibilities", "achievements",
]

CERTIFICATE_KEYWORDS = [
    "this is to certify", "certificate of completion", "certifies that",
    "in recognition of", "has successfully completed", "awarded to",
    "certificate of achievement", "diploma", "this certifies", "issued to",
    "has fulfilled the requirements", "certification", "certificate number",
]


def classify_document_type(text: str) -> str:
    """
    Lightweight heuristic classifier: "cv", "certificate", or "unknown".
    Keeps things free/local (no LLM call needed just to sort the input folder).
    """
    if not text or len(text.strip()) < 20:
        return "unknown"

    lower = text.lower()
    cv_score = sum(1 for kw in CV_KEYWORDS if kw in lower)
    cert_score = sum(1 for kw in CERTIFICATE_KEYWORDS if kw in lower)

    if cert_score >= 2 and cert_score > cv_score:
        return "certificate"
    if cv_score >= 2 and cv_score >= cert_score:
        return "cv"
    return "unknown"


_STEM_STOPWORDS = {
    "cv", "resume", "resumes", "curriculum", "vitae", "certificate", "certificates",
    "cert", "certs", "certification", "certifications", "diploma", "diplomas",
    "the", "of", "final", "updated", "new", "copy",
}


def _normalize_stem(filename: str) -> str:
    """
    Normalize a filename stem for grouping certificates with their CV, e.g.
    'john_doe_cv.pdf' and 'John_Doe-AWS-Certificate.png' -> both become 'johndoe...'
    with CV/certificate-type words and separators stripped, so they overlap.
    """
    stem = os.path.splitext(os.path.basename(filename))[0].lower()
    stem = re.sub(r"[^a-z]+", " ", stem)  # underscores/dashes/digits/etc -> spaces
    tokens = [t for t in stem.split() if t not in _STEM_STOPWORDS]
    return "".join(tokens)


# ============================================================================
# Provider Interface
# ============================================================================

class CVProcessorBase(ABC):
    """Common interface every provider (local or cloud) must implement."""

    max_retries: int = 5

    @abstractmethod
    def get_provider_name(self) -> str:
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Cheap check so the chain can skip a dead provider without burning retries."""
        ...

    @abstractmethod
    def _call_model(self, raw_text: str) -> str:
        """Return the raw text content from the model. Raise on failure."""
        ...

    def process(self, raw_text: str) -> Optional[Dict[str, Any]]:
        if not raw_text or len(raw_text.strip()) < 50:
            logger.warning(f"Text too short ({len(raw_text)} chars), skipping")
            return None

        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                content = self._call_model(raw_text)
                cleaned = _clean_response(content)
                result = json.loads(cleaned)
                result = _validate_and_backfill(result)
                logger.info(f"✅ Successfully processed with {self.get_provider_name()}")
                return result

            except json.JSONDecodeError as e:
                last_error = f"JSON parse error: {e}"
                logger.warning(f"[{self.get_provider_name()}] Attempt {attempt}/{self.max_retries} failed: {last_error}")
            except Exception as e:
                last_error = str(e)
                logger.warning(f"[{self.get_provider_name()}] Attempt {attempt}/{self.max_retries} failed: {last_error}")

            if attempt < self.max_retries:
                wait_time = (1.5 ** attempt) + (attempt * 0.5)
                logger.info(f"⏳ Waiting {wait_time:.1f}s before retry...")
                time.sleep(wait_time)

        logger.error(f"❌ [{self.get_provider_name()}] All {self.max_retries} attempts failed: {last_error}")
        return None


# ============================================================================
# Ollama Processor (Primary - Free, Local)
# ============================================================================

class OllamaProcessor(CVProcessorBase):
    """Ollama local model processor - completely free, runs on your machine."""

    def __init__(self, model: str = "llama3.2", base_url: str = "http://localhost:11434", max_retries: int = 5):
        try:
            from ollama import Client
        except ImportError:
            raise ImportError("Please install ollama: pip install ollama")
        self.client = Client(host=base_url)
        self.model_name = model
        self.base_url = base_url
        self.max_retries = max_retries

    def get_provider_name(self) -> str:
        return f"Ollama ({self.model_name})"

    def is_available(self) -> bool:
        """Ping Ollama's local server to see if it's actually running."""
        try:
            self.client.list()
            return True
        except Exception as e:
            logger.warning(f"Ollama not reachable at {self.base_url}: {e}")
            return False

    def _call_model(self, raw_text: str) -> str:
        response = self.client.chat(
            model=self.model_name,
            messages=[
                {"role": "system", "content": SCHEMA_INSTRUCTIONS},
                {"role": "user", "content": f"Here is the raw CV text:\n\n---\n{raw_text}\n---"}
            ],
            options={
                "temperature": 0.2,
                "num_predict": 4096,
            },
            stream=False
        )
        return response["message"]["content"]


# ============================================================================
# OpenAI Processor (Optional Fallback)
# ============================================================================

class OpenAIProcessor(CVProcessorBase):
    """Cloud fallback using OpenAI's API. Requires OPENAI_API_KEY env var (or passed in)."""

    def __init__(self, model: str = "gpt-4o-mini", api_key: Optional[str] = None, max_retries: int = 3):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("Please install openai: pip install openai")
        key = api_key or os.environ.get("OPENAI_API_KEY")
        self.client = OpenAI(api_key=key) if key else None
        self.model_name = model
        self.max_retries = max_retries

    def get_provider_name(self) -> str:
        return f"OpenAI ({self.model_name})"

    def is_available(self) -> bool:
        return self.client is not None

    def _call_model(self, raw_text: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model_name,
            temperature=0.2,
            max_tokens=4096,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SCHEMA_INSTRUCTIONS},
                {"role": "user", "content": f"Here is the raw CV text:\n\n---\n{raw_text}\n---"}
            ],
        )
        return response.choices[0].message.content


# ============================================================================
# Anthropic Processor (Optional Fallback)
# ============================================================================

class AnthropicProcessor(CVProcessorBase):
    """Cloud fallback using Anthropic's API. Requires ANTHROPIC_API_KEY env var (or passed in)."""

    def __init__(self, model: str = "claude-sonnet-5", api_key: Optional[str] = None, max_retries: int = 3):
        try:
            from anthropic import Anthropic
        except ImportError:
            raise ImportError("Please install anthropic: pip install anthropic")
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.client = Anthropic(api_key=key) if key else None
        self.model_name = model
        self.max_retries = max_retries

    def get_provider_name(self) -> str:
        return f"Anthropic ({self.model_name})"

    def is_available(self) -> bool:
        return self.client is not None

    def _call_model(self, raw_text: str) -> str:
        response = self.client.messages.create(
            model=self.model_name,
            max_tokens=4096,
            temperature=0.2,
            system=SCHEMA_INSTRUCTIONS,
            messages=[
                {"role": "user", "content": f"Here is the raw CV text:\n\n---\n{raw_text}\n---"}
            ],
        )
        return "".join(block.text for block in response.content if block.type == "text")


# ============================================================================
# Provider Chain - tries providers in order, falls back automatically
# ============================================================================

class ProviderChain:
    """
    Tries each provider in order (e.g. local Ollama first, then cloud fallbacks)
    and returns the first successful result. Skips providers that report
    themselves unavailable instead of wasting retries on them.
    """

    def __init__(self, providers: List[CVProcessorBase]):
        if not providers:
            raise ValueError("ProviderChain needs at least one provider")
        self.providers = providers

    def process(self, raw_text: str) -> Dict[str, Any]:
        for provider in self.providers:
            try:
                if not provider.is_available():
                    logger.warning(f"⏭️  Skipping {provider.get_provider_name()} (not available)")
                    continue
            except Exception as e:
                logger.warning(f"⏭️  Skipping {provider.get_provider_name()} (availability check errored: {e})")
                continue

            logger.info(f"➡️  Trying provider: {provider.get_provider_name()}")
            result = provider.process(raw_text)
            if result is not None:
                result.setdefault("data_quality_notes", []).append(
                    f"Processed by {provider.get_provider_name()}"
                )
                return result
            logger.warning(f"⚠️  {provider.get_provider_name()} failed, falling back to next provider...")

        logger.error("❌ All providers in the chain failed")
        return _get_placeholder("All providers in the chain failed")


# ============================================================================
# Convenience Functions
# ============================================================================

def build_default_chain(
    ollama_model: str = "llama3.2",
    ollama_base_url: str = "http://localhost:11434",
    openai_model: str = "gpt-4o-mini",
    anthropic_model: str = "claude-sonnet-5",
    use_cloud_fallback: bool = True,
) -> ProviderChain:
    """
    Build the default provider chain: local Ollama first (free), then optional
    cloud fallbacks if API keys are present in the environment.
    """
    providers: List[CVProcessorBase] = []

    try:
        providers.append(OllamaProcessor(model=ollama_model, base_url=ollama_base_url))
    except ImportError as e:
        logger.warning(f"Ollama python package not installed, skipping local provider: {e}")

    if use_cloud_fallback:
        for cls, model in ((OpenAIProcessor, openai_model), (AnthropicProcessor, anthropic_model)):
            try:
                providers.append(cls(model=model))
            except ImportError as e:
                logger.info(f"Skipping optional provider {cls.__name__}: {e}")

    if not providers:
        raise RuntimeError(
            "No usable provider: install the `ollama` package for local processing "
            "(pip install ollama) and/or set OPENAI_API_KEY / ANTHROPIC_API_KEY for cloud fallback."
        )

    return ProviderChain(providers)


def process_cv(
    raw_text: str,
    model: str = "llama3.2",
    base_url: str = "http://localhost:11434",
    use_cloud_fallback: bool = True,
) -> Dict[str, Any]:
    """
    Process raw CV text, preferring local Ollama and falling back to cloud
    providers (if configured) only if Ollama is unavailable or fails.

    Args:
        raw_text: Raw extracted text from CV
        model: Ollama model to use (default: llama3.2)
        base_url: Ollama API URL (default: http://localhost:11434)
        use_cloud_fallback: whether to add OpenAI/Anthropic as fallbacks

    Returns:
        Structured dictionary following the schema, or a placeholder on total failure
    """
    try:
        chain = build_default_chain(
            ollama_model=model, ollama_base_url=base_url, use_cloud_fallback=use_cloud_fallback
        )
        return chain.process(raw_text)
    except Exception as e:
        logger.error(f"Error building provider chain: {e}")
        return _get_placeholder(f"Chain creation failed: {str(e)}")


def process_cv_batch(
    input_dir: str,
    output_dir: str,
    model: str = "llama3.2",
    base_url: str = "http://localhost:11434",
    use_cloud_fallback: bool = True,
    recursive: bool = False,
) -> Dict[str, Any]:
    """
    Process a real-world mixed input folder — PDFs, Word docs, images, plain
    text — that may also contain certificates/diplomas alongside actual CVs.

    Steps:
      1. Extract text from every supported file (OCR fallback for scans/images).
      2. Classify each file as "cv", "certificate", or "unknown".
      3. Group certificates with the CV they likely belong to, by filename
         (e.g. "jane_doe_cv.pdf" + "jane_doe_aws_certificate.png").
      4. Run each CV through the provider chain (local Ollama first), and fold
         a note about matched certificates into the result.
      5. Write one JSON per candidate into `output_dir`, plus a batch report
         and a file for any certificates that couldn't be matched to a CV.

    Returns a summary dict:
        {
          "processed": int, "failed": list[str], "unknown_files": list[str],
          "unmatched_certificates": list[str], "output_dir": str
        }
    """
    os.makedirs(output_dir, exist_ok=True)

    pattern = "**/*" if recursive else "*"
    all_files = [
        f for f in glob.glob(os.path.join(input_dir, pattern), recursive=recursive)
        if os.path.isfile(f) and os.path.splitext(f)[1].lower() in SUPPORTED_EXTENSIONS
    ]

    if not all_files:
        logger.warning(f"No supported files found in {input_dir}")
        return {"processed": 0, "failed": [], "unknown_files": [], "unmatched_certificates": [], "output_dir": output_dir}

    # --- Pass 1: extract + classify every file -------------------------------
    cv_files: Dict[str, str] = {}           # filepath -> text
    certificate_files: Dict[str, str] = {}  # filepath -> text
    unknown_files: List[str] = []
    extraction_report: Dict[str, str] = {}  # filepath -> extraction method

    for filepath in sorted(all_files):
        name = os.path.basename(filepath)
        logger.info(f"🔍 Reading {name}...")
        text, method = extract_text_from_file(filepath)
        extraction_report[filepath] = method

        if len(text.strip()) < 20:
            logger.warning(f"⚠️  Got almost no text from {name} (method={method}) — marking unknown")
            unknown_files.append(filepath)
            continue

        doc_type = classify_document_type(text)
        if doc_type == "cv":
            cv_files[filepath] = text
        elif doc_type == "certificate":
            certificate_files[filepath] = text
        else:
            unknown_files.append(filepath)

    logger.info(
        f"📊 Classified {len(cv_files)} CV(s), {len(certificate_files)} certificate(s), "
        f"{len(unknown_files)} unknown file(s)"
    )

    # --- Pass 2: group certificates with their most likely CV ----------------
    cv_stems = {path: _normalize_stem(path) for path in cv_files}
    cert_matches: Dict[str, List[str]] = {path: [] for path in cv_files}  # cv_path -> [cert_paths]
    unmatched_certificates: List[str] = []

    for cert_path in certificate_files:
        cert_stem = _normalize_stem(cert_path)
        match = None
        for cv_path, stem in cv_stems.items():
            if stem and (stem in cert_stem or cert_stem in stem):
                match = cv_path
                break
        if match:
            cert_matches[match].append(cert_path)
        else:
            unmatched_certificates.append(cert_path)

    # --- Pass 3: process each CV, folding in matched certificate notes -------
    chain = build_default_chain(ollama_model=model, ollama_base_url=base_url, use_cloud_fallback=use_cloud_fallback)

    processed = 0
    failed = []

    for cv_path, raw_text in cv_files.items():
        name = os.path.splitext(os.path.basename(cv_path))[0]
        logger.info(f"📄 Processing CV: {name}...")

        result = chain.process(raw_text)

        matched_certs = cert_matches.get(cv_path, [])
        if matched_certs:
            cert_names = [os.path.basename(c) for c in matched_certs]
            result.setdefault("certifications", [])
            for cert_name in cert_names:
                result["certifications"].append(f"[from attached file] {cert_name}")
            result.setdefault("data_quality_notes", []).append(
                f"Matched {len(cert_names)} certificate file(s) by filename: {', '.join(cert_names)}"
            )

        out_path = os.path.join(output_dir, f"{name}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)

        if "PROCESSING FAILED" in json.dumps(result.get("data_quality_notes", [])):
            failed.append(name)
        else:
            processed += 1

    # --- Write auxiliary reports ----------------------------------------------
    if unmatched_certificates:
        unmatched_path = os.path.join(output_dir, "_unmatched_certificates.json")
        with open(unmatched_path, "w", encoding="utf-8") as f:
            json.dump(
                [{"file": os.path.basename(p), "path": p, "excerpt": certificate_files[p][:300]}
                 for p in unmatched_certificates],
                f, indent=2,
            )
        logger.info(f"📎 {len(unmatched_certificates)} certificate(s) could not be matched to a CV "
                    f"— see _unmatched_certificates.json")

    report_path = os.path.join(output_dir, "_batch_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({
            "total_files_seen": len(all_files),
            "cv_files": [os.path.basename(p) for p in cv_files],
            "certificate_files": [os.path.basename(p) for p in certificate_files],
            "unknown_files": [os.path.basename(p) for p in unknown_files],
            "extraction_methods": {os.path.basename(k): v for k, v in extraction_report.items()},
            "processed": processed,
            "failed": failed,
        }, f, indent=2)

    logger.info(f"🏁 Batch complete: {processed} CV(s) processed, {len(failed)} failed, "
                f"{len(unknown_files)} unrecognized file(s) skipped")

    return {
        "processed": processed,
        "failed": failed,
        "unknown_files": [os.path.basename(p) for p in unknown_files],
        "unmatched_certificates": [os.path.basename(p) for p in unmatched_certificates],
        "output_dir": output_dir,
    }


def _get_placeholder(error_message: str = None) -> Dict[str, Any]:
    """Return a placeholder object for failed processing."""
    return {
        "full_name": None,
        "contact": {
            "email": None, "phone": None, "location": None,
            "linkedin": None, "portfolio_or_github": None
        },
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
        "data_quality_notes": [f"PROCESSING FAILED: {error_message or 'Unknown error'}"],
    }


# ============================================================================
# Usage Examples
# ============================================================================

if __name__ == "__main__":
    sample_text = """
    John Doe
    Senior Software Engineer
    Email: john.doe@example.com
    Phone: +1-555-123-4567
    Location: San Francisco, CA

    Summary: Experienced software engineer with 8+ years in full-stack development.

    Work Experience:
    - Google, Senior Software Engineer (2020-Present)
      Led team of 5 developers building cloud infrastructure
    - Amazon, Software Engineer (2016-2020)
      Built AWS services using Python and Java

    Education:
    - Stanford University, MS Computer Science (2016)
    - UC Berkeley, BS Computer Science (2014)

    Skills: Python, Java, AWS, Docker, Kubernetes, React
    """

    print("=== Testing single CV (Ollama primary, cloud fallback if configured) ===")
    result = process_cv(sample_text, model="llama3.2")
    print(json.dumps(result, indent=2))

    # Batch example — processes a real mixed folder of PDFs/DOCX/images,
    # separating out certificates automatically (uncomment to use):
    # summary = process_cv_batch(
    #     input_dir="./cvs_input",
    #     output_dir="./cvs_structured",
    #     model="llama3.2",
    # )
    # print(summary)