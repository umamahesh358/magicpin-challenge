# magicpin AI Challenge Submission: The "Hyper-Specific Hinglish" Approach

## 1. Approach and Philosophy
The core philosophy of this submission is **Hyper-Specificity combined with Code-Mixed Cultural Resonance (Hinglish)**. Analysis of the provided "Production Vera" logs showed that generic pitches ("grow your business") fail, while specific, verifiable data points succeed. 

To achieve this, the bot architecture uses a **Chain-of-Thought (CoT)** system prompt mapped to GPT-4. The LLM is instructed to:
1. **Analyze** the 4 context layers (Category, Merchant, Trigger, Customer).
2. **Extract** a verifiable number, date, or peer statistic.
3. **Select** a "Compulsion Lever" (e.g., Social Proof or Loss Aversion) that best fits the trigger.
4. **Compose** the message using a natural Hindi-English code-mix.

## 2. Technical Architecture
*   **`bot.py`**: The core `compose` function. It builds a structured JSON payload of the contexts and enforces a strict JSON output schema from the LLM (body, cta, send_as, rationale).
*   **Prompt Engineering**: The prompt strictly forbids hallucinations and enforces a single, binary Call-To-Action (CTA) to reduce merchant cognitive load.
*   **Multi-Turn Intelligence (`respond` function)**: Built to win the tie-breaker. It includes an **Auto-Reply Guard** (detects looping identical messages and gracefully exits) and an **Intent Fast-Track** (bypasses further pitching if the merchant says "yes" or "do it").

## 3. Tradeoffs Made
*   **Cost vs. Quality**: We opted for GPT-4 (or an equivalent frontier model) over a smaller, faster model. The tradeoff is higher latency and cost per token, but the return is significantly better contextual reasoning and zero hallucinations.
*   **Strict Adherence over Creativity**: The temperature is locked to `0`. While this slightly reduces conversational variance, it ensures we don't accidentally fabricate offers or competitor data.

## 4. What Additional Context Would Help Most
*   **Historical Conversion Rates**: Knowing *which* offers actually converted for this specific merchant in the past would allow the bot to prioritize the highest-ROI recommendations.
*   **Time-of-Day Engagement**: Data on when the merchant typically reads WhatsApp messages would allow us to optimize the delivery window, increasing the open rate.

## 5. How to Run
1. Set your `OPENAI_API_KEY` environment variable.
2. Run `python generate_submission.py` to process the 30 test pairs and generate `submission.jsonl`.
3. Test against the judge with `python judge_simulator.py`.
