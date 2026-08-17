"""
auto_update.py
──────────────
Runs silently via Windows Task Scheduler (every hour).
Checks if a newer IT or GRN file has appeared in Downloads.
If yes: slims the IT file, pushes both to GitHub, updates snapshot.
Nothing happens if files haven't changed.
"""
import pathlib, datetime, shutil, subprocess, sys, json, logging
import pandas as pd

# ── Paths ────────────────────────────────────────────────────────────────────
ROOT      = pathlib.Path(__file__).parent
DATA_DIR  = ROOT / "data"
DOWNLOADS = pathlib.Path(r"C:\Users\Vaibhav\Downloads")
LOG_FILE  = ROOT / "data" / "auto_update.log"
STATE     = ROOT / "data" / "auto_update_state.json"

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.info
err = logging.error

def find_latest(pattern):
    files = sorted(DOWNLOADS.glob(pattern), key=lambda f: f.stat().st_mtime, reverse=True)
    return files[0] if files else None

def load_state():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except Exception:
            pass
    return {}

def save_state(d):
    STATE.write_text(json.dumps(d, indent=2))

def slim_it(src: pathlib.Path) -> pathlib.Path:
    """Keep only rows with Intransit_quantity > 0. Returns path to slim file."""
    df = pd.read_csv(src, low_memory=False)
    df.columns = df.columns.str.strip()
    col = next((c for c in df.columns if c.lower() == "intransit_quantity"), None)
    if not col:
        raise ValueError("intransit_quantity column not found")
    df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    before = len(df)
    df = df[df[col] > 0]
    out = DATA_DIR / "latest_it.csv"
    df.to_csv(out, index=False)
    log(f"Slimmed IT: {before:,} → {len(df):,} rows  ({out.stat().st_size/1e6:.1f} MB)")
    return out

def run(cmd, cwd=None):
    result = subprocess.run(cmd, cwd=cwd or ROOT, capture_output=True, text=True, shell=True)
    if result.stdout.strip():
        log(result.stdout.strip())
    if result.stderr.strip():
        log(result.stderr.strip())
    return result.returncode

def main():
    log("─── auto_update check ───")

    it_file  = find_latest("inventory_dataframe*.csv")
    grn_file = find_latest("india_grn1*.csv")

    if not it_file:
        log("No inventory_dataframe*.csv in Downloads — skipping"); return
    if not grn_file:
        log("No india_grn1*.csv in Downloads — skipping"); return

    state = load_state()
    it_key  = str(it_file.stat().st_mtime)
    grn_key = str(grn_file.stat().st_mtime)

    if state.get("it") == it_key and state.get("grn") == grn_key:
        log(f"Files unchanged ({it_file.name}) — nothing to do"); return

    log(f"New files detected:  IT={it_file.name}  GRN={grn_file.name}")

    try:
        # Slim IT (strips zero-qty rows so the file fits under 25 MB)
        slim_it(it_file)

        # Copy GRN
        shutil.copy(grn_file, DATA_DIR / "latest_grn.csv")
        log("Copied GRN file")

        # Append snapshot
        rc = run(f'python "{ROOT / "append_snapshot.py"}"')
        if rc != 0:
            log("WARNING: append_snapshot.py returned non-zero — continuing")

        # Git add + commit + push
        label = datetime.date.today().strftime("%d %b %Y")
        rc = run(
            f'git add data/latest_it.csv data/latest_grn.csv '
            f'data/snapshot_history.json data/current_summary.json data/prev_summary.json '
            f'&& git commit -m "Auto-update {label}" '
            f'&& git push origin master'
        )
        if rc == 0:
            log(f"✅ Pushed successfully — dashboard will refresh in ~2 min")
            save_state({"it": it_key, "grn": grn_key, "last_push": label})
        else:
            err("git push failed — check credentials or network")

    except Exception as e:
        err(f"auto_update failed: {e}")

if __name__ == "__main__":
    main()
