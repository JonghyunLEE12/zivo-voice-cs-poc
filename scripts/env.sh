# Source from bash/zsh before setup or engine commands. All writable caches stay here.
ZIVO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-${(%):-%N}}")/.." && pwd)"
export HF_HOME="$ZIVO_ROOT/models"
export HF_HUB_CACHE="$HF_HOME/hub"
export HF_XET_CACHE="$HF_HOME/xet"
export HF_XET_CHUNK_CACHE_SIZE_BYTES=0
export UV_CACHE_DIR="$ZIVO_ROOT/.cache/uv"
export UV_PYTHON_INSTALL_DIR="$ZIVO_ROOT/.cache/python"
export XDG_CACHE_HOME="$ZIVO_ROOT/.cache"
export MPLCONFIGDIR="$ZIVO_ROOT/.cache/matplotlib"
export TORCH_HOME="$ZIVO_ROOT/.cache/torch"
export TMPDIR="$ZIVO_ROOT/.cache/tmp"
mkdir -p "$TMPDIR"
