# PyInstaller spec of the xat-rag service: one folder, no network code that is not needed.
# Build: uv run pyinstaller build/xat-rag-service.spec --distpath build/out/dist --workpath build/out/work
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
    get_package_paths,
)

# The folders in "transformers/models" that the service uses. rt_detr_v2 is the docling layout model, and rt_detr
# holds its backbone. "auto" finds the model classes from the model config, and it imports encoder_decoder.
# If you add a transformers model to build/models.lock.json, add its folder in "transformers/models" here.
TRANSFORMERS_MODELS = ("auto", "encoder_decoder", "rt_detr", "rt_detr_v2")


def _needed_transformers_module(name):
    """Return False for a module in "transformers.models.<x>" when "<x>" is not in TRANSFORMERS_MODELS, else True."""
    parts = name.split(".")
    return len(parts) < 3 or parts[1] != "models" or parts[2] in TRANSFORMERS_MODELS


# "transformers/models/__init__.py" imports all model folders for the type checker, and PyInstaller follows these
# imports. Thus exclude the folders that the service does not use.
_, _transformers_models = get_package_paths("transformers.models")
UNUSED_TRANSFORMERS_MODELS = [
    f"transformers.models.{folder.name}"
    for folder in sorted(Path(_transformers_models).iterdir())
    if (folder / "__init__.py").is_file() and folder.name not in TRANSFORMERS_MODELS
]


datas = collect_data_files("sqlite_vec")
for package in ("docling", "docling_core", "docling_parse", "docling_ibm_models", "docx", "latex2mathml", "rapidocr"):
    datas += collect_data_files(package)
# The default patterns match only "lib*.so" on Linux, so they do not find "vec0.so".
binaries = collect_dynamic_libs("sqlite_vec", search_patterns=["vec0.*"]) + collect_dynamic_libs("onnxruntime")
hidden = []
for package in ("docling", "docling_core", "docling_parse", "docling_ibm_models", "torchvision"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hidden += package_hidden
# All of transformers has more than 500 model folders. Collect only the models that the service uses. Docling reads
# the version of transformers from its metadata, so collect the metadata too.
datas += collect_data_files("transformers", excludes=["models/**"]) + copy_metadata("transformers")
hidden += collect_submodules("transformers", filter=_needed_transformers_module)

a = Analysis(
    ["../packages/xat-rag-service/src/xat_rag_service/main.py"],
    binaries=binaries,
    datas=datas,
    hiddenimports=["uvicorn.lifespan.off", "uvicorn.protocols.http.h11_impl", "uvicorn.loops.asyncio", *hidden],
    runtime_hooks=["pyinstaller_runtime_hook.py"],
    # Build tools and optional imports that the service does not use.
    excludes=[
        "setuptools", "pkg_resources", "tkinter", "IPython", "matplotlib", "pytest", "mistral_common",
        "PyInstaller",
        *UNUSED_TRANSFORMERS_MODELS,
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="xat-rag-service", console=True, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="xat-rag-service")
