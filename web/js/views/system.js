/* 系统视图：能力健康矩阵 / 磁盘 / 版本 / 实时事件日志。 */
import { api } from "../api.js";
import { bus } from "../state.js";
import { el, fmtTime } from "../ui.js";

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
    rows.push(el("tr", {}, el("td", {}, k), el("td.mono", {}, String(v))));
  }
  root.append(el("div.card", {},
    el("h3", {}, "环境"), el("table", {}, ...rows)));

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
