const PRESETS = {
  Kannada: "ನನ್ನ ಆರ್ಡರ್ #4821 ಎಲ್ಲಿದೆ ಮತ್ತು ನನ್ನ EMI ಪಾವತಿ ವಿವರಗಳನ್ನು ತಿಳಿಸಿ?",
  Hindi: "मेरा ऑर्डर #4821 कब डिलीवर होगा और मुझे GST इनवॉइस कैसे मिलेगा?",
  Telugu: "నా ఆర్డర్ #4821 ఎప్పుడు డెలివరీ అవుతుంది మరియు EMI వివరాలు చెప్పండి?",
  Bengali: "আমার অর্ডার #4821 কবে ডেলিভারি হবে এবং GST ইনভয়েস কীভাবে পাবো?",
  English: "Where is my order #4821 and how do I download my GST invoice?"
};

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function loadPreset(lang) {
  const langSelect = document.getElementById("language");
  const promptInput = document.getElementById("promptInput");
  if (langSelect) langSelect.value = lang;
  if (promptInput && PRESETS[lang]) promptInput.value = PRESETS[lang];
  runAnalyze();
}

async function runAnalyze() {
  const text = document.getElementById("promptInput").value;
  const language = document.getElementById("language").value;
  const daily_requests = document.getElementById("dailyRequests").value || 10000;

  try {
    const res = await fetch("/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, language, daily_requests })
    });
    const data = await res.json();
    if (data.error) return alert(data.error);

    document.getElementById("analyzeSection").classList.remove("hidden");
    document.getElementById("routerRoute").innerText = "ROUTER: " + data.router_decision.route;
    document.getElementById("routerConf").innerText = "Confidence: " + data.router_decision.confidence;
    document.getElementById("routerReason").innerText = data.router_decision.reason;

    const riskDiv = document.getElementById("riskContainer");
    riskDiv.innerHTML = (data.router_decision.risks || [])
      .map(r => `<span class="risk-tag">⚠ ${escapeHtml(r)}</span>`)
      .join("");

    document.getElementById("taxMultiplier").innerText = data.tax_multiplier + "x";
    document.getElementById("tokenCountCompare").innerText = `${data.tokens} → ${data.optimized_tokens}`;
    document.getElementById("savingsPct").innerText = data.savings_percent + "%";
    document.getElementById("monthlySaved").innerText =
      "₹" + data.monthly_roi.monthly_saved_inr.toLocaleString("en-IN");

    document.getElementById("legacyTokensBox").innerHTML = data.token_pieces
      .map(p => `<span class="chip chip-bad">${escapeHtml(p)}</span>`)
      .join("");

    document.getElementById("optTokensBox").innerHTML = data.optimized_token_pieces
      .map(p => `<span class="chip chip-good">${escapeHtml(p)}</span>`)
      .join("");
  } catch (err) {
    console.error("Analyze failed:", err);
  }
}

async function runRace() {
  const text = document.getElementById("promptInput").value;
  const language = document.getElementById("language").value;
  const hybrid_mode = document.getElementById("hybridMode").checked;

  await runAnalyze();
  const raceSection = document.getElementById("raceSection");
  const routeBadge = document.getElementById("raceRouteBadge");

  raceSection.classList.remove("hidden");
  routeBadge.innerText = "Running NLLB-200 + Qwen2.5-3B...";

  try {
    const res = await fetch("/race", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, language, hybrid_mode })
    });
    const data = await res.json();
    if (data.error) return alert(data.error);

    routeBadge.innerText = data.route_used;
    document.getElementById("nativeTokens").innerText = data.native.tokens;
    document.getElementById("nativeLatency").innerText = data.native.latency_ms + " ms";
    document.getElementById("nativeCost").innerText = "₹" + data.native.cost_inr;
    document.getElementById("nativeReply").innerText = data.final_reply;

    document.getElementById("smartTokens").innerText = data.tokensathi.tokens;
    document.getElementById("raceReduction").innerText = data.tokensathi.token_reduction_pct + "%";
    document.getElementById("smartLatency").innerText = data.tokensathi.latency_ms + " ms";
    document.getElementById("qualityScore").innerText = data.tokensathi.quality_score + "%";
    document.getElementById("englishPivot").innerText = data.english_intermediate;
    document.getElementById("smartReply").innerText = data.final_reply;
  } catch (err) {
    console.error("Race failed:", err);
    routeBadge.innerText = "Error running pipeline";
  }
}

window.addEventListener("DOMContentLoaded", () => {
  runAnalyze();
});
