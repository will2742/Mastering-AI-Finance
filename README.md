# Mastering AI for Finance — Checkpoint 1

This repository contains an SEC-backed equity-research desk for FIN 67110.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Create `.env` locally. Never commit it.

```text
API_KEY=paste-here
SEC_USER_AGENT="Your Name your-email@purdue.edu"
```

## Run the seven-step desk

```powershell
.\.venv\Scripts\python.exe .\desk.py CRSP --peers NTLA,EDIT,BEAM
```

The command:

1. Resolves the ticker to a CIK.
2. Selects the latest 10-K, 10-Q, and Item 2.02 earnings 8-K.
3. Downloads and line-numbers the filings and Exhibit 99.1.
4. Reads annual XBRL facts with their tags, periods, and accessions.
5. Computes ratios in Python.
6. Locates quarterly facts in the earnings release.
7. Repeats comparable facts for the selected peers.

Each run is saved under `runs/`. The CRSP report is a course-approved fallback report because a compatible text-model provider key was not available for the four paid model calls. The report still uses the four-role division of work and the required five-part template.

## Security

`.gitignore` excludes `.env` and `.venv`. The public repository contains no API key.
