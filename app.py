# -------------------
# Imports
# -------------------

from __future__ import division

from flask import Flask, make_response, request, current_app, jsonify, render_template
from datetime import datetime, timedelta, date
from functools import update_wrapper
import json, os, requests, time
from flask.ext.heroku import Heroku
from flask.ext.sqlalchemy import SQLAlchemy
from sqlalchemy.ext.mutable import Mutable
from sqlalchemy import types, desc
from dictalchemy import make_class_dictable
from dateutil.tz import tzoffset
from mimetypes import guess_type
from copy import deepcopy
from os.path import join
from math import ceil

# -------------------
# Init
# -------------------

app = Flask(__name__)
heroku = Heroku(app)
db = SQLAlchemy(app)
make_class_dictable(db.Model)

# -------------------
# Settings
# -------------------

def add_cors_header(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Authorization, Content-Type'
    response.headers['Access-Control-Allow-Methods'] = 'POST, GET, PUT, PATCH, DELETE, OPTIONS'
    return response

app.after_request(add_cors_header)


# -------------------
# Types
# -------------------

class JsonType(Mutable, types.TypeDecorator):
    ''' JSON wrapper type for TEXT database storage.

        References:
        http://stackoverflow.com/questions/4038314/sqlalchemy-json-as-blob-text
        http://docs.sqlalchemy.org/en/rel_0_9/orm/extensions/mutable.html
    '''
    impl = types.Unicode

    def process_bind_param(self, value, engine):
        return unicode(json.dumps(value))

    def process_result_value(self, value, engine):
        if value:
            return json.loads(value)
        else:
            # default can also be a list
            return {}


# -------------------
# Models
# -------------------

class Organization(db.Model):
    '''
        Brigades and other civic tech organizations
    '''
    #Columns
    name = db.Column(db.Unicode(), primary_key=True)
    website = db.Column(db.Unicode())
    events_url = db.Column(db.Unicode())
    rss = db.Column(db.Unicode())
    projects_list_url = db.Column(db.Unicode())
    type = db.Column(db.Unicode())
    city = db.Column(db.Unicode())
    latitude = db.Column(db.Float())
    longitude = db.Column(db.Float())
    last_updated = db.Column(db.Integer())
    started_on = db.Column(db.Unicode())
    keep = db.Column(db.Boolean())

    # Relationships
    events = db.relationship('Event')
    projects = db.relationship('Project')

    def __init__(self, name, website=None, events_url=None,
                 rss=None, projects_list_url=None, type=None, city=None, latitude=None, longitude=None):
        self.name = name
        self.website = website
        self.events_url = events_url
        self.rss = rss
        self.projects_list_url = projects_list_url
        self.type = type
        self.city = city
        self.latitude = latitude
        self.longitude = longitude
        self.keep = True
        self.last_updated = time.time()
        self.started_on = str(date.today())

    def current_events(self):
        '''
            Return the two soonest upcoming events
        '''
        filter_old = Event.start_time_notz >= datetime.utcnow()
        current_events = Event.query.filter_by(organization_name=self.name)\
            .filter(filter_old).order_by(Event.start_time_notz.asc()).limit(2).all()
        current_events_json = [row.asdict() for row in current_events]
        return current_events_json

    def current_projects(self):
        '''
            Return the three most current projects
        '''
        all_projects = Project.query.filter_by(organization_name=self.name).all()
        all_projects_json = [project.asdict() for project in all_projects]

        # If its a non GitHub project, don't show it as a most recent project.
        # We don't have a good way to test dates of updates to non GitHub projects yet.
        github_projects_only = []
        for project in all_projects_json:
            if project['github_details']:
                github_projects_only.append(project)

        github_projects_only.sort(key=lambda k: k['github_details']['updated_at'], reverse=True)
        current_projects = github_projects_only[0:3]
        return current_projects

    def current_stories(self):
        '''
            Return the two most current stories
        '''
        current_stories = Story.query.filter_by(organization_name=self.name).limit(2).all()
        current_stories_json = [row.asdict() for row in current_stories]
        return current_stories_json

    def all_events(self):
        ''' API link to all an orgs events
        '''
        # Make a nice org name
        organization_name = safe_name(self.name)
        return '%s://%s/api/organizations/%s/events' % (request.scheme, request.host, organization_name)

    def upcoming_events(self):
        ''' API link to an orgs upcoming events
        '''
        # Make a nice org name
        organization_name = safe_name(self.name)
        return '%s://%s/api/organizations/%s/upcoming_events' % (request.scheme, request.host, organization_name)

    def past_events(self):
        ''' API link to an orgs past events
        '''
        # Make a nice org name
        organization_name = safe_name(self.name)
        return '%s://%s/api/organizations/%s/past_events' % (request.scheme, request.host, organization_name)

    def all_projects(self):
        ''' API link to all an orgs projects
        '''
        # Make a nice org name
        organization_name = safe_name(self.name)
        return '%s://%s/api/organizations/%s/projects' % (request.scheme, request.host, organization_name)

    def all_stories(self):
        ''' API link to all an orgs stories
        '''
        # Make a nice org name
        organization_name = safe_name(self.name)
        return '%s://%s/api/organizations/%s/stories' % (request.scheme, request.host, organization_name)

    def api_id(self):
        ''' Return organization name made safe for use in a URL.
        '''
        return safe_name(self.name)

    def api_url(self):
        ''' API link to itself
        '''
        return '%s://%s/api/organizations/%s' % (request.scheme, request.host, self.api_id())

    def asdict(self, include_extras=False):
        ''' Return Organization as a dictionary, with some properties tweaked.

            Optionally include linked projects, events, and stories.
        '''
        organization_dict = db.Model.asdict(self)

        del organization_dict['keep']

        for key in ('all_events', 'all_projects', 'all_stories',
                    'upcoming_events', 'past_events', 'api_url'):
            organization_dict[key] = getattr(self, key)()

        if include_extras:
            for key in ('current_events', 'current_projects', 'current_stories'):
                organization_dict[key] = getattr(self, key)()

        return organization_dict

class Story(db.Model):
    '''
        Blog posts from a Brigade.
    '''
    # Columns
    id = db.Column(db.Integer(), primary_key=True)
    title = db.Column(db.Unicode())
    link = db.Column(db.Unicode())
    type = db.Column(db.Unicode())
    keep = db.Column(db.Boolean())

    # Relationships
    organization = db.relationship('Organization')
    organization_name = db.Column(db.Unicode(), db.ForeignKey('organization.name'))

    def __init__(self, title=None, link=None, type=None, organization_name=None):
        self.title = title
        self.link = link
        self.type = type
        self.organization_name = organization_name
        self.keep = True

    def api_url(self):
        ''' API link to itself
        '''
        return '%s://%s/api/stories/%s' % (request.scheme, request.host, str(self.id))

    def asdict(self, include_organization=False):
        ''' Return Story as a dictionary, with some properties tweaked.

            Optionally include linked organization.
        '''
        story_dict = db.Model.asdict(self)

        del story_dict['keep']
        story_dict['api_url'] = self.api_url()

        if include_organization:
            story_dict['organization'] = self.organization.asdict()

        return story_dict

class Project(db.Model):
    '''
        Civic tech projects on GitHub
    '''
    # Columns
    id = db.Column(db.Integer(), primary_key=True)
    name = db.Column(db.Unicode())
    code_url = db.Column(db.Unicode())
    link_url = db.Column(db.Unicode())
    description = db.Column(db.Unicode())
    type = db.Column(db.Unicode())
    categories = db.Column(db.Unicode())
    github_details = db.Column(JsonType())
    keep = db.Column(db.Boolean())

    # Relationships
    organization = db.relationship('Organization')
    organization_name = db.Column(db.Unicode(), db.ForeignKey('organization.name'))

    def __init__(self, name, code_url=None, link_url=None,
                 description=None, type=None, categories=None,
                 github_details=None, organization_name=None, keep=None):
        self.name = name
        self.code_url = code_url
        self.link_url = link_url
        self.description = description
        self.type = type
        self.categories = categories
        self.github_details = github_details
        self.organization_name = organization_name
        self.keep = True

    def api_url(self):
        ''' API link to itself
        '''
        return '%s://%s/api/projects/%s' % (request.scheme, request.host, str(self.id))

    def asdict(self, include_organization=False):
        ''' Return Project as a dictionary, with some properties tweaked.

            Optionally include linked organization.
        '''
        project_dict = db.Model.asdict(self)

        del project_dict['keep']
        project_dict['api_url'] = self.api_url()

        if include_organization:
            project_dict['organization'] = self.organization.asdict()

        return project_dict

class Event(db.Model):
    '''
        Organizations events from Meetup
    '''
    # Columns
    id  = db.Column(db.Integer(), primary_key=True)
    name = db.Column(db.Unicode())
    description = db.Column(db.Unicode())
    event_url = db.Column(db.Unicode())
    location = db.Column(db.Unicode())
    created_at = db.Column(db.Unicode())
    start_time_notz = db.Column(db.DateTime(False))
    end_time_notz = db.Column(db.DateTime(False))
    utc_offset = db.Column(db.Integer())
    keep = db.Column(db.Boolean())

    # Relationships
    organization = db.relationship('Organization')
    organization_name = db.Column(db.Unicode(), db.ForeignKey('organization.name'))

    def __init__(self, name, event_url, start_time_notz, created_at, utc_offset,
                 organization_name, location=None, end_time_notz=None, description=None):
        self.name = name
        self.description = description
        self.location = location
        self.event_url = event_url
        self.start_time_notz = start_time_notz
        self.utc_offset = utc_offset
        self.end_time_notz = end_time_notz
        self.organization_name = organization_name
        self.created_at = created_at
        self.keep = True

    def start_time(self):
        ''' Get a string representation of the start time with UTC offset.
        '''
        if self.start_time_notz is None:
            return None
        tz = tzoffset(None, self.utc_offset)
        st = self.start_time_notz
        dt = datetime(st.year, st.month, st.day, st.hour, st.minute, st.second, tzinfo=tz)
        return dt.strftime('%Y-%m-%d %H:%M:%S %z')

    def end_time(self):
        ''' Get a string representation of the end time with UTC offset.
        '''
        if self.end_time_notz is None:
            return None
        tz = tzoffset(None, self.utc_offset)
        et = self.end_time_notz
        dt = datetime(et.year, et.month, et.day, et.hour, et.minute, et.second, tzinfo=tz)
        return dt.strftime('%Y-%m-%d %H:%M:%S %z')

    def api_url(self):
        ''' API link to itself
        '''
        return '%s://%s/api/events/%s' % (request.scheme, request.host, str(self.id))

    def asdict(self, include_organization=False):
        ''' Return Event as a dictionary, with some properties tweaked.

            Optionally include linked organization.
        '''
        event_dict = db.Model.asdict(self)

        for key in ('keep', 'start_time_notz', 'end_time_notz', 'utc_offset'):
            del event_dict[key]

        for key in ('start_time', 'end_time', 'api_url'):
            event_dict[key] = getattr(self, key)()

        if include_organization:
            event_dict['organization'] = self.organization.asdict()

        return event_dict

class Error(db.Model):
    '''
        Errors from run_update.py
    '''
    # Columns
    id  = db.Column(db.Integer(), primary_key=True)
    error = db.Column(db.Unicode())
    time = db.Column(db.DateTime(False))

# -------------------
# API
# -------------------

def page_info(query, page, limit):
    ''' Return last page and offset for a query.
    '''
    # Get a bunch of projects.
    total = query.count()
    last = int(ceil(total / limit))
    offset = (page - 1) * limit

    return last, offset

def pages_dict(page, last):
    ''' Return a dictionary of pages to return in API responses.
    '''
    url = '%s://%s%s' % (request.scheme, request.host, request.path)

    pages = dict()

    if page > 1:
        pages['first'] = url

    if page == 2:
        pages['prev'] = url
    elif page > 2:
        pages['prev'] = '%s?page=%d' % (url, page - 1)

    if page < last:
        pages['next'] = '%s?page=%d' % (url, page + 1)
        pages['last'] = '%s?page=%d' % (url, last)

    return pages

def paged_results(query, page, per_page):
    '''
    '''
    total = query.count()
    last, offset = page_info(query, page, per_page)
    model_dicts = [o.asdict(True) for o in query.limit(per_page).offset(offset)]

    return dict(total=total, pages=pages_dict(page, last), objects=model_dicts)

def is_safe_name(name):
    ''' Return True if the string is a safe name.
    '''
    return raw_name(safe_name(name)) == name

def safe_name(name):
    ''' Return URL-safe organization name with spaces replaced by dashes.

        Slashes will be removed, which is incompatible with raw_name().
    '''
    return name.replace(' ', '-').replace('/', '-').replace('?','-').replace('#','-')

def raw_name(name):
    ''' Return raw organization name with dashes replaced by spaces.

        Also replace old-style underscores with spaces.
    '''
    return name.replace('_', ' ').replace('-', ' ')

@app.route('/api/organizations')
@app.route('/api/organizations/<name>')
def get_organizations(name=None):
    ''' Regular response option for organizations.
    '''
    if name:
        # Get one named organization.
        filter = Organization.name == raw_name(name)
        org = db.session.query(Organization).filter(filter).first()
        return jsonify(org.asdict(True))

    # Get a bunch of organizations.
    query = db.session.query(Organization)
    response = paged_results(query, int(request.args.get('page', 1)), 10)
    return jsonify(response)

@app.route('/api/organizations.geojson')
def get_organizations_geojson():
    ''' GeoJSON response option for organizations.
    '''
    geojson = dict(type='FeatureCollection', features=[])

    for org in db.session.query(Organization):
        # The unique identifier of an organization.
        id = org.api_id()

        # Pick out all the properties that aren't part of the location.
        props = org.asdict()

        # GeoJSON Point geometry, http://geojson.org/geojson-spec.html#point
        geom = dict(type='Point', coordinates=[org.longitude, org.latitude])

        feature = dict(type='Feature', id=id, properties=props, geometry=geom)
        geojson['features'].append(feature)

    return jsonify(geojson)

@app.route("/api/organizations/<organization_name>/events")
def get_orgs_events(organization_name):
    '''
        A cleaner url for getting an organizations events
        Better than /api/events?q={"filters":[{"name":"organization_name","op":"eq","val":"Code for San Francisco"}]}
    '''
    # Check org name
    organization = Organization.query.filter_by(name=raw_name(organization_name)).first()
    if not organization:
        return "Organization not found", 404

    # Get event objects
    query = Event.query.filter_by(organization_name=organization.name)
    response = paged_results(query, int(request.args.get('page', 1)), 25)
    return jsonify(response)

@app.route("/api/organizations/<organization_name>/upcoming_events")
def get_upcoming_events(organization_name):
    '''
        Get events that occur in the future. Order asc.
    '''
    # Check org name
    organization = Organization.query.filter_by(name=raw_name(organization_name)).first()
    if not organization:
        return "Organization not found", 404
    # Get upcoming event objects
    query = Event.query.filter(Event.organization_name == organization.name, Event.start_time_notz >= datetime.utcnow())
    response = paged_results(query, int(request.args.get('page', 1)), 25)
    return jsonify(response)

@app.route("/api/organizations/<organization_name>/past_events")
def get_past_events(organization_name):
    '''
        Get events that occur in the past. Order desc.
    '''
    # Check org name
    organization = Organization.query.filter_by(name=raw_name(organization_name)).first()
    if not organization:
        return "Organization not found", 404
    # Get past event objects
    query = Event.query.filter(Event.organization_name == organization.name, Event.start_time_notz < datetime.utcnow()).\
            order_by(desc(Event.start_time_notz))
    response = paged_results(query, int(request.args.get('page', 1)), 25)
    return jsonify(response)

@app.route("/api/organizations/<organization_name>/stories")
def get_orgs_stories(organization_name):
    '''
        A cleaner url for getting an organizations stories
    '''
    # Check org name
    organization = Organization.query.filter_by(name=raw_name(organization_name)).first()
    if not organization:
        return "Organization not found", 404

    # Get story objects
    query = Story.query.filter_by(organization_name=organization.name)
    response = paged_results(query, int(request.args.get('page', 1)), 25)
    return jsonify(response)

@app.route("/api/organizations/<organization_name>/projects")
def get_orgs_projects(organization_name):
    '''
        A cleaner url for getting an organizations projects
    '''
    # Check org name
    organization = Organization.query.filter_by(name=raw_name(organization_name)).first()
    if not organization:
        return "Organization not found", 404

    # Get project objects
    query = Project.query.filter_by(organization_name=organization.name)
    response = paged_results(query, int(request.args.get('page', 1)), 10)
    return jsonify(response)

@app.route('/api/projects')
@app.route('/api/projects/<int:id>')
def get_projects(id=None):
    ''' Regular response option for projects.
    '''
    if id:
        # Get one named project.
        filter = Project.id == id
        proj = db.session.query(Project).filter(filter).first()
        return jsonify(proj.asdict(True))

    # Get a bunch of projects.
    query = db.session.query(Project)
    response = paged_results(query, int(request.args.get('page', 1)), 10)
    return jsonify(response)

@app.route('/api/events')
@app.route('/api/events/<int:id>')
def get_events(id=None):
    ''' Regular response option for events.
    '''
    if id:
        # Get one named event.
        filter = Event.id == id
        event = db.session.query(Event).filter(filter).first()
        return jsonify(event.asdict(True))

    # Get a bunch of events.
    query = db.session.query(Event)
    response = paged_results(query, int(request.args.get('page', 1)), 25)
    return jsonify(response)

@app.route('/api/stories')
@app.route('/api/stories/<int:id>')
def get_stories(id=None):
    ''' Regular response option for stories.
    '''
    if id:
        # Get one named story.
        filter = Story.id == id
        story = db.session.query(Story).filter(filter).first()
        return jsonify(story.asdict(True))

    # Get a bunch of stories.
    query = db.session.query(Story)
    response = paged_results(query, int(request.args.get('page', 1)), 25)
    return jsonify(response)

# -------------------
# Routes
# -------------------

@app.route('/api/.well-known/status')
def well_known_status():
    ''' Return status information for Engine Light.

        http://engine-light.codeforamerica.org
    '''
    if 'GITHUB_TOKEN' in os.environ:
        github_auth = (os.environ['GITHUB_TOKEN'], '')
    else:
        github_auth = None

    meetup_key = os.environ['MEETUP_KEY']

    try:
        org = db.session.query(Organization).order_by(Organization.last_updated).limit(1).first()
        project = db.session.query(Project).limit(1).first()
        rate_limit = requests.get('https://api.github.com/rate_limit', auth=github_auth)
        remaining_github = rate_limit.json()['resources']['core']['remaining']
        recent_error = db.session.query(Error).order_by(desc(Error.time)).limit(1).first()

        meetup_url = 'https://api.meetup.com/status?format=json&key='+meetup_key
        meetup_status = requests.get(meetup_url).json().get('status')

        time_since_updated = time.time() - getattr(org, 'last_updated', -1)

        if not hasattr(project, 'name'):
            status = 'Sample project is missing a name'

        elif not hasattr(org, 'name'):
            status = 'Sample project is missing a name'

        elif recent_error:
            if recent_error.time.date() == date.today():
                status = recent_error.error
            else:
                status = 'ok' # is this really okay?

        elif time_since_updated > 6 * 60 * 60:
            status = 'Oldest organization (%s) updated more than 6 hours ago' % org.name

        elif remaining_github < 1000:
            status = 'Only %d remaining Github requests' % remaining_github

        elif meetup_status != 'ok':
            status = 'Meetup status is "%s"' % meetup_status

        else:
            status = 'ok'

    except Exception, e:
        status = 'Error: ' + str(e)

    state = dict(status=status, updated=int(time.time()), resources=[])
    state.update(dict(dependencies=['Meetup', 'Github', 'PostgreSQL']))

    return jsonify(state)

@app.route("/")
def index():
    response = make_response('Look in /api', 302)
    response.headers['Location'] = '/api'
    return response

@app.route("/api")
@app.route("/api/")
def api_index():
    return render_template('index.html', api_base='%s://%s' % (request.scheme, request.host))

@app.route("/api/static/<path:path>")
def api_static_file(path):
    local_path = join('static', path)
    mime_type, _ = guess_type(path)
    response = make_response(open(local_path).read())
    response.headers['Content-Type'] = mime_type
    return response

if __name__ == "__main__":
    app.run(debug=True)
