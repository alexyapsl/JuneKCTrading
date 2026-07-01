# How to Push This Code to GitHub (Secure PAT Method)

## Step 1: Create a Fine-Grained Personal Access Token (Recommended)

1. Go to GitHub → Settings → **Developer settings** → **Personal access tokens** → **Fine-grained tokens**
2. Click **"Generate new token"**
3. **Repository access**: Choose **Only select repositories** → Select `JuneKCTrading`
4. **Permissions**:
   - Repository permissions → **Contents** → **Read and write**
   - (Optional) Repository permissions → **Metadata** → Read-only (usually auto-selected)
5. Click **Generate token**
6. **Copy the token immediately** — you will not see it again.

---

## Step 2: Push the Code (Two Options)

### Option A — Using Git Credential Manager (Easiest on Windows)

```powershell
cd C:\Users\alexy\.openclaw\workspace

# Initialize git if not already done
git init
git add ig_dow_candle_stream.py HOW_TO_PUSH_TO_GITHUB.md
git commit -m "feat: production IG Dow 3-min candle streamer with JSONL logging"

# Add remote
git remote add origin https://github.com/alexyapsl/JuneKCTrading.git

# Push (will prompt for PAT when using HTTPS)
git branch -M main
git push -u origin main
```

When prompted for password, **paste the fine-grained PAT** (not your GitHub password).

---

### Option B — Using the PAT directly (more explicit)

```powershell
$token = "github_pat_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
git push https://alexassistclawbot:${token}@github.com/alexyapsl/JuneKCTrading.git main
```

---

## Security Notes

- Never commit the PAT to any file.
- The PAT created in Step 1 only has access to `JuneKCTrading` and can be revoked anytime.
- You can create multiple PATs for different machines / bots.
- Rotate the token every 90 days for better security.

---

## After Pushing

Once the code is in the repo, you (or CI) can run it 24/7 using the same `.env` file pattern. The JSONL log files will be written to the `logs/` folder and can be easily ingested by any downstream trading system.