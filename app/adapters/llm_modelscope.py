"""LLM 后端 2/3：modelscope（ModelScope 原生 LLM 推理）。

使用 ModelScope 自带的 ``AutoModelForCausalLM`` / ``AutoTokenizer`` 进行
本地 LLM 推理，替代已移除的 ollama 后端。模型通过 ``modelscope.snapshot_download``
统一下载，``from_pretrained(model_id)`` 自动处理缓存与离线加载。

设计要点
--------
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
            "model_id": "qwen/Qwen2.5-1.5B-Instruct",
            "device": "auto",
            "max_new_tokens": 1024,
            "temperature": 0.8,
        },
        param_docs={
            "model_id": "ModelScope 模型 ID（如 qwen/Qwen2.5-1.5B-Instruct）",
            "device": "推理设备 auto/cpu/cuda，auto 自动探测",
            "max_new_tokens": "单次生成最大 token 数",
            "temperature": "采样温度（0~1.5，越大越发散）",
        },
        vram_gb=2.0, license="Apache-2.0（Qwen 系列模型）",
    )

    _slot = ModelSlot("llm_modelscope", capability="llm")

    def _load(self):
        if self._slot.is_loaded:
            return self._slot.model
        if not check_vram(self.spec.vram_gb):
            raise AdapterError(f"显存不足：需要约 {self.spec.vram_gb}GB，当前可用不足。"
                               f"请先在系统页查看显存状态，或切换到不需要 GPU 的后端。")

        def _do_load():
            import torch
            from modelscope import AutoModelForCausalLM, AutoTokenizer

            model_id = str(self.params.get("model_id") or "").strip()
            if not model_id:
                raise AdapterError(
                    "modelscope LLM 后端需要设置参数 model_id"
                    "（如 qwen/Qwen2.5-1.5B-Instruct）。")
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
