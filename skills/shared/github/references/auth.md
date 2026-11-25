# GitHub Authentication Setup

This skill sets up authentication so the agent can work with GitHub repositories, PRs, issues, and CI. It covers two paths:

- **`git` (always available)** — uses HTTPS personal access tokens or SSH keys
- **`gh` CLI (if installed)** — richer GitHub API access with a simpler auth flow

## Detection Flow

When a user asks you to work with GitHub, run this check first:

```bash
# Check what's available
git --version
gh --version 2>/dev/null || echo "gh not installed"

# Check if already authenticated
gh auth status 2>/dev/null || echo "gh not authenticated"
git config --global credential.helper 2>/dev/null || echo "no git credential helper"
```

**Decision tree:**
1. If `gh auth status` shows authenticated → you're good, use `gh` for everything
2. If `gh` is installed but not authenticated → use "gh auth" method below
3. If `gh` is not installed → use "git-only" method below (no sudo needed)

---

## Method 1: Git-Only Authentication (No gh, No sudo)

This works on any machine with `git` installed. No root access needed.

### Option A: HTTPS with Personal Access Token (Recommended)

This is the most portable method — works everywhere, no SSH config needed.

**Step 1: Create a personal access token**

Tell the user to go to: **https://github.com/settings/tokens**

- Click "Generate new token (classic)"
- Give it a name like "hermes-agent"
- Select scopes:
  - `repo` (full repository access — read, write, push, PRs)
  - `workflow` (trigger and manage GitHub Actions)
  - `read:org` (if working with organization repos)
- Set expiration (90 days is a good default)
- Copy the token — it won't be shown again

**Step 2: Configure git to store the token**

```bash
# Set up the credential helper to cache credentials
# "store" saves to ~/.git-credentials in plaintext (simple, persistent)
git config --global credential.helper store

# Now do a test operation that triggers auth — git will prompt for credentials
# Username: <their-github-username>
# Password: <paste the personal access token, NOT their GitHub password>
git ls-remote https://github.com/<their-username>/<any-repo>.git
```

After entering credentials once, they're saved and reused for all future operations.

**Alternative: cache helper (credentials expire from memory)**

```bash
# Cache in memory for 8 hours (28800 seconds) instead of saving to disk
git config --global credential.helper 'cache --timeout=28800'
```

**Alternative: set the token directly in the remote URL (per-repo)**

```bash
# Embed token in the remote URL (avoids credential prompts entirely)
git remote set-url origin https://<username>:<token>@github.com/<owner>/<repo>.git
```

**Step 3: Configure git identity**

```bash
# Required for commits — set name and email
git config --global user.name "Their Name"
git config --global user.email "their-email@example.com"
```

**Step 4: Verify**

```bash
# Test push access (this should work without any prompts now)
git ls-remote https://github.com/<their-username>/<any-repo>.git

# Verify identity
git config --global user.name
git config --global user.email
```

### Option B: SSH Key Authentication

Good for users who prefer SSH or already have keys set up.

**Step 1: Check for existing SSH keys**

```bash
ls -la ~/.ssh/id_*.pub 2>/dev/null || echo "No SSH keys found"
```

**Step 2: Generate a key if needed**

```bash
# Generate an ed25519 key (modern, secure, fast)
ssh-keygen -t ed25519 -C "their-email@example.com" -f ~/.ssh/id_ed25519 -N ""

# Display the public key for them to add to GitHub
cat ~/.ssh/id_ed25519.pub
```

Tell the user to add the public key at: **https://github.com/settings/keys**
- Click "New SSH key"
- Paste the public key content
- Give it a title like "hermes-agent-<machine-name>"

**Step 3: Test the connection**

```bash
ssh -T git@github.com
# Expected: "Hi <username>! You've successfully authenticated..."
```

**Step 4: Configure git to use SSH for GitHub**

```bash
# Rewrite HTTPS GitHub URLs to SSH automatically
git config --global url."git@github.com:".insteadOf "https://github.com/"
```

**Step 5: Configure git identity**

```bash
git config --global user.name "Their Name"
git config --global user.email "their-email@example.com"
```

---

## Method 2: gh CLI Authentication

If `gh` is installed, it handles both API access and git credentials in one step.

### Interactive Browser Login (Desktop)

> **PITFALL (agent-driven sessions on Windows):** when driving `gh auth login` through a pty background process, answer prompts with `process(submit)` — never `process(write)` with a bare `\n`. Enter on a Windows PTY (ConPTY/pywinpty) is a carriage return; a lone `\n` is not delivered as a line terminator, so gh's "Press Enter to open the browser" prompt (a blocking line read) silently never returns and the login hangs. Also note the browser may not open on the user's desktop from a background session — if they report that, fall back to the device flow below.

```bash
gh auth login
# Select: GitHub.com
# Select: HTTPS
# Authenticate via browser
```

### Manual OAuth Device Flow (no TTY needed — PROVEN)

Fallback when interactive login is impractical (agent-driven sessions, no browser launch, headless). Uses gh's public OAuth client id; the user just enters a code at github.com/login/device. Scopes: `repo,read:org,gist` is the documented minimum for `gh auth login --with-token`; append `,workflow` only if you need to push workflow files.

```bash
# 1. Request a device code (gh's official client_id)
RESP=$(curl -s -X POST -H "Accept: application/json" \
  -d "client_id=178c6fc778ccc68e1d6a&scope=repo,read:org,gist" \
  https://github.com/login/device/code)
DEVICE_CODE=$(echo "$RESP" | sed 's/.*"device_code":"\([^"]*\)".*/\1/')
USER_CODE=$(echo "$RESP" | sed 's/.*"user_code":"\([^"]*\)".*/\1/')
INTERVAL=$(echo "$RESP" | sed 's/.*"interval":\([0-9]*\).*/\1/'); INTERVAL=${INTERVAL:-5}
echo "Tell the user: go to https://github.com/login/device and enter code: $USER_CODE"

# 2. Poll for the token (respect interval; +5s on slow_down; ~15 min expiry).
#    Run this loop as a background process and show the user the code first.
while true; do
  sleep "$INTERVAL"
  POLL=$(curl -s -X POST -H "Accept: application/json" \
    -d "client_id=178c6fc778ccc68e1d6a&device_code=${DEVICE_CODE}&grant_type=urn:ietf:params:oauth:grant-type:device_code" \
    https://github.com/login/oauth/access_token)
  case "$POLL" in
    *access_token*)
      # Never echo the token; pipe it straight into gh.
      # timeout guards the headless-keyring hang (see pitfall below) —
      # on exit 124, fall back to writing ~/.config/gh/hosts.yml directly.
      echo "$POLL" | sed 's/.*"access_token":"\([^"]*\)".*/\1/' | timeout 20 gh auth login --with-token \
        || { echo "WITH_TOKEN_HUNG_OR_FAILED — use the hosts.yml fallback below"; exit 1; }
      gh auth setup-git
      gh auth status
      echo "LOGIN_COMPLETE"; break ;;
    *authorization_pending*) ;;                      # keep polling
    *slow_down*) INTERVAL=$((INTERVAL + 5)) ;;       # back off per GitHub docs
    *expired_token*) echo "CODE_EXPIRED — restart the flow"; exit 1 ;;
    *access_denied*) echo "USER_DENIED"; exit 1 ;;
    *) echo "UNEXPECTED: $POLL"; exit 1 ;;
  esac
done
```

Note: on Windows winget installs, gh lands at `/c/Program Files/GitHub CLI` — add it to PATH in the same shell: `export PATH="$PATH:/c/Program Files/GitHub CLI"`.

> **PITFALL (headless Linux): `gh auth login --with-token` can hang forever.**
> On keyring-less/headless boxes (VPS, containers, no dbus session), gh's
> credential storage may block indefinitely waiting on a secret-service
> keyring — even with `--insecure-storage`, and with no output. If the
> command doesn't return within ~20s (wrap it in `timeout 20 …` to detect
> this), skip gh's login machinery and write the credential store directly:
>
> ```bash
> # $TOKEN = the access token from the device flow above (never echo it)
> mkdir -p ~/.config/gh
> LOGIN=$(curl -s -H "Authorization: token $TOKEN" https://api.github.com/user \
>   | sed 's/.*"login": *"\([^"]*\)".*/\1/')
> printf 'github.com:\n    users:\n        %s:\n            oauth_token: %s\n    git_protocol: https\n    oauth_token: %s\n    user: %s\n' \
>   "$LOGIN" "$TOKEN" "$TOKEN" "$LOGIN" > ~/.config/gh/hosts.yml
> chmod 600 ~/.config/gh/hosts.yml
> gh auth status          # reads hosts.yml directly — verifies without the keyring
> gh auth setup-git       # wires the git credential helper (does not hang)
> ```
>
> `gh auth status` and `setup-git` read the file store without touching the
> keyring, so they work immediately. Proven on a headless x86_64 VPS
> (gh 2.97.0, Aug 2026) after `--with-token` hung twice.

### Token-Based Login (Headless / SSH Servers)

```bash
echo "<THEIR_TOKEN>" | gh auth login --with-token

# Set up git credentials through gh
gh auth setup-git
```

If `--with-token` hangs here, use the hosts.yml fallback from the pitfall above.

### Verify

```bash
gh auth status
```

---

## Using the GitHub API Without gh

When `gh` is not available, you can still access the full GitHub API using `curl` with a personal access token. This is how the other GitHub skills implement their fallbacks.

### Setting the Token for API Calls

```bash
# Option 1: Export as env var (preferred — keeps it out of commands)
export GITHUB_TOKEN="<token>"

# Then use in curl calls:
curl -s -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/user
```

### Extracting the Token from Git Credentials

If git credentials are already configured (via credential.helper store), the token can be extracted:

```bash
# Read from git credential store
uv run python "$HOME/.config/opencode/skills/github/github-auth/scripts/git-credential-token.py"
```

### Helper: Detect Auth Method

Use this pattern at the start of any GitHub workflow:

```bash
# Try gh first, fall back to git + curl
if command -v gh &>/dev/null && gh auth status &>/dev/null; then
  echo "AUTH_METHOD=gh"
elif [ -n "$GITHUB_TOKEN" ]; then
  echo "AUTH_METHOD=curl"
elif _hermes_env="${HERMES_HOME:-$HOME/.hermes}/.env"; [ -f "$_hermes_env" ] && grep -q "^GITHUB_TOKEN=" "$_hermes_env"; then
  export GITHUB_TOKEN=$(grep "^GITHUB_TOKEN=" "$_hermes_env" | head -1 | cut -d= -f2 | tr -d '\n\r')
  echo "AUTH_METHOD=curl"
elif grep -q "github.com" ~/.git-credentials 2>/dev/null; then
  export GITHUB_TOKEN=$(uv run python "$HOME/.config/opencode/skills/github/github-auth/scripts/git-credential-token.py")
  echo "AUTH_METHOD=curl"
else
  echo "AUTH_METHOD=none"
  echo "Need to set up authentication first"
fi
```

---

## Multiple accounts on one machine (gh)

`gh auth status` lists every logged-in account and marks the active one.

- **PREFER THE SSH-ALIAS SPLIT over switching accounts.** When two identities coexist long-term
  (personal/portfolio vs. freelance/employer), the durable fix is one SSH key per account plus a `Host`
  alias per account, so pushes never depend on which account `gh` has active:

  ```bash
  ssh-keygen -t ed25519 -f ~/.ssh/id_<login> -N "" -C "<login>@<host>" -q

  # ~/.ssh/config
  Host github-<login>
    HostName github.com
    User git
    IdentityFile ~/.ssh/id_<login>
    IdentitiesOnly yes          # sin esto ssh prueba todas las claves y puede entrar como la otra cuenta
    StrictHostKeyChecking accept-new

  # por repo
  git remote set-url origin git@github-<login>:<owner>/<repo>.git
  ```

  The plain `Host github.com` block keeps the other account's key, so the existing workflow is untouched.
  Diagnose ownership before touching anything: `ssh -T git@github.com` answers `Hi <login>!` — that tells
  you whose key is installed at the default host.
- **`gh ssh-key add` needs the `admin:public_key` scope and fails misleadingly:** without it the call
  returns `HTTP 404: Not Found (https://api.github.com/user/keys)`, which reads like a missing repo. Fix:
  `gh auth refresh -h github.com -s admin:public_key` (device flow, one code for the user), or just hand
  the user the `.pub` contents to paste at `https://github.com/settings/ssh/new` — cheaper when the token
  is fine otherwise. Read the public key with `cat`, never regenerate it.
- **Switch with `gh auth switch -u <login>`, then always run `gh auth setup-git` immediately.** The git
  credential helper holds one token at a time: after a switch, pushes keep using the previous account and
  fail with `remote: Permission to <owner>/<repo>.git denied to <current-user>` (403) even though the API
  calls succeed. Repo write only happens through the helper, so the two halves must be re-pointed
  together — and set the remote to `https://github.com/<new-owner>/<repo>.git`, because SSH authenticates
  with whoever owns the key, not with the active `gh` account.
- **Scopes are per account, not global:** `gh api user -i | grep -i x-oauth-scopes`. The web-login
  default set is `gist, read:org, repo, workflow`; anything destructive (`delete_repo`) or
  identity-changing (`user`) has to be added with `gh auth refresh -h github.com -s <scope>`, which runs
  the device flow again (one short-lived code for the user, never a password in chat).
- **A GitHub username rename desyncs `gh`.** `~/.config/gh/hosts.yml` keys the account by its old login,
  so the next `gh auth refresh` aborts with *"error refreshing credentials for <old>, received
  credentials for <new>, did you use the correct account in the browser?"* although the user authorized
  correctly. Repair (the token survives a rename): back up the file, `sed -i 's/<old>/<new>/g'
  ~/.config/gh/hosts.yml`, then confirm with `gh auth status` and `gh api user --jq .login`.
- **Never treat `200` from `PATCH /user` as proof a field changed.** It applies `name`, `bio`, `blog`,
  `location`, `hireable`, and silently ignores `login` and `avatar_url` (both are web-UI-only: username in
  `settings/admin`, avatar in `settings/profile`). Re-read with `GET /users/<login>` and compare the
  actual field.
- **Find which account owns an email without asking:** push a commit authored with that address and read
  `gh api repos/<owner>/<repo>/commits --jq '.[0].author.login'` — GitHub attributes by verified email,
  so the login that comes back is the owner.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `git push` asks for password | GitHub disabled password auth. Use a personal access token as the password, or switch to SSH |
| `remote: Permission to X denied` | Token may lack `repo` scope — regenerate with correct scopes |
| `fatal: Authentication failed` | Cached credentials may be stale — run `git credential reject` then re-authenticate |
| `ssh: connect to host github.com port 22: Connection refused` | Try SSH over HTTPS port: add `Host github.com` with `Port 443` and `Hostname ssh.github.com` to `~/.ssh/config` |
| Credentials not persisting | Check `git config --global credential.helper` — must be `store` or `cache` |
| Multiple GitHub accounts | `gh auth switch -u <login>` + `gh auth setup-git` (both, or pushes 403); see the multi-account section above |
| `gh: command not found` + no sudo | Use git-only Method 1 above — no installation needed |
