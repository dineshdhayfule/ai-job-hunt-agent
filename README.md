# AI Job Hunt Agent

An AI-powered job discovery and matching agent designed to help fresh graduates find relevant software development opportunities with less manual searching.

The agent collects job listings from supported sources, evaluates them against a candidate profile, applies freshness filters, and distinguishes potential matches from jobs with confirmed eligibility.

## Features

- **Multi-source job discovery** — extensible adapters for supported job sources.
- **Personalized matching** — evaluates skills, role relevance, and location compatibility.
- **Freshness filtering** — supports 24-hour, 72-hour, and 7-day search windows.
- **Fresher eligibility assessment** — separates confirmed eligibility, unknown eligibility, and ineligible listings.
- **Configurable match threshold** — defaults to a minimum match score of 75%.
- **Remote-location awareness** — recognizes worldwide remote listings while respecting explicit geographic restrictions.
- **Caching and persistence** — reduces unnecessary API requests and saves retrieved listings.
- **Automated testing** — unit tests cover matching, live search, and job-source behavior.

## Tech Stack

- Python
- Google Gemini API integration
- Requests
- python-dotenv
- JSON-based profile, memory, and job storage
- Python `unittest`

## How Matching Works

The matching system evaluates three main dimensions:

| Dimension | Weight |
|---|---:|
| Skills compatibility | 50 |
| Role relevance | 20 |
| Location compatibility | 15 |
| **Total** | **85** |

The normalized match score is calculated as:

`match_score = weighted_score / 85 × 100`

The default minimum match threshold is **75%**.

### Match classifications

- **Confirmed match:** Meets the score threshold and eligibility has been confirmed.
- **Potential match:** Meets the score threshold, but eligibility remains unknown.
- **Ineligible:** Available evidence indicates that the candidate does not meet the job's eligibility requirements.
- **Below threshold:** Does not meet the configured match score.

A high match score does not guarantee an interview, job offer, or even that a vacancy is still open. Always verify the employer's requirements and application page.

## Current Job Sources

| Source | Status |
|---|---|
| Remotive | Implemented |
| Greenhouse | Optional; requires configured board identifiers |
| Lever | Optional; requires configured company identifiers |

Source availability depends on configuration. Listings returned by a source are not automatically considered confirmed-open vacancies.

Remotive listing data may be delayed, and source coverage is not exhaustive.

## Project Structure

```text
ai-job-hunt-agent/
├── agent.py
├── test_gemini.py
├── .env
├── .gitignore
├── data/
│   ├── profile.json
│   ├── memory.json
│   ├── jobs.json
│   ├── live_jobs.json
│   └── fetch_status.json
├── tools/
│   ├── match_jobs.py
│   ├── memory.py
│   ├── search_jobs.py
│   ├── search_live_jobs.py
│   └── job_sources/
│       ├── base.py
│       ├── remotive.py
│       ├── greenhouse.py
│       ├── lever.py
│       └── registry.py
└── tests/
```

Runtime-generated data files may not exist until the agent runs.

## Getting Started

### 1. Clone the repository

```bash
git clone https://github.com/YOUR_USERNAME/ai-job-hunt-agent.git
cd ai-job-hunt-agent
```

Replace `YOUR_USERNAME` with your GitHub username.

### 2. Create a virtual environment

```bash
python -m venv .venv
```

Activate it on Windows:

```powershell
.venv\Scripts\Activate.ps1
```

Or on Ubuntu/WSL:

```bash
source .venv/bin/activate
```

### 3. Install dependencies

Install the project's required dependencies in the virtual environment. If a `requirements.txt` is included:

```bash
python -m pip install -r requirements.txt
```

### 4. Configure environment variables

Create a local `.env` file in the project root and add the credentials required by the enabled integrations.

Example:

```dotenv
GEMINI_API_KEY=your_api_key_here
```

Only add source-specific configuration when needed. Never commit `.env` or expose API keys in source code, screenshots, logs, or public repositories.

### 5. Configure your profile

Update `data/profile.json` with your target roles, experience level, preferred locations, and minimum match score.

The agent also supports stored preferences through `data/memory.json`.

### 6. Run the tests

```bash
python -m unittest discover -s tests -v
```

### 7. Run the agent

Use the entry point and commands documented by the current implementation. Live-search parameters and source configuration can be inspected in `tools/search_live_jobs.py` and `tools/job_sources/registry.py`.

## Configuration

The agent supports:

- Target roles and skills
- Experience level
- Preferred locations
- Minimum match score
- Freshness window
- Supported job sources
- Source caching

The default match threshold is 75%. Freshness filtering supports windows of 24, 72, and 168 hours (7 days).

## Verification Status

The current development milestone has passed a reported suite of **180 automated tests** and a controlled live-search verification using Remotive.

The live verification retrieved 10 source records and identified one potential match above the configured threshold. Eligibility and vacancy-open status were not confirmed for that listing.

These results demonstrate the tested functionality, not guaranteed discovery of suitable jobs on every run.

## Roadmap

- [x] Profile-based matching
- [x] Freshness filtering
- [x] Eligibility-aware match classification
- [x] Source caching and persistence
- [x] Controlled live-source verification
- [ ] Expand verified job-source coverage
- [ ] Build a web dashboard
- [ ] Add saved jobs and application tracking
- [ ] Improve source-level vacancy-status verification

## Security

- Keep API keys in `.env`.
- Keep `.env` and local virtual environments out of version control.
- Avoid committing personal information from candidate profiles.
- Treat external job descriptions and application links as untrusted input.

## Disclaimer

This project is a personal job-search assistance tool. Match scores are estimates based on available listing data and configured preferences. They do not guarantee eligibility, vacancy availability, or hiring outcomes. Verify all requirements directly with the employer before applying.

