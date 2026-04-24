# Deploying the mock UI to Streamlit Community Cloud

Goal: a private URL (`https://<something>.streamlit.app`) gated by a shared password, so the team can click and use the mock without running anything locally.

**Time:** ~10 minutes end-to-end.

---

## 0. Prerequisites

- A GitHub account.
- Git installed locally.
- A Streamlit Community Cloud account: sign in with GitHub at [share.streamlit.io](https://share.streamlit.io). Free.

---

## 1. Link the local folder to the existing remote and create a new branch

You already have a remote repo `bio_seq_project` on GitHub. We'll connect the local folder to it, create a new branch off `main`, commit the mock UI there, and push.

From the repo root (the `BioSeq investigator` folder):

```bash
git init
git remote add origin https://github.com/<your-user>/bio_seq_project.git
git fetch origin
git checkout -b mock-ui origin/main
```

Pick any branch name you like instead of `mock-ui` — e.g. `feature/streamlit-mock`, `mvp/ui`, etc. It just has to be new (not already on the remote).

> **If `git checkout` fails** with *"would be overwritten by checkout"* — you have a local file with the same name as a file on `origin/main` (often `README.md` or `.gitignore`). Rename or move the local copy aside, retry the checkout, then merge the contents manually.

## 2. Stage, verify, commit, push

```bash
git add .
git status --short
```

Now verify that `.venv/` and `.streamlit/secrets.toml` are **not** staged.

**Git Bash / macOS / Linux:**

```bash
git ls-files --cached | grep -E "(\.venv|secrets\.toml$)"
```

**Windows PowerShell:**

```powershell
git ls-files --cached | Select-String -Pattern "\.venv|secrets\.toml$"
```

The command must return nothing. If it prints a path, **stop** and fix `.gitignore` before committing — the password would leak.

Then commit and push the new branch:

```bash
git commit -m "Add Streamlit mock UI"
git push -u origin mock-ui
```

On GitHub you should now see a new branch `mock-ui` under the `bio_seq_project` repo, with all the mock UI files. (Open a PR to `main` later when the team wants to merge — or leave it as a feature branch for the demo.)

---

## 3. Deploy on Streamlit Community Cloud

1. Open [share.streamlit.io](https://share.streamlit.io) → **New app**.
2. Fill in:
   - **Repository:** `<your-user>/bio_seq_project`
   - **Branch:** `mock-ui` *(the branch you just pushed, not `main`)*
   - **Main file path:** `streamlit_ui/app.py`
   - **App URL:** pick something like `bioseq-investigator` (you'll get `bioseq-investigator.streamlit.app`).
3. Click **Advanced settings** → **Python version:** `3.11` → **Secrets:** paste exactly:

   ```toml
   app_password = "<the-password-you-want-to-share>"
   ```

4. Click **Deploy**. First build takes 2–5 minutes (installing deps).

When the page lands, you should see the password prompt.

---

## 4. Point Streamlit Cloud at the right requirements file

By default Streamlit Cloud looks for `requirements.txt` at repo root. Ours is at `streamlit_ui/requirements.txt`. Two options — pick one:

**Option A (simplest, recommended):** create a root-level `requirements.txt` that just points at the real one:

```bash
echo "-r streamlit_ui/requirements.txt" > requirements.txt
git add requirements.txt
git commit -m "Forward root requirements to streamlit_ui"
git push
```

**Option B:** in the Streamlit Cloud **Advanced settings** of the app, Streamlit Cloud now auto-detects `requirements.txt` next to the main file. If the build uses `streamlit_ui/requirements.txt` automatically, you're already done and can skip Option A. Check the build log to confirm.

---

## 5. Share with the team

Message to the team:

```
Mock UI: https://bioseq-investigator.streamlit.app
Password: <the-password>
```

Rotate the password any time via **App Settings → Secrets** on share.streamlit.io — takes ~20 s for the app to pick up the new value.

---

## Security notes (read before sharing)

- The password gate is a single shared secret — fine for a closed team demo, **not** for anything sensitive. No per-user accounts, no rate limiting, no lockout on wrong attempts.
- `st.secrets` is never exposed to the browser, so the password itself isn't leaked — only someone who knows it (or brute-forces it) gets in. Pick something longer than 8 chars.
- GitHub repo should stay **private**, even though the password logic doesn't depend on it — `secrets.toml` might accidentally get committed in a rush later, and a private repo limits the blast radius.
- Streamlit Cloud free tier shows your email on the app admin page; the *public URL* does not.

## Turning the gate off

Delete the `app_password` key from Secrets (cloud) or `.streamlit/secrets.toml` (local) → the app becomes open again, no code change needed.

## Troubleshooting

- **"ModuleNotFoundError" on first deploy** — you hit Option B but Streamlit didn't pick up `streamlit_ui/requirements.txt`. Do Option A.
- **App sleeps after inactivity** — Community Cloud apps sleep after ~7 days idle. First hit wakes them in ~30 s. Tell the team.
- **Password prompt keeps coming back** — browser is blocking the session cookie Streamlit uses. Disable strict tracking-prevention for `*.streamlit.app`.
- **Secrets changes don't take effect** — Streamlit Cloud needs ~15–20 s after saving; then reload the tab.

## Alternatives if Streamlit Cloud is blocked for you

- **Hugging Face Spaces** (Streamlit SDK, can be Private → invite-only).
- **ngrok** (`ngrok http 8501 --basic-auth=team:pass`) — keeps the app on your laptop, URL changes on restart on the free tier.
- **Cloudflare Tunnel + Access** — email-whitelist auth, free up to 50 users, but a fiddlier setup.
