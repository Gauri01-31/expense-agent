# Expense & Bill Agent

An agentic assistant that reads bills/receipts, records expenses, and acts on them:
duplicate detection, budgets, due-date alerts, reminders, and natural-language queries.

## Run
```bash
pip install -r requirements.txt
export GEMINI_API_KEY=your_key      # PowerShell: $env:GEMINI_API_KEY="your_key"
streamlit run app.py
```
Optional: `GEMINI_MODEL` to change the model, `EXPENSE_DB` for the SQLite path.

## Architecture
```
Streamlit UI (app.py) ── Chat tab ──► agent.py: Gemini tool-calling loop ──► db.py (SQLite)
                     └─ Upload tab ─► extractor.py: vision extraction ─► human review ─► db.py
```
- **agent.py**: 8 tools (`add_expense`, `query_expenses`, `summarize_spending`, `set_budget`, `budget_status`, `upcoming_dues`, `mark_paid`, `add_reminder`). The model decides which to call and chains them.
- **Guardrails**: duplicate check inside `add_expense`; extraction confidence shown and human confirmation required before saving a scanned bill.
- **Proactive**: `db.alerts()` surfaces overdue bills, budgets at 80%+, and due reminders in the sidebar.

## Evaluation (for your report)
1. Collect 30-50 receipts, hand-label vendor/amount/date.
2. Run `extractor.extract` on each and report field-level accuracy and confidence calibration.
3. Write 20 chat prompts with expected tool calls; measure tool-selection accuracy.

## Roadmap
Gmail bill ingestion, UPI deep links to pay bills, recurring-bill detection, multi-user auth, export to CSV, LangGraph version with explicit states.
