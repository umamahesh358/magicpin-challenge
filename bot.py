import json
import os
import hashlib
from groq import Groq
from dotenv import load_dotenv

load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# ─────────────────────────────────────────────
# Simple in-memory cache (prevents duplicate API calls)
# ─────────────────────────────────────────────
_cache = {}

def _cache_key(merchant_id: str, trigger_id: str) -> str:
    return hashlib.md5(f"{merchant_id}:{trigger_id}".encode()).hexdigest()

# ─────────────────────────────────────────────
# Context trimmer — extracts only key fields
# Reduces token usage by ~70% vs full JSON dump
# ─────────────────────────────────────────────
def _trim_context(category: dict, merchant: dict, trigger: dict, customer: dict | None) -> dict:
    identity = merchant.get("identity", {})
    perf     = merchant.get("performance", {})
    sub      = merchant.get("subscription", {})
    cust_agg = merchant.get("customer_aggregate", {})
    signals  = merchant.get("signals", [])
    offers   = [o.get("title") for o in merchant.get("offers", []) if o.get("status") == "active"]

    cat_voice = category.get("voice", {})
    peer_stats = category.get("peer_stats", {})
    digest_top = (category.get("digest") or [{}])[:2]  # Only top 2 digest items
    seasonal   = (category.get("seasonal_beats") or [{}])[:1]

    trigger_payload = trigger.get("payload", {})

    ctx = {
        "category": {
            "slug":       category.get("slug"),
            "voice_tone": cat_voice.get("tone"),
            "taboos":     cat_voice.get("vocab_taboo", [])[:3],
            "peer_avg_ctr":    peer_stats.get("avg_ctr"),
            "peer_avg_rating": peer_stats.get("avg_rating"),
            "digest_top":      digest_top,
            "seasonal_now":    seasonal,
        },
        "merchant": {
            "name":          identity.get("name"),
            "owner":         identity.get("owner_first_name"),
            "city":          identity.get("city"),
            "locality":      identity.get("locality"),
            "languages":     identity.get("languages", []),
            "verified":      identity.get("verified"),
            "plan":          sub.get("plan"),
            "days_remaining":sub.get("days_remaining"),
            "sub_status":    sub.get("status"),
            "views_30d":     perf.get("views"),
            "calls_30d":     perf.get("calls"),
            "ctr":           perf.get("ctr"),
            "delta_views":   perf.get("delta_7d", {}).get("views_pct"),
            "delta_calls":   perf.get("delta_7d", {}).get("calls_pct"),
            "active_offers": offers,
            "signals":       signals[:4],
            "total_customers_ytd": cust_agg.get("total_unique_ytd"),
            "lapsed_customers":    cust_agg.get("lapsed_180d_plus"),
            "retention_pct":       cust_agg.get("retention_6mo_pct"),
        },
        "trigger": {
            "kind":            trigger.get("kind"),
            "source":          trigger.get("source"),
            "scope":           trigger.get("scope"),
            "urgency":         trigger.get("urgency"),
            "suppression_key": trigger.get("suppression_key"),
            "payload":         trigger_payload,
        },
        "customer": None,
    }

    if customer:
        cust_id  = customer.get("identity", {})
        rel      = customer.get("relationship", {})
        ctx["customer"] = {
            "name":        cust_id.get("name"),
            "language":    cust_id.get("language_pref"),
            "state":       customer.get("state"),
            "last_visit":  rel.get("last_visit"),
            "visits":      rel.get("visits_total"),
            "services":    rel.get("services_received", [])[:3],
            "pref_time":   customer.get("preferences", {}).get("preferred_time"),
        }

    return ctx

# ─────────────────────────────────────────────
# Trigger-kind specific prompts
# Each trigger type gets a focused angle
# ─────────────────────────────────────────────
TRIGGER_ANGLES = {
    "perf_dip":          "Loss Aversion — merchant is losing ground vs peers. Lead with the gap number. Ask if they want you to fix it.",
    "perf_spike":        "Curiosity + Reciprocity — something worked this week. Lead with the spike number. Offer to double it.",
    "competitor_opened": "Loss Aversion + Urgency — a new competitor just opened nearby. Lead with the threat. Offer a specific countermove.",
    "research_digest":   "Reciprocity + Curiosity — new clinical/industry finding relevant to their patient/customer segment. Cite source. Offer to draft a patient message.",
    "recall_due":        "Effort Externalization — customer is due for a return visit. Lead with name + time since last visit. Offer 2 specific time slots.",
    "milestone_reached": "Social Proof + Curiosity — they hit a milestone. Celebrate it briefly. Then tell them what top peers do next.",
    "dormant_with_vera": "Curiosity — they've been quiet. Open with 'I noticed something in your account.' Make them curious.",
    "festival_upcoming": "Loss Aversion + Urgency — festival in X days. Competitors are prepping. Lead with the festival name and days remaining.",
    "review_theme_emerged": "Social Proof — 3+ reviews mention the same theme. Surface it. Offer a specific response or action.",
    "curious_ask_due":   "Asking the Merchant — open a curiosity question about their business this week. No CTA needed.",
    "customer_lapsed_soft": "Effort Externalization — a valuable customer hasn't returned. Pre-select 2 slots and draft the message for the merchant.",
    "chronic_refill_due": "Effort Externalization — customer's prescription refill is due. Message on behalf of merchant with exact medication details.",
    "appointment_tomorrow": "Effort Externalization — appointment tomorrow. Send a friendly reminder as merchant with confirmation CTA.",
    "renewal_due":       "Loss Aversion — subscription expiring soon. Show ROI (views, calls this period). Ask to renew.",
    "trial_followup":    "Curiosity + Social Proof — trial is active. Show what's happening. Ask one specific question.",
}

SYSTEM_PROMPT = """You are Vera, magicpin's merchant-AI WhatsApp assistant for Indian merchants.

RULES (zero tolerance):
1. ANCHOR: First sentence MUST contain a specific number from the context (e.g., CTR %, views, days, price, count).
2. HINGLISH MANDATORY: Mix Hindi + English naturally in every message. Example: "Aapka CTR 2.1% hai, jo peer average 3.0% se 30% kam hai."
3. VOICE: Match category tone — dentist=clinical-peer, salon=warm-personal, restaurant=operator-buddy, gym=coaching, pharmacy=professional.
4. ONE CTA: End with exactly one call-to-action. Use binary (YES/STOP or Reply 1/2) for action triggers. Open-ended for info.
5. NO hallucinations: Only use numbers and facts from the context. Never invent stats.
6. NO URLS ever in the body — automatic penalty.
7. RATIONALE: A single plain string (not JSON) naming: the compulsion lever, the anchor fact used, and trigger fit reason.

Return ONLY valid JSON with exactly these string keys:
{"body": "...", "cta": "binary_yes_no OR open_ended OR multi_choice_slot OR none", "send_as": "vera (if messaging the merchant) OR merchant_on_behalf (if messaging a customer)", "suppression_key": "...", "rationale": "single plain string here"}"""


def compose(category: dict, merchant: dict, trigger: dict, customer: dict | None = None) -> dict:
    merchant_id = merchant.get("merchant_id", "")
    trigger_id  = trigger.get("id", trigger.get("suppression_key", ""))

    # Check cache first
    key = _cache_key(merchant_id, trigger_id)
    if key in _cache:
        return _cache[key]

    # Trim context to reduce tokens
    ctx = _trim_context(category, merchant, trigger, customer)

    # Get trigger-specific angle
    trigger_kind  = trigger.get("kind", "generic")
    trigger_angle = TRIGGER_ANGLES.get(trigger_kind, "Engage the merchant with relevant, specific information.")
    target        = "customer" if customer else "merchant"

    # Build social proof hook from peer stats
    peer_ctr      = category.get("peer_stats", {}).get("avg_ctr", 0)
    merchant_ctr  = merchant.get("performance", {}).get("ctr", 0)
    locality      = merchant.get("identity", {}).get("locality", "aapke area")
    social_proof  = ""
    if peer_ctr and merchant_ctr and merchant_ctr < peer_ctr:
        gap = round(((peer_ctr - merchant_ctr) / peer_ctr) * 100)
        social_proof = f"Peer insight: {locality} ke similar businesses ka avg CTR {peer_ctr*100:.1f}% hai — {gap}% above yours."

    user_prompt = f"""Target: {target}
Trigger kind: {trigger_kind}
Strategy: {trigger_angle}
{f'Social proof hook (use if relevant): {social_proof}' if social_proof else ''}

Context (trimmed):
{json.dumps(ctx, indent=2, ensure_ascii=False)}

Write the WhatsApp message now."""

    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",   # Fast + low token usage
            temperature=0,
            max_tokens=400,                  # Cap output tokens
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt},
            ],
        )

        output_text = response.choices[0].message.content.strip()

        # Strip markdown code fences if present
        if output_text.startswith("```"):
            output_text = output_text.split("```")[1]
            if output_text.startswith("json"):
                output_text = output_text[4:]
            output_text = output_text.strip()

        result = json.loads(output_text)

        # Ensure suppression key is always set
        if not result.get("suppression_key"):
            result["suppression_key"] = trigger.get("suppression_key", "")

        # Cache and return
        _cache[key] = result
        return result

    except Exception as e:
        fallback = {
            "body": f"Namaste! Aapke account mein kuch interesting activity hai. Kya main details share karoon?",
            "cta": "binary_yes_no",
            "send_as": "vera",
            "suppression_key": trigger.get("suppression_key", "fallback"),
            "rationale": f"Soft curiosity fallback due to: {str(e)[:80]}",
        }
        return fallback


# ─────────────────────────────────────────────
# Multi-turn reply handler
# ─────────────────────────────────────────────
AUTO_REPLY_SIGNATURES = [
    "thank you for contacting", "our team will respond", "automated assistant",
    "main ek automated", "aapki jaankari ke liye shukriya", "we will get back to you",
    "yeh ek swachalit sandesh hai", "this is an automated message", "i am an automated",
    "hamari team aapse jald"
]

HOSTILE_SIGNALS = [
    "not interested", "useless", "spam", "don't message",
    "mat karo", "band karo", "nahi chahiye", "bothering", "annoying"
]

def handle_reply(history: list, merchant_message: str) -> dict:
    msg_lower = merchant_message.lower()

    # 1. Hostile / STOP detection (Priority 1)
    words = set(msg_lower.replace(",", " ").replace(".", " ").split())
    if words.intersection({"stop", "no", "nahi", "quit", "cancel"}) or any(sig in msg_lower for sig in HOSTILE_SIGNALS):
        return {
            "action": "end",
            "rationale": "Merchant explicitly requested to stop or opted out. Terminating conversation."
        }

    # 2. Auto-reply detection
    if any(sig in msg_lower for sig in AUTO_REPLY_SIGNATURES):
        auto_count = sum(1 for h in history if h.get("auto_reply"))
        if auto_count >= 1: # Second auto-reply -> terminate immediately (prevent loop)
            return {
                "action": "end",
                "rationale": "Consecutive auto-replies detected. Terminating to prevent loop."
            }
        return {
            "action": "send",
            "body": "Lagta hai auto-reply hai 😊 Jab aap khud dekhen, bas YES type karein.",
            "cta": "binary_yes_no",
            "rationale": "First auto-reply detected. Sent human prompt."
        }

    # 3. Dynamic LLM response for slots/custom replies
    try:
        prompt = f"""You are Vera, a WhatsApp AI assistant for Indian merchants.
The user just replied: "{merchant_message}"

Rules:
1. Respond in natural Hinglish.
2. If they mention a specific slot/date/action (e.g. "book me for Wed 5 Nov"), specifically acknowledge that detail in your reply (e.g. "Wed 5 Nov, 6pm confirmed").
3. If it's a generic 'yes', say you are initiating the process.
4. Keep it very short (1-2 sentences).
5. Output ONLY JSON with these exact keys: {{"action": "send", "body": "...", "cta": "open_ended", "rationale": "..."}}
"""
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            temperature=0.2,
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        
        out = response.choices[0].message.content.strip()
        if out.startswith("```"):
            out = out.split("```")[1]
            if out.startswith("json"): out = out[4:]
            out = out.strip()
            
        result = json.loads(out)
        result["action"] = "send" # Ensure it always sends
        return result
    except Exception as e:
        return {
            "action": "send",
            "body": "Done! Main aage ka process karti hoon.",
            "cta": "open_ended",
            "rationale": "Fallback reply due to LLM error."
        }
