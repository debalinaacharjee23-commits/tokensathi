# ⚡ TokenSathi v2.0
**₹0 Token Cost • Zero-LLM • Zero-API • 100% Rule-Based Indic Prompt Optimizer**

TokenSathi eliminates the **"Indic Token Tax"**—where non-Latin Indian scripts (Bengali, Hindi, Kannada, Telugu) consume **2x to 4x more BPE tokens** than English for the exact same semantic meaning. Sitting as a deterministic `<1ms` middleware layer before any LLM call, TokenSathi cuts token usage by up to **70%** with zero GPU or secondary-model overhead.

---

## 🎯 The Problem: The Indic Token Tax
Modern LLM tokenizers (BPE) are trained primarily on English text. Because UTF-8 Indic characters occupy 3 bytes and lack dense subword merges, Indian startups and developers pay **3x–4x higher API costs** and suffer higher decoding latency for the same conversation.

---

## 🚀 Key Features
1. **🔍 Token Tax Scanner:** Calculates real-time BPE token fertility (tokens/word), UTF-8 encoding multiplier vs. English baseline, and context window freed.
2. **✂️ 4-Stage Deterministic Rule-Based Optimizer:**
   - **Entity Shielding:** Regex-locks numbers, currency (`₹`, `$`, `টাকা`, `ರೂ`), dates, durations, and IDs so critical facts are never altered.
   - **Conversational Filler & Stopword Pruning:** Strips politeness padding and consecutive duplicates while preserving polarity, negations, and core intent.
   - **Phonetic ASCII Pack (1-Byte):** Transliterates 3-byte Indic Unicode into lightweight 1-byte phonetic ASCII understood natively by modern LLMs.
   - **Output-Tax Deflector:** Injects concise `[SYS_DIRECTIVE]` constraints to prevent verbose, high-cost output token generation.
3. **🛡️ Smart Router & Pipeline Race:** Benchmarks TokenSathi's `<1ms` CPU execution against high-latency (~400ms) neural prompt compressors.
4. **💰 Startup ROI Calculator:** Projects monthly and annual ₹ savings at scale.

---

## 🛠️ Tech Stack & Architecture
- **Backend:** Python 3, Flask (`app.py`), Gunicorn (`Procfile`)
- **Frontend:** Vanilla HTML5, CSS3, JavaScript (`index.html`, `script.js`, `style.css`)
- **Benchmarks:** Cross-lingual semantic baseline dataset (`samples.json`)
- **Dependencies:** Zero heavy ML libraries (`flask==3.0.3`, `gunicorn==22.0.0`)

---

## ⚙️ Quick Start & API Usage

### Local Setup
```bash
git clone https://github.com
cd tokensathi
pip install -r requirements.txt
python app.py
```

### REST API Endpoint (`POST /api/optimize`)
```bash
curl -X POST http://localhost:5000/api/optimize \
  -H "Content-Type: application/json" \
  -d '{"prompt": "হ্যালো, আমার আসলে একটা খুব জরুরি সাহায্যের দরকার ছিল, ৩ দিন বাড়তি সময় চাই।"}'
```

---

## 🏆 Built For
**The Big Hack 2.0 — AI Research Track (Open Domain)**
