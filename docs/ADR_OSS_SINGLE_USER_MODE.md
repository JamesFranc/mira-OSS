# ADR: OSS Single-User Mode

**Status:** Approved
**Date:** 2025-10-04
**Decision Makers:** Taylor

---

## Context

MIRA is being prepared for open-source release. The current implementation includes a complete web interface (login, signup, chat UI, settings, memories viewer) and sophisticated multi-user authentication system (magic links, WebAuthn, sessions, CSRF protection, email integration).

For the OSS release, we want to prevent easy deployment of a competing hosted service while maintaining full programmatic functionality for developers and self-hosters.

## Decision

The OSS version will be **single-user, API-only** with the following characteristics:

### Authentication
- Single static API token generated on first startup
- Token stored in HashiCorp Vault at `mira/api_keys/mira_api`
- All API requests require `Authorization: Bearer <token>` header
- No sessions, cookies, magic links, email verification, or WebAuthn
- No user signup or login endpoints

### User Management
- Exactly one user allowed in the database
- On first startup: auto-create user if none exists
- On subsequent startups: load and use existing single user
- Error and refuse to start if multiple users detected
- User context set globally at startup (not per-request)

### Interface
- Complete removal of web interface (HTML/CSS/JS frontend)
- All interactive endpoints removed (WebSocket chat, page routes)
- Programmatic API endpoints fully preserved:
  - POST /api/chat (primary endpoint)
  - GET/POST /api/data
  - POST /api/actions
  - GET /api/health
- Single introspection endpoint preserved: GET /auth/session

### Removed Components
- Web directory and all frontend assets (~3000 lines)
- WebSocket chat endpoint
- Email service (SendGrid integration)
- WebAuthn biometric authentication
- Magic link authentication flow
- Multi-user session management
- CSRF token system
- User signup/login/logout endpoints

## Rationale

### Moat Strategy
Removing the web interface and multi-user authentication creates a significant barrier to deploying MIRA as a competing SaaS:

1. **Frontend Development Burden:** Competitors must build entire chat UI, authentication flow, and user management interface from scratch (~3000+ lines of production-quality code)

2. **Authentication Complexity:** Multi-user systems require session management, email verification, security hardening, rate limiting, distributed locking - all removed

3. **Operational Complexity:** Running a multi-tenant service requires user isolation, data privacy controls, billing integration, support infrastructure - none of which OSS version supports

4. **Time-to-Market:** Estimated 4-8 weeks of full-time development to rebuild removed components to production quality

### Developer Experience
For legitimate self-hosting and development use cases:

1. **Simple Setup:** Single command startup, no email configuration, no authentication setup beyond saving one token
2. **Standard API Access:** Familiar Bearer token pattern, works with curl, Postman, SDKs
3. **Full Functionality:** Zero feature loss in core AI capabilities, tools, memory systems
4. **Easy Integration:** Programmatic API suitable for custom frontends, CLI tools, scripts, integrations

### Security Posture
Even in single-user mode, basic security maintained:

1. **Authentication Required:** Static token prevents accidental open exposure
2. **Token Protection:** Stored in Vault (not logged after first display), rotatable by deleting from Vault and restarting
3. **Standard Pattern:** Industry-familiar Bearer token approach
4. **Minimal Attack Surface:** Removed endpoints eliminate entire classes of vulnerabilities (CSRF, session fixation, email enumeration, timing attacks on login)

## Consequences

### Positive
- Strong moat against competitive deployment
- Simplified codebase (remove ~4000 lines including web assets)
- Reduced dependencies (no SendGrid, WebAuthn, SSE libraries)
- Lower operational complexity for self-hosters
- Standard programmatic access patterns
- Preserved full AI/memory/tool capabilities

### Negative
- No built-in UI for OSS users (must build their own or use API directly)
- Single user limit may require workarounds for family/team self-hosting
- Database schema remains multi-user capable (slight inefficiency)
- Some existing documentation assumes web interface exists

### Neutral
- Requires fork maintenance if we add features to commercial web version
- OSS users can build custom frontends (web, CLI, mobile, etc.)
- Token reset requires Vault key deletion + restart (acceptable for single-user scenario)

---

# Blueprint: OSS Single-User Implementation

This blueprint provides step-by-step instructions for converting MIRA to single-user, API-only mode.

## Prerequisites

- Familiarity with MIRA codebase structure
- Understanding of FastAPI routing and dependency injection
- Knowledge of Python contextvars for user context management
- Access to test environment for validation

---

## Phase 1: Authentication System Modification

### Task 1.1: Implement Static Token Generation

**File:** `main.py` - `ensure_single_user()` function

**Location:** Called at start of `lifespan()` function

**Instructions:**
1. Token generation happens as part of user creation in `ensure_single_user()`
2. If zero users exist:
   - Generate cryptographically secure token using `secrets.token_urlsafe(32)` with `mira_` prefix
   - Store in HashiCorp Vault at path `mira/api_keys` with key `mira_api`
   - Display token prominently in console (only time it's shown)
3. If one user exists:
   - Retrieve token from Vault at `mira/api_keys/mira_api`
   - Log info message confirming user loaded
4. Store token in `app.state.api_key` for later validation access

**Edge Cases:**
- Handle Vault connection errors gracefully (fail fast with clear message)
- If Vault storage fails during creation, warn but continue (token displayed in console)
- If Vault retrieval fails on startup, exit with error

**Vault Credential File Formats:**

When deploy script stores Vault AppRole credentials, the files have this format:

`/opt/vault/role-id.txt`:
```
Key        Value
---        -----
role_id    <uuid-value>
```

`/opt/vault/secret-id.txt`:
```
Key                   Value
---                   -----
secret_id             <uuid-value>
secret_id_accessor    <uuid-value>
secret_id_num_uses    0
secret_id_ttl         0s
```

**Correct extraction patterns:**
```bash
# Extract role_id (awk matches line containing 'role_id', prints 2nd field)
VAULT_ROLE_ID=$(awk '/role_id/ {print $2}' /opt/vault/role-id.txt)

# Extract secret_id (note space after 'secret_id ' to avoid matching secret_id_accessor)
VAULT_SECRET_ID=$(awk '/secret_id / {print $2}' /opt/vault/secret-id.txt)
```

### Task 1.2: Implement Single-User Startup Logic

**File:** `main.py` - `ensure_single_user()` function

**Location:** Called at start of `lifespan()` function

**Instructions:**
1. Use `get_shared_session_manager()` to get database access
2. Query user count: `SELECT COUNT(*) as count FROM users`
3. Branch on result:
   - **Zero users:**
     - Generate UUID for user_id
     - Insert user with email "user@localhost" directly via SQL
     - Generate and store API key (see Task 1.1)
     - Print formatted success message with API key
   - **One user:**
     - Query user: `SELECT id, email FROM users LIMIT 1`
     - Load API key from Vault
     - Print ready message with user email
   - **Multiple users:**
     - Print error message explaining single-user limitation
     - Call `sys.exit(1)`
4. Store in `app.state`:
   - `single_user_id` - UUID string
   - `user_email` - email string
   - `api_key` - token string

**Edge Cases:**
- Database connection failures should propagate (fail startup)
- Vault failures during key retrieval should exit with clear error
- No prepopulation script called (user starts with empty state)

### Task 1.3: Implement Auth Dependency

**File:** `main.py` - `get_current_user()` function (lines 45-70)

**Instructions:**
1. Define `get_current_user()` as async function with dependencies:
   - `request: Request`
   - `credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)`
2. Implementation:
   - Check if credentials present and not None
   - If missing: raise HTTPException(401, "Missing authentication token")
   - Compare `credentials.credentials` with `request.app.state.api_key`
   - If mismatch: raise HTTPException(401, "Invalid authentication token")
   - If match:
     - Get user_id and email from `app.state`
     - Set user context via `set_current_user_id()` and `set_current_user_data()`
     - Return dict with user_id and email
3. Use `HTTPBearer(auto_error=False)` for the security scheme

**Note:** Auth dependency `get_current_user` is in `auth/api.py` (minimal version), not `main.py`

### Task 1.4: Replace Auth Module with Minimal Version

**Directory to REPLACE:** `auth/`

**Instructions:**
1. Delete entire `auth/` directory
2. Create new `auth/` directory with only these two files:
   - `auth/__init__.py`
   - `auth/api.py`
3. Files removed from closed source:
   - `auth/service.py` - Auth service class
   - `auth/database.py` - Auth database operations
   - `auth/email_service.py` - SendGrid integration
   - `auth/webauthn_service.py` - WebAuthn support
   - `auth/session.py` - Session management
   - `auth/rate_limiter.py` - Rate limiting
   - `auth/types.py` - Auth types
   - `auth/config.py` - Auth config
   - `auth/exceptions.py` - Auth exceptions
   - `auth/account_gc.py` - Account garbage collection
   - `auth/security_logger.py` - Security logging
   - `auth/security_middleware.py` - Security middleware
3. Create minimal `auth/__init__.py`:
   ```python
   """Single-user authentication module."""
   from auth.api import get_current_user
   __all__ = ["get_current_user"]
   ```
4. Create minimal `auth/api.py` with:
   - `security = HTTPBearer(auto_error=False)`
   - `async def get_current_user()` - validates Bearer token against `app.state.api_key`
   - Sets user context via `set_current_user_id()` and `set_current_user_data()`
5. **Keep imports unchanged** - all API files continue using `from auth.api import get_current_user`
6. Update `from auth.user_credentials import UserCredentialService` → `from utils.user_credentials import UserCredentialService`

**Note:** This approach maintains import compatibility with closed source - when converting, simply replace the minimal `auth/` with the full closed source version.

---

## Phase 2: Web Interface Removal

### Task 2.1: Remove Web Routes

**File:** `main.py` - function `create_app()`

**Lines to DELETE:** 269-328

**Instructions:**
1. Remove all protected page route handlers:
   - `/chat` and `/chat/` (lines 273-277)
   - `/settings` and `/settings/` (lines 279-283)
   - `/memories` and `/memories/` (lines 285-289)
2. Remove conditional block `if Path("web").exists():` and entire contents (lines 292-328):
   - Root route `/` handler
   - `/login` routes
   - `/signup` routes
   - `/verify-magic-link` routes
   - Browser asset routes (apple-touch-icon, favicon, manifest)
   - Static file mounting for `/assets`
3. Remove import: `get_current_user_for_pages` (line 270)
4. Remove import: `Depends` from fastapi (if not used elsewhere in file)

**Note:** Ensure no orphaned variables or imports remain

### Task 2.2: Remove WebSocket Router

**File:** `main.py` - function `create_app()`

**Line to DELETE:** 258

**Instructions:**
1. Remove: `app.include_router(websocket_chat.router, tags=["websocket"])`
2. Remove import at top: `from cns.api import websocket_chat` (line 27)

### Task 2.3: Delete Web Interface Files

**Directory to DELETE:** `web/`

**Instructions:**
1. Delete entire directory and all contents
2. This removes approximately:
   - 7 HTML pages (~2900 lines)
   - CSS and JavaScript assets
   - Images and icons
   - Blog section
3. Optionally keep minimal files if you want basic favicon support:
   - Can manually copy `favicon.ico` to project root if needed
   - Not required for API-only functionality

---

## Phase 3: Service Layer Cleanup

### Task 3.1: Delete Email Service

**File to DELETE:** `auth/email_service.py`

**Instructions:**
1. Delete entire file
2. Search codebase for imports of `EmailService`:
   - Should only be in `auth/service.py` as lazy-loaded property
3. In `auth/service.py`:
   - Remove `email_service` property (lines 39-50)
   - Remove `email_service.setter` (lines 47-50)
   - Remove lazy import in property getter
   - Any calls to `self.email_service.send_magic_link()` are in removed endpoints, should already be gone

### Task 3.2: Delete WebAuthn Service

**File to DELETE:** `auth/webauthn_service.py`

**Instructions:**
1. Delete entire file
2. Verify no remaining imports in codebase (should only have been in deleted `auth/api.py` endpoints)

### Task 3.3: Delete WebSocket Chat Endpoint

**File to DELETE:** `cns/api/websocket_chat.py`

**Instructions:**
1. Delete entire file (~600 lines)
2. Verify websocket imports removed from `main.py` (already done in Phase 2)

### Task 3.4: Make OpenAI Embeddings Optional

**File:** `clients/hybrid_embeddings_provider.py`

**Instructions:**
1. Modify `HybridEmbeddingsProvider.__init__()` to handle missing OpenAI key gracefully
2. Wrap OpenAI embeddings initialization in try/except:
   ```python
   try:
       self.deep_model = OpenAIEmbeddingModel(model="text-embedding-3-small")
   except (KeyError, ValueError) as e:
       logger.warning(f"OpenAI embeddings unavailable: {e}. Using AllMiniLM only.")
       self.deep_model = None
   ```
3. Update all methods that use `self.deep_model` to check if it's None first
4. Fallback to AllMiniLM-only operation when OpenAI unavailable

**Rationale:** OSS users shouldn't need to pay for OpenAI embeddings. Local AllMiniLM provides sufficient functionality for single-user deployments.

---

## Phase 4: Dependency Cleanup

### Task 4.1: Update Requirements File

**File:** `requirements.txt`

**Dependencies to REMOVE:**
1. `sendgrid` - Email service integration
2. `webauthn` - Biometric authentication
3. `sse-starlette` - Server-sent events (only used by removed websocket)

**Instructions:**
1. Remove these three lines from requirements.txt
2. After implementation complete, run in clean virtual environment:
   - `pip install -r requirements.txt`
   - Verify no import errors on startup
   - Confirm removed packages not inadvertently required by other dependencies

**Dependencies to KEEP:**
- `fastapi`, `starlette` - Core framework (required)
- `hypercorn` - HTTP/2 server (required for streaming)
- All AI/ML dependencies (anthropic, openai, torch, transformers, etc.)
- Database dependencies (psycopg2, pgvector)
- All tool dependencies (caldav, googlemaps, kasa, etc.)

---

## Phase 5: Database Setup

### Task 5.1: Apply Database Schema (Required)

**File:** `deploy/mira_service_schema.sql`

**Instructions:**
1. After PostgreSQL is configured with users and database, apply the schema:
   ```bash
   PGPASSWORD='changethisifdeployingpwd' psql -U mira_admin -h localhost -d mira_service -f /opt/mira/app/deploy/mira_service_schema.sql
   ```
2. This creates all required tables: users, conversations, messages, memories, etc.
3. Deploy script should include this step after PostgreSQL configuration

**Note:** Without this step, MIRA will fail with `relation "users" does not exist`

### Task 5.2: Schema Cleanup (Optional)

**Files:** `docs/mira_service_schema.sql`, `scripts/prepopulate_new_user.sql`

**Instructions:**

**Option A: Leave schema as-is (RECOMMENDED)**
- Simplest approach
- Maintains compatibility if ever need to restore multi-user
- Unused tables/columns have minimal performance impact
- No migration required for existing installations

**Option B: Clean unused components**

If choosing Option B, modify schema:

1. **docs/mira_service_schema.sql:**
   - Drop `magic_links` table definition
   - Drop `sessions` table definition (unless keeping API token endpoints)
   - Remove `webauthn_credentials` column from `users` table
   - Keep `users` table with: id, email, is_active, created_at, last_login_at, timezone
   - Keep all other tables (conversations, messages, user_credentials, etc.)

2. **For existing installations:**
   - Create migration script to drop these tables/columns
   - Ensure no foreign key constraints break
   - Backup data before running

3. **scripts/prepopulate_new_user.sql:**
   - No changes needed (does not reference removed tables)

**Recommendation:** Start with Option A. Only pursue Option B if storage optimization becomes priority.

---

## Phase 6: Testing & Validation

### Task 6.1: Startup Testing

**Test Case 1: Fresh Installation (No Users)**

1. Start with empty database (or drop users table data)
2. Run `python main.py`
3. Verify console shows:
   - Formatted box with "MIRA Ready - Single-User OSS Mode"
   - User email displayed
   - API Key displayed (starts with `mira_`)
4. Verify token stored in Vault: `vault kv get secret/mira/api_keys`
5. Verify database has exactly one user with email "user@localhost"

**Test Case 2: Existing Single User**

1. Ensure database has exactly one user
2. Ensure API key exists in Vault at `mira/api_keys/mira_api`
3. Run `python main.py`
4. Verify console shows:
   - "MIRA Ready - User: {email}"
5. Verify no errors or warnings

**Test Case 3: Multiple Users (Error Condition)**

1. Manually insert second user into database
2. Run `python main.py`
3. Verify:
   - Startup fails with `sys.exit(1)`
   - Error message: "ERROR: Found {count} users"
   - Message explains "MIRA OSS operates in single-user mode only"

### Task 6.2: API Authentication Testing

**Test Case 4: Valid Token**

1. Start MIRA and capture API token
2. Make request:
   ```
   POST http://localhost:1993/v0/api/chat
   Header: Authorization: Bearer {valid_token}
   Header: Content-Type: application/json
   Body: {"message": "Hello MIRA"}
   ```
3. Verify:
   - 200 OK response
   - Valid JSON response with continuum_id and response text
   - No authentication errors

**Test Case 5: Missing Token**

1. Make request without Authorization header
2. Verify:
   - 401 Unauthorized response
   - Error message mentions missing Authorization header
   - Includes usage hint about Bearer token format

**Test Case 6: Invalid Token**

1. Make request with wrong token:
   ```
   Header: Authorization: Bearer invalid_token_123
   ```
2. Verify:
   - 401 Unauthorized response
   - Error message indicates invalid token
   - No information leakage about valid token format

**Test Case 7: Token Rotation**

1. Note current token from Vault
2. Stop MIRA
3. Delete token from Vault: `vault kv delete secret/mira/api_keys`
4. Delete user from database (to trigger fresh creation)
5. Start MIRA
6. Verify new token generated (different from previous)
7. Verify old token no longer works
8. Verify new token works

### Task 6.3: Endpoint Coverage Testing

**Test Case 8: Verify Removed Endpoints Return 404**

Test that these endpoints no longer exist:
- POST /auth/signup
- POST /auth/magic-link
- POST /auth/verify
- POST /auth/logout
- POST /auth/logout-all
- POST /auth/csrf
- GET /
- GET /chat
- GET /login
- GET /signup
- GET /settings
- GET /memories
- All /auth/webauthn/* endpoints
- WebSocket endpoint (attempt WebSocket connection)

For each:
1. Make request with valid token
2. Verify 404 Not Found response

**Test Case 9: Verify Kept Endpoints Work**

Test these endpoints still function:
- GET /api/health (should work without token based on current implementation)
- GET /auth/session (with valid token)
- POST /api/chat (with valid token)
- GET /api/data (with valid token)
- POST /api/actions (with valid token)

For each:
1. Make appropriate request with valid token
2. Verify expected response
3. Verify functionality preserved

### Task 6.4: User Context Testing

**Test Case 10: Verify Global User Context**

1. Start MIRA with single user
2. Make API request that uses tools (e.g., reminder creation)
3. Verify in logs that:
   - User context is available to tool execution
   - No "user context not set" errors
   - Tool data stored with correct user_id
4. Query database to confirm:
   - Tool data (e.g., reminder files) stored in correct user directory
   - No cross-user contamination possible

### Task 6.5: Edge Case Testing

**Test Case 11: Vault Connectivity**

1. Test MIRA behavior if Vault is unreachable (should fail startup with clear error)
2. Test MIRA behavior if Vault key doesn't exist (should fail startup with clear error)
3. Verify Vault AppRole authentication works correctly

**Test Case 12: Concurrent Requests**

1. Send multiple simultaneous requests to /api/chat
2. Verify distributed request lock still works (only one processes at a time)
3. Verify queued requests wait and process successfully
4. Verify no race conditions or context corruption

---

## Phase 7: Documentation Updates

### Task 7.1: Update README

**File:** `README.md` (create if doesn't exist)

**Required Sections:**

1. **Installation:**
   - Prerequisites (Python version, PostgreSQL, Redis/Valkey)
   - Dependency installation
   - Database setup
   - Initial startup instructions

2. **Authentication:**
   - Explain single static token model
   - Show where to find token on first startup (console output)
   - Explain Vault storage location
   - How to rotate token (delete from Vault + delete user + restart)

3. **API Usage:**
   - Show curl example with Bearer token
   - Document primary endpoint: POST /api/chat
   - Link to full API documentation
   - Example request/response

4. **Configuration:**
   - Environment variables
   - Vault setup for API keys
   - Tool configuration

5. **Limitations:**
   - Single user only
   - No web interface included
   - Must build custom frontend or use programmatically

### Task 7.2: Create API Documentation

**File:** `docs/API_REFERENCE.md` (create)

**Required Content:**

1. Authentication header format
2. Complete endpoint listing:
   - POST /api/chat (primary)
   - GET /api/health
   - GET /auth/session
   - GET/POST /api/data
   - POST /api/actions
3. Request/response schemas for each endpoint
4. Error response formats
5. Rate limiting behavior (if applicable)

### Task 7.3: Update Development Docs

**File:** `CLAUDE.md` (existing)

**Updates Required:**

1. Remove references to web interface development
2. Update authentication section to reflect token-based model
3. Remove references to:
   - Magic link flow
   - Email service
   - WebAuthn
   - Cookie/session management
   - CSRF tokens
4. Add note about single-user constraint
5. Update testing instructions to use API token

---

## Phase 8: Final Validation

### Task 8.1: Complete System Test

**Comprehensive Validation:**

1. Fresh install on clean system:
   - Clone repository
   - Setup database
   - Install dependencies
   - Run first startup
   - Capture and test API token
   - Execute sample conversation via API
   - Verify all expected functionality works

2. Security audit:
   - Verify no endpoints accessible without token
   - Verify token not leaked in logs
   - Verify token file has secure permissions
   - Verify no web interface accessible
   - Verify removed endpoints truly removed

3. Performance check:
   - Verify startup time acceptable
   - Verify API response times unchanged
   - Verify memory usage reasonable
   - Verify global user context doesn't cause issues

4. Documentation review:
   - Follow README from scratch
   - Verify all instructions accurate
   - Verify no references to removed features
   - Verify API documentation matches implementation

### Task 8.2: Create Migration Guide

**File:** `docs/MIGRATION_TO_OSS.md` (create)

**For existing MIRA users who want to migrate to OSS version:**

1. Backup instructions (database, user data)
2. How to export single user's data if coming from multi-user
3. How to preserve API credentials during transition
4. Step-by-step migration procedure
5. Rollback procedure if issues arise
6. FAQ for common migration issues

---

## Success Criteria

The implementation is complete when:

- [x] MIRA starts successfully with zero or one user in database
- [x] Static API token generated and stored in Vault on first startup
- [x] All API endpoints require valid Bearer token
- [x] Invalid/missing tokens return 401 with helpful message
- [x] All web routes and HTML pages removed
- [x] WebSocket endpoint removed
- [x] Email service removed (no SendGrid dependency)
- [x] WebAuthn service removed
- [x] Replaced `auth/` module with minimal single-user version (keeps import paths compatible)
- [x] Full conversation flow works via POST /api/chat
- [x] All tools function correctly with user context
- [x] Memory systems work (working memory, long-term memory, surfacing)
- [x] No "user context not set" errors occur
- [x] Database enforces single user on startup (exits if >1)
- [x] Token rotation works (delete from Vault + delete user, restart)
- [x] Documentation complete (README, guides, release notes)
- [ ] All test cases pass

---

## Rollback Plan

If issues arise during implementation:

1. **Immediate rollback:** Git revert all changes
2. **Partial rollback:** Keep auth changes, restore web interface temporarily
3. **Data safety:** All changes are code-only; no data loss risk
4. **Testing:** Test rollback in staging before production

---

## Implementation Sequence

Follow phases in order:

1. **Phase 1** - Authentication (most critical, foundational)
2. **Phase 2** - Web interface removal (visible changes)
3. **Phase 3** - Service cleanup (dependency reduction)
4. **Phase 4** - Dependencies (external cleanup)
5. **Phase 5** - Database (optional optimization)
6. **Phase 6** - Testing (validation)
7. **Phase 7** - Documentation (communication)
8. **Phase 8** - Final validation (ship readiness)

Estimate: 2-3 days for experienced developer familiar with codebase.

---

## Support & Questions

For implementation questions:
- Reference this blueprint
- Check existing MIRA architecture docs
- Review git history for context on removed components
- Test each phase thoroughly before proceeding to next
