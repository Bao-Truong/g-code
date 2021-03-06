# Copyright 2018 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


from datetime import timedelta, datetime

from airflow import DAG
from airflow import models
from airflow.contrib.operators.dataflow_operator import DataflowTemplateOperator
from airflow.operators.dummy_operator import DummyOperator
from airflow.models import Variable
from airflow.contrib.operators import bigquery_get_data
from airflow.contrib.operators import bigquery_operator
from airflow.contrib.operators import bigquery_to_gcs
from airflow.operators import bash_operator
from airflow.operators import email_operator
from airflow.utils import trigger_rule

## Requires these variables to be setup on composer (airflow)

PROJECT = models.Variable.get('gcp_project')
TEMP_BUCKET = models.Variable.get('gcs_bucket')
ZONE = models.Variable.get("gce_zone")
REGION = models.Variable.get("gce_region")
EMAIL = models.Variable.get("email")
BQ_DATASET = models.Variable.get("dataset_id")

## Dataflow template to be used
## Update it according to your deployment region. Example: gs://dataflow-templates-us-central1/latest/Jdbc_to_BigQuery

TEMPLATE = 'gs://dataflow-templates-us-<CHANGEME>"/latest/Jdbc_to_BigQuery'

### <CHANGEME> needs to be replaced by your own value

ENVIRONMENT = {
    "bypassTempDirValidation": "false",
    "maxWorkers": "20",
    "numWorkers": "1",
    "serviceAccountEmail": "<CHANGEME>",
    "tempLocation": "gs://<CHANGEME>",
    "ipConfiguration": "WORKER_IP_UNSPECIFIED",
    "additionalExperiments": [
    "sideinput_io_metrics"
    ]
}

PARAMETERS = {
    "connectionURL": "jdbc:mysql://<CHANGEME>:<CHANGEME>/testdb",
    "driverClassName": "com.mysql.jdbc.Driver",
    "query": "select * from testdb.table1",
    "outputTable": "google.com:caugusto-actifio:Google_CarlosAugusto.table1_staging",
    "driverJars": "gs://<CHANGEME>/<CHANGEME>.jar",
    "bigQueryLoadingTemporaryDirectory": "gs://<CHANGEME>",
    "username": "<CHANGEME>",
    "password": "<CHANGEME>"
 }

TODAY = datetime.today()
TODAY_STRING = datetime.today().strftime('%Y%m%d')

DEFAULT_DAG_ARGS = {
    'owner': '<CHANGEME>',
    'depends_on_past': False,
    'start_date': TODAY,
    'email': EMAIL,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=1),
    'schedule_interval': '@daily',
    'dataflow_default_options': {
               'project': PROJECT,
               'region': REGION,
               'zone': ZONE,
               'stagingLocation': TEMP_BUCKET
           }
}

dag = DAG(
    'CDC-DAG-v1',
    default_args=DEFAULT_DAG_ARGS,
    dagrun_timeout=timedelta(hours=3),
    schedule_interval='00 * * * *'
)

start = DummyOperator(task_id='Start', dag=dag)
end = DummyOperator(task_id='End', dag=dag)

dataflow_load_table1 = DataflowTemplateOperator(
    task_id='loadbq_table1_dataflow',
   template=TEMPLATE,
    parameters=PARAMETERS,
    environment=ENVIRONMENT,
    dag=dag)


# Perform most popular question query.
bq_merge_table1 = bigquery_operator.BigQueryOperator(
        task_id='bq_merge_table1',
        bql="""
        MERGE
          `{table}` T
        USING
          (
          SELECT
            CASE
              WHEN a.id IS NULL AND b.id IS NOT NULL THEN "I"
              WHEN a.id IS NOT NULL
            AND b.id IS NULL THEN "D"
            ELSE
            "U"
          END
            AS op,
          IF
            (b.id IS NULL,
              a,
              b).*
          FROM
            `Google_CarlosAugusto.table1` a
          FULL OUTER JOIN
            `Google_CarlosAugusto.table1_staging` b
          ON
            a.id = b.id
          WHERE
            a.id IS NULL
            OR b.id IS NULL
            OR FARM_FINGERPRINT(FORMAT("%T", a)) <> FARM_FINGERPRINT(FORMAT("%T", b)) ) S
        ON
          T.id = S.id
          WHEN MATCHED AND S.op = 'U' THEN UPDATE SET col1 = S.col1, col2 = S.col2, col3 = S.col3, col4 = S.col4
          WHEN MATCHED
          AND S.op = 'D' THEN
        DELETE
          WHEN NOT MATCHED THEN INSERT (id, col1, col2, col3, col4) VALUES (S.id, S.col1, S.col2, S.col3, S.col4)
        """.format(table="Google_CarlosAugusto.table1"),
        use_legacy_sql=False,
        dag=dag)

# Perform most popular question query.
bq_log_table1_cdc = bigquery_operator.BigQueryOperator(
        task_id='bq_log_table1_cdc',
        bql="""
        INSERT INTO `{table}` 
        SELECT
          CASE
            WHEN a.id IS NULL AND b.id IS NOT NULL THEN "I"
            WHEN a.id IS NOT NULL
          AND b.id IS NULL THEN "D"
          ELSE
          "U"
        END
          AS op,
        IF
          (b.id IS NULL,
            a,
            b).*,
        CURRENT_DATETIME() as changetime
        FROM
          `Google_CarlosAugusto.table1` a
        FULL OUTER JOIN
          `Google_CarlosAugusto.table1_staging` b
        ON
          a.id = b.id
        WHERE
          a.id IS NULL
          OR b.id IS NULL
          OR FARM_FINGERPRINT(FORMAT("%T", a)) <> FARM_FINGERPRINT(FORMAT("%T", b))
        """.format(table="Google_CarlosAugusto.table1_log"),
        use_legacy_sql=False,
        dag=dag)


bq_trunc_table1_staging = bigquery_operator.BigQueryOperator(
        task_id='bq_truncate_table1_staging',
        bql="""
        TRUNCATE TABLE 
          `{table}`
        """.format(table="Google_CarlosAugusto.table1_staging"),
        use_legacy_sql=False,
        dag=dag)


# Send email confirmation
# Needs SendGrid to be configured here
email_summary = email_operator.EmailOperator(
    task_id='email_summary',
    to=models.Variable.get('email'),
    subject='Table1 Load Job',
    html_content=""" <h3>Table1 Load Job Finished Successfully</h3> """,
    dag=dag)
# [END composer_email]


#start >> t1 >> end

start >> bq_trunc_table1_staging >> dataflow_load_table1 >> bq_log_table1_cdc >> bq_merge_table1  >> email_summary >> end

