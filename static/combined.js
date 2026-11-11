"use strict";

const $ = id => document.getElementById(id);
const preview = $("preview");
const draft = $("draft");
const draftKey = "fsl-to-text-draft";
let state = null;
let busy = false;
let cameraRequested = false;
let autoStartPending = true;
let cameraIssue = "";
let revision = 0;
let eventId = null;

try { draft.value = localStorage.getItem(draftKey) || ""; } catch {}
function saveDraft() {
  try { localStorage.setItem(draftKey, draft.value); }
  catch { $("text-status").textContent = "This browser could not save the draft."; }
}
draft.addEventListener("input", saveDraft);

function status(message, announcement = message) {
  if ($("status").textContent !== announcement) $("status").textContent = announcement;
  if ($("feedback").textContent !== message) $("feedback").textContent = message;
}

function applyState(next) {
  const previousCamera = state?.camera;
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
  if (cameraRequested && next.camera === "error") {
    releasePreview(next.message);
  } else if (cameraRequested && next.camera === "off" &&
             (previousCamera === "opening" || previousCamera === "live")) {
    releasePreview("Camera stopped. Try starting it again.");
  }
}

function releasePreview(message = "") {
  cameraRequested = false;
  cameraIssue = message;
  preview.removeAttribute("src");
}

function startCamera() {
  if (state.camera !== "off" && state.camera !== "error") {
    cameraIssue = "Camera in use. Close the other camera tab and try again.";
    render();
    return;
  }
  revision += 1;
  cameraIssue = "";
  cameraRequested = true;
  preview.src = `/video_feed?${Date.now()}`;
  render();
}

function setAvailable(button, available) {
  button.setAttribute("aria-disabled", !available);
}

function render() {
  if (!state) return;
  const focused = document.activeElement;
  const live = state.camera === "live" && cameraRequested;
  const signs = state.mode === "signs";
  const countdown = state.phase === "countdown";
  const recording = state.phase === "recording";
  const processing = state.phase === "processing";
  const capturing = countdown || recording || processing;
  const result = live && state.phase === "result" && Boolean(state.label);
  const signResult = signs && result;
  const hasScore = result && Number.isFinite(state.confidence);
  $("camera-toggle").textContent = cameraRequested ? "Stop camera" : "Start camera";
  setAvailable($("camera-toggle"), !busy && state.camera !== "stopping");
  preview.hidden = !live;

  document.querySelectorAll("[data-mode]").forEach(button => {
    button.setAttribute("aria-pressed", button.dataset.mode === state.mode);
    setAvailable(button, !busy);
  });
  $("mirror").hidden = signs;
  setAvailable($("mirror"), !busy);
  $("mirror").setAttribute("aria-pressed", state.mirror);
  $("detection-toggle").textContent = state.detecting ? "Stop detection" : "Start detection";
  $("detection-toggle").hidden = signs;
  setAvailable($("detection-toggle"), !busy && live);
  $("record").hidden = !signs || capturing;
  $("record").textContent = ["result", "retry", "error"].includes(state.phase) ? "Record again" : "Record sign";
  setAvailable($("record"), !busy && live && !state.worker_busy);
  $("cancel").hidden = !signs || !capturing;
  setAvailable($("cancel"), !busy && live);
  $("accept").hidden = !signResult;
  setAvailable($("accept"), !busy && live);
  $("result-empty").hidden = result;
  $("prediction").hidden = !result;
  $("prediction").textContent = result ? state.label : "";
  $("score").hidden = !hasScore;
  $("score").textContent = hasScore
    ? (signs ? `Sign score gap ${state.confidence.toFixed(2)}` : `Letter confidence ${state.confidence}%`)
    : "";
  $("review").hidden = !signResult;
  $("review").textContent = signResult ? state.message : "";
  $("countdown").hidden = !(live && (countdown || recording));
  $("countdown").textContent = $("countdown").hidden ? "" : `${Math.ceil(state.remaining)} s`;

  const messages = {
    countdown: state.message, recording: "Recording sign…", processing: "Recognizing sign…",
    no_hand: "No hand detected.", tracking: "Hold the letter steady.",
    error: state.message, retry: state.message,
    result: "",
    ready: state.detecting ? "Getting ready…" : "",
  };
  const feedback = live ? messages[state.phase] :
    cameraRequested ? "Opening camera…" :
    state.camera === "stopping" ? "Stopping camera…" :
    state.camera === "error" ? state.message : cameraIssue || "Camera is off.";
  const announcement = signResult ? `Predicted sign ${state.label}. ${state.message}` :
    result ? `Predicted letter ${state.label}.` : feedback;
  status(feedback, announcement);

  if (focused?.hidden && focused.closest(".camera-controls")) {
    [$("cancel"), $("accept"), $("record"), $("detection-toggle"), $("camera-toggle")]
      .find(button => !button.hidden).focus();
  }
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
      if (autoStartPending) {
        autoStartPending = false;
        startCamera();
      } else {
        render();
      }
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
    render();
    await refresh();
  } catch (error) {
    busy = false;
    render();
    status(error.message);
  }
}

function onControl(button, action) {
  button.onclick = () => {
    if (button.getAttribute("aria-disabled") !== "true") action();
  };
}

onControl($("camera-toggle"), () => {
  if (cameraRequested) {
    releasePreview();
    act("stop");
  } else {
    startCamera();
  }
});
preview.addEventListener("error", () => {
  if (!cameraRequested) return;
  revision += 1;
  releasePreview("Preview unavailable. Try starting the camera again.");
  render();
  refresh();
});
document.querySelectorAll("[data-mode]").forEach(button => {
  onControl(button, () => act("mode", {mode: button.dataset.mode}));
});
onControl($("mirror"), () => act("mirror"));
onControl($("detection-toggle"), () => act("detection", {enabled: !state.detecting}));
onControl($("record"), () => act("record"));
onControl($("cancel"), () => act("cancel"));
onControl($("accept"), () => act("accept", {generation: state.generation}));
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
window.addEventListener("pagehide", () => releasePreview());
async function poll() { await refresh(); setTimeout(poll, 250); }
poll();
