import pandas as pd
import io, re

TEST_PATTERNS = {
    "CTPI":       [r"\bctpi\b", r"манлайлах", r"стратеги", r"өөрчлөлт удирдах"],
    "Big5":       [r"\bbig.?5\b", r"нийтэч", r"нягт нямбай", r"нээлттэй",
                   r"sociability", r"meticulousness", r"emotional.?balance"],
    "PP":         [r"\bpp2?\b", r"professional.?profile", r"desire.?to.?lead",
                   r"удирдан чиглүүлэх", r"ятган нөлөөлөх"],
    "VOC":        [r"\bvoc\b", r"мэргэжлийн сонирхол"],
    "EQ":         [r"\beq\b", r"emotional.?intell", r"сэтгэл хөдлөлийн"],
    "MOTIVATION": [r"\bmotivation\b", r"сэдэл"],
    "SALES":      [r"\bsales.?competency\b", r"борлуулалт"],
}

def detect_test_type(columns):
    cols_lower = " ".join(str(c).lower() for c in columns)
    found = {}
    for test, patterns in TEST_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, cols_lower, re.IGNORECASE):
                found[test] = True
                break
    return found

def find_name_column(df):
    name_hints = ["нэр", "name", "ажилтны нэр", "хэн", "овог нэр", "овог", "employee"]
    for col in df.columns:
        if any(h in str(col).lower() for h in name_hints):
            return col
    for col in df.columns:
        if df[col].dtype == object:
            return col
    return None

def smart_column_selection(df, max_cols=20):
    num_cols = df.select_dtypes(include="number").columns.tolist()
    text_cols = df.select_dtypes(exclude="number").columns.tolist()
    name_col = find_name_column(df)
    keep_text = [name_col] if name_col and name_col in df.columns else text_cols[:2]
    if len(num_cols) <= max_cols:
        return df[keep_text + num_cols], 0, len(num_cols)
    stds = df[num_cols].std().sort_values(ascending=False)
    selected_num = stds.head(max_cols).index.tolist()
    dropped = len(num_cols) - max_cols
    return df[keep_text + selected_num], dropped, len(num_cols)

def process_excel(file_bytes: bytes, filename: str, question: str) -> dict:
    # ── Файл унших ──────────────────────────────────────────────────────────
    try:
        if filename.lower().endswith(".csv"):
            # UTF-8 эхлээд, дараа нь cp1251 оролдоно
            try:
                df = pd.read_csv(io.BytesIO(file_bytes), encoding="utf-8")
            except UnicodeDecodeError:
                df = pd.read_csv(io.BytesIO(file_bytes), encoding="cp1251")
        else:
            df = pd.read_excel(io.BytesIO(file_bytes))
    except Exception as e:
        raise ValueError(f"Файл унших боломжгүй: {e}")

    # ── Цэвэрлэгээ ──────────────────────────────────────────────────────────
    df = df.dropna(how="all").dropna(axis=1, how="all")
    df.columns = [str(c).strip() for c in df.columns]

    if df.empty or len(df.columns) == 0:
        raise ValueError("Файл хоосон байна эсвэл боловсруулах боломжтой өгөгдөл байхгүй.")

    original_col_count = len(df.columns)
    num_cols_all = df.select_dtypes(include="number").columns.tolist()
    name_col = find_name_column(df)
    detected_tests = detect_test_type(list(df.columns))

    # ── Багана сонголт ───────────────────────────────────────────────────────
    dropped_cols = 0
    if len(num_cols_all) > 20:
        df, dropped_cols, _ = smart_column_selection(df, max_cols=20)
        num_cols = df.select_dtypes(include="number").columns.tolist()
    else:
        num_cols = num_cols_all

    # ── Summary бүтээх ───────────────────────────────────────────────────────
    test_list = ", ".join(detected_tests.keys()) if detected_tests else "Тодорхойгүй"
    summary_parts = [
        f"Файл: {filename}",
        f"Нийт мөр: {len(df)}, Нийт багана: {original_col_count}",
        f"Илэрсэн тестүүд: {test_list}",
    ]
    if dropped_cols > 0:
        summary_parts.append(f"⚠ {dropped_cols} багана орхигдлоо (хамгийн ялгаатай 20-г авлаа)")
    summary_parts.append(f"Баганууд: {list(df.columns)}")
    summary_parts.append("")

    if len(df) == 0:
        summary_parts.append("⚠ Өгөгдөл байхгүй (мөр тоо: 0)")

    elif len(df) <= 30:
        # Бүх өгөгдлийг харуулна
        summary_parts.append("=== Бүх өгөгдөл ===")
        summary_parts.append(df.to_string(index=False))

    else:
        # 30-аас дээш мөр: top/bottom + статистик
        if num_cols:
            df2 = df.copy()
            df2["__avg__"] = df2[num_cols].mean(axis=1).round(2)
            top5 = df2.nlargest(5, "__avg__").drop(columns=["__avg__"])
            bot5 = df2.nsmallest(5, "__avg__").drop(columns=["__avg__"])
            summary_parts.append("=== ТОП 5 (өндөр оноо) ===")
            summary_parts.append(top5.to_string(index=False))
            summary_parts.append("\n=== Хамгийн бага оноотой 5 мөр ===")
            summary_parts.append(bot5.to_string(index=False))
            summary_parts.append("\n=== Статистик ===")
            summary_parts.append(df[num_cols].describe().round(2).to_string())
        else:
            # ── FIX: тоон багана байхгүй үед describe() дуудахгүй ──────────
            summary_parts.append("⚠ Тоон өгөгдөл байхгүй — текст баганууд:")
            summary_parts.append(df.head(10).to_string(index=False))

    summary = "\n".join(summary_parts)
    raw_data = df.head(200).fillna("").to_dict(orient="records")

    return {
        "summary":        summary,
        "columns":        list(df.columns),
        "rows":           len(df),
        "raw_data":       raw_data,
        "detected_tests": list(detected_tests.keys()),
        "dropped_cols":   dropped_cols,
        "name_col":       name_col,
        "num_cols":       num_cols,
        "prompt_data":    summary,
    }

def build_excel_prompt(processed: dict, question: str, base_prompt: str) -> str:
    if processed["detected_tests"]:
        test_ctx = f"\nИлэрсэн тестүүд: {', '.join(processed['detected_tests'])}\n"
    else:
        test_ctx = "\nАнхааруулга: Тестийн нэрийг автоматаар тодорхойлж чадсангүй.\n"
    return base_prompt + test_ctx + f"\nАсуулт: {question}\n\nӨгөгдөл:\n{processed['prompt_data']}"
