"""LLM 后端 2/3：modelscope（ModelScope 原生 LLM 推理）。

使用 ModelScope 自带的 ``AutoModelForCausalLM`` / ``AutoTokenizer`` 进行
本地 LLM 推理，替代已移除的 ollama 后端。

设计要点
--------
* ``params.model_id`` 支持两种写法（app/adapters/model_paths.py 统一解析）：
  - 本地路径：预设名（``qwen2.5-1.5b``）或 ``models/llm/<预设名>``
    （相对项目根）或绝对路径——存在即完全离线加载；
  - 在线仓库 id（如 ``qwen/Qwen2.5-1.5B-Instruct``）——自动下载（缓存锚定
    项目根 ``models/_cache``）。
* ``requires=["modelscope"]`` 声明依赖，``is_available()`` 自动探测。
* 重依赖 torch/modelscope 在 ``run()`` 内部惰性导入，保证未安装时模块
  仍可被导入、注册、探测为"不可用"。
* 显存生命周期通过 ``ModelSlot`` 管理，OOM 时自动回退 CPU。
* 支持 ``device="auto"``：优先 CUDA，显存不足自动降级 CPU。
"""
from __future__ import annotations

from typing import Any

from app.adapters.base import (AdapterBase, AdapterError, AdapterSpec,
                               register_adapter)
from app.adapters.model_paths import ModelPathError, model_source
from app.paths import models_root
from app.vram import ModelSlot, check_vram, pick_device


@register_adapter
class ModelScopeLLM(AdapterBase):
    spec = AdapterSpec(
        name="modelscope", capability="llm",
        display_name="ModelScope LLM（Qwen 系列）",
        description="ModelScope 原生 LLM 推理：qwen/Qwen2.5-0.5B-Instruct(CPU)、"
        "qwen/Qwen2.5-1.5B-Instruct(推荐)、qwen/Qwen2.5-7B-Instruct(高质量)。"
        "使用 modelscope.AutoModelForCausalLM，模型自动下载与缓存。",
        priority=15, requires=["modelscope"],
        default_params={
            "model_id": "models/llm/qwen2.5-1.5b",  # 本地路径或在线仓库 id
            "device": "auto",
            "max_new_tokens": 1024,
            "temperature": 0.8,
        },
        param_docs={
            "model_id": "预设名（qwen2.5-1.5b）/ models/llm/<预设名>（相对项目根）"
                        "/ 绝对路径 / ModelScope 在线仓库 id",
            "device": "推理设备 auto/cpu/cuda，auto 自动探测",
            "max_new_tokens": "单次生成最大 token 数",
            "temperature": "采样温度（0~1.5，越大越发散）",
        },
        vram_gb=2.0, license="Apache-2.0（Qwen 系列模型）",
    )

    _slot = ModelSlot("llm_modelscope", capability="llm")

    def _resolve_source(self) -> tuple[str, bool]:
        """返回 (加载源, 是否本地)。"""
        try:
            return model_source(str(self.params.get("model_id") or ""),
                                "llm")
        except ModelPathError as exc:
            raise AdapterError(str(exc)) from exc

    def _load(self):
        if self._slot.is_loaded:
            return self._slot.model
        if not check_vram(self.spec.vram_gb):
            raise AdapterError(f"显存不足：需要约 {self.spec.vram_gb}GB，当前可用不足。"
                               f"请先在系统页查看显存状态，或切换到不需要 GPU 的后端。")

        source, _is_local = self._resolve_source()

        def _do_load():
            import os

            import torch
            from modelscope import AutoModelForCausalLM, AutoTokenizer

            # 在线模式的下载缓存也锚定到项目根 models/_cache
            os.environ.setdefault("MODELSCOPE_CACHE",
                                  str(models_root() / "_cache"))
            model_id = source
            device = pick_device(self.params.get("device", "auto"), self.spec.vram_gb)
            tokenizer = AutoTokenizer.from_pretrained(
                model_id, trust_remote_code=True)
            try:
                model = AutoModelForCausalLM.from_pretrained(
                    model_id, torch_dtype="auto",
                    trust_remote_code=True,
                    device_map=device if device == "cuda" else None,
                ).eval()
                if device != "cuda":
                    model = model.to(device)
            except (torch.cuda.OutOfMemoryError, RuntimeError) as exc:
                if "out of memory" in str(exc).lower():
                    model = AutoModelForCausalLM.from_pretrained(
                        model_id, torch_dtype=torch.float32,
                        trust_remote_code=True,
                    ).to("cpu").eval()
                    device = "cpu"
                else:
                    raise
            return (tokenizer, model, device)

        return self._slot.load(_do_load)

    def unload(self) -> None:
        self._slot.unload()

    def run(self, ctx: dict[str, Any], progress=None) -> dict[str, Any]:
        import torch

        tokenizer, model, device = self._load()
        if progress:
            progress("模型已加载，生成中", 30.0)
        messages = []
        if ctx.get("system"):
            messages.append({"role": "system", "content": ctx["system"]})
        messages += [m for m in ctx.get("messages", [])
                     if m.get("role") != "system"]

        text = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(text, return_tensors="pt").to(device)

        with torch.no_grad():
            out = model.generate(
                **inputs,
                max_new_tokens=int(self.params.get("max_new_tokens", 1024)),
                do_sample=float(self.params.get("temperature", 0.8)) > 0,
                temperature=max(0.05, float(self.params.get("temperature", 0.8))),
            )
        result = tokenizer.decode(
            out[0][inputs["input_ids"].shape[1]:],
            skip_special_tokens=True)
        if progress:
            progress("生成完成", 90.0)
        return {"text": result.strip()}
