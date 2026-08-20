/* DOM 工具：声明式建节点 + 通用小组件。 */

/** el("div.card", {onclick}, children...) —— class 用 . 连接。 */
export function el(spec, attrs = {}, ...children) {
  const [tag, ...classes] = spec.split(".");
  const node = document.createElement(tag || "div");
  if (classes.length) node.className = classes.join(" ");
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null) continue;
    if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (k === "html") node.innerHTML = v;
    else if (k in node && k !== "type" && k !== "value" && typeof node[k] === "boolean") node[k] = v;
    else node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.append(c.nodeType ? c : document.createTextNode(c));
  }
  return node;
}

export function toast(message, isErr = false) {
  const box = document.getElementById("toast");
  box.textContent = message;
  box.className = "toast" + (isErr ? " err" : "");
  box.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { box.hidden = true; }, 3600);
}

export function badge(status) {
  const label = { ready: "已就绪", generating: "生成中", pending: "排队中",
                  running: "运行中", succeeded: "成功", failed: "失败",
                  canceled: "已取消", draft: "草稿", missing: "未生成" }[status] || status;
  return el("span.badge." + status, {}, label);
}

/** 8 阶段流水线条。stages: {stage: "ready"|"missing"|"running"|"failed"} */
export function pipelineBar(stageStates, stagesMeta, onStageClick) {
  const wrap = el("div.pipeline");
  for (const { stage, label } of stagesMeta) {
    const st = stageStates?.[stage] || "missing";
    const node = el("span.stage." + (st === "ready" ? "ready" : st),
                    { title: `${label}：${{ ready: "产物齐备", missing: "未生成",
                     running: "运行中", failed: "失败" }[st] || st}` },
                    label);
    if (onStageClick) {
      node.style.cursor = "pointer";
      node.addEventListener("click", () => onStageClick(stage, st));
    }
    wrap.append(node);
  }
  return wrap;
}

export function fmtTime(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso
    : d.toLocaleString("zh-CN", { hour12: false });
}

export function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
