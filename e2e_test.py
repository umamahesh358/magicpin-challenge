"""
Full end-to-end test against the live Render deployment.
Simulates exactly what the judge does.
"""
import json
import sys
import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE = "https://magicpin-challenge-umamahesh.onrender.com"
HEADERS = {"Content-Type": "application/json"}

def test(label, method, path, body=None):
    url = f"{BASE}{path}"
    r = requests.request(method, url, json=body, headers=HEADERS, timeout=30)
    print(f"\n{'='*55}")
    print(f"  {label}")
    print(f"  {method} {path} -> {r.status_code}")
    print(f"  {json.dumps(r.json(), ensure_ascii=False, indent=2)[:400]}")
    return r.json()

# --- Phase 1: Warmup ---
test("HEALTHZ (before context)", "GET", "/v1/healthz")
test("METADATA", "GET", "/v1/metadata")

# Push category
test("PUSH CATEGORY: dentists", "POST", "/v1/context", {
    "scope": "category", "context_id": "dentists", "version": 1,
    "payload": {
        "slug": "dentists",
        "voice": {"tone": "peer_clinical", "vocab_taboo": ["guaranteed", "100% safe"]},
        "peer_stats": {"avg_ctr": 0.030, "avg_rating": 4.4},
        "digest": [{"id": "d_001", "kind": "research",
                    "title": "3-month fluoride recall cuts caries 38%",
                    "source": "JIDA Oct 2026, p.14"}]
    }
})

# Push merchant
test("PUSH MERCHANT: Dr. Meera", "POST", "/v1/context", {
    "scope": "merchant", "context_id": "m_001_drmeera", "version": 1,
    "payload": {
        "merchant_id": "m_001_drmeera",
        "category_slug": "dentists",
        "identity": {
            "name": "Dr. Meera's Dental Clinic",
            "owner_first_name": "Meera",
            "city": "Delhi", "locality": "Lajpat Nagar",
            "verified": True, "languages": ["en", "hi"]
        },
        "subscription": {"status": "active", "plan": "Pro", "days_remaining": 82},
        "performance": {"views": 2410, "calls": 18, "ctr": 0.021,
                        "delta_7d": {"views_pct": 0.18, "calls_pct": -0.05}},
        "offers": [{"id": "o_001", "title": "Dental Cleaning @ Rs.299", "status": "active"}],
        "customer_aggregate": {"total_unique_ytd": 540, "lapsed_180d_plus": 78,
                               "high_risk_adult_count": 124},
        "signals": ["stale_posts:22d", "ctr_below_peer_median"]
    }
})

# Push trigger
test("PUSH TRIGGER: research_digest", "POST", "/v1/context", {
    "scope": "trigger", "context_id": "trg_001", "version": 1,
    "payload": {
        "id": "trg_001",
        "kind": "research_digest",
        "merchant_id": "m_001_drmeera",
        "customer_id": None,
        "urgency": 2,
        "suppression_key": "research:dentists:2026-W17",
        "payload": {"category": "dentists", "top_item_id": "d_001"}
    }
})

# Healthz after warmup
test("HEALTHZ (after warmup)", "GET", "/v1/healthz")

# --- Phase 2: TICK ---
tick_result = test("TICK -> compose message", "POST", "/v1/tick", {
    "now": "2026-05-02T10:35:00Z",
    "available_triggers": ["trg_001"]
})

actions = tick_result.get("actions", [])
if actions:
    conv_id = actions[0].get("conversation_id")
    print(f"\n  *** COMPOSED MESSAGE ***")
    print(f"  {actions[0].get('body', '')}")
    print(f"  CTA: {actions[0].get('cta')}")
    print(f"  Rationale: {actions[0].get('rationale', '')[:120]}")

    # --- Phase 3: REPLY tests ---
    test("REPLY: auto-reply detection", "POST", "/v1/reply", {
        "conversation_id": conv_id,
        "merchant_id": "m_001_drmeera",
        "from_role": "merchant",
        "message": "Thank you for contacting Dr. Meera's Dental Clinic! Our team will respond shortly.",
        "received_at": "2026-05-02T10:42:00Z",
        "turn_number": 2
    })

    test("REPLY: positive intent (YES)", "POST", "/v1/reply", {
        "conversation_id": conv_id,
        "merchant_id": "m_001_drmeera",
        "from_role": "merchant",
        "message": "Yes please send the abstract!",
        "received_at": "2026-05-02T10:43:00Z",
        "turn_number": 3
    })

    test("REPLY: hostile opt-out", "POST", "/v1/reply", {
        "conversation_id": conv_id + "_2",
        "merchant_id": "m_001_drmeera",
        "from_role": "merchant",
        "message": "Stop messaging me. Not interested.",
        "received_at": "2026-05-02T10:44:00Z",
        "turn_number": 2
    })

print(f"\n{'='*55}")
print("  ALL TESTS COMPLETE")
print(f"{'='*55}\n")
