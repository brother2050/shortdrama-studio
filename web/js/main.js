/* 应用入口：路由（标签页）/ 项目切换器 / SSE 实时联动。 */
import { api } from "./api.js";
import { state, bus } from "./state.js";
import { el, toast } from "./ui.js";
import * as chat from "./views/chat.js";
import * as projects from "./views/projects.js";
import * as episode from "./views/episode.js";
import * as tasks from "./views/tasks.js";
import * as settings from "./views/settings.js";
import * as system from "./views/system.js";

const VIEWS = { chat, projects, episode, tasks, settings, system };
let currentView = localStorage.getItem("sd.view") || "chat";
let refreshTimer = null;

function setTab(name) {
  if (!VIEWS[name]) name = "chat";
  currentView = name;
  localStorage.setItem("sd.view", name);
  document.querySelectorAll(".tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === name));
  const root = document.getElementById("view");
  root.innerHTML = "";
  VIEWS[name].render(root);
}

function renderProjectSelect() {
  const sel = document.getElementById("projectSelect");
  sel.innerHTML = "";
  if (!state.projects.length) {
    sel.append(el("option", { value: "" }, "（无项目）"));
    sel.disabled = true;
    return;
  }
  sel.disabled = false;
  for (const p of state.projects) {
    sel.append(el("option", { value: p.id, selected: p.id === state.projectId ? "" : null },
      `《${p.name}》`));
  }
  sel.value = state.projectId;
}

async function refreshProjects(silent = true) {
  try {
    const list = await api.projects();
    state.setProjects(list);
    renderProjectSelect();
    if (!silent) toast(`已刷新 ${list.length} 个项目`);
  } catch (e) {
    if (!silent) toast(`项目加载失败：${e.message}`, true);
  }
}

/* SSE：任务状态变化 → 节流刷新项目/任务视图；chat 事件 → 刷新对话。 */
function connectSSE() {
  const es = new EventSource("/api/events");
  es.onopen = () => { /* hello 后端已发 */ };
  es.onerror = () => { /* EventSource 自动重连 */ };
  for (const type of ["task", "progress", "chat", "episode", "project", "pipeline_error"]) {
    es.addEventListener(type, (ev) => {
      let payload = {};
      try { payload = JSON.parse(ev.data); } catch { /* ignore */ }
      payload.type = type;
      bus.emit("sse", payload);
      if (type === "chat") bus.emit("chat-updated");
      if (type === "task" || type === "episode" || type === "project") {
        clearTimeout(refreshTimer);
        refreshTimer = setTimeout(() => bus.emit("refresh-projects"), 600);
      }
    });
  }
}

async function init() {
  const v = await api.version().catch(() => null);
  if (v) document.getElementById("version").textContent = `v${v.version}`;

  document.getElementById("tabs").addEventListener("click", (ev) => {
    const btn = ev.target.closest(".tab");
    if (btn) setTab(btn.dataset.view);
  });

  const sel = document.getElementById("projectSelect");
  sel.addEventListener("change", () => {
    state.setProject(sel.value || null);
    if (currentView === "projects" || currentView === "episode") setTab(currentView);
  });

  bus.on("nav", (name) => setTab(name));
  bus.on("projects", renderProjectSelect);

  await refreshProjects();
  try { state.stages = await api.stages(); } catch { /* 忽略 */ }
  connectSSE();
  setTab(currentView);
}

init();
