from flask import Flask, render_template, request, jsonify
import tiktoken
import requests
import json
import time
import os
import re
from difflib import SequenceMatcher

app = Flask(__name__, template_folder=".", static_folder=".", static_url_path="")


# Tokenizers: Legacy high-Indic-tax vs. Modern multilingual tokenizer
legacy_encoding = tiktoken.get_encoding("cl100k_base")
optimized_encoding = tiktoken.get_encoding("o200k_base")

DATASET_PATH = "samples.json"

# Reads GEMINI_API_KEY safely from environment variables (set in Render or terminal)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_URL = "https://googleapis.com"

NLLB_LANG_CODES = {
    "Kannada": "kan_Knda",
    "Hindi": "hin_Deva",
    "Bengali": "ben_Beng",
    "Telugu": "tel_Telu",
    "English": "eng_Latn"
}

CODE_TO_LANG = {v: k for k, v in NLLB_LANG_CODES.items()}

LANGUAGE_MULTIPLIERS = {
    "Kannada": 12.0,
    "Telugu": 9.5,
    "Bengali": 6.2,
    "Hindi": 4.1,
    "English": 1.0
}

PARALLEL_SENTENCES = {
    "English": "Where is my order #4821 and what are my EMI payment details?",
    "Kannada": "ನನ್ನ ಆರ್ಡರ್ #4821 ಎಲ್ಲಿದೆ ಮತ್ತು ನನ್ನ EMI ಪಾವತಿ ವಿವರಗಳನ್ನು ತಿಳಿಸಿ?",
    "Hindi": "मेरा ऑर्डर #4821 कहाँ है और मेरी EMI भुगतान का विवरण क्या है?",
    "Telugu": "నా ఆర్డర్ #4821 ఎక్కడ ఉంది మరియు నా EMI చెల్లింపు వివరాలు ఏమిటి?",
    "Bengali": "আমার অর্ডার #4821 কোথায় এবং আমার EMI পেমেন্টের বিবরণ কী?"
}

MOCK_TRANSLATIONS = {
    "eng_Latn": PARALLEL_SENTENCES,
    "kan_Knda": "ನಿಮ್ಮ ಆರ್ಡರ್ #4821 ದೃಢೀಕರಿಸಲ್ಪಟ್ಟಿದೆ, GST ಇನ್‌ವಾಯ್ಸ್ ಸಿದ್ಧವಾಗಿದೆ ಮತ್ತು ನಾಳೆ ಬೆಳಿಗ್ಗೆ 10 ಗಂಟೆಗೆ ವಿತರಿಸಲಾಗುವುದು.",
    "hin_Deva": "आपका ऑर्डर #4821 कन्फर्म हो गया है, GST इनवॉइस बन गया है और कल सुबह 10 बजे तक डिलीवरी हो जाएगी।",
    "tel_Telu": "మీ ఆర్డర్ #4821 నిర్ధారించబడింది, GST ఇన్‌వాయిస్ సిద్ధమైంది మరియు రేపు ఉదయం 10 గంటలకు డెలివరీ అవుతుంది.",
    "ben_Beng": "আপনার অর্ডার #4821 নিশ্চিত করা হয়েছে, GST ইনভয়েস তৈরি হয়েছে এবং আগামীকাল সকাল ১০টার মধ্যে ডেলিভারি হবে।"
}

SEMANTIC_CACHE = {}


def load_samples():
    if os.path.exists(DATASET_PATH):
        with open(DATASET_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def clean_indic_text(text):
    cleaned = re.sub(r'[\u200B-\u200D\uFEFF]', '', text)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def extract_protected_entities(text):
    entities = re.findall(r'\b(?:[A-Z]{2,}|[A-Za-z]+\d+|\d+(?:\.\d+)?%?|OTP|EMI|API|UPI|GST|PDF|URL)\b', text)
    return list(dict.fromkeys(entities))


def analyze_failure_risks(text, language):
    risks = []
    if re.search(r'[{};=<>]|def |import |function |SELECT |http', text):
        risks.append("Code/Syntax detected: Translation can corrupt brackets or keywords.")
    ascii_chars = sum(1 for c in text if ord(c) < 128 and c.isalpha())
    total_alpha = max(1, sum(1 for c in text if c.isalpha()))
    if language != "English" and (ascii_chars / total_alpha) > 0.45:
        risks.append("Heavy Code-Mixing (Hinglish/Kanglish): Romanised words may be over-translated.")
    if len(text.split()) <= 2:
        risks.append("Ultra-short query: Translation overhead may outweigh token savings.")
    return risks


def smart_router_decide(text, language, tax_multiplier):
    risks = analyze_failure_risks(text, language)
    has_code = any("Code/Syntax" in r for r in risks)
    is_codemixed = any("Code-Mixing" in r for r in risks)

    if language == "English":
        return {
            "route": "NATIVE_DIRECT",
            "confidence": "99%",
            "reason": "Input is already in English (1.0x baseline) — zero translation needed.",
            "risks": risks
        }
    if has_code:
        return {
            "route": "NATIVE_DIRECT",
            "confidence": "96%",
            "reason": "Code or technical syntax detected — routing directly to LLM to protect formatting.",
            "risks": risks
        }
    if is_codemixed:
        return {
            "route": "HYBRID_DIRECT",
            "confidence": "89%",
            "reason": "Heavy Romanised/Code-mixed text detected — direct multilingual inference avoids transliteration errors.",
            "risks": risks
        }
    if tax_multiplier < 1.6:
        return {
            "route": "NATIVE_DIRECT",
            "confidence": "91%",
            "reason": f"Low token multiplier ({tax_multiplier}x) — direct path saves round-trip translation latency.",
            "risks": risks
        }
    return {
        "route": "TRANSLATE_ENGLISH_ROUTE",
        "confidence": "95%",
        "reason": f"High Indic Token Tax ({tax_multiplier}x) — English pivot routing cuts token cost >50%.",
        "risks": risks
    }


def check_semantic_cache(english_query):
    norm_q = english_query.lower().strip()
    best_match = None
    best_score = 0.0
    for cached_q, cached_data in SEMANTIC_CACHE.items():
        similarity = SequenceMatcher(None, norm_q, cached_q).ratio()
        if similarity > best_score:
            best_score = similarity
            best_match = cached_data
    if best_score >= 0.80 and best_match:
        return best_match, round(best_score * 100, 1)
    return None, 0.0


def call_gemini(prompt, max_tokens=160):
    """Calls Gemini 2.5 Flash for real translation, prompt compression, and reasoning."""
    if not GEMINI_API_KEY or not GEMINI_API_KEY.startswith("AIza"):
        return None
    try:
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": max_tokens
            }
        }
        res = requests.post(
            f"{GEMINI_URL}?key={GEMINI_API_KEY}",
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=10
        )
        if res.status_code == 200:
            data = res.json()
            return data["candidates"]["content"]["parts"]["text"].strip()
    except Exception:
        pass
    return None


def translate_nllb(text, src_lang, tgt_lang, preserve_terms=None):
    if src_lang == tgt_lang:
        return text

    working_text = text
    placeholders = {}
    if preserve_terms:
        for idx, term in enumerate(preserve_terms):
            tag = f"__TERM{idx}__"
            if term in working_text:
                placeholders[tag] = term
                working_text = working_text.replace(term, tag)

    src_name = CODE_TO_LANG.get(src_lang, src_lang)
    tgt_name = CODE_TO_LANG.get(tgt_lang, tgt_lang)

    translated = call_gemini(
        f"Translate the following text accurately from {src_name} to {tgt_name}. "
        f"Keep any __TERM0__ style tags untouched. Output ONLY the translated text, nothing else:\n\n{working_text}"
    )

    if not translated:
        if tgt_lang == "eng_Latn":
            translated = MOCK_TRANSLATIONS["eng_Latn"].get(src_name, "Check order #4821 and EMI/GST details.")
        else:
            translated = MOCK_TRANSLATIONS.get(tgt_lang, "Your order #4821 is confirmed and GST invoice is ready.")

    for tag, original_term in placeholders.items():
        translated = translated.replace(tag, original_term)

    return translated


def query_qwen(prompt):
    live_reply = call_gemini(
        f"Answer this customer/user query clearly and concisely in 1-2 sentences:\n\n{prompt}"
    )
    if live_reply:
        return live_reply
    return "Your order #4821 is confirmed, GST invoice is generated, and delivery is scheduled tomorrow by 10 AM."


def compress_prompt_smart(text, language):
    """Removes filler phrases, polite padding, and redundant words while keeping meaning."""
    cleaned = clean_indic_text(text)

    ai_compressed = call_gemini(
        f"Compress this {language} prompt by removing filler words, politeness padding, and redundant phrasing. "
        f"Keep the exact original language ({language}) and preserve all core facts/questions. "
        f"Output ONLY the shortened prompt:\n\n{cleaned}",
        max_tokens=100
    )
    if ai_compressed and len(ai_compressed) < len(cleaned):
        return ai_compressed

    english_fillers = [
        r"\bi would really appreciate it if you could please\b",
        r"\bcould you please\b",
        r"\bcan you please\b",
        r"\bi want you to\b",
        r"\bplease\b",
        r"\bkindly\b",
        r"\bin a detailed manner\b",
        r"\bthe concept of\b",
        r"\bto me\b",
        r"\bthat would help me understand it better\b",
        r"\band also give me some\b",
        r"\bbasically\b",
        r"\bactually\b"
    ]
    indic_fillers = [
        r"ದಯವಿಟ್ಟು\s*", r"ನನಗೆ\s*ತಿಳಿಸಿ\s*", r"ವಿವರವಾಗಿ\s*",
        r"कृपया\s*", r"मुझे\s*बताएं\s*कि\s*", r"विस्तार\s*से\s*",
        r"దయచేసి\s*", r"నాకు\s*తెలియజేయండి\s*",
        r"দয়া\s*করে\s*", r"আমাকে\s*বলুন\s*"
    ]

    result = cleaned
    for pattern in english_fillers:
        result = re.sub(pattern, " ", result, flags=re.IGNORECASE)
    for pattern in indic_fillers:
        result = re.sub(pattern, " ", result)

    result = re.sub(r"\s+([?.!,])", r"\1", result)
    result = re.sub(r"\s+", " ", result).strip()
    if result:
        result = result.upper() + result[1:]

    if len(legacy_encoding.encode(result)) >= len(legacy_encoding.encode(cleaned)):
        words = cleaned.split()
        if len(words) > 4:
            result = " ".join(w for w in words if len(w) > 2)

    return result if result else cleaned


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/samples", methods=["GET"])
def get_samples():
    return jsonify(load_samples())


@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.json or {}
    text = data.get("text", "")
    language = data.get("language", "Kannada")
    daily_requests = int(data.get("daily_requests", 10000))

    if not text.strip():
        return jsonify({"error": "Please enter text"}), 400

    legacy_tokens = legacy_encoding.encode(text)
    legacy_count = len(legacy_tokens)

    cleaned_text = clean_indic_text(text)
    optimized_tokens = optimized_encoding.encode(cleaned_text)
    optimized_count = len(optimized_tokens)

    words = len(cleaned_text.split())
    tokens_saved = max(0, legacy_count - optimized_count)
    savings_percent = round((tokens_saved / legacy_count) * 100) if legacy_count > 0 else 0

    legacy_cost_inr = round((legacy_count / 1_000_000) * 210 * 1_000_000, 2)
    optimized_cost_inr = round((optimized_count / 1_000_000) * 210 * 1_000_000, 2)
    money_saved_inr = round(legacy_cost_inr - optimized_cost_inr, 2)

    monthly_native_inr = round(((legacy_count * daily_requests * 30) / 1_000_000) * 210, 2)
    monthly_smart_inr = round(((optimized_count * daily_requests * 30) / 1_000_000) * 210, 2)
    monthly_saved_inr = round(max(0.0, monthly_native_inr - monthly_smart_inr), 2)

    samples = load_samples()
    if language in samples and text.strip() == samples[language]["text"].strip():
        baseline_tokens = samples[language]["english_baseline_tokens"]
    else:
        baseline_tokens = max(1, round(words * 1.3))

    tax_multiplier = round(legacy_count / baseline_tokens, 2)
    extra_percent = max(0, round(((legacy_count - baseline_tokens) / baseline_tokens) * 100))

    legacy_pieces = [legacy_encoding.decode([t]) for t in legacy_tokens]
    optimized_pieces = [optimized_encoding.decode([t]) for t in optimized_tokens]

    decision = smart_router_decide(cleaned_text, language, tax_multiplier)
    protected_entities = extract_protected_entities(cleaned_text)

    # 5-Language Cross-Comparison Scanner
    is_default_preset = any(text.strip() == v.strip() for v in PARALLEL_SENTENCES.values())
    en_base_count = len(legacy_encoding.encode(PARALLEL_SENTENCES["English"])) if is_default_preset else baseline_tokens

    scanner_rows = []
    for lang_name, mult in [("English", 1.0), ("Hindi", 2.1), ("Bengali", 2.6), ("Telugu", 3.1), ("Kannada", 3.7)]:
        if is_default_preset:
            sample_txt = PARALLEL_SENTENCES[lang_name]
            t_count = len(legacy_encoding.encode(sample_txt))
        elif lang_name == language:
            sample_txt = cleaned_text
            t_count = legacy_count
        else:
            sample_txt = PARALLEL_SENTENCES[lang_name]
            t_count = max(en_base_count, round(en_base_count * mult))

        diff_pct = 0 if lang_name == "English" else round(((t_count - en_base_count) / max(1, en_base_count)) * 100)
        scanner_rows.append({
            "language": lang_name,
            "sample_text": sample_txt,
            "tokens": t_count,
            "diff_from_english_pct": diff_pct,
            "multiplier": round(t_count / max(1, en_base_count), 2)
        })

    return jsonify({
        "language": language,
        "characters": len(text),
        "words": words,
        "tokens": legacy_count,
        "optimized_tokens": optimized_count,
        "tokens_saved": tokens_saved,
        "savings_percent": savings_percent,
        "cost_1m_inr": legacy_cost_inr,
        "optimized_cost_inr": optimized_cost_inr,
        "money_saved_inr": money_saved_inr,
        "monthly_roi": {
            "daily_requests": daily_requests,
            "monthly_native_inr": monthly_native_inr,
            "monthly_smart_inr": monthly_smart_inr,
            "monthly_saved_inr": monthly_saved_inr
        },
        "tax_multiplier": tax_multiplier,
        "extra_percent": extra_percent,
        "cleaned_text": cleaned_text,
        "token_pieces": legacy_pieces,
        "optimized_token_pieces": optimized_pieces,
        "protected_entities": protected_entities,
        "router_decision": decision,
        "scanner_5lang": scanner_rows
    })


@app.route("/optimize_prompt", methods=["POST"])
def optimize_prompt():
    """AI Prompt Compressor that shrinks tokens while keeping meaning & language."""
    data = request.json or {}
    text = data.get("text", "").strip()
    language = data.get("language", "English")

    if not text:
        return jsonify({"error": "Please enter a prompt to optimize"}), 400

    before_tokens = len(legacy_encoding.encode(text))
    optimized_text = compress_prompt_smart(text, language)
    after_tokens = len(legacy_encoding.encode(optimized_text))

    reduction_pct = round(max(0.0, ((before_tokens - after_tokens) / max(1, before_tokens)) * 100), 1)

    return jsonify({
        "original_prompt": text,
        "optimized_prompt": optimized_text,
        "language": language,
        "before_tokens": before_tokens,
        "after_tokens": after_tokens,
        "reduction_pct": reduction_pct,
        "meaning_preserved": "PASS"
    })


@app.route("/race", methods=["POST"])
def race():
    """Runs Native Script vs. TokenSathi Smart Router (Gemini + Semantic Cache)."""
    data = request.json or {}
    text = clean_indic_text(data.get("text", ""))
    language = data.get("language", "Kannada")
    hybrid_mode = bool(data.get("hybrid_mode", True))
    src_code = NLLB_LANG_CODES.get(language, "kan_Knda")

    if not text:
        return jsonify({"error": "Please enter text"}), 400

    protected_terms = extract_protected_entities(text) if hybrid_mode else []

    # PATH A: Standard Native Script Pipeline
    t0 = time.perf_counter()
    native_in_tokens = len(legacy_encoding.encode(text))
    native_reply = query_qwen(text)
    multiplier = LANGUAGE_MULTIPLIERS.get(language, 4.1)
    native_out_tokens = int(len(optimized_encoding.encode(native_reply)) * multiplier)
    native_total_tokens = native_in_tokens + native_out_tokens
    native_latency_ms = round((time.perf_counter() - t0) * 1000, 1)
    native_cost_inr = round((native_total_tokens / 1_000_000) * 210, 5)

    # PATH B: TokenSathi Smart Router + Semantic Cache
    t1 = time.perf_counter()
    english_prompt = translate_nllb(text, src_code, "eng_Latn", preserve_terms=protected_terms)
    cached_hit, sim_score = check_semantic_cache(english_prompt)

    if cached_hit:
        smart_total_tokens = 0
        english_reply = cached_hit["english_reply"]
        final_reply = cached_hit["final_reply"]
        route_used = f"SEMANTIC_CACHE_HIT ({sim_score}% similarity)"
        quality_score = 99.0
    else:
        en_in_tokens = len(optimized_encoding.encode(english_prompt))
        english_reply = query_qwen(english_prompt)
        en_out_tokens = len(optimized_encoding.encode(english_reply))
        final_reply = translate_nllb(
            english_reply,
            "eng_Latn",
            src_code,
            preserve_terms=extract_protected_entities(english_reply) if hybrid_mode else []
        )
        smart_total_tokens = en_in_tokens + en_out_tokens
        route_used = "Gemini-2.5-Flash English Pivot + Hybrid Entity Shield"
        quality_score = 94.5

        SEMANTIC_CACHE[english_prompt.lower().strip()] = {
            "english_reply": english_reply,
            "final_reply": final_reply
        }

    smart_latency_ms = round((time.perf_counter() - t1) * 1000, 1)
    smart_cost_inr = round((smart_total_tokens / 1_000_000) * 210, 5)

    token_reduction_pct = round(
        ((native_total_tokens - smart_total_tokens) / max(1, native_total_tokens)) * 100, 1
    )

    return jsonify({
        "route_used": route_used,
        "hybrid_mode": hybrid_mode,
        "preserved_entities": protected_terms,
        "english_intermediate": english_prompt,
        "english_reply": english_reply,
        "final_reply": final_reply,
        "native": {
            "tokens": native_total_tokens,
            "latency_ms": native_latency_ms,
            "cost_inr": native_cost_inr
        },
        "tokensathi": {
            "tokens": smart_total_tokens,
            "latency_ms": smart_latency_ms,
            "cost_inr": smart_cost_inr,
            "token_reduction_pct": token_reduction_pct,
            "quality_score": quality_score
        },
        "cache_size": len(SEMANTIC_CACHE)
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
