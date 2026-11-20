import json
import os
import re
import time
from difflib import SequenceMatcher

import requests
import tiktoken
from flask import Flask, jsonify, render_template, request

# index.html must live in ./templates/ (and any css/js in ./static/)
app = Flask(__name__)

# Tokenizers: older cl100k (GPT-4) vs newer o200k (GPT-4o)
legacy_encoding = tiktoken.get_encoding("cl100k_base")
optimized_encoding = tiktoken.get_encoding("o200k_base")

DATASET_PATH = "samples.json"

# Set GROQ_API_KEY in your environment (terminal or Render dashboard)
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.1-8b-instant"

# Assumed price used for every cost figure (INR per 1M tokens). Change as needed.
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

# Real parallel sentences (the same meaning in each language)
PARALLEL_SENTENCES = {
    "English": "Where is my order #4821 and what are my EMI payment details?",
    "Kannada": "ನನ್ನ ಆರ್ಡರ್ #4821 ಎಲ್ಲಿದೆ ಮತ್ತು ನನ್ನ EMI ಪಾವತಿ ವಿವರಗಳನ್ನು ತಿಳಿಸಿ?",
    "Hindi": "मेरा ऑर्डर #4821 कहाँ है और मेरी EMI भुगतान का विवरण क्या है?",
    "Telugu": "నా ఆర్డర్ #4821 ఎక్కడ ఉంది మరియు నా EMI చెల్లింపు వివరాలు ఏమిటి?",
    "Bengali": "আমার অর্ডার #4821 কোথায় এবং আমার EMI পেমেন্টের বিবরণ কী?",
}

# Each entry: {"key": cached english prompt, "english_reply", "final_reply", "core_tokens"}
SEMANTIC_CACHE = []


# ----------------------------------------------------------------- helpers
def count_tokens(text):
    return len(legacy_encoding.encode(text))


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
    # Latin-only text: trust the caller (could be romanised Kannada, etc.)
    return fallback_lang if fallback_lang in NLLB_LANG_CODES else "English"


def load_samples():
    if os.path.exists(DATASET_PATH):
        try:
            with open(DATASET_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print("samples.json error:", e)
    return {}


def clean_indic_text(text):
    # Keep ZWJ/ZWNJ (\u200C, \u200D): Indic scripts need them for conjuncts.
    cleaned = re.sub(r"[\u200B\uFEFF]", "", text)
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
    """Simple rule-based router. Rules are heuristics, not measured accuracies."""
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
        f"High token multiplier ({tax_multiplier}x), an English pivot may reduce cost "
        f"(see /race for the measured result).",
    )


def check_semantic_cache(english_query):
    """String-similarity cache. Refuses a hit if the numbers differ (e.g. #4821 vs #4822)."""
    norm_q = english_query.lower().strip()
    best, best_score = None, 0.0
    for entry in SEMANTIC_CACHE:
        score = SequenceMatcher(None, norm_q, entry["key"]).ratio()
        if score > best_score:
            best, best_score = entry, score
    if best and best_score >= 0.80 and numbers_in(norm_q) == numbers_in(best["key"]):
        return best, round(best_score * 100, 1)
    return None, 0.0


# --------------------------------------------------------------------- LLM
def call_llm(prompt, max_tokens=250):
    """Calls Groq (Llama 3.1 8B). Returns text, or None on any failure."""
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
    """Translate with the LLM. Returns None if the LLM is unavailable (no fake output)."""
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
    """Shortens a prompt. Uses the LLM if available, else simple filler removal."""
    cleaned = clean_indic_text(text)
    before = count_tokens(cleaned)

    ai = call_llm(
        "Rewrite this prompt in the SAME language: fix stretched words and grammar, remove filler "
        "and redundancy, keep every fact, name, number and intent. Output ONLY the rewritten prompt:\n\n"
        + cleaned,
        max_tokens=220,
    )
    if ai and count_tokens(ai) < before:
        return ai

    result = re.sub(r"([A-Za-z])\1{2,}", r"\1", cleaned)  # hiiiii -> hi (letters only)
    replacements = [
        (r"^(hi|hello|hey)[,!.\s]+", ""),
        (r"\bi really need your help\b", "Please help"),
        (r"\bi would really appreciate it if you could please\b", ""),
        (r"\bcould you please\b", ""),
        (r"\bcan you please\b", ""),
        (r"\bi am wanting to\b", "I want to"),
        (r"\bi want you to\b", ""),
        (r"\bsummarize this article for me and give me the main points\b", "Summarize the main points"),
        (r"\ba bit\b", ""),
        (r"\bkind of\b", ""),
        (r"\bsomewhat\b", ""),
        (r"\breally\b", ""),
        (r"\bbasically\b", ""),
        (r"\bactually\b", ""),
        (r"\bplease\b", ""),
        (r"\bkindly\b", ""),
        (r"\bin a detailed manner\b", "clearly"),
        (r"\bthe concept of\b", ""),
        (r"\bthat would help me understand it better\b", ""),
        (r"\band also give me some\b", "with"),
        (r"\bi\b", "I"),
    ]
    for pattern, repl in replacements:
        result = re.sub(pattern, repl, result, flags=re.IGNORECASE)

    result = re.sub(r"\s+([?.!,])", r"\1", result)
    result = re.sub(r"\s+", " ", result).strip()
    if result:
        result = result[:1].upper() + result[1:]
        if result[-1] not in ".!?।":
            result += "."
    return result or cleaned


# ------------------------------------------------------------------ routes
@app.route("/")
def home():
    return render_template("index.html")


@app.route("/samples", methods=["GET"])
def get_samples():
    return jsonify(load_samples())


@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.get_json(silent=True) or {}
    text = data.get("text", "")
    if not text.strip():
        return jsonify({"error": "Please enter text"}), 400

    language = detect_script_language(text, data.get("language", "English"))

    try:
        daily_requests = max(1, int(data.get("daily_requests", 10000)))
    except (TypeError, ValueError):
        daily_requests = 10000

    legacy_tokens = legacy_encoding.encode(text)
    legacy_count = len(legacy_tokens)

    cleaned_text = clean_indic_text(text)
    optimized_tokens = optimized_encoding.encode(cleaned_text)
    optimized_count = len(optimized_tokens)

    words = len(cleaned_text.split())
    tokens_saved = max(0, legacy_count - optimized_count)
    savings_percent = round(tokens_saved / legacy_count * 100) if legacy_count else 0

    # NOTE: "cost_1m_inr" is the cost of THIS request (kept as key name for the frontend)
    legacy_cost_inr = round(legacy_count / 1_000_000 * RATE_INR_PER_1M, 6)
    optimized_cost_inr = round(optimized_count / 1_000_000 * RATE_INR_PER_1M, 6)
    money_saved_inr = round(legacy_cost_inr - optimized_cost_inr, 6)

    monthly_native_inr = round(legacy_count * daily_requests * 30 / 1_000_000 * RATE_INR_PER_1M, 2)
    monthly_smart_inr = round(optimized_count * daily_requests * 30 / 1_000_000 * RATE_INR_PER_1M, 2)
    monthly_saved_inr = round(max(0.0, monthly_native_inr - monthly_smart_inr), 2)

    # English baseline: measured if this is a known sample, otherwise an estimate
    samples = load_samples()
    sample = samples.get(language) or {}
    if sample.get("text", "").strip() == text.strip() and sample.get("english_baseline_tokens"):
        baseline_tokens = max(1, sample["english_baseline_tokens"])
        baseline_is_estimate = False
    else:
        baseline_tokens = max(1, round(words * 1.3))
        baseline_is_estimate = True

    tax_multiplier = round(legacy_count / baseline_tokens, 2)
    extra_percent = max(0, round((legacy_count - baseline_tokens) / baseline_tokens * 100))

    legacy_pieces = [legacy_encoding.decode([t]) for t in legacy_tokens]
    optimized_pieces = [optimized_encoding.decode([t]) for t in optimized_tokens]

    decision = smart_router_decide(cleaned_text, language, tax_multiplier)
    protected_entities = extract_protected_entities(cleaned_text)

    # Cross-language table: ALWAYS real token counts of real parallel sentences
    en_base_count = count_tokens(PARALLEL_SENTENCES["English"])
    scanner_rows = []
    for lang_name, sample_txt in PARALLEL_SENTENCES.items():
        t_count = count_tokens(sample_txt)
        scanner_rows.append({
            "language": lang_name,
            "sample_text": sample_txt,
            "tokens": t_count,
            "diff_from_english_pct": 0 if lang_name == "English"
            else round((t_count - en_base_count) / en_base_count * 100),
            "multiplier": round(t_count / en_base_count, 2),
        })

    return jsonify({
        "language": language,
        "characters": len(text),
        "words": words,
        "tokens": legacy_count,
        "optimized_tokens": optimized_count,
        "tokens_saved": tokens_saved,
        "savings_percent": savings_percent,
        "cost_1m_inr": legacy_cost_inr,          # cost of this request, cl100k
        "optimized_cost_inr": optimized_cost_inr,  # cost of this request, o200k
        "money_saved_inr": money_saved_inr,
        "rate_inr_per_1m_tokens": RATE_INR_PER_1M,
        "monthly_roi": {
            "daily_requests": daily_requests,
            "monthly_native_inr": monthly_native_inr,
            "monthly_smart_inr": monthly_smart_inr,
            "monthly_saved_inr": monthly_saved_inr,
        },
        "tax_multiplier": tax_multiplier,
        "baseline_tokens": baseline_tokens,
        "baseline_is_estimate": baseline_is_estimate,
        "extra_percent": extra_percent,
        "cleaned_text": cleaned_text,
        "token_pieces": legacy_pieces,
        "optimized_token_pieces": optimized_pieces,
        "protected_entities": protected_entities,
        "router_decision": decision,
        "scanner_5lang": scanner_rows,
        "scanner_source": "real parallel sentences, cl100k_base token counts",
    })


@app.route("/optimize_prompt", methods=["POST"])
def optimize_prompt():
    data = request.get_json(silent=True) or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "Please enter a prompt to optimize"}), 400

    language = detect_script_language(text, data.get("language", "English"))
    before_tokens = count_tokens(text)
    optimized_text = compress_prompt_smart(text)
    after_tokens = count_tokens(optimized_text)
    reduction_pct = round(max(0.0, (before_tokens - after_tokens) / max(1, before_tokens) * 100), 1)

    # Real (limited) check: every number/acronym/ID in the original must survive
    missing = [e for e in extract_protected_entities(text) if e not in optimized_text]

    return jsonify({
        "original_prompt": text,
        "optimized_prompt": optimized_text,
        "language": language,
        "before_tokens": before_tokens,
        "after_tokens": after_tokens,
        "reduction_pct": reduction_pct,
        "meaning_preserved": "PASS" if not missing else "CHECK",
        "missing_entities": missing,
        "used_llm": llm_available(),
    })


@app.route("/race", methods=["POST"])
def race():
    """Native-script pipeline vs English-pivot pipeline, all token counts measured."""
    data = request.get_json(silent=True) or {}
    text = clean_indic_text(data.get("text", ""))
    if not text:
        return jsonify({"error": "Please enter text"}), 400
    if not llm_available():
        return jsonify({"error": "LLM unavailable: set a valid GROQ_API_KEY (starts with gsk_)."}), 503

    language = detect_script_language(text, data.get("language", "English"))
    hybrid_mode = bool(data.get("hybrid_mode", True))
    src_code = NLLB_LANG_CODES.get(language, "eng_Latn")
    protected_terms = extract_protected_entities(text) if hybrid_mode else []

    # PATH A: native script, the model answers in the user's language
    t0 = time.perf_counter()
    native_reply = query_llm(text, language)
    if not native_reply:
        return jsonify({"error": "LLM call failed (native path). Check server logs."}), 502
    native_in = count_tokens(text)
    native_out = count_tokens(native_reply)
    native_total = native_in + native_out
    native_latency_ms = round((time.perf_counter() - t0) * 1000, 1)
    native_cost_inr = round(native_total / 1_000_000 * RATE_INR_PER_1M, 6)

    # PATH B: English pivot + cache
    t1 = time.perf_counter()
    english_prompt = translate_llm(text, src_code, "eng_Latn", preserve_terms=protected_terms)
    if not english_prompt:
        return jsonify({"error": "LLM call failed (translation to English)."}), 502

    cached, sim_score = check_semantic_cache(english_prompt)
    if cached:
        cache_hit = True
        english_reply = cached["english_reply"]
        final_reply = cached["final_reply"]
        core_tokens = cached["core_tokens"]
        route_used = f"SEMANTIC_CACHE_HIT ({sim_score}% string similarity)"
        overhead_tokens = native_in  # translation of the question still ran
    else:
        cache_hit = False
        english_reply = query_llm(english_prompt, "English")
        if not english_reply:
            return jsonify({"error": "LLM call failed (English answer)."}), 502
        final_reply = translate_llm(
            english_reply, "eng_Latn", src_code,
            preserve_terms=extract_protected_entities(english_reply) if hybrid_mode else [],
        )
        if not final_reply:
            return jsonify({"error": "LLM call failed (translation back)."}), 502
        core_tokens = count_tokens(english_prompt) + count_tokens(english_reply)
        # Native-script text the two translation calls had to read/write
        overhead_tokens = native_in + count_tokens(final_reply)
        route_used = f"{GROQ_MODEL} (Groq) English pivot" + (" + entity shield" if hybrid_mode else "")
        SEMANTIC_CACHE.append({
            "key": english_prompt.lower().strip(),
            "english_reply": english_reply,
            "final_reply": final_reply,
            "core_tokens": core_tokens,
        })

    smart_latency_ms = round((time.perf_counter() - t1) * 1000, 1)
    smart_total_with_overhead = core_tokens + overhead_tokens
    smart_cost_inr = round(core_tokens / 1_000_000 * RATE_INR_PER_1M, 6)

    reduction_core = round((native_total - core_tokens) / max(1, native_total) * 100, 1)
    reduction_overhead = round((native_total - smart_total_with_overhead) / max(1, native_total) * 100, 1)

    # Measured quality proxy: share of protected entities that survived in the final reply
    if protected_terms:
        kept = sum(1 for t in protected_terms if t in final_reply or t in native_reply)
        entity_score = round(kept / len(protected_terms) * 100, 1)
    else:
        entity_score = None

    return jsonify({
        "route_used": route_used,
        "cache_hit": cache_hit,
        "api_tokens_spent_on_answer": 0 if cache_hit else core_tokens,
        "hybrid_mode": hybrid_mode,
        "preserved_entities": protected_terms,
        "english_intermediate": english_prompt,
        "english_reply": english_reply,
        "final_reply": final_reply,
        "native_reply": native_reply,
        "native": {
            "tokens": native_total,
            "latency_ms": native_latency_ms,
            "cost_inr": native_cost_inr,
        },
        "tokensathi": {
            "tokens": core_tokens,
            "overhead_tokens": overhead_tokens,
            "tokens_with_overhead": smart_total_with_overhead,
            "latency_ms": smart_latency_ms,
            "cost_inr": smart_cost_inr,
            "token_reduction_pct": reduction_core,
            "token_reduction_pct_with_overhead": reduction_overhead,
            "quality_score": entity_score,
            "quality_metric": "protected-entity preservation (not a full quality score)",
        },
        "cache_size": len(SEMANTIC_CACHE),
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
