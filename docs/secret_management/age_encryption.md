# Age Encryption for SOPS Secrets

This document explains the age encryption system used by MIRA-OSS for secrets management.

## What is Age?

[Age](https://github.com/FiloSottile/age) is a modern, simple file encryption tool designed by Filippo Valsorda (Go security lead at Google). It's the recommended encryption backend for SOPS in new projects.

### Why Age over GPG?

| Property | Age | GPG |
|----------|-----|-----|
| Key format | Simple text file | Complex keyring |
| Setup complexity | Generate one file | Install gpg-agent, create keyring, configure trust |
| Key management | Copy one file | Export/import, trust levels, subkeys |
| Algorithm | X25519 + ChaCha20-Poly1305 | RSA/DSA/ECDSA (configurable) |
| Audited | Yes (Cure53, 2019) | Partial, complex codebase |

Age provides equivalent security with dramatically simpler operations.

## Key Components

### 1. Age Private Key (`age.key`)

The private key is a single line starting with `AGE-SECRET-KEY-`:

```
AGE-SECRET-KEY-1QQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQ
```

**Storage location**: `~/.config/mira/age.key`

**Security requirements**:
- File permissions MUST be `600` (owner read/write only)
- Never commit to version control
- Never share over insecure channels
- Backup securely (password manager, encrypted backup)

### 2. Age Public Key

Derived from the private key, starts with `age1`:

```
age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq
```

This is embedded in `.sops.yaml` to specify who can encrypt secrets:

```yaml
creation_rules:
  - age: age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq
```

The public key can be shared freely.

### 3. SOPS Configuration (`.sops.yaml`)

Tells SOPS which keys to use for encryption:

```yaml
creation_rules:
  - path_regex: secrets\.enc\.yaml$
    age: age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq
```

## Key Generation

### Using the CLI (Recommended)

```bash
mira secrets init
```

This command:
1. Generates an age keypair
2. Saves private key to `~/.config/mira/age.key`
3. Sets file permissions to `600`
4. Creates `.sops.yaml` with the public key
5. Creates empty `secrets.enc.yaml` template

### Manual Generation

If you need to generate keys manually:

```bash
# Install age
brew install age  # macOS
# or: apt install age  # Debian/Ubuntu

# Generate keypair
age-keygen -o ~/.config/mira/age.key

# Set permissions
chmod 600 ~/.config/mira/age.key

# Extract public key (shown during generation, or):
age-keygen -y ~/.config/mira/age.key
```

## How Encryption Works

### Encryption Flow (when you run `mira secrets edit`)

1. SOPS reads `.sops.yaml` to find the age public key
2. Generates a random data encryption key (DEK)
3. Encrypts each secret value with DEK using AES-256-GCM
4. Encrypts DEK with your age public key using X25519
5. Stores encrypted DEK in file header
6. Computes MAC over entire file for integrity

### Decryption Flow (at application startup)

1. SOPS reads encrypted file
2. Finds encrypted DEK in header
3. Decrypts DEK using your age private key
4. Decrypts each secret value using DEK
5. Verifies MAC to ensure file integrity
6. Returns plaintext secrets to application

## Security Properties

### What Age Protects Against

- ✅ **File theft**: Encrypted file is useless without private key
- ✅ **Tampering**: MAC verification detects modifications
- ✅ **Future key compromise**: Forward secrecy via ephemeral keys

### What Age Does NOT Protect Against

- ❌ **Compromised machine**: If attacker has access to running system, they can read decrypted secrets from memory
- ❌ **Lost private key**: No recovery possible without backup
- ❌ **Weak file permissions**: If `age.key` is world-readable, anyone can decrypt

## Key Backup Recommendations

### DO

1. Store backup in password manager (1Password, Bitwarden)
2. Print and store in physical safe (for disaster recovery)
3. Use encrypted backup service with separate credentials

### DON'T

1. Email the key to yourself
2. Store in cloud sync (Dropbox, iCloud) unencrypted
3. Commit to any git repository
4. Store on shared drives

## Rotating Keys

To rotate to a new age key:

```bash
# 1. Generate new keypair
age-keygen -o ~/.config/mira/age.key.new

# 2. Update .sops.yaml with new public key

# 3. Re-encrypt secrets with new key
sops updatekeys secrets.enc.yaml

# 4. Replace old key
mv ~/.config/mira/age.key.new ~/.config/mira/age.key
chmod 600 ~/.config/mira/age.key

# 5. Securely delete old key backup
```

## Troubleshooting

### "Age key not found"

```
FATAL: Age private key not found at /Users/you/.config/mira/age.key
```

**Solution**: Run `mira secrets init` or copy key from backup.

### "Permission denied" / "Insecure file permissions"

```
FATAL: age.key has insecure permissions (644). Required: 600
```

**Solution**: `chmod 600 ~/.config/mira/age.key`

### "MAC mismatch" / "Integrity check failed"

The secrets file was modified without proper re-encryption.

**Solution**: Restore from git or backup, then re-edit properly with `mira secrets edit`.

