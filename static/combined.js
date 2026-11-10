"use strict";

const $ = id => document.getElementById(id);
const preview = $("preview");
const draft = $("draft");
const draftKey = "fsl-to-text-draft";
let state = null;
let busy = false;
let cameraRequested = false;
let revision = 0;
let eventId = null;

try { draft.value = localStorage.getItem(draftKey) || ""; } catch {}
function saveDraft() {
  try { localStorage.setItem(draftKey, draft.value); }
  catch { $("text-status").textContent = "This browser could not save the draft."; }
}
draft.addEventListener("input", saveDraft);

function status(message) {
  if ($("status").textContent !== message) $("status").textContent = message;
}

function applyState(next) {
  state = next;
  if (eventId !== null && cameraRequested) {
    const added = next.events.filter(event => event.id > eventId);
    for (const {mode, label} of added) {
      draft.value += (mode === "signs" && /\S$/.test(draft.value) ? " " : "") + label;
    }
    if (added.length) {
      saveDraft();
      $("text-status").textContent = `Added ${added.at(-1).label}.`;
    }
  }
  eventId = next.event_id;
}

function render() {
  if (!state) return;
  const live = state.camera === "live";
  const signs = state.mode === "signs";
  const countdown = state.phase === "countdown";
  const recording = state.phase === "recording";
  const processing = state.phase === "processing";
  const capturing = countdown || recording || processing;
  const result = live && state.label;
  const waitingForCamera = cameraRequested && state.camera === "off";
  $("camera-toggle").textContent = cameraRequested ? "Stop camera" : "Start camera";
  $("camera-toggle").disabled = busy || state.camera === "stopping";
  if (state.camera === "error" && cameraRequested) {
    cameraRequested = false;
    preview.removeAttribute("src");
    $("camera-toggle").textContent = "Start camera";
  }
  preview.hidden = !(live && cameraRequested);
  $("preview-empty").hidden = !preview.hidden;
  $("preview-empty").textContent = state.camera === "error" ? "Camera unavailable" :
    waitingForCamera || state.camera === "opening" ? "Opening camera…" : "Camera is off";

  document.querySelectorAll("[data-mode]").forEach(button => {
    button.setAttribute("aria-pressed", button.dataset.mode === state.mode);
    button.disabled = busy;
  });
  $("instructions").textContent = signs ?
    (state.sign_count === 20 ?
      "Use one of the 20 conversational signs, such as HELLO or THANK YOU. Sign once per recording." :
      "Keep both hands and shoulders in view. Sign once per recording.") :
    "Hold each letter steady until it appears in your text. Lower your hand briefly to repeat a letter.";
  $("mirror").hidden = signs;
  $("mirror").disabled = busy;
  $("mirror").setAttribute("aria-pressed", state.mirror);
  $("detection-toggle").textContent = state.detecting ? "Stop detection" : "Start detection";
  $("detection-toggle").hidden = signs;
  $("detection-toggle").disabled = busy || !live || !cameraRequested;
  $("record").hidden = !signs || capturing;
  $("record").textContent = ["result", "retry", "error"].includes(state.phase) ? "Record again" : "Record sign";
  $("record").disabled = busy || !live || !cameraRequested || state.worker_busy;
  $("cancel").hidden = !signs || !capturing;
  $("cancel").disabled = busy || !live || !cameraRequested;
  $("accept").hidden = !signs || !result;
  $("accept").disabled = busy || !cameraRequested;
  $("prediction").hidden = !result;
  $("prediction").textContent = result ? state.label : "";
  $("score").hidden = !result;
  $("score").textContent = result ?
    (signs ? `SVM margin ${state.confidence.toFixed(2)} · lead over the next closest sign` :
      `Letter confidence ${state.confidence}%`) : "";
  $("countdown").hidden = !(countdown || recording);
  $("countdown").textContent = countdown || recording ? `${Math.ceil(state.remaining)} s` : "";

  const messages = {
    countdown: state.message, recording: "Recording sign…", processing: "Recognizing sign…",
    no_hand: "No hand detected.", tracking: "Hold the letter steady.",
    error: state.message, retry: state.message,
    result: signs ? state.message : `Letter: ${state.label}.`,
    ready: signs ? "Press Record sign when ready." :
      state.detecting ? "Getting ready…" : "Press Start detection when ready.",
  };
  status(state.camera === "error" ? state.message : live ? messages[state.phase] :
    waitingForCamera || state.camera === "opening" ? "Opening camera…" : "Start the camera to begin.");
}

async function refresh() {
  const version = revision;
  if (busy) return;
  try {
    const reply = await fetch("/api/state", {cache: "no-store"});
    if (!reply.ok) throw new Error();
    const next = await reply.json();
    if (version === revision && !busy) {
      applyState(next);
      if (state.camera === "off" && cameraRequested && preview.hidden === false) {
        cameraRequested = false;
        preview.removeAttribute("src");
      }
      render();
    }
  } catch { status("Connection lost. Check that the local app is running."); }
}

async function act(action, payload = {}) {
  if (busy) return;
  revision += 1;
  busy = true;
  render();
  try {
    const response = await fetch(`/api/${action}`, {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload),
    });
    const next = await response.json();
    if (!response.ok) throw new Error(next.error || "Action failed.");
    applyState(next);
    busy = false;
    await refresh();
  } catch (error) {
    busy = false;
    render();
    status(error.message);
  }
}

$("camera-toggle").onclick = () => {
  if (cameraRequested) {
    cameraRequested = false;
    preview.removeAttribute("src");
    act("stop");
  } else {
    revision += 1;
    cameraRequested = true;
    preview.src = `/video_feed?${Date.now()}`;
    render();
  }
};
preview.addEventListener("error", async () => {
  if (!cameraRequested) return;
  cameraRequested = false;
  preview.removeAttribute("src");
  await refresh();
  if (state && state.camera !== "error") status("Preview unavailable. Close other camera tabs and try again.");
});
document.querySelectorAll("[data-mode]").forEach(button => {
  button.onclick = () => act("mode", {mode: button.dataset.mode});
});
$("mirror").onclick = () => act("mirror");
$("detection-toggle").onclick = () => act("detection", {enabled: !state.detecting});
$("record").onclick = () => act("record");
$("cancel").onclick = () => act("cancel");
$("accept").onclick = () => act("accept", {generation: state.generation});
$("space").onclick = () => { draft.value += " "; saveDraft(); };
$("backspace").onclick = () => { draft.value = Array.from(draft.value).slice(0, -1).join(""); saveDraft(); };
$("clear").onclick = () => { draft.value = ""; saveDraft(); $("text-status").textContent = "Text cleared."; };
$("copy").onclick = async () => {
  if (!draft.value) { $("text-status").textContent = "There is no text to copy."; return; }
  try {
    await navigator.clipboard.writeText(draft.value);
    $("text-status").textContent = "Copied.";
  } catch {
    draft.select();
    $("text-status").textContent = "Text selected. Press Ctrl+C to copy.";
  }
};
window.addEventListener("pagehide", () => { preview.removeAttribute("src"); });
async function poll() { await refresh(); setTimeout(poll, 250); }
poll();
