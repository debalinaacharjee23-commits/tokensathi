const PRESETS = {
  Kannada: "ನನ್ನ ಆರ್ಡರ್ #4821 ಎಲ್ಲಿದೆ ಮತ್ತು ನನ್ನ EMI ಪಾವತಿ ವಿವರಗಳನ್ನು ತಿಳಿಸಿ?",
  Hindi: "मेरा ऑर्डर #4821 कहाँ है और मेरी EMI भुगतान का विवरण क्या है?",
  Telugu: "నా ఆర్డర్ #4821 ఎక్కడ ఉంది మరియు నా EMI చెల్లింపు వివరాలు ఏమిటి?",
  Bengali: "আমার অর্ডার #4821 কোথায় এবং আমার EMI পেমেন্টের বিবরণ কী?",
  English: "Where is my order #4821 and what are my EMI payment details?"
};

const FLAGS = {
  English: "🇬🇧 English",
  Kannada: "🇮🇳 Kannada",
  Hindi: "🇮🇳 Hindi",
  Telugu: "🇮🇳 Telugu",
  Bengali: "🇮🇳 Bengali"
};

let lastLegacyTokens = 60;
let lastOptTokens = 23;

// Inject Dynamic PWA Manifest so phones can install TokenSathi as an app
(function initPWA() {
  const manifest = {
    name: "TokenSathi — Indic LLM Optimizer",
    short_name: "TokenSathi",
    start_url: "/",
    display: "standalone",
    background_color: "#070b14",
    theme_color: "#38bdf8",
    icons: [{
      src: "data:image/svg+xml,<svg xmlns='http://w3.org' viewBox='0 0 100 100'><rect width='100' height='100' rx='22' fill='%230b0f19'/><text x='50' y='68' font-size='58' text-anchor='middle'>⚡</text></svg>",
      sizes: "192x192",
      type: "image/svg+xml"
    }]
  };
  const blob = new Blob([JSON.stringify(manifest)], { type: "application/json" });
  const link = document.getElementById("pwa-manifest");
  if (link) link.href = URL.createObjectURL(blob);
})();

function switchTab(tabId) {
  ["tab-scanner", "tab-optimizer", "tab-router"].forEach(id => {
    document.getElementById(id).classList.add("hidden");
    document.getElementById("btn-" + id).classList.remove("active");
  });
  document.getElementById(tabId).classList.remove("hidden");
  document.getElementById("btn-" + tabId).classList.add("active");
}

function escapeHtml(str) {
  return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function copyShareLink() {
  navigator.clipboard.writeText(window.location.href);
  alert("Live TokenSathi URL copied! Share it with judges or open on your phone.");
}

function copyOptimizedPrompt() {
  const txt = document.getElementById("optAfterText").innerText;
  if (txt) {
    navigator.clipboard.writeText(txt);
    alert("Optimized prompt copied!");
  }
}

function updateSliderROI() {
  const daily = parseInt(document.getElementById("dailySlider").value, 10);
  document.getElementById("sliderLabel").innerText = daily.toLocaleString("en-IN");
  const savedTokensPerReq = Math.max(1, lastLegacyTokens - lastOptTokens);
  const monthlySaved = ((savedTokensPerReq * daily * 30) / 1_000_000) * 210;
  const annualSaved = monthlySaved * 12;

  document.getElementById("monthlySaved").innerText = "₹" + Math.round(monthlySaved).toLocaleString("en-IN");
  document.getElementById("annualSaved").innerText = "₹" + Math.round(annualSaved).toLocaleString("en-IN") + " / yr";
}

function loadPreset(lang) {
  document.getElementById("language").value = lang;
  document.getElementById("promptInput").value = PRESETS[lang];
  runAnalyze();
}

function loadBloatedSample() {
  document.getElementById("language").value = "English";
  document.getElementById("promptInput").value =
    "I would really appreciate it if you could please explain the concept of machine learning to me in a detailed manner and also give me some examples that would help me understand it better.";
  switchTab("tab-optimizer");
  runOptimizePrompt();
}

async function runAnalyze() {
  const text = document.getElementById("promptInput").value;
  const language = document.getElementById("language").value;
  const daily_requests = document.getElementById("dailySlider").value || 10000;

  try {
    const res = await fetch("/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, language, daily_requests })
    });
    const data = await res.json();
    if (data.error) return alert(data.error);

    lastLegacyTokens = data.tokens;
    lastOptTokens = data.optimized_tokens;
    updateSliderROI();

    document.getElementById("routerRoute").innerText = "ROUTER: " + data.router_decision.route;
    document.getElementById("routerConf").innerText = "Confidence: " + data.router_decision.confidence;
    document.getElementById("routerReason").innerText = data.router_decision.reason;
    document.getElementById("riskContainer").innerHTML = (data.router_decision.risks || [])
      .map(r => `<span class="risk-tag">⚠ ${escapeHtml(r)}</span>`).join("");

    document.getElementById("taxMultiplier").innerText = data.tax_multiplier + "x";
    document.getElementById("tokenCountCompare").innerText = `${data.tokens} → ${data.optimized_tokens}`;
    document.getElementById("savingsPct").innerText = data.savings_percent + "%";

    const rows = data.scanner_5lang || [];
    const maxTokens = Math.max(1, ...rows.map(r => r.tokens));
    document.getElementById("scannerTableBody").innerHTML = rows.map(row => {
      const pctWidth = Math.round((row.tokens / maxTokens) * 100);
      const isEn = row.language === "English";
      const barColor = isEn ? "var(--green)" : (row.multiplier >= 3.0 ? "var(--red)" : "var(--amber)");
      const diffLabel = isEn ? "Baseline (0%)" : `+${row.diff_from_english_pct}% (${row.multiplier}x)`;
      return `
        <tr>
          <td><strong>${FLAGS[row.language] || row.language}</strong></td>
          <td><strong>${row.tokens}</strong></td>
          <td style="color: ${barColor}; font-weight: 600;">${diffLabel}</td>
          <td><div class="bar-wrap"><div class="bar-fill" style="width: ${pctWidth}%; background: ${barColor};"></div></div></td>
        </tr>
      `;
    }).join("");

    document.getElementById("legacyTokensBox").innerHTML = data.token_pieces
      .map(p => `<span class="chip chip-bad">${escapeHtml(p)}</span>`).join("");
    document.getElementById("optTokensBox").innerHTML = data.optimized_token_pieces
      .map(p => `<span class="chip chip-good">${escapeHtml(p)}</span>`).join("");
  } catch (err) {
    console.error("Analyze failed:", err);
  }
}

async function runOptimizePrompt() {
  const text = document.getElementById("promptInput").value;
  const language = document.getElementById("language").value;

  try {
    const res = await fetch("/optimize_prompt", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, language })
    });
    const data = await res.json();
    if (data.error) return alert(data.error);

    document.getElementById("optBeforeTokens").innerText = data.before_tokens;
    document.getElementById("optBeforeText").innerText = data.original_prompt;
    document.getElementById("optAfterTokens").innerText = data.after_tokens;
    document.getElementById("optReductionPct").innerText = data.reduction_pct + "%";
    document.getElementById("optAfterText").innerText = data.optimized_prompt;
    document.getElementById("meaningPass").innerText = data.meaning_preserved;
  } catch (err) {
    console.error("Optimize prompt failed:", err);
  }
}

async function runRace() {
  const text = document.getElementById("promptInput").value;
  const language = document.getElementById("language").value;
  const hybrid_mode = document.getElementById("hybridMode").checked;

  await runAnalyze();
  const routeBadge = document.getElementById("raceRouteBadge");
  routeBadge.innerText = "Running Pipeline...";

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
    routeBadge.innerText = "Error running pipeline";
  }
}

async function runBatchBenchmark() {
  const langs = ["Kannada", "Hindi", "Telugu", "Bengali"];
  let totalSavedPct = 0;
  let totalQuality = 0;

  document.getElementById("batchScorecard").classList.remove("hidden");
  document.getElementById("batchMetrics").innerHTML = `<div class="stat"><div class="stat-label">Status</div><div class="stat-val">Testing 4 Languages...</div></div>`;

  for (const lang of langs) {
    const res = await fetch("/race", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: PRESETS[lang], language: lang, hybrid_mode: true })
    });
    const d = await res.json();
    totalSavedPct += d.tokensathi.token_reduction_pct;
    totalQuality += d.tokensathi.quality_score;
  }

  const avgSaved = (totalSavedPct / langs.length).toFixed(1);
  const avgQual = (totalQuality / langs.length).toFixed(1);

  document.getElementById("batchMetrics").innerHTML = `
    <div class="stat"><div class="stat-label">Languages Benchmarked</div><div class="stat-val">4 Scripts</div></div>
    <div class="stat"><div class="stat-label">Avg Token Reduction</div><div class="stat-val green">${avgSaved}%</div></div>
    <div class="stat"><div class="stat-label">Avg Semantic Quality</div><div class="stat-val green">${avgQual}%</div></div>
    <div class="stat"><div class="stat-label">Cache Entries Active</div><div class="stat-val" style="color:var(--accent)">4 Ready</div></div>
  `;
}

window.addEventListener("DOMContentLoaded", () => {
  runAnalyze();
  runOptimizePrompt();
});
