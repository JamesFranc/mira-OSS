# deploy/dependencies.sh
# System package installation and Ollama setup
# Source this file - do not execute directly
#
# Requires: lib/output.sh and lib/services.sh sourced first
# Requires: OS, DISTRO, CONFIG_OFFLINE_MODE, CONFIG_OLLAMA_MODEL, LOUD_MODE variables set
#
# Sets: PYTHON_VER

# Validate required variables
: "${OS:?Error: OS must be set}"
# DISTRO can be empty for macOS - use ? instead of :? to allow empty string
: "${DISTRO?Error: DISTRO must be set (can be empty for macOS)}"

print_header "Step 1: System Dependencies"

if [ "$OS" = "linux" ] && [ "$DISTRO" = "debian" ]; then
    # Debian/Ubuntu: Add PostgreSQL APT repository for PostgreSQL 17
    if [ ! -f /etc/apt/sources.list.d/pgdg.list ]; then
        run_with_status "Adding PostgreSQL APT repository" \
            bash -c 'sudo apt-get install -y ca-certificates wget > /dev/null 2>&1 && \
                     sudo install -d /usr/share/postgresql-common/pgdg && \
                     sudo wget -q -O /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc && \
                     echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" | sudo tee /etc/apt/sources.list.d/pgdg.list > /dev/null'
    fi

    # Detect Python version to use (newest available, 3.12+ required)
    PYTHON_VER=$(python3 --version 2>&1 | sed -n 's/Python \([0-9]*\.[0-9]*\).*/\1/p')

    if [ "$LOUD_MODE" = true ]; then
        print_step "Updating package lists..."
        sudo apt-get update
        print_step "Installing system packages (Python ${PYTHON_VER})..."
        sudo apt-get install -y \
            build-essential \
            python${PYTHON_VER}-venv \
            python${PYTHON_VER}-dev \
            libpq-dev \
            postgresql-server-dev-17 \
            unzip \
            wget \
            curl \
            postgresql-17 \
            postgresql-contrib \
            postgresql-17-pgvector \
            valkey \
            libatk1.0-0t64 \
            libatk-bridge2.0-0t64 \
            libatspi2.0-0t64 \
            libxcomposite1
    else
        # Silent mode with progress indicator
        (sudo apt-get update > /dev/null 2>&1) &
        show_progress $! "Updating package lists"

        (sudo apt-get install -y \
            build-essential python${PYTHON_VER}-venv python${PYTHON_VER}-dev libpq-dev \
            postgresql-server-dev-17 unzip wget curl postgresql-17 \
            postgresql-contrib postgresql-17-pgvector valkey \
            libatk1.0-0t64 libatk-bridge2.0-0t64 libatspi2.0-0t64 \
            libxcomposite1 > /dev/null 2>&1) &
        show_progress $! "Installing system packages (18 packages)"
    fi
elif [ "$OS" = "linux" ] && [ "$DISTRO" = "fedora" ]; then
    # Fedora/RHEL: Add PostgreSQL PGDG repository for PostgreSQL 17
    if ! rpm -q pgdg-fedora-repo-latest > /dev/null 2>&1 && ! rpm -q pgdg-redhat-repo-latest > /dev/null 2>&1; then
        if [ -f /etc/fedora-release ]; then
            run_with_status "Adding PostgreSQL PGDG repository" \
                sudo dnf install -y https://download.postgresql.org/pub/repos/yum/reporpms/F-$(rpm -E %fedora)-x86_64/pgdg-fedora-repo-latest.noarch.rpm
        else
            # RHEL/CentOS/Rocky/Alma
            run_with_status "Adding PostgreSQL PGDG repository" \
                sudo dnf install -y https://download.postgresql.org/pub/repos/yum/reporpms/EL-$(rpm -E %rhel)-x86_64/pgdg-redhat-repo-latest.noarch.rpm
        fi
    fi

    # Disable built-in PostgreSQL module to avoid conflicts
    run_quiet sudo dnf -qy module disable postgresql || true

    if [ "$LOUD_MODE" = true ]; then
        print_step "Updating package lists..."
        sudo dnf makecache
        print_step "Installing system packages..."
        sudo dnf install -y \
            @development-tools \
            python3-devel \
            python3-pip \
            libpq-devel \
            postgresql17-server \
            postgresql17-contrib \
            postgresql17-devel \
            pgvector_17 \
            unzip \
            wget \
            curl \
            valkey \
            atk \
            at-spi2-atk \
            at-spi2-core \
            libXcomposite
    else
        # Silent mode with progress indicator
        (sudo dnf makecache > /dev/null 2>&1) &
        show_progress $! "Updating package lists"

        (sudo dnf install -y \
            @development-tools python3-devel python3-pip libpq-devel \
            postgresql17-server postgresql17-contrib postgresql17-devel pgvector_17 \
            unzip wget curl valkey \
            atk at-spi2-atk at-spi2-core libXcomposite > /dev/null 2>&1) &
        show_progress $! "Installing system packages (17 packages)"
    fi

    # Initialize PostgreSQL database cluster if not already done
    if [ ! -d /var/lib/pgsql/17/data/base ]; then
        run_with_status "Initializing PostgreSQL database cluster" \
            sudo /usr/pgsql-17/bin/postgresql-17-setup initdb
    fi

    # Configure pg_hba.conf for password authentication (Fedora defaults to ident)
    PG_HBA="/var/lib/pgsql/17/data/pg_hba.conf"
    if [ -f "$PG_HBA" ]; then
        # Check if already configured for scram-sha-256/md5
        if ! grep -q "^local.*all.*all.*scram-sha-256" "$PG_HBA" 2>/dev/null; then
            run_with_status "Configuring PostgreSQL authentication (scram-sha-256)" \
                bash -c "sudo sed -i 's/^local.*all.*all.*ident/local   all             all                                     scram-sha-256/' $PG_HBA && \
                         sudo sed -i 's/^local.*all.*all.*peer/local   all             all                                     scram-sha-256/' $PG_HBA && \
                         sudo sed -i 's/^host.*all.*all.*127.0.0.1.*ident/host    all             all             127.0.0.1\\/32            scram-sha-256/' $PG_HBA && \
                         sudo sed -i 's/^host.*all.*all.*::1.*ident/host    all             all             ::1\\/128                 scram-sha-256/' $PG_HBA"
        fi
    fi

    # Enable and start PostgreSQL service
    run_with_status "Enabling PostgreSQL service" \
        sudo systemctl enable postgresql-17
    run_with_status "Starting PostgreSQL service" \
        sudo systemctl start postgresql-17

    # Enable and start Valkey service
    run_with_status "Enabling Valkey service" \
        sudo systemctl enable valkey
    run_with_status "Starting Valkey service" \
        sudo systemctl start valkey

    # Detect Python version after installation
    PYTHON_VER=$(python3 --version 2>&1 | sed -n 's/Python \([0-9]*\.[0-9]*\).*/\1/p')

elif [ "$OS" = "macos" ]; then
    # macOS Homebrew package installation
    # Check if Homebrew is installed
    echo -ne "${DIM}${ARROW}${RESET} Checking for Homebrew... "
    if ! command -v brew &> /dev/null; then
        echo -e "${ERROR}"
        print_error "Homebrew is not installed. Please install Homebrew first:"
        print_info "/bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
        exit 1
    fi
    echo -e "${CHECKMARK}"

    # Detect Homebrew Python version (prefer modern versions, ignore system Python)
    PYTHON_VER=""
    for ver in 3.13 3.12 3.11 3.10; do
        if [ -f "/opt/homebrew/bin/python${ver}" ] || [ -f "/usr/local/bin/python${ver}" ]; then
            PYTHON_VER="$ver"
            break
        fi
    done

    # Build package list - only include Python if not already installed
    BREW_PACKAGES="wget curl postgresql@17 pgvector valkey age sops"
    if [ -z "$PYTHON_VER" ]; then
        # No suitable Python found, install Python 3.13
        BREW_PACKAGES="python@3.13 $BREW_PACKAGES"
        PYTHON_VER="3.13"
    fi

    if [ "$LOUD_MODE" = true ]; then
        print_step "Updating Homebrew..."
        brew update
        print_step "Installing dependencies via Homebrew..."
        brew install $BREW_PACKAGES
    else
        (brew update > /dev/null 2>&1) &
        show_progress $! "Updating Homebrew"

        # Run brew install with error checking - don't suppress errors completely
        BREW_LOG=$(mktemp)
        if ! brew install $BREW_PACKAGES > "$BREW_LOG" 2>&1 & then
            show_progress $! "Installing dependencies via Homebrew"
        else
            show_progress $! "Installing dependencies via Homebrew"
        fi
        # Check if any critical packages failed
        if ! brew list postgresql@17 &>/dev/null; then
            cat "$BREW_LOG"
            rm -f "$BREW_LOG"
            print_error "Failed to install postgresql@17. Check Homebrew output above."
            exit 1
        fi
        rm -f "$BREW_LOG"
    fi

    print_info "Playwright will install its own browser dependencies"
fi

print_success "System dependencies installed"

# Install age and sops for secrets management (Linux only - macOS uses brew)
if [ "$OS" = "linux" ]; then
    print_header "Step 1c: Secrets Management Tools"

    # Install age if not present
    echo -ne "${DIM}${ARROW}${RESET} Checking for age... "
    if command -v age &> /dev/null; then
        AGE_VERSION=$(age --version 2>&1 | head -1)
        echo -e "${CHECKMARK} ${DIM}($AGE_VERSION)${RESET}"
    else
        echo -e "${DIM}(not found)${RESET}"
        AGE_VERSION="1.2.0"
        AGE_URL="https://github.com/FiloSottile/age/releases/download/v${AGE_VERSION}/age-v${AGE_VERSION}-linux-amd64.tar.gz"
        if [ "$LOUD_MODE" = true ]; then
            print_step "Installing age v${AGE_VERSION}..."
            wget -q -O /tmp/age.tar.gz "$AGE_URL"
            tar -xzf /tmp/age.tar.gz -C /tmp
            sudo mv /tmp/age/age /usr/local/bin/
            sudo mv /tmp/age/age-keygen /usr/local/bin/
            rm -rf /tmp/age /tmp/age.tar.gz
        else
            (wget -q -O /tmp/age.tar.gz "$AGE_URL" && \
             tar -xzf /tmp/age.tar.gz -C /tmp && \
             sudo mv /tmp/age/age /usr/local/bin/ && \
             sudo mv /tmp/age/age-keygen /usr/local/bin/ && \
             rm -rf /tmp/age /tmp/age.tar.gz) &
            show_progress $! "Installing age v${AGE_VERSION}"
        fi
    fi

    # Install sops if not present
    echo -ne "${DIM}${ARROW}${RESET} Checking for sops... "
    if command -v sops &> /dev/null; then
        SOPS_VERSION=$(sops --version 2>&1 | head -1)
        echo -e "${CHECKMARK} ${DIM}($SOPS_VERSION)${RESET}"
    else
        echo -e "${DIM}(not found)${RESET}"
        SOPS_VERSION="3.9.0"
        SOPS_URL="https://github.com/getsops/sops/releases/download/v${SOPS_VERSION}/sops-v${SOPS_VERSION}.linux.amd64"
        if [ "$LOUD_MODE" = true ]; then
            print_step "Installing sops v${SOPS_VERSION}..."
            sudo wget -q -O /usr/local/bin/sops "$SOPS_URL"
            sudo chmod +x /usr/local/bin/sops
        else
            (sudo wget -q -O /usr/local/bin/sops "$SOPS_URL" && \
             sudo chmod +x /usr/local/bin/sops) &
            show_progress $! "Installing sops v${SOPS_VERSION}"
        fi
    fi

    print_success "Secrets management tools installed"
fi

# Ollama setup (only for offline mode)
if [ "$CONFIG_OFFLINE_MODE" = "yes" ]; then
    print_header "Step 1b: Ollama Setup (Offline Mode)"

    # Install Ollama if not present
    echo -ne "${DIM}${ARROW}${RESET} Checking for Ollama... "
    if command -v ollama &> /dev/null; then
        echo -e "${CHECKMARK} ${DIM}(already installed)${RESET}"
        OLLAMA_INSTALLED=true
    else
        echo -e "${DIM}(not found)${RESET}"
        if [ "$LOUD_MODE" = true ]; then
            print_step "Installing Ollama..."
            if curl -fsSL https://ollama.com/install.sh | sh; then
                OLLAMA_INSTALLED=true
            else
                OLLAMA_INSTALLED=false
            fi
        else
            (curl -fsSL https://ollama.com/install.sh | sh > /dev/null 2>&1) &
            if show_progress $! "Installing Ollama"; then
                OLLAMA_INSTALLED=true
            else
                OLLAMA_INSTALLED=false
            fi
        fi
    fi

    if [ "$OLLAMA_INSTALLED" = true ]; then
        # Start Ollama server if not already running
        echo -ne "${DIM}${ARROW}${RESET} Checking Ollama server... "
        if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
            echo -e "${CHECKMARK} ${DIM}(already running)${RESET}"
        else
            echo -e "${DIM}(starting)${RESET}"
            # Start server based on OS/init system
            if [ "$OS" = "linux" ] && systemctl is-enabled ollama &>/dev/null 2>&1; then
                run_with_status "Starting Ollama service" \
                    sudo systemctl start ollama
            else
                # Start in background for macOS or non-systemd Linux
                ollama serve > /dev/null 2>&1 &
                OLLAMA_PID=$!
                print_info "Started Ollama server (PID $OLLAMA_PID)"
            fi

            # Wait for server to be ready
            echo -ne "${DIM}${ARROW}${RESET} Waiting for Ollama server... "
            OLLAMA_READY=0
            for i in {1..30}; do
                if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
                    OLLAMA_READY=1
                    break
                fi
                sleep 1
            done

            if [ $OLLAMA_READY -eq 1 ]; then
                echo -e "${CHECKMARK} ${DIM}(ready after ${i}s)${RESET}"
            else
                echo -e "${ERROR}"
                print_warning "Ollama server did not start within 30 seconds"
                print_info "Model pull will be skipped - you can pull manually later:"
                print_info "  ollama serve &"
                print_info "  ollama pull ${CONFIG_OLLAMA_MODEL}"
            fi
        fi

        # Pull the model if server is ready
        if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
            if [ "$LOUD_MODE" = true ]; then
                print_step "Pulling model ${CONFIG_OLLAMA_MODEL}..."
                if ollama pull "$CONFIG_OLLAMA_MODEL"; then
                    print_success "Model ${CONFIG_OLLAMA_MODEL} ready"
                else
                    print_warning "Could not pull model (network unavailable)"
                fi
            else
                (ollama pull "$CONFIG_OLLAMA_MODEL" > /dev/null 2>&1) &
                if show_progress $! "Pulling model ${CONFIG_OLLAMA_MODEL}"; then
                    print_success "Model ${CONFIG_OLLAMA_MODEL} ready"
                else
                    print_warning "Could not pull model (network unavailable)"
                    echo ""
                    print_info "For air-gapped installation, manually transfer the model:"
                    print_info "  1. On a connected machine: ollama pull ${CONFIG_OLLAMA_MODEL}"
                    print_info "  2. Export: ~/.ollama/models -> transfer to this machine"
                    print_info "  3. Or use: ollama create ${CONFIG_OLLAMA_MODEL} -f Modelfile"
                fi
            fi
        fi
    else
        print_warning "Could not install Ollama (network unavailable or blocked)"
        echo ""
        print_info "For air-gapped Ollama installation:"
        print_info "  1. Download Ollama binary from https://ollama.com/download"
        print_info "  2. Transfer and install manually"
        print_info "  3. Transfer model files to ~/.ollama/models"
        print_info "  4. Start Ollama: ollama serve"
    fi

    print_success "Ollama setup complete"
fi
