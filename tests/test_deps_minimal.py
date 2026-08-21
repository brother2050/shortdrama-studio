"""依赖最小性 / 前端完整性 / 仓库工程化文件测试。"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


# ----------------------------------------------------------------------
# 依赖最小且有效
# ----------------------------------------------------------------------
def test_core_app_imports_without_heavy_deps():
    """核心链路导入不得新增重依赖（环境可能预载，故比较 sys.modules 差集）。"""
    import importlib

    before = set(sys.modules)
    for mod in ("app.main", "app.pipeline", "app.chat", "app.composer",
                "app.services", "app.store", "app.tasks"):
        importlib.import_module(mod)
    heavy = ("torch", "transformers", "diffsynth", "modelscope", "funasr",
             "numpy", "PIL", "requests")
    newly = [m for m in heavy if m in sys.modules and m not in before]
    assert not newly, f"核心链路意外引入 {newly}（核心依赖应保持最小）"


def test_requirements_all_used_and_importable():
    """requirements.txt 每一项：能导入 + 已安装 + 在源码中被引用。"""
    reqs = []
    for line in (REPO / "requirements.txt").read_text("utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            reqs.append(line)
    assert reqs, "requirements.txt 为空"

    sources = ""
    for py in (REPO / "app").rglob("*.py"):
        sources += py.read_text("utf-8")

    import importlib
    import importlib.metadata
    import importlib.util

    for req in reqs:
        m = re.match(r"([A-Za-z0-9_.\-]+)(?:[><=!].*)?", req)
        assert m, f"无法解析依赖项: {req}"
        name = m.group(1)
        importlib.import_module(name)
        assert importlib.util.find_spec(name) is not None
        importlib.metadata.version(name)  # 未安装会抛 PackageNotFoundError
        # 被源码使用（import 或文档引用）
        assert name.lower() in sources.lower(), f"{name} 未在 app/ 中使用"


def test_dev_requirements_importable():
    for name in ("pytest", "httpx"):
        import importlib

        importlib.import_module(name)


def test_optional_model_deps_not_required():
    """requirements-models.txt 中的重依赖缺位时，核心功能可用（本环境即验证）。"""
    opt = REPO / "requirements-models.txt"
    assert opt.exists()
    from app.adapters import registry

    for cap in ("llm", "tts", "image", "video"):
        picked = registry.auto_pick(cap)
        assert picked, f"{cap} 在无重依赖环境无可用后端"


# ----------------------------------------------------------------------
# 前端界面齐全
# ----------------------------------------------------------------------
def test_frontend_files_complete():
    web = REPO / "web"
    assert (web / "index.html").exists()
    assert (web / "css/app.css").exists()
    for mod in ("main.js", "api.js", "state.js", "ui.js"):
        assert (web / "js" / mod).exists(), f"缺前端模块 {mod}"
    for view in ("chat", "projects", "episode", "tasks", "settings", "system"):
        f = web / "js" / "views" / f"{view}.js"
        assert f.exists(), f"缺视图 {view}"
        assert "export function render" in f.read_text("utf-8")


def test_frontend_index_references_all_views():
    html = (REPO / "web/index.html").read_text("utf-8")
    for view in ("chat", "projects", "episode", "tasks", "settings", "system"):
        assert f'data-view="{view}"' in html


def test_frontend_js_syntax_valid():
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用（CI 已有独立步骤）")
    js_files = sorted((REPO / "web/js").rglob("*.js"))
    assert js_files
    for f in js_files:
        proc = subprocess.run([node, "--input-type=module", "--check"],
                              input=f.read_text("utf-8"), capture_output=True, text=True)
        assert proc.returncode == 0, f"{f} 语法错误:\n{proc.stderr}"


def test_api_routes_cover_ui_needs():
    """前端调用的每个端点都真实存在（防接口漂移）。"""
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    # FastAPI 0.141 起 app.routes 不再展平 include 的路由，改以 OpenAPI 规范为准
    spec = client.get("/openapi.json").json()
    paths = set(spec["paths"])
    paths_needed = ["/api/version", "/api/projects", "/api/tasks",
                    "/api/tasks/stages", "/api/system/health",
                    "/api/system/backends", "/api/settings", "/api/events",
                    "/api/chat"]
    for p in paths_needed:
        assert p in paths, f"前端依赖的端点缺失: {p}"
    # 媒体路由（{rel:path} 转换器在 OpenAPI 模板中显示为 {rel}）
    assert "/api/projects/{pid}/media/{rel}" in paths


# ----------------------------------------------------------------------
# 工程化文件齐全
# ----------------------------------------------------------------------
def test_repo_meta_files():
    for f in ("README.md", "DESIGN.md", "LICENSE", "pyproject.toml",
              "requirements.txt", "requirements-dev.txt", "requirements-models.txt",
              ".gitignore", ".github/workflows/ci.yml"):
        assert (REPO / f).exists(), f"缺工程文件 {f}"


def test_gitignore_protects_runtime_data():
    gi = (REPO / ".gitignore").read_text("utf-8")
    assert "data/" in gi


def test_model_download_script_exists_and_offline_friendly():
    script = REPO / "scripts/download_models.py"
    assert script.exists(), "缺模型离线下载脚本"
    src = script.read_text("utf-8")
    assert "modelscope" in src
    # 不选也有默认值：脚本可无参数运行
    assert 'default=' in src or "MODELSCOPE_CACHE" in src


def test_docs_exist():
    docs = REPO / "docs"
    assert (docs / "models.md").exists()
    assert (docs / "offline.md").exists()
    assert (docs / "api.md").exists()


def test_no_hardcoded_retry_loops():
    """设计约束：不允许在代码里内置自动重试次数（简单子串检查，防回溯挂起）。"""
    forbidden = ("max_retries", "max_retry", "retry_count", "num_retries",
                 "attempts=", "backoff", "while not success")
    violations = []
    for py in (REPO / "app").rglob("*.py"):
        src = py.read_text("utf-8")
        for word in forbidden:
            if word in src:
                violations.append(f"{py.name}: {word}")
    assert not violations, f"发现疑似自动重试逻辑: {violations}"
