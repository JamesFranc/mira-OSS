#!/bin/bash
export VAULT_ADDR='http://127.0.0.1:8200'

# Disable and re-enable AppRole
vault auth disable approle
vault auth enable approle

# Create policy
vault policy write mira-policy - <<'EOF'
path "secret/*" {
  capabilities = ["create", "read", "update", "delete", "list"]
}
path "secret/metadata/*" {
  capabilities = ["list", "read", "delete"]
}
EOF

# Create AppRole
vault write auth/approle/role/mira \
  policies="mira-policy" \
  token_ttl=1h \
  token_max_ttl=4h

# Generate credentials
vault read -format=table auth/approle/role/mira/role-id > /opt/vault/role-id.txt
vault write -f -format=table auth/approle/role/mira/secret-id > /opt/vault/secret-id.txt

# Test login
echo "Testing AppRole login..."
vault write auth/approle/login \
  role_id="$(awk '/role_id/ {print $2}' /opt/vault/role-id.txt)" \
  secret_id="$(awk '/secret_id / {print $2}' /opt/vault/secret-id.txt)"
