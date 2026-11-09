# PyInstaller spec of the xat-rag service: one folder, no network code that is not needed.
# Build: uv run pyinstaller build/xat-rag-service.spec --distpath build/out/dist --workpath build/out/work
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs

datas = collect_data_files("sqlite_vec")
for package in ("docling", "docling_core", "docling_parse", "docling_ibm_models", "docx", "latex2mathml", "rapidocr"):
    datas += collect_data_files(package)
# The default patterns match only "lib*.so" on Linux, so they do not find "vec0.so".
binaries = collect_dynamic_libs("sqlite_vec", search_patterns=["vec0.*"]) + collect_dynamic_libs("onnxruntime")
hidden = []
for package in ("docling", "docling_core", "docling_parse", "docling_ibm_models", "torchvision", "transformers"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hidden += package_hidden

a = Analysis(
    ["../packages/xat-rag-service/src/xat_rag_service/main.py"],
    binaries=binaries,
    datas=datas,
    hiddenimports=["uvicorn.lifespan.off", "uvicorn.protocols.http.h11_impl", "uvicorn.loops.asyncio", *hidden],
    runtime_hooks=["pyinstaller_runtime_hook.py"],
    # Optional imports of pypdf (PIL) and the build tools.
    excludes=[
        "setuptools", "pkg_resources", "tkinter", "IPython", "matplotlib", "pytest", "mistral_common",
        "PyInstaller",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="xat-rag-service", console=True, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="xat-rag-service")
