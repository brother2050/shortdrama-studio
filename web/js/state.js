/* 极简响应式状态 + 事件订阅（跨视图通信）。 */

class Emitter {
  constructor() { this.map = new Map(); }
  on(type, fn) {
    if (!this.map.has(type)) this.map.set(type, new Set());
    this.map.get(type).add(fn);
    return () => this.map.get(type)?.delete(fn);
  }
  emit(type, payload) { this.map.get(type)?.forEach((fn) => fn(payload)); }
}

export const bus = new Emitter();

export const state = {
  projects: [],
  projectId: localStorage.getItem("sd.projectId") || null,
  episodeIdx: Number(localStorage.getItem("sd.episodeIdx") || 1),
  stages: [],           // [{stage,label}]
  lastEvent: null,
  setProjects(list) {
    this.projects = list || [];
    if (this.projects.length && !this.projects.some((p) => p.id === this.projectId)) {
      this.projectId = this.projects[0].id;
      localStorage.setItem("sd.projectId", this.projectId);
    }
    if (!this.projects.length) this.projectId = null;
    bus.emit("projects", this.projects);
  },
  setProject(pid) {
    this.projectId = pid;
    localStorage.setItem("sd.projectId", pid || "");
    bus.emit("projects", this.projects);
  },
  setEpisode(idx) {
    this.episodeIdx = idx;
    localStorage.setItem("sd.episodeIdx", String(idx));
    bus.emit("episode", idx);
  },
};
