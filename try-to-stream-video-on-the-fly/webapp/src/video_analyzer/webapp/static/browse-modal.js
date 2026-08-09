// A directory-browsing modal for picking a video file from anywhere on the filesystem.
// Exposes window.openBrowseModal(onSelect) — source.js calls it, and onSelect(path) is invoked
// with the absolute path of whichever video file the user clicks.

const overlay = document.getElementById("browse-modal");
const pathInput = document.getElementById("browse-path");
const upButton = document.getElementById("browse-up");
const closeButton = document.getElementById("browse-close");
const browseErrorBox = document.getElementById("browse-error");
const entriesList = document.getElementById("browse-entries");

let onSelectCallback = null;
let currentParent = null;

function showError(message) {
  browseErrorBox.textContent = message;
  browseErrorBox.hidden = false;
}

function renderEntries(entries) {
  entriesList.innerHTML = "";
  for (const entry of entries) {
    const li = document.createElement("li");
    li.className = entry.is_dir ? "modal-entry modal-entry-dir" : "modal-entry modal-entry-file";
    li.textContent = (entry.is_dir ? "📁 " : "🎬 ") + entry.name;
    li.addEventListener("click", () => {
      if (entry.is_dir) {
        load(entry.path);
      } else {
        onSelectCallback(entry.path);
        closeModal();
      }
    });
    entriesList.appendChild(li);
  }
  if (entries.length === 0) {
    const li = document.createElement("li");
    li.className = "modal-entry modal-entry-empty";
    li.textContent = "(no folders or video files here)";
    entriesList.appendChild(li);
  }
}

async function load(path) {
  browseErrorBox.hidden = true;
  try {
    const query = path ? `?path=${encodeURIComponent(path)}` : "";
    const res = await fetch(`/api/browse${query}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    pathInput.value = data.path;
    currentParent = data.parent;
    upButton.disabled = currentParent === null;
    renderEntries(data.entries);
  } catch (err) {
    showError(err.message);
  }
}

function closeModal() {
  overlay.hidden = true;
  onSelectCallback = null;
}

upButton.addEventListener("click", () => {
  if (currentParent !== null) load(currentParent);
});

closeButton.addEventListener("click", closeModal);

overlay.addEventListener("click", (e) => {
  if (e.target === overlay) closeModal(); // click on the backdrop, not the modal itself
});

pathInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    load(pathInput.value.trim());
  }
});

window.openBrowseModal = function openBrowseModal(onSelect) {
  onSelectCallback = onSelect;
  overlay.hidden = false;
  load(pathInput.value.trim() || null);
};
