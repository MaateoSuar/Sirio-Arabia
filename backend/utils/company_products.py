import io
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pandas as pd


ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls"}


@dataclass
class ParsedTable:
    columns: List[str]
    rows: List[Dict[str, str]]
    total_rows: int


def _normalize_columns(cols: List[str]) -> List[str]:
    out: List[str] = []
    seen = {}
    for c in cols:
        base = ("" if c is None else str(c)).strip()
        base = base if base else "(sin nombre)"
        key = base
        if key in seen:
            seen[key] += 1
            key = f"{base} ({seen[base]})"
        else:
            seen[key] = 1
        out.append(key)
    return out


def validate_filename(filename: str) -> Tuple[bool, str]:
    name = (filename or "").strip()
    if not name:
        return False, "Archivo inválido"
    ext = os.path.splitext(name)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return False, "Tipo de archivo no permitido. Subí .xlsx, .xls o .csv"
    return True, ""


def read_dataframe_from_bytes(filename: str, content: bytes) -> pd.DataFrame:
    ext = os.path.splitext((filename or "").strip())[1].lower()
    if ext == ".csv":
        # Mantener todo como string para no perder ceros a la izquierda / formatos
        return pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False)
    if ext in {".xlsx", ".xls"}:
        try:
            return pd.read_excel(io.BytesIO(content), dtype=str, keep_default_na=False)
        except Exception:
            return pd.read_excel(io.BytesIO(content), dtype=str, keep_default_na=False, engine="openpyxl")
    raise ValueError("unsupported_file")


def parse_dynamic_table(
    df: pd.DataFrame,
    q: Optional[str] = None,
    page: int = 1,
    per_page: int = 50,
) -> ParsedTable:
    if df is None:
        return ParsedTable(columns=[], rows=[], total_rows=0)

    # Normalizar columnas
    cols = _normalize_columns(list(df.columns))
    df = df.copy()
    df.columns = cols

    # Filtro por cualquier columna
    if q:
        needle = str(q).strip().lower()
        if needle:
            try:
                s = df.astype(str).apply(lambda row: " ".join(row.values.astype(str)), axis=1)
                mask = s.str.lower().str.contains(needle, na=False)
                df = df[mask]
            except Exception:
                pass

    total = int(len(df.index))

    # Paginación
    try:
        page = int(page or 1)
    except Exception:
        page = 1
    if page < 1:
        page = 1

    try:
        per_page = int(per_page or 50)
    except Exception:
        per_page = 50
    if per_page < 1:
        per_page = 50
    if per_page > 200:
        per_page = 200

    start = (page - 1) * per_page
    end = start + per_page
    df_page = df.iloc[start:end]

    # Rows como lista de dicts (strings)
    rows: List[Dict[str, str]] = []
    for _, r in df_page.iterrows():
        d: Dict[str, str] = {}
        for c in cols:
            try:
                v = r.get(c, "")
            except Exception:
                v = ""
            if v is None:
                v = ""
            d[c] = str(v)
        rows.append(d)

    return ParsedTable(columns=cols, rows=rows, total_rows=total)
