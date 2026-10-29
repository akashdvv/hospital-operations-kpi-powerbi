# Building the Power BI report

This takes about 2–3 hours in **Power BI Desktop** (free, Windows). Everything the report needs is already in the repo: model-ready CSVs, every DAX measure, and the numbers each visual should show.

> Tip: save as `powerbi/hospital_operations.pbix` and commit it, then add screenshots of each page to `images/` and the README.

---

## 1. Load the data (15 min)

**Option A: CSV (no database needed).** *Home → Get data → Text/CSV*, then load these 7 files from `data/powerbi/`:

| File | Rename the table to |
|---|---|
| `date.csv` | `Date` |
| `department.csv` | `Department` |
| `payer.csv` | `Payer` |
| `patient.csv` | `Patient` |
| `daily_census.csv` | `Daily Census` |
| `inpatient_stays.csv` | `Inpatient Stays` |
| `ed_visits.csv` | `ED Visits` |

**Option B: PostgreSQL.** Run the three files in `sql/`, then *Get data → PostgreSQL database* and import `dim_date`, `dim_department`, `dim_payer`, `dim_patient`, `vw_daily_census`, `vw_inpatient_stay` and `vw_ed_visit`. Rename them as above.

## 2. Check types in Power Query (10 min)

Click **Transform data** and confirm or set:

| Column(s) | Type |
|---|---|
| every `*_key`, `midnight_census`, `staffed_beds`, `los_days`, `is_*`, `readmitted_30d`, `esi_level`, `arrival_hour` | Whole number |
| `calendar_date`, `week_start` | Date |
| `admit_ts`, `discharge_ts`, `arrival_ts` | Date/Time |
| `occupancy_rate`, `*_min` | Decimal number |
| `is_weekend`, `in_reporting_period` | True/False |

Blank `discharge_date_key`, `discharge_ts`, `door_to_provider_min` and `boarding_min` values are expected. They mean the patient is still admitted, left without being seen, or was not admitted. Do **not** replace them with 0, because that would distort the medians and ALOS. Then click **Close & Apply**.

## 3. Build the model (15 min)

In **Model view**, delete any relationships Power BI auto-detected, then create these:

| From (many) | To (one) | Active |
|---|---|---|
| `Daily Census[date_key]` | `Date[date_key]` | ✅ |
| `Daily Census[department_key]` | `Department[department_key]` | ✅ |
| `Inpatient Stays[discharge_date_key]` | `Date[date_key]` | ✅ |
| `Inpatient Stays[admit_date_key]` | `Date[date_key]` | ❌ **inactive** (the Admissions measure uses it) |
| `Inpatient Stays[department_key]` | `Department[department_key]` | ✅ |
| `Inpatient Stays[payer_key]` | `Payer[payer_key]` | ✅ |
| `Inpatient Stays[patient_key]` | `Patient[patient_key]` | ✅ |
| `ED Visits[arrival_date_key]` | `Date[date_key]` | ✅ |
| `ED Visits[patient_key]` | `Patient[patient_key]` | ✅ |

All are single-direction, one-to-many. Then:

- Select the `Date` table, choose *Table tools → Mark as date table*, and pick `calendar_date`.
- Set `Date[month_name]` to sort by `month_num` (*Column tools → Sort by column*).
- Hide the key columns and `occupancy_rate` from report view. The **Occupancy %** measure is the correct one, because averaging daily rates weights a 16-bed unit the same as a 64-bed unit.

The model is a **star schema**: three fact tables at different grains (department-day, stay, ED visit) sharing conformed Date, Department and Patient dimensions.

## 4. Add the measures (20 min)

*Home → Enter data*, name the table `_Measures`, and load it. Then add every measure in [`measures.dax`](measures.dax) with *New measure*. Set formats:

- Percentage, 1 decimal: Occupancy %, Readmission Rate 30d (and Rolling 3M), LWBS Rate, ED Admit Rate, ED Admission Share
- Decimal, 1 decimal: ALOS, all medians, Occupancy Change (pp)
- Whole number with thousands separator: counts

## 5. Build the pages (1.5–2 hours)

Add a **report-level filter** `Date[in_reporting_period] = True`. Aug–Sep 2025 only exists so that patients already in a bed on Oct 1 are counted in the census.

### Page 1: Executive overview
- **Cards:** Occupancy %, ALOS (days), Readmission Rate 30d, ED Visits, Median Door-to-Provider (min), LWBS Rate
- **Line chart:** X `Date[year_month]`, Y `Occupancy %`, Legend `Department[service_line]`. Add a constant line at 0.85 (*Analytics pane*).
- **Matrix:** rows `Department[dept_name]`; values Occupancy %, ALOS, Readmission Rate 30d, Discharges. Apply conditional formatting to Occupancy % (*Background color → Field value → Occupancy Color*).
- **Slicers:** `Date[fiscal_month]` or `year_month`, `Department[service_line]`

### Page 2: Capacity
- **Line chart:** X `Date[calendar_date]`, Y `Patient Days` and `Bed Days` (the daily census against staffed beds).
- **Matrix heatmap:** rows `Department[dept_name]`, columns `Date[week_start]`, value `Occupancy %` with a 3-colour background scale (green 70%, amber 85%, red 100%).
- **Cards:** Peak Daily Census, Days at or Above 95%, Occupancy Change (pp).
- **Title:** use the `Department Title` measure (*Format → Title → fx*).

### Page 3: Emergency Department
- **Line chart:** X `Date[week_start]`, Y `Median Boarding (hrs)`.
- **Line chart:** X `Date[week_start]`, Y `LWBS Rate`.
- **Scatter:** Details `Date[week_start]`, X `Medicine Occupancy %`, Y `Median Boarding (hrs)`. This is the page's key visual: boarding rises steeply once Medicine passes 85%.
- **Column chart:** X `ED Visits[arrival_hour]`, Y `ED Visits`, plus a second visual with `Median Door-to-Provider (min)`.
- **Bar chart:** `ED Visits[esi_level]` by `Median ED Length of Stay (hrs)`.

### Page 4: Readmissions
- **Bar chart:** `Department[dept_name]` by `Readmission Rate 30d`, sorted descending, with a constant line at the hospital rate.
- **Line chart:** X `Date[year_month]`, Y `Readmission Rate 30d (Rolling 3M)`.
- **Bar charts:** by `Payer[payer_name]`, `Patient[age_group]` and `Inpatient Stays[discharge_disposition]`.
- **Table:** Department, Index Discharges, Readmissions 30d, Readmission Rate 30d, Readmission Rate vs Hospital (pp).
- **Text box:** "September discharges are excluded from readmission rates until their 30-day follow-up window closes."

## 6. Validate against the reference numbers (10 min)

Open [`reports/reference_kpis.md`](../reports/reference_kpis.md). Every card, monthly value and department value must match **exactly**. If one doesn't, the usual causes are:

| Symptom | Likely cause |
|---|---|
| Admissions = Discharges, or Admissions too high | Admissions isn't using `USERELATIONSHIP`, or the report filter on `in_reporting_period` is missing |
| Occupancy slightly different | Used the average of `occupancy_rate` instead of the `Occupancy %` measure |
| ALOS too low | Blank `los_days` replaced with 0 in Power Query |
| Medians too low | Blank `boarding_min` or `door_to_provider_min` replaced with 0 |
| ED visits don't change with the Department slicer | Correct. ED visits aren't attributed to a department; use `Medicine Occupancy %` for that comparison |

## 7. Finish

- Apply a consistent theme (*View → Themes*) and give each page a title and the `Report Period Label` measure.
- *File → Export → PDF* for a static version.
- Take screenshots of each page into `images/` and add them to the README.
- Optional: *Publish* to Power BI Service and set a scheduled refresh against the PostgreSQL source.
