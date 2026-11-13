"""Turn a receipt/bill image or PDF into structured fields with a vision-capable model."""
import json
import re

from google.genai import types

import agent

SYSTEM = """You extract data from receipts and bills. Reply with ONLY a JSON object, no markdown:
{"vendor": str, "amount": number (final payable total), "currency": str, "expense_date": "YYYY-MM-DD" (bill/receipt date),
 "due_date": "YYYY-MM-DD" or null, "category": one of [Food, Utilities, Rent, Transport, Shopping, Health, Entertainment, Education, Other],
 "is_bill": true if it is an invoice/bill with a due date, else false, "notes": short string,
 "confidence": number 0-1 for how sure you are about vendor, amount and date}
Use null for anything not visible. Never invent values."""


def extract(data: bytes, media_type: str) -> dict:
    resp = agent.generate(
        contents=[types.Part.from_bytes(data=data, mime_type=media_type), "Extract the fields."],
        config=types.GenerateContentConfig(system_instruction=SYSTEM,
                                           response_mime_type="application/json"))
    text = (resp.text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    return json.loads(text)