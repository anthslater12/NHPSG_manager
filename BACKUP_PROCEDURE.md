# NHPSG Manager -- Backup and Recovery Procedure

**Repository:** `C:\NHPSG_Manager`\
**Git Remote:** Private GitHub repository (`origin`)\
**Authoritative Working Repository:** `C:\NHPSG_Manager`

------------------------------------------------------------------------

# Daily Development Workflow

1.  Open PowerShell.
2.  Change to the project folder:

``` powershell
cd C:\NHPSG_Manager
```

3.  Activate the virtual environment:

``` powershell
.\venv\Scripts\Activate.ps1
```

4.  Update from GitHub:

``` powershell
git pull
```

5.  Develop and test.

6.  Commit:

``` powershell
git add <files>
git commit -m "Describe the change"
```

7.  Push:

``` powershell
git push
```

------------------------------------------------------------------------

# Database Backup Procedure

Before backing up:

-   Stop Flask (`Ctrl+C`).

Create a timestamped backup:

``` powershell
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"

$backup = "C:\Users\anths\Sync\NHPSG_Manager_DB_Backups\nhpsg_$stamp.db"

Copy-Item C:\NHPSG_Manager\nhpsg.db $backup
```

Verify the backup:

``` powershell
Get-FileHash C:\NHPSG_Manager\nhpsg.db -Algorithm SHA256
Get-FileHash $backup -Algorithm SHA256
```

The hashes must match.

------------------------------------------------------------------------

# Milestone Backup

For major milestones:

1.  Stop Flask.
2.  Create a database backup.
3.  Create a ZIP snapshot of the repository.
4.  Commit and push to GitHub.

Store milestone ZIP files in:

`C:\Users\anths\Sync\NHPSG_Manager_Final_Backups`

------------------------------------------------------------------------

# Restoring the Database

1.  Stop Flask.
2.  Rename the current database if required.
3.  Copy the chosen backup to:

`C:\NHPSG_Manager\nhpsg.db`

4.  Start the application:

``` powershell
python app.py
```

------------------------------------------------------------------------

# Setting Up on a New Computer

1.  Clone the repository:

``` powershell
git clone <repository-url> C:\NHPSG_Manager
```

2.  Create a virtual environment:

``` powershell
cd C:\NHPSG_Manager
py -m venv venv
.\venv\Scripts\Activate.ps1
```

3.  Install dependencies:

``` powershell
pip install -r requirements.txt
```

4.  Copy a backed-up `nhpsg.db` into the project folder.

5.  Run:

``` powershell
python app.py
```

------------------------------------------------------------------------

# Files NOT Stored in Git

-   `nhpsg.db`
-   `*.db`
-   `*.db-wal`
-   `*.db-shm`
-   `*.sqlite`
-   `*.sqlite3`
-   `venv`
-   `.env`

------------------------------------------------------------------------

# Repository Structure

    C:\NHPSG_Manager
    ├── app.py
    ├── templates
    ├── static
    ├── tests
    ├── documentation
    ├── venv
    ├── nhpsg.db
    └── requirements.txt

------------------------------------------------------------------------

*Last updated: 2026-07-31*
