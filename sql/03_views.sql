-- Reporting views. Power BI imports these (or the CSV exports of them in data/powerbi/).
-- Every KPI definition lives here once, so Power BI, Excel and SQL users all get the same numbers.

-- 1. Midnight census and occupancy, one row per department per day in the reporting period.
--    A patient counts toward a day's census if they are in a bed at midnight at the end of that day.
create or replace view vw_daily_census as
select
    d.date_key,
    d.calendar_date,
    c.department_key,
    c.staffed_beds,
    count(e.encounter_id)                               as midnight_census,
    cast(count(e.encounter_id) as numeric(10, 4)) / c.staffed_beds as occupancy_rate
from dim_date d
join fact_bed_capacity c
  on c.date_key = d.date_key
left join fact_inpatient_encounter e
  on  e.department_key = c.department_key
  and e.admit_ts < d.calendar_date + interval '1 day'
  and (e.discharge_ts is null or e.discharge_ts >= d.calendar_date + interval '1 day')
where d.in_reporting_period
group by d.date_key, d.calendar_date, c.department_key, c.staffed_beds;


-- 2. One row per inpatient stay with length of stay and the 30-day readmission flags.
--    Index discharge (CMS-style): discharged alive, not transferred out, not against medical advice,
--    and at least 30 days of follow-up left in the data (otherwise the readmission rate is understated).
create or replace view vw_inpatient_stay as
with ordered as (
    select
        e.*,
        lead(e.admit_ts)     over (partition by e.patient_key order by e.admit_ts) as next_admit_ts,
        lead(e.encounter_id) over (partition by e.patient_key order by e.admit_ts) as next_encounter_id
    from fact_inpatient_encounter e
),
data_end as (
    select max(calendar_date) as last_day from dim_date
)
select
    o.encounter_id,
    o.patient_key,
    o.department_key,
    o.payer_key,
    o.admit_date_key,
    o.discharge_date_key,
    o.admit_ts,
    o.discharge_ts,
    o.admission_source,
    o.discharge_disposition,
    o.ed_visit_id,
    case when o.discharge_ts is not null
         then greatest(1, cast(o.discharge_ts as date) - cast(o.admit_ts as date)) end    as los_days,
    case when o.discharge_ts is not null
          and o.discharge_disposition not in ('Expired', 'Transferred', 'Left AMA')
          and cast(o.discharge_ts as date) <= x.last_day - 30
         then 1 else 0 end                                                                as is_index_discharge,
    case when o.discharge_ts is not null
          and o.discharge_disposition not in ('Expired', 'Transferred', 'Left AMA')
          and cast(o.discharge_ts as date) <= x.last_day - 30
          and o.next_admit_ts <= o.discharge_ts + interval '30 days'
         then 1 else 0 end                                                                as readmitted_30d,
    case when o.next_admit_ts <= o.discharge_ts + interval '30 days'
         then o.next_encounter_id end                                                     as readmission_encounter_id
from ordered o
cross join data_end x;


-- 3. One row per ED visit with the throughput intervals in minutes.
create or replace view vw_ed_visit as
select
    v.ed_visit_id,
    v.patient_key,
    v.arrival_date_key,
    v.arrival_ts,
    extract(hour from v.arrival_ts)                                         as arrival_hour,
    v.esi_level,
    v.disposition,
    extract(epoch from (v.provider_ts  - v.arrival_ts)) / 60.0              as door_to_provider_min,
    extract(epoch from (v.departure_ts - v.arrival_ts)) / 60.0              as ed_los_min,
    extract(epoch from (v.departure_ts - v.admit_decision_ts)) / 60.0       as boarding_min,
    case when v.disposition = 'Left without being seen' then 1 else 0 end  as is_lwbs,
    case when v.disposition = 'Admitted' then 1 else 0 end                 as is_admitted
from fact_ed_visit v;


-- 4. Hospital-wide monthly scorecard: the reference numbers the Power BI report must reproduce.
create or replace view vw_monthly_hospital_kpi as
with census as (
    select d.year_month,
           sum(c.midnight_census) as patient_days,
           sum(c.staffed_beds)    as bed_days
    from vw_daily_census c join dim_date d on d.date_key = c.date_key
    group by d.year_month
),
adm as (
    select d.year_month, count(*) as admissions
    from vw_inpatient_stay s join dim_date d on d.date_key = s.admit_date_key
    where d.in_reporting_period
    group by d.year_month
),
dis as (
    select d.year_month,
           count(*)                    as discharges,
           sum(s.los_days)             as los_days,
           sum(s.is_index_discharge)   as index_discharges,
           sum(s.readmitted_30d)       as readmissions_30d
    from vw_inpatient_stay s join dim_date d on d.date_key = s.discharge_date_key
    group by d.year_month
),
ed as (
    select d.year_month,
           count(*)                                                               as ed_visits,
           percentile_cont(0.5) within group (order by v.door_to_provider_min)    as median_door_to_provider_min,
           percentile_cont(0.5) within group (order by v.ed_los_min)              as median_ed_los_min,
           percentile_cont(0.5) within group (order by v.boarding_min)            as median_boarding_min,
           avg(cast(v.is_lwbs as numeric))                                        as lwbs_rate
    from vw_ed_visit v join dim_date d on d.date_key = v.arrival_date_key
    group by d.year_month
)
select
    c.year_month,
    a.admissions,
    s.discharges,
    c.patient_days,
    c.bed_days,
    round(cast(c.patient_days as numeric) / c.bed_days, 4)                         as occupancy_rate,
    round(cast(s.los_days as numeric) / s.discharges, 2)                           as alos_days,
    s.index_discharges,
    s.readmissions_30d,
    case when s.index_discharges > 0
         then round(cast(s.readmissions_30d as numeric) / s.index_discharges, 4) end as readmission_rate_30d,
    e.ed_visits,
    round(cast(e.median_door_to_provider_min as numeric), 1)                       as median_door_to_provider_min,
    round(cast(e.median_ed_los_min as numeric), 1)                                 as median_ed_los_min,
    round(cast(e.median_boarding_min as numeric), 1)                               as median_boarding_min,
    round(e.lwbs_rate, 4)                                                          as lwbs_rate
from census c
left join adm a on a.year_month = c.year_month
left join dis s on s.year_month = c.year_month
left join ed  e on e.year_month = c.year_month
order by c.year_month;
