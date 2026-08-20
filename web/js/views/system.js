/* 系统视图：能力健康矩阵 / 磁盘 / 版本 / 显存管理 / 实时事件日志。 */
import { api } from "../api.js";
import { bus } from "../state.js";
import { el, fmtTime, toast } from "../ui.js";

const CAP_NAMES = { llm: "剧本 LLM", tts: "语音合成", image: "关键帧图像",
                    video: "镜头视频", asr: "语音识别" };

async function load(root) {
  const health = await api.health().catch(() => null);
  if (!health) { root.append(el("div.card", {}, "健康检查失败")); return; }
  root.innerHTML = "";

  root.append(el("h2", {}, "系统状态"));
  root.append(el("div.card", {},
    el("table.matrix", {},
      el("tr", {}, el("th", {}, "能力"), el("th", {}, "配置"), el("th", {}, "实际生效"),
        el("th", {}, "可用后端")),
      ...Object.entries(health.capabilities || {}).map(([cap, info]) =>
        el("tr", {},
          el("td", {}, CAP_NAMES[cap] || cap),
          el("td", {}, info.configured),
          el("td", {}, info.active),
          el("td", {}, (info.backends || []).map((b) => b.name).join(", ")))))));

  const rows = [];
  for (const [k, v] of Object.entries(health)) {
    if (k === "capabilities") continue;
    if (k === "vram" && v) {
      rows.push(el("tr", {},
        el("td", {}, "GPU/VRAM"),
        el("td.mono", {}, v.available
          ? `${v.device} | 总:${v.total_gb}GB 已用:${v.used_gb}GB 可用:${v.free_gb}GB`
          : (v.device || "无 CUDA"))));
      if (v.available && v.loaded_models?.length) {
        rows.push(el("tr", {},
          el("td", {}, "已加载模型"),
          el("td.mono", {}, v.loaded_models.join(", "))));
      }
      continue;
    }
    rows.push(el("tr", {}, el("td", {}, k), el("td.mono", {}, String(v))));
  }
  root.append(el("div.card", {},
    el("h3", {}, "环境"), el("table", {}, ...rows)));

  // 显存管理卡片
  if (health.vram) {
    const vr = health.vram;
    const vramCard = el("div.card", {},
      el("div.row.spread", {},
        el("h3", { style: "margin:0" }, "显存管理"),
        vr.available
          ? el("button.ghost.small", {
              onclick: async () => {
                try {
                  await api.releaseVRAM();
                  toast("已释放所有模型显存");
                  load(root);
                } catch (e) { toast(e.message, true); }
              } }, "释放显存")
          : null),
      vr.available
        ? el("div", {},
            el("div.row", { style: "margin:6px 0" },
              el("span.badge", { style: "color:var(--ok);border-color:var(--ok)" }, "GPU 可用"),
              el("span.muted.small", {}, vr.device),
              el("span.muted.small", {}, `CUDA ${vr.cuda_version || ""}`)),
            el("div.row", { style: "gap:20px;margin:8px 0" },
              el("div", {}, el("div.muted.small", {}, "总显存"),
                el("strong", {}, `${vr.total_gb} GB`)),
              el("div", {}, el("div.muted.small", {}, "已用"),
                el("strong", { style: `color:${vr.used_gb / vr.total_gb > 0.8 ? "var(--err)" : "var(--warn)"}` },
                  `${vr.used_gb} GB`)),
              el("div", {}, el("div.muted.small", {}, "可用"),
                el("strong", { style: `color:${vr.free_gb < 2 ? "var(--err)" : "var(--ok)"}` },
                  `${vr.free_gb} GB`))),
            // 显存使用进度条
            (() => {
              const pct = Math.round(vr.used_gb / vr.total_gb * 100);
              const bar = el("div", {
                style: `width:100%;height:8px;border-radius:4px;background:var(--panel-2);margin-top:6px;position:relative` });
              bar.append(el("div", {
                style: `width:${pct}%;height:100%;border-radius:4px;background:${pct > 80 ? "var(--err)" : pct > 50 ? "var(--warn)" : "var(--ok)"};transition:width .3s` }));
              return bar;
            })(),
            vr.loaded_models?.length
              ? el("div", { style: "margin-top:10px" },
                  el("div.muted.small", {}, "当前已加载模型："),
                  ...vr.loaded_models.map((m) =>
                    el("span.badge", { style: "margin:2px" }, m)))
              : el("div.muted.small", { style: "margin-top:10px" }, "当前无模型加载"))
        : el("div.muted", {}, "当前环境无 GPU/CUDA，所有推理在 CPU 上运行（速度较慢但功能完整）。"));
    root.append(vramCard);
  }

  const logEl = el("div#eventLog", {},
    el("div.muted", {}, "等待事件…（SSE 实时推送）"));
  root.append(el("div.card", {},
    el("h3", {}, "实时事件"), logEl));

  bus.on("sse", (event) => {
    if (!logEl.isConnected) return;
    if (logEl.firstChild?.classList?.contains("muted")) logEl.innerHTML = "";
    logEl.prepend(el("div", {},
      el("span.mono", {}, fmtTime(event.ts)), "　",
      `${event.type}　${event.stage || ""} ${event.status || ""} ${event.message || ""}`
        .replace(/\s+/g, " ")));
  });
}

export function render(root) { load(root); }
