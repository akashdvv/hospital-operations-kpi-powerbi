"""
Build the reporting layer without a database server.

    python data/generate.py     # synthetic source data -> data/csv/
    python build.py             # schema + views in DuckDB -> data/powerbi/ (Power BI-ready CSVs),
                                #   reports/reference_kpis.md, images/*.png

The same three SQL files load into PostgreSQL with psql (see sql/02_load_postgres.sql).
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parent
CSV, PBI, SQL = ROOT / "data" / "csv", ROOT / "data" / "powerbi", ROOT / "sql"
LOAD_ORDER = ["dim_date", "dim_department", "dim_payer", "dim_patient", "fact_bed_capacity",
              "fact_ed_visit", "fact_inpatient_encounter"]
# table name in Power BI  <-  source
EXPORTS = {"Date": "dim_date", "Department": "dim_department", "Payer": "dim_payer", "Patient": "dim_patient",
           "Daily Census": "vw_daily_census", "Inpatient Stays": "vw_inpatient_stay", "ED Visits": "vw_ed_visit"}
BLUE, ORANGE, AQUA, INK, INK_2, GRID = "#2a78d6", "#eb6834", "#1baf7a", "#0b0b0b", "#52514e", "#e4e3df"


def build_database(path: str = ":memory:", csv_dir: Path = CSV) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(path)
    con.execute((SQL / "01_schema.sql").read_text())
    for t in LOAD_ORDER:
        con.execute(f"insert into {t} select * from read_csv('{csv_dir / (t + '.csv')}', header=true)")
    con.execute((SQL / "03_views.sql").read_text())
    return con


def export_for_powerbi(con) -> None:
    PBI.mkdir(parents=True, exist_ok=True)
    for name, src in EXPORTS.items():
        con.execute(f"copy (select * from {src} order by all) to '{PBI / (name.lower().replace(' ', '_') + '.csv')}' (header, delimiter ',')")


def reference_kpis(con) -> pd.DataFrame:
    return con.execute("select * from vw_monthly_hospital_kpi").df()


def dept_summary(con) -> pd.DataFrame:
    return con.execute("""
        with occ as (
            select department_key, sum(midnight_census) * 1.0 / sum(staffed_beds) as occupancy
            from vw_daily_census group by department_key),
        stays as (
            select department_key,
                   count(discharge_ts)                                  as discharges,
                   sum(los_days) * 1.0 / count(discharge_ts)            as alos,
                   sum(readmitted_30d) * 1.0 / nullif(sum(is_index_discharge), 0) as readmit_rate,
                   sum(is_index_discharge)                              as index_discharges
            from vw_inpatient_stay where discharge_date_key >= 20251001 group by department_key)
        select d.dept_code, d.dept_name, o.occupancy, s.discharges, s.alos, s.readmit_rate, s.index_discharges
        from dim_department d join occ o using (department_key) join stays s using (department_key)
        order by o.occupancy desc""").df()


def write_reference(k: pd.DataFrame, depts: pd.DataFrame, con) -> str:
    tot = con.execute("""
        select sum(midnight_census) * 1.0 / sum(staffed_beds) from vw_daily_census""").fetchall()[0][0]
    yr = con.execute("""
        select count(*) filter (where admit_date_key >= 20251001),
               count(discharge_ts) filter (where discharge_date_key >= 20251001),
               sum(los_days) filter (where discharge_date_key >= 20251001) * 1.0
                   / count(discharge_ts) filter (where discharge_date_key >= 20251001),
               sum(readmitted_30d) * 1.0 / sum(is_index_discharge)
        from vw_inpatient_stay""").fetchall()[0]
    ed = con.execute("""
        select count(*), median(door_to_provider_min), avg(is_lwbs), median(boarding_min) from vw_ed_visit""").fetchall()[0]
    lines = ["# Reference KPI values (FY2026: Oct 2025 – Sep 2026)", "",
             "Computed by the SQL views. Your Power BI visuals should match these numbers exactly; if one",
             "doesn't, check the relationship or filter context of that measure first.", "",
             "## FY2026 totals (cards)", "", "| Measure | Value |", "|---|---|",
             f"| Admissions | {yr[0]:,} |", f"| Discharges | {yr[1]:,} |", f"| Occupancy % | {tot:.1%} |",
             f"| ALOS (days) | {yr[2]:.2f} |", f"| Readmission Rate 30d | {yr[3]:.1%} |",
             f"| ED Visits | {ed[0]:,} |", f"| Median Door-to-Provider (min) | {ed[1]:.1f} |",
             f"| LWBS Rate | {ed[2]:.1%} |", f"| Median Boarding (hrs) | {ed[3] / 60:.1f} |", "",
             "## By month", "",
             "| Month | Admissions | Discharges | Occupancy | ALOS | Readmit 30d | ED visits | Door-to-provider (min) | LWBS | Boarding (hrs) |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in k.itertuples():
        rr = "—" if pd.isna(r.readmission_rate_30d) else f"{r.readmission_rate_30d:.1%}"
        lines.append(f"| {r.year_month} | {r.admissions:,} | {r.discharges:,} | {r.occupancy_rate:.1%} | {r.alos_days:.2f} | "
                     f"{rr} | {r.ed_visits:,} | {r.median_door_to_provider_min:.1f} | {r.lwbs_rate:.1%} | "
                     f"{r.median_boarding_min / 60:.1f} |")
    lines += ["", "September has no readmission rate: its discharges don't yet have 30 days of follow-up.", "",
              "## By department (FY2026)", "", "| Department | Occupancy | Discharges | ALOS | Readmit 30d |", "|---|---|---|---|---|"]
    for r in depts.itertuples():
        lines.append(f"| {r.dept_name} | {r.occupancy:.1%} | {r.discharges:,} | {r.alos:.2f} | {r.readmit_rate:.1%} |")
    md = "\n".join(lines) + "\n"
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "reference_kpis.md").write_text(md)
    return md


def _style(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.yaxis.grid(True, color=GRID)
    ax.set_axisbelow(True)


def charts(con) -> None:
    (ROOT / "images").mkdir(exist_ok=True)
    m = con.execute("""
        select d.year_month, dep.dept_code, sum(c.midnight_census) * 1.0 / sum(c.staffed_beds) as occ
        from vw_daily_census c join dim_date d using (date_key) join dim_department dep using (department_key)
        group by 1, 2
        union all
        select d.year_month, 'Hospital', sum(c.midnight_census) * 1.0 / sum(c.staffed_beds)
        from vw_daily_census c join dim_date d using (date_key) group by 1
        order by 1""").df().pivot(index="year_month", columns="dept_code", values="occ")
    labels = pd.to_datetime(m.index).strftime("%b %y")
    fig, ax = plt.subplots(figsize=(10, 4))
    for col, color, name in [("MED", BLUE, "General Medicine"), ("ICU", ORANGE, "Intensive Care"), ("Hospital", AQUA, "Whole hospital")]:
        ax.plot(labels, m[col] * 100, marker="o", color=color, linewidth=2, label=name)
    ax.axhline(85, color=INK_2, linestyle="--", linewidth=0.9)
    ax.text(len(labels) - 1, 86.2, "85% safe-occupancy threshold", ha="right", fontsize=9, color=INK_2)
    ax.annotate("Jan 12: ICU staffed beds 16 → 20", xy=(3.08, m.loc["2026-01", "ICU"] * 100), xytext=(4.6, 108),
                fontsize=9, color=INK_2, arrowprops=dict(arrowstyle="->", color=INK_2))
    ax.set_title("Monthly bed occupancy (midnight census ÷ staffed beds)", loc="left", fontweight="bold")
    ax.set_ylabel("Occupancy %")
    ax.set_ylim(40, 115)
    ax.legend(frameon=False, ncol=3, loc="lower left")
    _style(ax)
    fig.tight_layout()
    fig.savefig(ROOT / "images" / "occupancy_by_month.png", dpi=150)
    plt.close(fig)

    w = con.execute("""
        with occ as (
            select d.week_start, sum(c.midnight_census) * 1.0 / sum(c.staffed_beds) as med_occ
            from vw_daily_census c join dim_date d using (date_key)
            where c.department_key = (select department_key from dim_department where dept_code = 'MED')
            group by 1),
        ed as (
            select d.week_start, median(v.boarding_min) / 60 as boarding_h, avg(v.is_lwbs) as lwbs
            from vw_ed_visit v join dim_date d on d.date_key = v.arrival_date_key group by 1)
        select * from occ join ed using (week_start) order by week_start""").df()
    w = w.iloc[1:-1]   # drop partial first/last weeks
    fig, axes = plt.subplots(3, 1, figsize=(10, 6.6), sharex=True)
    for ax, (col, scale, color, title) in zip(axes, [
            ("med_occ", 100, BLUE, "General Medicine occupancy (%)"),
            ("boarding_h", 1, ORANGE, "Median ED boarding time for admitted patients (hours)"),
            ("lwbs", 100, INK_2, "ED patients who left without being seen (%)")]):
        ax.plot(pd.to_datetime(w.week_start), w[col] * scale, color=color, linewidth=2)
        ax.set_title(title, loc="left", fontsize=10, fontweight="bold")
        _style(ax)
    axes[0].axhline(85, color=INK_2, linestyle="--", linewidth=0.9)
    fig.suptitle("When Medicine beds fill, the ED backs up (weekly)", x=0.01, ha="left", fontweight="bold")
    fig.tight_layout()
    fig.savefig(ROOT / "images" / "occupancy_drives_ed_boarding.png", dpi=150)
    plt.close(fig)

    d = dept_summary(con).sort_values("readmit_rate")
    overall = con.execute("select sum(readmitted_30d) * 1.0 / sum(is_index_discharge) from vw_inpatient_stay").fetchall()[0][0]
    fig, ax = plt.subplots(figsize=(8, 3.8))
    ax.barh(d.dept_name, d.readmit_rate * 100, color=BLUE, height=0.6)
    ax.axvline(overall * 100, color=INK_2, linestyle="--", linewidth=0.9)
    ax.text(overall * 100 + 0.3, len(d) - 0.45, f"hospital {overall:.1%}", fontsize=9, color=INK_2)
    for y, v in enumerate(d.readmit_rate):
        ax.text(v * 100 + 0.3, y, f"{v:.1%}", va="center", fontsize=9, color=INK)
    ax.set_title("30-day readmission rate by discharging department", loc="left", fontweight="bold")
    ax.set_xlabel("% of index discharges readmitted within 30 days")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.xaxis.grid(True, color=GRID)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(ROOT / "images" / "readmission_by_department.png", dpi=150)
    plt.close(fig)


def main() -> None:
    con = build_database()
    export_for_powerbi(con)
    k = reference_kpis(con)
    print(write_reference(k, dept_summary(con), con))
    charts(con)


if __name__ == "__main__":
    main()
