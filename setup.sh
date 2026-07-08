#!/bin/bash
# setup.sh - Setup environment for CV processing

echo "========================================="
echo "CV Processing with Gemini AI - Setup"
echo "========================================="

# Check if API key is set
if [ -z "$GEMINI_API_KEY" ]; then
    echo "⚠️  GEMINI_API_KEY is not set."
    echo ""
    echo "Please enter your Gemini API key (get it from https://aistudio.google.com/):"
    read -s API_KEY
    export GEMINI_API_KEY="$API_KEY"
    echo "✅ API key set for this session"
    echo ""
    echo "To make it permanent, add this to your ~/.bashrc:"
    echo "export GEMINI_API_KEY=\"$API_KEY\""
else
    echo "✅ GEMINI_API_KEY is already set"
fi

# Create necessary directories
mkdir -p cvs_input data

echo ""
echo "📂 Directory structure ready:"
echo "   - cvs_input/  (place your CV files here)"
echo "   - data/       (output will be saved here)"
echo ""
echo "Next steps:"
echo "1. Copy your CV files to cvs_input/ folder"
echo "2. Run: python3 build_database.py --input cvs_input --output data/candidates.json"
echo "========================================="