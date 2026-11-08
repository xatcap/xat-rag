# PyInstaller spec of the xat-rag service: one folder, no network code that is not needed.
# Build: uv run pyinstaller build/xat-rag-service.spec --distpath build/out/dist --workpath build/out/work
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

datas = collect_data_files("sqlite_vec") + collect_data_files("langchain_core") + collect_data_files("pypdf")
binaries = collect_dynamic_libs("sqlite_vec") + collect_dynamic_libs("onnxruntime")

a = Analysis(
    ["../packages/xat-rag-service/src/xat_rag_service/main.py"],
    binaries=binaries,
    datas=datas,
    hiddenimports=["uvicorn.lifespan.off", "uvicorn.protocols.http.h11_impl", "uvicorn.loops.asyncio"],
    runtime_hooks=["pyinstaller_runtime_hook.py"],
    # ADR 0011 section 4, plus optional imports of pypdf (PIL) and the build tools.
    excludes=[
        "tiktoken", "openai", "posthog", "nltk", "torch", "transformers", "sentence_transformers", "sentry_sdk",
        "opentelemetry.exporter", "opentelemetry.sdk", "unstructured", "cryptography", "langchain_community",
        "langchain_classic", "langchain", "langgraph", "sqlalchemy", "aiohttp", "PIL", "setuptools", "pkg_resources",
        "tkinter", "IPython", "matplotlib", "pytest", "mistral_common", "PyInstaller",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="xat-rag-service", console=True, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="xat-rag-service")
