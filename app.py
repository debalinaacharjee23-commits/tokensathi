import re
import math
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder='.', template_folder='.')

def detect_language(text: str) -> str:
    if re.search(r'[\u0980-\u09FF]', text): return "Bengali"
    if re.search(r'[\u0C80-\u0CFF]', text): return "Kannada"
    if re.search(r'[\u0900-\u097F]', text): return "Hindi"
    if re.search(r'[\u0C00-\u0C7F]', text): return "Telugu"
    return "English"

def count_tokens(text: str) -> int:
    if not text or not text.strip(): return 0
    non_ascii = sum(1 for c in text if ord(c) > 127)
    ascii_chars = len(text) - non_ascii
    return max(1, math.ceil((ascii_chars / 4.0) + (non_ascii * 1.45)))

FILLER_PHRASES = [
    r"হ্যালো,?\s*", r"নমস্কার,?\s*", r"আমার আসলে একটা খুব জরুরি সাহায্যের দরকার ছিল,?\s*",
    r"তাই সব দিক বজায় রেখে\s*", r"যেটা পড়তে বোরিং লাগবে না আর খুব বেশি বড়ও হবে না\.?",
    r"এর মূল কারণ হলো\s*", r"আর তার ওপর\s*",
    r"ನಮಸ್ಕಾರ,?\s*", r"ನನಗೆ ತುಂಬಾ ತುರ್ತು ಸಹಾಯ ಬೇಕಾಗಿದೆ,?\s*", r"ದಯವಿಟ್ಟು ನನಗೆ ಸಹಾಯ ಮಾಡಿ,?\s*",
    r"नमस्ते,?\s*", r"मुझे वास्तव में आपकी मदद की ज़रूरत है,?\s*",
    r"hey,?\s*i really need your help\s*(?:with|writing)?\s*",
    r"so please write something that\s*", r"could you please\s*"
]

STOPWORDS = {
    "actually", "basically", "really", "very", "just", "please", "maybe", "somewhat",
    "আসলে", "একটু", "খুব", "একদম", "তো", "আর", "যেন", "তবে", "বরং", "বেশ",
    "ನಿಜವಾಗಿಯೂ", "ಸ್ವಲ್ಪ", "ತುಂಬಾ", "ಆದರೆ", "ಮತ್ತು",
    "वास्तव में", "थोड़ा", "बहुत", "बस", "कृपया", "शायद"
}

PIVOT_KEYWORDS = {
    "ইমেইল": "email", "বস": "boss", "প্রজেক্ট": "project", "ডেডলাইন": "deadline",
    "ভেন্ডর": "vendor", "অসুস্থ": "sick", "বাজেট": "budget", "ক্লায়েন্ট": "client",
    "দিন": "days", "সময়": "time", "সাহায্য": "help", "কাজ": "work",
    "ಆರ್ಡರ್": "order", "ಸಹಾಯ": "help", "ಸಮಯ": "time", "ಕೆಲಸ": "work",
    "ईमेल": "email", "बॉस": "boss", "प्रोजेक्ट": "project", "समय": "time", "मदद": "help"
}

def build_rule_pivot(text: str, entities: list) -> str:
    words = re.sub(r'[,\.\!\?।;:]', '', text).split()
    mapped = [PIVOT_KEYWORDS[w] for w in words if w in PIVOT_KEYWORDS]
    unique_terms = list(dict.fromkeys(mapped))
    ent_str = ", ".join(entities) if entities else "none"
    terms_str = " | ".join(unique_terms) if unique_terms else "core-request"
    return f"[Structured Intent: {terms_str}] [Protected Entities: {ent_str}]"

def run_rule_compression(raw_text: str) -> dict:
    lang = detect_language(raw_text)
    orig_tokens = count_tokens(raw_text)
    entities = re.findall(
        r'[\d০-৯೦-೯௧-௯]+(?:\s*(?:দিন|days?|hrs?|hours?|%|₹|\$|টাকা|ರೂ))?|#[A-Za-z0-9]+',
        raw_text
    )
    cleaned = raw_text.strip()
    for pattern in FILLER_PHRASES:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)

    kept = []
    for tok in cleaned.split():
        bare = re.sub(r'[,\.\!\?।;:"\']', '', tok).lower()
        if bare not in STOPWORDS or any(e in tok for e in entities):
            kept.append(tok)

    compressed = re.sub(r'\s+([,\.\!\?।])', r'\1', " ".join(kept)).strip()
    if len(compressed) < 5:
        compressed = raw_text.strip()

    opt_tokens = count_tokens(compressed)
    saved_tokens = max(0, orig_tokens - opt_tokens)
    saved_pct = round((saved_tokens / max(1, orig_tokens)) * 100, 1)
    all_entities_intact = all(e.split()[0] in compressed for e in entities)
    meaning_status = "PASS" if (all_entities_intact and len(compressed) >= 10) else "WARN"

    return {
        "language": lang,
        "original_tokens": orig_tokens,
        "optimized_tokens": opt_tokens,
        "saved_tokens": saved_tokens,
        "saved_percentage": saved_pct,
        "compressed_prompt": compressed,
        "entities_protected": entities,
        "meaning_preserved": meaning_status,
        "english_pivot": build_rule_pivot(compressed, entities)
    }

@app.route('/')
def home():
    return send_from_directory('.', 'index.html')

@app.route('/<path:path>')
def static_files(path):
    return send_from_directory('.', path)

@app.route('/optimize', methods=['POST'])
@app.route('/analyze', methods=['POST'])
@app.route('/api/optimize', methods=['POST'])
def optimize_endpoint():
    data = request.get_json(silent=True) or {}
    prompt_text = data.get('prompt') or data.get('text') or ""
    if not prompt_text.strip():
        return jsonify({"error": "Empty prompt"}), 400
    return jsonify(run_rule_compression(prompt_text))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
