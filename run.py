# run.py
from cv_processor import process_cv_batch

summary = process_cv_batch(
    input_dir="cvs_input",       # your folder of CVs/certs
    output_dir="cvs_structured", # where JSON results go
    model="llama3.2",
)
print(summary)