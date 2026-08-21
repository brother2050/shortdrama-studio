/* 设置视图：能力后端选择 + 模型预设（自动填充 JSON）+ 参数 + 画幅 + 分集默认值。 */
import { api } from "../api.js";
import { el, toast } from "../ui.js";

/** 当前配置对应的模型预设名（匹配不上 = 自定义）。 */
function detectPresetName(conf, presets) {
  for (const p of presets) {
    if (conf.backend !== p.backend) continue;
    const key = ["model_preset", "model_id", "model_dir",
                 "checkpoint_dir", "model"].find((k) => p.params[k] != null);
    if (!key || conf.params?.[key] === p.params[key]) return p.name;
  }
  return "custom";
}

async function load(root) {
  const [settings, backends, catalog] = await Promise.all([
    api.settings(), api.backends(), api.modelsCatalog()]);
  root.innerHTML = "";
  const draft = structuredClone(settings);

  /* 模型存放说明（统一项目根 models/） */
  root.append(el("div.card", {},
    el("h3", {}, "模型目录"),
    el("div.muted.small", {},
      `所有离线模型统一存放在项目根目录：${catalog.models_root}`,
      el("div", { style: "margin-top:4px" },
        "布局：models/<能力>/<预设名>/（共享组件在 models/<能力>/_shared/）。"),
      el("div", { style: "margin-top:4px" },
        "下载：python scripts/download_models.py --capability <能力> --preset <预设名>",
        "；选择下方「模型预设」后参数 JSON 自动生成，可手动微调。"))));

  const capCards = [];
  for (const [cap, specs] of Object.entries(backends)) {
    const conf = draft.capabilities[cap];
    const presets = catalog.capabilities[cap] || [];

    /* 后端下拉 */
    const sel = el("select", {},
      el("option", { value: "auto" }, "auto（自动选择可用后端）"),
      ...specs.map((s) => el("option", {
        value: s.name,
        selected: conf.backend === s.name ? "" : null,
        disabled: s.available === false ? "" : null },
        `${s.name} — ${s.display_name}${s.available === false ? "（未安装依赖）" : " ✓"}`)));
    sel.value = conf.backend;

    /* 模型预设下拉：选中即自动填充 backend + params JSON */
    const presetSel = el("select", { title: "选择模型预设，自动生成参数 JSON" },
      el("option", { value: "custom" }, "自定义（手动编辑参数 JSON）"),
      ...presets.map((p) => el("option", {
        value: p.name,
        selected: detectPresetName(conf, presets) === p.name ? "" : null },
        `${p.name}${p.default ? "（推荐）" : ""} — ${p.desc}`
        + `${p.downloaded ? " ✓已下载" : `（未下载，约 ${p.size_gb}GB）`}`)));
    presetSel.value = detectPresetName(conf, presets);

    /* 参数 JSON 编辑框 */
    const paramsArea = el("textarea.json", {
      placeholder: "{}（留空使用后端默认参数）" });
    paramsArea.value = JSON.stringify(conf.params || {}, null, 2);
    paramsArea.addEventListener("change", () => {
      try { conf.params = JSON.parse(paramsArea.value || "{}"); }
      catch { toast("参数不是合法 JSON，未保存", true); }
    });

    const docs = el("div.small.muted");
    const updateDocs = () => {
      const s = specs.find((x) => x.name === sel.value);
      docs.innerHTML = "";
      docs.append(s ? `${s.description || ""}` : "auto：按可用性与优先级自动挑选已注册后端。");
      if (s?.param_docs && Object.keys(s.param_docs).length) {
        const docsList = el("div.mono", { style: "margin-top:4px" });
        for (const [k, v] of Object.entries(s.param_docs)) {
          docsList.append(el("div", { style: "margin:2px 0" },
            el("span", { style: "color:var(--accent)" }, k),
            `: ${v}`));
        }
        docs.append(docsList);
      }
      if (s?.vram_gb) {
        docs.append(el("div", { style: "margin-top:6px" },
          el("span.badge", {}, `显存需求: ${s.vram_gb}GB`)));
      }
    };

    /* 预设选择 → 自动填充（backend + 完整参数 JSON），可再手动微调 */
    presetSel.addEventListener("change", () => {
      const name = presetSel.value;
      if (name === "custom") return;
      const p = presets.find((x) => x.name === name);
      if (!p) return;
      conf.backend = p.backend;
      conf.params = { ...p.params };
      sel.value = p.backend;
      paramsArea.value = JSON.stringify(conf.params, null, 2);
      updateDocs();
      toast(`已选预设 ${name}：${p.downloaded ? "本地已下载，完全离线可用" : "未下载，首次使用将自动下载（也可先运行下载脚本）"}`);
    });

    /* 后端切换 → 预设匹配状态刷新 + 参数模板提示 */
    sel.addEventListener("change", () => {
      conf.backend = sel.value;
      const newSpec = specs.find((s) => s.name === sel.value);
      presetSel.value = detectPresetName(conf, presets);
      if (newSpec?.default_params && Object.keys(newSpec.default_params).length) {
        const merged = { ...newSpec.default_params, ...(conf.params || {}) };
        conf.params = merged;
        paramsArea.value = JSON.stringify(merged, null, 2);
      }
      updateDocs();
    });
    updateDocs();

    capCards.push(el("div.card", {},
      el("div.row.spread", {},
        el("h3", { style: "margin:0" },
          { llm: "剧本 LLM", tts: "语音合成 TTS", image: "关键帧图像",
            video: "镜头视频", asr: "语音识别 ASR" }[cap] || cap),
        el("div.row", {},
          el("label.row.small", {}, "后端 ", sel),
          el("label.row.small", {}, "模型预设 ", presetSel))),
      paramsArea, docs));
  }

  const vo = draft.video_output;
  const ed = draft.episode_defaults;
  const num = (obj, key, label, step = 1) => {
    const input = el("input", { type: "number", step, value: obj[key] });
    input.addEventListener("change", () => { obj[key] = Number(input.value); });
    return el("label.row.small", {}, `${label} `, input);
  };
  const toggle = (obj, key, label) => {
    const input = el("input", { type: "checkbox" });
    input.checked = !!obj[key];
    input.addEventListener("change", () => { obj[key] = input.checked; });
    return el("label.row.small", {}, `${label} `, input);
  };
  const select = (obj, key, label, options) => {
    const sel = el("select", {},
      ...options.map(([v, name]) =>
        el("option", { value: v, selected: obj[key] === v ? "" : null }, name)));
    sel.value = obj[key] ?? options[0][0];
    sel.addEventListener("change", () => { obj[key] = sel.value; });
    return el("label.row.small", {}, `${label} `, sel);
  };

  root.append(
    el("h2", {}, "生成设置（即时保存到 data/config.json，可直接手工编辑）"),
    el("div.grid.cols-2", {}, ...capCards),
    el("div.card", {},
      el("h3", {}, "画幅与帧率"),
      el("div.row", {},
        num(vo, "width", "宽"), num(vo, "height", "高"), num(vo, "fps", "帧率"))),
    el("div.card", {},
      el("h3", {}, "分集默认值（创建项目时可覆盖）"),
      el("div.row", {},
        num(ed, "shots_per_episode", "每集镜头数"),
        num(ed, "target_clip_seconds", "单镜头目标秒", 0.5),
        select(ed, "transition", "镜头过渡",
          [["none", "关闭"], ["flf2v", "首尾帧转场（需 flf2v 模型）"]]),
        toggle(ed, "character_refs", "角色参考图（锁定外貌）")),
      el("div.muted.small", { style: "margin-top:6px" },
        "镜头过渡需 video 后端选 diffsynth_wan 且 model_preset=wan2.1-flf2v-14b；",
        "角色参考图搭配 image 后端 model_preset=qwen-image-edit 效果最佳。"),
      (() => {
        const ta = el("textarea", { style: "width:100%;margin-top:8px" }, ed.style);
        ta.addEventListener("change", () => { ed.style = ta.value; });
        return ta;
      })()),
    el("div.row", {},
      el("button.primary", {
        onclick: async () => {
          try { await api.saveSettings(draft); toast("设置已保存"); }
          catch (e) { toast(`保存失败：${e.message}`, true); }
        } }, "保存全部设置"),
      el("button.ghost", { onclick: () => load(root) }, "放弃修改")));
}

export function render(root) { load(root); }
