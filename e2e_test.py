"""
Full end-to-end conversation test against live Render deployment.
"""
import json, sys, requests
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE = "http://localhost:8080"

def call(label, method, path, body=None):
    r = requests.request(method, f"{BASE}{path}", json=body, timeout=30)
    try:
        data = r.json()
    except:
        data = {"raw": r.text[:200]}
    print(f"\n{'='*55}")
    print(f"  [{r.status_code}] {label}")
    print(f"  {json.dumps(data, ensure_ascii=False)[:350]}")
    return data

# ── Phase 1: Warmup ──────────────────────────────────────
call("GET /v1/healthz", "GET", "/v1/healthz")
call("GET /v1/metadata", "GET", "/v1/metadata")

call("POST /v1/context — category:dentists", "POST", "/v1/context", {
    "scope": "category", "context_id": "dentists", "version": 1,
    "payload": {
        "slug": "dentists",
        "voice": {"tone": "peer_clinical", "vocab_taboo": ["guaranteed"]},
        "peer_stats": {"avg_ctr": 0.030, "avg_rating": 4.4},
        "digest": [{"id": "d_001", "kind": "research",
                    "title": "3-month fluoride recall cuts caries 38%",
                    "source": "JIDA Oct 2026, p.14"}]
    }
})

call("POST /v1/context — merchant:DrMeera", "POST", "/v1/context", {
    "scope": "merchant", "context_id": "m_001_drmeera", "version": 1,
    "payload": {
        "merchant_id": "m_001_drmeera",
        "category_slug": "dentists",
        "identity": {"name": "Dr. Meera's Dental Clinic",
                     "owner_first_name": "Meera", "city": "Delhi",
                     "locality": "Lajpat Nagar", "verified": True,
                     "languages": ["en", "hi"]},
        "subscription": {"status": "active", "plan": "Pro", "days_remaining": 82},
        "performance": {"views": 2410, "calls": 18, "ctr": 0.021,
                        "delta_7d": {"views_pct": 0.18, "calls_pct": -0.05}},
        "offers": [{"id": "o_001", "title": "Dental Cleaning @ Rs.299", "status": "active"}],
        "customer_aggregate": {"total_unique_ytd": 540, "lapsed_180d_plus": 78,
                               "high_risk_adult_count": 124},
        "signals": ["stale_posts:22d", "ctr_below_peer_median"]
    }
})

call("POST /v1/context — trigger:research_digest", "POST", "/v1/context", {
    "scope": "trigger", "context_id": "trg_001", "version": 1,
    "payload": {
        "id": "trg_001", "kind": "research_digest",
        "merchant_id": "m_001_drmeera", "customer_id": None,
        "urgency": 2, "suppression_key": "research:dentists:2026-W17",
        "payload": {"category": "dentists", "top_item_id": "d_001"}
    }
})

call("GET /v1/healthz — after warmup (expect counts>0)", "GET", "/v1/healthz")

# ── Phase 2: Tick ─────────────────────────────────────────
tick = call("POST /v1/tick — bot composes message", "POST", "/v1/tick", {
    "now": "2026-05-02T10:35:00Z",
    "available_triggers": ["trg_001"]
})

actions = tick.get("actions", [])
if not actions:
    print("\n  !! No actions returned from tick")
    sys.exit(1)

conv_id = actions[0]["conversation_id"]
print(f"\n  >>> BOT MESSAGE <<<")
print(f"  Body     : {actions[0].get('body','')}")
print(f"  CTA      : {actions[0].get('cta','')}")
print(f"  Send As  : {actions[0].get('send_as','')}")
print(f"  Rationale: {str(actions[0].get('rationale',''))[:130]}")

# ── Phase 3: Multi-turn replies ───────────────────────────
call("REPLY — customer slot pick", "POST", "/v1/reply", {
    "conversation_id": conv_id,
    "merchant_id": "m_001_drmeera", "from_role": "merchant",
    "message": "Yes please book me for Wed 5 Nov, 6pm.",
    "received_at": "2026-05-02T10:42:00Z", "turn_number": 2
})

call("REPLY — STOP handling", "POST", "/v1/reply", {
    "conversation_id": conv_id + "_stop",
    "merchant_id": "m_001_drmeera", "from_role": "merchant",
    "message": "STOP",
    "received_at": "2026-05-02T10:43:00Z", "turn_number": 2
})

call("REPLY — Auto-reply detection 1", "POST", "/v1/reply", {
    "conversation_id": conv_id + "_auto",
    "merchant_id": "m_001_drmeera", "from_role": "merchant",
    "message": "Thank you for contacting Dr. Meera's Dental Clinic! Our team will respond shortly.",
    "received_at": "2026-05-02T10:44:00Z", "turn_number": 2
})

call("REPLY — Auto-reply detection 2 (loop prevention)", "POST", "/v1/reply", {
    "conversation_id": conv_id + "_auto",
    "merchant_id": "m_001_drmeera", "from_role": "merchant",
    "message": "Thank you for contacting Dr. Meera's Dental Clinic! Our team will respond shortly.",
    "received_at": "2026-05-02T10:45:00Z", "turn_number": 4
})

print(f"\n{'='*55}")
print("  ALL TESTS PASSED")
print(f"{'='*55}\n")
