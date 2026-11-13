"""Tool-using agent loop on top of the Gemini API (google-genai SDK, free tier)."""
import json
import os
import time
from datetime import date

from google import genai
from google.genai import types

import db

# Override with: $env:GEMINI_MODEL="gemini-3.5-flash"  (any model your key can use)
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
_client = None


def client():
    global _client
    if _client is None:
        _client = genai.Client()  # reads GEMINI_API_KEY (or GOOGLE_API_KEY)
    return _client


FALLBACKS = [m.strip() for m in os.getenv("GEMINI_FALLBACK_MODELS", "gemini-3.5-flash").split(",") if m.strip()]


def generate(contents, config):
    """generate_content with retries on overload (503/429) and fallback to other models."""
    last = None
    for m in [MODEL] + FALLBACKS:
        for attempt in range(3):
            try:
                return client().models.generate_content(model=m, contents=contents, config=config)
            except Exception as e:
                last = e
                msg = str(e)
                if "404" in msg or "NOT_FOUND" in msg:
                    break  # model name not available to this key: try the next model
                if any(k in msg for k in ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED")):
                    time.sleep(2 ** attempt)  # 1s, 2s, 4s
                    continue
                raise
    raise last


def system_prompt():
    return f"""You are a personal expense and bill assistant. Today is {date.today().isoformat()}. Currency is INR (₹) unless told otherwise.

Rules:
- Use the tools for every fact about the user's spending; never guess numbers.
- Before saving, infer a sensible category. If amount or vendor is genuinely unclear, ask one short question.
- If add_expense reports a possible duplicate, tell the user and ask before forcing the save.
- Resolve relative dates ("last month", "this week") into explicit YYYY-MM-DD ranges.
- After answering a spending question, add one short proactive insight only if it is useful (budget risk, unusual spike, bill due soon).
- Be concise."""


S = {"type": "string"}
N = {"type": "number"}
I = {"type": "integer"}
B = {"type": "boolean"}


def obj(props, required=None):
    d = {"type": "object", "properties": props}
    if required:
        d["required"] = required
    return d


TOOL_SPECS = [
    ("add_expense",
     "Record an expense or bill. Checks for duplicates first and refuses to save a likely duplicate unless force=true.",
     obj({"vendor": S, "amount": N,
          "category": {"type": "string", "description": "Food, Utilities, Rent, Transport, Shopping, Health, Entertainment, Education, Other"},
          "expense_date": {"type": "string", "description": "YYYY-MM-DD"},
          "is_bill": B, "due_date": {"type": "string", "description": "YYYY-MM-DD"},
          "status": {"type": "string", "enum": ["paid", "unpaid"]},
          "notes": S, "force": B},
         ["vendor", "amount", "category", "expense_date"])),
    ("query_expenses", "List expenses with optional filters (dates are YYYY-MM-DD).",
     obj({"start": S, "end": S, "category": S, "vendor": S, "limit": I})),
    ("summarize_spending", "Total spending grouped by category, vendor or month for a date range.",
     obj({"start": S, "end": S, "group_by": {"type": "string", "enum": ["category", "vendor", "month"]}})),
    ("set_budget", "Set or update a monthly budget limit for a category.",
     obj({"category": S, "monthly_limit": N}, ["category", "monthly_limit"])),
    ("budget_status", "Spent vs. limit for each budget in a month (YYYY-MM, default current).",
     obj({"month": S})),
    ("upcoming_dues", "Unpaid bills due within N days (default 14), including overdue ones.",
     obj({"days": I})),
    ("mark_paid", "Mark a bill as paid.", obj({"expense_id": I}, ["expense_id"])),
    ("add_reminder", "Schedule a reminder for a date.",
     obj({"remind_on": {"type": "string", "description": "YYYY-MM-DD"}, "message": S, "expense_id": I},
         ["remind_on", "message"])),
]

DISPATCH = {
    "add_expense": db.add_expense,
    "query_expenses": db.query_expenses,
    "summarize_spending": db.summarize_spending,
    "set_budget": db.set_budget,
    "budget_status": db.budget_status,
    "upcoming_dues": db.upcoming_dues,
    "mark_paid": db.mark_paid,
    "add_reminder": db.add_reminder,
}


def _config():
    decls = [types.FunctionDeclaration(name=n, description=d, parameters=p) for n, d, p in TOOL_SPECS]
    return types.GenerateContentConfig(
        system_instruction=system_prompt(),
        tools=[types.Tool(function_declarations=decls)],
        # we run the tool loop ourselves so the UI can show each action
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )


def user_message(text):
    return types.Content(role="user", parts=[types.Part(text=text)])


def _clean(v):
    """Convert SDK containers (MapComposite etc.) to plain Python."""
    return json.loads(json.dumps(v, default=lambda o: dict(o) if hasattr(o, "items") else str(o)))


def call_tool(name, args):
    try:
        return DISPATCH[name](**args)
    except Exception as e:  # hand errors back to the model so it can recover
        return {"error": f"{type(e).__name__}: {e}"}


def run(contents, max_steps=8):
    """Mutates `contents` (list of types.Content). Returns (reply_text, tool_trace)."""
    trace = []
    for _ in range(max_steps):
        resp = generate(contents, _config())
        if not resp.candidates or not resp.candidates[0].content:
            return "The model returned no answer. Please try rephrasing.", trace
        content = resp.candidates[0].content
        contents.append(content)  # keep as-is (Gemini 3 needs its thought signatures echoed back)
        parts = content.parts or []
        calls = [p.function_call for p in parts if p.function_call]
        if not calls:
            return "".join(p.text for p in parts if p.text), trace
        replies = []
        for fc in calls:
            args = _clean(dict(fc.args or {}))
            out = _clean(call_tool(fc.name, args))
            trace.append({"tool": fc.name, "input": args, "output": out})
            replies.append(types.Part.from_function_response(name=fc.name, response={"result": out}))
        contents.append(types.Content(role="user", parts=replies))
    return "I stopped after too many steps; please rephrase or narrow the request.", trace