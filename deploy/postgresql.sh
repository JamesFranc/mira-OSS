# deploy/postgresql.sh
# PostgreSQL service startup and schema deployment
# Source this file - do not execute directly
#
# Requires: lib/output.sh and lib/services.sh sourced first
# Requires: OS, DISTRO, CONFIG_*, LOUD_MODE variables set

# Validate required variables
: "${OS:?Error: OS must be set}"
: "${CONFIG_DB_PASSWORD:?Error: CONFIG_DB_PASSWORD must be set}"

# Set PostgreSQL paths for macOS (keg-only formula)
if [ "$OS" = "macos" ]; then
    # Homebrew postgresql@17 is keg-only - find the correct path
    if [ -d "/opt/homebrew/opt/postgresql@17/bin" ]; then
        PG_BIN="/opt/homebrew/opt/postgresql@17/bin"
    elif [ -d "/usr/local/opt/postgresql@17/bin" ]; then
        PG_BIN="/usr/local/opt/postgresql@17/bin"
    else
        print_error "PostgreSQL 17 not found in Homebrew. Run: brew install postgresql@17"
        exit 1
    fi
    PSQL_CMD="$PG_BIN/psql"
    PG_ISREADY_CMD="$PG_BIN/pg_isready"
fi

if [ "$OS" = "macos" ]; then
    print_header "Step 12: Starting Services"

    start_service valkey brew
    start_service postgresql@17 brew

    sleep 2
fi

# Wait for PostgreSQL to be ready to accept connections
echo -ne "${DIM}${ARROW}${RESET} Waiting for PostgreSQL to be ready... "
PG_READY=0
for i in {1..30}; do
    if [ "$OS" = "linux" ]; then
        # On Linux, check with pg_isready (Fedora PGDG uses /usr/pgsql-17/bin/)
        if sudo -u postgres pg_isready > /dev/null 2>&1 || \
           sudo -u postgres /usr/pgsql-17/bin/pg_isready > /dev/null 2>&1; then
            PG_READY=1
            break
        fi
    elif [ "$OS" = "macos" ]; then
        # On macOS, use the full path to pg_isready
        if $PG_ISREADY_CMD > /dev/null 2>&1; then
            PG_READY=1
            break
        fi
    fi
    sleep 1
done

if [ $PG_READY -eq 0 ]; then
    echo -e "${ERROR}"
    print_error "PostgreSQL did not become ready within 30 seconds"
    if [ "$OS" = "linux" ]; then
        if [ "$DISTRO" = "fedora" ]; then
            print_info "Check status: systemctl status postgresql-17"
            print_info "Check logs: journalctl -u postgresql-17 -n 50"
        else
            print_info "Check status: systemctl status postgresql"
            print_info "Check logs: journalctl -u postgresql -n 50"
        fi
    elif [ "$OS" = "macos" ]; then
        print_info "Check status: brew services list | grep postgresql"
        print_info "Check logs: brew services info postgresql@17"
    fi
    exit 1
fi
echo -e "${CHECKMARK} ${DIM}(ready after ${i}s)${RESET}"

print_header "Step 13: PostgreSQL Configuration"

# Run schema file - single source of truth for database structure
# Schema file creates: roles, database, extensions, tables, indexes, RLS policies
echo -ne "${DIM}${ARROW}${RESET} Running database schema (roles, tables, indexes, RLS)... "
SCHEMA_FILE="/opt/mira/app/deploy/mira_service_schema.sql"
if [ -f "$SCHEMA_FILE" ]; then
    if [ "$OS" = "linux" ]; then
        # Run as postgres superuser; schema handles CREATE DATABASE and \c
        if sudo -u postgres psql -f "$SCHEMA_FILE" > /dev/null 2>&1; then
            echo -e "${CHECKMARK}"
        else
            echo -e "${ERROR}"
            print_error "Failed to run schema file"
            exit 1
        fi
    elif [ "$OS" = "macos" ]; then
        # Use full path to psql for keg-only postgresql@17
        if $PSQL_CMD postgres -f "$SCHEMA_FILE" > /dev/null 2>&1; then
            echo -e "${CHECKMARK}"
        else
            echo -e "${ERROR}"
            print_error "Failed to run schema file"
            # Show actual error for debugging
            $PSQL_CMD postgres -f "$SCHEMA_FILE" 2>&1 | tail -10
            exit 1
        fi
    fi
else
    echo -e "${ERROR}"
    print_error "Schema file not found: $SCHEMA_FILE"
    exit 1
fi

# Configure LLM endpoints for offline mode (use local Ollama)
if [ "$CONFIG_OFFLINE_MODE" = "yes" ]; then
    echo -ne "${DIM}${ARROW}${RESET} Configuring LLM endpoints for offline mode (Ollama)... "
    OFFLINE_SQL="UPDATE account_tiers SET endpoint_url = 'http://localhost:11434/v1/chat/completions', model = 'qwen3:1.7b', api_key_name = NULL WHERE provider = 'generic'; UPDATE internal_llm SET endpoint_url = 'http://localhost:11434/v1/chat/completions', model = 'qwen3:1.7b', api_key_name = NULL WHERE endpoint_url LIKE 'https://%';"
    if [ "$OS" = "linux" ]; then
        if sudo -u postgres psql -d mira_service -c "$OFFLINE_SQL" > /dev/null 2>&1; then
            echo -e "${CHECKMARK}"
        else
            echo -e "${ERROR}"
            print_warning "Failed to configure offline mode - you may need to run manually"
        fi
    elif [ "$OS" = "macos" ]; then
        if $PSQL_CMD mira_service -c "$OFFLINE_SQL" > /dev/null 2>&1; then
            echo -e "${CHECKMARK}"
        else
            echo -e "${ERROR}"
            print_warning "Failed to configure offline mode - you may need to run manually"
        fi
    fi
fi

# Update PostgreSQL passwords if custom password was set
if [ "$CONFIG_DB_PASSWORD" != "changethisifdeployingpwd" ]; then
    echo -ne "${DIM}${ARROW}${RESET} Updating database passwords... "
    if [ "$OS" = "linux" ]; then
        sudo -u postgres psql -c "ALTER USER mira_admin WITH PASSWORD '${CONFIG_DB_PASSWORD}';" > /dev/null 2>&1 && \
        sudo -u postgres psql -c "ALTER USER mira_dbuser WITH PASSWORD '${CONFIG_DB_PASSWORD}';" > /dev/null 2>&1
    elif [ "$OS" = "macos" ]; then
        $PSQL_CMD postgres -c "ALTER USER mira_admin WITH PASSWORD '${CONFIG_DB_PASSWORD}';" > /dev/null 2>&1 && \
        $PSQL_CMD postgres -c "ALTER USER mira_dbuser WITH PASSWORD '${CONFIG_DB_PASSWORD}';" > /dev/null 2>&1
    fi
    if [ $? -eq 0 ]; then
        echo -e "${CHECKMARK}"
    else
        echo -e "${ERROR}"
        print_warning "Failed to update passwords - you may need to update manually"
    fi
fi

print_success "PostgreSQL configured"
