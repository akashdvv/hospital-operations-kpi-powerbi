-- Hospital operations star schema (PostgreSQL; also runs unchanged in DuckDB).
-- Grain of each fact table is stated above it.

drop view  if exists vw_monthly_hospital_kpi;
drop view  if exists vw_ed_visit;
drop view  if exists vw_inpatient_stay;
drop view  if exists vw_daily_census;
drop table if exists fact_inpatient_encounter;
drop table if exists fact_ed_visit;
drop table if exists fact_bed_capacity;
drop table if exists dim_patient;
drop table if exists dim_payer;
drop table if exists dim_department;
drop table if exists dim_date;

create table dim_date (
    date_key             integer primary key,      -- yyyymmdd
    calendar_date        date    not null unique,
    year                 integer not null,
    month_num            integer not null,
    month_name           varchar(3) not null,
    year_month           varchar(7) not null,
    fiscal_year          varchar(6) not null,      -- fiscal year starts 1 October
    fiscal_month         integer not null,
    week_start           date    not null,
    day_name             varchar(3) not null,
    is_weekend           boolean not null,
    in_reporting_period  boolean not null          -- FY2026 (Aug-Sep 2025 rows exist only for the opening census)
);

create table dim_department (
    department_key  integer primary key,
    dept_code       varchar(8)  not null unique,
    dept_name       varchar(40) not null,
    service_line    varchar(30) not null
);

create table dim_payer (
    payer_key    integer primary key,
    payer_code   varchar(8)  not null unique,
    payer_name   varchar(30) not null,
    payer_group  varchar(20) not null
);

create table dim_patient (
    patient_key  integer primary key,
    age_group    varchar(8) not null,
    sex          char(1)    not null
);

-- grain: one row per department per day
create table fact_bed_capacity (
    date_key        integer not null references dim_date (date_key),
    department_key  integer not null references dim_department (department_key),
    staffed_beds    integer not null check (staffed_beds > 0),
    primary key (date_key, department_key)
);

-- grain: one row per emergency department visit
create table fact_ed_visit (
    ed_visit_id        varchar(12) primary key,
    patient_key        integer not null references dim_patient (patient_key),
    arrival_date_key   integer not null references dim_date (date_key),
    arrival_ts         timestamp not null,
    triage_ts          timestamp,
    provider_ts        timestamp,               -- null when the patient left without being seen
    admit_decision_ts  timestamp,               -- only for admitted patients
    departure_ts       timestamp not null,
    esi_level          integer not null check (esi_level between 1 and 5),
    disposition        varchar(30) not null
);

-- grain: one row per inpatient stay
create table fact_inpatient_encounter (
    encounter_id           varchar(10) primary key,
    patient_key            integer not null references dim_patient (patient_key),
    department_key         integer not null references dim_department (department_key),
    payer_key              integer not null references dim_payer (payer_key),
    admit_date_key         integer not null references dim_date (date_key),
    discharge_date_key     integer references dim_date (date_key),   -- null = still in hospital
    admit_ts               timestamp not null,
    discharge_ts           timestamp,
    admission_source       varchar(20) not null,
    discharge_disposition  varchar(30),
    ed_visit_id            varchar(12) references fact_ed_visit (ed_visit_id),
    check (discharge_ts is null or discharge_ts > admit_ts)
);

create index ix_enc_patient on fact_inpatient_encounter (patient_key, admit_ts);
create index ix_enc_dept    on fact_inpatient_encounter (department_key, admit_ts);
create index ix_ed_arrival  on fact_ed_visit (arrival_date_key);
