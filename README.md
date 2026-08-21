# AI Finance Controller

A sophisticated reconciliation engine that closes the finance-ops loop across a batch of synthetic financial data (bank statements, internal ledgers, and gateway exports).

This project goes beyond basic script-matching by employing a **two-tier architecture**: a deterministic fast-path for clean records, and a dual-agent **Proposer-Verifier LLM pipeline** to intelligently resolve ambiguous exceptions, many-to-one settlements, and heavily decoyed transaction descriptions.

## 🧠 Why This Isn't Just a Matching Script (AI Judgment)

Early on, we realized a pure deterministic script (e.g., pandas + fuzzy matching) would fail the "AI Judgment" criteria. Real-world reconciliation features edge cases that rule-based systems simply cannot safely resolve:

* **Many-to-one settlements:** One bank entry equals the sum of several ledger entries minus a payment gateway fee. No 1:1 match exists.
* **Description mismatches & Decoys:** Same transaction, wildly different text ("RZRPY SETL 08/19" vs "Razorpay settlement batch #4471"). We introduced deliberate decoy records (matching amount/date but completely wrong entity) to prove that only semantic reasoning—not just string matching—can resolve genuine ambiguity.

## 🏗️ Architecture: Two-Tier Reconciliation

<p align="center">
  <img
    src="https://github.com/user-attachments/assets/12444bae-a734-40f4-9855-cfaa7625bb15"
    alt="Agentic Reconciliation Architecture"
    width="750"
  />
</p>

Our pipeline ensures maximum throughput by letting code handle the easy majority, reserving expensive LLM cycles exclusively for genuine edge cases.

1. **Data Normalization:** Standardizes dates, amounts, and IDs across all 3 raw sources.
2. **Deterministic Fast Path:**
* Exact reference-number match $\rightarrow$ *Auto-confirmed*
* Tolerance match (amount ±1%, date ±3 days) with exactly 1 candidate $\rightarrow$ *Confirmed*
* Zero or Multiple candidates $\rightarrow$ *Routed to AI*


3. **LLM Reasoning Agents (The Exception Path):**
* Unresolved records are passed to the AI with specific tool-calling capabilities (`sum_check` for batched settlements, `description_similarity` for semantic search).



### The Differentiator: Proposer-Verifier Agent Architecture

A single agent proposing matches is dangerous—it will confidently hallucinate a connection to a decoy record. To solve the industry bottleneck of *verification capacity*, we built a dual-agent system:

* **Proposer Agent:** Analyzes the unresolved record and candidates, uses tools, and suggests a match with a reasoning trace.
* **Verifier Agent:** Independently reviews the proposal with a strict system prompt instructing it to actively *argue against* the match and hunt for flaws.
* **Agree:** Match confirmed.
* **Disagree:** Forces a retry with more evidence.
* **Still Disagrees:** Escalates to an honest, human-readable **Exception Log**.

<p align="center">
  <img
    src="https://github.com/user-attachments/assets/8377f3a0-cb0a-4102-9098-49dfcd59488b"
    alt="Proposer Verifier Agent Loop"
    width="800"
  />
</p>



## 📊 Synthetic Data Design

We built a 69-record batch of deliberately messy transactions across a 3-week window, evaluated against an isolated `mapping.csv` ground truth that the AI never sees.

| Pattern | % of Data | Description |
| --- | --- | --- |
| **Clean 1:1 Matches** | ~40% | Same transaction, all 3 sources, exact ID matches. |
| **Many-to-one** | ~20% | One bank entry = sum of 2-4 ledger entries minus a fee. |
| **Mismatches & Decoys** | ~15% | Wildly different text + deliberate decoy records to fool fuzzy matchers. |
| **Near-miss Noise** | ~15% | Small rounding/fee variance or date drift. |
| **Genuine Anomalies** | ~10% | Appear in only one source, correctly flagged as unresolved. |

## 💻 Tech Stack

* **Backend:** Python, FastAPI
* **AI / LLM:** Groq API (Primary - high speed, tool-calling support), Google Gemini API (Fallback)
* **Frontend:** Next.js, React, TypeScript (App Router)
* **Data Layer:** In-memory CSVs and JSON files (purpose-built for hackathon portability)

## 🚀 Quick Start

### Backend Setup

```bash
cd backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment variables
cp .env.example .env
# Edit .env and add your GROQ_API_KEY

# Run the server
uvicorn app.main:app --reload --port 8000

```

Backend API will be live at: `http://localhost:8000`

### Frontend Setup

```bash
cd frontend

# Install dependencies
npm install

# Run development server
npm run dev

```

Frontend UI will be live at: `http://localhost:3000`

## 📁 Project Structure

```text
RazorPay/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── data_generation/
│   │   │   ├── generator.py
│   │   │   ├── samples/          # (3 messy CSVs)
│   │   │   └── ground_truth/     # (isolated answer key)
│   │   ├── matcher/
│   │   │   ├── fast_matcher.py   # (exact + tolerance matching)
│   │   │   └── reconciler.py     # (orchestrates fast path -> AI)
│   │   ├── agent/
│   │   │   ├── llm_agent.py      # (Proposer-Verifier loop)
│   │   │   ├── tools.py          # (sum-check & similarity)
│   │   │   └── prompts.py        # (system instructions)
│   │   ├── api/
│   │   │   ├── routes.py
│   │   │   └── schemas.py
│   │   └── eval/                 # (scoring against ground truth)
│   ├── requirements.txt
│   └── .env
├── frontend/
│   ├── src/
│   │   ├── app/ 
│   │   ├── components/           # (Results, Traces, Exception Lists)
│   │   ├── types/
│   │   └── lib/api.ts
│   └── package.json
└── README.md

```
