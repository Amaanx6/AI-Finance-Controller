# AI Finance Controller

A hackathon project that reconciles financial transactions across three synthetic data sources (bank statement, internal ledger, gateway export) using a two-tier approach: deterministic fast-path matching + LLM agent for exceptions.

## Project Structure

```
.
├── backend/          # FastAPI backend for reconciliation
├── frontend/         # Next.js + TypeScript frontend dashboard
├── README.md         # This file
└── .gitignore        # Git ignore rules
```

## Quick Start

### Backend Setup

```bash
cd backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env and add your Groq API key

# Run the server
uvicorn app.main:app --reload --port 8000
```

Backend API: http://localhost:8000

### Frontend Setup

```bash
cd frontend

# Install dependencies
npm install

# Run development server
npm run dev
```

Frontend: http://localhost:3000

## Architecture

### Two-Tier Reconciliation

1. **Fast Path**: Deterministic matching via exact ID match, then tolerance-based matching on amount/date
2. **LLM Agent Escalation**: Unresolved records escalate to Groq LLM with tool-calling
   - **Sum-check tool**: Verify if subset of candidates sums to target within tolerance
   - **Description similarity**: Semantic matching on transaction descriptions

### Tech Stack

- **Backend**: Python, FastAPI, Groq LLM API
- **Frontend**: Next.js, React, TypeScript
- **Data**: CSV files and JSON (no database for hackathon)

## Development

Refer to individual README files for detailed setup:
- [Backend README](./backend/README.md)
- [Frontend README](./frontend/README.md)

## Environment Variables

Create a `backend/.env` file (see `backend/.env.example`):

```
GROQ_API_KEY=your_groq_api_key
BACKEND_PORT=8000
FRONTEND_URL=http://localhost:3000
```

## Next Steps

1. Implement data generation in `backend/app/data_generation/generator.py`
2. Build fast matching logic in `backend/app/matcher/fast_matcher.py`
3. Implement LLM agent in `backend/app/agent/llm_agent.py`
4. Create API endpoints in `backend/app/api/routes.py`
5. Build UI components in `frontend/src/components/`
6. Add evaluation script in `backend/app/eval/`
