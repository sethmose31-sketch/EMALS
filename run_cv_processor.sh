#!/bin/bash
# run_cv_processor.sh

echo "========================================="
echo "CV Processing with Gemini AI"
echo "========================================="

# Check if Python 3 is installed
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed. Please install it first."
    exit 1
fi

# Check if API key is set
if [ -z "$GEMINI_API_KEY" ]; then
    echo "⚠️  GEMINI_API_KEY environment variable is not set."
    echo "Please set it with: export GEMINI_API_KEY='your_api_key_here'"
    echo "Get your API key from: https://aistudio.google.com/apikey"
    exit 1
fi

# Create directories if they don't exist
mkdir -p cvs_input
mkdir -p data

# Process the CVs
echo "📂 Processing CVs from 'cvs_input' folder..."
python3 build_database.py --input cvs_input --output data/candidates.json "$@"

echo "✅ Done! View results with: streamlit run app.py"