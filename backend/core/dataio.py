"""Reading uploaded files and persisting dataset versions."""
import io
from pathlib import Path
import pandas as pd

from ..config import STORAGE_DIR

NULL_TOKENS = {"", "nan", "null", "none", "n/a", "na", "-", "--"}


def is_null(v) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and v != v:
        return True
    return str(v).strip().lower() in NULL_TOKENS


def read_table(content: bytes, filename: str) -> pd.DataFrame:
    """CSV/Excel -> all-string DataFrame (raw values preserved so we can detect formatting problems)."""
    name = filename.lower()
    if name.endswith((".xlsx", ".xlsm", ".xls")):
        df = pd.read_excel(io.BytesIO(content), dtype=str, keep_default_na=False)
    else:
        text = None
        for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                text = content.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, skipinitialspace=False)
    df.columns = [str(c).strip() for c in df.columns]
    df = df.loc[:, [c for c in df.columns if not c.startswith("Unnamed:")]]
    df = df[~(df.apply(lambda r: all(is_null(x) for x in r), axis=1))].reset_index(drop=True)
    return df.astype(str)


def version_path(dataset_id: int, version: int) -> Path:
    return STORAGE_DIR / "datasets" / f"dataset_{dataset_id}_v{version}.csv"


def save_df(df: pd.DataFrame, path: Path) -> None:
    df.to_csv(path, index=False, encoding="utf-8-sig")


def load_df(path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
