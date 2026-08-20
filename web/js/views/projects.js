/* 项目视图：项目卡片 + 分集列表 + 流水线阶段 + 生成/重跑操作。 */
import { api } from "../api.js";
import { state, bus } from "../state.js";
import { el, toast, badge, pipelineBar } from "../ui.js";

async function generate(eid, stage, force = false) {
  try {
    await api.generate(eid, stage, force);
    toast(`已提交：${stage === "all" ? "整集流水线" : stage}${force ? "（强制重跑）" : ""}`);
    bus.emit("refresh-projects");
  } catch (e) { toast(`启动失败：${e.message}`, true); }
}

function episodeRow(pid, ep, stagesMeta) {
  const stageStates = ep.stages || {};
  const last = ep.last_task;
  const card = el("div.card", {},
    el("div.row.spread", {},
      el("div.row", {},
        el("strong", {}, ep.title || `第 ${ep.idx} 集`),
        badge(ep.status)),
      el("div.row", {},
        el("button.ghost.small", { onclick: () => { state.setEpisode(ep.idx); bus.emit("nav", "episode"); } }, "详情"),
        el("button.ghost.small", { onclick: () => generate(ep.id, "all") }, "生成/续跑整集"),
        el("button.ghost.small", { onclick: () => generate(ep.id, "all", true) }, "全部重跑"),
        ep.status === "generating"
          ? el("button.danger.small", { onclick: async () => {
              try {
                const tasks = await api.tasks({ episode_id: ep.id, status: "running" });
                if (!tasks.length) throw new Error("没有运行中的任务");
                await api.cancelTask(tasks[0].id);
                toast("已请求取消");
              } catch (e) { toast(e.message, true); }
            } }, "取消")
          : null)),
    pipelineBar(stageStates, stagesMeta, (stage) => {
      const st = stageStates[stage];
      if (st === "ready") generate(ep.id, stage, true);  // 已有产物 → 强制重跑该阶段
      else generate(ep.id, stage);                        // 缺产物 → 从该阶段续跑
    }),
    ep.summary ? el("div.muted.small", {}, ep.summary) : null,
    last && last.status === "failed"
      ? el("div.small", { style: "color:var(--err)" },
          `失败：${last.error || "未知错误"}　`,
          el("a", { href: "#", onclick: async (ev) => {
            ev.preventDefault();
            try { await api.retryTask(last.id); toast("已重试"); bus.emit("refresh-projects"); }
            catch (e) { toast(e.message, true); }
          } }, "手工重试"))
      : null,
  );
  return card;
}

async function load(root) {
  root.innerHTML = "";
  const stagesMeta = state.stages.length ? state.stages : await api.stages();
  const projects = state.projects.length ? state.projects : await api.projects();

  if (!projects.length) {
    root.append(el("div.card", {},
      "还没有项目。去「对话」页说一句「创建一部 3 集的都市爱情短剧，名字叫《晚风》」即可。",
      el("div.row", { style: "margin-top:10px" },
        el("button.primary", { onclick: () => bus.emit("nav", "chat") }, "去对话创建"))));
    return;
  }

  for (const p of projects) {
    const detail = await api.project(p.id).catch(() => null);
    if (!detail) continue;
    const projCard = el("div.card", {},
      el("div.row.spread", {},
        el("h2", { style: "margin:0" }, `《${detail.project.name}》`),
        el("div.row", {},
          badge(detail.project.status || ""),
          el("span.muted.small", {}, detail.project.genre || ""),
          el("button.danger.small", {
            onclick: async () => {
              if (!confirm(`删除《${detail.project.name}》及全部产物？不可恢复。`)) return;
              try { await api.deleteProject(p.id); toast("已删除"); bus.emit("refresh-projects"); }
              catch (e) { toast(e.message, true); }
            } }, "删除项目"))),
      el("div.muted.small", {}, detail.project.premise || ""),
      el("div.row", { style: "margin-top:8px" },
        el("button.ghost.small", {
          onclick: async () => {
            try {
              const eps = detail.episodes || [];
              const next = eps.length + 1;
              const ep = await api.createEpisode(p.id, { title: `第${next}集` });
              await generate(ep.id, "all");
              state.setEpisode(next);
            } catch (e) { toast(e.message, true); }
          } }, "＋ 新的一集"),
        el("span.muted.small", {},
          `已 ${detail.episodes?.length || 0} / 计划 ${detail.project.episodes_planned || "?"} 集`)),
    );
    root.append(projCard);
    for (const ep of detail.episodes || []) root.append(episodeRow(p.id, ep, stagesMeta));
  }
}

export function render(root) {
  load(root);
  bus.on("refresh-projects", () => { if (root.isConnected) load(root); });
}
