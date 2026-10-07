# Organizing Freezer Box

A shared freezer sample tracker for the lab. It records freezers → racks → boxes → samples, and shows who in the lab is using which space.

- **Boxes:** 9×9 and 10×10 grids. Click a spot to add, edit, move or remove a sample (name, type, date, owner, notes). There are also **no-grid** boxes, which are plain lists for bags, large tubes and odd items.
- **Search** across every box by name, type, owner, notes or sample code (`S-000123`).
- **History** keeps every change: who made it, when, and what it was before and after. Removed samples stay readable.
- **Who's where:**
  - color any box by owner
  - claim a rack or box ("Claim box")
  - reserve empty spots
  - see a summary of each person's samples, claims and free space
  - "Find room" highlights boxes with enough free, unclaimed spots
  - Putting a sample into someone else's claimed box or reserved spot asks you to confirm first.
- **Labels:** print a sheet of text labels for one sample, a selection, or a whole box.
- **Import / Export (CSV):**
  - **Export** everything, or one box, as a spreadsheet for Excel, Numbers or Google Sheets.
  - **Import** a spreadsheet to load an existing inventory. A preview shows what will be added or created, and which rows have problems, before anything is saved.

Only the Python 3 that comes with macOS is needed. There is nothing to install.

## How it's set up in the lab

One always-on lab Mac runs the app and holds the database (`freezer.db`).
Everyone else uses it from their own browser at `http://<lab-mac>.local:8000/`. They can view and edit there without installing anything.
Changes show up on everyone's screen within about 15 seconds.

```
lab mates' browsers ──► lab Mac (server.py + freezer.db) ──nightly──► /Volumes/MTB_Lab/FreezerTracker/backups
```

### Install on the lab Mac (once)

1. Copy or clone this folder onto the lab Mac.
2. Run `./install_lab_server.sh`. It:
   - copies the app to `~/FreezerTracker`
   - asks for a lab passcode
   - optionally loads example data
   - keeps the server running, including after a reboot
   - schedules a backup to the lab share every night at 2 am
3. If macOS asks whether Python may accept incoming connections, click **Allow**.
4. Share the address the script prints with the lab.

To use it, lab mates need to be on the campus network or the WashU VPN. If they can't connect, the campus network may be blocking the port; ask WashU IT to allow it.

### Backups and restore

- `backup.sh` writes `freezer-YYYY-MM-DD_HHMM.db` to the `backup_dir` in `config.json` and keeps 30 days. If the share isn't mounted, it skips that night.
- To restore:
  1. Stop the server: `launchctl unload ~/Library/LaunchAgents/org.lab.freezer-tracker.plist`
  2. Copy a backup over `~/FreezerTracker/freezer.db`
  3. Start it again: `launchctl load ~/Library/LaunchAgents/org.lab.freezer-tracker.plist`

## Trying it on your own Mac

```bash
python3 seed.py --reset   # example data: 13 boxes, ~540 samples, 5 placeholder lab mates
python3 server.py         # opens http://localhost:8000/
```

Without a `config.json`, the server only listens on this Mac (127.0.0.1) and has no passcode.

## Settings (`config.json`)

| key | meaning |
|---|---|
| `host` | `0.0.0.0` lets lab mates connect; `127.0.0.1` allows this Mac only |
| `port` | default `8000` |
| `db` | database file (relative to the app folder) |
| `passcode` | shared lab passcode; leave empty for none |
| `backup_dir`, `backup_keep_days` | nightly backup location and how many days to keep |

`freezer.db` and `config.json` are in `.gitignore`, so lab data and the passcode never go to GitHub.

## CSV import format

Columns (any order; common header names like "Sample Name" or "Date Frozen" are recognised):

`freezer, rack, box, box_type, position, name, type, date, owner, notes`

- Only `name` is required. A missing freezer, rack or box is filed under "Imported".
- `position` is `A1`–`J10`, left blank for no-grid boxes.
- New boxes get their type from `box_type` (`9x9`, `10x10`, `list`). Without it, the type is guessed from the positions used.
- Freezers, racks, boxes and lab mates that don't exist yet are created.
- Rows with problems are skipped and listed, e.g. a spot that's taken, a position outside the box, or no name.
- Dates like `3/14/2025` become `2025-03-14`.
- An exported file is a valid import file. Re-importing it skips samples already in the tracker.

## Tests

```bash
python3 -m unittest discover tests
```
