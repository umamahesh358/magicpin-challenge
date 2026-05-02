"""
server.py — Vera Bot REST API Server
Implements: GET /v1/healthz, GET /v1/metadata
            POST /v1/context, POST /v1/tick, POST /v1/reply
"""

import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

from bot import compose, respond  # our existing logic

# ─────────────────────────────────────────────
# In-memory store
# ─────────────────────────────────────────────
START_TIME = time.time()

store = {
    "categories":    {},   # slug → {version, payload}
    "merchants":     {},   # merchant_id → {version, payload}
    "customers":     {},   # customer_id → {version, payload}
    "triggers":      {},   # trigger_id → {version, payload}
    "conversations": {},   # conv_id → {history, suppressed}
    "fired":         set(),# suppression_keys already used
}

app = FastAPI(title="Vera Bot API")

# ─────────────────────────────────────────────
# GET /v1/healthz
# ─────────────────────────────────────────────
@app.get("/v1/healthz")
def healthz():
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - START_TIME),
        "contexts_loaded": {
            "category": len(store["categories"]),
            "merchant":  len(store["merchants"]),
            "customer":  len(store["customers"]),
            "trigger":   len(store["triggers"]),
        }
    }

# ─────────────────────────────────────────────
# GET /v1/metadata
# ─────────────────────────────────────────────
@app.get("/v1/metadata")
def metadata():
    return {
        "team_name":    "Vera AI Challenge Bot",
        "team_members": ["Participant"],
        "model":        "llama-3.3-70b-versatile (Groq)",
        "approach":     "Chain-of-Thought Hinglish composer with trigger-kind dispatch, auto-reply guard, and intent fast-track",
        "contact_email": "participant@example.com",
        "version":      "1.0.0",
        "submitted_at": "2026-05-02T12:00:00Z",
    }

# ─────────────────────────────────────────────
# POST /v1/context
# ─────────────────────────────────────────────
@app.post("/v1/context")
async def push_context(request: Request):
    body = await request.json()

    scope      = body.get("scope")        # category | merchant | customer | trigger
    context_id = body.get("context_id")
    version    = body.get("version", 1)
    payload    = body.get("payload", {})

    bucket_map = {
        "category": "categories",
        "merchant": "merchants",
        "customer": "customers",
        "trigger":  "triggers",
    }

    bucket = bucket_map.get(scope)
    if not bucket:
        return JSONResponse({"accepted": False, "reason": "unknown_scope"}, status_code=400)

    existing = store[bucket].get(context_id)

    # Idempotency: reject same version re-push
    if existing and existing["version"] == version:
        return JSONResponse(
            {"accepted": False, "reason": "stale_version", "current_version": existing["version"]},
            status_code=409,
        )

    store[bucket][context_id] = {"version": version, "payload": payload}
    ack_id = f"ack_{context_id}_v{version}"
    stored_at = datetime.now(timezone.utc).isoformat()

    return {"accepted": True, "ack_id": ack_id, "stored_at": stored_at}

# ─────────────────────────────────────────────
# POST /v1/tick
# ─────────────────────────────────────────────
@app.post("/v1/tick")
async def tick(request: Request):
    body = await request.json()
    available_triggers = body.get("available_triggers", [])

    actions = []

    for trigger_id in available_triggers:
        t_entry = store["triggers"].get(trigger_id)
        if not t_entry:
            continue

        trigger = t_entry["payload"]
        suppression_key = trigger.get("suppression_key", "")

        # Skip if already suppressed
        if suppression_key and suppression_key in store["fired"]:
            continue

        merchant_id = trigger.get("merchant_id") or trigger.get("payload", {}).get("merchant_id")
        customer_id = trigger.get("customer_id") or trigger.get("payload", {}).get("customer_id")

        m_entry = store["merchants"].get(merchant_id)
        if not m_entry:
            continue

        merchant = m_entry["payload"]
        category_slug = merchant.get("category_slug", "")
        c_entry = store["categories"].get(category_slug)
        category = c_entry["payload"] if c_entry else {}

        customer = None
        if customer_id:
            cu_entry = store["customers"].get(customer_id)
            if cu_entry:
                customer = cu_entry["payload"]

        # Call our compose function
        try:
            result = compose(category, merchant, trigger, customer)
        except Exception as e:
            continue

        # Mark suppression key as used
        if suppression_key:
            store["fired"].add(suppression_key)

        # Build conversation ID
        conv_id = f"conv_{merchant_id}_{trigger_id}"

        # Store conversation for multi-turn
        store["conversations"][conv_id] = {
            "merchant_id": merchant_id,
            "customer_id": customer_id,
            "trigger_id":  trigger_id,
            "history":     [],
            "suppressed":  False,
        }

        actions.append({
            "conversation_id": conv_id,
            "merchant_id":     merchant_id,
            "customer_id":     customer_id,
            "send_as":         result.get("send_as", "vera"),
            "trigger_id":      trigger_id,
            "template_name":   f"vera_{trigger.get('kind', 'generic')}_v1",
            "template_params": [result.get("body", "")],
            "body":            result.get("body", ""),
            "cta":             result.get("cta", "open_ended"),
            "suppression_key": suppression_key,
            "rationale":       result.get("rationale", ""),
        })

    return {"actions": actions}

# ─────────────────────────────────────────────
# POST /v1/reply
# ─────────────────────────────────────────────
@app.post("/v1/reply")
async def reply(request: Request):
    body = await request.json()

    conv_id    = body.get("conversation_id", "")
    merchant_msg = body.get("message", "")
    turn_number  = body.get("turn_number", 1)

    conv = store["conversations"].get(conv_id, {})

    # Mark suppressed if needed
    if conv.get("suppressed"):
        return {"action": "end", "rationale": "Conversation already closed."}

    history = conv.get("history", [])

    # === AUTO-REPLY DETECTION ===
    auto_reply_phrases = [
        "thank you for contacting",
        "our team will respond",
        "automated assistant",
        "aapki jaankari ke liye shukriya",
    ]
    is_auto_reply = any(phrase in merchant_msg.lower() for phrase in auto_reply_phrases)

    if is_auto_reply:
        if len(history) >= 1 and history[-1].get("auto_reply"):
            # Second auto-reply in a row → wait 24h
            history.append({"msg": merchant_msg, "auto_reply": True})
            if len([h for h in history if h.get("auto_reply")]) >= 3:
                conv["suppressed"] = True
                return {
                    "action": "end",
                    "rationale": "Auto-reply 3x in a row — no real engagement. Closing conversation."
                }
            return {
                "action": "wait",
                "wait_seconds": 86400,
                "rationale": "Same auto-reply twice → owner not at phone. Waiting 24h before retry."
            }
        history.append({"msg": merchant_msg, "auto_reply": True})
        return {
            "action": "send",
            "body": "Lagta hai auto-reply hai 😊 Jab owner dekhe, bas 'YES' type karein to proceed.",
            "cta": "binary_yes_no",
            "rationale": "Detected auto-reply; one prompt to flag for owner."
        }

    # === HOSTILE DETECTION ===
    hostile_phrases = ["stop messaging", "not interested", "useless", "spam", "don't message"]
    is_hostile = any(phrase in merchant_msg.lower() for phrase in hostile_phrases)
    if is_hostile:
        conv["suppressed"] = True
        return {
            "action": "end",
            "rationale": "Merchant explicitly opted out. Suppressing for 30 days."
        }

    # === INTENT FAST-TRACK ===
    positive_intents = ["yes", "han", "haan", "karo", "do it", "let's do it", "ok let's", "confirm", "proceed", "start", "join", "go ahead"]
    if any(intent in merchant_msg.lower() for intent in positive_intents):
        history.append({"msg": merchant_msg, "auto_reply": False})
        return {
            "action": "send",
            "body": "Done! Main abhi process initiate kar rahi hoon. Ek minute dijiye — aapko confirmation milegi.",
            "cta": "binary_confirm_cancel",
            "rationale": "Merchant explicitly committed; switched from qualification to action-execution mode."
        }

    # === OUT-OF-SCOPE CURVEBALL ===
    out_of_scope = ["gst", "income tax", "loan", "job", "hiring", "legal", "court"]
    if any(word in merchant_msg.lower() for word in out_of_scope):
        history.append({"msg": merchant_msg, "auto_reply": False})
        return {
            "action": "send",
            "body": "Yeh cheez meri expertise se bahar hai. Aapke original topic par wapas aate hain — kya aap proceed karna chahte hain?",
            "cta": "open_ended",
            "rationale": "Out-of-scope request politely deflected; redirected to original trigger."
        }

    # === GENERIC CONTINUE (use LLM respond function) ===
    history.append({"msg": merchant_msg, "auto_reply": False})
    state = {"history": [h["msg"] for h in history]}
    result = respond(state, merchant_msg)

    return {
        "action": "send",
        "body": result.get("body", "Samajh gayi. Kya aap aage badhna chahte hain?"),
        "cta": "open_ended",
        "rationale": "Continuing conversation based on merchant response.",
    }


# ─────────────────────────────────────────────
# Run server
# ─────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
