/* Recall — voice dictation using the browser's built-in Web Speech API.
   Free, no API key. Best in Chrome/Edge; auto-hides where unsupported.

   Usage: put a button with data-voice-target="#fieldId" next to any input or
   textarea. Language comes from <select class="voice-lang"> (persisted). */
(function () {
  var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  var LANG_KEY = "recall-voicelang";

  function lang() { return localStorage.getItem(LANG_KEY) || "en-US"; }

  function attach(btn) {
    var target = document.querySelector(btn.getAttribute("data-voice-target"));
    if (!target) return;
    if (!SR) { btn.style.display = "none"; return; }   // unsupported browser

    var rec = new SR();
    rec.interimResults = true;
    rec.continuous = true;
    var listening = false, finalText = "";

    btn.addEventListener("click", function () {
      if (listening) { rec.stop(); return; }
      rec.lang = lang();
      finalText = target.value ? target.value.replace(/\s+$/, "") + " " : "";
      try { rec.start(); } catch (e) { /* already started */ }
    });

    rec.onstart = function () { listening = true; btn.classList.add("listening"); };
    rec.onend = function () { listening = false; btn.classList.remove("listening"); };
    rec.onerror = function (e) {
      listening = false; btn.classList.remove("listening");
      var msg = e.error === "not-allowed"
        ? "Microphone blocked — allow mic access in the browser."
        : e.error === "no-speech" ? "Didn't catch that — try again."
        : "Voice error: " + e.error;
      if (window.recallToast) window.recallToast(msg);
    };
    rec.onresult = function (e) {
      var interim = "";
      for (var i = e.resultIndex; i < e.results.length; i++) {
        var t = e.results[i][0].transcript;
        if (e.results[i].isFinal) finalText += t + " "; else interim += t;
      }
      target.value = finalText + interim;
      target.dispatchEvent(new Event("input"));   // let filters/handlers react
    };
  }

  document.addEventListener("DOMContentLoaded", function () {
    // sync any language pickers to the stored choice
    document.querySelectorAll(".voice-lang").forEach(function (sel) {
      if (!SR) { sel.style.display = "none"; return; }
      sel.value = lang();
      sel.addEventListener("change", function () {
        localStorage.setItem(LANG_KEY, sel.value);
      });
    });
    document.querySelectorAll("[data-voice-target]").forEach(attach);
  });
})();
