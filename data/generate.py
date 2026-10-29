"""
Generate 12 months (Oct 2025 - Sep 2026) of synthetic hospital operations data as a star schema.

    dim_date, dim_department, dim_payer, dim_patient
    fact_inpatient_encounter   one row per inpatient stay
    fact_ed_visit              one row per emergency department visit
    fact_bed_capacity          staffed beds per department per day

The simulation has a cause-and-effect story built in, so the dashboard has something to find:
a winter respiratory surge fills Medicine beds, admitted ED patients wait longer for a bed
(boarding), and the ED backs up: longer waits to see a provider and more patients leaving
without being seen. ICU staffed beds rise from 16 to 20 on 2026-01-12 in response.

    python data/generate.py      # writes data/csv/*.csv
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "csv"
SEED = 2026
START, END = date(2025, 10, 1), date(2026, 9, 30)
SIM_START = date(2025, 8, 1)          # warm-up so the hospital is not empty on Oct 1

# code: name, service line, staffed beds, daily admissions, mean LOS (days), share via ED, 30-day readmit prob
DEPTS = {
    "MED":  ("General Medicine", "Medicine", 64, 8.3, 4.6, 0.80, 0.150),
    "CARD": ("Cardiology",       "Medicine", 24, 3.6, 4.1, 0.65, 0.140),
    "ICU":  ("Intensive Care",   "Critical Care", 16, 2.3, 5.2, 0.70, 0.110),
    "SURG": ("General Surgery",  "Surgery", 36, 6.0, 4.2, 0.35, 0.090),
    "ORTH": ("Orthopedics",      "Surgery", 20, 3.8, 3.1, 0.20, 0.060),
    "OB":   ("Obstetrics",       "Women & Children", 18, 5.2, 2.3, 0.10, 0.030),
    "PEDS": ("Pediatrics",       "Women & Children", 16, 2.6, 3.0, 0.55, 0.050),
}
PAYERS = [("MCR", "Medicare", "Government"), ("MCD", "Medicaid", "Government"),
          ("COM", "Commercial", "Commercial"), ("SELF", "Self-pay", "Self-pay")]
ICU_EXPANSION = date(2026, 1, 12)


def seasonal(d: date, dept: str) -> float:
    """Winter respiratory surge, strongest in Medicine/ICU/Peds, peaking mid-January."""
    day = (d - date(2026, 1, 15)).days
    bump = np.exp(-(day / 32) ** 2)
    amp = {"MED": 0.24, "ICU": 0.30, "PEDS": 0.40, "CARD": 0.12}.get(dept, 0.0)
    weekday = 0.88 if d.weekday() >= 5 and dept in ("SURG", "ORTH") else 1.0   # fewer elective admits at weekends
    return (1 + amp * bump) * weekday


def main(out: Path = OUT) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(SEED)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    days = pd.date_range(START, END, freq="D").date

    # ---------- dimensions ----------
    dim_date = pd.DataFrame({"date": pd.date_range(SIM_START, END, freq="D")})
    dim_date["date_key"] = dim_date.date.dt.strftime("%Y%m%d").astype(int)
    dim_date["year"] = dim_date.date.dt.year
    dim_date["month_num"] = dim_date.date.dt.month
    dim_date["month_name"] = dim_date.date.dt.strftime("%b")
    dim_date["year_month"] = dim_date.date.dt.strftime("%Y-%m")
    dim_date["fiscal_year"] = "FY" + (dim_date.year + (dim_date.month_num >= 10)).astype(str)   # FY starts Oct
    dim_date["fiscal_month"] = (dim_date.month_num - 10) % 12 + 1
    dim_date["week_start"] = (dim_date.date - pd.to_timedelta(dim_date.date.dt.weekday, unit="D")).dt.date
    dim_date["day_name"] = dim_date.date.dt.strftime("%a")
    dim_date["is_weekend"] = dim_date.date.dt.weekday >= 5
    dim_date["in_reporting_period"] = dim_date.date.dt.date >= START     # Aug-Sep 2025 only hold opening-census admits
    dim_date["date"] = dim_date.date.dt.date
    dim_date = dim_date.rename(columns={"date": "calendar_date"})
    dim_date = dim_date[["date_key", "calendar_date", "year", "month_num", "month_name", "year_month", "fiscal_year",
                         "fiscal_month", "week_start", "day_name", "is_weekend", "in_reporting_period"]]

    dim_department = pd.DataFrame([{"department_key": i, "dept_code": c, "dept_name": v[0], "service_line": v[1]}
                                   for i, (c, v) in enumerate(DEPTS.items(), 1)])
    dkey = dict(zip(dim_department.dept_code, dim_department.department_key))
    dim_payer = pd.DataFrame([{"payer_key": i, "payer_code": c, "payer_name": n, "payer_group": g}
                              for i, (c, n, g) in enumerate(PAYERS, 1)])

    n_pat = 26000
    ages = rng.choice(["0-17", "18-44", "45-64", "65-79", "80+"], n_pat, p=[0.10, 0.25, 0.28, 0.24, 0.13])
    dim_patient = pd.DataFrame({"patient_key": np.arange(1, n_pat + 1), "age_group": ages,
                                "sex": rng.choice(["F", "M"], n_pat, p=[0.53, 0.47])})
    by_age = {a: dim_patient.patient_key[dim_patient.age_group == a].to_numpy() for a in dim_patient.age_group.unique()}

    # ---------- capacity ----------
    cap = [{"date_key": int(d.strftime("%Y%m%d")), "department_key": dkey[c],
            "staffed_beds": (20 if c == "ICU" and d >= ICU_EXPANSION else v[2])}
           for d in dim_date.calendar_date for c, v in DEPTS.items()]
    fact_bed_capacity = pd.DataFrame(cap)

    # ---------- inpatient stays ----------
    def pick_patient(dept):
        if dept == "PEDS":
            pool = by_age["0-17"]
        elif dept == "OB":
            pool = by_age["18-44"]
        else:
            pool = np.concatenate([by_age["45-64"], by_age["65-79"], by_age["80+"], by_age["18-44"][:2000]])
        return int(rng.choice(pool))

    def payer_for(pkey):
        age = dim_patient.age_group.iat[pkey - 1]
        p = {"65-79": [.78, .06, .14, .02], "80+": [.88, .04, .07, .01], "0-17": [0, .48, .48, .04]}.get(age, [.08, .26, .58, .08])
        return int(rng.choice([1, 2, 3, 4], p=p))

    stays = []
    for d in pd.date_range(SIM_START, END, freq="D").date:
        for c, (_, _, _, rate, los_mean, ed_share, readmit_p) in DEPTS.items():
            for _ in range(rng.poisson(rate * seasonal(d, c))):
                stays.append((d, c, pick_patient(c), False))
    # process stays in date order; a discharge can schedule a 30-day readmission later in the queue
    enc_rows = []
    queue = sorted(stays, key=lambda s: s[0])
    i = 0
    eid = 0
    while i < len(queue):
        d, c, pk, is_readmit = queue[i]
        i += 1
        name, line, beds, rate, los_mean, ed_share, readmit_p = DEPTS[c]
        winter = 1 + 0.18 * np.exp(-(((d - date(2026, 1, 15)).days) / 35) ** 2) * (c in ("MED", "ICU"))
        los = max(0.25, rng.lognormal(np.log(los_mean * winter) - 0.245, 0.7))
        hour = int(rng.choice(24, p=_admit_hour_profile()))
        admit_ts = datetime(d.year, d.month, d.day, hour, int(rng.integers(60)))
        disch_ts = admit_ts + timedelta(days=float(los))
        disch_ts = disch_ts.replace(hour=int(np.clip(rng.normal(14, 2.5), 8, 22)), minute=int(rng.integers(60)))
        if disch_ts <= admit_ts:
            disch_ts = admit_ts + timedelta(hours=float(rng.uniform(6, 20)))
        expired_p = {"ICU": 0.09, "MED": 0.025, "CARD": 0.02}.get(c, 0.004)
        disp = rng.choice(["Home", "Home health", "Skilled nursing facility", "Expired", "Left AMA", "Transferred"],
                          p=_disposition_p(c, expired_p))
        source = "ED" if rng.random() < (0.9 if is_readmit else ed_share) else ("Elective" if c in ("SURG", "ORTH", "OB") else "Direct/Referral")
        eid += 1
        enc_rows.append({"encounter_id": f"IP{eid:06d}", "patient_key": pk, "department_key": dkey[c],
                         "payer_key": payer_for(pk), "admit_ts": admit_ts, "discharge_ts": disch_ts,
                         "admission_source": source, "discharge_disposition": disp})
        # readmission risk is higher after long stays and for SNF discharges
        p = readmit_p * (1.35 if los > los_mean * 1.5 else 1.0) * (1.3 if disp == "Skilled nursing facility" else 1.0)
        if disp not in ("Expired", "Transferred") and rng.random() < p:
            gap = int(min(29, rng.exponential(9))) + 1
            rd = disch_ts.date() + timedelta(days=gap)
            if rd <= END:
                rc = c if rng.random() < 0.55 else ("MED" if c != "PEDS" else "PEDS")
                _insort(queue, i, (rd, rc, pk, True))
    enc = pd.DataFrame(enc_rows)
    enc = enc[enc.admit_ts.dt.date <= END]
    # keep stays admitted in FY2026, plus warm-up stays still in a bed on Oct 1 (the opening census)
    enc = enc[(enc.admit_ts.dt.date >= START) | (enc.discharge_ts.dt.date >= START)].reset_index(drop=True)
    still_in = enc.discharge_ts.dt.date > END
    enc["discharge_ts"] = enc.discharge_ts.where(~still_in)        # still in hospital at extract time
    enc.loc[still_in, "discharge_disposition"] = None

    # ---------- daily midnight occupancy (drives ED crowding) ----------
    occ = _occupancy(enc, fact_bed_capacity, dim_department)

    # ---------- ED visits ----------
    ed_rows, ed_link = [], {}
    admitted = enc[enc.admission_source == "ED"]
    for r in admitted.itertuples():
        ed_link[r.encounter_id] = r
    n_other = {d: rng.poisson(112 * (1 + 0.30 * np.exp(-(((d - date(2026, 1, 12)).days) / 30) ** 2))) for d in days}
    vid = 0

    def ed_times(arrival, esi, admitted_flag, med_occ):
        crowd = min(1.5, max(0.0, med_occ - 0.85) * 10)   # 0 at <=85% Medicine occupancy, 1.0 at 95%, capped
        night = 0.7 if arrival.hour < 8 else 1.0
        d2p = {1: 2, 2: 9, 3: 26, 4: 38, 5: 42}[esi] * night * (1 + 1.6 * crowd) * rng.lognormal(0, 0.45)
        triage = arrival + timedelta(minutes=float(rng.uniform(2, 12)))
        provider = triage + timedelta(minutes=float(max(1, d2p)))
        workup = rng.lognormal(np.log({1: 150, 2: 200, 3: 210, 4: 95, 5: 60}[esi]), 0.45)
        decision = provider + timedelta(minutes=float(workup))
        if admitted_flag:
            boarding = rng.lognormal(np.log(95 * (1 + 4.0 * crowd ** 1.3)), 0.6)
            return triage, provider, decision, decision + timedelta(minutes=float(boarding)), crowd
        return triage, provider, None, decision + timedelta(minutes=float(rng.uniform(5, 25))), crowd

    med_occ = occ[occ.dept_code == "MED"].set_index("date").occupancy.to_dict()
    for eidx, r in ed_link.items():
        vid += 1
        dep = r.admit_ts - timedelta(minutes=0)
        esi = int(rng.choice([1, 2, 3], p=[0.06, 0.38, 0.56]))
        # work backwards: build times from an arrival placed before the admit timestamp
        arrival = r.admit_ts - timedelta(hours=float(rng.uniform(4, 9)))
        tr, pr, dec, departure, _ = ed_times(arrival, esi, True, med_occ.get(arrival.date(), 0.8))
        shift = r.admit_ts - departure                     # align ED departure with the inpatient admit time
        ed_rows.append({"ed_visit_id": f"ED{vid:07d}", "patient_key": r.patient_key, "arrival_ts": arrival + shift,
                        "triage_ts": tr + shift, "provider_ts": pr + shift, "admit_decision_ts": dec + shift,
                        "departure_ts": departure + shift, "esi_level": esi, "disposition": "Admitted",
                        "encounter_id": eidx})
    all_pk = dim_patient.patient_key.to_numpy()
    for d in days:
        mo = med_occ.get(d, 0.8)
        for _ in range(n_other[d]):
            vid += 1
            hour = int(rng.choice(24, p=_ed_hour_profile()))
            arrival = datetime(d.year, d.month, d.day, hour, int(rng.integers(60)))
            esi = int(rng.choice([1, 2, 3, 4, 5], p=[0.003, 0.08, 0.42, 0.38, 0.117]))
            tr, pr, _, departure, crowd = ed_times(arrival, esi, False, mo)
            wait = (pr - tr).total_seconds() / 60
            lwbs_p = 0.004 + 0.0011 * max(0, wait - 20) if esi >= 3 else 0.0
            if rng.random() < min(lwbs_p, 0.35):
                disp, pr, departure = "Left without being seen", None, tr + timedelta(minutes=float(wait * rng.uniform(0.6, 1.0)))
            else:
                disp = rng.choice(["Discharged", "Transferred"], p=[0.975, 0.025])
            ed_rows.append({"ed_visit_id": f"ED{vid:07d}", "patient_key": int(rng.choice(all_pk)), "arrival_ts": arrival,
                            "triage_ts": tr, "provider_ts": pr, "admit_decision_ts": None, "departure_ts": departure,
                            "esi_level": esi, "disposition": disp, "encounter_id": None})
    ed = pd.DataFrame(ed_rows)
    ed = ed[(ed.arrival_ts.dt.date >= START) & (ed.arrival_ts.dt.date <= END)]
    ed = ed.sort_values("arrival_ts").reset_index(drop=True)
    ed["ed_visit_id"] = [f"ED{i:07d}" for i in range(1, len(ed) + 1)]
    ed["arrival_date_key"] = ed.arrival_ts.dt.strftime("%Y%m%d").astype(int)
    for c in ("triage_ts", "provider_ts", "admit_decision_ts", "departure_ts"):
        ed[c] = pd.to_datetime(ed[c])
    link = ed.dropna(subset=["encounter_id"]).set_index("encounter_id").ed_visit_id

    enc = enc.sort_values("admit_ts").reset_index(drop=True)
    enc["ed_visit_id"] = enc.encounter_id.map(link)
    enc["encounter_id"] = [f"IP{i:06d}" for i in range(1, len(enc) + 1)]
    enc["admit_date_key"] = enc.admit_ts.dt.strftime("%Y%m%d").astype(int)
    enc["discharge_date_key"] = enc.discharge_ts.dt.strftime("%Y%m%d").astype("Int64")
    enc = enc[["encounter_id", "patient_key", "department_key", "payer_key", "admit_date_key", "discharge_date_key",
               "admit_ts", "discharge_ts", "admission_source", "discharge_disposition", "ed_visit_id"]]
    ed = ed[["ed_visit_id", "patient_key", "arrival_date_key", "arrival_ts", "triage_ts", "provider_ts",
             "admit_decision_ts", "departure_ts", "esi_level", "disposition"]]

    tables = {"dim_date": dim_date, "dim_department": dim_department, "dim_payer": dim_payer,
              "dim_patient": dim_patient, "fact_bed_capacity": fact_bed_capacity,
              "fact_inpatient_encounter": enc, "fact_ed_visit": ed}
    for name, df in tables.items():
        df.to_csv(out / f"{name}.csv", index=False, date_format="%Y-%m-%d %H:%M:%S")
    return tables


def _insort(queue, lo, item):
    j = lo
    while j < len(queue) and queue[j][0] <= item[0]:
        j += 1
    queue.insert(j, item)


def _admit_hour_profile():
    p = np.array([2, 2, 1, 1, 1, 1, 2, 4, 7, 8, 8, 8, 7, 7, 7, 7, 7, 6, 5, 5, 4, 3, 3, 2], float)
    return p / p.sum()


def _ed_hour_profile():
    p = np.array([3, 2, 2, 1.5, 1.5, 1.5, 2, 3, 5, 6.5, 7, 7, 7, 6.5, 6.5, 6.5, 6.5, 6.5, 6, 5.5, 5, 4.5, 4, 3.5], float)
    return p / p.sum()


def _disposition_p(dept, expired):
    snf = {"ORTH": 0.22, "MED": 0.16, "CARD": 0.12, "ICU": 0.14, "SURG": 0.08}.get(dept, 0.005)
    hh = 0.08
    ama, tr = 0.015, 0.015
    home = 1 - snf - hh - expired - ama - tr
    return [home, hh, snf, expired, ama, tr]


def _occupancy(enc, cap, dim_department):
    """Midnight census / staffed beds, by department and date (same definition as sql/views.sql)."""
    ends = enc.discharge_ts.fillna(pd.Timestamp(END + timedelta(days=1)))
    rows = []
    code = dict(zip(dim_department.department_key, dim_department.dept_code))
    for dk in code:
        m = enc.department_key == dk
        a, b = enc.admit_ts[m].to_numpy(), ends[m].to_numpy()
        for d in pd.date_range(START, END):
            midnight = np.datetime64(d + pd.Timedelta(days=1))   # census taken at 23:59 / midnight
            rows.append((d.date(), code[dk], int(((a < midnight) & (b >= midnight)).sum())))
    o = pd.DataFrame(rows, columns=["date", "dept_code", "census"])
    beds = cap.assign(date=pd.to_datetime(cap.date_key.astype(str)).dt.date,
                      dept_code=cap.department_key.map(code))[["date", "dept_code", "staffed_beds"]]
    o = o.merge(beds, on=["date", "dept_code"])
    o["occupancy"] = o.census / o.staffed_beds
    return o


if __name__ == "__main__":
    t = main()
    for k, v in t.items():
        print(f"{k:28s} {len(v):>7,} rows")
