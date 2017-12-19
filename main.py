from flask import Flask, render_template, request
from oauth2client.client import GoogleCredentials
from googleapiclient import discovery
from sqlalchemy import create_engine, Column
from sqlalchemy.types import String, TEXT
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from pytz import utc
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.pool import ThreadPoolExecutor, ProcessPoolExecutor
from datetime import datetime
import socket, os, json

DEFAULT_PJ = 'xxx'
DEFAULT_ZONE = 'asia-northeast1-a'
SQLITE_DB = 'instance-cache.db'

app = Flask(__name__)
credentials = GoogleCredentials.get_application_default()
compute = discovery.build('compute', 'v1', credentials=credentials)
Base = declarative_base()
engine = create_engine('sqlite:///%s'% SQLITE_DB, echo=True)
Session = sessionmaker(bind=engine)
session = Session()

jobstores = {
    'default': SQLAlchemyJobStore(url='sqlite:///jobs.sqlite')
}
executors = {
    'default': ThreadPoolExecutor(20),
    'processpool': ProcessPoolExecutor(5)
}
job_defaults = {
    'coalesce': False,
    'max_instances': 3
}
scheduler = BackgroundScheduler(jobstores=jobstores, executors=executors, job_defaults=job_defaults, timezone=utc)

class GCPInstance(Base):
    __tablename__ = 'gcp_instances'
    name = Column(String, primary_key=True)
    status = Column(String)
    machine_type = Column(String)
    cpu_platform = Column(String)
    instance_metadata = Column(TEXT)


def info_logging(msg):
    now = datetime.now()
    print '[%s] gke-sample-app INFO: %s' % (now, msg)


def log_access_info(msg=''):
    uri = request.script_root + request.path
    if request.headers.getlist("X-Forwarded-For"):
        ip = request.headers.getlist("X-Forwarded-For")[0]
        if ',' in ip:
            ip = ip.split(',')[0]
    else:
        ip = request.remote_addr

    info_logging('%s %s %s HTTP/1.1 - %s' % (request.method, ip, uri, msg))
    #app.logger.info('%s - %s: %s' % (ip, uri, msg))


def get_instances():
    Base.metadata.create_all(engine)
    pj = os.getenv("GCP_PJ", DEFAULT_PJ)
    zone = os.getenv("GCP_ZONE", DEFAULT_ZONE)
    result = compute.instances().list(project=pj, zone=zone).execute()
    return result


@app.route("/")
def index():
    log_access_info('200')

    hostname = socket.gethostname()
    return render_template('index.html', hostname=hostname)


@app.route("/instance/<instance_name>")
def get_instance(instance_name):
    log_access_info('200')
    gcp_instance = session.query(GCPInstance).filter_by(name=instance_name).first()
    return render_template(
        'instance.html',
        instance=gcp_instance,
    )


@app.route("/instance")
def get_all_instance():
    log_access_info('200')
    instances = []
    num_gcp_instances = session.query(GCPInstance).count()
    if num_gcp_instances == 0:
        get_instances()

    query = session.query(GCPInstance)

    for instance in query:
        instances.append(instance)
    return render_template(
            'all_instances.html',
            instances=instances,
            )


@app.route("/admin/make_cache")
def make_cache():
    result = get_instances()
    for instance_item in result['items']:
        gcp_instance = session.query(GCPInstance).filter_by(name=instance_item['name']).first()
        if gcp_instance:
            gcp_instance.name = instance_item['name']
            gcp_instance.machine_type = instance_item['machineType']
            gcp_instance.cpu_platform = instance_item['cpuPlatform']
            gcp_instance.status = instance_item['status']
            if 'items' in instance_item['metadata']:
                gcp_instance.instance_metadata = json.dumps(instance_item['metadata']['items'])
            else:
                gcp_instance.instance_metadata = None
            session.add(gcp_instance)
            session.commit()
        else:
            new_gcp_instance = GCPInstance(
                    name=instance_item['name'],
                    machine_type=instance_item['machineType'],
                    cpu_platform=instance_item['cpuPlatform'],
                    status=instance_item['status'],
                        )
            if 'items' in instance_item['metadata']:
                new_gcp_instance.instance_metadata=json.dumps(instance_item['metadata']['items'])
            session.add(new_gcp_instance)
            session.commit()

    log_access_info("make cache successed!!")
    return 'make_cache successed!!'

@app.errorhandler(404)
def page_not_found(e):
    log_access_info('404')
    return '404 Not Found!!', 404

@app.errorhandler(500)
def page_not_found(e):
    log_access_info('500')
    return '500 Internal Server Error', 500

job = scheduler.add_job(make_cache, 'interval', minutes=5)
scheduler.start()

