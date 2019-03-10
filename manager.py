import json, secrets
from flask import json, Flask, request, g
from flask_httpauth import HTTPBasicAuth, HTTPTokenAuth, MultiAuth
from functions.db.mongo import *
from functions.hashing.hashing import *
from bson.json_util import dumps
from cachetools import cached, TTLCache
from retrying import retry


API_VERSION = "v2"


# get setting from envvar with failover from config/conf.json file if envvar not set
# using skip rather then None so passing a None type will still pass a None value rather then assuming there should be
# default value thus allowing to have No value set where needed (like in the case of registry user\pass)
def get_conf_setting(setting, settings_json, default_value="skip"):
    try:
        setting_value = os.getenv(setting.upper(), settings_json.get(setting, default_value))
        if setting_value == "true":
            return True
        elif setting_value == "false":
            return False
    except Exception as e:
        print("missing " + setting + " config setting", file=sys.stderr)
        print("missing " + setting + " config setting")
        os._exit(2)
    if setting_value == "skip":
        print("missing " + setting + " config setting", file=sys.stderr)
        print("missing " + setting + " config setting")
        os._exit(2)
    return setting_value


# takes an invalid request & figure out what params are missing on a request and returns a list of those, this function
# should only be called in cases where the "invalid_request" has been tried and found to be missing a param as it fails
# hard on failure like the rest of the code (which in this case also means no missing params)
def find_missing_params(invalid_request):
    try:
        required_params = ["docker_image"]
        missing_params = dict()
        missing_params["missing_parameters"] = list(set(required_params) - set(invalid_request))

    except Exception as e:
        print("unable to find missing params yet the request is returning an error", file=sys.stderr)
        os._exit(2)
    return missing_params


# take a name of a parameter, the dict that parameter may or may not be and a sane default and returns the param from
# the dict if it exists in it or the sane default otherwise
def return_sane_default_if_not_declared(needed_parameter, parameters_dict, sane_default):
    try:
        if needed_parameter in parameters_dict:
            returned_value = parameters_dict[needed_parameter]
        else:
            returned_value = sane_default
    except Exception as e:
        print("problem with parameter phrasing", file=sys.stderr)
        os._exit(2)
    return returned_value


# check for edge case of port being outside of the valid port range
def check_ports_valid_range(checked_ports):
    for checked_port in checked_ports:
        if isinstance(checked_port, int):
            if not 1 <= checked_port <= 65535:
                return "{\"starting_ports\": \"invalid port\"}", 400
        elif isinstance(checked_port, dict):
            for host_port, container_port in checked_port.items():
                try:
                    if not 1 <= int(host_port) <= 65535 or not 1 <= int(container_port) <= 65535:
                        return "{\"starting_ports\": \"invalid port\"}", 400
                except ValueError:
                    return "{\"starting_ports\": \"can only be a list containing integers or dicts\"}", 403
        else:
            return "{\"starting_ports\": \"can only be a list containing integers or dicts\"}", 403
    return "all ports checked are in a valid 1-65535 range", 200


# used to filter the hostname & device_group reports filtering to something that MongoDB can process
def get_param_filter(param_name, full_request, filter_param="eq", request_type=str):
    filter_param = "$" + filter_param
    param_value = full_request.args.get(param_name, type=request_type)
    if param_value is not None:
        return {param_name: {filter_param: param_value}}
    else:
        return None


# read config file at startup
# load the login params from envvar or auth.json file if envvar is not set, if both are unset will load the default
# value if one exists for the param
if os.path.exists("config/conf.json"):
    print("reading config file")
    auth_file = json.load(open("config/conf.json"))
else:
    print("config file not found - skipping reading it and checking if needed params are given from envvars")
    auth_file = {}

print("reading config variables")
basic_auth_user = get_conf_setting("basic_auth_user", auth_file, None)
basic_auth_password = get_conf_setting("basic_auth_password", auth_file, None)
auth_token = get_conf_setting("auth_token", auth_file, None)
mongo_url = get_conf_setting("mongo_url", auth_file)
schema_name = get_conf_setting("schema_name", auth_file, "nebula")
auth_enabled = get_conf_setting("auth_enabled", auth_file, True)
cache_time = int(get_conf_setting("cache_time", auth_file, "10"))
cache_max_size = int(get_conf_setting("cache_max_size", auth_file, "1024"))
mongo_max_pool_size = int(get_conf_setting("mongo_max_pool_size", auth_file, "25"))

# login to db at startup
mongo_connection = MongoConnection(mongo_url, schema_name, max_pool_size=mongo_max_pool_size)
print("opened MongoDB connection")

# ensure mongo is indexed properly
mongo_connection.mongo_create_indexes("app_name", "device_group", "users")

# get current list of apps at startup
nebula_apps = mongo_connection.mongo_list_apps()
print("got list of all mongo apps")

# open waiting connection
try:
    app = Flask(__name__)
    # basic auth for api
    basic_auth = HTTPBasicAuth(realm='nebula')
    token_auth = HTTPTokenAuth('Bearer')
    multi_auth = MultiAuth(basic_auth, token_auth)
    print("startup completed - now waiting for connections")
except Exception as e:
    print("Flask connection configuration failure - dropping container")
    print(e, file=sys.stderr)
    os._exit(2)


# this function checks basic_auth to allow access to authenticated users.
@basic_auth.verify_password
def verify_password(username, password):
    # if auth_enabled is set to false then always allow access
    if auth_enabled is False:
        return True
    # else if username and password matches the admin user set in the manager config allow access
    elif username == basic_auth_user and password == basic_auth_password:
        return True
    # else if the user and password matches any in the DB allow access
    elif mongo_connection.mongo_check_user_exists(username) is True:
        app_exists, user_json = mongo_connection.mongo_get_user(username)
        if check_secret_matches(password, user_json["password"]) is True:
            return True
        else:
            return False
    else:
        return False


# this function checks token based auth to allow access to authenticated users.
@token_auth.verify_token
def verify_token(token):
    if auth_enabled is False:
        return True
    elif auth_token == token:
        return True
    # TODO - finish checking against the DB users
    else:
        return False


# api check page - return 200 and a massage just so we know API is reachable
@app.route('/api/' + API_VERSION + '/status', methods=["GET"])
@retry(stop_max_attempt_number=3, wait_exponential_multiplier=200, wait_exponential_max=500)
@multi_auth.login_required
def check_page():
    return "{\"api_available\": true}", 200


# create a new app
@app.route('/api/' + API_VERSION + '/apps/<app_name>', methods=["POST"])
@multi_auth.login_required
def create_app(app_name):
    # check app does't exists first
    app_exists = mongo_connection.mongo_check_app_exists(app_name)
    if app_exists is True:
        return "{\"app_exists\": true}", 403
    else:
        # check the request is passed with all needed parameters
        try:
            app_json = request.json
        except:
            return json.dumps(find_missing_params({})), 400
        try:
            starting_ports = return_sane_default_if_not_declared("starting_ports", app_json, [])
            containers_per = return_sane_default_if_not_declared("containers_per", app_json, {"server": 1})
            env_vars = return_sane_default_if_not_declared("env_vars", app_json, [])
            docker_image = app_json["docker_image"]
            running = return_sane_default_if_not_declared("running", app_json, True)
            networks = return_sane_default_if_not_declared("networks", app_json, ["nebula", "bridge"])
            volumes = return_sane_default_if_not_declared("volumes", app_json, [])
            devices = return_sane_default_if_not_declared("devices", app_json, [])
            privileged = return_sane_default_if_not_declared("privileged", app_json, False)
            rolling_restart = return_sane_default_if_not_declared("rolling_restart", app_json, False)
        except:
            return json.dumps(find_missing_params(app_json)), 400
        # check edge case of port being outside of possible port ranges
        ports_check_return_message, port_check_return_code = check_ports_valid_range(starting_ports)
        if port_check_return_code >= 300:
            return ports_check_return_message, port_check_return_code
        # update the db
        app_json = mongo_connection.mongo_add_app(app_name, starting_ports, containers_per, env_vars, docker_image,
                                                  running, networks, volumes, devices, privileged, rolling_restart)
        return dumps(app_json), 200


# delete an app
@app.route('/api/' + API_VERSION + '/apps/<app_name>', methods=["DELETE"])
@multi_auth.login_required
def delete_app(app_name):
    # check app exists first
    app_exists = mongo_connection.mongo_check_app_exists(app_name)
    if app_exists is False:
        return "{\"app_exists\": false}", 403
    # remove from db
    mongo_connection.mongo_remove_app(app_name)
    return "{}", 200


# restart an app
@app.route('/api/' + API_VERSION + '/apps/<app_name>/restart', methods=["POST"])
@multi_auth.login_required
def restart_app(app_name):
    app_exists, app_json = mongo_connection.mongo_get_app(app_name)
    # check app exists first
    if app_exists is False:
        return "{\"app_exists\": \"False\"}", 403
    # check if app already running:
    if app_json["running"] is False:
        return "{\"running_before_restart\": false}", 403
    # post to db
    app_json = mongo_connection.mongo_increase_app_id(app_name)
    return dumps(app_json), 202


# stop an app
@app.route('/api/' + API_VERSION + '/apps/<app_name>/stop', methods=["POST"])
@multi_auth.login_required
def stop_app(app_name):
    # check app exists first
    app_exists = mongo_connection.mongo_check_app_exists(app_name)
    if app_exists is False:
        return "{\"app_exists\": false}", 403
    # post to db
    app_json = mongo_connection.mongo_update_app_running_state(app_name, False)
    return dumps(app_json), 202


# start an app
@app.route('/api/' + API_VERSION + '/apps/<app_name>/start', methods=["POST"])
@multi_auth.login_required
def start_app(app_name):
    # check app exists first
    app_exists = mongo_connection.mongo_check_app_exists(app_name)
    if app_exists is False:
        return "{\"app_exists\": false}", 403
    # post to db
    app_json = mongo_connection.mongo_update_app_running_state(app_name, True)
    return dumps(app_json), 202


# POST update an app - requires all the params to be given in the request body or else will be reset to default values
@app.route('/api/' + API_VERSION + '/apps/<app_name>/update', methods=["POST"])
@multi_auth.login_required
def update_app(app_name):
    # check app exists first
    app_exists = mongo_connection.mongo_check_app_exists(app_name)
    if app_exists is False:
        return "{\"app_exists\": false}", 403
    # check app got all needed parameters
    try:
        app_json = request.json
    except:
        return json.dumps(find_missing_params({})), 400
    try:
        starting_ports = return_sane_default_if_not_declared("starting_ports", app_json, [])
        containers_per = return_sane_default_if_not_declared("containers_per", app_json, {"server": 1})
        env_vars = return_sane_default_if_not_declared("env_vars", app_json, [])
        docker_image = app_json["docker_image"]
        running = return_sane_default_if_not_declared("running", app_json, True)
        networks = return_sane_default_if_not_declared("networks", app_json, ["nebula", "bridge"])
        volumes = return_sane_default_if_not_declared("volumes", app_json, [])
        devices = return_sane_default_if_not_declared("devices", app_json, [])
        privileged = return_sane_default_if_not_declared("privileged", app_json, False)
        rolling_restart = return_sane_default_if_not_declared("rolling_restart", app_json, False)
    except:
        return json.dumps(find_missing_params(app_json)), 400
    # check edge case of port being outside of possible port ranges
    ports_check_return_message, port_check_return_code = check_ports_valid_range(starting_ports)
    if port_check_return_code >= 300:
        return ports_check_return_message, port_check_return_code
    # update db
    app_json = mongo_connection.mongo_update_app(app_name, starting_ports, containers_per, env_vars, docker_image,
                                                 running, networks, volumes, devices, privileged, rolling_restart)
    return dumps(app_json), 202


# PUT update some fields of an app - params not given will be unchanged from their current value
@app.route('/api/' + API_VERSION + '/apps/<app_name>/update', methods=["PUT", "PATCH"])
@multi_auth.login_required
def update_app_fields(app_name):
    # check app exists first
    app_exists = mongo_connection.mongo_check_app_exists(app_name)
    if app_exists is False:
        return "{\"app_exists\": false}", 403
    # check app got update parameters
    try:
        app_json = request.json
        if len(app_json) == 0:
            return "{\"missing_parameters\": true}", 400
    except:
        return "{\"missing_parameters\": true}", 400
    # check edge case of port being outside of possible port ranges in case trying to update port listing
    try:
        starting_ports = request.json["starting_ports"]
        ports_check_return_message, port_check_return_code = check_ports_valid_range(starting_ports)
        if port_check_return_code >= 300:
            return ports_check_return_message, port_check_return_code
    except:
        pass
    # update db
    app_json = mongo_connection.mongo_update_app_fields(app_name, request.json)
    return dumps(app_json), 202


# list apps
@app.route('/api/' + API_VERSION + '/apps', methods=["GET"])
@retry(stop_max_attempt_number=3, wait_exponential_multiplier=200, wait_exponential_max=500)
@multi_auth.login_required
def list_apps():
    nebula_apps_list = mongo_connection.mongo_list_apps()
    return "{\"apps\": " + dumps(nebula_apps_list) + " }", 200


# get app info
@app.route('/api/' + API_VERSION + '/apps/<app_name>', methods=["GET"])
@retry(stop_max_attempt_number=3, wait_exponential_multiplier=200, wait_exponential_max=500)
@multi_auth.login_required
def get_app(app_name):
    app_exists, app_json = mongo_connection.mongo_get_app(app_name)
    if app_exists is True:
        return dumps(app_json), 200
    elif app_exists is False:
        return "{\"app_exists\": false}", 403


# get device_group info
@app.route('/api/' + API_VERSION + '/device_groups/<device_group>/info', methods=["GET"])
@cached(cache=TTLCache(maxsize=cache_max_size, ttl=cache_time))
@retry(stop_max_attempt_number=3, wait_exponential_multiplier=200, wait_exponential_max=500)
@multi_auth.login_required
def get_device_group_info(device_group):
    device_group_exists, device_group_json = mongo_connection.mongo_get_device_group(device_group)
    if device_group_exists is False:
        return "{\"device_group_exists\": false}", 403
    device_group_config = {"apps": [], "apps_list": [], "prune_id": device_group_json["prune_id"],
                           "device_group_id": device_group_json["device_group_id"]}
    for device_app in device_group_json["apps"]:
        app_exists, app_json = mongo_connection.mongo_get_app(device_app)
        if app_exists is True:
            device_group_config["apps"].append(app_json)
            device_group_config["apps_list"].append(app_json["app_name"])
    return dumps(device_group_config), 200


# create device_group
@app.route('/api/' + API_VERSION + '/device_groups/<device_group>', methods=["POST"])
@multi_auth.login_required
def create_device_group(device_group):
    # check app does't exists first
    device_group_exists = mongo_connection.mongo_check_device_group_exists(device_group)
    if device_group_exists is True:
        return "{\"device_group_exists\": true}", 403
    else:
        # check the request is passed with all needed parameters
        try:
            app_json = request.json
        except:
            return json.dumps({"missing_parameters": ["apps"]}), 400
        try:
            apps = request.json["apps"]
        except:
            return json.dumps({"missing_parameters": ["apps"]}), 400
        # check edge case where apps is not a list
        if type(apps) is not list:
            return "{\"apps_is_list\": false}", 400
        # check edge case where adding an app that does not exist
        for device_app in apps:
            app_exists, app_json = mongo_connection.mongo_get_app(device_app)
            if app_exists is False:
                return "{\"app_exists\": false}", 403
        # update the db
        app_json = mongo_connection.mongo_add_device_group(device_group, apps)
        return dumps(app_json), 200


# list device_group
@app.route('/api/' + API_VERSION + '/device_groups/<device_group>', methods=["GET"])
@retry(stop_max_attempt_number=3, wait_exponential_multiplier=200, wait_exponential_max=500)
@multi_auth.login_required
def get_device_group(device_group):
    device_group_exists, device_group_json = mongo_connection.mongo_get_device_group(device_group)
    if device_group_exists is True:
        return dumps(device_group_json), 200
    elif device_group_exists is False:
        return "{\"device_group_exists\": false}", 403


# POST update device_group - requires a full list of apps to be given in the request body
@app.route('/api/' + API_VERSION + '/device_groups/<device_group>/update', methods=["POST"])
@multi_auth.login_required
def update_device_group(device_group):
    # check device_group exists first
    device_group_exists = mongo_connection.mongo_check_device_group_exists(device_group)
    if device_group_exists is False:
        return "{\"app_exists\": false}", 403
    # check app got all needed parameters
    try:
        app_json = request.json
    except:
        return json.dumps({"missing_parameters": ["apps"]}), 400
    try:
        apps = request.json["apps"]
    except:
        return json.dumps({"missing_parameters": ["apps"]}), 400
        # check edge case where apps is not a list
    if type(apps) is not list:
        return "{\"apps_is_list\": false}", 400
    # check edge case where adding an app that does not exist
    for device_app in apps:
        app_exists, app_json = mongo_connection.mongo_get_app(device_app)
        if app_exists is False:
            return "{\"app_exists\": false}", 403
    # update db
    app_json = mongo_connection.mongo_update_device_group(device_group, apps)
    return dumps(app_json), 202


# delete device_group
@app.route('/api/' + API_VERSION + '/device_groups/<device_group>', methods=["DELETE"])
@multi_auth.login_required
def delete_device_group(device_group):
    # check app exists first
    device_group_exists = mongo_connection.mongo_check_device_group_exists(device_group)
    if device_group_exists is False:
        return "{\"device_group_exists\": false}", 403
    # remove from db
    mongo_connection.mongo_remove_device_group(device_group)
    return "{}", 200


# list device_groups
@app.route('/api/' + API_VERSION + '/device_groups', methods=["GET"])
@retry(stop_max_attempt_number=3, wait_exponential_multiplier=200, wait_exponential_max=500)
@multi_auth.login_required
def list_device_groups():
    nebula_device_groups_list = mongo_connection.mongo_list_device_groups()
    return "{\"device_groups\": " + dumps(nebula_device_groups_list) + " }", 200


# prune unused images on all devices running said device_group
@app.route('/api/' + API_VERSION + '/device_groups/<device_group>/prune', methods=["POST"])
@multi_auth.login_required
def prune_device_group_images(device_group):
    # check device_group exists first
    device_group_exists = mongo_connection.mongo_check_device_group_exists(device_group)
    if device_group_exists is False:
        return "{\"app_exists\": false}", 403
    # update db
    app_json = mongo_connection.mongo_increase_prune_id(device_group)
    return dumps(app_json), 202


# prune unused images on all devices
@app.route('/api/' + API_VERSION + '/prune', methods=["POST"])
@multi_auth.login_required
def prune_images_on_all_device_groups():
    # get a list of all device_groups
    device_groups = mongo_connection.mongo_list_device_groups()
    all_device_groups_prune_id = {"prune_ids": {}}
    # loop over all device groups
    for device_group in device_groups:
        # check device_group exists first
        device_group_exists = mongo_connection.mongo_check_device_group_exists(device_group)
        if device_group_exists is False:
            return "{\"app_exists\": false}", 403
        # update db
        app_json = mongo_connection.mongo_increase_prune_id(device_group)
        all_device_groups_prune_id["prune_ids"][device_group] = app_json["prune_id"]
    return dumps(all_device_groups_prune_id), 202


# list reports
@app.route('/api/' + API_VERSION + '/reports', methods=["GET"])
@retry(stop_max_attempt_number=3, wait_exponential_multiplier=200, wait_exponential_max=500)
@multi_auth.login_required
def get_report():

    # first we get all the data from the params and pass it through the get_param_filter which will return them in the
    # format MongoDB uses to filtering
    last_id = request.args.get('last_id')
    page_size = request.args.get('page_size', 10, int)
    hostname = get_param_filter("hostname", request)
    device_group = get_param_filter("device_group", request)
    report_creation_time_filter = request.args.get('report_creation_time_filter', "eq", str)
    report_creation_time = get_param_filter("report_creation_time", request, report_creation_time_filter,
                                            request_type=int)

    # Now we combine all the filters
    filters = {}
    for filter in [hostname, device_group, report_creation_time]:
        if filter is not None:
            filters = {**filters, **filter}

    # lastly we return the requests reports to the user
    data, last_id = mongo_connection.mango_list_paginated_filtered_reports(page_size=page_size, last_id=last_id,
                                                                           filters=filters)
    reply = {"data": data, "last_id": last_id}
    return dumps(reply), 200


# set json header - the API is JSON only so the header is set on all requests
@app.after_request
def apply_caching(response):
    response.headers["Content-Type"] = "application/json"
    return response


# list users
@app.route('/api/' + API_VERSION + '/users', methods=["GET"])
@retry(stop_max_attempt_number=3, wait_exponential_multiplier=200, wait_exponential_max=500)
@multi_auth.login_required
def list_users():
    nebula_users_list = mongo_connection.mongo_list_users()
    return "{\"users\": " + dumps(nebula_users_list) + " }", 200


# get user info
@app.route('/api/' + API_VERSION + '/users/<user_name>', methods=["GET"])
@retry(stop_max_attempt_number=3, wait_exponential_multiplier=200, wait_exponential_max=500)
@multi_auth.login_required
def get_user(user_name):
    user_exists, user_json = mongo_connection.mongo_get_user(user_name)
    if user_exists is True:
        return dumps(user_json), 200
    elif user_exists is False:
        return "{\"user_exists\": false}", 403


# delete a user
@app.route('/api/' + API_VERSION + '/users/<user_name>', methods=["DELETE"])
@multi_auth.login_required
def delete_user(user_name):
    # check user exists first
    user_exists = mongo_connection.mongo_check_user_exists(user_name)
    if user_exists is False:
        return "{\"user_exists\": false}", 403
    # remove from db
    mongo_connection.mongo_delete_user(user_name)
    return "{}", 200


# update a user
@app.route('/api/' + API_VERSION + '/users/<user_name>/update', methods=["PUT", "PATCH"])
@multi_auth.login_required
def update_user(user_name):
    # check user exists first
    user_exists = mongo_connection.mongo_check_user_exists(user_name)
    if user_exists is False:
        return "{\"user_name\": false}", 403
    # check user got update parameters
    try:
        user_json = request.json
        if len(user_json) == 0:
            return "{\"missing_parameters\": true}", 400
    except:
        return "{\"missing_parameters\": true}", 400
    # if part of the update includes a token hash it
    try:
        request.json["token"] = hash_secret(request.json["token"])
    except:
        pass
    # if part of the update includes a password hash it
    try:
        request.json["password"] = hash_secret(request.json["password"])
    except:
        pass
    # update db
    user_json = mongo_connection.mongo_update_user(user_name, request.json)
    return dumps(user_json), 202


# refresh a user token
@app.route('/api/' + API_VERSION + '/users/<user_name>/refresh', methods=["POST"])
@multi_auth.login_required
def refresh_user_token(user_name):
    # check user exists first
    user_exists = mongo_connection.mongo_check_user_exists(user_name)
    if user_exists is False:
        return "{\"user_name\": false}", 403
    # get current user data and update the token for him
    try:
        new_token = secrets.token_urlsafe()
        app_exists, user_json = mongo_connection.mongo_get_user(user_name)
        user_json["token"] = hash_secret(new_token)
    except:
        return "{\"token_refreshed\": false}", 403
    # update db
    user_json = mongo_connection.mongo_update_user(user_name, user_json)
    return "{\"token\": \"" + new_token + "\" }", 202


# create new user
@app.route('/api/' + API_VERSION + '/users/<user_name>', methods=["POST"])
@multi_auth.login_required
def create_user(user_name):
    # check app does't exists first
    user_exists = mongo_connection.mongo_check_user_exists(user_name)
    if user_exists is True:
        return "{\"user_name\": true}", 403
    else:
        # check the request is passed with all needed parameters
        try:
            user_json = request.json
        except:
            return "{\"missing_parameters\": true}", 400
        try:
            password = hash_secret(return_sane_default_if_not_declared("password", user_json, secrets.token_urlsafe()))
            token = hash_secret(return_sane_default_if_not_declared("token", user_json, secrets.token_urlsafe()))
        except:
            return "{\"missing_parameters\": true}", 400
        # update the db
        user_json = mongo_connection.mongo_add_user(user_name, password, token)
        return dumps(user_json), 200


# used for when running with the 'ENV' envvar set to dev to open a new thread with flask builtin web server
def run_dev(dev_host='0.0.0.0', dev_port=5000, dev_threaded=True):
    try:
        app.run(host=dev_host, port=dev_port, threaded=dev_threaded)
    except Exception as e:
        print("Flask connection failure - dropping container")
        print(e, file=sys.stderr)
        os._exit(2)


# will usually run in gunicorn but for debugging set the "ENV" envvar to "dev" to run from flask built in web server
# opens in a new thread, DO NOT SET AS 'dev' FOR PRODUCTION USE!!!
if os.getenv("ENV", "prod") == "dev":
    try:
        run_dev()
    except Exception as e:
        print("Flask connection failure - dropping container")
        print(e, file=sys.stderr)
        os._exit(2)
