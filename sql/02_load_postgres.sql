-- Load the CSVs into PostgreSQL. Run from the repo root with psql:
--   psql -d hospital_ops -f sql/01_schema.sql -f sql/02_load_postgres.sql -f sql/03_views.sql
-- (build.py does the same thing in DuckDB, so no database server is needed to try the project.)

\copy dim_date                 from 'data/csv/dim_date.csv'                 with (format csv, header true)
\copy dim_department           from 'data/csv/dim_department.csv'           with (format csv, header true)
\copy dim_payer                from 'data/csv/dim_payer.csv'                with (format csv, header true)
\copy dim_patient              from 'data/csv/dim_patient.csv'              with (format csv, header true)
\copy fact_bed_capacity        from 'data/csv/fact_bed_capacity.csv'        with (format csv, header true)
\copy fact_ed_visit            from 'data/csv/fact_ed_visit.csv'            with (format csv, header true)
\copy fact_inpatient_encounter from 'data/csv/fact_inpatient_encounter.csv' with (format csv, header true)

analyze;
