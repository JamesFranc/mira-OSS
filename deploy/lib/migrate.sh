# deploy/lib/migrate.sh
# Migration helper functions for MIRA upgrades
# Source this file - do not execute directly
#
# Requires: lib/output.sh, lib/services.sh sourced first
# Requires: OS, LOUD_MODE variables set

# ============================================================================
# Logging Functions
# ============================================================================
# Set up migration logging to capture all output

MIGRATION_LOG_FILE=""

setup_migration_logging() {
    local backup_dir="$1"
    MIGRATION_LOG_FILE="${backup_dir}/migration.log"

    # Create log file and write header
    cat > "$MIGRATION_LOG_FILE" << EOF
================================================================================
MIRA Migration Log
Started: $(date '+%Y-%m-%d %H:%M:%S %Z')
Host: $(hostname)
User: $(whoami)
OS: ${OS} ${DISTRO:-}
================================================================================

EOF

    # Tee all output to log file while preserving terminal output
    # Use file descriptor 3 to save original stdout
    exec 3>&1
    exec > >(tee -a "$MIGRATION_LOG_FILE")
    exec 2>&1

    print_info "Logging to: $MIGRATION_LOG_FILE"
}

finalize_migration_log() {
    local status="$1"

    # Append footer to log
    cat >> "$MIGRATION_LOG_FILE" << EOF

================================================================================
Migration ${status}
Completed: $(date '+%Y-%m-%d %H:%M:%S %Z')
================================================================================
EOF
}

# ============================================================================
# Dry-Run Mode Functions
# ============================================================================
# When DRY_RUN_MODE=true, show what would happen without making changes

# Check if we're in dry-run mode
is_dry_run() {
    [ "$DRY_RUN_MODE" = true ]
}

# Print dry-run notice for an action
dry_run_notice() {
    local action="$1"
    if is_dry_run; then
        echo -e "${CYAN}[DRY-RUN]${RESET} Would: $action"
        return 0
    fi
    return 1
}

# Skip destructive action in dry-run mode
dry_run_skip() {
    local action="$1"
    if is_dry_run; then
        echo -e "${CYAN}[DRY-RUN]${RESET} Skipping: $action"
        return 0
    fi
    return 1
}

# ============================================================================
# Backup Verification Functions
# ============================================================================
# Verify backups are valid before proceeding with destructive operations

verify_postgresql_backup() {
    local backup_file="$1"

    echo -ne "${DIM}${ARROW}${RESET} Verifying PostgreSQL backup integrity... "

    if [ ! -f "$backup_file" ]; then
        echo -e "${ERROR}"
        print_error "Backup file not found: $backup_file"
        return 1
    fi

    # Check file size (should be non-zero)
    local file_size
    file_size=$(stat -f%z "$backup_file" 2>/dev/null || stat -c%s "$backup_file" 2>/dev/null)
    if [ "$file_size" -eq 0 ]; then
        echo -e "${ERROR}"
        print_error "Backup file is empty"
        return 1
    fi

    # Verify pg_dump format is readable with pg_restore --list
    if ! pg_restore --list "$backup_file" > /dev/null 2>&1; then
        echo -e "${ERROR}"
        print_error "Backup file is corrupted or unreadable"
        return 1
    fi

    # Count objects in backup
    local object_count
    object_count=$(pg_restore --list "$backup_file" 2>/dev/null | grep -c "^[0-9]" || echo "0")

    local size_human
    if [ "$file_size" -gt 1048576 ]; then
        size_human="$((file_size / 1048576)) MB"
    else
        size_human="$((file_size / 1024)) KB"
    fi

    echo -e "${CHECKMARK} ${DIM}($size_human, $object_count objects)${RESET}"
    return 0
}

verify_secrets_backup() {
    local backup_dir="$1"

    echo -ne "${DIM}${ARROW}${RESET} Verifying secrets backup integrity... "

    # Check for secrets.enc.yaml backup
    if [ ! -f "${backup_dir}/secrets.enc.yaml" ]; then
        echo -e "${WARNING}"
        print_warning "No secrets backup found"
        return 0
    fi

    # Verify it's a valid SOPS-encrypted file (has sops metadata)
    if ! grep -q "sops:" "${backup_dir}/secrets.enc.yaml" 2>/dev/null; then
        echo -e "${ERROR}"
        print_error "Secrets backup is not a valid SOPS-encrypted file"
        return 1
    fi

    # Check for age key backup
    if [ ! -f "${backup_dir}/age.key" ]; then
        echo -e "${WARNING}"
        print_warning "Age key not backed up - secrets may not be decryptable"
    fi

    echo -e "${CHECKMARK}"
    return 0
}

verify_database_backup() {
    local backup_dir="$1"

    echo -ne "${DIM}${ARROW}${RESET} Verifying database snapshot integrity... "

    local snapshot_file="${backup_dir}/db_snapshot.json"

    if [ ! -f "$snapshot_file" ]; then
        echo -e "${ERROR}"
        print_error "Database snapshot not found"
        return 1
    fi

    # Verify it's valid JSON with expected structure
    if ! jq -e '.row_counts and .structural_data' "$snapshot_file" > /dev/null 2>&1; then
        echo -e "${ERROR}"
        print_error "Database snapshot is malformed"
        return 1
    fi

    # Verify checksum file exists
    if [ ! -f "${snapshot_file}.sha256" ]; then
        echo -e "${WARNING}"
        print_warning "Checksum file missing (verification may be less reliable)"
    fi

    local table_count
    table_count=$(jq '.row_counts | keys | length' "$snapshot_file" 2>/dev/null || echo "0")

    echo -e "${CHECKMARK} ${DIM}($table_count tables captured)${RESET}"
    return 0
}

# Master verification function - verify all backups before destructive ops
verify_all_backups() {
    print_header "Verifying Backup Integrity"

    local all_valid=true

    verify_postgresql_backup "${BACKUP_DIR}/postgresql_backup.dump" || all_valid=false
    verify_secrets_backup "$BACKUP_DIR" || all_valid=false
    verify_database_backup "$BACKUP_DIR" || all_valid=false

    if [ "$all_valid" = true ]; then
        print_success "All backups verified successfully"
        return 0
    else
        print_error "Backup verification failed - aborting migration"
        print_info "Fix backup issues before proceeding with destructive operations"
        return 1
    fi
}

# ============================================================================
# Secrets Snapshot Functions (Critical for Data Integrity)
# ============================================================================
# These functions capture the SOPS secrets state and verify it after restore.

# Capture secrets snapshot for comparison
capture_secrets_snapshot() {
    local snapshot_file="$1"

    echo -ne "${DIM}${ARROW}${RESET} Capturing secrets snapshot... "

    local secrets_file="/opt/mira/app/secrets.enc.yaml"
    local age_key_file="$HOME/.config/mira/age.key"

    if [ ! -f "$secrets_file" ]; then
        echo -e "${WARNING}"
        print_warning "No secrets.enc.yaml found"
        echo '{"_empty": true}' > "$snapshot_file"
        return 0
    fi

    # Decrypt and capture secrets structure (keys only, not values for security)
    export SOPS_AGE_KEY_FILE="$age_key_file"
    if sops -d "$secrets_file" 2>/dev/null | yq -o=json 'keys' > "$snapshot_file" 2>/dev/null; then
        echo -e "${CHECKMARK}"
    else
        print_error "Cannot decrypt secrets for snapshot"
        return 1
    fi

    return 0
}

# Verify secrets file exists and is decryptable
verify_secrets_restored() {
    local backup_dir="$1"

    echo -ne "${DIM}${ARROW}${RESET} Verifying secrets integrity... "

    local secrets_file="/opt/mira/app/secrets.enc.yaml"
    local age_key_file="$HOME/.config/mira/age.key"

    if [ ! -f "$secrets_file" ]; then
        echo -e "${ERROR}"
        print_error "Secrets file not found at $secrets_file"
        return 1
    fi

    if [ ! -f "$age_key_file" ]; then
        echo -e "${ERROR}"
        print_error "Age key not found at $age_key_file"
        return 1
    fi

    # Try to decrypt and verify structure
    export SOPS_AGE_KEY_FILE="$age_key_file"
    if sops -d "$secrets_file" 2>/dev/null | grep -q "providers:" 2>/dev/null; then
        echo -e "${CHECKMARK}"
        return 0
    else
        echo -e "${ERROR}"
        print_error "Cannot decrypt secrets file or invalid structure"
        return 1
    fi
}

# ============================================================================
# Database Snapshot Functions (Critical for Data Integrity)
# ============================================================================
# These functions capture structural database state and verify after restore.
# We compare structural data (users, relationships) but ignore timestamps.

# Tables to snapshot with their structural columns (excluding timestamps)
# Format: "table:col1,col2,col3"
DB_STRUCTURAL_TABLES=(
    "users:id,email,first_name,last_name,timezone,llm_tier,max_tier,is_active,memory_manipulation_enabled,cumulative_activity_days"
    "continuums:id,user_id,last_message_position"
    "api_tokens:id,user_id,name,token_hash"
    "account_tiers:name,model,provider,endpoint_url,api_key_name,thinking_budget"
    "domain_knowledge_blocks:id,user_id,domain_label,domain_name,enabled"
    "entities:id,user_id,name,entity_type"
)

# Tables where we only verify counts (too large or timestamps dominate)
DB_COUNT_ONLY_TABLES=(
    "messages"
    "memories"
    "user_activity_days"
)

# Helper: Run psql query based on OS
run_psql() {
    local query="$1"
    if [ "$OS" = "linux" ]; then
        sudo -u postgres psql -d mira_service -tAc "$query" 2>/dev/null
    else
        psql -U mira_admin -h localhost -d mira_service -tAc "$query" 2>/dev/null
    fi
}

# Capture database snapshot to JSON file
capture_database_snapshot() {
    local snapshot_file="$1"

    echo -ne "${DIM}${ARROW}${RESET} Capturing database snapshot... "

    # Start JSON
    echo "{" > "$snapshot_file"
    echo '  "row_counts": {' >> "$snapshot_file"

    # Capture row counts for ALL user data tables
    local all_tables="users continuums messages memories entities api_tokens user_activity_days domain_knowledge_blocks domain_knowledge_block_content account_tiers"
    local first=true

    for table in $all_tables; do
        local count
        count=$(run_psql "SELECT COUNT(*) FROM $table" 2>/dev/null || echo "0")
        count=$(echo "$count" | tr -d ' \n')

        if [ "$first" = true ]; then
            first=false
        else
            echo "," >> "$snapshot_file"
        fi
        echo -n "    \"$table\": $count" >> "$snapshot_file"
    done

    echo "" >> "$snapshot_file"
    echo "  }," >> "$snapshot_file"

    # Capture structural data for key tables
    echo '  "structural_data": {' >> "$snapshot_file"

    first=true
    for table_spec in "${DB_STRUCTURAL_TABLES[@]}"; do
        local table="${table_spec%%:*}"
        local columns="${table_spec#*:}"

        # Query structural columns, order by primary key for consistent comparison
        local data
        data=$(run_psql "SELECT json_agg(row_to_json(t) ORDER BY id) FROM (SELECT $columns FROM $table ORDER BY id) t" 2>/dev/null || echo "[]")

        # Handle null result
        if [ -z "$data" ] || [ "$data" = "" ]; then
            data="[]"
        fi

        if [ "$first" = true ]; then
            first=false
        else
            echo "," >> "$snapshot_file"
        fi
        echo "    \"$table\": $data" >> "$snapshot_file"
    done

    echo "" >> "$snapshot_file"
    echo "  }," >> "$snapshot_file"

    # Capture sample IDs from large tables for spot-check verification
    echo '  "sample_ids": {' >> "$snapshot_file"

    first=true
    for table in "${DB_COUNT_ONLY_TABLES[@]}"; do
        # Get first 10 and last 10 IDs for verification
        local first_ids last_ids
        first_ids=$(run_psql "SELECT json_agg(id) FROM (SELECT id FROM $table ORDER BY created_at ASC LIMIT 10) t" 2>/dev/null || echo "[]")
        last_ids=$(run_psql "SELECT json_agg(id) FROM (SELECT id FROM $table ORDER BY created_at DESC LIMIT 10) t" 2>/dev/null || echo "[]")

        [ -z "$first_ids" ] || [ "$first_ids" = "" ] && first_ids="[]"
        [ -z "$last_ids" ] || [ "$last_ids" = "" ] && last_ids="[]"

        if [ "$first" = true ]; then
            first=false
        else
            echo "," >> "$snapshot_file"
        fi
        echo "    \"${table}\": {\"first\": $first_ids, \"last\": $last_ids}" >> "$snapshot_file"
    done

    echo "" >> "$snapshot_file"
    echo "  }" >> "$snapshot_file"
    echo "}" >> "$snapshot_file"

    # Calculate checksum
    local checksum
    if command -v sha256sum &> /dev/null; then
        checksum=$(jq -cS '.' "$snapshot_file" 2>/dev/null | sha256sum | cut -d' ' -f1)
    else
        checksum=$(jq -cS '.' "$snapshot_file" 2>/dev/null | shasum -a 256 | cut -d' ' -f1)
    fi
    echo "$checksum" > "${snapshot_file}.sha256"

    local table_count=$(echo "$all_tables" | wc -w | tr -d ' ')
    echo -e "${CHECKMARK} ${DIM}($table_count tables, checksum: ${checksum:0:12}...)${RESET}"
    return 0
}

# Verify database matches pre-migration snapshot
# Shows colored diff and requires confirmation for each discrepancy
verify_database_snapshot() {
    local original_snapshot="$1"
    local current_snapshot="${BACKUP_DIR}/db_current_snapshot.json"

    echo -ne "${DIM}${ARROW}${RESET} Verifying database integrity... "

    # Capture current state
    capture_database_snapshot "$current_snapshot" > /dev/null 2>&1 || {
        echo -e "${ERROR}"
        print_error "Failed to capture current database state"
        return 1
    }

    # Compare row counts first
    local original_counts current_counts
    original_counts=$(jq -c '.row_counts' "$original_snapshot" 2>/dev/null)
    current_counts=$(jq -c '.row_counts' "$current_snapshot" 2>/dev/null)

    if [ "$original_counts" = "$current_counts" ]; then
        # Quick check structural data
        local original_struct current_struct
        original_struct=$(jq -cS '.structural_data' "$original_snapshot" 2>/dev/null)
        current_struct=$(jq -cS '.structural_data' "$current_snapshot" 2>/dev/null)

        if [ "$original_struct" = "$current_struct" ]; then
            echo -e "${CHECKMARK} ${DIM}(all counts and structures match)${RESET}"
            return 0
        fi
    fi

    # Differences detected - show detailed comparison
    echo -e "${WARNING}"
    echo ""
    echo -e "${BOLD}${YELLOW}════════════════════════════════════════════════════════════${RESET}"
    echo -e "${BOLD}${YELLOW}  DATABASE INTEGRITY CHECK: Differences Detected${RESET}"
    echo -e "${BOLD}${YELLOW}════════════════════════════════════════════════════════════${RESET}"
    echo ""

    local has_differences=false
    local user_confirmed_all=true

    # Compare row counts
    echo -e "${BOLD}Row Count Comparison:${RESET}"
    echo ""

    local tables
    tables=$(jq -r '.row_counts | keys[]' "$original_snapshot" 2>/dev/null)

    for table in $tables; do
        local orig_count curr_count
        orig_count=$(jq -r ".row_counts.\"$table\"" "$original_snapshot" 2>/dev/null)
        curr_count=$(jq -r ".row_counts.\"$table\"" "$current_snapshot" 2>/dev/null)

        if [ "$orig_count" != "$curr_count" ]; then
            has_differences=true
            local diff=$((curr_count - orig_count))
            local color="$YELLOW"
            local symbol="~"

            if [ "$diff" -lt 0 ]; then
                color="$RED"
                symbol="-"
                echo -e "${color}  ${symbol} ${table}: ${orig_count} → ${curr_count} (${diff} rows LOST)${RESET}"
            else
                color="$GREEN"
                symbol="+"
                echo -e "${color}  ${symbol} ${table}: ${orig_count} → ${curr_count} (+${diff} rows)${RESET}"
            fi
        else
            echo -e "${DIM}  ✓ ${table}: ${orig_count} rows${RESET}"
        fi
    done
    echo ""

    # If row counts differ, require confirmation
    if [ "$has_differences" = true ]; then
        # Check if any critical data was lost
        local users_orig users_curr
        users_orig=$(jq -r '.row_counts.users' "$original_snapshot" 2>/dev/null)
        users_curr=$(jq -r '.row_counts.users' "$current_snapshot" 2>/dev/null)

        if [ "$users_curr" -lt "$users_orig" ]; then
            echo -e "${BOLD}${RED}╔══ CRITICAL: USER DATA LOSS DETECTED ══╗${RESET}"
            echo -e "${RED}  Original users: $users_orig${RESET}"
            echo -e "${RED}  Current users:  $users_curr${RESET}"
            echo -e "${RED}  LOST: $((users_orig - users_curr)) user accounts${RESET}"
            echo -e "${RED}╚════════════════════════════════════════╝${RESET}"
            echo ""

            read -p "$(echo -e ${YELLOW}CRITICAL: Acknowledge USER DATA LOSS and continue?${RESET}) (yes/no): " confirm
            if [ "$confirm" != "yes" ]; then
                echo -e "${RED}Migration aborted by user.${RESET}"
                user_confirmed_all=false
            fi
            echo ""
        fi

        local msgs_orig msgs_curr
        msgs_orig=$(jq -r '.row_counts.messages' "$original_snapshot" 2>/dev/null)
        msgs_curr=$(jq -r '.row_counts.messages' "$current_snapshot" 2>/dev/null)

        if [ "$msgs_curr" -lt "$msgs_orig" ] && [ "$user_confirmed_all" = true ]; then
            local lost=$((msgs_orig - msgs_curr))
            echo -e "${BOLD}${RED}╔══ MESSAGE LOSS DETECTED ══╗${RESET}"
            echo -e "${RED}  Original: $msgs_orig messages${RESET}"
            echo -e "${RED}  Current:  $msgs_curr messages${RESET}"
            echo -e "${RED}  LOST: $lost messages${RESET}"
            echo -e "${RED}╚═══════════════════════════╝${RESET}"
            echo ""

            read -p "$(echo -e ${YELLOW}Acknowledge message loss and continue?${RESET}) (yes/no): " confirm
            if [ "$confirm" != "yes" ]; then
                echo -e "${RED}Migration aborted by user.${RESET}"
                user_confirmed_all=false
            fi
            echo ""
        fi

        local mems_orig mems_curr
        mems_orig=$(jq -r '.row_counts.memories' "$original_snapshot" 2>/dev/null)
        mems_curr=$(jq -r '.row_counts.memories' "$current_snapshot" 2>/dev/null)

        if [ "$mems_curr" -lt "$mems_orig" ] && [ "$user_confirmed_all" = true ]; then
            local lost=$((mems_orig - mems_curr))
            echo -e "${BOLD}${RED}╔══ MEMORY LOSS DETECTED ══╗${RESET}"
            echo -e "${RED}  Original: $mems_orig memories${RESET}"
            echo -e "${RED}  Current:  $mems_curr memories${RESET}"
            echo -e "${RED}  LOST: $lost memories${RESET}"
            echo -e "${RED}╚══════════════════════════╝${RESET}"
            echo ""

            read -p "$(echo -e ${YELLOW}Acknowledge memory loss and continue?${RESET}) (yes/no): " confirm
            if [ "$confirm" != "yes" ]; then
                echo -e "${RED}Migration aborted by user.${RESET}"
                user_confirmed_all=false
            fi
            echo ""
        fi
    fi

    # Compare structural data (user details, etc.)
    if [ "$user_confirmed_all" = true ]; then
        echo -e "${BOLD}Structural Data Comparison:${RESET}"
        echo ""

        for table_spec in "${DB_STRUCTURAL_TABLES[@]}"; do
            local table="${table_spec%%:*}"

            local orig_data curr_data
            orig_data=$(jq -cS ".structural_data.\"$table\"" "$original_snapshot" 2>/dev/null)
            curr_data=$(jq -cS ".structural_data.\"$table\"" "$current_snapshot" 2>/dev/null)

            if [ "$orig_data" != "$curr_data" ]; then
                has_differences=true

                echo -e "${BOLD}${YELLOW}╔══ STRUCTURAL DIFFERENCES: ${table} ══╗${RESET}"

                # For users table, show specific field differences
                if [ "$table" = "users" ]; then
                    local orig_emails curr_emails
                    orig_emails=$(echo "$orig_data" | jq -r '.[].email' 2>/dev/null | sort)
                    curr_emails=$(echo "$curr_data" | jq -r '.[].email' 2>/dev/null | sort)

                    # Missing users
                    local missing_users
                    missing_users=$(comm -23 <(echo "$orig_emails") <(echo "$curr_emails") 2>/dev/null)
                    if [ -n "$missing_users" ]; then
                        echo "$missing_users" | while read email; do
                            [ -z "$email" ] && continue
                            echo -e "${RED}  - User MISSING: ${email}${RESET}"
                        done
                    fi

                    # New users
                    local new_users
                    new_users=$(comm -13 <(echo "$orig_emails") <(echo "$curr_emails") 2>/dev/null)
                    if [ -n "$new_users" ]; then
                        echo "$new_users" | while read email; do
                            [ -z "$email" ] && continue
                            echo -e "${GREEN}  + User ADDED: ${email}${RESET}"
                        done
                    fi

                    # Changed user data (same email, different fields)
                    local common_emails
                    common_emails=$(comm -12 <(echo "$orig_emails") <(echo "$curr_emails") 2>/dev/null)
                    for email in $common_emails; do
                        [ -z "$email" ] && continue
                        local orig_user curr_user
                        orig_user=$(echo "$orig_data" | jq -c ".[] | select(.email == \"$email\")" 2>/dev/null)
                        curr_user=$(echo "$curr_data" | jq -c ".[] | select(.email == \"$email\")" 2>/dev/null)

                        if [ "$orig_user" != "$curr_user" ]; then
                            echo -e "${YELLOW}  ~ User CHANGED: ${email}${RESET}"
                            # Show which fields changed
                            local fields="timezone llm_tier max_tier first_name last_name"
                            for field in $fields; do
                                local orig_val curr_val
                                orig_val=$(echo "$orig_user" | jq -r ".$field // empty" 2>/dev/null)
                                curr_val=$(echo "$curr_user" | jq -r ".$field // empty" 2>/dev/null)
                                if [ "$orig_val" != "$curr_val" ]; then
                                    echo -e "${DIM}${YELLOW}      $field: \"$orig_val\" → \"$curr_val\"${RESET}"
                                fi
                            done
                        fi
                    done
                else
                    # Generic table diff - just show counts changed
                    local orig_count curr_count
                    orig_count=$(echo "$orig_data" | jq 'length' 2>/dev/null || echo "0")
                    curr_count=$(echo "$curr_data" | jq 'length' 2>/dev/null || echo "0")
                    echo -e "${YELLOW}  Rows: $orig_count → $curr_count${RESET}"
                fi

                echo -e "${YELLOW}╚══════════════════════════════════════════════════════════════╝${RESET}"
                echo ""

                read -p "$(echo -e ${YELLOW}Acknowledge changes to ${table} and continue?${RESET}) (yes/no): " confirm
                if [ "$confirm" != "yes" ]; then
                    echo -e "${RED}Migration aborted by user.${RESET}"
                    user_confirmed_all=false
                    break
                fi
                echo ""
            else
                echo -e "${DIM}  ✓ ${table}: structural data matches${RESET}"
            fi
        done
    fi

    # Final verdict
    if [ "$user_confirmed_all" = false ]; then
        echo ""
        print_error "Migration aborted due to unconfirmed database differences"
        print_info "Original snapshot: $original_snapshot"
        print_info "Database backup: ${BACKUP_DIR}/postgresql_backup.dump"
        return 1
    fi

    if [ "$has_differences" = true ]; then
        echo ""
        echo -e "${BOLD}${GREEN}════════════════════════════════════════════════════════════${RESET}"
        echo -e "${GREEN}  All database differences acknowledged by user${RESET}"
        echo -e "${BOLD}${GREEN}════════════════════════════════════════════════════════════${RESET}"
        echo ""
    fi

    return 0
}

# ============================================================================
# Pre-flight Validation Functions
# ============================================================================

# Check for existing MIRA installation
migrate_check_existing_install() {
    echo -ne "${DIM}${ARROW}${RESET} Checking existing installation... "

    if [ ! -d "/opt/mira/app" ]; then
        echo -e "${ERROR}"
        print_error "No MIRA installation found at /opt/mira/app"
        print_info "Use 'deploy.sh' without --migrate for fresh installation"
        return 1
    fi

    if [ ! -f "/opt/mira/app/talkto_mira.py" ]; then
        echo -e "${ERROR}"
        print_error "Invalid MIRA installation (missing talkto_mira.py)"
        return 1
    fi

    echo -e "${CHECKMARK}"
    return 0
}

# Check PostgreSQL connectivity
migrate_check_postgresql_running() {
    echo -ne "${DIM}${ARROW}${RESET} Checking PostgreSQL connectivity... "

    if [ "$OS" = "linux" ]; then
        if ! sudo -u postgres psql -d mira_service -c "SELECT 1" > /dev/null 2>&1; then
            echo -e "${ERROR}"
            print_error "Cannot connect to PostgreSQL mira_service database"
            print_info "Ensure PostgreSQL is running and database exists"
            return 1
        fi
    elif [ "$OS" = "macos" ]; then
        if ! psql -U mira_admin -h localhost -d mira_service -c "SELECT 1" > /dev/null 2>&1; then
            echo -e "${ERROR}"
            print_error "Cannot connect to PostgreSQL as mira_admin"
            print_info "Ensure PostgreSQL is running and mira_service database exists"
            return 1
        fi
    fi

    echo -e "${CHECKMARK}"
    return 0
}

# Check secrets accessibility
migrate_check_secrets_accessible() {
    echo -ne "${DIM}${ARROW}${RESET} Checking secrets accessibility... "

    local secrets_file="/opt/mira/app/secrets.enc.yaml"
    local age_key_file="$HOME/.config/mira/age.key"

    # Check secrets file exists
    if [ ! -f "$secrets_file" ]; then
        echo -e "${ERROR}"
        print_error "Secrets file not found at $secrets_file"
        return 1
    fi

    # Check age key exists
    if [ ! -f "$age_key_file" ]; then
        echo -e "${ERROR}"
        print_error "Age key not found at $age_key_file"
        print_info "Cannot decrypt secrets without age key"
        return 1
    fi

    # Verify we can decrypt secrets
    export SOPS_AGE_KEY_FILE="$age_key_file"
    if ! sops -d "$secrets_file" > /dev/null 2>&1; then
        echo -e "${ERROR}"
        print_error "Cannot decrypt secrets file"
        return 1
    fi

    echo -e "${CHECKMARK}"
    return 0
}

# Check disk space for backup
migrate_check_disk_space() {
    echo -ne "${DIM}${ARROW}${RESET} Checking disk space... "

    # Get PostgreSQL database size in bytes
    local pg_size
    if [ "$OS" = "linux" ]; then
        pg_size=$(sudo -u postgres psql -d mira_service -tAc "SELECT pg_database_size('mira_service')" 2>/dev/null || echo "0")
    else
        pg_size=$(psql -U mira_admin -h localhost -d mira_service -tAc "SELECT pg_database_size('mira_service')" 2>/dev/null || echo "0")
    fi

    # Get user data size in KB
    local user_size_kb
    user_size_kb=$(du -sk /opt/mira/app/data/users 2>/dev/null | cut -f1 || echo "0")

    # Get MIRA app size in KB (for backup)
    local app_size_kb
    app_size_kb=$(du -sk /opt/mira/app 2>/dev/null | cut -f1 || echo "0")

    # Calculate total needed (convert pg_size from bytes to KB)
    local pg_size_kb=$((pg_size / 1024))
    local total_kb=$((pg_size_kb + user_size_kb + app_size_kb))
    local required_kb=$((total_kb * 2))  # 2x for backup headroom

    # Get available space at /opt
    local available_kb
    if [ "$OS" = "macos" ]; then
        available_kb=$(df -k /opt 2>/dev/null | tail -1 | awk '{print $4}' || df -k / | tail -1 | awk '{print $4}')
    else
        available_kb=$(df -k /opt 2>/dev/null | tail -1 | awk '{print $4}')
    fi

    if [ "$available_kb" -lt "$required_kb" ]; then
        echo -e "${ERROR}"
        print_error "Insufficient disk space"
        print_info "Required: $((required_kb / 1024)) MB, Available: $((available_kb / 1024)) MB"
        return 1
    fi

    echo -e "${CHECKMARK} ${DIM}($((required_kb / 1024)) MB needed, $((available_kb / 1024)) MB available)${RESET}"
    return 0
}

# Check for active database sessions
migrate_check_no_active_sessions() {
    echo -ne "${DIM}${ARROW}${RESET} Checking for active sessions... "

    local active
    if [ "$OS" = "linux" ]; then
        active=$(sudo -u postgres psql -d mira_service -tAc \
            "SELECT COUNT(*) FROM pg_stat_activity WHERE datname='mira_service' AND state='active'" 2>/dev/null || echo "0")
    else
        active=$(psql -U mira_admin -h localhost -d mira_service -tAc \
            "SELECT COUNT(*) FROM pg_stat_activity WHERE datname='mira_service' AND state='active'" 2>/dev/null || echo "0")
    fi

    if [ "$active" -gt 1 ]; then
        echo -e "${WARNING}"
        print_warning "Active database connections detected ($active)"
        print_info "Ensure MIRA application is stopped before migration"
        # Continue - just warn, don't fail
    else
        echo -e "${CHECKMARK}"
    fi

    return 0
}

# ============================================================================
# Backup Functions
# ============================================================================

# Backup PostgreSQL user data tables
backup_postgresql_data() {
    local backup_file="${BACKUP_DIR}/postgresql_backup.dump"

    echo -ne "${DIM}${ARROW}${RESET} Backing up PostgreSQL data... "

    # Tables to backup (user data only, not system config)
    local tables="users continuums messages memories entities user_activity_days domain_knowledge_blocks domain_knowledge_block_content api_tokens extraction_batches post_processing_batches"

    # Build table arguments for pg_dump
    local table_args=""
    for table in $tables; do
        table_args="$table_args --table=$table"
    done

    if [ "$OS" = "linux" ]; then
        if sudo -u postgres pg_dump -d mira_service \
            --format=custom \
            --no-owner \
            --no-privileges \
            --data-only \
            $table_args \
            --file="$backup_file" 2>/dev/null; then
            echo -e "${CHECKMARK}"
        else
            echo -e "${ERROR}"
            print_error "Failed to backup PostgreSQL data"
            return 1
        fi
    else
        # Extract database password from SOPS secrets for authentication
        local db_password=""
        local secrets_file="/opt/mira/app/secrets.enc.yaml"
        local age_key_file="$HOME/.config/mira/age.key"
        if [ -f "$secrets_file" ] && [ -f "$age_key_file" ]; then
            export SOPS_AGE_KEY_FILE="$age_key_file"
            db_password=$(sops -d "$secrets_file" 2>/dev/null | yq '.database.password // empty' 2>/dev/null || echo "")
        fi

        # Use PGPASSWORD for authentication (avoids interactive prompt)
        if PGPASSWORD="$db_password" pg_dump -U mira_admin -h localhost -d mira_service \
            --format=custom \
            --no-owner \
            --no-privileges \
            --data-only \
            $table_args \
            --file="$backup_file" 2>/dev/null; then
            echo -e "${CHECKMARK}"
        else
            echo -e "${ERROR}"
            print_error "Failed to backup PostgreSQL data"
            if [ -z "$db_password" ]; then
                print_info "Database password not found in secrets"
            fi
            return 1
        fi
    fi

    # Record backup size
    local backup_size=$(du -sh "$backup_file" | cut -f1)
    print_info "Database backup: $backup_size"

    return 0
}

# Backup SOPS secrets file and age key
backup_secrets() {
    echo -ne "${DIM}${ARROW}${RESET} Backing up secrets... "

    local secrets_file="/opt/mira/app/secrets.enc.yaml"
    local age_key_file="$HOME/.config/mira/age.key"
    local sops_config="/opt/mira/app/.sops.yaml"

    local success=true

    # Backup encrypted secrets file
    if [ -f "$secrets_file" ]; then
        cp "$secrets_file" "${BACKUP_DIR}/secrets.enc.yaml"
        chmod 600 "${BACKUP_DIR}/secrets.enc.yaml"
    else
        echo -e "${WARNING}"
        print_warning "No secrets.enc.yaml found"
        success=false
    fi

    # Backup age key (critical for decryption)
    if [ -f "$age_key_file" ]; then
        cp "$age_key_file" "${BACKUP_DIR}/age.key"
        chmod 600 "${BACKUP_DIR}/age.key"
    else
        echo -e "${ERROR}"
        print_error "Age key not found - secrets cannot be restored without it"
        success=false
    fi

    # Backup SOPS config
    if [ -f "$sops_config" ]; then
        cp "$sops_config" "${BACKUP_DIR}/.sops.yaml"
    fi

    if [ "$success" = true ]; then
        echo -e "${CHECKMARK}"
    else
        return 1
    fi

    return 0
}

# Backup user data files (SQLite DBs and tool data)
backup_user_data_files() {
    local source_dir="/opt/mira/app/data/users"
    local backup_dir="${BACKUP_DIR}/user_data"

    echo -ne "${DIM}${ARROW}${RESET} Backing up user data files... "

    if [ ! -d "$source_dir" ]; then
        echo -e "${CHECKMARK} ${DIM}(no user data directory)${RESET}"
        return 0
    fi

    mkdir -p "$backup_dir"

    if cp -a "$source_dir"/* "$backup_dir/" 2>/dev/null; then
        local user_count=$(ls -d "$backup_dir"/*/ 2>/dev/null | wc -l | tr -d ' ')
        echo -e "${CHECKMARK} ${DIM}($user_count user directories)${RESET}"
    else
        echo -e "${CHECKMARK} ${DIM}(empty)${RESET}"
    fi

    return 0
}

# Create backup manifest with metadata
create_backup_manifest() {
    echo -ne "${DIM}${ARROW}${RESET} Creating backup manifest... "

    local mira_version
    mira_version=$(cat /opt/mira/app/VERSION 2>/dev/null || echo "unknown")

    local pg_version
    pg_version=$(psql --version 2>/dev/null | head -1 || echo "unknown")

    local sops_version
    sops_version=$(sops --version 2>/dev/null | head -1 || echo "unknown")

    cat > "${BACKUP_DIR}/manifest.json" <<EOF
{
    "backup_timestamp": "${BACKUP_TIMESTAMP}",
    "mira_version": "${mira_version}",
    "postgresql_version": "${pg_version}",
    "sops_version": "${sops_version}",
    "os": "${OS}",
    "contents": {
        "postgresql_backup": "postgresql_backup.dump",
        "secrets": "secrets.enc.yaml",
        "age_key": "age.key",
        "sops_config": ".sops.yaml",
        "user_data": "user_data/"
    }
}
EOF

    echo -e "${CHECKMARK}"
    return 0
}

# ============================================================================
# Restore Functions
# ============================================================================

# Restore SOPS secrets from backup
restore_secrets() {
    echo -ne "${DIM}${ARROW}${RESET} Restoring secrets... "

    local secrets_file="/opt/mira/app/secrets.enc.yaml"
    local age_key_dir="$HOME/.config/mira"
    local age_key_file="$age_key_dir/age.key"
    local sops_config="/opt/mira/app/.sops.yaml"

    local success=true

    # Restore age key first (needed for decryption)
    if [ -f "${BACKUP_DIR}/age.key" ]; then
        mkdir -p "$age_key_dir"
        cp "${BACKUP_DIR}/age.key" "$age_key_file"
        chmod 600 "$age_key_file"
    else
        echo -e "${ERROR}"
        print_error "Age key backup not found - cannot restore secrets"
        return 1
    fi

    # Restore encrypted secrets file
    if [ -f "${BACKUP_DIR}/secrets.enc.yaml" ]; then
        cp "${BACKUP_DIR}/secrets.enc.yaml" "$secrets_file"
        chmod 600 "$secrets_file"
    else
        echo -e "${ERROR}"
        print_error "Secrets backup not found"
        return 1
    fi

    # Restore SOPS config if present
    if [ -f "${BACKUP_DIR}/.sops.yaml" ]; then
        cp "${BACKUP_DIR}/.sops.yaml" "$sops_config"
    fi

    # Verify we can decrypt the restored secrets
    export SOPS_AGE_KEY_FILE="$age_key_file"
    if ! sops -d "$secrets_file" > /dev/null 2>&1; then
        echo -e "${ERROR}"
        print_error "Cannot decrypt restored secrets - key mismatch?"
        return 1
    fi

    echo -e "${CHECKMARK}"
    return 0
}

# Restore PostgreSQL data from backup
restore_postgresql_data() {
    local backup_file="${BACKUP_DIR}/postgresql_backup.dump"

    echo -ne "${DIM}${ARROW}${RESET} Restoring PostgreSQL data... "

    if [ ! -f "$backup_file" ]; then
        echo -e "${ERROR}"
        print_error "Backup file not found: $backup_file"
        return 1
    fi

    # Disable triggers during restore for FK constraint handling
    if [ "$OS" = "linux" ]; then
        # Restore with triggers disabled
        if sudo -u postgres pg_restore -d mira_service \
            --data-only \
            --disable-triggers \
            --single-transaction \
            "$backup_file" 2>/dev/null; then
            echo -e "${CHECKMARK}"
        else
            # pg_restore may return non-zero even on partial success
            # Check if data was actually restored
            local user_count
            user_count=$(sudo -u postgres psql -d mira_service -tAc "SELECT COUNT(*) FROM users" 2>/dev/null || echo "0")
            if [ "$user_count" -gt 0 ]; then
                echo -e "${CHECKMARK} ${DIM}(with warnings)${RESET}"
            else
                echo -e "${ERROR}"
                print_error "Failed to restore PostgreSQL data"
                return 1
            fi
        fi
    else
        # Extract database password from SOPS secrets for authentication
        local db_password=""
        local secrets_file="/opt/mira/app/secrets.enc.yaml"
        local age_key_file="$HOME/.config/mira/age.key"
        if [ -f "$secrets_file" ] && [ -f "$age_key_file" ]; then
            export SOPS_AGE_KEY_FILE="$age_key_file"
            db_password=$(sops -d "$secrets_file" 2>/dev/null | yq '.database.password // empty' 2>/dev/null || echo "")
        fi

        # Use PGPASSWORD for authentication (avoids interactive prompt)
        if PGPASSWORD="$db_password" pg_restore -U mira_admin -h localhost -d mira_service \
            --data-only \
            --disable-triggers \
            --single-transaction \
            "$backup_file" 2>/dev/null; then
            echo -e "${CHECKMARK}"
        else
            local user_count
            user_count=$(PGPASSWORD="$db_password" psql -U mira_admin -h localhost -d mira_service -tAc "SELECT COUNT(*) FROM users" 2>/dev/null || echo "0")
            if [ "$user_count" -gt 0 ]; then
                echo -e "${CHECKMARK} ${DIM}(with warnings)${RESET}"
            else
                echo -e "${ERROR}"
                print_error "Failed to restore PostgreSQL data"
                return 1
            fi
        fi
    fi

    return 0
}

# Restore user data files
restore_user_data_files() {
    local backup_dir="${BACKUP_DIR}/user_data"
    local target_dir="/opt/mira/app/data/users"

    echo -ne "${DIM}${ARROW}${RESET} Restoring user data files... "

    if [ ! -d "$backup_dir" ]; then
        echo -e "${CHECKMARK} ${DIM}(no user data to restore)${RESET}"
        return 0
    fi

    # Check if backup has content
    if [ -z "$(ls -A "$backup_dir" 2>/dev/null)" ]; then
        echo -e "${CHECKMARK} ${DIM}(empty backup)${RESET}"
        return 0
    fi

    mkdir -p "$target_dir"

    if cp -a "$backup_dir"/* "$target_dir/" 2>/dev/null; then
        # Set ownership
        chown -R ${MIRA_USER}:${MIRA_GROUP} "$target_dir" 2>/dev/null || true

        local user_count=$(ls -d "$target_dir"/*/ 2>/dev/null | wc -l | tr -d ' ')
        echo -e "${CHECKMARK} ${DIM}($user_count user directories)${RESET}"
    else
        echo -e "${ERROR}"
        print_error "Failed to restore user data files"
        return 1
    fi

    return 0
}

# ============================================================================
# Verification Functions
# ============================================================================

# Verify secrets are accessible
verify_secrets_accessible() {
    echo -ne "${DIM}${ARROW}${RESET} Verifying secrets... "

    local secrets_file="/opt/mira/app/secrets.enc.yaml"
    local age_key_file="$HOME/.config/mira/age.key"

    if [ ! -f "$secrets_file" ] || [ ! -f "$age_key_file" ]; then
        echo -e "${ERROR}"
        print_error "Secrets files missing after migration"
        return 1
    fi

    export SOPS_AGE_KEY_FILE="$age_key_file"
    if sops -d "$secrets_file" > /dev/null 2>&1; then
        echo -e "${CHECKMARK}"
        return 0
    else
        echo -e "${ERROR}"
        print_error "Cannot decrypt secrets after migration"
        return 1
    fi
}

# Verify memory embeddings preserved
verify_memory_embeddings() {
    echo -ne "${DIM}${ARROW}${RESET} Verifying vector embeddings... "

    local memories_with_embeddings
    if [ "$OS" = "linux" ]; then
        memories_with_embeddings=$(sudo -u postgres psql -d mira_service -tAc \
            "SELECT COUNT(*) FROM memories WHERE embedding IS NOT NULL" 2>/dev/null || echo "0")
    else
        memories_with_embeddings=$(psql -U mira_admin -h localhost -d mira_service -tAc \
            "SELECT COUNT(*) FROM memories WHERE embedding IS NOT NULL" 2>/dev/null || echo "0")
    fi

    echo -e "${CHECKMARK} ${DIM}($memories_with_embeddings memories with embeddings)${RESET}"
    return 0
}

# Verify user data files exist
verify_user_data_files() {
    echo -ne "${DIM}${ARROW}${RESET} Verifying user data files... "

    local target_dir="/opt/mira/app/data/users"

    if [ ! -d "$target_dir" ]; then
        echo -e "${CHECKMARK} ${DIM}(no user data directory)${RESET}"
        return 0
    fi

    local user_count=$(ls -d "$target_dir"/*/ 2>/dev/null | wc -l | tr -d ' ')
    echo -e "${CHECKMARK} ${DIM}($user_count user directories)${RESET}"
    return 0
}

# ============================================================================
# Metric Capture Functions
# ============================================================================

# Capture pre-migration metrics for verification
capture_pre_migration_metrics() {
    if [ "$OS" = "linux" ]; then
        PRE_USER_COUNT=$(sudo -u postgres psql -d mira_service -tAc "SELECT COUNT(*) FROM users" 2>/dev/null || echo "0")
        PRE_MESSAGE_COUNT=$(sudo -u postgres psql -d mira_service -tAc "SELECT COUNT(*) FROM messages" 2>/dev/null || echo "0")
        PRE_MEMORY_COUNT=$(sudo -u postgres psql -d mira_service -tAc "SELECT COUNT(*) FROM memories" 2>/dev/null || echo "0")
    else
        PRE_USER_COUNT=$(psql -U mira_admin -h localhost -d mira_service -tAc "SELECT COUNT(*) FROM users" 2>/dev/null || echo "0")
        PRE_MESSAGE_COUNT=$(psql -U mira_admin -h localhost -d mira_service -tAc "SELECT COUNT(*) FROM messages" 2>/dev/null || echo "0")
        PRE_MEMORY_COUNT=$(psql -U mira_admin -h localhost -d mira_service -tAc "SELECT COUNT(*) FROM memories" 2>/dev/null || echo "0")
    fi

    # Trim whitespace
    PRE_USER_COUNT=$(echo "$PRE_USER_COUNT" | tr -d ' ')
    PRE_MESSAGE_COUNT=$(echo "$PRE_MESSAGE_COUNT" | tr -d ' ')
    PRE_MEMORY_COUNT=$(echo "$PRE_MEMORY_COUNT" | tr -d ' ')

    print_info "Found: $PRE_USER_COUNT users, $PRE_MESSAGE_COUNT messages, $PRE_MEMORY_COUNT memories"
}

# Capture and verify post-migration metrics
verify_post_migration_metrics() {
    local post_user_count post_message_count post_memory_count

    if [ "$OS" = "linux" ]; then
        post_user_count=$(sudo -u postgres psql -d mira_service -tAc "SELECT COUNT(*) FROM users" 2>/dev/null || echo "0")
        post_message_count=$(sudo -u postgres psql -d mira_service -tAc "SELECT COUNT(*) FROM messages" 2>/dev/null || echo "0")
        post_memory_count=$(sudo -u postgres psql -d mira_service -tAc "SELECT COUNT(*) FROM memories" 2>/dev/null || echo "0")
    else
        post_user_count=$(psql -U mira_admin -h localhost -d mira_service -tAc "SELECT COUNT(*) FROM users" 2>/dev/null || echo "0")
        post_message_count=$(psql -U mira_admin -h localhost -d mira_service -tAc "SELECT COUNT(*) FROM messages" 2>/dev/null || echo "0")
        post_memory_count=$(psql -U mira_admin -h localhost -d mira_service -tAc "SELECT COUNT(*) FROM memories" 2>/dev/null || echo "0")
    fi

    # Trim whitespace
    post_user_count=$(echo "$post_user_count" | tr -d ' ')
    post_message_count=$(echo "$post_message_count" | tr -d ' ')
    post_memory_count=$(echo "$post_memory_count" | tr -d ' ')

    local verification_passed=true

    echo -ne "${DIM}${ARROW}${RESET} Verifying user count... "
    if [ "$PRE_USER_COUNT" != "$post_user_count" ]; then
        echo -e "${ERROR}"
        print_error "User count mismatch: $PRE_USER_COUNT -> $post_user_count"
        verification_passed=false
    else
        echo -e "${CHECKMARK} ${DIM}($post_user_count users)${RESET}"
    fi

    echo -ne "${DIM}${ARROW}${RESET} Verifying message count... "
    if [ "$PRE_MESSAGE_COUNT" != "$post_message_count" ]; then
        echo -e "${ERROR}"
        print_error "Message count mismatch: $PRE_MESSAGE_COUNT -> $post_message_count"
        verification_passed=false
    else
        echo -e "${CHECKMARK} ${DIM}($post_message_count messages)${RESET}"
    fi

    echo -ne "${DIM}${ARROW}${RESET} Verifying memory count... "
    if [ "$PRE_MEMORY_COUNT" != "$post_memory_count" ]; then
        echo -e "${ERROR}"
        print_error "Memory count mismatch: $PRE_MEMORY_COUNT -> $post_memory_count"
        verification_passed=false
    else
        echo -e "${CHECKMARK} ${DIM}($post_memory_count memories)${RESET}"
    fi

    if [ "$verification_passed" = true ]; then
        return 0
    else
        return 1
    fi
}
