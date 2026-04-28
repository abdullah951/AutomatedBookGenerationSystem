# Automated Book Generation System (MVP)

This repository contains a minimal Python-based automation to ingest book requests from a CSV/Excel file, store them in Supabase, generate a 3-chapter outline using OpenAI, and save results back to Supabase.

Quick start (after filling `.env`):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# generate a sample Excel input (optional)
python scripts\generate_sample_xlsx.py
# run the pipeline (reads sample_requests.xlsx)
python -m src.main
```

Files of interest:
- `src/ingest_excel.py` — CSV ingestion
- `src/generator.py` — outline generator (OpenAI)
- `src/supabase_client.py` — Supabase helpers
- `sample_requests.csv` — example input

