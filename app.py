import json
import os
import re
import time
from difflib import SequenceMatcher

import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

# ----------------------------------------------------------------- Safe Tokenizer Setup
try:
    import tiktoken
    legacy_encoding = tiktoken.get_encoding("cl100k_base")
    optimized_encoding = tiktoken.get_encoding("o200k_base")
    TIKTOKEN_READY = True
except Exception as e:
    print(f"Warning: tiktoken failed to load ({e}). Using fallback estimator.")
    legacy_encoding = None
    optimized_encoding = None
    TIKTOKEN_READY = False


def count_tokens(text, mode="legacy"):
    if not text:
        return 0
    if TIKTOKEN_READY:
        try:
            enc = optimized_encoding if mode == "optimized" else legacy_encoding
            return len(enc.encode(text))
        except Exception:
            pass
    # Safe fallback if tiktoken is unavailable on Render
    divisor = 3.5 if mode == "optimized" else 2.0
    return max(1, int(len(text) / divisor))


# ----------------------------------------------------------------- Config & Constants
DATASET_PATH = os.path.join(os.path.dirname(__file__), "samples.json")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_URL = "https://groq.com"
GROQ_MODEL = "llama-3.1-8b-instant"

RATE_INR_PER_1M = 210

NLLB_LANG_CODES = {
    "Kannada": "kan_Knda",
    "Hindi": "hin_Deva",
    "Bengali": "ben_Beng",
    "Telugu": "tel_Telu",
    "Tamil": "tam_Taml",
    "English": "eng_Latn",
}
CODE_TO_LANG = {v: k for k, v in NLLB_LANG_CODES.items()}

PARALLEL_SENTENCES = {
    "English": "Where is my order #4821 and what are my EMI payment details?",
    "Kannada": "ನನ್ನ ಆರ್ಡರ್ #4821 ಎಲ್ಲಿದೆ ಮತ್ತು ನನ್ನ EMI ಪಾವತಿ ವಿವರಗಳನ್ನು ತಿಳಿಸಿ?",
    "Hindi": "मेरा ऑर्डर #4821 कहाँ है और मेरी EMI भुगतान का विवरण क्या है?",
    "Telugu": "నా ఆర్డర్ #4821 ఎక్కడ ఉంది మరియు నా EMI చెల్లింపు వివరాలు ఏమిటి?",
    "Bengali": "আমার অর্ডার #4821 কোথায় এবং আমার EMI পেমেন্টের বিবরণ কী?",
}

SEMANTIC_CACHE = []


# ----------------------------------------------------------------- Helpers
def llm_available():
    return bool(GROQ_API_KEY) and GROQ_API_KEY.startswith("gsk_")


def detect_script_language(text, fallback_lang="English"):
    ranges = {
        "Kannada": ("\u0C80", "\u0CFF"),
        "Bengali": ("\u0980", "\u09FF"),
        "Hindi": ("\u0900", "\u097F"),
        "Telugu": ("\u0C00", "\u0C7F"),
        "Tamil": ("\u0B80", "\u0BFF"),
    }
    counts = {
        lang: sum(1 for c in text if lo <= c <= hi)
        for lang, (lo, hi) in ranges.items()
    }
    detected = max(counts, key=counts.get)
    if counts[detected] > 0:
        return detected
    return fallback_lang if fallback_lang in NLLB_LANG_CODES else "English"


def load_samples():
    if os.path.exists(DATASET_PATH):
        try:
            with open(DATASET_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print("samples.json error:", e)
    return PARALLEL_SENTENCES


def clean_indic_text(text):
    cleaned = re.sub(r"[\u200B\uFEFF]", "", str(text))
    return re.sub(r"\s+", " ", cleaned).strip()


def extract_protected_entities(text):
    pattern = (
        r"(?<![A-Za-z0-9])(?:\d+(?:\.\d+)?%|[A-Z]{2,}|[A-Za-z]+\d+|\d+(?:\.\d+)?)(?![A-Za-z0-9])"
    )
    return list(dict.fromkeys(re.findall(pattern, text)))


def numbers_in(text):
    return re.findall(r"\d+", text)


def analyze_failure_risks(text, language):
    risks = []
    if re.search(r"[{};]|=>|\bdef \w+\(|\bimport \w+|\bfunction\s*\(|\bSELECT\s|https?://", text):
        risks.append("Code/Syntax detected: translation can corrupt brackets or keywords.")
    ascii_chars = sum(1 for c in text if ord(c) < 128 and c.isalpha())
    total_alpha = max(1, sum(1 for c in text if c.isalpha()))
    if language != "English" and (ascii_chars / total_alpha) > 0.45:
        risks.append("Heavy code-mixing (Hinglish/Kanglish): romanised words may be mistranslated.")
    if len(text.split()) <= 2:
        risks.append("Ultra-short query: translation overhead may outweigh token savings.")
    return risks


def smart_router_decide(text, language, tax_multiplier):
    risks = analyze_failure_risks(text, language)
    has_code = any("Code/Syntax" in r for r in risks)
    is_codemixed = any("code-mixing" in r for r in risks)

    def out(route, reason):
        return {"route": route, "confidence": "rule-based", "reason": reason, "risks": risks}

    if language == "English":
        return out("NATIVE_DIRECT", "Input is already English, no translation needed.")
    if has_code:
        return out("NATIVE_DIRECT", "Code/technical syntax detected, send directly to protect formatting.")
    if is_codemixed:
        return out("HYBRID_DIRECT", "Heavy romanised/code-mixed text, direct inference avoids transliteration errors.")
    if tax_multiplier < 1.6:
        return out("NATIVE_DIRECT", f"Low token multiplier ({tax_multiplier}x), the pivot is unlikely to pay off.")
    return out(
        "TRANSLATE_ENGLISH_ROUTE",
        f"High token multiplier ({tax_multiplier}x), an English pivot may reduce cost.",
    )


def check_semantic_cache(english_query):
    norm_q = english_query.lower().strip()
    best, best_score = None, 0.0
    for entry in SEMANTIC_CACHE:
        score = SequenceMatcher(None, norm_q, entry["key"]).ratio()
        if score > best_score:
            best, best_score = entry, score
    if best and best_score >= 0.80 and numbers_in(norm_q) == numbers_in(best["key"]):
        return best, round(best_score * 100, 1)
    return None, 0.0


# ----------------------------------------------------------------- LLM Calls
def call_llm(prompt, max_tokens=250):
    if not llm_available():
        return None
    try:
        res = requests.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": GROQ_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
                "max_tokens": max_tokens,
            },
            timeout=15,
        )
        if res.status_code == 200:
            return res.json()["choices"][0]["message"]["content"].strip()
        print("LLM HTTP error:", res.status_code, res.text[:200])
    except Exception as e:
        print("LLM error:", e)
    return None


def translate_llm(text, src_lang, tgt_lang, preserve_terms=None):
    if src_lang == tgt_lang:
        return text

    working_text = text
    placeholders = {}
    for idx, term in enumerate(preserve_terms or []):
        tag = f"__TERM{idx}__"
        if term in working_text:
            placeholders[tag] = term
            working_text = working_text.replace(term, tag)

    src_name = CODE_TO_LANG.get(src_lang, src_lang)
    tgt_name = CODE_TO_LANG.get(tgt_lang, tgt_lang)

    translated = call_llm(
        f"Translate the following text accurately from {src_name} to {tgt_name}. "
        f"Keep any __TERM0__ style tags untouched. Output ONLY the translated text:\n\n{working_text}"
    )
    if not translated:
        return None
    for tag, original in placeholders.items():
        translated = translated.replace(tag, original)
    return translated


def query_llm(prompt, reply_language):
    return call_llm(
        f"Answer this customer query clearly and concisely in 1-2 sentences. "
        f"Reply in {reply_language}:\n\n{prompt}"
    )


def compress_prompt_smart(text):
    cleaned = clean_indic_text(text)
    before = count_tokens(cleaned)

    ai = call_llm(
        "Rewrite this prompt in the SAME language: fix stretched words and grammar, remove filler "
        "and redundancy, keep every fact, name, number and intent. Output ONLY the rewritten prompt:\n\n"
        + cleaned
    )

    if ai:
        compressed = clean_indic_text(ai)
    else:
        # Fallback rule-based compression if GROQ_API_KEY is missing or API fails
        compressed = re.sub(r"(.)\1{2,}", r"\1\1", cleaned)
        fillers = r"\b(please|kindly|basically|actually|just|very|really)\b"
        compressed = re.sub(fillers, "", compressed, flags=re.IGNORECASE)
        compressed = re.sub(r"\s+", " ", compressed).strip()

    after = count_tokens(compressed)
    if after >= before:
        compressed = cleaned
        after = before

    return {
        "original": cleaned,
        "compressed": compressed,
        "tokens_before": before,
        "tokens_after": after,
        "tokens_saved": max(0, before - after),
    }


# ----------------------------------------------------------------- Flask Routes
@app.route("/")
def index():
    template_path = os.path.join(app.root_path, "templates", "index.html")
    if not os.path.exists(template_path):
        return (
            "<h2>Backend is running!</h2>"
            "<p>However, <code>templates/index.html</code> was not found. "
            "Move your <code>index.html</code> file into a folder named <code>templates/</code>.</p>",
            200,
        )
    return render_template("index.html")


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "tiktoken_loaded": TIKTOKEN_READY,
        "groq_configured": llm_available(),
    })


@app.route("/samples", methods=["GET"])
def get_samples():
    return jsonify(load_samples())


@app.route("/analyze", methods=["POST"])
def analyze():
    try:
        data = request.get_json(silent=True) or {}
        raw_text = data.get("text", "")
        if not raw_text.strip():
            return jsonify({"error": "Text input is required."}), 400

        cleaned = clean_indic_text(raw_text)
        requested_lang = data.get("language", "English")
        language = detect_script_language(cleaned, fallback_lang=requested_lang)

