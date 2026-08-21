/* 对话视图：消息流 + 动作卡片 + 快捷指令 + 发送。 */
import { api } from "../api.js";
import { state, bus } from "../state.js";
import { el, toast, fmtTime } from "../ui.js";

const SUGGESTIONS = [
  "创建一部 3 集的都市爱情短剧，名字叫《晚风》",
  "生成第 1 集", "拍下一集", "现在什么进度",
  "第 1 集讲到哪了", "重试失败的任务", "取消当前任务", "帮助",
];

let logEl = null;

function messageNode(msg) {
  const isUser = msg.role === "user";
  const bubble = el("div.bubble", {}, msg.content);
  for (const card of msg.cards || []) {
    const payload = Object.entries(card.payload || {})
      .map(([k, v]) => `${k}=${v}`).join("  ");
    bubble.append(el("div.action-card", {},
      el("span." + (card.ok ? "ok-true" : "ok-false"), {}, card.ok ? "✓ " : "✗ "),
      `${card.summary || card.intent}${payload ? "　" + payload : ""}`));
  }
  const node = el("div.msg" + (isUser ? ".user" : ""), {},
    el("div.avatar", {}, isUser ? "我" : "剧"),
    bubble);
  return node;
}

async function loadHistory() {
  if (!state.projectId) return;
  try {
    const list = await api.chatHistory(state.projectId, 80);
    logEl.innerHTML = "";
    for (const m of list) logEl.append(messageNode(m));
    scrollBottom();
  } catch (e) { /* 新项目无历史 */ }
}

function scrollBottom() { logEl.scrollTop = logEl.scrollHeight; }

async function send(text) {
  text = (text || "").trim();
  if (!text) return;
  logEl.append(messageNode({ role: "user", content: text }));
  scrollBottom();
  const thinking = el("div.msg", {},
    el("div.avatar", {}, "剧"),
    el("div.bubble", {}, "思考中…"));
  logEl.append(thinking);
  scrollBottom();
  try {
    const out = await api.chat(text, state.projectId);
    thinking.remove();
    logEl.append(messageNode({ role: "assistant", content: out.reply, cards: out.actions }));
    if (out.project_id && out.project_id !== state.projectId) state.setProject(out.project_id);
    bus.emit("refresh-projects");
  } catch (e) {
    thinking.remove();
    logEl.append(messageNode({ role: "assistant", content: `请求失败：${e.message}` }));
    toast(`对话失败：${e.message}`, true);
  }
  scrollBottom();
}

export function render(root) {
  const input = el("textarea", { placeholder: "描述你的短剧，或直接下指令…（Enter 发送 / Shift+Enter 换行）" });
  const sendBtn = el("button.primary", { onclick: () => send(input.value) }, "发送");
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); send(input.value); }
  });

  logEl = el("div.chat-log");
  if (!state.projects.length) {
    logEl.append(el("div.msg", {},
      el("div.avatar", {}, "剧"),
      el("div.bubble", {},
        el("strong", {}, "欢迎使用 ShortDrama Studio"),
        el("div", { style: "margin-top:6px" },
          "我是你的短剧导演助手，可以帮你从零开始创作连续短剧。"),
        el("div", { style: "margin-top:8px" },
          el("strong", {}, "快速开始：")),
        el("ol", { style: "margin:4px 0; padding-left:20px" },
          el("li", {}, "在下方输入「创建一部 3 集的都市爱情短剧，名字叫《晚风》」"),
          el("li", {}, "然后说「生成第 1 集」，我会自动完成 8 个阶段"),
          el("li", {}, "随时说「现在什么进度」查看进展"),
          el("li", {}, "失败后说「重试失败的任务」即可继续")),
        el("div.muted.small", { style: "margin-top:8px" },
          "💡 首次使用推荐先用 mock 后端快速体验全流程，再到「设置」页配置真实模型。"),
        el("div.muted.small", {},
          "💡 想要更高质量？在「设置」页把后端切换到 diffsynth/qwen-image/cosyvoice；"),
        el("div.muted.small", {},
          "💡 说「开启角色参考图」「开启镜头过渡」可提升角色一致性与镜头衔接。"))));
  }

  root.append(el("div.chat-wrap", {},
    el("div.suggestions",
      { html: "" },
      ...SUGGESTIONS.map((s) => el("span.suggestion",
        { onclick: () => send(s) }, s))),
    logEl,
    el("div.chat-input", {}, input, sendBtn),
  ));
  loadHistory();

  bus.on("chat-updated", () => { if (root.isConnected) loadHistory(); });
}
