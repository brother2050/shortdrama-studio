/* API 封装：统一错误处理（错误消息来自后端中文 detail）。 */

async function request(method, url, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(url, opts);
  if (res.status === 204) return null;
  let data = null;
  try { data = await res.json(); } catch { /* 非 JSON */ }
  if (!res.ok) {
    const detail = data && (data.detail?.toString?.() || JSON.stringify(data));
    throw new Error(detail || `${method} ${url} 失败（HTTP ${res.status}）`);
  }
  return data;
}

export const api = {
  version: () => request("GET", "/api/version"),
  /* 对话 */
  chat: (message, projectId) =>
    request("POST", "/api/chat", { message, project_id: projectId }),
  chatHistory: (pid, limit = 200) =>
    request("GET", `/api/projects/${pid}/chat?limit=${limit}`),
  /* 项目与分集 */
  projects: () => request("GET", "/api/projects"),
  project: (pid) => request("GET", `/api/projects/${pid}`),
  createProject: (body) => request("POST", "/api/projects", body),
  deleteProject: (pid) => request("DELETE", `/api/projects/${pid}`),
  patchProject: (pid, fields) => request("PATCH", `/api/projects/${pid}`, fields),
  episode: (pid, idx) => request("GET", `/api/projects/${pid}/episodes/${idx}`),
  createEpisode: (pid, body) => request("POST", `/api/projects/${pid}/episodes`, body),
  generate: (eid, stage = "all", force = false) =>
    request("POST", `/api/episodes/${eid}/generate`, { stage, force }),
  /* 任务 */
  tasks: (params = {}) => {
    const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v != null && v !== ""));
    return request("GET", `/api/tasks?${q}`);
  },
  stages: () => request("GET", "/api/tasks/stages"),
  retryTask: (tid) => request("POST", `/api/tasks/${tid}/retry`),
  cancelTask: (tid) => request("POST", `/api/tasks/${tid}/cancel`),
  /* 系统 */
  health: () => request("GET", "/api/system/health"),
  backends: () => request("GET", "/api/system/backends"),
  modelsCatalog: () => request("GET", "/api/system/models"),
  vramStatus: () => request("GET", "/api/system/vram"),
  releaseVRAM: () => request("POST", "/api/system/vram/release"),
  settings: () => request("GET", "/api/settings"),
  saveSettings: (settings) => request("PUT", "/api/settings", { settings }),
};

/** 绝对产物路径 → 受控媒体 URL（提取 projects/{pid}/ 之后的部分）。 */
export function mediaUrl(pid, absPath) {
  if (!absPath) return null;
  const m = String(absPath).match(/projects[/\\][^/\\]+[/\\](.+)$/);
  if (!m) return null;
  return `/api/projects/${pid}/media/${m[1].split("\\").join("/")}`;
}
