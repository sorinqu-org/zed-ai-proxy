#!/usr/bin/env bash
set -e

# zed-ai-proxy Global CLI Installer for Bash, Zsh, and Fish

INSTALL_DIR="${HOME}/.local/share/zed-ai-proxy"
BIN_DIR="${HOME}/.local/bin"
CONFIG_DIR="${HOME}/.config/zed-ai-proxy"
STATE_DIR="${HOME}/.local/state/zed-ai-proxy"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Color codes (ANSI)
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

uninstall() {
    log_info "Uninstalling zed-ai-proxy..."

    # Stop daemon if running
    if [ -f "${STATE_DIR}/proxy.pid" ]; then
        PID=$(cat "${STATE_DIR}/proxy.pid" 2>/dev/null || true)
        if [ -n "${PID}" ] && kill -0 "${PID}" 2>/dev/null; then
            log_info "Stopping running daemon (PID: ${PID})..."
            kill "${PID}" 2>/dev/null || true
        fi
        rm -f "${STATE_DIR}/proxy.pid"
    fi

    # Remove binaries
    rm -f "${BIN_DIR}/zed-proxy"
    rm -f "${BIN_DIR}/zed-ai-proxy"

    # Remove completions
    rm -f "${HOME}/.local/share/bash-completion/completions/zed-proxy"
    rm -f "${HOME}/.local/share/bash-completion/completions/zed-ai-proxy"
    rm -f "${HOME}/.config/fish/completions/zed-proxy.fish"
    rm -f "${HOME}/.config/fish/completions/zed-ai-proxy.fish"
    rm -f "${HOME}/.zsh/completions/_zed-proxy"

    # Remove installation directory
    rm -rf "${INSTALL_DIR}"

    log_success "zed-ai-proxy has been uninstalled."
    log_info "Note: User configuration in ${CONFIG_DIR} was preserved."
    exit 0
}

if [ "$1" = "--uninstall" ] || [ "$1" = "uninstall" ]; then
    uninstall
fi

log_info "Starting global installation of zed-ai-proxy..."

# 1. Verify Python 3
PYTHON_BIN=""
for py in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "${py}" >/dev/null 2>&1; then
        PY_VER=$("${py}" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        PY_MAJOR=$("${py}" -c "import sys; print(sys.version_info.major)")
        PY_MINOR=$("${py}" -c "import sys; print(sys.version_info.minor)")
        if [ "${PY_MAJOR}" -eq 3 ] && [ "${PY_MINOR}" -ge 9 ]; then
            PYTHON_BIN="${py}"
            break
        fi
    fi
done

if [ -z "${PYTHON_BIN}" ]; then
    log_error "Python 3.9+ is required but was not found. Please install Python 3.9+."
    exit 1
fi
log_info "Using Python interpreter: ${PYTHON_BIN} (${PY_VER})"

# 2. Create destination directories
mkdir -p "${BIN_DIR}"
mkdir -p "${CONFIG_DIR}"
mkdir -p "${STATE_DIR}"
mkdir -p "${INSTALL_DIR}"

# 3. Initialize default accounts.json if not present
if [ ! -f "${CONFIG_DIR}/accounts.json" ]; then
    if [ -f "${SOURCE_DIR}/accounts.json" ]; then
        cp "${SOURCE_DIR}/accounts.json" "${CONFIG_DIR}/accounts.json"
        log_info "Copied existing accounts.json to ${CONFIG_DIR}/accounts.json"
    elif [ -f "${SOURCE_DIR}/accounts.example.json" ]; then
        cp "${SOURCE_DIR}/accounts.example.json" "${CONFIG_DIR}/accounts.json"
        log_info "Initialized ${CONFIG_DIR}/accounts.json from template"
    fi
fi

# 4. Copy application source files
log_info "Deploying application files to ${INSTALL_DIR}..."
cp -r "${SOURCE_DIR}/app" "${INSTALL_DIR}/"
cp -r "${SOURCE_DIR}/scripts" "${INSTALL_DIR}/"
cp "${SOURCE_DIR}/requirements.txt" "${INSTALL_DIR}/"
cp "${SOURCE_DIR}/pyproject.toml" "${INSTALL_DIR}/"

# 5. Create or update virtual environment
VENV_DIR="${INSTALL_DIR}/venv"
if [ ! -d "${VENV_DIR}" ]; then
    log_info "Creating isolated virtual environment in ${VENV_DIR}..."
    "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

log_info "Installing package and dependencies into virtual environment..."
"${VENV_DIR}/bin/pip" install --quiet --upgrade pip
"${VENV_DIR}/bin/pip" install --quiet -e "${INSTALL_DIR}"

# 6. Create wrapper scripts in BIN_DIR
cat << WRAPPER_EOF > "${BIN_DIR}/zed-proxy"
#!/usr/bin/env bash
export ACCOUNTS_FILE="\${ACCOUNTS_FILE:-${CONFIG_DIR}/accounts.json}"
exec "${VENV_DIR}/bin/zed-proxy" "\$@"
WRAPPER_EOF
chmod +x "${BIN_DIR}/zed-proxy"

ln -sf "${BIN_DIR}/zed-proxy" "${BIN_DIR}/zed-ai-proxy"
log_success "Installed binary launcher: ${BIN_DIR}/zed-proxy (and alias zed-ai-proxy)"

# 7. Configure PATH in user shell configuration files
add_path_to_file() {
    local shell_file="$1"
    local shell_name="$2"
    if [ -f "${shell_file}" ]; then
        if ! grep -q "${BIN_DIR}" "${shell_file}" 2>/dev/null; then
            log_info "Adding ${BIN_DIR} to PATH in ${shell_file} (${shell_name})..."
            echo "" >> "${shell_file}"
            echo "# Added by zed-ai-proxy installer" >> "${shell_file}"
            echo "export PATH=\"${BIN_DIR}:\$PATH\"" >> "${shell_file}"
        else
            log_info "PATH already configured in ${shell_file}"
        fi
    fi
}

# Bash
add_path_to_file "${HOME}/.bashrc" "bash"
if [ -f "${HOME}/.bash_profile" ]; then
    add_path_to_file "${HOME}/.bash_profile" "bash"
fi

# Zsh
add_path_to_file "${HOME}/.zshrc" "zsh"

# Fish
if command -v fish >/dev/null 2>&1; then
    fish -c "fish_add_path -g ${BIN_DIR}" 2>/dev/null || true
    FISH_CONFIG="${HOME}/.config/fish/config.fish"
    mkdir -p "${HOME}/.config/fish"
    if [ -f "${FISH_CONFIG}" ]; then
        if ! grep -q "${BIN_DIR}" "${FISH_CONFIG}" 2>/dev/null; then
            echo "" >> "${FISH_CONFIG}"
            echo "# Added by zed-ai-proxy installer" >> "${FISH_CONFIG}"
            echo "fish_add_path ${BIN_DIR}" >> "${FISH_CONFIG}"
        fi
    fi
    log_info "Configured PATH for fish shell"
fi

# 8. Install shell completions
log_info "Generating and installing shell autocompletions..."

# Bash completion
BASH_COMP_DIR="${HOME}/.local/share/bash-completion/completions"
mkdir -p "${BASH_COMP_DIR}"
"${BIN_DIR}/zed-proxy" completion bash > "${BASH_COMP_DIR}/zed-proxy" 2>/dev/null || true
ln -sf "${BASH_COMP_DIR}/zed-proxy" "${BASH_COMP_DIR}/zed-ai-proxy"

# Zsh completion
ZSH_COMP_DIR="${HOME}/.local/share/zsh/site-functions"
mkdir -p "${ZSH_COMP_DIR}"
"${BIN_DIR}/zed-proxy" completion zsh > "${ZSH_COMP_DIR}/_zed-proxy" 2>/dev/null || true
if [ -f "${HOME}/.zshrc" ] && ! grep -q "site-functions" "${HOME}/.zshrc" 2>/dev/null; then
    echo "fpath=(\"${ZSH_COMP_DIR}\" \$fpath)" >> "${HOME}/.zshrc"
fi

# Fish completion
FISH_COMP_DIR="${HOME}/.config/fish/completions"
mkdir -p "${FISH_COMP_DIR}"
"${BIN_DIR}/zed-proxy" completion fish > "${FISH_COMP_DIR}/zed-proxy.fish" 2>/dev/null || true
ln -sf "${FISH_COMP_DIR}/zed-proxy.fish" "${FISH_COMP_DIR}/zed-ai-proxy.fish"

log_success "Shell completions installed for bash, zsh, and fish."

# 9. Verification
log_info "Verifying global CLI command..."
if "${BIN_DIR}/zed-proxy" --help >/dev/null 2>&1; then
    log_success "Installation verified successfully!"
else
    log_error "Verification failed. Could not execute ${BIN_DIR}/zed-proxy"
    exit 1
fi

echo ""
echo "============================================================"
echo " zed-ai-proxy CLI installed successfully!"
echo "============================================================"
echo " Commands available in bash, zsh, and fish:"
echo "   zed-proxy start       - Start proxy server in foreground"
echo "   zed-proxy start -d    - Start proxy server as background daemon"
echo "   zed-proxy stop        - Stop running background daemon"
echo "   zed-proxy status      - Inspect account pool and health"
echo "   zed-proxy sync        - Extract & sync credentials from OS keychain"
echo "   zed-proxy refresh     - Hot-reload accounts without restart"
echo "   zed-proxy models      - List available models"
echo "   zed-proxy logs -f     - View and follow daemon logs"
echo ""
echo " Config file: ${CONFIG_DIR}/accounts.json"
echo " Binary path: ${BIN_DIR}/zed-proxy"
echo ""
echo " If '${BIN_DIR}' is not yet in your current terminal PATH, run:"
echo "   source ~/.bashrc   # for bash"
echo "   source ~/.zshrc    # for zsh"
echo "============================================================"
