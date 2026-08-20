/* 任务中心：全部任务 / 过滤 / 手工重试 / 取消。 */
import { api } from "../api.js";
import { bus } from "../state.js";
import { el, toast, badge, fmtTime } from "../ui.js";

let filterStatus = "";

async function load(root) {
  const params = { limit: 300 };
  if (filterStatus) params.status = filterStatus;
  const tasks = await api.tasks(params).catch(() => []);
  root.innerHTML = "";
  root.append(
    el("div.card", {},
      el("div.row.spread", {},
        el("h2", { style: "margin:0" }, `任务中心（${tasks.length}）`),
        el("div.row", {},
          ...["", "running", "pending", "failed", "succeeded", "canceled"].map((s) =>
            el("button." + (filterStatus === s ? "primary" : "ghost") + ".small", {
              onclick: () => { filterStatus = s; load(root); } },
              s || "全部"))),
        el("button.ghost.small", { onclick: () => load(root) }, "刷新"))),
    el("table.card", {},
      el("tr", {},
        el("th", {}, "阶段"), el("th", {}, "状态"), el("th", {}, "提交时间"),
        el("th", {}, "错误 / 产物"), el("th", {}, "操作")),
      ...tasks.map((t) => el("tr", {},
        el("td", {}, t.stage),
        el("td", {}, badge(t.status)),
        el("td", {}, fmtTime(t.created_at)),
        el("td", { style: "max-width:420px" },
          t.error ? el("span", { style: "color:var(--err)" }, t.error)
                  : (t.artifacts || []).map((a) => String(a).split("/").pop()).join(", ")),
        el("td", {},
          el("div.row", {},
            ["failed", "canceled", "succeeded"].includes(t.status)
              ? el("button.ghost.small", {
                  onclick: async () => {
                    try { await api.retryTask(t.id); toast("已重试"); load(root); }
                    catch (e) { toast(e.message, true); }
                  } }, "重试") : null,
            ["pending", "running"].includes(t.status)
              ? el("button.danger.small", {
                  onclick: async () => {
                    try { await api.cancelTask(t.id); toast("已请求取消"); }
                    catch (e) { toast(e.message, true); }
                  } }, "取消") : null))))));
}

export function render(root) {
  load(root);
  bus.on("refresh-projects", () => { if (root.isConnected) load(root); });
}
