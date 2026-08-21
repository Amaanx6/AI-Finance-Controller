# AI Finance Controller - Backend

FastAPI backend for the AI Finance Controller hackathon project.

## Setup

1. Create a Python virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Configure environment variables:
   ```bash
   cp .env.example .env
   # Edit .env and add your Groq API key
   ```

4. Run the server:
   ```bash
   uvicorn app.main:app --reload --port 8000
   ```

The API will be available at `http://localhost:8000`

## Project Structure

- `app/data_generation/` - Synthetic data generation for bank statements, ledger, and gateway exports
- `app/matcher/` - Reconciliation matching logic (fast path + LLM escalation)
- `app/agent/` - LLM agent with tool-calling for ambiguous matches
- `app/api/` - REST API endpoints
- `app/eval/` - Evaluation and scoring utilities
