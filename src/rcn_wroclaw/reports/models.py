"""Stage-4 report: cleaning, splits, model × variant comparison (Polish text)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from rcn_wroclaw import config  # noqa: E402
from rcn_wroclaw.model.dataset import DATASET_PATH, fit_price_index  # noqa: E402
from rcn_wroclaw.model.evaluate import (  # noqa: E402
    IMPORTANCE_PATH,
    PREDICTIONS_PATH,
    TEST_START,
    metrics,
    summarize,
)
from rcn_wroclaw.reports.eda_rcn import SURFACE, TEXT_SECONDARY, _md_table, _style  # noqa: E402

VARIANT_ORDER = ["rcn_bez_osiedla", "rcn", "rcn_lok", "rcn_lok_sel", "rcn_lok_xy"]
VARIANT_LABEL = {
    "rcn_bez_osiedla": "RCN bez osiedla",
    "rcn": "RCN (+ osiedle)",
    "rcn_lok": "RCN + lokalizacja",
    "rcn_lok_sel": "RCN + lokalizacja (51 cech)",
    "rcn_lok_xy": "RCN + lokalizacja + x/y",
}
MODEL_COLOR = {"ridge": "#2a78d6", "lgbm": "#eb6834"}
MODEL_LABEL = {"ridge": "Ridge", "lgbm": "LightGBM"}


def _fig_mae(summary: pd.DataFrame, path: Path) -> None:
    splits = [("temporal", "Test czasowy (2026)"), ("spatial_cv", "Przestrzenna CV (2023–2025)")]
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.8), sharey=True)
    x = np.arange(len(VARIANT_ORDER))
    w = 0.38
    for ax, (split, title) in zip(axes, splits, strict=True):
        s = summary[summary["split"] == split].set_index(["model", "variant"])
        for k, model in enumerate(["ridge", "lgbm"]):
            vals = [s.loc[(model, v), "MAE"] for v in VARIANT_ORDER]
            bars = ax.bar(
                x + (k - 0.5) * w,
                vals,
                w * 0.92,
                color=MODEL_COLOR[model],
                label=MODEL_LABEL[model],
                edgecolor=SURFACE,
            )
            for b, v in zip(bars, vals, strict=True):
                ax.text(
                    b.get_x() + b.get_width() / 2,
                    v,
                    f"{v:,.0f}",
                    ha="center",
                    va="bottom",
                    fontsize=8,
                    color=TEXT_SECONDARY,
                )
        ax.set_xticks(
            x, [VARIANT_LABEL[v].replace(" + ", "\n+ ") for v in VARIANT_ORDER], fontsize=8
        )
        ax.set_title(title)
        ax.grid(axis="x", visible=False)
    axes[0].set_ylabel("MAE [zł/m²]")
    axes[0].legend(loc="lower left")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _fig_index(df: pd.DataFrame, path: Path) -> None:
    train = df[df["month"] < TEST_START]
    idx = np.exp(fit_price_index(train["month"], train["log_price_m2"]))
    monthly = df.groupby("month")["price_m2"].median()
    fig, ax = plt.subplots(figsize=(9, 3.4))
    ax.plot(
        monthly.index,
        monthly.to_numpy(),
        color="#eb6834",
        lw=2,
        label="mediana miesięczna (wszystkie dane)",
    )
    ax.plot(
        idx.index,
        idx.to_numpy(),
        color="#2a78d6",
        lw=2,
        label="indeks (trening, mediana krocząca 3 m-ce)",
    )
    ax.axvline(TEST_START, color=TEXT_SECONDARY, lw=1, ls="--")
    ax.text(TEST_START, ax.get_ylim()[1], " test", va="top", color=TEXT_SECONDARY)
    ax.set_ylabel("zł/m²")
    ax.set_title("Miesięczny indeks cen (cecha czasowa)")
    ax.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def build_report(out_md: Path, fig_dir: Path) -> Path:
    _style()
    fig_dir.mkdir(parents=True, exist_ok=True)
    df = pd.read_parquet(DATASET_PATH)
    pred = pd.read_parquet(PREDICTIONS_PATH)
    summary = summarize(pred)
    imp = pd.read_parquet(IMPORTANCE_PATH)
    clog = pd.read_parquet(config.PROCESSED_DIR / "cleaning_log.parquet")

    L: list[str] = []
    add = L.append
    add("# Etap 4 – czyszczenie i modele\n")
    add(f"Wygenerowano: {dt.datetime.now():%Y-%m-%d %H:%M}.\n")

    add("## 1. Czyszczenie\n")
    add(_md_table(clog.set_index("krok")) + "\n")
    add(
        f"Wynik: **{len(df):,} transakcji**, {df['month'].min():%Y-%m} – "
        f"{df['month'].max():%Y-%m}; rynek pierwotny {100 * df['is_primary'].mean():.1f}%, "
        f"flaga parkingu {100 * df['has_parking_note'].mean():.1f}%.\n"
    )
    add(
        "Cena/m² = `lok_cena_brutto / lok_pow_uzyt`; gdy brak ceny lokalu – "
        "`tran_cena_brutto / lok_pow_uzyt` tylko dla transakcji z jednym rekordem lokalu.\n"
    )

    add("## 2. Schemat walidacji\n")
    tr = df[df["month"] < TEST_START]
    add(
        f"- **Test czasowy:** trening {tr['month'].min():%Y-%m} – {tr['month'].max():%Y-%m} "
        f"({len(tr):,}), test od {TEST_START:%Y-%m} ({(df['month'] >= TEST_START).sum():,}). "
        "Wczesne zatrzymanie LightGBM na ostatnich 3 miesiącach treningu, potem douczenie na "
        "całym treningu (liczba drzew × 1,1)."
    )
    add(
        "- **Przestrzenna CV:** 5 foldów GroupKFold po blokach 1×1 km, tylko okres "
        "treningowy (bez danych z przyszłości). Walidacja LightGBM: 15% bloków treningowych."
    )
    add(
        "- **Indeks cen** liczony wyłącznie na danych treningowych danego podziału; dla "
        "miesięcy testowych przenoszona jest ostatnia znana wartość."
    )
    add(
        "- Cel modeli: log(cena/m²); metryki liczone po powrocie do zł/m² (i zł dla ceny "
        "całkowitej = predykcja/m² × powierzchnia).\n"
    )
    f_idx = fig_dir / "model_price_index.png"
    _fig_index(df, f_idx)
    add(f"![Indeks]({fig_dir.name}/{f_idx.name})\n")

    add("## 3. Warianty cech\n")
    add("| wariant | zawartość |\n|:--|:--|")
    add(
        "| RCN bez osiedla | powierzchnia, piętro, izby, pow./izbę, pom. przynależne, flaga "
        "parkingu, rynek, sprzedający, kupujący, miesiąc, indeks cen |"
    )
    add("| RCN (+ osiedle) | jw. + osiedle (48 kategorii) – **wariant bazowy** |")
    add("| RCN + lokalizacja | jw. + 115 cech dostępności z etapu 3 (bez współrzędnych) |")
    add(
        "| RCN + lokalizacja (51 cech) | jak wyżej, ale 1 promień (500 m) na rodzinę cech, "
        "odległości piesze zamiast prostych |"
    )
    add("| RCN + lokalizacja + x/y | jw. + współrzędne (górna granica „czystej” lokalizacji) |\n")

    add("## 4. Wyniki\n")
    f_mae = fig_dir / "model_mae.png"
    _fig_mae(summary, f_mae)
    add(f"![MAE]({fig_dir.name}/{f_mae.name})\n")
    for split, title in [
        ("temporal", "Test czasowy 2026"),
        ("spatial_cv", "Przestrzenna CV (wyniki zbiorcze z 5 foldów)"),
    ]:
        s = summary[summary["split"] == split].copy()
        s["wariant"] = s["variant"].map(VARIANT_LABEL)
        s["model"] = s["model"].map(MODEL_LABEL)
        s["_o"] = s["variant"].map({v: i for i, v in enumerate(VARIANT_ORDER)})
        s = s.sort_values(["model", "_o"]).set_index(["model", "wariant"])
        add(f"### {title} (n = {int(s['n'].iloc[0]):,})\n")
        cols = ["MAE", "RMSE", "MAPE", "R2", "MAE_total", "MAPE_total"]
        add(_md_table(s[cols], floatfmt=",.3f") + "\n")

    add("### Zysk z cech lokalizacyjnych (względem wariantu bazowego „RCN + osiedle”)\n")
    rows = []
    for (split, model), g in summary[summary["split"].isin(["temporal", "spatial_cv"])].groupby(
        ["split", "model"]
    ):
        g = g.set_index("variant")
        for v, ref in [("rcn_lok", "rcn"), ("rcn_lok_xy", "rcn"), ("rcn", "rcn_bez_osiedla")]:
            rows.append({
                "podział": split, "model": MODEL_LABEL[model],
                "porównanie": f"{VARIANT_LABEL[v]} vs {VARIANT_LABEL[ref]}",
                "ΔMAE [zł/m²]": g.loc[v, "MAE"] - g.loc[ref, "MAE"],
                "ΔMAE [%]": 100 * (g.loc[v, "MAE"] / g.loc[ref, "MAE"] - 1),
                "ΔR²": g.loc[v, "R2"] - g.loc[ref, "R2"],
            })  # fmt: skip
    add(
        _md_table(pd.DataFrame(rows).set_index(["podział", "model", "porównanie"]), floatfmt=",.3f")
        + "\n"
    )

    sp = pred[pred["split"] == "spatial_cv"]
    folds = (
        sp.groupby(["model", "variant", "fold"])
        .apply(lambda g: metrics(g["price_m2"], g["pred_m2"])["MAE"], include_groups=False)
        .unstack("fold")
    )
    folds.index = [f"{MODEL_LABEL[m]} / {VARIANT_LABEL[v]}" for m, v in folds.index]
    add(
        "MAE w poszczególnych foldach przestrzennych (rozrzut ≈ niepewność; pełne przedziały "
        "bootstrap w etapie 5):\n"
    )
    add(_md_table(folds, floatfmt=",.0f") + "\n")

    add("## 5. Diagnostyka testu czasowego\n")
    t = pred[(pred["split"] == "temporal") & (pred["model"] == "lgbm")
             & (pred["variant"].isin(["rcn", "rcn_lok"]))]  # fmt: skip
    t = t.assign(
        err=t["pred_m2"] - t["price_m2"],
        rynek=np.where(t["is_primary"] == 1, "pierwotny", "wtórny"),
    )
    by_m = t.pivot_table(index="month", columns="variant", values="err", aggfunc="median")
    by_m.index = by_m.index.strftime("%Y-%m")
    add(
        "Mediana błędu (predykcja − cena) wg miesiąca, LightGBM – ujemna = niedoszacowanie "
        "(efekt przeniesionego indeksu przy rosnących cenach):\n"
    )
    add(_md_table(by_m.rename(columns=VARIANT_LABEL), floatfmt=",.0f") + "\n")
    by_r = (
        t.groupby(["rynek", "variant"])
        .apply(lambda g: pd.Series(metrics(g["price_m2"], g["pred_m2"])), include_groups=False)[
            ["MAE", "MAPE"]
        ]
        .unstack("variant")
    )
    add("MAE / MAPE wg rynku (LightGBM):\n")
    add(_md_table(by_r, floatfmt=",.1f") + "\n")

    # Buildings that sell in both periods let a flexible model "recognise" the location.
    train_locs = set(df.loc[df["month"] < TEST_START, "location_id"])
    tt = pred[pred["split"] == "temporal"].merge(df[["gid", "location_id"]], on="gid")
    tt["lokalizacja"] = np.where(
        tt["location_id"].isin(train_locs), "widziana w treningu", "nowa (niewidziana)"
    )
    share_seen = (tt.drop_duplicates("gid")["lokalizacja"] == "widziana w treningu").mean()
    seen = (
        tt[tt["variant"].isin(["rcn", "rcn_lok"])]
        .groupby(["lokalizacja", "model", "variant"])
        .apply(lambda g: metrics(g["price_m2"], g["pred_m2"])["MAE"], include_groups=False)
        .unstack("variant")
        .rename(columns=VARIANT_LABEL)
    )
    seen["ΔMAE %"] = 100 * (seen[VARIANT_LABEL["rcn_lok"]] / seen[VARIANT_LABEL["rcn"]] - 1)
    seen["n"] = (
        tt.drop_duplicates("gid")
        .groupby("lokalizacja")
        .size()
        .reindex(seen.index.get_level_values(0))
        .to_numpy()
    )
    add(
        f"**Lokalizacje widziane w treningu:** {100 * share_seen:.1f}% transakcji testowych "
        "leży w punkcie (budynku), który miał transakcje w okresie treningowym. MAE wg tego "
        "podziału:\n"
    )
    add(_md_table(seen, floatfmt=",.1f") + "\n")

    add("## 6. Ważność cech (LightGBM, gain, test czasowy)\n")
    for v in ("rcn", "rcn_lok"):
        g = imp[(imp["split"] == "temporal") & (imp["variant"] == v)].nlargest(15, "gain")
        g = g.assign(
            udział=100
            * g["gain"]
            / imp[(imp["split"] == "temporal") & (imp["variant"] == v)]["gain"].sum()
        )
        add(f"**{VARIANT_LABEL[v]}** – top 15 (% całkowitego gain):\n")
        add(_md_table(g.set_index("feature")[["udział"]], floatfmt=".1f") + "\n")
    add("SHAP i przedziały ufności – etap 5.\n")

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(L), encoding="utf-8")
    return out_md
