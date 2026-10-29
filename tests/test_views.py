"""The SQL views are the single definition of every KPI, so each one is re-derived here independently in pandas."""
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "data"))

import generate  # noqa: E402
from build import CSV, build_database  # noqa: E402


@pytest.fixture(scope="module")
def con():
    return build_database()          # loading enforces primary keys, foreign keys and check constraints


@pytest.fixture(scope="module")
def enc():
    return pd.read_csv(CSV / "fact_inpatient_encounter.csv", parse_dates=["admit_ts", "discharge_ts"])


def test_census_view_matches_independent_calculation(con, enc):
    sql = con.execute("""select c.calendar_date, d.dept_code, c.midnight_census
                         from vw_daily_census c join dim_department d using (department_key)""").df()
    py = generate._occupancy(enc, pd.read_csv(CSV / "fact_bed_capacity.csv"), pd.read_csv(CSV / "dim_department.csv"))
    m = sql.assign(date=pd.to_datetime(sql.calendar_date).dt.date).merge(py, on=["date", "dept_code"])
    assert len(m) == len(py) == 365 * 7
    assert (m.midnight_census == m.census).all()


def test_readmission_flags_match_pandas(con, enc):
    e = enc.sort_values(["patient_key", "admit_ts"]).copy()
    e["next_admit"] = e.groupby("patient_key").admit_ts.shift(-1)
    last_day = pd.Timestamp("2026-09-30")
    index = (e.discharge_ts.notna() & ~e.discharge_disposition.isin(["Expired", "Transferred", "Left AMA"])
             & (e.discharge_ts.dt.normalize() <= last_day - pd.Timedelta(days=30)))
    readmit = index & (e.next_admit <= e.discharge_ts + pd.Timedelta(days=30))
    v = con.execute("select sum(is_index_discharge), sum(readmitted_30d) from vw_inpatient_stay").fetchall()[0]
    assert v == (index.sum(), readmit.sum())


def test_no_index_discharge_without_30_days_of_follow_up(con):
    late = con.execute("""select count(*) from vw_inpatient_stay
                          where is_index_discharge = 1 and cast(discharge_ts as date) > date '2026-08-31'""").fetchall()[0][0]
    assert late == 0


def test_ed_medians_match_pandas(con):
    ed = pd.read_csv(CSV / "fact_ed_visit.csv", parse_dates=["arrival_ts", "provider_ts", "admit_decision_ts", "departure_ts"])
    d2p = ((ed.provider_ts - ed.arrival_ts).dt.total_seconds() / 60).median()
    board = ((ed.departure_ts - ed.admit_decision_ts).dt.total_seconds() / 60).median()
    lwbs = ed.disposition.eq("Left without being seen").mean()
    got = con.execute("select median(door_to_provider_min), median(boarding_min), avg(is_lwbs) from vw_ed_visit").fetchall()[0]
    assert got == pytest.approx((d2p, board, lwbs))


def test_monthly_scorecard_reconciles_to_detail(con):
    k = con.execute("select * from vw_monthly_hospital_kpi").df()
    assert len(k) == 12
    assert k.patient_days.sum() == con.execute("select sum(midnight_census) from vw_daily_census").fetchall()[0][0]
    assert k.ed_visits.sum() == con.execute("select count(*) from fact_ed_visit").fetchall()[0][0]
    assert k.admissions.sum() == con.execute(
        "select count(*) from fact_inpatient_encounter where admit_date_key >= 20251001").fetchall()[0][0]


def test_ed_admissions_link_to_an_admitted_ed_visit(con):
    bad = con.execute("""
        select count(*) from fact_inpatient_encounter e join fact_ed_visit v using (ed_visit_id)
        where v.disposition <> 'Admitted' or abs(extract(epoch from (e.admit_ts - v.departure_ts))) > 60""").fetchall()[0][0]
    assert bad == 0
