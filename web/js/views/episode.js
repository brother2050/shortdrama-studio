/* 分集详情：剧本 / 镜头墙（关键帧+片段+配音）/ 字幕 / 成片预览。 */
import { api, mediaUrl } from "../api.js";
import { state, bus } from "../state.js";
import { el, toast, badge, pipelineBar, escapeHtml } from "../ui.js";

function scriptHtml(script) {
  if (!script || !script.scenes?.length) return "<p class='muted'>剧本尚未生成。</p>";
  let html = `<p><strong>${escapeHtml(script.title || "")}</strong>　`
    + `<span class="muted">${escapeHtml(script.summary || "")}</span></p>`;
  for (const sc of script.scenes) {
    html += `<h3>${escapeHtml(sc.name)}　<span class="muted small">${escapeHtml(sc.mood || "")}</span></h3>`
      + `<p class="small">${escapeHtml(sc.location || "")}${sc.action ? " ｜ " + escapeHtml(sc.action) : ""}</p>`;
    for (const ln of sc.lines || []) {
      html += `<p>【${escapeHtml(ln.speaker)}】${escapeHtml(ln.text)}`
        + ` <span class="muted small">（${escapeHtml(ln.emotion || "")}）</span></p>`;
    }
  }
  return html;
}

function shotCard(pid, shot) {
  const kf = mediaUrl(pid, shot.keyframe);
  const clip = mediaUrl(pid, shot.clip);
  const vo = mediaUrl(pid, shot.vo);
  const lines = (shot.lines || []).map((l) => `【${l.speaker}】${l.text}`).join("　");
  return el("div.card.shot-card", {},
    el("div.row.spread", { style: "margin-bottom:8px" },
      el("strong", {}, `镜头 ${String(shot.idx).padStart(2, "0")}`),
      el("span.muted.small", {},
        `${shot.scene || ""} · ${shot.camera || ""} · ${shot.motion || ""} · `
        + `${(shot.vo_duration || shot.duration_hint || 0).toFixed(1)}s`)),
    clip
      ? el("video", { controls: true, preload: "none", src: clip })
      : kf ? el("img", { src: kf, alt: `镜头${shot.idx} 关键帧` })
           : el("div.noimg", {}, "关键帧未生成"),
    el("div.small", { style: "margin-top:8px" }, shot.description || ""),
    lines ? el("div.small.muted", { style: "margin-top:6px" }, lines) : null,
    vo ? el("audio", { controls: true, preload: "none", src: vo, style: "width:100%;margin-top:6px" }) : null,
  );
}

async function load(root) {
  root.innerHTML = "";
  if (!state.projectId) {
    root.append(el("div.card", {}, "先在「对话」页创建或选择一个项目。"));
    return;
  }
  const pid = state.projectId;
  const project = (await api.project(pid)).project;
  const detail = await api.episode(pid, state.episodeIdx).catch((e) => null);
  if (!detail) {
    root.append(el("div.card", {},
      `第 ${state.episodeIdx} 集不存在。`,
      el("div.row", { style: "margin-top:8px" },
        el("button.primary", {
          onclick: async () => {
            const ep = await api.createEpisode(pid, { title: `第${state.episodeIdx}集` });
            await api.generate(ep.id, "all");
            toast("已启动流水线");
            bus.emit("refresh-projects");
          } }, "创建并生成这一集"))));
    return;
  }

  const stagesMeta = state.stages.length ? state.stages : await api.stages();
  const ep = detail.episode;
  const art = detail.artifacts || {};

  root.append(el("div.card", {},
    el("div.row.spread", {},
      el("h2", { style: "margin:0" }, `《${project.name}》第 ${ep.idx} 集 · ${ep.title || ""}`),
      el("div.row", {},
        badge(ep.status),
        el("button.ghost.small", { onclick: () => bus.emit("refresh-projects") }, "刷新"),
        el("button.ghost.small", {
          onclick: async () => {
            try { await api.generate(ep.id, "all"); toast("已启动/续跑整集"); }
            catch (e) { toast(e.message, true); }
          } }, "生成/续跑整集"))),
    pipelineBar(detail.stages, stagesMeta),
    ep.summary ? el("div.muted.small", {}, ep.summary) : null,
    el("div.row", { style: "margin-top:10px" },
      art.episode_mp4 ? el("a", { href: mediaUrl(pid, art.episode_mp4), target: "_blank" }, "⬇ 成片 MP4") : null,
      art.episode_srt ? el("a", { href: mediaUrl(pid, art.episode_srt), target: "_blank" }, "⬇ 字幕 SRT") : null,
      art.script_md ? el("a", { href: mediaUrl(pid, art.script_md), target: "_blank" }, "⬇ 剧本 MD") : null,
      art.worldview_md ? el("a", { href: mediaUrl(pid, art.worldview_md), target: "_blank" }, "⬇ 世界观 MD") : null)));

  if (art.episode_mp4) {
    root.append(el("div.card", {},
      el("h3", {}, "成片预览"),
      el("video", { controls: true, style: "width:100%", src: mediaUrl(pid, art.episode_mp4) })));
  }

  const failed = (detail.tasks || []).filter((t) => t.status === "failed");
  if (failed.length) {
    root.append(el("div.card", {},
      el("h3", {}, "失败任务（手工重试，不限次数）"),
      el("table", {},
        el("tr", {}, el("th", {}, "阶段"), el("th", {}, "错误"), el("th", {}, "操作")),
        ...failed.map((t) => el("tr", {},
          el("td", {}, t.stage), el("td", {}, t.error || ""),
          el("td", {}, el("button.ghost.small", {
            onclick: async () => {
              try { await api.retryTask(t.id); toast("已重试"); bus.emit("refresh-projects"); }
              catch (e) { toast(e.message, true); }
            } }, "重试")))))));
  }

  root.append(el("div.card", {}, el("h3", {}, "剧本"), el("div", { html: scriptHtml(detail.script) })));

  if (detail.shots?.length) {
    root.append(el("h3", {}, `分镜（${detail.shots.length} 个镜头）`));
    root.append(el("div.grid.cols-3", {}, ...detail.shots.map((s) => shotCard(pid, s))));
  }
}

export function render(root) {
  load(root);
  bus.on("refresh-projects", () => { if (root.isConnected) load(root); });
  bus.on("episode", () => { if (root.isConnected) load(root); });
}
