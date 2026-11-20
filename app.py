// 1. Auto-detect script on typing/pasting and sync dropdown
function detectScriptLanguage(text) {
  if (/[\u0980-\u09FF]/.test(text)) return "Bengali";
  if (/[\u0C80-\u0CFF]/.test(text)) return "Kannada";
  if (/[\u0900-\u097F]/.test(text)) return "Hindi";
  if (/[\u0C00-\u0C7F]/.test(text)) return "Telugu";
  return "English";
}

document.addEventListener("DOMContentLoaded", () => {
  const promptInput = document.querySelector("textarea");
  const langSelect = document.querySelector("select");

  if (promptInput && langSelect) {
    promptInput.addEventListener("input", () => {
      const detected = detectScriptLanguage(promptInput.value);
      for (const option of langSelect.options) {
        if (option.text.toLowerCase().includes(detected.toLowerCase())) {
          langSelect.value = option.value;
          break;
        }
      }
    });
  }
});

// 2. Call the zero-cost Python rule engine
async function optimizeWithRuleEngine(promptText) {
  const response = await fetch("/optimize", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt: promptText })
  });
  return await response.json();
}
