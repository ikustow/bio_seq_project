# bio_seq_project

## Repository workflow rules

1. Clone the repository

- Copy the repository locally:
  - `git clone <url>`
- Enter the project folder:
  - `cd bio_seq_project`

2. Update your local copy

- Always sync with the remote `main` branch before starting work:
  - `git checkout main`
  - `git pull origin main`

3. Create a new branch

- The starting point is always `main`.
- Create your branch from the up-to-date `main` branch:
  - `git checkout main`
  - `git pull origin main`
  - `git checkout -b feature/your-task-name`

4. Branch naming rules

- Use clear and concise names.
- Branch name format:
  - `feature/<description>` — new feature
  - `fix/<description>` — bug fix
  - `docs/<description>` — documentation
  - `chore/<description>` — maintenance tasks
- Examples:
  - `feature/add-sequence-parser`
  - `fix/readme-typo`

5. Working in your branch

- Make small, logical commits.
- Write meaningful commit messages:
  - `git commit -m "Add sequence parser"`
- Before pushing, make sure your branch is clean:
  - `git status`

6. Publishing your branch

- Push your branch to the remote repository:
  - `git push -u origin <branch-name>`

7. Creating a pull request / merge request

- Create PR/MR into `main`.
- In the description include:
  - what was done;
  - why it was done;
  - if needed — a short test plan.

8. Review and merge

- After positive review, merge changes into `main`.
- Before merging, update your branch from `main` if needed:
  - `git checkout main`
  - `git pull origin main`
  - `git checkout <branch-name>`
  - `git merge main`

9. Deleting a branch

- After merge, delete the local and remote branch:
  - `git branch -d <branch-name>`
  - `git push origin --delete <branch-name>`

10. General recommendations

- Work from an up-to-date `main`.
- Avoid working directly on `main`.
- Write understandable commit messages.
- Commit frequently.
