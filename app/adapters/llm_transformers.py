"""LLM 后端 2/3：transformers_qwen（ModelScope 离线模型）。

离线要点（见 docs/offline.md）：
1. 联网时 ``python scripts/download_models.py --capability llm --local-dir ./models``
2. 本后端 ``params.model_path`` 指向本地目录（或已缓存到 ~/.cache 的模型 id）；
3. ``from_pretrained(local_dir)`` 全程无外网请求。

重依赖 torch/transformers 在 run() 内惰性导入。
"""
from __future__ import annotations

from typing import Any

from app.adapters.base import (AdapterBase, AdapterError, AdapterSpec,
                               register_adapter)
from app.vram import ModelSlot, pick_device, unload_model, check_vram


@register_adapter
class TransformersQwenLLM(AdapterBase):
    spec = AdapterSpec(
        name="transformers_qwen", capability="llm", display_name="Qwen（transformers 本地）",
        description="ModelScope/HuggingFace 本地 Qwen 系列（Qwen2.5-0.5B/1.5B/7B、Qwen3 系列），"
                    "完全离线推理，CPU 可跑小参数量版本。",
        priority=20, requires=["torch", "transformers"],
        default_params={
            "model_path": "",          # 本地模型目录；空则报错并给出下载指引
            "device": "auto",          # auto / cpu / cuda
            "max_new_tokens": 1024,
            "temperature": 0.8,
        },
        param_docs={
            "model_path": "本地模型目录（scripts/download_models.py 下载后的路径）",
            "device": "推理设备 auto/cpu/cuda，auto 自动探测",
            "max_new_tokens": "单次生成最大 token 数",
            "temperature": "采样温度（0~1.5，越大越发散）",
        },
        vram_gb=2.0, license="Apache-2.0（Qwen 系列模型）",
    )

    _slot = ModelSlot("llm_transformers")

    def _load(self):
        path = str(self.params.get("model_path") or "").strip()
        if not path:
            raise AdapterError(
                "transformers_qwen 需要设置参数 model_path（本地模型目录）。"
                "离线下载：python scripts/download_models.py --capability llm --local-dir ./models")
        if self._slot.is_loaded:
            return self._slot.model
        if not check_vram(self.spec.vram_gb):
            raise AdapterError(f"显存不足：需要约 {self.spec.vram_gb}GB，当前可用不足。"
                               f"请先在系统页查看显存状态，或切换到不需要 GPU 的后端。")

        def _do_load():
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
            device = pick_device(self.params.get("device", "auto"), self.spec.vram_gb)
            tok = AutoTokenizer.from_pretrained(path, trust_remote_code=True)
            try:
                model = AutoModelForCausalLM.from_pretrained(
                    path, torch_dtype="auto", trust_remote_code=True,
                ).to(device).eval()
            except (torch.cuda.OutOfMemoryError, RuntimeError) as exc:
                if "out of memory" in str(exc).lower():
                    # OOM 恢复：回退到 CPU + float32
                    model = AutoModelForCausalLM.from_pretrained(
                        path, torch_dtype=torch.float32, trust_remote_code=True,
                    ).to("cpu").eval()
                    device = "cpu"
                else:
                    raise
            return (tok, model, device)

        return self._slot.load(_do_load)

    def unload(self) -> None:
        self._slot.unload()

    def run(self, ctx: dict[str, Any], progress=None) -> dict[str, Any]:
        import torch
        tok, model, device = self._load()
        if progress:
            progress("模型已加载，生成中", 30.0)
        messages = []
        if ctx.get("system"):
            messages.append({"role": "system", "content": ctx["system"]})
        messages += [m for m in ctx.get("messages", []) if m.get("role") != "system"]
        text = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = tok(text, return_tensors="pt").to(device)
        with torch.no_grad():
            out = model.generate(
                **inputs,
                max_new_tokens=int(self.params.get("max_new_tokens", 1024)),
                do_sample=float(self.params.get("temperature", 0.8)) > 0,
                temperature=max(0.05, float(self.params.get("temperature", 0.8))),
            )
        result = tok.decode(out[0][inputs["input_ids"].shape[1]:],
                            skip_special_tokens=True)
        if progress:
            progress("生成完成", 90.0)
        return {"text": result.strip()}
