"""
build_database.py
-----------------
Processes CVs from various formats (PDF, DOC/DOCX, images) using Gemini AI
and builds a structured JSON database incrementally with proper retry handling.

Usage:
    export GEMINI_API_KEY=your-key
    python3 build_database.py --input cvs_folder --output data/candidates.json
    
    # Resume from last processed file
    python3 build_database.py --input cvs_folder --output data/candidates.json --resume
"""

import os
import json
import time
import argparse
from datetime import datetime
from pathlib import Path
import logging
import re

# Document processing libraries
import PyPDF2
import docx
from PIL import Image
import pytesseract
from pdf2image import convert_from_path

# Google Gemini
from google import genai
from google.genai import types

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------------
# Database Management
# ----------------------------------------------------------------------------

class CVDatabase:
    """Manages incremental writing of candidate data to JSON file."""
    
    def __init__(self, output_file, resume=False):
        self.output_file = Path(output_file)
        self.output_file.parent.mkdir(parents=True, exist_ok=True)
        self.resume = resume
        self.candidates = []
        self.processed_files = set()
        self.total_files = 0
        self.successful_extractions = 0
        self.start_time = datetime.now()
        
        # Load existing data if resuming
        if resume and self.output_file.exists():
            self._load_existing()
    
    def _load_existing(self):
        """Load existing database for resuming."""
        try:
            with open(self.output_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.candidates = data.get("candidates", [])
                # Track which files have been processed
                for candidate in self.candidates:
                    if "source_file" in candidate:
                        self.processed_files.add(candidate["source_file"])
                self.successful_extractions = len(self.candidates)
                logger.info(f"✅ Resuming from existing database with {len(self.candidates)} candidates")
        except Exception as e:
            logger.warning(f"Could not load existing database: {e}")
            self.candidates = []
            self.processed_files = set()
    
    def add_candidate(self, candidate_data):
        """Add a candidate and immediately save to disk."""
        if not candidate_data:
            return False
        
        # Check if already processed (by filename)
        source_file = candidate_data.get("source_file")
        if source_file in self.processed_files:
            logger.info(f"⏭️ Skipping already processed file: {source_file}")
            return False
        
        # Add to list
        self.candidates.append(candidate_data)
        self.processed_files.add(source_file)
        self.successful_extractions += 1
        
        # Immediately save to disk
        self._save_to_disk()
        return True
    
    def _save_to_disk(self):
        """Save current state to JSON file."""
        database = {
            "generated_at": datetime.now().isoformat(),
            "started_at": self.start_time.isoformat(),
            "total_files_processed": self.total_files,
            "successful_extractions": self.successful_extractions,
            "candidates": self.candidates,
            "processed_files": list(self.processed_files),
            "last_updated": datetime.now().isoformat()
        }
        
        try:
            # Write atomically - write to temp file then rename
            temp_file = self.output_file.with_suffix('.tmp')
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(database, f, indent=2, ensure_ascii=False)
            temp_file.rename(self.output_file)
            logger.debug(f"💾 Database saved: {len(self.candidates)} candidates")
        except Exception as e:
            logger.error(f"❌ Error saving database: {e}")
    
    def get_stats(self):
        """Get processing statistics."""
        return {
            "total_candidates": len(self.candidates),
            "processed_files": len(self.processed_files),
            "successful_extractions": self.successful_extractions,
            "total_files": self.total_files
        }
    
    def is_processed(self, filename):
        """Check if a file has already been processed."""
        return filename in self.processed_files

# ----------------------------------------------------------------------------
# Text Extraction Functions for Different File Types
# ----------------------------------------------------------------------------

def extract_text_from_pdf(pdf_path):
    """Extract text from PDF files."""
    try:
        text = ""
        with open(pdf_path, 'rb') as file:
            pdf_reader = PyPDF2.PdfReader(file)
            for page in pdf_reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
        
        # If no text extracted (scanned PDF), try OCR
        if not text.strip():
            logger.info(f"PDF appears to be scanned, attempting OCR: {pdf_path.name}")
            text = extract_text_from_image_pdf(pdf_path)
        
        return text.strip()
    except Exception as e:
        logger.error(f"Error extracting text from PDF {pdf_path.name}: {e}")
        return ""

def extract_text_from_image_pdf(pdf_path):
    """Extract text from scanned PDFs using OCR."""
    try:
        images = convert_from_path(pdf_path, dpi=300)
        text = ""
        for image in images:
            text += pytesseract.image_to_string(image) + "\n"
        return text.strip()
    except Exception as e:
        logger.error(f"Error performing OCR on PDF {pdf_path.name}: {e}")
        return ""

def extract_text_from_docx(docx_path):
    """Extract text from DOCX files."""
    try:
        doc = docx.Document(docx_path)
        text = "\n".join([paragraph.text for paragraph in doc.paragraphs])
        return text.strip()
    except Exception as e:
        logger.error(f"Error extracting text from DOCX {docx_path.name}: {e}")
        return ""

def extract_text_from_image(image_path):
    """Extract text from image files using OCR."""
    try:
        image = Image.open(image_path)
        text = pytesseract.image_to_string(image)
        return text.strip()
    except Exception as e:
        logger.error(f"Error extracting text from image {image_path.name}: {e}")
        return ""

def extract_text_from_file(file_path):
    """
    Determine file type and extract text accordingly.
    
    Supported formats:
    - PDF: .pdf
    - Word: .docx, .doc
    - Images: .jpg, .jpeg, .png, .tiff, .bmp
    """
    file_path = Path(file_path)
    extension = file_path.suffix.lower()
    
    logger.info(f"📄 Processing: {file_path.name}")
    
    if extension == '.pdf':
        return extract_text_from_pdf(file_path)
    elif extension in ['.docx']:
        return extract_text_from_docx(file_path)
    elif extension in ['.doc']:
        # For old .doc files, we'll try to extract as text or inform user
        logger.warning(f"Legacy .doc file detected: {file_path.name}. Consider converting to .docx or PDF.")
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read()
        except:
            return ""
    elif extension in ['.jpg', '.jpeg', '.png', '.tiff', '.bmp']:
        return extract_text_from_image(file_path)
    elif extension in ['.txt']:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        except:
            return ""
    else:
        logger.warning(f"Unsupported file type: {extension} for {file_path.name}")
        return ""

# ----------------------------------------------------------------------------
# Gemini AI Extraction Function with Smart Retry
# ----------------------------------------------------------------------------

def extract_cv_data_with_ai(filename, raw_text, api_key=None, max_retries=5):
    """
    Sends raw CV text to Gemini and returns structured JSON data.
    Implements smart retry logic for 429 errors.
    
    Args:
        filename: Name of the source file
        raw_text: Extracted text from the CV
        api_key: Gemini API key
        max_retries: Maximum number of retries for 429 errors
    
    Returns:
        dict: Structured candidate data or None if parsing fails
    """
    if not raw_text or not raw_text.strip():
        logger.warning(f"Empty text for {filename}, skipping...")
        return None
    
    # Initialize the client
    if api_key:
        client = genai.Client(api_key=api_key)
    else:
        client = genai.Client()
    
    prompt = f"""
    You are an expert HR data extraction assistant. Analyze the following raw text from a CV/Resume.
    Extract the candidate's core details and return them strictly in JSON format.
    
    Important: For any field where information is not found, use null or empty strings/arrays as appropriate.
    DO NOT make up information that isn't present in the text.
    
    Expected JSON Structure:
    {{
        "full_name": "Candidate Full Name (derive from context if not explicitly stated)",
        "contact": {{
            "email": "Email address if found",
            "phone": "Phone number if found",
            "location": "City/Country if found",
            "linkedin": "LinkedIn URL if found",
            "portfolio_or_github": "Portfolio/GitHub URL if found"
        }},
        "current_title": "Current or most recent job title",
        "total_experience_years": 5.5,  // Number as float
        "seniority_level": "One of: Entry, Junior, Mid, Senior, Lead, Executive (default to Unknown)",
        "summary": "A brief 2-3 sentence summary of their professional background",
        "skills": {{
            "technical": ["List", "Of", "Technical", "Skills"],
            "tools_and_platforms": ["Tools", "Frameworks", "Platforms"],
            "soft": ["Soft", "Skills"]
        }},
        "work_experience": [
            {{
                "company": "Company Name",
                "role": "Job Title",
                "start_date": "Start Date (YYYY-MM or YYYY)",
                "end_date": "End Date (YYYY-MM or YYYY or Present)",
                "responsibilities": ["Key", "Responsibilities"]
            }}
        ],
        "education": [
            {{
                "institution": "University/College Name",
                "degree": "Degree Name",
                "field_of_study": "Field/Major",
                "graduation_year": "Year (YYYY)"
            }}
        ],
        "certifications": ["List", "Of", "Certifications"],
        "languages": ["Languages", "Spoken"],
        "red_flags_or_gaps": ["Employment gaps", "Missing info", "Inconsistencies"],
        "data_quality_notes": ["Notes about extraction quality or missing data"]
    }}

    Raw Text from CV:
    {raw_text}
    """
    
    retry_count = 0
    wait_time = 6  # Base wait time in seconds
    
    while retry_count <= max_retries:
        try:
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1
                )
            )
            
            # Parse and return the JSON
            parsed_data = json.loads(response.text)
            parsed_data["source_file"] = filename
            
            # Ensure contact field exists
            if "contact" not in parsed_data:
                parsed_data["contact"] = {}
            
            # Move top-level contact fields into contact object if they exist
            for field in ["email", "phone", "location", "linkedin", "portfolio_or_github"]:
                if field in parsed_data and field != "contact":
                    parsed_data["contact"][field] = parsed_data.pop(field)
            
            logger.info(f"✅ Successfully processed: {filename}")
            return parsed_data

        except Exception as e:
            error_str = str(e)
            
            # Check if it's a 429 rate limit error
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                retry_count += 1
                
                # Extract retry delay from error message if available
                retry_delay = None
                if "retryDelay" in error_str:
                    import re
                    match = re.search(r'retryDelay:\s*(\d+)s', error_str)
                    if match:
                        retry_delay = int(match.group(1))
                    else:
                        # Try alternative pattern
                        match = re.search(r'retry in (\d+\.?\d*)s', error_str)
                        if match:
                            retry_delay = float(match.group(1))
                
                # Use extracted delay or calculated backoff
                if retry_delay:
                    wait_time = max(retry_delay, 10)  # Minimum 10 seconds
                else:
                    # Exponential backoff: 60, 120, 240, 480, 960 seconds
                    wait_time = min(60 * (2 ** (retry_count - 1)), 960)
                
                if retry_count <= max_retries:
                    logger.warning(f"⚠️ Rate limit (429) for {filename}. Retry {retry_count}/{max_retries} in {wait_time:.0f}s...")
                    time.sleep(wait_time)
                    continue
                else:
                    logger.error(f"❌ Max retries exceeded for {filename} (429 error)")
                    return None
            else:
                # Other errors (not rate limit)
                logger.error(f"❌ Gemini API error for {filename}: {e}")
                return None
    
    return None

# ----------------------------------------------------------------------------
# Main Processing Function
# ----------------------------------------------------------------------------

def process_folder(input_folder, output_file, api_key=None, resume=False):
    """
    Process all CV files in a folder and create the JSON database incrementally.
    
    Args:
        input_folder: Path to folder containing CV files
        output_file: Path to output JSON file
        api_key: Gemini API key
        resume: Whether to resume from existing database
    """
    input_path = Path(input_folder)
    
    if not input_path.exists():
        logger.error(f"Input folder not found: {input_folder}")
        return
    
    # Initialize database
    db = CVDatabase(output_file, resume=resume)
    
    # Get all supported files
    supported_extensions = ['.pdf', '.docx', '.doc', '.jpg', '.jpeg', '.png', '.tiff', '.bmp', '.txt']
    files = []
    
    for ext in supported_extensions:
        files.extend(input_path.glob(f'*{ext}'))
    
    # Also get files with uppercase extensions
    for ext in supported_extensions:
        files.extend(input_path.glob(f'*{ext.upper()}'))
    
    # Remove duplicates and sort for consistent processing
    files = sorted(list(set(files)))
    
    if not files:
        logger.error(f"No supported files found in {input_folder}")
        logger.info(f"Supported formats: {', '.join(supported_extensions)}")
        return
    
    # Filter out already processed files if resuming
    if resume:
        original_count = len(files)
        files = [f for f in files if not db.is_processed(f.name)]
        logger.info(f"⏭️ Skipping {original_count - len(files)} already processed files")
    
    if not files:
        logger.info("✅ All files have been processed already!")
        db._save_to_disk()
        return
    
    db.total_files = len(files)
    logger.info(f"📂 Found {len(files)} new files to process")
    
    # Process files one by one
    successful_count = 0
    failed_count = 0
    rate_limit_pauses = 0
    
    for idx, file_path in enumerate(files, 1):
        logger.info(f"\n{'='*60}")
        logger.info(f"Processing {idx}/{len(files)}: {file_path.name}")
        logger.info(f"{'='*60}")
        
        # Extract text
        text = extract_text_from_file(file_path)
        
        if text and len(text.strip()) > 50:  # Require at least 50 characters
            logger.info(f"📝 Extracted {len(text)} characters")
            
            # Process with Gemini (with retry logic)
            logger.info(f"🤖 Sending to Gemini AI...")
            candidate = extract_cv_data_with_ai(file_path.name, text, api_key)
            
            if candidate:
                # Add to database (this automatically saves)
                if db.add_candidate(candidate):
                    successful_count += 1
                    stats = db.get_stats()
                    logger.info(f"✅ Candidate added! Total: {stats['total_candidates']}")
                else:
                    logger.warning(f"⚠️ File already in database, skipping")
            else:
                failed_count += 1
                logger.warning(f"❌ Failed to extract data for {file_path.name}")
        else:
            logger.warning(f"⚠️ Insufficient text extracted from {file_path.name}")
            failed_count += 1
        
        # Rate limiting: Only sleep if not rate limited (which already includes sleep)
        # The retry logic handles its own sleeping for 429 errors
        if idx < len(files):
            # Check if the last file was a rate limit case by checking if we just waited a lot
            # Simple approach: always wait 2 seconds between files to be safe
            logger.info(f"⏳ Waiting 2 seconds before next request...")
            time.sleep(2)
        
        # Show current stats
        stats = db.get_stats()
        logger.info(f"📊 Progress: {idx}/{len(files)} | Success: {successful_count} | Failed: {failed_count} | Total in DB: {stats['total_candidates']}")
    
    # Final save
    db._save_to_disk()
    
    # Final summary
    stats = db.get_stats()
    logger.info(f"\n{'='*60}")
    logger.info(f"✅ Processing Complete!")
    logger.info(f"{'='*60}")
    logger.info(f"📊 Final Statistics:")
    logger.info(f"   - Total files processed: {stats['total_files']}")
    logger.info(f"   - Successful extractions: {stats['successful_extractions']}")
    logger.info(f"   - Failed extractions: {failed_count}")
    logger.info(f"   - Total candidates in database: {stats['total_candidates']}")
    logger.info(f"   - Database saved to: {output_file}")
    logger.info(f"{'='*60}")

# ----------------------------------------------------------------------------
# Command Line Interface
# ----------------------------------------------------------------------------



#  this function for checking how far to your build_database.py

def get_next_file_to_process(files, checkpoint_file):
    """
    Get the next file to process based on checkpoint.
    """
    checkpoint_path = Path(checkpoint_file)
    if checkpoint_path.exists():
        with open(checkpoint_path, 'r') as f:
            last_processed = f.read().strip()
        # Find the index of the last processed file
        for idx, file_path in enumerate(files):
            if file_path.name == last_processed:
                return idx + 1  # Start from the next file
    return 0

def save_checkpoint(checkpoint_file, filename):
    """
    Save the last successfully processed file.
    """
    with open(checkpoint_file, 'w') as f:
        f.write(filename)



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process CVs and build candidate database incrementally")
    parser.add_argument("--input", "-i", default="cvs_input", 
                       help="Input folder containing CV files")
    parser.add_argument("--output", "-o", default="data/candidates.json",
                       help="Output JSON file path")
    parser.add_argument("--api-key", help="Gemini API key (or set GEMINI_API_KEY env var)")
    parser.add_argument("--resume", "-r", action="store_true",
                       help="Resume from existing database (skips already processed files)")
    
    args = parser.parse_args()
    
    # Get API key from arguments or environment
    api_key = args.api_key or os.getenv("GEMINI_API_KEY")
    
    if not api_key:
        logger.error("❌ No API key found. Please set GEMINI_API_KEY environment variable or use --api-key")
        logger.info("Get your API key from: https://aistudio.google.com/apikey")
        exit(1)
    
    process_folder(args.input, args.output, api_key, args.resume)